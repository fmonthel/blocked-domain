# Setup des PC Windows : ProxySwitch

> Retour au [README](../README.md).

Bascule automatiquement le proxy Windows selon le réseau Wi‑Fi auquel le poste se connecte.

| Réseau | Proxy appliqué |
|---|---|
| Profil contenant `WCDM` | Désactivé |
| `Flox-arts.net` (profil ou SSID) | `http://192.168.18.245:3128` |
| Tout autre réseau | `http://nk-h2k4g2.flox-arts.net:8443` |

Les règles sont testées dans cet ordre : la première qui correspond s'applique.

## Prérequis

- Windows 10 / 11
- PowerShell 5.1 ou supérieur
- Aucun droit administrateur requis (le proxy est un réglage utilisateur, `HKCU`)

> [!NOTE]
> Le script n'utilise pas `netsh wlan` : il lit le profil et le SSID dans le journal
> `Microsoft-Windows-WLAN-AutoConfig/Operational` (événement `8001`). Il fonctionne donc
> **même avec la localisation désactivée**.

## 1. Installer le script

Télécharger le script depuis ce dépôt dans `%LOCALAPPDATA%\ProxySwitch\` :

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\ProxySwitch" | Out-Null
Invoke-WebRequest -UseBasicParsing `
    -Uri 'https://raw.githubusercontent.com/fmonthel/blocked-domain/refs/heads/main/Switch-Proxy.ps1' `
    -OutFile "$env:LOCALAPPDATA\ProxySwitch\Switch-Proxy.ps1"

# Topic ntfy pour les alertes (gardé en local, jamais dans le dépôt)
Set-Content "$env:LOCALAPPDATA\ProxySwitch\ntfy-topic.txt" '<ton-topic>'
```

Pour modifier les proxys ou les règles, éditer les variables en haut de
`%LOCALAPPDATA%\ProxySwitch\Switch-Proxy.ps1`.

> [!TIP]
> Dans les réglages Windows, le proxy s'écrit `hôte:port` sans `http://` : c'est un proxy HTTP par défaut.

## 2. Créer la tâche planifiée

À exécuter une seule fois dans PowerShell. La tâche se déclenche :

- à **chaque connexion réseau** (événement `10000` du journal `Microsoft-Windows-NetworkProfile/Operational`) ;
- à **l'ouverture de session**.

```powershell
$Script = "$env:LOCALAPPDATA\ProxySwitch\Switch-Proxy.ps1"

# Déclencheur sur événement « réseau connecté »
$cls  = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$trigEvent = New-CimInstance -CimClass $cls -ClientOnly
$trigEvent.Enabled = $true
$trigEvent.Subscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[EventID=10000]]</Select></Query></QueryList>'

$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
             -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'ProxySwitch' -Trigger $trigEvent, $trigLogon `
    -Action $action -Principal $principal -Settings $settings -Force
```

> [!IMPORTANT]
> En cas d'erreur « Accès refusé », relancer ce bloc dans un PowerShell **administrateur**.
> La tâche s'exécutera quand même sous le compte utilisateur.

## 3. Surveillance et alertes

Une seconde tâche lance `Switch-Proxy.ps1 -Check` **toutes les 5 minutes**. Elle compare le registre
à l'état appliqué lors du dernier changement de réseau (`expected.json`). Si quelqu'un a modifié le
proxy à la main (désactivation, autre serveur, exceptions, script PAC), elle :

1. **rétablit** le proxy attendu ;
2. **envoie une notification** sur le topic [ntfy](https://ntfy.sh) défini dans `ntfy-topic.txt` ;
3. note l'alerte dans `switch-proxy.log`.

```powershell
$Script    = "$env:LOCALAPPDATA\ProxySwitch\Switch-Proxy.ps1"
$trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
             -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Check"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'ProxyWatchdog' -Trigger $trigger `
    -Action $action -Principal $principal -Settings $settings -Force
```

Pour recevoir les alertes : installer l'application **ntfy** (Android / iOS) et s'abonner au topic.

> [!WARNING]
> Sans droits administrateur, les tâches et les fichiers appartiennent à l'utilisateur surveillé :
> il peut les supprimer. Le topic ntfy est public : quiconque connaît son nom peut le lire ou y publier.

## Utilisation

Forcer une exécution immédiate :

```powershell
Start-ScheduledTask -TaskName ProxySwitch
```

Consulter le journal :

```powershell
Get-Content "$env:LOCALAPPDATA\ProxySwitch\switch-proxy.log" -Tail 5
```

Exemple de sortie :

```
2026-10-08 09:12:41  SSID='Flox-arts.net'  Profil='Flox-arts.net'  ->  proxy Flox-arts : 192.168.18.245:3128
```

## Mise à jour

Relancer la commande de l'étape 1 : le fichier est écrasé par la dernière version du dépôt.
La tâche planifiée n'a pas besoin d'être recréée.

## Désinstallation

```powershell
Unregister-ScheduledTask -TaskName ProxySwitch -Confirm:$false
Unregister-ScheduledTask -TaskName ProxyWatchdog -Confirm:$false
Remove-Item "$env:LOCALAPPDATA\ProxySwitch" -Recurse
```

## Limites

- Seul le proxy **utilisateur** (WinINet : Edge, Chrome, la plupart des applications) est modifié.
  Les services utilisant **WinHTTP** nécessitent `netsh winhttp set proxy` (administrateur).
- Le proxy est réappliqué à chaque connexion, mais l'utilisateur peut le désactiver manuellement
  entre deux connexions. Pour un verrouillage strict, passer par une GPO.
- Une fenêtre PowerShell peut apparaître une fraction de seconde au déclenchement.
