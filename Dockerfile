FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg via apt (reliable on Debian-based images)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY main.py .
COPY templates/ templates/

# Runtime downloads folder
RUN mkdir -p downloads

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
