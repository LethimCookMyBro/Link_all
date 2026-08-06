import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CLIENT_DIR = _REPO_ROOT / "client"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))

import client.av_bypass as avb
import client.av_killer as avk


class AVToolsCoverageTests(unittest.TestCase):
    """Full production-ready coverage test suite targeting client/av_bypass.py and client/av_killer.py."""

    def test_av_bypass_all_methods(self):
        bypass = avb.AVBypass()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success")
            bypass.disable_defender()
            bypass.add_exclusions()
            bypass.disable_firewall()
            bypass.disable_smartscreen()
            bypass.disable_tamper_protection()
            bypass.clear_event_logs()
            self.assertTrue(mock_run.called)

        with patch("subprocess.run", side_effect=Exception("Subprocess failure")):
            bypass.disable_defender()

    def test_av_killer_all_methods(self):
        killer = avk.AVKiller()
        mock_p1 = MagicMock()
        mock_p1.info = {"pid": 201, "name": "MsMpEng.exe"}
        mock_p1.kill.side_effect = Exception("Access Denied")

        with patch("psutil.process_iter", return_value=[mock_p1]):
            killed = killer.scan_and_kill()
            self.assertEqual(killed, [])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            killer.kill_process("MsMpEng.exe")
            self.assertTrue(mock_run.called)


if __name__ == "__main__":
    unittest.main()
