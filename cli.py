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

from pipeline import process_images_to_pdf, scan_file


def cmd_scan(args: argparse.Namespace) -> None:
    src = Path(args.image).expanduser()
    if not src.exists():
        raise SystemExit(f"Image not found: {src}")
    out = Path(args.out).expanduser() if args.out else None
    result = scan_file(src, out, mode=args.mode, auto_crop=not args.no_crop)
    print(f"Saved: {result.resolve()}")


def cmd_pdf(args: argparse.Namespace) -> None:
    paths = [Path(p).expanduser() for p in args.images]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit("Image not found: " + ", ".join(str(m) for m in missing))
    result = process_images_to_pdf(
        paths,
        Path(args.out).expanduser(),
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
