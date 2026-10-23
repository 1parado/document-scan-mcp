"""Pack processed page images into a single PDF."""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import img2pdf

from .imio import imwrite


def images_to_pdf(
    images_bgr: list,
    output_path: str | Path,
    dpi: int = 200,
) -> Path:
    """
    Write a list of BGR numpy images into one PDF.

    Uses PNG intermediates so quality stays high; img2pdf embeds them losslessly.
    dpi fixes the physical page size: OpenCV writes PNGs without DPI metadata,
    so without it img2pdf assumes 96 dpi and an A4 raster lands on an A2 page.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        png_paths: list[Path] = []
        for i, img in enumerate(images_bgr):
            p = Path(tmp) / f"page_{i:03d}.png"
            # Optional mild downscale if huge (keeps PDF reasonable)
            h, w = img.shape[:2]
            if max(h, w) > 3500:
                scale = 3500.0 / max(h, w)
                img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            imwrite(p, img)
            png_paths.append(p)

        layout_fun = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
        # Default: one image per page, preserve aspect ratio
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(png_paths, layout_fun=layout_fun))

    return output_path.resolve()
