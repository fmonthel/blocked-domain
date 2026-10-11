#!/bin/bash
# Update Squid blocklist from GitHub, reload Squid, roll back on failure.
# Install: /root/squid/update-squid-blocklist.sh (chmod 755, owner root), run hourly from root's crontab
set -u

# GitHub contents API with raw output: always serves the latest commit
# (raw.githubusercontent.com is cached up to 5 min after a push)
URL="https://api.github.com/repos/fmonthel/blocked-domain/contents/blocked_domains.txt?ref=main"
DEST="/etc/squid/blocked_domains.txt"
BACKUP="${DEST}.bak"
LOCK="/run/update-squid-blocklist.lock"
TAG="squid-blocklist"
TMP="$(mktemp)"

log() { logger -t "$TAG" "$*"; echo "$(date '+%F %T') $*"; }
trap 'rm -f "$TMP"' EXIT

# Prevent overlapping runs
exec 9>"$LOCK"
flock -n 9 || { log "Another run in progress, exiting"; exit 0; }

# 1) Download new list
if ! curl -fsSL --max-time 60 --retry 2 -H "Accept: application/vnd.github.raw" -o "$TMP" "$URL"; then
    log "Download failed, keeping current file"
    exit 1
fi
if [ ! -s "$TMP" ]; then
    log "Downloaded file is empty, keeping current file"
    exit 1
fi
# Nothing changed -> no reload needed
if [ -f "$DEST" ] && cmp -s "$TMP" "$DEST"; then
    log "No change in blocklist ($(wc -l < "$DEST") entries)"
    exit 0
fi

# Backup current file, install new one
[ -f "$DEST" ] && cp -p "$DEST" "$BACKUP"
install -m 644 -o root -g root "$TMP" "$DEST"

# 2) Validate config, reload, check Squid is still running
if squid -k parse >/dev/null 2>&1 \
   && systemctl reload squid \
   && sleep 5 \
   && systemctl is-active --quiet squid; then
    log "Blocklist updated and Squid reloaded OK ($(wc -l < "$DEST") entries)"
    exit 0
fi

# 3) Rollback: restore old file and reload (restart if Squid died)
log "New blocklist failed, rolling back"
if [ -f "$BACKUP" ]; then
    cp -p "$BACKUP" "$DEST"
fi
systemctl reload squid 2>/dev/null || systemctl restart squid
sleep 5
if systemctl is-active --quiet squid; then
    log "Rollback OK, Squid running with previous blocklist"
else
    log "CRITICAL: Squid not running after rollback"
fi
exit 1
