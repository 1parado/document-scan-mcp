#!/usr/bin/env python3
"""
Simple CLI for the document-scan pipeline (no MCP required).

Examples:
  python cli.py scan photo.jpg
  python cli.py scan photo.jpg --mode bw --out clean.png
  python cli.py pdf page1.jpg page2.jpg page3.jpg -o contract.pdf --mode bw
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from pipeline import process_images_to_pdf, process_one_image


def cmd_scan(args: argparse.Namespace) -> None:
    src = Path(args.image)
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_scanned{src.suffix}")
    result = process_one_image(src, mode=args.mode, auto_crop=not args.no_crop)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), result)
    print(f"Saved: {out.resolve()}")


def cmd_pdf(args: argparse.Namespace) -> None:
    out = Path(args.out)
    result = process_images_to_pdf(
        args.images,
        out,
        mode=args.mode,
        auto_crop=not args.no_crop,
    )
    print(f"Saved: {result}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local CamScanner-style document scanner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Process a single image")
    p_scan.add_argument("image", help="Input image path")
    p_scan.add_argument("-o", "--out", help="Output image path")
    p_scan.add_argument(
        "--mode",
        choices=["color", "gray", "bw"],
        default="color",
        help="Enhancement mode (default: color)",
    )
    p_scan.add_argument("--no-crop", action="store_true", help="Skip auto edge detection")
    p_scan.set_defaults(func=cmd_scan)

    p_pdf = sub.add_parser("pdf", help="Process multiple images → one PDF")
    p_pdf.add_argument("images", nargs="+", help="Input image paths (page order)")
    p_pdf.add_argument("-o", "--out", required=True, help="Output PDF path")
    p_pdf.add_argument(
        "--mode",
        choices=["color", "gray", "bw"],
        default="color",
        help="Enhancement mode (default: color)",
    )
    p_pdf.add_argument("--no-crop", action="store_true", help="Skip auto edge detection")
    p_pdf.set_defaults(func=cmd_pdf)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
