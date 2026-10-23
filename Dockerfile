# Document Scan — web UI + API in one container.
# LibreOffice is included so Word conversion works without MS Word
# (the Word COM path is Windows-only; the container uses the fallback).
# Build:  docker build -t document-scan .
# Run:    docker run --rm -p 8765:8765 document-scan
FROM python:3.12-slim

# LibreOffice headless powers docx→pdf; fonts-noto-cjk keeps Chinese
# documents from turning into boxes in the text fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY web/ ./web/

# Job outputs live here; mount a volume to keep them across restarts.
RUN mkdir -p /app/web/outputs
ENV SCAN_HOST=0.0.0.0 \
    SCAN_PORT=8765 \
    SCAN_CLEAN_ON_EXIT=true
VOLUME ["/app/web/outputs"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/',timeout=3)"

CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8765"]
