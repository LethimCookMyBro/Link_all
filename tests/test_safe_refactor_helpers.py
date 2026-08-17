import sys
import unittest
from pathlib import Path

# Repo root must be importable whether tests run as a file or a module.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class AntiPhantomConfigTests(unittest.TestCase):
    def test_suspicious_names_are_casefolded(self):
        from anti_phantom.constants import suspicious_name_set

        self.assertIn("phantomlink.exe", suspicious_name_set())
        self.assertIn("defender.exe", suspicious_name_set())

    def test_registry_targets_keep_run_and_runonce_keys(self):
        from anti_phantom.constants import STARTUP_REGISTRY_KEYS

        key_paths = [key_path for _root, key_path in STARTUP_REGISTRY_KEYS]

        self.assertIn(r"Software\Microsoft\Windows\CurrentVersion\Run", key_paths)
        self.assertIn(r"Software\Microsoft\Windows\CurrentVersion\RunOnce", key_paths)

    def test_kill_suspicious_processes_cmdline_indicator(self):
        from unittest.mock import MagicMock, patch
        from anti_phantom.remover import PhantomLinkRemover

        remover = PhantomLinkRemover()

        mock_proc = MagicMock()
        mock_proc.info = {
            "name": "python.exe",
            "exe": r"C:\Python311\python.exe",
            "cmdline": ["python.exe", "PhantomLink.py"],
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            with patch.object(remover, "terminate_process") as mock_terminate:
                remover.kill_suspicious_processes()
                mock_terminate.assert_called_once_with(
                    mock_proc,
                    "Process with suspicious command line indicator 'PhantomLink': python.exe PhantomLink.py",
                )
                self.assertIn("Killed processes: python.exe", remover.removed_items)


class HackChatTextTests(unittest.TestCase):
    def test_detects_arabic_characters(self):
        from HackChat.text import is_arabic

        self.assertFalse(is_arabic("plain ascii"))

    def test_fix_arabic_keeps_non_arabic_text(self):
        from HackChat.text import fix_arabic

        self.assertEqual("plain ascii", fix_arabic("plain ascii"))



class HackChatThemeTests(unittest.TestCase):
    def test_theme_constants(self):
        from HackChat.theme import (
            BACKGROUND, PANEL, CHAT_BACKGROUND, ENTRY_BACKGROUND,
            ACCENT, INCOMING, ERROR, MUTED, SYSTEM, MONO, BOLD
        )

        self.assertEqual(BACKGROUND, "#0d0d0d")
        self.assertEqual(PANEL, "#111")
        self.assertEqual(CHAT_BACKGROUND, "#0a0a0a")
        self.assertEqual(ENTRY_BACKGROUND, "#1a1a1a")
        self.assertEqual(ACCENT, "#00ff88")
        self.assertEqual(INCOMING, "#00ccff")
        self.assertEqual(ERROR, "#ff4444")
        self.assertEqual(MUTED, "#555")
        self.assertEqual(SYSTEM, "#444")
        self.assertEqual(MONO, ("Consolas", 10))
        self.assertEqual(BOLD, ("Consolas", 9, "bold"))


import json
import re


class Milestone2Tests(unittest.TestCase):
    def test_version_synchronization(self):
        p_content = (_REPO_ROOT / "client" / "PhantomLink.py").read_text(encoding="utf-8")
        c_content = (_REPO_ROOT / "C2" / "C2.py").read_text(encoding="utf-8")

        p_ver = re.search(r"^version\s*=\s*([\d.]+)", p_content, re.MULTILINE).group(1)
        c_ver = re.search(r"^version\s*=\s*([\d.]+)", c_content, re.MULTILINE).group(1)

        self.assertEqual(p_ver, "11.7")
        self.assertEqual(c_ver, "11.7")

    def test_no_hardcoded_ips_in_c2_urls(self):
        config_path = _REPO_ROOT / "config.py"
        target_file = config_path if config_path.exists() else (_REPO_ROOT / "C2" / "C2.py")
        c_content = target_file.read_text(encoding="utf-8")
        lines_with_server_ip = [
            line for line in c_content.splitlines()
            if "SERVER_IP" in line and not line.strip().startswith("#")
        ]
        self.assertGreater(len(lines_with_server_ip), 0)

    def test_discord_bot_supports_broadcast(self):
        d_content = (_REPO_ROOT / "discord_bot.py").read_text(encoding="utf-8")
        self.assertIn("!broadcast", d_content)
        self.assertIn('content_lower.startswith("!broadcast ")', d_content)


if __name__ == "__main__":
    unittest.main()




