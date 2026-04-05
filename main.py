from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import yt_dlp
import os
import base64
import time
from typing import Dict
import threading

app = FastAPI(title="VideoDL")
templates = Jinja2Templates(directory="templates")

# ── No DOWNLOAD_DIR needed — files never touch the server ────

# ─────────────────────────────────────────────────────────────
# Cookies bootstrap
# ─────────────────────────────────────────────────────────────
COOKIES_FILE    = "youtube_cookies.txt"
_COOKIES_STATUS = {"loaded": False, "lines": 0, "error": ""}

def _bootstrap_cookies() -> None:
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        _COOKIES_STATUS["error"] = "YOUTUBE_COOKIES env var not set"
        return
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except Exception:
        decoded = raw

    lines        = [l for l in decoded.splitlines() if l.strip()]
    cookie_lines = [l for l in lines if not l.startswith("#") and "\t" in l]
    if not cookie_lines:
        _COOKIES_STATUS["error"] = "No valid Netscape cookie entries found"
        return

    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        if not any("Netscape" in l for l in lines[:3]):
            f.write("# Netscape HTTP Cookie File\n")
        f.write(decoded)

    _COOKIES_STATUS.update({"loaded": True, "lines": len(cookie_lines), "error": ""})
    print(f"[VideoDL] ✓ cookies loaded — {len(cookie_lines)} entries")

_bootstrap_cookies()


# ─────────────────────────────────────────────────────────────
# In-memory job store  (holds extracted URLs, not files)
# ─────────────────────────────────────────────────────────────
UNCLAIMED_TTL  = 10 * 60     # extracted URLs expire after 10 min
SWEEP_INTERVAL = 60

jobs: Dict[str, dict] = {}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# Background watcher — just purges stale job records (no files)
# ─────────────────────────────────────────────────────────────

def _watcher():
    while True:
        time.sleep(SWEEP_INTERVAL)
        now = time.time()
        with _lock:
            expired = [
                jid for jid, j in jobs.items()
                if (now - j.get("_marked_at", now)) > UNCLAIMED_TTL
            ]
        for jid in expired:
            with _lock:
                jobs.pop(jid, None)

threading.Thread(target=_watcher, daemon=True).start()


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _cookies_opts() -> dict:
    if _COOKIES_STATUS["loaded"] and os.path.exists(COOKIES_FILE):
        return {"cookiefile": COOKIES_FILE}
    return {}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/cookies-status")
async def cookies_status():
    return JSONResponse({
        "status":         _COOKIES_STATUS,
        "yt_dlp_version": yt_dlp.version.__version__,
    })


@app.post("/start-download")
async def start_download(request: Request):
    form = await request.form()
    url  = str(form.get("url", "")).strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    job_id = str(__import__("uuid").uuid4())
    with _lock:
        jobs[job_id] = {
            "status": "queued", "error": None, "title": "",
            "thumbnail": "", "download_url": None, "filename": "",
            "ext": "mp4", "_marked_at": time.time(),
        }

    threading.Thread(target=extract_url, args=(job_id, url), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    with _lock:
        job = jobs.get(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({k: v for k, v in job.items() if not k.startswith("_")})


# ─────────────────────────────────────────────────────────────
# URL extractor  (no download — just resolve the direct CDN URL)
# ─────────────────────────────────────────────────────────────

def extract_url(job_id: str, url: str):
    with _lock:
        jobs[job_id]["status"] = "fetching_info"

    ydl_opts = {
        # Don't download anything — just resolve formats
        "format":        "bestvideo+bestaudio/best",
        "noplaylist":    True,
        "quiet":         True,
        "no_warnings":   True,
        "http_headers":  _HEADERS,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv_embedded", "mweb", "ios"],
            }
        },
        **_cookies_opts(),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            raise ValueError("Could not extract video info.")

        # Unwrap playlist-wrapped singles
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise ValueError("Playlist is empty.")
            info = entries[0]

        title     = info.get("title", "video")
        thumbnail = info.get("thumbnail", "")
        ext       = info.get("ext", "mp4")

        # ── Resolve the best direct URL ──────────────────────
        # For merged formats yt-dlp picks a single "url" field.
        # For split formats it returns "requested_formats" with
        # separate video/audio entries — we take the video one
        # and let the browser download it (audio merged separately
        # is not possible purely client-side without WASM ffmpeg).
        direct_url = info.get("url")

        if not direct_url:
            # Try requested_formats (split video+audio case)
            fmts = info.get("requested_formats") or []
            # Prefer the video stream; it usually contains both on some extractors
            for fmt in fmts:
                if fmt.get("url"):
                    direct_url = fmt["url"]
                    ext        = fmt.get("ext", ext)
                    break

        if not direct_url:
            raise ValueError("Could not resolve a direct download URL.")

        safe_title = "".join(c for c in title if c.isalnum() or c in " ._-")[:80].strip()
        filename   = f"{safe_title}.{ext}" if safe_title else f"video.{ext}"

        with _lock:
            jobs[job_id].update({
                "status":       "done",
                "title":        title,
                "thumbnail":    thumbnail,
                "download_url": direct_url,
                "filename":     filename,
                "ext":          ext,
                "_marked_at":   time.time(),
            })

    except Exception as exc:
        with _lock:
            jobs[job_id].update({
                "status":     "error",
                "error":      str(exc),
                "_marked_at": time.time(),
            })