# Vidéothèque (Video Library)

Copie locale de chaînes YouTube éducatives sur **nicomaque**, servie par un site façon YouTube
(interface en anglais). Pas de connexion : les enfants y passent par le proxy (déjà authentifié) ; les
accès extérieurs directs sont limités et surveillés (voir « Protection contre les abus »).

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
| `videotheque/vt-trusted.sh` | `/opt/videotheque/vt-trusted.sh` |
| `videotheque/videotheque.cron` | `/etc/cron.d/videotheque` |
| `videotheque/nginx-videotheque.conf` | `/etc/nginx/sites-available/videotheque` |
| `videotheque/fail2ban-videotheque.conf` | `/etc/fail2ban/jail.d/videotheque.conf` |
| `videotheque/fail2ban-filter-videotheque-scan.conf` | `/etc/fail2ban/filter.d/videotheque-scan.conf` |
| `videotheque/web/*` | `/opt/videotheque/web/` |

`sync-from-github.sh` n'installe ces fichiers que si `/etc/videotheque/enabled` existe (préparation unique du
serveur, faite hors dépôt : volume `/srv/videotheque`, nginx, ffmpeg, yt-dlp dans `/opt/videotheque/venv`,
QuickJS-NG `/usr/local/bin/qjs`, utilisateur `videotheque`, pare-feu 8090). aphrodite les ignore.

## Accès

- Site : `http://videos.flox-arts.net:8090/` — port 8090 redirigé par le routeur vers nicomaque. Pas de connexion.
- La même adresse marche partout : à la maison (le routeur Nokia d'EBOX fait du NAT loopback, testé le
  2026-11-24 : les appareils de la maison arrivent avec l'IP publique de la maison), à l'extérieur, via les
  proxys et via le VPN.
- Via le proxy de nicomaque : `/etc/hosts` de nicomaque fait pointer le nom vers 127.0.0.1 (trafic local).
- Via le VPN de nicomaque : BIND (zone RPZ locale `/etc/bind/db.rpz.local`, propre au serveur) répond
  `192.168.18.245` pour ce nom.
- Secours à la maison : `http://192.168.18.245:8090/`.

## Protection contre les abus

Sources **de confiance**, jamais limitées ni bannies : proxy de nicomaque (127.0.0.1), réseau de la maison
(192.168.18.0/24), VPN (10.10.10.0/24), IP publique d'aphrodite (son proxy) et IP publique de la maison
(appareils de la maison via le NAT loopback du routeur) ; ces deux IP dynamiques sont résolues toutes les
5 min par `vt-trusted.sh`. On se fie à l'IP source, pas aux en-têtes `Via` / `X-Forwarded-For` (falsifiables).
nginx note dans le journal `1` (confiance) ou `0` (extérieur) en 2e champ.

Pour l'**extérieur** :

| Protection | Règle |
|---|---|
| nginx : pages, catalogue, miniatures | 10 requêtes/s par IP (rafale 60), sinon 429 |
| nginx : vidéos | 4 connexions simultanées, 1 Mo/s par connexion (720p ≈ 0,15 Mo/s) |
| fail2ban `videotheque-limit` | bannie 1 h (croissant) après 20 refus 429 en 1 min |
| fail2ban `videotheque-scan` | bannie après 15 erreurs (400/403/404/405) en 10 min (robots `/wp-login`, `/.env`…) |
| server-watch | alerte par courriel si le trafic extérieur dépasse 3 Go en 1 h, ou si le site ou la synchro de nuit ne vont pas |

```bash
fail2ban-client status videotheque-limit ; fail2ban-client status videotheque-scan
fail2ban-client set videotheque-scan unbanip <IP>     # débannir
awk '$2==0' /var/log/nginx/videotheque.access.log | tail   # requêtes extérieures
```

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

Usage strictement familial et privé : les vidéos ne sont pas redistribuées ; l'adresse n'est pas publiée.
