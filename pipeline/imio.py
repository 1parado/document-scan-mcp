"""Image IO that survives non-ASCII paths.

cv2.imread/cv2.imwrite only accept paths the C runtime can encode, so on
Windows they silently fail (None / False) for any path containing characters
outside the active code page -- e.g. a Chinese user profile or project folder.
Decoding from bytes removes the filename from the equation entirely.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def decode(data: bytes) -> np.ndarray | None:
    """Decode a BGR image from raw bytes, or None if it is not an image."""
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def imread(path: str | Path) -> np.ndarray | None:
    """Decode a BGR image from a path. Returns None if unreadable or undecodable."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return decode(data)


def imwrite(path: str | Path, image: np.ndarray, params: list | None = None) -> Path:
    """Encode and write an image.

    Raises OSError instead of returning False, so a failed write cannot be
    mistaken for a success by the caller.
    """
    target = Path(path)
    suffix = target.suffix or ".png"
    try:
        ok, buf = cv2.imencode(suffix, image, params if params is not None else [])
    except cv2.error as e:
        # OpenCV raises for an unsupported extension instead of returning False.
        raise OSError(f"Cannot encode image as {suffix!r} for {target}: {e}") from e
    if not ok:
        raise OSError(f"Cannot encode image as {suffix!r} for {target}")
    target.write_bytes(buf.tobytes())
    return target
