import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg

import psutil

from .constants import (
    HOSTS_PATH_TEMPLATE,
    MALICIOUS_IP_MARKER,
    MALICIOUS_STARTUP_ENTRIES,
    SCHEDULED_TASK_INDICATORS,
    STARTUP_REGISTRY_KEYS,
    SUSPICIOUS_CMDLINE_INDICATORS,
    SUSPICIOUS_DIRECTORY_INDICATORS,
    suspicious_name_set,
    suspicious_paths,
    search_locations,
)
from .reporting import write_report


class PhantomLinkRemover:
    def __init__(self):
        self.suspicious_names = list(suspicious_name_set())
        self.suspicious_paths = suspicious_paths()
        self.removed_items = []
        self.errors = []

    def is_admin(self):
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False

    def elevate_privileges(self):
        if not self.is_admin():
            print("[!] Administrator privileges required. Requesting elevation...")
            try:
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, " ".join(sys.argv), None, 1
                )
                sys.exit()
            except Exception:
                print("[!] Failed to elevate privileges. Some operations may fail.")
                return False
        return True

    def log_action(self, action, success=True):
        status = "[+]" if success else "[!]"
        message = f"{status} {action}"
        print(message)
        if success:
            self.removed_items.append(action)
        else:
            self.errors.append(action)

    def kill_suspicious_processes(self):
        print("\n[*] Scanning for malicious processes...")
        killed_processes = []

        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = proc.info["name"] or ""
                exe_path = proc.info["exe"]
                _cmdline = " ".join(proc.info["cmdline"] or [])
                _suspicious_cmdline_indicators = SUSPICIOUS_CMDLINE_INDICATORS

                if name.lower() in self.suspicious_names:
                    self.terminate_process(proc, f"Suspicious process: {name}")
                    killed_processes.append(name)
                    continue

                if exe_path:
                    for sus_path in self.suspicious_paths:
                        if sus_path.lower() in exe_path.lower():
                            self.terminate_process(
                                proc, f"Process in suspicious location: {exe_path}"
                            )
                            killed_processes.append(name)
                            break

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if killed_processes:
            self.log_action(f"Killed processes: {', '.join(set(killed_processes))}")
        else:
            self.log_action("No suspicious processes found")

    def terminate_process(self, proc, reason):
        """Safely terminate a process."""
        try:
            print(f"[!] {reason} (PID: {proc.pid})")
            proc.terminate()
            proc.wait(timeout=5)
            if proc.is_running():
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def remove_startup_entries(self):
        print("\n[*] Removing startup entries...")

        for hkey, key_path in STARTUP_REGISTRY_KEYS:
            try:
                reg_key = winreg.OpenKey(hkey, key_path, 0, winreg.KEY_ALL_ACCESS)
                values_to_delete = self.find_startup_values_to_delete(reg_key)

                for name in values_to_delete:
                    try:
                        winreg.DeleteValue(reg_key, name)
                        self.log_action(f"Removed startup entry: {name}")
                    except Exception as e:
                        self.log_action(
                            f"Failed to remove startup entry {name}: {e}", False
                        )

                winreg.CloseKey(reg_key)

            except Exception as e:
                self.log_action(f"Error accessing registry key {key_path}: {e}", False)

    def find_startup_values_to_delete(self, reg_key):
        i = 0
        values_to_delete = []
        while True:
            try:
                name, value, _value_type = winreg.EnumValue(reg_key, i)

                if name in MALICIOUS_STARTUP_ENTRIES:
                    values_to_delete.append(name)
                elif self.has_suspicious_path(str(value)):
                    values_to_delete.append(name)
                elif self.has_suspicious_name(str(value)):
                    values_to_delete.append(name)

                i += 1
            except WindowsError:
                break
        return values_to_delete

    def has_suspicious_path(self, value):
        return any(sus_path.lower() in value.lower() for sus_path in self.suspicious_paths)

    def has_suspicious_name(self, value):
        return any(sus_name in value.lower() for sus_name in self.suspicious_names)

    def remove_scheduled_tasks(self):
        """Remove malicious scheduled tasks."""
        print("\n[*] Checking for malicious scheduled tasks...")

        try:
            result = subprocess.run(
                ["schtasks", "/query", "/fo", "csv"],
                capture_output=True,
                text=True,
                check=True,
            )

            tasks = result.stdout.split("\n")[1:]
            suspicious_tasks = []

            for task_line in tasks:
                if task_line.strip():
                    task_name = task_line.split(",")[0].strip('"')

                    if any(sus in task_name.lower() for sus in SCHEDULED_TASK_INDICATORS):
                        suspicious_tasks.append(task_name)

            for task in suspicious_tasks:
                try:
                    subprocess.run(
                        ["schtasks", "/delete", "/tn", task, "/f"],
                        check=True,
                        capture_output=True,
                    )
                    self.log_action(f"Removed scheduled task: {task}")
                except subprocess.CalledProcessError:
                    self.log_action(f"Failed to remove scheduled task: {task}", False)

        except subprocess.CalledProcessError:
            self.log_action("Could not enumerate scheduled tasks", False)

    def remove_malicious_files(self):
        """Remove malicious files and directories."""
        print("\n[*] Removing malicious files...")

        for path in self.suspicious_paths:
            if os.path.exists(path):
                try:
                    shutil.rmtree(path, ignore_errors=True)
                    if os.path.exists(path):
                        subprocess.run(["rmdir", "/s", "/q", path], shell=True)

                    if not os.path.exists(path):
                        self.log_action(f"Removed directory: {path}")
                    else:
                        self.log_action(f"Failed to completely remove: {path}", False)
                except Exception as e:
                    self.log_action(f"Error removing {path}: {e}", False)

        for location in search_locations():
            if os.path.exists(location):
                self.scan_and_remove_files(location)

    def scan_and_remove_files(self, directory):
        try:
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.lower() in self.suspicious_names:
                        file_path = os.path.join(root, file)
                        try:
                            os.remove(file_path)
                            self.log_action(f"Removed file: {file_path}")
                        except Exception as e:
                            self.log_action(f"Failed to remove {file_path}: {e}", False)

                for dir_name in dirs[:]:
                    if any(sus in dir_name.lower() for sus in SUSPICIOUS_DIRECTORY_INDICATORS):
                        dir_path = os.path.join(root, dir_name)
                        try:
                            shutil.rmtree(dir_path, ignore_errors=True)
                            if not os.path.exists(dir_path):
                                self.log_action(f"Removed directory: {dir_path}")
                                dirs.remove(dir_name)
                        except Exception:
                            pass
        except Exception as e:
            self.log_action(f"Error scanning {directory}: {e}", False)

    def restore_system_settings(self):
        """Restore modified system settings."""
        print("\n[*] Restoring system settings...")

        try:
            subprocess.run(
                [
                    "reg",
                    "add",
                    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
                    "/v",
                    "EnableLUA",
                    "/t",
                    "REG_DWORD",
                    "/d",
                    "1",
                    "/f",
                ],
                check=True,
                capture_output=True,
            )
            self.log_action("Re-enabled UAC")
        except subprocess.CalledProcessError:
            self.log_action("Failed to re-enable UAC", False)

        try:
            subprocess.run(
                [
                    "reg",
                    "delete",
                    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System",
                    "/v",
                    "DisableTaskMgr",
                    "/f",
                ],
                capture_output=True,
            )
            self.log_action("Re-enabled Task Manager")
        except subprocess.CalledProcessError:
            pass

        try:
            subprocess.run(
                ["net", "user", "PhantomLink", "/delete"],
                check=True,
                capture_output=True,
            )
            self.log_action("Removed malicious user account: PhantomLink")
        except subprocess.CalledProcessError:
            pass

    def clean_hosts_file(self):
        print("\n[*] Cleaning hosts file...")

        hosts_path = os.path.expandvars(HOSTS_PATH_TEMPLATE)

        try:
            if os.path.exists(hosts_path):
                with open(hosts_path, "r", encoding="utf-8", errors="ignore") as hosts:
                    lines = hosts.readlines()

                clean_lines = []
                removed_entries = []

                for line in lines:
                    if MALICIOUS_IP_MARKER and any(
                        marker in line for marker in MALICIOUS_IP_MARKER
                    ):
                        removed_entries.append(line.strip())
                    else:
                        clean_lines.append(line)

                if removed_entries:
                    shutil.copy2(hosts_path, hosts_path + ".backup")

                    with open(hosts_path, "w", encoding="utf-8") as hosts:
                        hosts.writelines(clean_lines)

                    self.log_action(f"Cleaned {len(removed_entries)} entries from hosts file")

                    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
                    self.log_action("Flushed DNS cache")
                else:
                    self.log_action("Hosts file is clean")

        except Exception as e:
            self.log_action(f"Error cleaning hosts file: {e}", False)

    def check_network_connections(self):
        print("\n[*] Checking network connections...")

        suspicious_connections = []

        for conn in psutil.net_connections():
            if conn.raddr and conn.raddr.ip in MALICIOUS_IP_MARKER:
                suspicious_connections.append(f"{conn.raddr.ip}:{conn.raddr.port}")

        if suspicious_connections:
            self.log_action(
                "WARNING: Active connections to malicious IPs: "
                f"{', '.join(suspicious_connections)}",
                False,
            )
        else:
            self.log_action("No suspicious network connections found")

    def run_system_scans(self):
        print("\n[*] Running system scans...")

        try:
            print("[*] Running System File Checker (sfc /scannow)...")
            result = subprocess.run(
                ["sfc", "/scannow"], capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                self.log_action("System File Checker completed successfully")
            else:
                self.log_action("System File Checker found issues", False)
        except subprocess.TimeoutExpired:
            self.log_action("System File Checker timed out", False)
        except Exception as e:
            self.log_action(f"Failed to run System File Checker: {e}", False)

    def generate_report(self):
        print("\n" + "=" * 60)
        print("PHANTOMLINK RAT REMOVAL REPORT")
        print("=" * 60)

        print(f"\n[+] Actions completed successfully: {len(self.removed_items)}")
        for item in self.removed_items:
            print(f"  - {item}")

        if self.errors:
            print(f"\n[!] Errors encountered: {len(self.errors)}")
            for error in self.errors:
                print(f"  - {error}")

        print("\n[*] RECOMMENDATIONS:")
        print("  1. Restart your computer to complete the removal process")
        print("  2. Run a full antivirus scan with updated definitions")
        print("  3. Change all passwords from a clean system")
        print("  4. Monitor system behavior for any remaining suspicious activity")
        print("  5. Update all software and operating system")

        try:
            path = write_report(self.removed_items, self.errors)
            print(f"\n[+] Detailed report saved to: {path}")
        except Exception as e:
            print(f"[!] Failed to save report: {e}")

    def run_full_removal(self):
        print("PhantomLink RAT Removal Tool")
        print("=" * 40)

        if not self.elevate_privileges():
            print("[!] Some operations may fail without administrator privileges")

        print("[*] Starting comprehensive malware removal...")

        self.kill_suspicious_processes()

        time.sleep(2)

        self.remove_startup_entries()
        self.remove_scheduled_tasks()

        self.remove_malicious_files()

        self.restore_system_settings()

        self.clean_hosts_file()
        self.check_network_connections()

        self.generate_report()

        print("\n[+] Removal process completed!")
        print("[*] Please restart your system and run a full antivirus scan.")


def main():
    try:
        remover = PhantomLinkRemover()
        remover.run_full_removal()
    except KeyboardInterrupt:
        print("\n[!] Removal process interrupted by user")
    except Exception as e:
        print(f"\n[!] Unexpected error: {e}")
        print("[*] Please run the tool as administrator and try again")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
