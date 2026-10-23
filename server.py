"""
Lightweight local MCP server for CamScanner-style document scanning.

Tools:
  - scan_image          : single photo → enhanced image
  - scan_images_to_pdf  : multiple photos → multi-page PDF
  - enhance_only        : enhance without auto-crop
"""
from __future__ import annotations

from pathlib import Path

import cv2
from mcp.server.fastmcp import FastMCP

from pipeline import process_images_to_pdf, process_one_image

mcp = FastMCP("document-scan")


@mcp.tool()
def scan_image(
    image_path: str,
    output_path: str | None = None,
    mode: str = "color",
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
    src = Path(image_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Image not found: {src}")

    if output_path is None:
        out = src.with_name(f"{src.stem}_scanned{src.suffix}")
    else:
        out = Path(output_path).expanduser().resolve()

    result = process_one_image(src, mode=mode, auto_crop=auto_crop)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), result)
    return str(out)


@mcp.tool()
def scan_images_to_pdf(
    image_paths: list[str],
    output_pdf: str,
    mode: str = "color",
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
    mode: str = "bw",
) -> str:
    """
    Only enhance / binarize an image (no edge detection or perspective warp).
    Useful when the photo is already cropped.
    """
    return scan_image(image_path, output_path, mode=mode, auto_crop=False)


if __name__ == "__main__":
    mcp.run()
