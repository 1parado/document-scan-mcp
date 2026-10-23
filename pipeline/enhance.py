"""Shadow removal and document enhancement (CamScanner-style)."""
from __future__ import annotations

import cv2
import numpy as np


def remove_shadow(bgr: np.ndarray) -> np.ndarray:
    """Normalize background to reduce uneven lighting / shadows."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    planes = cv2.split(rgb)
    result_planes = []
    for plane in planes:
        dilated = cv2.dilate(plane, np.ones((7, 7), np.uint8))
        bg = cv2.medianBlur(dilated, 21)
        diff = 255 - cv2.absdiff(plane, bg)
        norm = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)
        result_planes.append(norm)
    result = cv2.merge(result_planes)
    return cv2.cvtColor(result, cv2.COLOR_RGB2BGR)


def enhance_document(bgr: np.ndarray, mode: str = "color") -> np.ndarray:
    """
    Enhance a document image.

    mode:
      - color : shadow removal + mild sharpen
      - gray  : grayscale + CLAHE contrast
      - bw    : adaptive threshold (classic scan look)
    """
    cleaned = remove_shadow(bgr)

    if mode == "color":
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        return cv2.filter2D(cleaned, -1, kernel)

    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)

    if mode == "gray":
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    # bw – adaptive binary (CamScanner-like)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        15,
        8,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
