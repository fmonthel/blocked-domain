# Switch-Proxy.ps1 : choisit le proxy selon le réseau Wi-Fi
#   Switch-Proxy.ps1          -> applique le proxy du réseau actuel (tâche ProxySwitch)
#   Switch-Proxy.ps1 -Check   -> vérifie que personne n'a modifié le proxy (tâche ProxyWatchdog)
# N'utilise pas netsh : fonctionne sans la localisation et sans droits admin
param([switch]$Check)

$ProxyDefaut = 'nk-h2k4g2.flox-arts.net:8443'
$ProxyFlox   = '192.168.18.245:3128'
$ProxyBypass = '<local>'
$RegPath     = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$Log         = Join-Path $PSScriptRoot 'switch-proxy.log'
$Etat        = Join-Path $PSScriptRoot 'expected.json'
$NtfyFile    = Join-Path $PSScriptRoot 'ntfy-topic.txt'   # topic ntfy, hors du dépôt

function Write-Log($Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message" | Add-Content $Log
}

# Prévient Windows que les réglages ont changé
function Update-WinInet {
    if (-not ('Win.WinInet' -as [type])) {
        Add-Type -Namespace Win -Name WinInet -MemberDefinition '[DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr h, int o, IntPtr b, int l);'
    }
    [Win.WinInet]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) | Out-Null   # SETTINGS_CHANGED
    [Win.WinInet]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) | Out-Null   # REFRESH
}

# Écrit un état complet dans le registre (une valeur vide est supprimée)
function Set-ProxyState($State) {
    Set-ItemProperty $RegPath -Name ProxyEnable -Value ([int]$State.ProxyEnable) -Type DWord
    foreach ($nom in 'ProxyServer', 'ProxyOverride', 'AutoConfigURL') {
        if ($State.$nom) {
            Set-ItemProperty $RegPath -Name $nom -Value $State.$nom
        } else {
            Remove-ItemProperty $RegPath -Name $nom -ErrorAction SilentlyContinue
        }
    }
    Update-WinInet
}

# Différences entre l'état attendu et le registre (vide si tout est conforme)
function Compare-ProxyState {
    $attendu = Get-Content $Etat -Raw | ConvertFrom-Json
    $reel    = Get-ItemProperty $RegPath
    $diff    = @()
    if ([int]$reel.ProxyEnable -ne [int]$attendu.ProxyEnable) {
        $diff += "ProxyEnable=$([int]$reel.ProxyEnable) (attendu $($attendu.ProxyEnable))"
    }
    if ($attendu.ProxyEnable -eq 1) {
        foreach ($nom in 'ProxyServer', 'ProxyOverride') {
            if ([string]$reel.$nom -ne [string]$attendu.$nom) {
                $diff += "$nom='$($reel.$nom)' (attendu '$($attendu.$nom)')"
            }
        }
    }
    # Un script PAC passe avant le proxy manuel : il ne doit jamais être défini
    if ($reel.AutoConfigURL) { $diff += "AutoConfigURL='$($reel.AutoConfigURL)'" }
    return $diff
}

function Send-Alerte($Message) {
    if (-not (Test-Path $NtfyFile)) { return }
    $topic = (Get-Content $NtfyFile -Raw).Trim()
    try {
        Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$topic" `
            -Headers @{ Title = "Proxy modifie sur $env:COMPUTERNAME"; Priority = 'high'; Tags = 'warning' } `
            -ContentType 'text/plain; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($Message)) | Out-Null
    } catch {
        Write-Log "Erreur ntfy : $($_.Exception.Message)"
    }
}

# --- Mode surveillance -------------------------------------------------------
if ($Check) {
    if (-not (Test-Path $Etat)) { return }
    if (-not (Compare-ProxyState)) { return }

    Start-Sleep -Seconds 15   # un changement de réseau est peut-être en cours
    $diff = Compare-ProxyState
    if (-not $diff) { return }

    Set-ProxyState (Get-Content $Etat -Raw | ConvertFrom-Json)
    $msg = "Utilisateur $env:USERNAME : $($diff -join ' | '). Proxy rétabli."
    Write-Log "ALERTE  $msg"
    Send-Alerte $msg
    return
}

# --- Mode changement de réseau -----------------------------------------------
Start-Sleep -Seconds 3   # laisse la connexion se stabiliser

# Dernière connexion Wi-Fi réussie (événement 8001 du journal WLAN-AutoConfig)
$profil = $null
$ssid   = $null
$evt = Get-WinEvent -LogName 'Microsoft-Windows-WLAN-AutoConfig/Operational' `
       -FilterXPath '*[System[EventID=8001]]' -MaxEvents 1 -ErrorAction SilentlyContinue
if ($evt) {
    $data = @{}
    ([xml]$evt.ToXml()).Event.EventData.Data | ForEach-Object { $data[$_.Name] = $_.'#text' }
    # Ne retenir l'événement que si la carte Wi-Fi concernée est toujours connectée
    $carte = Get-NetAdapter | Where-Object { $_.InterfaceGuid -eq $data.InterfaceGuid -and $_.Status -eq 'Up' }
    if ($carte) {
        $profil = $data.ProfileName
        $ssid   = $data.SSID
    }
}

# Pas de Wi-Fi connecté (Ethernet…) : nom du réseau vu par Windows
if (-not $profil) {
    $profil = (Get-NetConnectionProfile | Select-Object -ExpandProperty Name) -join ', '
}

# Règles, testées dans l'ordre
if ($profil -like '*WCDM*') {
    $serveur = $null
    $action  = 'proxy DÉSACTIVÉ'
}
elseif ($profil -eq 'Flox-arts.net' -or $ssid -eq 'Flox-arts.net') {
    $serveur = $ProxyFlox
    $action  = "proxy Flox-arts : $ProxyFlox"
}
else {
    $serveur = $ProxyDefaut
    $action  = "proxy FORCÉ : $ProxyDefaut"
}

$attendu = if ($serveur) {
    @{ ProxyEnable = 1; ProxyServer = $serveur; ProxyOverride = $ProxyBypass; AutoConfigURL = '' }
} else {
    $actuel = Get-ItemProperty $RegPath
    @{ ProxyEnable = 0; ProxyServer = $actuel.ProxyServer; ProxyOverride = $actuel.ProxyOverride; AutoConfigURL = '' }
}

Set-ProxyState $attendu
$attendu | ConvertTo-Json | Set-Content $Etat   # référence pour le mode -Check

Write-Log "SSID='$ssid'  Profil='$profil'  ->  $action"
