"""Document scan pipeline: detect → warp → enhance → PDF."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .detect import find_document_corners
from .enhance import MODES, enhance_document
from .imio import imread, imwrite
from .warp import four_point_transform

# 210x297mm at 200 DPI
A4_W, A4_H = 1654, 2339


def expand_corners(
    pts: np.ndarray,
    img_shape,
    margin_ratio: float = 0.08,
) -> np.ndarray:
    """Push the detected quad outward from its centre so edge content survives."""
    h, w = img_shape[:2]
    center = pts.mean(axis=0)
    expanded = center + (pts - center) * (1.0 + margin_ratio)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, w - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, h - 1)
    return expanded.astype("float32")


def process_image(
    img: np.ndarray,
    mode: str = "color",
    auto_crop: bool = True,
    margin_ratio: float = 0.08,
    skip_cover: float = 0.82,
) -> np.ndarray:
    """Enhance an already-decoded photo, warping it if a document quad is found.

    skip_cover: when the detected quad already covers this fraction of the
    frame, the photo is effectively a full-page shot and warping only adds
    distortion, so the original is kept.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {', '.join(MODES)}")

    if auto_crop:
        corners = find_document_corners(img)
        if corners is not None:
            xs, ys = corners[:, 0], corners[:, 1]
            cover = ((xs.max() - xs.min()) * (ys.max() - ys.min())) / float(
                img.shape[1] * img.shape[0]
            )
            if cover <= skip_cover:
                img = four_point_transform(
                    img, expand_corners(corners, img.shape, margin_ratio)
                )

    return enhance_document(img, mode=mode)


def process_one_image(
    image_path: str | Path,
    mode: str = "color",
    auto_crop: bool = True,
    margin_ratio: float = 0.08,
    skip_cover: float = 0.82,
) -> np.ndarray:
    """
    Full pipeline for a single photo → clean document image.

    Returns BGR numpy array.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    img = imread(path)
    if img is None:
        raise ValueError(f"Cannot decode image: {path}")

    return process_image(
        img, mode=mode, auto_crop=auto_crop, margin_ratio=margin_ratio, skip_cover=skip_cover
    )


def scan_file(
    image_path: str | Path,
    output_path: str | Path | None = None,
    mode: str = "color",
    auto_crop: bool = True,
) -> Path:
    """Scan one photo on disk and write the result. Returns the output path."""
    src = Path(image_path)
    out = Path(output_path) if output_path else src.with_name(
        f"{src.stem}_scanned{src.suffix}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    imwrite(out, process_one_image(src, mode=mode, auto_crop=auto_crop))
    return out


def to_a4(img: np.ndarray) -> np.ndarray:
    """Scale a page onto a centred white A4 canvas."""
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


def process_images_to_pdf(
    image_paths: list[str | Path],
    output_pdf: str | Path,
    mode: str = "color",
    auto_crop: bool = True,
) -> Path:
    """Batch process photos and merge into one multi-page PDF."""
    from .pdf import images_to_pdf  # img2pdf is only needed for PDF export

    pages = [
        process_one_image(p, mode=mode, auto_crop=auto_crop) for p in image_paths
    ]
    return images_to_pdf(pages, output_pdf)
