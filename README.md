# document-scan-mcp

Lightweight **local** document scanner (CamScanner-style) exposed as an MCP server and a simple CLI.

**Pipeline:** photo → edge detection → perspective correction → enhance (color / gray / bw) → PNG or multi-page PDF.

- 100% offline, no cloud, no watermark, no page limits  
- Works as MCP tools for Agents (Claude, Cursor, etc.)  
- CLI + **Web UI** for browser upload / download

## Install

```bash
cd document-scan-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

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
PYTHONPATH=. python -m uvicorn web.app:app --host 0.0.0.0 --port 8765
```

Open http://127.0.0.1:8765  

- Drag & drop multiple photos  
- Mode: color / gray / bw  
- Smart crop (with margin protection) + optional A4 unify  
- Preview pages and download PDF  

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
├── web/
│   ├── app.py         # FastAPI Web UI
│   └── templates/     # index.html
├── pipeline/
│   ├── detect.py      # Edge + corners
│   ├── warp.py        # Perspective transform
│   ├── enhance.py     # Shadow removal + contrast / binarize
│   └── pdf.py         # Images → PDF
└── requirements.txt
```
