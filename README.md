# ProxySwitch

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
> Sur Windows 11 24H2 et plus, `netsh wlan` peut exiger que la **localisation** soit activée
> (*Paramètres → Confidentialité et sécurité → Localisation*). Sinon, le script se rabat sur
> `Get-NetConnectionProfile`.

## 1. Installer le script

Créer le dossier `%LOCALAPPDATA%\ProxySwitch\` et y enregistrer le fichier `Switch-Proxy.ps1` :

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\ProxySwitch" | Out-Null
Invoke-WebRequest -UseBasicParsing `
    -Uri 'https://raw.githubusercontent.com/fmonthel/blocked-domain/refs/heads/main/Switch-proxy-ps1' `
    -OutFile "$env:LOCALAPPDATA\ProxySwitch\Switch-Proxy.ps1"
```

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

## Désinstallation

```powershell
Unregister-ScheduledTask -TaskName ProxySwitch -Confirm:$false
Remove-Item "$env:LOCALAPPDATA\ProxySwitch" -Recurse
```

## Limites

- Seul le proxy **utilisateur** (WinINet : Edge, Chrome, la plupart des applications) est modifié.
  Les services utilisant **WinHTTP** nécessitent `netsh winhttp set proxy` (administrateur).
- Le proxy est réappliqué à chaque connexion, mais l'utilisateur peut le désactiver manuellement
  entre deux connexions. Pour un verrouillage strict, passer par une GPO.
- Une fenêtre PowerShell peut apparaître une fraction de seconde au déclenchement.
