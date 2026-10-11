#!/usr/bin/env python3
"""Daily Squid report: per kid, sites allowed / blocked in web browsers, then in applications,
sent as an HTML email (French).

Install: /root/squid/daily-squid-report.py (chmod 700), config /root/squid/report.conf,
SMTP app password in the file named by password_file (chmod 600).
Cron:    0 21 * * * /usr/bin/python3 -B /root/squid/daily-squid-report.py >> /var/log/squid-report.log 2>&1

Usage:   daily-squid-report.py [--date YYYY-MM-DD] [--preview out.html]
"""
import argparse
import configparser
import datetime as dt
import gzip
import html
import os
import re
import smtplib
import sys
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

CONF = "/root/squid/report.conf"
LOGS = ["/var/log/squid/access.log.2.gz", "/var/log/squid/access.log.1", "/var/log/squid/access.log"]
# Same requests with the User-Agent (logformat kids_ua in squid.conf), used to tell browsers from apps
UA_LOGS = ["/var/log/squid/ua.log.2.gz", "/var/log/squid/ua.log.1", "/var/log/squid/ua.log"]
PASSWD = "/etc/squid/passwords"
SQUID_CONF = "/etc/squid/squid.conf"

# Ads, trackers and background services: counted but hidden from the allowed-site lists
NOISE = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "google-analytics.com",
    "googletagmanager.com", "googletagservices.com", "adtrafficquality.google", "rubiconproject.com",
    "smartadserver.com", "pubmatic.com", "criteo.com", "criteo.net", "openx.net", "adnxs.com",
    "amazon-adsystem.com", "casalemedia.com", "33across.com", "sharethrough.com", "lijit.com",
    "media.net", "id5-sync.com", "360yield.com", "adform.net", "gumgum.com", "dotomi.com",
    "omnitagjs.com", "unrulymedia.com", "richaudience.com", "onetag-sys.com", "a-mo.net",
    "yellowblue.io", "admanmedia.com", "1rx.io", "cootlogix.com", "loopme.me", "seedtag.com",
    "presage.io", "doubleverify.com", "outbrain.com", "taboola.com", "smilewanted.com",
    "yieldmo.com", "creativecdn.com", "bidbrain.app", "ingage.tech", "omni-dex.io", "ay.delivery",
    "4dex.io", "connectad.io", "bidr.io", "setupad.io", "programmaticx.ai", "scorecardresearch.com",
    "quantserve.com", "moatads.com", "adsrvr.org", "rlcdn.com", "demdex.net", "everesttech.net",
    "teads.tv", "teads.com", "sentry.io", "amplitude.com", "hotjar.com", "clarity.ms",
    "gstatic.com", "googleapis.com", "gvt1.com", "gvt2.com", "ggpht.com", "msftconnecttest.com",
    "windowsupdate.com", "digicert.com", "pki.goog", "lencr.org", "amx1.net", "sonobi.com",
    "eskimi.com", "zemanta.com", "tynt.com", "chocolateplatform.com", "stackadapt.com", "3lift.com",
    "contextweb.com", "ipredictive.com", "nextmillmedia.com", "sparteo.com", "in.net", "jsdelivr.net",
    "indexww.com", "adsafeprotected.com",
}
# Ad-tech / CDN name patterns (matched on the registrable domain)
NOISE_RX = re.compile(r"^ads?[a-z0-9-]*\.|bid|sync|track|pixel|analytic|metric|monetiz|measure|cdn|"
                      r"delivery|programmatic|telemetry|beacon|prebid", re.I)
SERVER = os.uname().nodename.split(".")[0]
SECOND_LEVEL = {"co", "com", "net", "org", "gov", "gouv", "edu", "ac", "qc", "on"}

# Report sections, in display order
GROUPS = [("browser", "Navigateurs internet", "Chrome, Edge, Firefox, Opera, Safari"),
          ("app", "Applications", "logiciels et applis qui se connectent sans navigateur"),
          ("unknown", "Source non déterminée", "trafic enregistré avant l'activation du suivi des navigateurs")]


def regdomain(host):
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_of(url):
    h = re.sub(r"^[a-z]+://", "", url)
    return re.split(r"[:/]", h, 1)[0].lower()


def load_regex():
    try:
        for line in open(SQUID_CONF):
            m = re.match(r"acl\s+game_words\s+dstdom_regex\s+-i\s+(\S+)", line)
            if m:
                return re.compile(m.group(1), re.I)
    except OSError:
        pass
    return None


def read_lines(paths):
    for path in paths:
        if not os.path.exists(path):
            continue
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as f:
            yield from f


NO_UA = object()  # record logged before ua.log existed: source unknown
UA_LINE = re.compile(r'^(\S+) (\S+) (\S+) (\S+) (\S+) (\S+) "(.*)"\s*$')


def records():
    """Yield (ts, ip, status, url, user, ua). Uses ua.log (with User-Agent) where it exists,
    and the classic access.log for the period before ua.log was enabled."""
    ua_start = None
    for line in read_lines(UA_LOGS):
        m = UA_LINE.match(line)
        if not m:
            continue
        ts = float(m.group(1))
        ua_start = ts if ua_start is None else min(ua_start, ts)
        yield ts, m.group(2), m.group(4), m.group(6), m.group(3), m.group(7)
    for line in read_lines(LOGS):
        f = line.split()
        if len(f) < 8:
            continue
        try:
            ts = float(f[0])
        except ValueError:
            continue
        if ua_start is None or ts < ua_start:
            yield ts, f[2], f[3], f[6], f[7], NO_UA
    records.ua_start = ua_start


def browser_of(ua):
    if not ua or ua == "-" or not ua.startswith("Mozilla/5.0"):
        return None
    if re.search(r"Electron/|Teams/|Discord|Spotify|Slack|WhatsApp|bot\b|crawler", ua, re.I):
        return None
    for pat, name in ((r"Edg(e|A|iOS)?/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/|FxiOS/", "Firefox"),
                      (r"Chrome/|CriOS/", "Chrome"), (r"Version/[\d.]+ .*Safari/", "Safari")):
        if re.search(pat, ua):
            return name
    return None


def app_of(ua):
    """Short application name for non-browser traffic."""
    if not ua or ua == "-":
        return "sans nom"
    m = re.search(r"\b(Discord|Teams|Spotify|Slack|WhatsApp|Steam|Roblox|Minecraft|Zoom|OneDrive|Outlook)\b", ua, re.I)
    if m:
        return m.group(1)
    if "Electron/" in ua:
        return "appli Electron"
    first = re.split(r"[/\s;(]", ua.strip(), 1)[0]
    return first[:30] or "sans nom"


def new_group():
    return {"sites": defaultdict(lambda: [0, None, None, set()]),
            "blocked": defaultdict(lambda: [0, None, "", set()]), "noise": 0}


def collect(day, kids):
    start = dt.datetime.combine(day, dt.time.min).timestamp()
    end = start + 86400
    known = set()
    try:
        known = {l.split(":", 1)[0] for l in open(PASSWD) if ":" in l}
    except OSError:
        pass
    game_rx = load_regex()
    data = {u: {"groups": {g: new_group() for g, _, _ in GROUPS}, "browsers": defaultdict(int),
                "requests": 0, "ips": set(), "first": None, "last": None} for u in kids}
    unknown = defaultdict(lambda: [0, set()])
    for ts, ip, status, url, user, ua in records():
        if not start <= ts < end:
            continue
        if user != "-" and user not in kids:
            if user not in known:
                unknown[user][0] += 1
                unknown[user][1].add(ip)
            continue
        if user not in kids:
            continue
        d = data[user]
        t = dt.datetime.fromtimestamp(ts)
        host = host_of(url)
        dom = regdomain(host)
        d["requests"] += 1
        d["ips"].add(ip)
        d["first"] = d["first"] or t
        d["last"] = t
        if ua is NO_UA:
            gname, label = "unknown", ""
        else:
            browser = browser_of(ua)
            gname, label = ("browser", browser) if browser else ("app", app_of(ua))
            if browser and "DENIED/407" not in status:
                d["browsers"][browser] += 1
        g = d["groups"][gname]
        if "DENIED/403" in status:
            b = g["blocked"][re.sub(r"^www\.", "", host)]
            b[0] += 1
            b[1] = b[1] or t
            b[2] = "mot-clé jeu/game" if game_rx and game_rx.search(host) else "liste"
            if label:
                b[3].add(label)
        elif "DENIED" in status:
            continue
        elif dom in NOISE or NOISE_RX.search(dom):
            g["noise"] += 1
        else:
            s = g["sites"][dom]
            s[0] += 1
            s[1] = s[1] or t
            s[2] = t
            if label:
                s[3].add(label)
    return data, unknown


def hm(t):
    return t.strftime("%H:%M") if t else "-"


MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def fr_date(day):
    return f"{DAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def labels(src, limit=3):
    items = sorted(src)
    txt = ", ".join(items[:limit])
    return txt + (f" +{len(items) - limit}" if len(items) > limit else "")


CSS_TD = "padding:6px 10px;border-bottom:1px solid #eef0f3;font-size:14px;"
CSS_TH = ("padding:6px 10px;text-align:left;font-size:12px;color:#6b7280;text-transform:uppercase;"
          "letter-spacing:.04em;border-bottom:2px solid #e5e7eb;")


def render_group(gid, title, subtitle, g, max_sites):
    e = html.escape
    sites = sorted(g["sites"].items(), key=lambda kv: -kv[1][0])
    blocked = sorted(g["blocked"].items(), key=lambda kv: -kv[1][0])
    src_col = "Navigateur" if gid == "browser" else "Application"
    show_src = gid != "unknown"
    out = [f'<div style="margin:18px 0 0;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;">'
           f'<div style="font-size:15px;font-weight:600;">{e(title)}</div>'
           f'<div style="font-size:12px;color:#9ca3af;margin-bottom:6px;">{e(subtitle)}</div>']
    # allowed
    out.append(f'<div style="font-size:13px;font-weight:600;color:#047857;margin:10px 0 4px;">&#10003; Sites autorisés ({len(sites)})</div>')
    if sites:
        out.append(f'<table style="width:100%;border-collapse:collapse;"><tr><th style="{CSS_TH}">Site</th>'
                   + (f'<th style="{CSS_TH}">{src_col}</th>' if show_src else "")
                   + f'<th style="{CSS_TH}text-align:right;">Nombre d’accès</th><th style="{CSS_TH}text-align:right;">Heures</th></tr>')
        for dom, (n, first, last, src) in sites[:max_sites]:
            out.append(f'<tr><td style="{CSS_TD}">{e(dom)}</td>'
                       + (f'<td style="{CSS_TD}color:#6b7280;">{e(labels(src))}</td>' if show_src else "")
                       + f'<td style="{CSS_TD}text-align:right;color:#6b7280;">{n}</td>'
                       f'<td style="{CSS_TD}text-align:right;color:#6b7280;white-space:nowrap;">{hm(first)}&ndash;{hm(last)}</td></tr>')
        out.append("</table>")
        if len(sites) > max_sites:
            out.append(f'<div style="font-size:12px;color:#9ca3af;margin-top:6px;">+ {len(sites) - max_sites} autres sites moins visités</div>')
    else:
        out.append('<div style="font-size:13px;color:#9ca3af;">Aucun.</div>')
    if g["noise"]:
        out.append(f'<div style="font-size:12px;color:#9ca3af;margin-top:4px;">{g["noise"]} requêtes publicitaires / techniques masquées</div>')
    # blocked
    nblocked = sum(v[0] for _, v in blocked)
    out.append(f'<div style="font-size:13px;font-weight:600;color:#b91c1c;margin:14px 0 4px;">&#10007; Sites bloqués ({len(blocked)} sites, {nblocked} tentatives)</div>')
    if blocked:
        out.append(f'<table style="width:100%;border-collapse:collapse;"><tr><th style="{CSS_TH}">Site</th>'
                   f'<th style="{CSS_TH}">Raison</th>'
                   + (f'<th style="{CSS_TH}">{src_col}</th>' if show_src else "")
                   + f'<th style="{CSS_TH}text-align:right;">Tentatives</th><th style="{CSS_TH}text-align:right;">1re fois</th></tr>')
        for dom, (n, first, why, src) in blocked:
            badge = "#fef3c7;color:#92400e" if why.startswith("mot") else "#fee2e2;color:#991b1b"
            out.append(f'<tr><td style="{CSS_TD}">{e(dom)}</td><td style="{CSS_TD}"><span style="background:{badge};'
                       f'padding:2px 8px;border-radius:999px;font-size:12px;white-space:nowrap;">{e(why)}</span></td>'
                       + (f'<td style="{CSS_TD}color:#6b7280;">{e(labels(src))}</td>' if show_src else "")
                       + f'<td style="{CSS_TD}text-align:right;color:#6b7280;">{n}</td>'
                       f'<td style="{CSS_TD}text-align:right;color:#6b7280;">{hm(first)}</td></tr>')
        out.append("</table>")
    else:
        out.append('<div style="font-size:13px;color:#9ca3af;">Aucun.</div>')
    out.append("</div>")
    return "".join(out)


def group_used(g):
    return g["sites"] or g["blocked"] or g["noise"]


def render_html(day, kids, data, unknown, max_sites):
    e = html.escape
    out = [f"""<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#111827;">
<div style="max-width:720px;margin:0 auto;padding:24px 16px;">
<div style="background:#1f2937;color:#fff;border-radius:12px 12px 0 0;padding:20px 24px;">
<div style="font-size:13px;opacity:.75;">Rapport du proxy familial &middot; serveur {e(SERVER)}</div>
<div style="font-size:22px;font-weight:600;margin-top:4px;">{e(fr_date(day).capitalize())}</div></div>
<div style="background:#fff;border-radius:0 0 12px 12px;padding:8px 24px 24px;">"""]
    for user, name in kids.items():
        d = data[user]
        groups = d["groups"]
        nblocked = sum(v[0] for g in groups.values() for v in g["blocked"].values())
        nsites = len(groups["browser"]["sites"]) + len(groups["unknown"]["sites"])
        stat = lambda v, l, c="#111827": (f'<td style="padding:10px 12px;background:#f9fafb;border-radius:8px;text-align:center;">'
                                         f'<div style="font-size:20px;font-weight:600;color:{c};">{v}</div>'
                                         f'<div style="font-size:12px;color:#6b7280;">{l}</div></td>')
        out.append(f'<h2 style="font-size:18px;margin:28px 0 10px;">{e(name)} <span style="font-weight:400;color:#9ca3af;font-size:14px;">({e(user)})</span></h2>')
        if not d["requests"]:
            out.append('<p style="color:#6b7280;font-size:14px;margin:0;">Aucune activité via ce proxy.</p>')
            continue
        out.append('<table role="presentation" cellspacing="6" style="width:100%;border-collapse:separate;"><tr>'
                   + stat(nsites, "sites (navigateur)")
                   + stat(nblocked, "tentatives bloquées", "#b91c1c" if nblocked else "#111827")
                   + stat(f"{hm(d['first'])}&ndash;{hm(d['last'])}", "activité")
                   + stat(len(d["ips"]), "appareil(s)") + "</tr></table>")
        out.append(f'<div style="font-size:12px;color:#9ca3af;margin:2px 0 0 6px;">Appareils : {e(", ".join(sorted(d["ips"])))}</div>')
        if d["browsers"]:
            nav = ", ".join(f"{b} ({n})" for b, n in sorted(d["browsers"].items(), key=lambda kv: -kv[1]))
            out.append(f'<div style="font-size:12px;color:#9ca3af;margin:2px 0 0 6px;">Navigateurs : {e(nav)}</div>')
        for gid, title, subtitle in GROUPS:
            g = groups[gid]
            if gid == "unknown" and not group_used(g):
                continue
            out.append(render_group(gid, title, subtitle, g, max_sites))
    if unknown:
        out.append('<h3 style="font-size:14px;margin:24px 0 6px;color:#92400e;">Identifiants inconnus utilisés</h3>'
                   '<p style="font-size:13px;color:#6b7280;margin:0 0 6px;">Connexions refusées avec un nom de compte qui n\'existe pas (faute de frappe ou essai de deviner un compte).</p><ul style="margin:0;padding-left:20px;font-size:14px;">')
        for u, (n, ips) in sorted(unknown.items(), key=lambda kv: -kv[1][0]):
            out.append(f"<li><b>{e(u)}</b> : {n} tentative(s) depuis {e(', '.join(sorted(ips)))}</li>")
        out.append("</ul>")
    until = f" jusqu'à {dt.datetime.now():%H:%M}" if day == dt.date.today() else ""
    out.append(f'<p style="font-size:12px;color:#9ca3af;margin-top:28px;border-top:1px solid #eef0f3;padding-top:12px;">'
               f'Proxy {e(os.uname().nodename)} &middot; journée du {day.isoformat()}{until} &middot; '
               f'sites autorisés regroupés par domaine, publicités et services techniques masqués.</p></div></div></body></html>')
    return "".join(out)


def render_text(day, kids, data, unknown):
    out = [f"Rapport du proxy familial ({SERVER}) - {fr_date(day)}", ""]
    for user, name in kids.items():
        d = data[user]
        out.append(f"== {name} ({user}) ==")
        if not d["requests"]:
            out += ["Aucune activité.", ""]
            continue
        out.append(f"Activité {hm(d['first'])}-{hm(d['last'])}, appareils : {', '.join(sorted(d['ips']))}")
        for gid, title, _ in GROUPS:
            g = d["groups"][gid]
            if gid == "unknown" and not group_used(g):
                continue
            out.append(f"-- {title} --")
            out.append("  Sites autorisés (nombre d’accès) :")
            for dom, (n, first, last, src) in sorted(g["sites"].items(), key=lambda kv: -kv[1][0]):
                out.append(f"    {dom:40} {n:5}  {hm(first)}-{hm(last)}  {labels(src)}")
            out.append("  Sites bloqués (tentatives) :")
            for dom, (n, first, why, src) in sorted(g["blocked"].items(), key=lambda kv: -kv[1][0]):
                out.append(f"    {dom:40} {n:5}  {why}  {labels(src)}")
        out.append("")
    for u, (n, ips) in unknown.items():
        out.append(f"Identifiant inconnu : {u} ({n} tentatives depuis {', '.join(sorted(ips))})")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--preview", help="write the HTML to this file instead of sending")
    args = ap.parse_args()

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    if not cfg.read(CONF):
        sys.exit(f"missing {CONF}")
    kids = dict(cfg["kids"])
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    data, unknown = collect(day, kids)
    if not any(data[u]["requests"] for u in kids) and not unknown:
        print(f"{dt.datetime.now():%F %T} no traffic on {day}, no report")
        return
    max_sites = cfg.getint("report", "max_sites", fallback=40)
    body_html = render_html(day, kids, data, unknown, max_sites)

    if args.preview:
        with open(args.preview, "w", encoding="utf-8") as f:
            f.write(body_html)
        print(f"preview written to {args.preview}")
        return

    s = cfg["smtp"]
    password = "".join(open(s["password_file"]).read().split())  # Google shows app passwords with spaces
    total_blocked = sum(v[0] for u in kids for g in data[u]["groups"].values() for v in g["blocked"].values())
    msg = EmailMessage()
    msg["Subject"] = f"Proxy familial ({SERVER}) - {fr_date(day)}" + (f" - {total_blocked} blocages" if total_blocked else "")
    msg["From"] = s["from"]
    msg["To"] = cfg["report"]["to"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=s["user"].split("@")[-1])
    msg.set_content(render_text(day, kids, data, unknown))
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP(s["host"], s.getint("port"), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(s["user"], password)
        smtp.send_message(msg)
    print(f"{dt.datetime.now():%F %T} report for {day} sent to {msg['To']}")


if __name__ == "__main__":
    main()
