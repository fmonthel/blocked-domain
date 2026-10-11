#!/bin/bash
# Keep this server in line with the GitHub repo (main): Squid config, block page, scripts,
# cron jobs and BIND options. Runs every 15 min from /etc/cron.d/squid-proxy.
#
# - Does nothing if the latest commit on main is already deployed (/root/squid/.deployed-commit).
# - Every file is validated before it is installed (bash -n, Python syntax, squid -k parse,
#   named-checkconf); an invalid file is skipped and the run is retried later.
# - Squid / BIND are reloaded only when their files changed; if Squid fails to come back,
#   the previous files are restored.
# - The blocklist itself is handled by update-squid-blocklist.sh (hourly).
#
# install: /root/squid/sync-from-github.sh (chmod 755)
# usage:   sync-from-github.sh [--dry-run] [--force]
set -u

REPO="fmonthel/blocked-domain"
BRANCH="main"
STATE="/root/squid/.deployed-commit"
LOCK="/run/sync-from-github.lock"
TAG="proxy-sync"
DRY=0; FORCE=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --force)   FORCE=1 ;;
    esac
done

log() { logger -t "$TAG" "$*"; echo "$(date '+%F %T') $*"; }

exec 9>"$LOCK"
flock -n 9 || exit 0

SHA=$(curl -fsS --max-time 30 -H "Accept: application/vnd.github.sha" \
      "https://api.github.com/repos/$REPO/commits/$BRANCH") || { log "cannot reach GitHub, nothing done"; exit 1; }
if [ $FORCE -eq 0 ] && [ $DRY -eq 0 ] && [ -f "$STATE" ] && [ "$(cat "$STATE")" = "$SHA" ]; then
    exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
if ! curl -fsSL --max-time 120 "https://codeload.github.com/$REPO/tar.gz/$SHA" | tar xz -C "$TMP" --strip-components=1; then
    log "download of ${SHA:0:7} failed, nothing done"
    exit 1
fi

# squid.conf in the repo has a placeholder host name: keep this server's own name
CUR_NAME=$(awk '/^visible_hostname/ {print $2; exit}' /etc/squid/squid.conf)
[ -n "$CUR_NAME" ] && [ -f "$TMP/squid/squid.conf" ] && \
    sed -i "s/^visible_hostname .*/visible_hostname $CUR_NAME/" "$TMP/squid/squid.conf"

check_bash()  { bash -n "$1"; }
check_py()    { python3 -B -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$1"; }
check_squid() { ! squid -k parse -f "$1" 2>&1 | grep -qE 'FATAL|ERROR|Bungled'; }
check_named() { named-checkconf "$1"; }
check_none()  { true; }

UPDATED=0; ERRORS=0; RELOAD_SQUID=0; RELOAD_NAMED=0
INSTALLED=()

# deploy <repo path> <destination> <mode> <check function>
# returns 0 if the file was installed, 1 otherwise
deploy() {
    local src="$TMP/$1" dst="$2" mode="$3" check="$4"
    [ -f "$src" ] || return 1
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        return 1
    fi
    if ! "$check" "$src" >/dev/null 2>&1; then
        log "INVALID $1 in ${SHA:0:7}, not installed"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
    if [ $DRY -eq 1 ]; then
        echo "would update $dst"
        return 1
    fi
    [ -f "$dst" ] && cp -p "$dst" "$dst.prev"
    install -D -m "$mode" -o root -g root "$src" "$dst"
    INSTALLED+=("$dst")
    UPDATED=$((UPDATED + 1))
    log "updated $dst"
    return 0
}

deploy squid/squid.conf            /etc/squid/squid.conf                   644 check_squid && RELOAD_SQUID=1
deploy squid/errors/ERR_HOMEWORK   /etc/squid/errors-custom/ERR_HOMEWORK   644 check_none  && RELOAD_SQUID=1
deploy scripts/update-squid-blocklist.sh /root/squid/update-squid-blocklist.sh 755 check_bash
deploy scripts/daily-squid-report.py     /root/squid/daily-squid-report.py     700 check_py
deploy scripts/sync-from-github.sh       /root/squid/sync-from-github.sh       755 check_bash
deploy scripts/squid-proxy.cron          /etc/cron.d/squid-proxy               644 check_none
deploy bind/named.conf.options           /etc/bind/named.conf.options          644 check_named && RELOAD_NAMED=1

# jobs now live in /etc/cron.d/squid-proxy: drop the old copies from root's crontab
if [ $DRY -eq 0 ] && [ -f /etc/cron.d/squid-proxy ] && crontab -l 2>/dev/null | grep -qE 'update-squid-blocklist|daily-squid-report|sync-from-github'; then
    crontab -l | grep -vE 'update-squid-blocklist|daily-squid-report|sync-from-github' | crontab -
    log "moved proxy jobs from root crontab to /etc/cron.d/squid-proxy"
fi

if [ $RELOAD_SQUID -eq 1 ]; then
    systemctl reload squid; sleep 5
    if systemctl is-active --quiet squid; then
        log "Squid reloaded OK"
    else
        log "Squid not running after reload, restoring previous files"
        for f in /etc/squid/squid.conf /etc/squid/errors-custom/ERR_HOMEWORK; do
            [ -f "$f.prev" ] && cp -p "$f.prev" "$f"
        done
        systemctl restart squid; sleep 5
        systemctl is-active --quiet squid && log "Rollback OK" || log "CRITICAL: Squid not running after rollback"
        ERRORS=$((ERRORS + 1))
    fi
fi
if [ $RELOAD_NAMED -eq 1 ]; then
    systemctl reload named && log "BIND reloaded OK" || { log "BIND reload failed"; ERRORS=$((ERRORS + 1)); }
fi

[ $DRY -eq 1 ] && { echo "dry run on ${SHA:0:7}: nothing installed"; exit 0; }
if [ $ERRORS -eq 0 ]; then
    echo "$SHA" > "$STATE"
    [ $UPDATED -gt 0 ] && log "deployed ${SHA:0:7} ($UPDATED file(s) updated)" || log "${SHA:0:7} already in place"
else
    log "${SHA:0:7}: $UPDATED file(s) updated, $ERRORS problem(s); will retry"
    exit 1
fi
