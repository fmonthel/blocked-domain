1. Le script Switch-Proxy.ps1

Enregistre-le dans un dossier qui ne bougera pas, par exemple %LOCALAPPDATA%\ProxySwitch\

2. Créer la tâche planifiée

Lance ce bloc une fois dans PowerShell. Il déclenche le script à chaque connexion réseau (événement 10000 du journal NetworkProfile) et à l’ouverture de session :

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
3. Tester
Start-ScheduledTask -TaskName ProxySwitch

Puis lis le log :

Get-Content "$env:LOCALAPPDATA\ProxySwitch\switch-proxy.log" -Tail 5

Tu peux aussi vérifier le résultat dans Paramètres → Réseau et Internet → Proxy.
