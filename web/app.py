"""
Document Scan Web UI
Upload photos → edge detect / warp / enhance → A4 PDF
"""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline.detect import find_document_corners
from pipeline.enhance import enhance_document
from pipeline.pdf import images_to_pdf
from pipeline.warp import four_point_transform

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
OUTPUT_DIR = APP_DIR / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

A4_W, A4_H = 1654, 2339
INDEX_HTML = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")

app = FastAPI(title="Document Scan", version="1.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def expand_corners(pts: np.ndarray, img_shape, margin_ratio: float = 0.08) -> np.ndarray:
    h, w = img_shape[:2]
    center = pts.mean(axis=0)
    expanded = center + (pts - center) * (1.0 + margin_ratio)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, w - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, h - 1)
    return expanded.astype("float32")


def smart_process(img: np.ndarray, mode: str = "color", auto_crop: bool = True) -> np.ndarray:
    h, w = img.shape[:2]
    if not auto_crop:
        return enhance_document(img, mode=mode)

    corners = find_document_corners(img)
    if corners is not None:
        xs, ys = corners[:, 0], corners[:, 1]
        cover = ((xs.max() - xs.min()) * (ys.max() - ys.min())) / float(w * h)
        corners = expand_corners(corners, img.shape, margin_ratio=0.08)
        if cover > 0.82:
            warped = img
        else:
            warped = four_point_transform(img, corners)
    else:
        warped = img
    return enhance_document(warped, mode=mode)


def to_a4(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    scale = min(A4_W / w, A4_H / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(img, (nw, nh), interpolation=interp)
    canvas = np.full((A4_H, A4_W, 3), 255, dtype=np.uint8)
    x0 = (A4_W - nw) // 2
    y0 = (A4_H - nh) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)


@app.post("/api/scan")
async def scan(
    files: List[UploadFile] = File(...),
    mode: str = Form("color"),
    auto_crop: str = Form("true"),
    unify_a4: str = Form("true"),
):
    if mode not in ("color", "gray", "bw"):
        mode = "color"
    do_crop = auto_crop.lower() in ("1", "true", "yes", "on")
    do_a4 = unify_a4.lower() in ("1", "true", "yes", "on")

    if not files:
        return JSONResponse({"error": "请至少上传一张图片"}, status_code=400)

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    pages: list[np.ndarray] = []
    previews: list[str] = []

    try:
        for i, uf in enumerate(files):
            suffix = Path(uf.filename or f"img_{i}.jpg").suffix.lower() or ".jpg"
            if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"):
                suffix = ".jpg"
            raw_path = job_dir / f"raw_{i:03d}{suffix}"
            content = await uf.read()
            raw_path.write_bytes(content)

            img = cv2.imread(str(raw_path))
            if img is None:
                arr = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return JSONResponse(
                    {"error": f"无法读取图片: {uf.filename}"}, status_code=400
                )

            processed = smart_process(img, mode=mode, auto_crop=do_crop)
            if do_a4:
                processed = to_a4(processed)
            pages.append(processed)

            prev_name = f"preview_{i:03d}.jpg"
            prev_path = job_dir / prev_name
            h, w = processed.shape[:2]
            scale = min(1.0, 900 / max(h, w))
            small = cv2.resize(
                processed,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
            cv2.imwrite(str(prev_path), small, [cv2.IMWRITE_JPEG_QUALITY, 88])
            previews.append(f"/outputs/{job_id}/{prev_name}")

        pdf_name = f"scan_{job_id}.pdf"
        pdf_path = job_dir / pdf_name
        images_to_pdf(pages, pdf_path)

        return {
            "job_id": job_id,
            "pages": len(pages),
            "mode": mode,
            "a4": do_a4,
            "previews": previews,
            "pdf_url": f"/outputs/{job_id}/{pdf_name}",
            "download_name": f"scanned_{job_id}.pdf",
        }
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/outputs/{job_id}/{filename}")
async def get_output(job_id: str, filename: str):
    path = OUTPUT_DIR / job_id / filename
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    media = "application/pdf" if filename.endswith(".pdf") else "image/jpeg"
    return FileResponse(
        path,
        media_type=media,
        filename=filename if filename.endswith(".pdf") else None,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)
