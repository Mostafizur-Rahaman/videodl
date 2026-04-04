from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import yt_dlp
import uuid
import os
import base64
import shutil
import time
from typing import Dict
import threading

app = FastAPI(title="VideoDL")
templates = Jinja2Templates(directory="templates")

DOWNLOAD_DIR = "downloads"
COOKIES_FILE  = "youtube_cookies.txt"   # written from env var at startup
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# Cookies bootstrap
# Set YOUTUBE_COOKIES in Railway Variables as the raw Netscape
# cookies.txt content (or base64-encoded — both supported).
# ─────────────────────────────────────────────────────────────

def _bootstrap_cookies() -> None:
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        return
    # Support both plain-text and base64-encoded values
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except Exception:
        decoded = raw
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(decoded)
    print(f"[VideoDL] cookies written → {COOKIES_FILE}")

_bootstrap_cookies()


# ─────────────────────────────────────────────────────────────
# Tunable cleanup constants
# ─────────────────────────────────────────────────────────────
SAVE_DELETE_DELAY = 1 * 60
UNCLAIMED_TTL     = 5 * 60
ERROR_TTL         = 2 * 60
SWEEP_INTERVAL    = 60

jobs: Dict[str, dict] = {}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _purge_job(job_id: str) -> None:
    shutil.rmtree(os.path.join(DOWNLOAD_DIR, job_id), ignore_errors=True)
    with _lock:
        jobs.pop(job_id, None)


def _cookies_opts() -> dict:
    """Return cookiefile option only if the file exists and is non-empty."""
    if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0:
        return {"cookiefile": COOKIES_FILE}
    return {}


# ─────────────────────────────────────────────────────────────
# Background watcher
# ─────────────────────────────────────────────────────────────

def _sweep_orphan_folders():
    try:
        for name in os.listdir(DOWNLOAD_DIR):
            folder = os.path.join(DOWNLOAD_DIR, name)
            if os.path.isdir(folder) and name not in jobs:
                shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        pass


def _watcher():
    _sweep_orphan_folders()
    while True:
        time.sleep(SWEEP_INTERVAL)
        now = time.time()
        with _lock:
            snapshot = list(jobs.items())
        for job_id, job in snapshot:
            status    = job.get("status")
            marked_at = job.get("_marked_at", now)
            if   status == "done"  and (now - marked_at) > UNCLAIMED_TTL:
                _purge_job(job_id)
            elif status == "error" and (now - marked_at) > ERROR_TTL:
                _purge_job(job_id)
        _sweep_orphan_folders()


threading.Thread(target=_watcher, daemon=True).start()


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/start-download")
async def start_download(request: Request):
    form = await request.form()
    url  = str(form.get("url", "")).strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    job_id = str(uuid.uuid4())
    with _lock:
        jobs[job_id] = {
            "status": "queued", "progress": 0, "speed": "", "eta": "",
            "filename": None, "filepath": None, "title": "",
            "thumbnail": "", "filesize": "", "error": None,
            "_marked_at": time.time(),
        }

    threading.Thread(target=download_video, args=(job_id, url), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    with _lock:
        job = jobs.get(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({k: v for k, v in job.items() if not k.startswith("_")})


@app.get("/file/{job_id}")
async def serve_file(job_id: str):
    with _lock:
        job = jobs.get(job_id)
    if not job or not job.get("filepath"):
        return JSONResponse({"error": "File not found"}, status_code=404)

    path = job["filepath"]
    if not os.path.exists(path):
        return JSONResponse({"error": "File already deleted or missing"}, status_code=404)

    title      = job.get("title", "video")
    safe_title = "".join(c for c in title if c.isalnum() or c in " ._-")[:80].strip()
    ext        = os.path.splitext(path)[1]
    dl_name    = f"{safe_title}{ext}" if safe_title else os.path.basename(path)

    with _lock:
        jobs[job_id]["status"]     = "served"
        jobs[job_id]["_marked_at"] = time.time()

    def _delete_after_save():
        time.sleep(SAVE_DELETE_DELAY)
        _purge_job(job_id)

    threading.Thread(target=_delete_after_save, daemon=True).start()
    return FileResponse(path, filename=dl_name, media_type="application/octet-stream")


# ─────────────────────────────────────────────────────────────
# Download worker
# ─────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def download_video(job_id: str, url: str):
    with _lock:
        jobs[job_id]["status"] = "fetching_info"

    final_filepath: list[str] = []

    def progress_hook(d):
        if d["status"] == "downloading":
            total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            pct        = int(downloaded / total * 100) if total else 0
            with _lock:
                jobs[job_id].update({
                    "status": "downloading", "progress": pct,
                    "speed":  d.get("_speed_str", "").strip(),
                    "eta":    d.get("_eta_str",   "").strip(),
                })
                if total:
                    jobs[job_id]["filesize"] = human_size(total)
        elif d["status"] == "finished":
            fpath = d.get("filename", "")
            if fpath and os.path.exists(fpath):
                final_filepath.clear()
                final_filepath.append(fpath)
            with _lock:
                jobs[job_id]["status"]   = "processing"
                jobs[job_id]["progress"] = 100

    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    _base = {
        "outtmpl":             os.path.join(job_dir, "%(title).80s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist":          True,
        "quiet":               True,
        "no_warnings":         True,
        "writesubtitles":      False,
        "subtitleslangs":      ["en"],
        "writeautomaticsub":   False,
        "writethumbnail":      False,
        "retries":             5,
        "fragment_retries":    5,
        "restrictfilenames":   True,
        "overwrites":          False,
        "http_headers":        _HEADERS,
        # YouTube bot-check bypass options
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "ios"],   # try web first, fall back to ios
            }
        },
        **_cookies_opts(),   # inject cookiefile if available
    }

    _info_opts = {**_base, "ignoreerrors": False, "format": "best"}
    _dl_opts   = {**_base, "format": "bestvideo+bestaudio/best",
                  "ignoreerrors": True, "progress_hooks": [progress_hook]}

    try:
        with yt_dlp.YoutubeDL(_info_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            raise ValueError(
                "Could not extract video info — URL may be private, "
                "geo-blocked, or unsupported."
            )

        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise ValueError("Playlist is empty or all entries failed.")
            info = entries[0]

        with _lock:
            jobs[job_id]["title"]     = info.get("title",     "video")
            jobs[job_id]["thumbnail"] = info.get("thumbnail", "")
            jobs[job_id]["status"]    = "downloading"

        with yt_dlp.YoutubeDL(_dl_opts) as ydl:
            ydl.download([url])

        found = final_filepath[0] if final_filepath else None
        if not found or not os.path.exists(found):
            candidates = [
                os.path.join(job_dir, f) for f in os.listdir(job_dir)
                if not f.endswith((".part", ".ytdl", ".json"))
            ]
            if candidates:
                found = max(candidates, key=os.path.getsize)

        if not found or not os.path.exists(found):
            raise FileNotFoundError("Download finished but output file could not be located.")

        with _lock:
            jobs[job_id].update({
                "filename":   os.path.basename(found),
                "filepath":   found,
                "filesize":   human_size(os.path.getsize(found)),
                "status":     "done",
                "progress":   100,
                "_marked_at": time.time(),
            })

    except Exception as exc:
        shutil.rmtree(os.path.join(DOWNLOAD_DIR, job_id), ignore_errors=True)
        with _lock:
            jobs[job_id].update({
                "status":     "error",
                "error":      str(exc),
                "progress":   0,
                "_marked_at": time.time(),
            })