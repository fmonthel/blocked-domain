#!/usr/bin/env python3
"""Vidéothèque : copie locale des chaînes YouTube listées dans chaines.conf.

Lancé chaque nuit par /etc/cron.d/videotheque (utilisateur « videotheque »).
1. Pour chaque chaîne : nom, avatar et bannière (une fois par semaine).
2. Passe « nouveautés » : les vidéos plus récentes que la dernière déjà copiée (--break-on-existing).
3. Passe « rattrapage » : tant qu'une chaîne n'est pas complète (première copie), ou le dimanche
   pour toutes, on remonte l'historique avec le budget restant de la nuit.
4. Reconstruit library.json (lu par le site) et status.json (lu par la supervision).

720p max, H.264 + AAC en mp4 : simple remux par ffmpeg, aucun réencodage (le Celeron ne suivrait pas).
Pause de 15 à 45 s entre deux vidéos et budget par nuit pour ne pas se faire bloquer par YouTube.

Réglages locaux facultatifs : /etc/videotheque/local.conf
    [vt]
    max_par_nuit = 150
    duree_max_heures = 5
    hauteur_max = 720

Usage : vt-sync.py [--index-only] [--max N]
"""
import configparser, datetime as dt, fcntl, json, os, re, subprocess, sys, time, urllib.request

BASE = "/srv/videotheque"
MEDIA = f"{BASE}/media"
ARCHIVE = f"{BASE}/archive.txt"
STATE = f"{BASE}/state.json"
LIBRARY = f"{BASE}/library.json"
STATUS = f"{BASE}/status.json"
CHANNELS_CONF = "/opt/videotheque/chaines.conf"
LOCAL_CONF = "/etc/videotheque/local.conf"
YTDLP = "/opt/videotheque/venv/bin/yt-dlp"
QJS = "/usr/local/bin/qjs"
NEW_PER_CHANNEL = 30           # passe nouveautés : au plus N vidéos par chaîne et par nuit
DESC_MAX = 1500                # description gardée dans library.json


def log(msg):
    print(f"{time.strftime('%F %T')} {msg}", flush=True)


def settings():
    c = configparser.ConfigParser()
    c.read(LOCAL_CONF)
    s = c["vt"] if c.has_section("vt") else {}
    get = lambda k, d: int(s.get(k, d)) if s else d
    return {"max": get("max_par_nuit", 150), "hours": get("duree_max_heures", 5), "height": get("hauteur_max", 720)}


def read_channels():
    urls = []
    with open(CHANNELS_CONF, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                urls.append(line.rstrip("/").removesuffix("/videos").removesuffix("/featured"))
    return urls


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data, compact=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def archive_count():
    try:
        with open(ARCHIVE) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def ytdlp_base():
    return [YTDLP, "--no-js-runtimes", "--js-runtimes", f"quickjs:{QJS}",
            "--ignore-config", "--no-progress", "--newline", "--retries", "5", "--fragment-retries", "5"]


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".tmp", "wb") as f:
        f.write(r.read())
    os.replace(dest + ".tmp", dest)


def channel_info(url, chs):
    """Nom, id, avatar, bannière de la chaîne (rafraîchi une fois par semaine)."""
    known = chs.get(url, {})
    if known.get("id") and time.time() - known.get("meta_ts", 0) < 7 * 86400:
        return known
    r = subprocess.run(ytdlp_base() + ["--flat-playlist", "-J", "--playlist-items", "0", url + "/videos"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        log(f"  infos de chaîne impossibles pour {url}: {r.stderr.strip()[-300:]}")
        return known
    j = json.loads(r.stdout)
    cid = j.get("channel_id") or j.get("id")
    d = f"{MEDIA}/{cid}"
    os.makedirs(d, exist_ok=True)
    thumbs = {t.get("id", ""): t.get("url") for t in j.get("thumbnails") or [] if t.get("url")}
    for kind, keys in (("avatar", ("avatar_uncropped",)), ("banner", ("banner_uncropped",))):
        u = next((thumbs[k] for k in keys if k in thumbs), None) or \
            next((v for k, v in thumbs.items() if kind in k), None)
        if u:
            try:
                fetch(u, f"{d}/{kind}.jpg")
            except Exception as e:  # image facultative
                log(f"  {kind} non téléchargé: {e}")
    info = {"id": cid, "name": j.get("channel") or j.get("title") or cid,
            "handle": j.get("uploader_id") or "", "desc": (j.get("description") or "")[:DESC_MAX],
            "complete": known.get("complete", False), "meta_ts": time.time()}
    save_json(f"{d}/channel.json", info)
    return info


def download(url, max_dl, deadline, break_on_existing, height):
    """Lance yt-dlp sur l'onglet Vidéos. Retourne (nouvelles vidéos, terminé, erreur fatale)."""
    if max_dl <= 0 or time.time() >= deadline:
        return 0, False, None
    fmt = (f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/"
           f"b[height<={height}][ext=mp4]/bv*[height<={height}]+ba/b[height<={height}]")
    cmd = ytdlp_base() + [
        "-f", fmt, "--merge-output-format", "mp4",
        "-o", f"{MEDIA}/%(channel_id)s/%(upload_date)s_%(id)s.%(ext)s",
        "--download-archive", ARCHIVE, "--max-downloads", str(max_dl),
        "--write-info-json", "--no-write-playlist-metafiles",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--match-filters", "!is_live & live_status!=is_upcoming & live_status!=was_live",
        "--ignore-errors", "--no-overwrites",
        "--sleep-requests", "1", "--sleep-interval", "15", "--max-sleep-interval", "45",
    ]
    if break_on_existing:
        cmd.append("--break-on-existing")
    cmd.append(url + "/videos")
    before = archive_count()
    tail, fatal = [], None
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            line = line.rstrip()
            if "[download] Destination" in line or "ERROR" in line or "WARNING" in line[:9]:
                log("  " + line[:300])
            tail = (tail + [line])[-40:]
            if time.time() > deadline:
                p.terminate()
                log("  durée max atteinte, arrêt")
                break
        rc = p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = -9
    new = archive_count() - before
    text = "\n".join(tail)
    if re.search(r"Sign in to confirm|HTTP Error 429|n challenge solving failed|Requested format is not available", text) and new == 0:
        fatal = next((l for l in reversed(tail) if "ERROR" in l), "YouTube a refusé les téléchargements")[:300]
    # 101 = --max-downloads atteint (il reste des vidéos) ; 0/1 = liste parcourue jusqu'au bout
    finished = rc in (0, 1) and new < max_dl and time.time() < deadline
    return new, finished, fatal


def slim_info(path):
    """Les .info.json de yt-dlp pèsent des centaines de Ko : on ne garde que l'utile."""
    j = load_json(path, None)
    if not j or j.get("_vt"):
        return j
    keep = {k: j.get(k) for k in ("id", "title", "upload_date", "duration", "description", "channel_id",
                                  "channel", "uploader_id", "width", "height", "view_count")}
    keep["chapters"] = [{"t": c.get("start_time"), "title": c.get("title")} for c in j.get("chapters") or []]
    keep["_vt"] = 1
    save_json(path, keep)
    return keep


def build_library(chs, urls):
    channels, videos = [], []
    for url in urls:
        c = chs.get(url)
        if not c or not c.get("id"):
            continue
        d = f"{MEDIA}/{c['id']}"
        n = 0
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".info.json"):
                continue
            stem = name[:-len(".info.json")]
            if not os.path.exists(f"{d}/{stem}.mp4"):
                continue
            j = slim_info(f"{d}/{name}")
            if not j:
                continue
            videos.append({
                "id": j["id"], "c": c["id"], "t": j.get("title") or "", "d": j.get("upload_date") or "",
                "l": int(j.get("duration") or 0), "s": (j.get("description") or "")[:DESC_MAX],
                "th": f"media/{c['id']}/{stem}.jpg" if os.path.exists(f"{d}/{stem}.jpg") else "",
                "f": f"media/{c['id']}/{stem}.mp4",
                "ch": [x for x in j.get("chapters") or [] if x.get("t") is not None],
            })
            n += 1
        channels.append({
            "id": c["id"], "name": c.get("name", c["id"]), "handle": c.get("handle", ""), "desc": c.get("desc", ""),
            "avatar": f"media/{c['id']}/avatar.jpg" if os.path.exists(f"{d}/avatar.jpg") else "",
            "banner": f"media/{c['id']}/banner.jpg" if os.path.exists(f"{d}/banner.jpg") else "",
            "n": n, "complete": c.get("complete", False),
        })
    videos.sort(key=lambda v: (v["d"], v["id"]), reverse=True)
    save_json(LIBRARY, {"generated": dt.datetime.now().isoformat(timespec="seconds"),
                        "channels": channels, "videos": videos}, compact=True)
    return channels, videos


def main():
    os.makedirs(MEDIA, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", f"{BASE}/.cache")
    lock = open(f"{BASE}/.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("déjà en cours, rien à faire")
        return 0

    cfg = settings()
    if "--max" in sys.argv:
        cfg["max"] = int(sys.argv[sys.argv.index("--max") + 1])
    urls = read_channels()
    state = load_json(STATE, {"channels": {}})
    chs = state.setdefault("channels", {})
    status = {"start": dt.datetime.now().isoformat(timespec="seconds"), "ok": True, "new": 0, "errors": []}

    if "--index-only" not in sys.argv:
        deadline = time.time() + cfg["hours"] * 3600
        budget = cfg["max"]
        log(f"début : {len(urls)} chaîne(s), budget {budget} vidéos, {cfg['hours']} h max, {cfg['height']}p")
        for url in urls:
            chs[url] = channel_info(url, chs) or chs.get(url, {})
        save_json(STATE, state)

        # 1) nouveautés
        for url in urls:
            if budget <= 0:
                break
            log(f"nouveautés : {chs[url].get('name', url)}")
            new, _, fatal = download(url, min(budget, NEW_PER_CHANNEL), deadline, True, cfg["height"])
            budget -= new
            status["new"] += new
            if fatal:
                status["errors"].append(f"{chs[url].get('name', url)}: {fatal}")

        # 2) rattrapage : chaînes incomplètes, ou toutes le dimanche
        sunday = dt.date.today().weekday() == 6
        todo = [u for u in urls if sunday or not chs[u].get("complete")]
        for i, url in enumerate(todo):
            if budget <= 0 or time.time() >= deadline:
                break
            share = max(1, budget // (len(todo) - i))
            log(f"rattrapage : {chs[url].get('name', url)} (jusqu'à {share})")
            new, finished, fatal = download(url, share, deadline, False, cfg["height"])
            budget -= new
            status["new"] += new
            if finished and not fatal:
                if not chs[url].get("complete"):
                    log(f"  chaîne complète : {chs[url].get('name', url)}")
                chs[url]["complete"] = True
            if fatal:
                status["errors"].append(f"{chs[url].get('name', url)}: {fatal}")
        save_json(STATE, state)

    channels, videos = build_library(chs, urls)
    status.update({"end": dt.datetime.now().isoformat(timespec="seconds"),
                   "ok": not status["errors"], "total": len(videos),
                   "channels": {c["name"]: {"videos": c["n"], "complete": c["complete"]} for c in channels}})
    save_json(STATUS, status)
    log(f"fin : {status['new']} nouvelle(s) vidéo(s), {len(videos)} au total, "
        f"{'OK' if status['ok'] else 'ERREURS: ' + ' | '.join(status['errors'])}")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
