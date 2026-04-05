FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg + Node.js
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install bgutil PO token server from GitHub (correct source)
RUN npm install -g https://github.com/Brainicism/bgutil-ytdlp-pot-provider

# Write the server.js path so Python can find it at runtime
RUN node -e "const p=require.resolve('bgutil-ytdlp-pot-provider/build/server.js'); console.log(p);" > /bgutil_path.txt 2>/dev/null || \
    find /usr/local/lib/node_modules /usr/lib/node_modules -name "server.js" -path "*/bgutil*" 2>/dev/null | head -1 > /bgutil_path.txt
RUN echo "bgutil path:" && cat /bgutil_path.txt

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp-get-pot plugin (bridges yt-dlp ↔ bgutil server)
RUN pip install --no-cache-dir yt-dlp-get-pot

# Copy app source
COPY main.py .
COPY templates/ templates/

RUN mkdir -p downloads

EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}