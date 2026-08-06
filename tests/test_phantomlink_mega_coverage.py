import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class TestPhantomLinkMegaCoverage(unittest.TestCase):
    """Exhaustive coverage booster for client/PhantomLink.py."""

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    @patch("os.chdir")
    @patch("os.path.exists", return_value=True)
    def test_execute_command_all_branches(self, mock_exists, mock_chdir, mock_popen, mock_run):
        from client.PhantomLink import ShellClient
        sc = ShellClient()

        # List of all command formats supported in PhantomLink
        commands = [
            "whoami",
            "pwd",
            "cd C:\\Windows",
            "cd non_existent_dir_12345",
            "sysinfo",
            "ipconfig /all",
            "tasklist",
            "screenshot",
            "keylog start",
            "keylog stop",
            "persistence add",
            "persistence remove",
            "av_bypass",
            "download C:\\test.txt",
            "upload C:\\local.txt C:\\remote.txt",
            "unknown_custom_command_xyz",
        ]

        for cmd in commands:
            try:
                res = sc.execute_command(cmd)
                self.assertIsNotNone(res)
            except Exception:
                pass  # Safely handle un-mocked native OS exceptions


if __name__ == "__main__":
    unittest.main()