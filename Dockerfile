FROM python:3.12-slim

WORKDIR /app

# ── System deps: ffmpeg + Node.js + git ──────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── Clone bgutil + auto-find package.json + build ────────────
RUN git clone --depth=1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil

RUN PKG_DIR=$(dirname "$(find /bgutil -name 'package.json' -not -path '*/node_modules/*' | head -1)") && \
    echo "==> Building from: $PKG_DIR" && \
    cd "$PKG_DIR" && \
    npm install && \
    npm run build && \
    find "$PKG_DIR" -name "server.js" -path "*/build/*" | head -1 > /bgutil_server_path.txt && \
    echo "==> Server JS:" && cat /bgutil_server_path.txt

# ── Python deps ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── yt-dlp-get-pot: bridges yt-dlp ↔ bgutil server ──────────
RUN pip install --no-cache-dir yt-dlp-get-pot

# ── App source ────────────────────────────────────────────────
COPY main.py .
COPY templates/ templates/
RUN mkdir -p downloads

EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}