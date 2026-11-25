#!/bin/bash
# Vidéothèque : IP publiques des AUTRES proxys de la famille (connexion obligatoire chez eux),
# traitées par nginx comme sources de confiance : jamais limitées, et marquées « 1 » dans le
# journal, donc ignorées par fail2ban et par la supervision.
# Ces IP sont dynamiques (No-IP) : résolues toutes les 5 min (cron) ; nginx n'est rechargé que
# si elles ont changé. Proxy local (127.0.0.1), maison et VPN : déjà dans nginx-videotheque.conf.
set -u
HOSTS="nk-j8b3b7.flox-arts.net"          # aphrodite
OUT=/etc/nginx/videotheque-trusted.conf
TMP=$(mktemp)
for h in $HOSTS; do
    for ip in $(getent ahostsv4 "$h" | awk '{print $1}' | sort -u); do
        echo "$ip 1;  # $h" >> "$TMP"
    done
done
# DNS en panne : on garde la liste précédente plutôt que de retirer la confiance
if [ ! -s "$TMP" ] && [ -f "$OUT" ]; then rm -f "$TMP"; exit 0; fi
if [ ! -f "$OUT" ] || ! cmp -s "$TMP" "$OUT"; then
    install -m 644 "$TMP" "$OUT"
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        logger -t videotheque "sources de confiance mises à jour : $(awk '{print $1}' "$OUT" | tr '\n' ' ')"
    fi
fi
rm -f "$TMP"
