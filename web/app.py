"""
Document Scan Web UI
Upload photos → edge detect / warp / enhance → A4 PDF
"""
from __future__ import annotations

import asyncio
import json
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline import process_image, to_a4
from pipeline.enhance import MODES
from pipeline.imio import decode, imwrite
from pipeline.pdf import images_to_pdf
from pipeline.pdf2scan import pdf_to_scanned_pdf
from pipeline.word2pdf import docx_to_pdf, docx_to_pdf_with_previews

logger = logging.getLogger("document-scan.web")

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PAGES = 40
PREVIEW_MAX_DIM = 900


def _truthy(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


def _parse_quad(raw: str):
    """Parse a manual-crop quad "TL,TR,BR,BL" (JSON 4×2 or flat 8 numbers)
    into a (4,2) float array, or None if absent/invalid."""
    import json as _json

    if not raw or not raw.strip():
        return None
    try:
        pts = _json.loads(raw)
    except ValueError:
        raise ScanError("corners 格式错误：应为 4 个 [x, y] 点", 422)
    pts = list(pts)
    if len(pts) == 8:  # flat form
        pts = [pts[j:j + 2] for j in range(0, 8, 2)]
    if len(pts) != 4 or any(len(p) != 2 for p in pts):
        raise ScanError("corners 格式错误：应为 4 个 [x, y] 点", 422)
    return [[float(p[0]), float(p[1])] for p in pts]


# Successful jobs live for SCAN_JOB_TTL and are then reaped; everything of ours
# also goes on a clean shutdown. Both only ever touch 12-hex job directories, so
# a folder someone made by hand inside web/outputs survives.
JOB_TTL_SECONDS = int(os.environ.get("SCAN_JOB_TTL", "21600"))
REAP_INTERVAL = int(os.environ.get("SCAN_REAP_INTERVAL", "300"))
CLEAN_ON_EXIT = _truthy(os.environ.get("SCAN_CLEAN_ON_EXIT", "true"))
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
PAIR_CODE_RE = re.compile(r"^[0-9a-f]{8}$")

INDEX_HTML = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
MOBILE_HTML = (APP_DIR / "templates" / "mobile.html").read_text(encoding="utf-8")


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
    corners: List[str] = Form([]),
):
    """Sync handler on purpose: FastAPI runs it in a worker thread, which keeps
    the multi-second OpenCV work off the event loop.

    corners: optional JSON quads, one per file ("[[x,y]×4]" in source-image
    pixels, TL/TR/BR/BL) from the manual crop UI. When given for a file,
    auto detection is skipped for it."""
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
        manual = [_parse_quad(c) for c in corners]

        statuses: list[str] = []
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

            page_status: list = []
            processed = process_image(
                img, mode=mode, auto_crop=do_crop, crop_status=page_status,
                corners=manual[i] if i < len(manual) else None,
            )
            if do_a4:
                processed = to_a4(processed)
            pages.append(processed)
            statuses.append(page_status[0] if page_status else "kept")

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
            "crop_status": statuses,
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


@app.post("/api/scan/stream")
async def scan_stream(
    files: List[UploadFile] = File(...),
    mode: str = Form("color"),
    auto_crop: str = Form("true"),
    unify_a4: str = Form("true"),
    corners: List[str] = Form([]),
):
    """Streaming variant of /api/scan: NDJSON, one line per finished page plus
    a final "done" line. The UI shows pages as they land instead of waiting
    for the whole job, which is what makes a 40-page scan feel fast."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    def sse(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    def validate() -> dict:
        if mode not in MODES:
            raise ScanError(f"mode 必须是 {' / '.join(MODES)} 之一", 422)
        if not files:
            raise ScanError("请至少上传一张图片")
        if len(files) > MAX_PAGES:
            raise ScanError(f"一次最多 {MAX_PAGES} 页", 413)
        return {"do_crop": _truthy(auto_crop), "do_a4": _truthy(unify_a4)}

    try:
        opts = validate()
    except ScanError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)

    # UploadFile file objects close when the request scope ends; the response
    # generator runs after that, so read every upload into memory up front.
    # Bounded by MAX_PAGES × MAX_UPLOAD_BYTES, same as the sync endpoint.
    try:
        manual = [_parse_quad(c) for c in corners]
    except ScanError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)
    uploaded: list[tuple[str, bytes]] = []
    for uf in files:
        content = uf.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"error": f"上传文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限"}, status_code=413
            )
        uploaded.append((Path(uf.filename or "").name, content))

    def generate():
        pages: list = []
        statuses: list[str] = []
        try:
            yield sse({"type": "start", "pages": len(uploaded), "mode": mode})
            for i, (name, content) in enumerate(uploaded):
                if len(content) > MAX_UPLOAD_BYTES:
                    raise ScanError(
                        f"第 {i + 1} 张超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限", 413
                    )
                img = decode(content)
                if img is None:
                    raise ScanError(f"无法读取图片: {name or f'第 {i + 1} 张'}")

                page_status: list = []
                processed = process_image(
                    img, mode=mode, auto_crop=opts["do_crop"], crop_status=page_status,
                    corners=manual[i] if i < len(manual) else None,
                )
                if opts["do_a4"]:
                    processed = to_a4(processed)
                pages.append(processed)
                statuses.append(page_status[0] if page_status else "kept")

                prev_name = f"preview_{i:03d}.jpg"
                h, w = processed.shape[:2]
                scale = min(1.0, PREVIEW_MAX_DIM / max(h, w))
                small = cv2.resize(
                    processed,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
                imwrite(job_dir / prev_name, small, [cv2.IMWRITE_JPEG_QUALITY, 88])
                yield sse({
                    "type": "page",
                    "index": i,
                    "mode": mode,
                    "preview": f"/outputs/{job_id}/{prev_name}",
                })

            pdf_name = f"scan_{job_id}.pdf"
            images_to_pdf(pages, job_dir / pdf_name)
            yield sse({
                "type": "done",
                "job_id": job_id,
                "pages": len(pages),
                "mode": mode,
                "a4": opts["do_a4"],
                "crop_status": statuses,
                "pdf_url": f"/outputs/{job_id}/{pdf_name}",
                "download_name": f"scanned_{mode}_{len(pages)}p_{job_id}.pdf",
            })
        except ScanError as e:
            yield sse({"error": e.message})
            _drop_job_dir(job_dir)
        except Exception:
            logger.exception("scan job %s failed", job_id)
            yield sse({"error": "处理失败，请查看服务端日志", "job_id": job_id})
            _drop_job_dir(job_dir)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.post("/api/word2pdf")
def word_to_pdf(word: UploadFile = File(...)):
    """Word → scan-styled PDF with per-page previews. One document per job."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    done = False
    try:
        name = Path(word.filename or "document.docx").name
        if not name.lower().endswith((".docx", ".doc")):
            raise ScanError("请上传 .docx 或 .doc 文件", 422)

        content = word.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ScanError(
                f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限", 413
            )

        ext = ".doc" if name.lower().endswith(".doc") else ".docx"
        src = job_dir / f"upload{ext}"
        src.write_bytes(content)

        pdf_name = f"scan_{job_id}.pdf"
        _, previews = docx_to_pdf_with_previews(src, job_dir / pdf_name)
        done = True

        preview_urls = []
        for i, small in enumerate(previews[:MAX_PAGES]):
            prev_name = f"preview_{i:03d}.jpg"
            imwrite(job_dir / prev_name, small, [cv2.IMWRITE_JPEG_QUALITY, 88])
            preview_urls.append(f"/outputs/{job_id}/{prev_name}")

        return {
            "job_id": job_id,
            "pages": len(previews),
            "mode": "gray",
            "a4": True,
            "previews": preview_urls,
            "pdf_url": f"/outputs/{job_id}/{pdf_name}",
            "download_name": f"scanned_{Path(name).stem}_{job_id}.pdf",
        }
    except ScanError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)
    except Exception:
        logger.exception("word2pdf job %s failed", job_id)
        return JSONResponse(
            {"error": "Word 转换失败，请确认文件未损坏", "job_id": job_id}, status_code=500
        )
    finally:
        if not done:
            _drop_job_dir(job_dir)


@app.post("/api/pdf2scan")
def pdf_to_scan(pdf: UploadFile = File(...)):
    """PDF → scan-styled PDF with per-page previews. One file per job."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    done = False
    try:
        name = Path(pdf.filename or "document.pdf").name
        if not name.lower().endswith(".pdf"):
            raise ScanError("请上传 .pdf 文件", 422)

        content = pdf.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ScanError(
                f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限", 413
            )

        src = job_dir / "upload.pdf"
        src.write_bytes(content)

        pdf_name = f"scan_{job_id}.pdf"
        out, pages, previews = pdf_to_scanned_pdf(src, job_dir / pdf_name)
        if not pages:
            raise ScanError("PDF 没有可处理的页面", 422)
        if len(pages) > MAX_PAGES:
            raise ScanError(f"PDF 超过 {MAX_PAGES} 页上限", 413)
        done = True

        preview_urls = []
        for i, small in enumerate(previews):
            prev_name = f"preview_{i:03d}.jpg"
            imwrite(job_dir / prev_name, small, [cv2.IMWRITE_JPEG_QUALITY, 88])
            preview_urls.append(f"/outputs/{job_id}/{prev_name}")

        return {
            "job_id": job_id,
            "pages": len(pages),
            "mode": "gray",
            "a4": True,
            "previews": preview_urls,
            "pdf_url": f"/outputs/{job_id}/{pdf_name}",
            "download_name": f"scanned_{Path(name).stem}_{job_id}.pdf",
        }
    except ScanError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)
    except Exception:
        logger.exception("pdf2scan job %s failed", job_id)
        return JSONResponse(
            {"error": "PDF 转换失败，请确认文件未损坏", "job_id": job_id}, status_code=500
        )
    finally:
        if not done:
            _drop_job_dir(job_dir)


# ---------- 扫码配对上传 ----------
# 桌面页请求一个配对码；手机扫码打开极简上传页，照片先落到内存里的配对槽，
# 桌面页轮询取走。槽 10 分钟过期，取走即销毁——不经任何云端。

import io as _io
import secrets as _secrets

PAIR_TTL = 600  # seconds
_pairs: dict = {}  # code -> {"files": [(name, bytes)], "created": float}


def _pair_gc():
    now = time.time()
    for k in [k for k, v in _pairs.items() if now - v["created"] > PAIR_TTL]:
        _pairs.pop(k, None)


@app.get("/api/pair/new")
async def pair_new(host: str = ""):
    """Create a pairing slot, return the code + a QR PNG encoding the mobile
    upload URL. `host` lets the desktop page pass its own address so the QR
    works from any interface (LAN IP, tunnel hostname...)."""
    _pair_gc()
    code = _secrets.token_hex(4)
    _pairs[code] = {"files": [], "created": time.time(), "url": ""}
    base = host.rstrip("/") or f"http://127.0.0.1:{int(os.environ.get('SCAN_PORT', '8765'))}"
    # http scheme default; the desktop page passes e.g. http://192.168.1.5:8765
    url = f"{base}/m/{code}"
    _pairs[code]["url"] = url
    return JSONResponse({"code": code, "url": url, "qr_png": f"/api/pair/{code}/qr.png"})


@app.get("/api/pair/{code}/qr.png")
async def pair_qr(code: str):
    import qrcode

    slot = _pairs.get(code)
    if not slot:
        return JSONResponse({"error": "配对码不存在或已过期"}, status_code=404)
    # Encode the absolute URL the desktop page registered for this slot.
    img = qrcode.make(slot.get("url") or f"/m/{code}", box_size=8, border=2)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/m/{code}", response_class=HTMLResponse)
async def mobile_upload_page(code: str):
    """Minimal phone-facing upload page. The server injects the absolute /m/
    path so the phone posts back to the same host it came from (LAN or tunnel
    — whatever the QR encoded)."""
    if not PAIR_CODE_RE.match(code) or code not in _pairs:
        return HTMLResponse("<meta charset=utf-8><body style='font-family:sans-serif;text-align:center;padding:40px'>配对码不存在或已过期，请在电脑上重新生成。</body>", status_code=404)
    return MOBILE_HTML


@app.post("/api/pair/{code}/upload")
async def pair_upload(code: str, files: List[UploadFile] = File(...)):
    """Phone posts photos here; they wait in the slot for the desktop to poll."""
    _pair_gc()
    slot = _pairs.get(code)
    if not slot:
        return JSONResponse({"error": "配对码不存在或已过期"}, status_code=404)
    added = 0
    for uf in files[:10]:
        content = await uf.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            continue
        slot["files"].append((Path(uf.filename or "photo.jpg").name, content))
        added += 1
    return {"added": added}


@app.get("/api/pair/{code}/poll")
async def pair_poll(code: str, consume: bool = True):
    """Desktop poll: returns files waiting in the slot and consumes them, so a
    photo lands on the desktop exactly once. An empty slot is "still waiting",
    not expired — expiry is purely TTL-based."""
    _pair_gc()
    slot = _pairs.get(code)
    if slot is None:
        return {"files": [], "expired": True}
    out = []
    for name, content in slot["files"]:
        img = decode(content)
        if img is None:
            continue
        ok, jpg = cv2.imencode(".jpg", img)
        out.append({"name": name, "data": jpg.tobytes().hex()})
    if out and consume:
        _pairs.pop(code, None)
        return {"files": out, "count": len(out)}
    # Drop undecodable entries but keep waiting.
    slot["files"] = [f for f in slot["files"] if decode(f[1]) is not None]
    return {"files": [], "count": 0}


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
        host=os.environ.get("SCAN_HOST", "0.0.0.0"),
        port=int(os.environ.get("SCAN_PORT", "8765")),
        reload=False,
        timeout_graceful_shutdown=int(os.environ.get("SCAN_SHUTDOWN_TIMEOUT", "5")),
    )
