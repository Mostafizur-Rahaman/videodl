FROM python:3.12-slim

WORKDIR /app

# ── System deps ───────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── Clone bgutil — install deps only (already plain JS, no build step) ──
RUN git clone --depth=1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil && \
    cd /bgutil/server && \
    npm install --omit=dev --no-audit --no-fund && \
    # Print the entry point so we can verify
    node -e "const p=require('./package.json'); console.log('entry:', p.main||'server.js')" && \
    ls -la *.js 2>/dev/null || ls -la

# ── Python deps ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt yt-dlp-get-pot

# ── App source ────────────────────────────────────────────────
COPY main.py .
COPY templates/ templates/
RUN mkdir -p downloads

EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}