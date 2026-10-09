Get-WinEvent -LogName "Microsoft-Windows-WLAN-AutoConfig/Operational" -FilterXPath "*[System[EventID=8001]]" |
    ForEach-Object {
        $d = ([xml]$_.ToXml()).Event.EventData.Data
        [PSCustomObject]@{
            Date   = $_.TimeCreated
            SSID   = ($d | Where-Object Name -eq 'SSID').'#text'
            Profil = ($d | Where-Object Name -eq 'ProfileName').'#text'
        }
    } | Format-Table -AutoSize
