"""Perspective transform (document unwarp)."""
from __future__ import annotations

import cv2
import numpy as np


def four_point_transform(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """
    Warp image so the quadrilateral defined by pts becomes a rectangle.

    Output width is at least 1200 px for decent print quality.
    """
    (tl, tr, br, bl) = pts

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_w = max(int(width_a), int(width_b), 1)

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_h = max(int(height_a), int(height_b), 1)

    # Upscale thin captures so text stays sharp
    scale = max(1.0, 1200.0 / max_w)
    max_w = int(max_w * scale)
    max_h = int(max_h * scale)

    dst = np.array(
        [
            [0, 0],
            [max_w - 1, 0],
            [max_w - 1, max_h - 1],
            [0, max_h - 1],
        ],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(pts.astype("float32"), dst)
    warped = cv2.warpPerspective(image, matrix, (max_w, max_h))
    return warped
