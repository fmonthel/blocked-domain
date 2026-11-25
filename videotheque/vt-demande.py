#!/usr/bin/env python3
"""Vidéothèque : téléchargement à la demande (chaînes « mode=demand » de chaines.conf).

Service systemd videotheque-demande (utilisateur « videotheque »), écoute 127.0.0.1:8091 ;
nginx lui transmet /api/ (mêmes limites que le reste du site pour l'extérieur).

  GET /api/prepare?id=<vidéo>  -> met la vidéo en file d'attente si elle n'est pas encore copiée
  GET /api/status?id=<vidéo>   -> {"state": "ready"|"queued"|"downloading"|"error"|"unknown", ...}
  GET /api/health              -> {"ok": true, "queue": n, "today": n}

Garde-fous : seules les vidéos du catalogue d'une chaîne « demand » sont acceptées (impossible de
faire télécharger n'importe quelle vidéo YouTube au serveur), une vidéo à la fois, file de 5 au plus,
60 téléchargements par jour au plus. Mêmes options yt-dlp que vt-sync.py (720p, remux, archive).
"""
import datetime as dt, glob, json, os, queue, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = "/srv/videotheque"
MEDIA = f"{BASE}/media"
LIBRARY = f"{BASE}/library.json"
ARCHIVE = f"{BASE}/archive.txt"
YTDLP = "/opt/videotheque/venv/bin/yt-dlp"
QJS = "/usr/local/bin/qjs"
HEIGHT = 720
MAX_QUEUE = 5
MAX_PER_DAY = 60
ID_RX = re.compile(r"^[A-Za-z0-9_-]{11}$")

jobs = {}            # id -> {"state", "pct", "phase", "error", "f", "ts"}
todo = queue.Queue()
lock = threading.Lock()
daily = {"day": "", "n": 0}
lib = {"mtime": 0, "demand": {}}   # id -> channel_id, pour les vidéos des chaînes « demand »


def log(msg):
    print(f"{time.strftime('%F %T')} {msg}", flush=True)


def demand_ids():
    try:
        m = os.path.getmtime(LIBRARY)
        if m != lib["mtime"]:
            with open(LIBRARY, encoding="utf-8") as f:
                j = json.load(f)
            lib["demand"] = {v["id"]: v["c"] for v in j.get("videos", []) if v.get("dm")}
            lib["mtime"] = m
    except (OSError, ValueError):
        pass
    return lib["demand"]


def local_file(vid, cid):
    hits = glob.glob(f"{MEDIA}/{cid}/*_{vid}.mp4")
    return f"media/{cid}/{os.path.basename(hits[0])}" if hits else None


def worker():
    fmt = (f"bv*[height<={HEIGHT}][vcodec^=avc1]+ba[ext=m4a]/b[height<={HEIGHT}][ext=mp4]/"
           f"bv*[height<={HEIGHT}]+ba/b[height<={HEIGHT}]")
    while True:
        vid, cid = todo.get()
        with lock:
            jobs[vid].update(state="downloading", pct=0, phase="video")
        log(f"téléchargement {vid} ({cid})")
        cmd = [YTDLP, "--no-js-runtimes", "--js-runtimes", f"quickjs:{QJS}", "--ignore-config",
               "--newline", "--retries", "5", "--fragment-retries", "5",
               "-f", fmt, "--merge-output-format", "mp4",
               "-o", f"{MEDIA}/%(channel_id)s/%(upload_date)s_%(id)s.%(ext)s",
               "--download-archive", ARCHIVE, "--write-info-json", "--write-thumbnail",
               "--convert-thumbnails", "jpg", "--no-overwrites",
               f"https://www.youtube.com/watch?v={vid}"]
        tail, phase = [], 0
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 env=dict(os.environ, XDG_CACHE_HOME=f"{BASE}/.cache"))
            for line in p.stdout:
                tail = (tail + [line.rstrip()])[-20:]
                if "[download] Destination" in line:
                    phase += 1
                    with lock:
                        jobs[vid]["phase"] = "video" if phase <= 1 else "audio"
                m = re.search(r"\[download\]\s+([\d.]+)%", line)
                if m:
                    pct = float(m.group(1))
                    with lock:   # vidéo = 0-85 %, son = 85-98 %, assemblage = 98-100 %
                        jobs[vid]["pct"] = round(pct * 0.85 if phase <= 1 else 85 + pct * 0.13)
                if "[Merger]" in line:
                    with lock:
                        jobs[vid].update(pct=98, phase="merge")
            rc = p.wait()
        except Exception as e:
            rc, tail = -1, tail + [str(e)]
        f = local_file(vid, cid)
        with lock:
            if f:
                jobs[vid].update(state="ready", pct=100, f=f, ts=time.time())
                log(f"prêt {vid}")
            else:
                err = next((l for l in reversed(tail) if "ERROR" in l), f"yt-dlp code {rc}")
                jobs[vid].update(state="error", error=err[:200], ts=time.time())
                log(f"échec {vid}: {err[:200]}")
        todo.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = "videotheque"

    def log_message(self, *a):
        pass   # nginx journalise déjà les requêtes

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health":
            return self.reply(200, {"ok": True, "queue": todo.qsize(), "today": daily["n"]})
        vid = (parse_qs(u.query).get("id") or [""])[0]
        if u.path not in ("/api/prepare", "/api/status") or not ID_RX.match(vid):
            return self.reply(404, {"error": "not found"})
        cid = demand_ids().get(vid)
        if not cid:
            return self.reply(404, {"state": "unknown", "error": "not an on-demand video"})
        f = local_file(vid, cid)
        if f:
            return self.reply(200, {"state": "ready", "f": f, "pct": 100})
        with lock:
            job = jobs.get(vid)
            if u.path == "/api/prepare" and (not job or (job["state"] == "error" and time.time() - job["ts"] > 300)):
                today = dt.date.today().isoformat()
                if daily["day"] != today:
                    daily.update(day=today, n=0)
                if daily["n"] >= MAX_PER_DAY:
                    return self.reply(429, {"state": "error", "error": "daily limit reached, try tomorrow"})
                if todo.qsize() >= MAX_QUEUE:
                    return self.reply(429, {"state": "error", "error": "too many videos being prepared, try again soon"})
                daily["n"] += 1
                job = jobs[vid] = {"state": "queued", "pct": 0, "phase": "", "ts": time.time()}
                todo.put((vid, cid))
            if not job:
                return self.reply(200, {"state": "unknown"})
            data = dict(job)
            if data["state"] == "queued":
                data["position"] = list(todo.queue).index((vid, cid)) + 1 if (vid, cid) in todo.queue else 1
            return self.reply(200, data)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    log("à l'écoute sur 127.0.0.1:8091")
    ThreadingHTTPServer(("127.0.0.1", 8091), Handler).serve_forever()
