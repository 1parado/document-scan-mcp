# document-scan-mcp

Lightweight **local** document scanner (CamScanner-style) exposed as an MCP server and a simple CLI.

**Pipeline:** photo → edge detection → perspective correction → enhance (color / gray / bw) → PNG or multi-page PDF. Also converts **Word / PDF documents into scan-styled PDFs**.

- 100% offline, no cloud, no watermark, no page limits  
- Works as MCP tools for Agents (Claude, Cursor, etc.)  
- CLI + **Web UI** for browser upload / download

## Docker (fastest start)

```bash
docker compose up --build
# then open http://localhost:8765
```

The image bundles LibreOffice (Word→PDF conversion) and CJK fonts. Job
outputs live in a named volume; set `SCAN_JOB_TTL=0` to keep them
indefinitely. Word conversion inside the container uses LibreOffice, so
complex Word layouts render slightly differently than desktop Word.

## Install (from source)

```bash
cd document-scan-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Word→PDF uses Microsoft Word via COM when available (Windows + Word
installed); otherwise it falls back to LibreOffice (`soffice` on PATH),
and finally to a plain-text typesetter — so it degrades gracefully
everywhere.

## CLI (quick test)

```bash
# Single image
python cli.py scan photo.jpg --mode bw

# Multiple images → PDF
python cli.py pdf page1.jpg page2.jpg page3.jpg -o out.pdf --mode bw
```

## Web UI

```bash
# from project root
PYTHONPATH=. python -m uvicorn web.app:app --host 0.0.0.0 --port 8765 \
    --timeout-graceful-shutdown 5
```

Open http://127.0.0.1:8765  

- Drag & drop photos, or drop a single `.docx` / `.pdf` to convert it
- Mode: color / gray / bw  
- Smart crop (with margin protection) + optional A4 unify  
- Streaming progress: pages appear as they finish  
- Preview pages and download PDF  
- Limits: 40 pages and 40 MB per image (tune `MAX_PAGES` / `MAX_UPLOAD_BYTES` in [web/app.py](web/app.py))

Results live in `web/outputs/<job-id>/` and are cleaned up automatically:

| env var | default | effect |
| --- | --- | --- |
| `SCAN_JOB_TTL` | `21600` (6 h) | seconds before a finished job dir is reaped; `0` keeps them while serving |
| `SCAN_REAP_INTERVAL` | `300` | how often the reaper wakes up |
| `SCAN_CLEAN_ON_EXIT` | `true` | purge every job dir on shutdown |
| `SCAN_HOST` / `SCAN_PORT` | `0.0.0.0` / `8765` | bind address when run as `python web/app.py` |

Only 12-hex-character job directories are ever touched, so folders you put in
`web/outputs` yourself survive. `--timeout-graceful-shutdown` matters because the
purge runs *after* uvicorn finishes waiting for in-flight responses: a client
still downloading a large PDF otherwise holds shutdown open indefinitely.

## MCP server

```bash
python server.py
```

Example MCP config (Claude Desktop / Cursor / etc.):

```json
{
  "mcpServers": {
    "document-scan": {
      "command": "/absolute/path/to/document-scan-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/document-scan-mcp/server.py"]
    }
  }
}
```

### Tools

| Tool | Description |
|------|-------------|
| `scan_image` | One photo → enhanced image |
| `scan_images_to_pdf` | Several photos → multi-page PDF |
| `enhance_only` | Enhance only (no auto-crop) |

`mode` options: `color` | `gray` | `bw`

## Project layout

```
document-scan-mcp/
├── server.py          # MCP entry
├── cli.py             # Command-line entry
├── Dockerfile         # docker compose up --build
├── docker-compose.yml
├── promo/             # 15s motion promo page (open promo/index.html)
├── web/
│   ├── app.py         # FastAPI Web UI
│   └── templates/     # index.html
├── pipeline/
│   ├── detect.py      # Edge + corners
│   ├── warp.py        # Perspective transform
│   ├── enhance.py     # Shadow removal + contrast / binarize
│   ├── imio.py        # Unicode-safe image read/write
│   ├── pdf.py         # Images → PDF
│   ├── word2pdf.py    # Word → scan PDF (COM / LibreOffice / text)
│   └── pdf2scan.py    # PDF → scan PDF
└── requirements.txt
```
