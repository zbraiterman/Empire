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
        instance = params["Instance"]
        check_all = params["CheckAll"].lower() == "true"
        username = params["Username"]
        password = params["Password"]

        # Always need Get-SQLServerLoginDefaultPw
        script, _err = main_menu.modulesv2.get_module_source(
            module_name="recon/Get-SQLServerLoginDefaultPw.ps1",
            obfuscate=obfuscate,
            obfuscate_command=obfuscation_command,
        )

        if check_all:
            # Also load Get-SQLInstanceDomain to discover instances
            script2, _err = main_menu.modulesv2.get_module_source(
                module_name="situational_awareness/network/Get-SQLInstanceDomain.ps1",
                obfuscate=obfuscate,
                obfuscate_command=obfuscation_command,
            )
            script += "\n" + script2

            script_end = "Get-SQLInstanceDomain"
            if username != "":
                script_end += " -Username " + username
            if password != "":
                script_end += " -Password " + password
            script_end += " | Get-SQLServerLoginDefaultPw"

        elif instance != "":
            script_end = "Get-SQLServerLoginDefaultPw"
            script_end += " -Instance '" + instance + "'"

        else:
            raise ModuleValidationException(
                "Either CheckAll or Instance must be specified."
            )

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end=script_end,
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
