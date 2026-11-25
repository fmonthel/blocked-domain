# Vidéothèque (Video Library)

Copie locale de chaînes YouTube éducatives sur **nicomaque**, servie par un site façon YouTube
(interface en anglais) et accessible aux enfants **uniquement via le proxy**, avec leurs comptes du proxy.

## Ajouter / retirer une chaîne

Modifier [`videotheque/chaines.conf`](../videotheque/chaines.conf) par pull request (une URL de chaîne par ligne,
ex. `https://www.youtube.com/@YMONKA`). Après fusion :
- la synchro (15 min) installe la nouvelle liste sur nicomaque ;
- la nuit suivante (01:30), `vt-sync.py` commence à copier la chaîne, des vidéos les plus récentes aux plus
  anciennes, avec un budget par nuit (150 vidéos par défaut) : une grosse chaîne met donc quelques nuits ;
- ensuite, chaque nuit, seulement les nouvelles vidéos (et le dimanche une repasse complète).

Une chaîne retirée disparaît du site ; ses fichiers restent sur le serveur (suppression à la demande).
Seul l'onglet « Vidéos » est copié (pas de Shorts, pas de directs), en 720p maximum (H.264, pas de réencodage).

## Fichiers du dépôt → serveur

| Dépôt | Installé dans |
|---|---|
| `videotheque/chaines.conf` | `/opt/videotheque/chaines.conf` |
| `videotheque/vt-sync.py` | `/opt/videotheque/vt-sync.py` |
| `videotheque/vt-htpasswd.sh` | `/opt/videotheque/vt-htpasswd.sh` |
| `videotheque/videotheque.cron` | `/etc/cron.d/videotheque` |
| `videotheque/nginx-videotheque.conf` | `/etc/nginx/sites-available/videotheque` |
| `videotheque/web/*` | `/opt/videotheque/web/` |

`sync-from-github.sh` n'installe ces fichiers que si `/etc/videotheque/enabled` existe (préparation unique du
serveur, faite hors dépôt : volume `/srv/videotheque`, nginx, ffmpeg, yt-dlp dans `/opt/videotheque/venv`,
QuickJS-NG `/usr/local/bin/qjs`, utilisateur `videotheque`, pare-feu 8090). aphrodite les ignore.

## Accès

- Site : `http://<nom public de nicomaque>:8090/` — port 8090 redirigé par le routeur vers nicomaque.
- Connexion : mêmes comptes que le proxy (`vt-htpasswd.sh` copie `/etc/squid/passwords` toutes les 15 min).
- Via le proxy de nicomaque : `/etc/hosts` de nicomaque fait pointer le nom vers 127.0.0.1 (pas de NAT loopback).
- Via le proxy d'aphrodite : passage par le port public.

## Sur le serveur

```bash
tail -f /var/log/videotheque/vt-sync.log              # dernière synchro
cat /srv/videotheque/status.json                      # bilan (lu par la supervision)
sudo -u videotheque python3 -B /opt/videotheque/vt-sync.py --max 5    # synchro manuelle limitée
sudo -u videotheque python3 -B /opt/videotheque/vt-sync.py --index-only  # reconstruire library.json
```

Réglages propres au serveur (facultatif) : `/etc/videotheque/local.conf`, section `[vt]` :
`max_par_nuit`, `duree_max_heures`, `hauteur_max`.

## Quand YouTube casse yt-dlp

yt-dlp est mis à jour chaque lundi (`pip install -U "yt-dlp[default]"`). Si `status.json` signale une erreur
(« Sign in to confirm », 429, défi JavaScript), la supervision envoie une alerte ; mettre à jour yt-dlp à la main
et relancer une synchro limitée. yt-dlp utilise QuickJS-NG comme moteur JavaScript (nicomaque est en i686 :
pas de deno ni de node ≥ 22).

Usage strictement familial et privé (connexion obligatoire) : les vidéos ne sont pas redistribuées.
