"""
Document Scan Web UI
Upload photos → edge detect / warp / enhance → A4 PDF
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import process_image, to_a4
from pipeline.enhance import MODES
from pipeline.imio import decode, imwrite
from pipeline.pdf import images_to_pdf

logger = logging.getLogger("document-scan.web")

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PAGES = 40
PREVIEW_MAX_DIM = 900


def _truthy(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


# Successful jobs live for SCAN_JOB_TTL and are then reaped; everything of ours
# also goes on a clean shutdown. Both only ever touch 12-hex job directories, so
# a folder someone made by hand inside web/outputs survives.
JOB_TTL_SECONDS = int(os.environ.get("SCAN_JOB_TTL", "21600"))
REAP_INTERVAL = int(os.environ.get("SCAN_REAP_INTERVAL", "300"))
CLEAN_ON_EXIT = _truthy(os.environ.get("SCAN_CLEAN_ON_EXIT", "true"))
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")

INDEX_HTML = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")


def _job_dirs() -> List[Path]:
    if not OUTPUT_DIR.is_dir():
        return []
    return [p for p in OUTPUT_DIR.iterdir() if p.is_dir() and JOB_ID_RE.match(p.name)]


def _drop_job_dir(path: Path) -> int:
    """Remove one job directory and report the bytes it held. A browser still
    reading the PDF can keep the tree open, so leftover is logged, not hidden."""
    size = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                size += f.stat().st_size
        except OSError:
            continue
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        logger.warning("job %s still present after cleanup (file open?)", path.name)
    return size


def _drop_all() -> tuple[int, int]:
    removed = freed = 0
    for d in _job_dirs():
        freed += _drop_job_dir(d)
        removed += 1
    return removed, freed


def _reap_once() -> tuple[int, int]:
    now = time.time()
    removed = freed = 0
    for d in _job_dirs():
        try:
            if now - d.stat().st_mtime < JOB_TTL_SECONDS:
                continue
        except OSError:
            continue
        freed += _drop_job_dir(d)
        removed += 1
    return removed, freed


async def _reaper() -> None:
    while True:
        await asyncio.sleep(REAP_INTERVAL)
        removed, freed = await asyncio.to_thread(_reap_once)
        if removed:
            logger.info("reaped %d expired job(s), %.1f MB", removed, freed / 1e6)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_reaper()) if JOB_TTL_SECONDS > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if CLEAN_ON_EXIT:
            removed, freed = await asyncio.to_thread(_drop_all)
            logger.info("shutdown: removed %d job dir(s), %.1f MB", removed, freed / 1e6)


app = FastAPI(title="Document Scan", version="1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

MEDIA_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png"}


class ScanError(Exception):
    """A client-caused scan failure: the message is safe to return."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)


@app.post("/api/scan")
def scan(
    files: List[UploadFile] = File(...),
    mode: str = Form("color"),
    auto_crop: str = Form("true"),
    unify_a4: str = Form("true"),
):
    """Sync handler on purpose: FastAPI runs it in a worker thread, which keeps
    the multi-second OpenCV work off the event loop."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    pages: list = []
    previews: list[str] = []
    done = False
    try:
        if mode not in MODES:
            raise ScanError(f"mode 必须是 {' / '.join(MODES)} 之一", 422)
        if not files:
            raise ScanError("请至少上传一张图片")
        if len(files) > MAX_PAGES:
            raise ScanError(f"一次最多 {MAX_PAGES} 页", 413)

        do_crop = _truthy(auto_crop)
        do_a4 = _truthy(unify_a4)

        for i, uf in enumerate(files):
            # Reads one byte past the cap so an oversized body is detected
            # without ever buffering the whole upload.
            content = uf.file.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise ScanError(
                    f"第 {i + 1} 张超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限", 413
                )

            img = decode(content)
            if img is None:
                name = Path(uf.filename or "").name or f"第 {i + 1} 张"
                raise ScanError(f"无法读取图片: {name}")

            processed = process_image(img, mode=mode, auto_crop=do_crop)
            if do_a4:
                processed = to_a4(processed)
            pages.append(processed)

            prev_name = f"preview_{i:03d}.jpg"
            h, w = processed.shape[:2]
            scale = min(1.0, PREVIEW_MAX_DIM / max(h, w))
            small = cv2.resize(
                processed,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
            imwrite(job_dir / prev_name, small, [cv2.IMWRITE_JPEG_QUALITY, 88])
            previews.append(f"/outputs/{job_id}/{prev_name}")

        pdf_name = f"scan_{job_id}.pdf"
        images_to_pdf(pages, job_dir / pdf_name)
        done = True

        return {
            "job_id": job_id,
            "pages": len(pages),
            "mode": mode,
            "a4": do_a4,
            "previews": previews,
            "pdf_url": f"/outputs/{job_id}/{pdf_name}",
            "download_name": f"scanned_{mode}_{len(pages)}p_{job_id}.pdf",
        }
    except ScanError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)
    except Exception:
        # Never forward internals: the traceback goes to the log, the user gets
        # the job id that identifies it there.
        logger.exception("scan job %s failed", job_id)
        return JSONResponse(
            {"error": "处理失败，请查看服务端日志", "job_id": job_id}, status_code=500
        )
    finally:
        if not done:
            _drop_job_dir(job_dir)


@app.get("/outputs/{job_id}/{filename}")
async def get_output(job_id: str, filename: str, inline: bool = False):
    root = OUTPUT_DIR.resolve()
    path = (OUTPUT_DIR / job_id / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        # A filename makes Starlette emit Content-Disposition: attachment, which
        # turns an in-page <iframe> preview into a download.
        filename=path.name
        if path.suffix.lower() == ".pdf" and not inline
        else None,
    )


if __name__ == "__main__":
    import uvicorn

    # Caps how long Ctrl+C waits for in-flight responses before running the
    # lifespan purge. An idle keep-alive from the PDF preview closes on its own;
    # a client still downloading a big PDF holds shutdown open forever, and the
    # purge would then never run. Price: that download is cut off at the timeout.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8765,
        reload=False,
        timeout_graceful_shutdown=int(os.environ.get("SCAN_SHUTDOWN_TIMEOUT", "5")),
    )
