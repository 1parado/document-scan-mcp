"""Document scan pipeline: detect → warp → enhance → PDF."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .detect import find_document_corners
from .enhance import enhance_document
from .pdf import images_to_pdf
from .warp import four_point_transform


def process_one_image(
    image_path: str | Path,
    mode: str = "color",
    auto_crop: bool = True,
) -> np.ndarray:
    """
    Full pipeline for a single photo → clean document image.

    Returns BGR numpy array.
    """
    path = Path(image_path)
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {path}")

    if auto_crop:
        corners = find_document_corners(img)
        if corners is not None:
            img = four_point_transform(img, corners)
        # else: keep original (no reliable edges found)

    return enhance_document(img, mode=mode)


def process_images_to_pdf(
    image_paths: list[str | Path],
    output_pdf: str | Path,
    mode: str = "color",
    auto_crop: bool = True,
) -> Path:
    """Batch process photos and merge into one multi-page PDF."""
    pages = [
        process_one_image(p, mode=mode, auto_crop=auto_crop) for p in image_paths
    ]
    return images_to_pdf(pages, output_pdf)
