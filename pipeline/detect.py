"""Document edge detection and corner finding."""
from __future__ import annotations

import cv2
import numpy as np


def order_points(pts: np.ndarray) -> np.ndarray:
    """Order four points as TL, TR, BR, BL."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left
    rect[2] = pts[np.argmax(s)]  # bottom-right
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect


def find_document_corners(image: np.ndarray) -> np.ndarray | None:
    """
    Find document quadrilateral corners.

    Returns (4, 2) float32 array ordered TL/TR/BR/BL, or None if not found.
    """
    h, w = image.shape[:2]
    # Work on a fixed-height image for stable thresholds
    target_h = 800
    ratio = target_h / float(h)
    small = cv2.resize(image, (int(w * ratio), target_h))

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    # Dilate to connect broken edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:15]

    img_area = small.shape[0] * small.shape[1]
    for c in contours:
        area = cv2.contourArea(c)
        if area < img_area * 0.15:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2).astype("float32") / ratio
            return order_points(pts)
    return None
