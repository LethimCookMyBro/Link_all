import os
import sys
import unittest
import winreg
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anti_phantom.remover import PhantomLinkRemover


class RemoverCoverageTests(unittest.TestCase):
    """Deep branch & statement coverage tests for anti_phantom/remover.py."""

    def test_remover_privileges_and_logging(self):
        remover = PhantomLinkRemover()

        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=True):
            self.assertTrue(remover.is_admin())

        with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=False):
            self.assertFalse(remover.is_admin())

        remover.log_action("Test action success", True)
        self.assertIn("Test action success", remover.removed_items)

        remover.log_action("Test action error", False)
        self.assertIn("Test action error", remover.errors)

    def test_remover_kill_suspicious_processes_branches(self):
        remover = PhantomLinkRemover()

        mock_p1 = MagicMock()
        mock_p1.info = {"pid": 101, "name": "phantomlink.exe", "exe": r"C:\Temp\phantomlink.exe", "cmdline": ["phantomlink.exe"]}

        mock_p2 = MagicMock()
        mock_p2.info = {"pid": 102, "name": "normal.exe", "exe": r"C:\Users\Public\MicrosoftUpdate\normal.exe", "cmdline": ["normal.exe"]}

        mock_p3 = MagicMock()
        mock_p3.info = {"pid": 103, "name": "cmd.exe", "exe": r"C:\Windows\System32\cmd.exe", "cmdline": ["cmd.exe", "PhantomLink"]}

        with patch("psutil.process_iter", return_value=[mock_p1, mock_p2, mock_p3]), patch.object(remover, "terminate_process"):
            remover.kill_suspicious_processes()
            self.assertTrue(len(remover.removed_items) > 0)

    def test_remover_registry_and_scheduled_tasks(self):
        remover = PhantomLinkRemover()

        # Startup entries
        with patch("winreg.OpenKey") as mock_open, patch("winreg.EnumValue") as mock_enum, patch("winreg.DeleteValue"):
            mock_enum.side_effect = [
                ("PhantomLink", "C:\\test.exe", winreg.REG_SZ),
                WindowsError()
            ]
            remover.remove_startup_entries()

        # Scheduled tasks
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout='"TaskName","Next Run Time"\n"phantomlink_task","12:00"\n', returncode=0)
            remover.remove_scheduled_tasks()
            self.assertTrue(any("phantomlink_task" in item for item in remover.removed_items))

    def test_remover_file_and_system_cleaning(self):
        remover = PhantomLinkRemover()

        with patch("os.path.exists", return_value=True), patch("shutil.rmtree"), patch("subprocess.run"):
            remover.remove_malicious_files()
            remover.restore_system_settings()

        with patch("os.path.exists", return_value=True), patch("builtins.open", unittest.mock.mock_open(read_data="127.0.0.1 81.10.55.8\n")), patch("shutil.copy2"), patch("subprocess.run"):
            remover.clean_hosts_file()

        with patch("psutil.net_connections") as mock_net:
            conn = MagicMock()
            conn.raddr = MagicMock(ip="81.10.55.8", port=5000)
            mock_net.return_value = [conn]
            remover.check_network_connections()


if __name__ == "__main__":
    unittest.main()
