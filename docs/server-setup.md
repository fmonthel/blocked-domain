# Installation d'un serveur

Procédure pour (ré)installer **nicomaque** ou **aphrodite** (Debian 11/12, en root).
Les deux serveurs sont indépendants : chacun télécharge la blocklist et envoie son propre rapport.

## 1. Réseau

```bash
# DNS statique (le routeur en premier)
cp system/resolv.conf /etc/resolv.conf

# IPv6 désactivé (pas de connectivité IPv6 sur ces réseaux), sauf sur lo
cp system/90-disable-ipv6.conf /etc/sysctl.d/ && sysctl -p /etc/sysctl.d/90-disable-ipv6.conf
```

## 2. Squid

```bash
apt install squid apache2-utils curl

# Configuration et page de blocage
cp squid/squid.conf /etc/squid/squid.conf
sed -i "s/^visible_hostname .*/visible_hostname $(hostname -f)/" /etc/squid/squid.conf
mkdir -p /etc/squid/errors-custom
cp /usr/share/squid/errors/templates/* /etc/squid/errors-custom/
cp squid/errors/ERR_HOMEWORK /etc/squid/errors-custom/

# Un compte par enfant (même nom et mot de passe sur les deux serveurs)
htpasswd -c /etc/squid/passwords <compte1>     # -c seulement pour le premier compte
htpasswd    /etc/squid/passwords <compte2>
chown root:proxy /etc/squid/passwords && chmod 640 /etc/squid/passwords

squid -k parse && systemctl restart squid
```

Pare-feu (ufw) :

```bash
ufw allow 22/tcp
ufw allow 3128                                   # nicomaque : le routeur redirige 8443 → 3128
ufw allow 8443/tcp                               # aphrodite : 8443 exposé directement
ufw allow from 192.168.18.0/24 to any port 8443 proto tcp
ufw allow 51820/udp                              # WireGuard
ufw allow from 10.10.10.0/24                     # clients VPN
```

## 3. Blocklist et rapport quotidien

```bash
mkdir -p /root/squid
cp scripts/update-squid-blocklist.sh scripts/daily-squid-report.py /root/squid/
chmod 755 /root/squid/update-squid-blocklist.sh
chmod 700 /root/squid/daily-squid-report.py
cp scripts/report.conf.example /root/squid/report.conf && chmod 600 /root/squid/report.conf
# éditer report.conf : comptes des enfants, destinataires, expéditeur

# Mot de passe d'application Google (16 caractères, les espaces sont ignorés)
read -rsp "Mot de passe d'application : " P && printf '%s' "$P" > /root/squid/.smtp-app-password \
  && chmod 600 /root/squid/.smtp-app-password && unset P

/root/squid/update-squid-blocklist.sh                                   # 1re liste
python3 -B /root/squid/daily-squid-report.py --preview /tmp/rapport.html # aperçu sans envoi
crontab -e                                                              # coller scripts/crontab
```

Le rapport n'est envoyé que s'il y a eu du trafic des enfants ce jour-là.
Options : `--date AAAA-MM-JJ` pour une autre journée, `--preview fichier.html` pour ne pas envoyer.

## 4. WireGuard et DNS des clients VPN

```bash
apt install wireguard bind9
cp wireguard/wg0.conf.example /etc/wireguard/wg0.conf     # puis y mettre les vraies clés
chmod 600 /etc/wireguard/wg0.conf
systemctl enable --now wg-quick@wg0

cp bind/named.conf.options /etc/bind/named.conf.options
named-checkconf && systemctl restart named
```

`bind/named.conf.options` force les requêtes DNS amont en **TCP** : les deux réseaux
jettent les paquets DNS UDP qui contiennent EDNS, ce qui empêchait BIND de répondre
(les téléphones en VPN n'avaient alors plus de DNS).

NAT pour les clients VPN : `net.ipv4.ip_forward = 1` et, dans `/etc/ufw/before.rules`,
un bloc `*nat` avec `-A POSTROUTING -o <interface-wan> -j MASQUERADE`.

## Vérifications

```bash
systemctl is-active squid named wg-quick@wg0
curl -s -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:3128 http://example.com   # 407 attendu
tail /var/log/squid-blocklist.log /var/log/squid-report.log
```
