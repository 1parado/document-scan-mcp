"""
Lightweight local MCP server for CamScanner-style document scanning.

Tools:
  - scan_image          : single photo → enhanced image
  - scan_images_to_pdf  : multiple photos → multi-page PDF
  - enhance_only        : enhance without auto-crop
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from pipeline import process_images_to_pdf, scan_file

Mode = Literal["color", "gray", "bw"]

mcp = FastMCP("document-scan")


def _scan(
    image_path: str,
    output_path: str | None,
    mode: str,
    auto_crop: bool,
) -> str:
    src = Path(image_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Image not found: {src}")

    out = Path(output_path).expanduser().resolve() if output_path else None
    # scan_file picks "<stem>_scanned<suffix>" when out is None and raises if
    # the write fails, so the returned path always exists.
    return str(scan_file(src, out, mode=mode, auto_crop=auto_crop).resolve())


@mcp.tool()
def scan_image(
    image_path: str,
    output_path: str | None = None,
    mode: Mode = "color",
    auto_crop: bool = True,
) -> str:
    """
    Scan a single photo into a clean document image
    (edge detect + perspective correction + enhancement).

    Args:
        image_path: Path to input image (jpg / png / webp / …).
        output_path: Where to write the result. Defaults to <stem>_scanned<suffix>.
        mode: "color" | "gray" | "bw" (black-and-white scan look).
        auto_crop: Detect document edges and warp if True.

    Returns:
        Absolute path of the processed image.
    """
    return _scan(image_path, output_path, mode, auto_crop)


@mcp.tool()
def scan_images_to_pdf(
    image_paths: list[str],
    output_pdf: str,
    mode: Mode = "color",
    auto_crop: bool = True,
) -> str:
    """
    Batch-scan multiple photos and merge them into one multi-page PDF
    (CamScanner-style multi-page export).

    Args:
        image_paths: List of image paths in page order.
        output_pdf: Path for the output PDF.
        mode: "color" | "gray" | "bw".
        auto_crop: Detect edges and warp each page if True.

    Returns:
        Absolute path of the generated PDF.
    """
    paths = [Path(p).expanduser().resolve() for p in image_paths]
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"Image not found: {p}")

    out = Path(output_pdf).expanduser().resolve()
    result = process_images_to_pdf(paths, out, mode=mode, auto_crop=auto_crop)
    return str(result)


@mcp.tool()
def enhance_only(
    image_path: str,
    output_path: str | None = None,
    mode: Mode = "bw",
) -> str:
    """
    Only enhance / binarize an image (no edge detection or perspective warp).
    Useful when the photo is already cropped.
    """
    return _scan(image_path, output_path, mode, auto_crop=False)


if __name__ == "__main__":
    mcp.run()
