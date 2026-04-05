FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg + Node.js
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install bgutil PO token server globally
RUN npm install -g @ybd-project/bgutil-ytdlp-pot-provider

# Verify the server entrypoint exists and print its path
RUN node -e "require('@ybd-project/bgutil-ytdlp-pot-provider')" 2>/dev/null || true
RUN ls $(npm root -g)/@ybd-project/bgutil-ytdlp-pot-provider/build/

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp-get-pot plugin (connects yt-dlp to bgutil server)
RUN pip install --no-cache-dir yt-dlp-get-pot

# Copy app source
COPY main.py .
COPY templates/ templates/

RUN mkdir -p downloads

# Store the global node_modules path so Python can find it
RUN node -e "console.log(require('path').join(require('child_process').execSync('npm root -g').toString().trim(), '@ybd-project/bgutil-ytdlp-pot-provider/build/server.js'))" > /bgutil_path.txt
RUN cat /bgutil_path.txt

EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}