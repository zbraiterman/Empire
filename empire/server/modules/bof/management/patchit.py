import base64

from empire.server.common.empire import MainMenu
from empire.server.core.module_models import EmpireModule
from empire.server.utils.bof_packer import Packer

MODE_MAP = {
    "check": 1,
    "all": 2,
    "amsi": 3,
    "etw": 4,
    "revertAll": 5,
    "revertAmsi": 6,
    "revertEtw": 7,
}


class Module:
    @staticmethod
    def generate(
        main_menu: MainMenu,
        module: EmpireModule,
        params: dict,
        obfuscate: bool = False,
        obfuscation_command: str = "",
        **kwargs,
    ):
        agent_language = kwargs.get("agent_language", "")

        mode_str = params.get("Mode", "all")
        mode = MODE_MAP.get(mode_str, 2)

        script_path = main_menu.modulesv2.module_source_path / module.bof.x64
        bof_data = script_path.read_bytes()
        b64_bof_data = base64.b64encode(bof_data).decode("utf-8")

        # Pack arguments: i = mode integer
        packer = Packer()
        packer.addint(mode)  # i - operation mode (1-7)

        return main_menu.modulesv2.format_bof_output(
            bof_data_b64=b64_bof_data,
            hex_data=packer.getbuffer_data(),
            agent_language=agent_language,
            obfuscate=obfuscate,
        )
