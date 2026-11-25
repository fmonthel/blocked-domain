#!/usr/bin/env python3
"""Vidéothèque : copie locale des chaînes YouTube listées dans chaines.conf.

Lancé chaque nuit par /etc/cron.d/videotheque (utilisateur « videotheque »).

Deux modes par chaîne (options sur la ligne de chaines.conf) :
- complet (défaut) : toute la chaîne est copiée.
    1. passe « nouveautés » : vidéos plus récentes que la dernière copiée (--break-on-existing) ;
    2. passe « rattrapage » : tant que la chaîne n'est pas complète, ou le dimanche, on remonte
       l'historique avec le budget restant de la nuit.
- demand : seul le catalogue (titres, durées, miniatures copiées en local) est récupéré chaque nuit,
    avec les `keep` vidéos les plus récentes. Les autres sont téléchargées au premier clic par
    vt-demande.py, puis effacées après `expire` jours sans visionnage (journal nginx).

Ensuite : reconstruit library.json (lu par le site) et status.json (lu par la supervision).
720p max, H.264 + AAC en mp4 : simple remux, aucun réencodage (le Celeron ne suivrait pas).
Pause de 15 à 45 s entre deux vidéos et budget par nuit pour ne pas se faire bloquer par YouTube.

Réglages locaux facultatifs : /etc/videotheque/local.conf
    [vt]
    max_par_nuit = 150
    duree_max_heures = 5
    hauteur_max = 720

Usage : vt-sync.py [--index-only] [--max N] [--conf FICHIER]
"""
import configparser, datetime as dt, fcntl, glob, json, os, re, subprocess, sys, time, urllib.request

BASE = "/srv/videotheque"
MEDIA = f"{BASE}/media"
ARCHIVE = f"{BASE}/archive.txt"
STATE = f"{BASE}/state.json"
LIBRARY = f"{BASE}/library.json"
STATUS = f"{BASE}/status.json"
CHANNELS_CONF = "/opt/videotheque/chaines.conf"
LOCAL_CONF = "/etc/videotheque/local.conf"
NGINX_LOGS = ["/var/log/nginx/videotheque.access.log", "/var/log/nginx/videotheque.access.log.1"]
YTDLP = "/opt/videotheque/venv/bin/yt-dlp"
QJS = "/usr/local/bin/qjs"
NEW_PER_CHANNEL = 30           # passe nouveautés : au plus N vidéos par chaîne et par nuit
DESC_MAX = 1500                # description gardée dans library.json
THUMBS_PER_NIGHT = 600         # miniatures du catalogue (mode demand) copiées par nuit au plus


def log(msg):
    print(f"{time.strftime('%F %T')} {msg}", flush=True)


def settings():
    c = configparser.ConfigParser()
    c.read(LOCAL_CONF)
    s = c["vt"] if c.has_section("vt") else {}
    get = lambda k, d: int(s.get(k, d)) if s else d
    return {"max": get("max_par_nuit", 150), "hours": get("duree_max_heures", 5), "height": get("hauteur_max", 720)}


def read_channels(path):
    """Une chaîne par ligne : URL [mode=demand] [keep=10] [expire=30]."""
    chans = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if not parts:
                continue
            url = parts[0].rstrip("/").removesuffix("/videos").removesuffix("/featured")
            opt = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            mode = "demand" if opt.get("mode", "").lower() in ("demand", "demande") else "full"
            chans.append({"url": url, "mode": mode,
                          "keep": int(opt.get("keep", 10)), "expire": int(opt.get("expire", 30))})
    return chans


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
    info = dict(known)
    info.update({"id": cid, "name": j.get("channel") or j.get("title") or cid,
                 "handle": j.get("uploader_id") or "", "desc": (j.get("description") or "")[:DESC_MAX],
                 "meta_ts": time.time()})
    info.setdefault("complete", False)
    save_json(f"{d}/channel.json", info)
    return info


def download(url, max_dl, deadline, break_on_existing, height, items=None):
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
    if items:
        cmd += ["--playlist-items", items]
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


# ---------------------------------------------------------------- mode « demand »

def update_catalog(url, cid, deadline):
    """Catalogue complet de la chaîne (sans télécharger les vidéos) + miniatures copiées en local
    (ytimg.com est bloqué pour les enfants). Retourne le nombre de vidéos du catalogue."""
    r = subprocess.run(ytdlp_base() + ["--flat-playlist", "-J", url + "/videos"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or "catalogue illisible")
    entries = [e for e in json.loads(r.stdout).get("entries") or [] if e.get("id")]
    cat = [{"id": e["id"], "t": e.get("title") or "", "l": int(e.get("duration") or 0)} for e in entries]
    d = f"{MEDIA}/{cid}"
    os.makedirs(f"{d}/thumbs", exist_ok=True)
    save_json(f"{d}/catalog.json", cat)
    got = 0
    for e in cat:
        dest = f"{d}/thumbs/{e['id']}.jpg"
        if os.path.exists(dest) or got >= THUMBS_PER_NIGHT or time.time() > deadline:
            continue
        try:
            fetch(f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg", dest)
            got += 1
            time.sleep(0.2)
        except Exception:
            pass
    log(f"  catalogue : {len(cat)} vidéos, {got} miniature(s) copiée(s)")
    return len(cat)


def views_from_nginx(state):
    """Dernier visionnage de chaque vidéo (requêtes .mp4 dans le journal nginx)."""
    seen = state.setdefault("seen", {})
    rx = re.compile(r'\[([^\]]+)\] "GET /media/[^/]+/\d*_([A-Za-z0-9_-]{11})\.mp4')
    for path in NGINX_LOGS:
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    m = rx.search(line)
                    if not m:
                        continue
                    try:
                        ts = dt.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S %z").timestamp()
                    except ValueError:
                        continue
                    if ts > seen.get(m.group(2), 0):
                        seen[m.group(2)] = ts
        except OSError:
            pass  # journal absent ou illisible (l'utilisateur videotheque doit être dans le groupe adm)
    return seen


def expire_demand(cid, keep_ids, days, seen):
    """Efface les vidéos d'une chaîne « demand » non regardées depuis `days` jours
    (sauf les `keep` plus récentes). Retourne le nombre de vidéos effacées."""
    limit = time.time() - days * 86400
    removed = []
    for mp4 in glob.glob(f"{MEDIA}/{cid}/*_*.mp4"):
        vid = os.path.basename(mp4)[:-4].split("_", 1)[1]
        if vid in keep_ids:
            continue
        last = max(seen.get(vid, 0), os.path.getmtime(mp4))
        if last < limit:
            stem = mp4[:-4]
            for ext in (".mp4", ".info.json", ".jpg"):
                try:
                    os.remove(stem + ext)
                except OSError:
                    pass
            removed.append(vid)
    if removed:
        try:
            with open(ARCHIVE) as f:
                lines = [l for l in f if l.split()[-1:] and l.split()[-1] not in removed]
            with open(ARCHIVE + ".tmp", "w") as f:
                f.writelines(lines)
            os.replace(ARCHIVE + ".tmp", ARCHIVE)
        except OSError:
            pass
    return len(removed)


# ---------------------------------------------------------------- catalogue du site

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


def downloaded(cid):
    """Vidéos copiées d'une chaîne : {id: entrée du catalogue du site}."""
    d = f"{MEDIA}/{cid}"
    out = {}
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".info.json"):
            continue
        stem = name[:-len(".info.json")]
        if not os.path.exists(f"{d}/{stem}.mp4"):
            continue
        j = slim_info(f"{d}/{name}")
        if not j:
            continue
        out[j["id"]] = {
            "id": j["id"], "c": cid, "t": j.get("title") or "", "d": j.get("upload_date") or "",
            "l": int(j.get("duration") or 0), "s": (j.get("description") or "")[:DESC_MAX],
            "th": f"media/{cid}/{stem}.jpg" if os.path.exists(f"{d}/{stem}.jpg") else "",
            "f": f"media/{cid}/{stem}.mp4",
            "ch": [x for x in j.get("chapters") or [] if x.get("t") is not None],
        }
    return out


def build_library(chs, channels):
    """Vidéos rangées chaîne par chaîne : par date (mode complet) ou dans l'ordre du catalogue
    YouTube, du plus récent au plus ancien (mode demand)."""
    out_ch, videos = [], []
    for c in channels:
        info = chs.get(c["url"])
        if not info or not info.get("id"):
            continue
        cid = info["id"]
        d = f"{MEDIA}/{cid}"
        have = downloaded(cid)
        if c["mode"] == "demand":
            vids = []
            for e in load_json(f"{d}/catalog.json", []):
                v = have.pop(e["id"], None) or {
                    "id": e["id"], "c": cid, "t": e["t"], "d": "", "l": e["l"], "s": "", "f": "", "ch": [],
                    "th": f"media/{cid}/thumbs/{e['id']}.jpg" if os.path.exists(f"{d}/thumbs/{e['id']}.jpg") else ""}
                v["dm"] = 1
                vids.append(v)
            vids += [dict(v, dm=1) for v in have.values()]   # copiées mais plus au catalogue
        else:
            vids = sorted(have.values(), key=lambda v: (v["d"], v["id"]), reverse=True)
        videos += vids
        out_ch.append({
            "id": cid, "name": info.get("name", cid), "handle": info.get("handle", ""), "desc": info.get("desc", ""),
            "avatar": f"media/{cid}/avatar.jpg" if os.path.exists(f"{d}/avatar.jpg") else "",
            "banner": f"media/{cid}/banner.jpg" if os.path.exists(f"{d}/banner.jpg") else "",
            "n": len(vids), "local": sum(1 for v in vids if v["f"]),
            "mode": c["mode"], "complete": info.get("complete", False) or c["mode"] == "demand",
        })
    save_json(LIBRARY, {"generated": dt.datetime.now().isoformat(timespec="seconds"),
                        "channels": out_ch, "videos": videos}, compact=True)
    return out_ch, videos


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
    conf = sys.argv[sys.argv.index("--conf") + 1] if "--conf" in sys.argv else CHANNELS_CONF
    channels = read_channels(conf)
    full = [c for c in channels if c["mode"] == "full"]
    demand = [c for c in channels if c["mode"] == "demand"]
    state = load_json(STATE, {"channels": {}})
    chs = state.setdefault("channels", {})
    status = {"start": dt.datetime.now().isoformat(timespec="seconds"), "ok": True, "new": 0,
              "expired": 0, "errors": []}
    name = lambda c: chs.get(c["url"], {}).get("name", c["url"])

    if "--index-only" not in sys.argv:
        deadline = time.time() + cfg["hours"] * 3600
        budget = cfg["max"]
        log(f"début : {len(full)} chaîne(s) complète(s), {len(demand)} à la demande, budget {budget} vidéos, "
            f"{cfg['hours']} h max, {cfg['height']}p")
        for c in channels:
            chs[c["url"]] = channel_info(c["url"], chs) or chs.get(c["url"], {})
        save_json(STATE, state)

        # mode demand : catalogue + `keep` plus récentes + ménage
        seen = views_from_nginx(state) if demand else {}
        for c in demand:
            cid = chs[c["url"]].get("id")
            if not cid:
                continue
            log(f"à la demande : {name(c)} (garder {c['keep']}, effacer après {c['expire']} j)")
            try:
                update_catalog(c["url"], cid, deadline)
            except Exception as e:
                status["errors"].append(f"{name(c)}: catalogue: {e}"[:300])
                continue
            new, _, fatal = download(c["url"], min(budget, c["keep"]), deadline, False, cfg["height"],
                                     items=f"1:{c['keep']}")
            budget -= new
            status["new"] += new
            if fatal:
                status["errors"].append(f"{name(c)}: {fatal}")
            keep_ids = {e["id"] for e in load_json(f"{MEDIA}/{cid}/catalog.json", [])[:c["keep"]]}
            n = expire_demand(cid, keep_ids, c["expire"], seen)
            status["expired"] += n
            if n:
                log(f"  {n} vidéo(s) non regardée(s) depuis {c['expire']} j effacée(s)")

        # mode complet 1) nouveautés
        for c in full:
            if budget <= 0:
                break
            log(f"nouveautés : {name(c)}")
            new, _, fatal = download(c["url"], min(budget, NEW_PER_CHANNEL), deadline, True, cfg["height"])
            budget -= new
            status["new"] += new
            if fatal:
                status["errors"].append(f"{name(c)}: {fatal}")

        # mode complet 2) rattrapage : chaînes incomplètes, ou toutes le dimanche
        sunday = dt.date.today().weekday() == 6
        todo = [c for c in full if sunday or not chs[c["url"]].get("complete")]
        for i, c in enumerate(todo):
            if budget <= 0 or time.time() >= deadline:
                break
            share = max(1, budget // (len(todo) - i))
            log(f"rattrapage : {name(c)} (jusqu'à {share})")
            new, finished, fatal = download(c["url"], share, deadline, False, cfg["height"])
            budget -= new
            status["new"] += new
            if finished and not fatal:
                if not chs[c["url"]].get("complete"):
                    log(f"  chaîne complète : {name(c)}")
                chs[c["url"]]["complete"] = True
            if fatal:
                status["errors"].append(f"{name(c)}: {fatal}")
        save_json(STATE, state)

    out_ch, videos = build_library(chs, channels)
    status.update({"end": dt.datetime.now().isoformat(timespec="seconds"),
                   "ok": not status["errors"], "total": len(videos),
                   "channels": {c["name"]: {"videos": c["n"], "local": c["local"], "mode": c["mode"],
                                            "complete": c["complete"]} for c in out_ch}})
    save_json(STATUS, status)
    log(f"fin : {status['new']} nouvelle(s) vidéo(s), {status['expired']} effacée(s), {len(videos)} au catalogue, "
        f"{'OK' if status['ok'] else 'ERREURS: ' + ' | '.join(status['errors'])}")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
