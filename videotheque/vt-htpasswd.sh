#!/bin/bash
# Vidéothèque : le site accepte les mêmes comptes que le proxy.
# Copie /etc/squid/passwords (format htpasswd $apr1$, lisible par nginx) vers le fichier de nginx,
# seulement s'il a changé. Lancé toutes les 15 min par /etc/cron.d/videotheque.
set -eu
SRC=/etc/squid/passwords
DST=/etc/nginx/videotheque.htpasswd
[ -s "$SRC" ] || exit 0
if [ ! -f "$DST" ] || ! cmp -s "$SRC" "$DST"; then
    install -m 640 -o root -g www-data "$SRC" "$DST"
    logger -t videotheque "comptes du site mis à jour depuis $SRC"
fi
