FROM python:3.12-slim

WORKDIR /app

# ── System deps: ffmpeg + Node.js + git ──────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── Clone + build bgutil PO token server ─────────────────────
# This TypeScript project must be compiled before it can run
RUN git clone --depth=1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil
WORKDIR /bgutil
RUN npm ci && npm run build
WORKDIR /app

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