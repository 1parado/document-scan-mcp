"""Convert Word documents into scan-style PDFs.

Primary path: Microsoft Word COM automation (Windows + Word installed).
It renders the document exactly as Word would — layout, tables, images,
headers/footers — by exporting the real .docx to PDF. The result then runs
through the same enhancement pipeline as photo scans, so a Word page comes
out looking like a scanned A4 page.

Second path: LibreOffice headless (Linux containers, the Docker image
ships it) — `soffice --convert-to pdf` keeps most layout.

Last fallback (neither available): extract text with python-docx and
typeset it via PyMuPDF. Plain but functional; loses layout.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .imio import imread
from .pdf import images_to_pdf

logger = logging.getLogger("document-scan.word2pdf")


def docx_to_pdf(word_path: str | Path, output_path: str | Path) -> Path:
    """Convert a .docx to a scan-styled PDF at output_path. Returns the path."""
    word_path = Path(word_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        raw_pdf = Path(tmp) / "raw.pdf"

        if _word_com_export(word_path, raw_pdf):
            source = raw_pdf
        elif _libreoffice_export(word_path, raw_pdf):
            source = raw_pdf
        else:
            logger.info("Word COM and LibreOffice unavailable, using text fallback")
            _docx_text_fallback(word_path, raw_pdf)
            source = raw_pdf

        # Rasterize the true-PDF pages and push each through the scan pipeline:
        # slight rotation + shadow + grayscale/whiteboard-style enhancement,
        # then pack back to PDF like any photo scan.
        pages = [_page_to_scan_image(source, i) for i in range(_page_count(source))]
        images_to_pdf(pages, output_path)

    return output_path.resolve()


def _word_com_export(src: Path, dst: Path) -> bool:
    """Export via Word COM. Returns False if Word isn't available (never raises
    for that case) — the caller then uses the text fallback."""
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            try:
                doc = word.Documents.Open(
                    str(src), ReadOnly=True, AddToRecentFiles=False, Visible=False
                )
                # 17 = wdExportFormatPDF; the export options keep bookmarks and
                # print the document exactly as Word prints it.
                doc.ExportAsFixedFormat(
                    OutputFileName=str(dst),
                    ExportFormat=17,
                    OpenAfterExport=False,
                    OptimizeFor=0,  # wdExportOptimizeForPrint (higher quality)
                    CreateBookmarks=0,
                )
                doc.Close(False)
            finally:
                word.Quit()
        finally:
            pythoncom.CoUninitialize()
        return dst.exists()
    except Exception as e:
        logger.info("Word COM export failed (%s), will try text fallback", e)
        return False


def _libreoffice_export(src: Path, dst: Path) -> bool:
    """Convert via `soffice --headless`. Returns False when LibreOffice isn't
    installed (the Docker image installs it; bare Linux users can too)."""
    import shutil
    import subprocess

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    try:
        # -env:UserInstallation avoids clashing with any running LibreOffice.
        result = subprocess.run(
            [
                soffice, "--headless", "--norestore",
                "-env:UserInstallation=file:///tmp/lo_scan_profile",
                "--convert-to", "pdf", "--outdir", str(dst.parent), str(src),
            ],
            capture_output=True, timeout=120, check=True,
        )
        produced = dst.parent / (src.stem + ".pdf")
        if produced.exists():
            produced.replace(dst)
            return True
        return False
    except (subprocess.SubprocessError, OSError) as e:
        logger.info("LibreOffice export failed (%s), trying next fallback", e)
        return False


def _docx_text_fallback(src: Path, dst: Path) -> None:
    """Extract paragraphs with python-docx and typeset them with PyMuPDF."""
    import docx
    import fitz

    document = docx.Document(str(src))
    doc = fitz.open()

    # A4 in points, standard margins.
    W, H, MARGIN = 595.28, 841.89, 56.7
    usable = W - 2 * MARGIN

    page = None
    y = 0.0
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            y += 6  # blank line
            continue
        size = 12.0
        if para.style.name.startswith("Heading"):
            size = 16.0 if para.style.name == "Heading 1" else 14.0

        # Word-wrap with PyMuPDF's measurer so nothing overflows the margin.
        font = fitz.Font("helv")
        for line in _wrap(text, font, size, usable):
            if page is None or y > H - MARGIN:
                page = doc.new_page(width=W, height=H)
                y = MARGIN + size
            page.insert_text((MARGIN, y), line, fontsize=size, fontname="helv")
            y += size * 1.55

    if page is None:  # empty document: still emit one blank page
        doc.new_page(width=W, height=H)
    doc.save(str(dst))
    doc.close()

    # CJK text can't render in base-14 helv; PyMuPDF drops those glyphs
    # silently. If the doc was mostly CJK this fallback is weak by design —
    # the real fix is Word COM, which the log line above already suggests.


def _wrap(text: str, font: fitz.Font, size: float, width: float) -> list[str]:
    lines: list[str] = []
    while text:
        cut = len(text)
        while cut > 1 and font.text_length(text[:cut], size) > width:
            cut = max(1, cut - 1)
        lines.append(text[:cut])
        text = text[cut:]
    return lines or [""]


def _page_count(pdf: Path) -> int:
    import fitz

    with fitz.open(str(pdf)) as d:
        return d.page_count


def _page_to_scan_image(pdf: Path, index: int, dpi: int = 150) -> np.ndarray:
    """Render one PDF page and push it through the scan enhancement pipeline."""
    import fitz

    from . import process_image, to_a4

    with fitz.open(str(pdf)) as d:
        pix = d[index].get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    # Same treatment a photo gets. auto_crop=False: a typeset page already has
    # clean rectangular content, edge detection would only chew the margins.
    scanned = process_image(img, mode="gray", auto_crop=False)
    return to_a4(scanned)


def word_pages_as_images(
    word_path: str | Path,
    preview_max_dim: int = 900,
) -> tuple[list, list]:
    """Full conversion returning (pages_bgr, previews_bgr) so the web layer can
    reuse its existing preview/JPEG plumbing without a second conversion."""
    word_path = Path(word_path)
    with tempfile.TemporaryDirectory() as tmp:
        raw_pdf = Path(tmp) / "raw.pdf"
        if _word_com_export(word_path, raw_pdf):
            source = raw_pdf
        else:
            _docx_text_fallback(word_path, raw_pdf)
            source = raw_pdf
        n = _page_count(source)
        pages = [_page_to_scan_image(source, i) for i in range(n)]
    previews = []
    for img in pages:
        h, w = img.shape[:2]
        scale = min(1.0, preview_max_dim / max(h, w))
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
        previews.append(small)
    return pages, previews


def docx_to_pdf_with_previews(
    word_path: str | Path,
    output_path: str | Path,
    preview_max_dim: int = 900,
) -> tuple[Path, list]:
    """Convert and also return downscaled previews for the web result row."""
    pages, previews = word_pages_as_images(word_path, preview_max_dim)
    out = images_to_pdf(pages, Path(output_path))
    return out.resolve(), previews
