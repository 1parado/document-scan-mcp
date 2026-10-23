"""Convert an existing PDF into a scan-styled PDF.

Each page is rasterized and pushed through the same enhancement pipeline as
photo scans (grayscale-ish enhancement + A4), so an electronic PDF comes out
looking like it went through a scanner.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import process_image, to_a4
from .pdf import images_to_pdf


def pdf_pages_as_images(
    pdf_path: str | Path,
    dpi: int = 150,
    crop_status: list | None = None,
) -> list:
    """Rasterize every page and scan-enhance it. Returns BGR page images."""
    import fitz

    pdf_path = Path(pdf_path)
    pages: list = []
    with fitz.open(str(pdf_path)) as d:
        for i, pno in enumerate(range(d.page_count)):
            pix = d[pno].get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
            status: list = []
            # auto_crop=False: a typeset PDF page already has clean rectangular
            # content; edge detection would only chew the margins.
            page = process_image(img, mode="gray", auto_crop=False, crop_status=status)
            if crop_status is not None:
                crop_status.append(status[0])
            pages.append(to_a4(page))
    return pages


def pdf_to_scanned_pdf(
    pdf_path: str | Path,
    output_path: str | Path,
    preview_max_dim: int = 900,
) -> tuple[Path, list, list]:
    """Full conversion. Returns (pdf_path, pages_bgr, previews_bgr)."""
    pdf_path = Path(pdf_path)
    pages = pdf_pages_as_images(pdf_path)
    previews = []
    for img in pages:
        h, w = img.shape[:2]
        scale = min(1.0, preview_max_dim / max(h, w))
        small = cv2.resize(
            img,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
        previews.append(small)
    out = images_to_pdf(pages, Path(output_path))
    return out, pages, previews
