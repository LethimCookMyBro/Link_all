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


class HackChatTextTests(unittest.TestCase):
    def test_detects_arabic_characters(self):
        from HackChat.text import is_arabic

        self.assertTrue(is_arabic("hello \u0645\u0631\u062d\u0628\u0627"))
        self.assertFalse(is_arabic("plain ascii"))

    def test_fix_arabic_keeps_non_arabic_text(self):
        from HackChat.text import fix_arabic

        self.assertEqual("plain ascii", fix_arabic("plain ascii"))


if __name__ == "__main__":
    unittest.main()
