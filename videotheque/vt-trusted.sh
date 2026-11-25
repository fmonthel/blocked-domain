#!/bin/bash
# Vidéothèque : IP publiques de la famille traitées par nginx comme sources de confiance : jamais
# limitées, et marquées « 1 » dans le journal, donc ignorées par fail2ban et par la supervision.
#  - aphrodite : son proxy (connexion obligatoire) arrive avec l'IP publique d'aphrodite ;
#  - nicomaque : le routeur de la maison fait du NAT loopback, les appareils de la maison qui
#    utilisent l'adresse publique arrivent avec l'IP publique de la maison. Sans cette ligne, la
#    maison entière pourrait être limitée, voire bannie par fail2ban.
# Ces IP sont dynamiques (No-IP) : résolues toutes les 5 min (cron) ; nginx n'est rechargé que
# si elles ont changé. Proxy local (127.0.0.1), LAN et VPN : déjà dans nginx-videotheque.conf.
set -u
HOSTS="nk-j8b3b7.flox-arts.net nk-h2k4g2.flox-arts.net"   # aphrodite, nicomaque (maison)
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
