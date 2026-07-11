import os
import winreg


SUSPICIOUS_NAMES = (
    "defender.exe",
    "client.exe",
    "PhantomLink.exe",
    "windows.exe",
    "keylogger.exe",
)

SUSPICIOUS_PATH_TEMPLATES = (
    r"%APPDATA%\MicrosoftUpdate",
    r"%APPDATA%\MicrosoftUpdater",
    r"%TEMP%\PhantomLink",
)

STARTUP_REGISTRY_KEYS = (
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
)

MALICIOUS_STARTUP_ENTRIES = (
    "Windows Defender Updater",
    "PhantomLink",
    "MicrosoftUpdate",
    "MicrosoftUpdater",
    "Windows Update",
    "System Defender",
    "Keylogger",
)

SCHEDULED_TASK_INDICATORS = (
    "phantomlink",
    "defender",
    "microsoftupdate",
    "windowsupdate",
    "keylogger",
)

SUSPICIOUS_DIRECTORY_INDICATORS = (
    "phantomlink",
    "microsoftupdate",
    "microsoftupdater",
    "keylogger",
)

SUSPICIOUS_CMDLINE_INDICATORS = (
    "PhantomLink",
    "MicrosoftUpdate",
    "defender.exe",
)

SEARCH_LOCATION_TEMPLATES = (
    r"%TEMP%",
    r"%APPDATA%",
    r"%LOCALAPPDATA%",
    r"%USERPROFILE%\Downloads",
    r"%USERPROFILE%\Desktop",
)

HOSTS_PATH_TEMPLATE = r"%WINDIR%\System32\drivers\etc\hosts"
REPORT_FILENAME = "PhantomLink_Removal_Report.txt"
MALICIOUS_IP_MARKER = {
    "81.10.55.8",
    # Add other known C2 IPs here
}


def expand_path_templates(templates):
    return [os.path.expandvars(path) for path in templates]


def suspicious_paths():
    return expand_path_templates(SUSPICIOUS_PATH_TEMPLATES)


def search_locations():
    return expand_path_templates(SEARCH_LOCATION_TEMPLATES)


def suspicious_name_set():
    return {name.lower() for name in SUSPICIOUS_NAMES}
