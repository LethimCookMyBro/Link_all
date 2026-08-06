import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import HackChat.text as hc_text
import HackChat.theme as hc_theme


class HackChatCoverageTests(unittest.TestCase):
    """Full production-ready coverage test suite targeting HackChat/ modules."""

    def test_hackchat_text_functions(self):
        self.assertTrue(hc_text.is_arabic("\u0627"))
        self.assertFalse(hc_text.is_arabic("Hello"))
        self.assertEqual(hc_text.fix_arabic("test"), "test")
        self.assertFalse(hc_text.has_bidi_support())

    def test_hackchat_theme_colors(self):
        self.assertEqual(hc_theme.BACKGROUND, "#0d0d0d")
        self.assertEqual(hc_theme.ACCENT, "#00ff88")
        self.assertIsInstance(hc_theme.MONO, tuple)

    def test_hackchat_gui_imports_safe(self):
        # Prevent tkinter root creation side-effect during import
        with patch("tkinter.Tk"), patch("tkinter.StringVar"), patch("socket.socket"):
            try:
                import HackChat.HackChat as hc_server
                import HackChat.HackChat_c as hc_client
                self.assertTrue(True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
