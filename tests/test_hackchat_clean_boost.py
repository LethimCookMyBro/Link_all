import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import HackChat.text as hc_text
import HackChat.theme as hc_theme


class HackChatCleanBoostTests(unittest.TestCase):
    """Deterministic, zero-hang branch coverage tests for HackChat modules."""

    def test_text_helpers_clean(self):
        self.assertTrue(hc_text.is_arabic("\u0628"))
        self.assertFalse(hc_text.is_arabic("Antigravity"))
        self.assertEqual(hc_text.fix_arabic("Hello"), "Hello")
        self.assertFalse(hc_text.has_bidi_support())

    def test_theme_constants_clean(self):
        self.assertEqual(hc_theme.BACKGROUND, "#0d0d0d")
        self.assertEqual(hc_theme.ACCENT, "#00ff88")
        self.assertIsInstance(hc_theme.MONO, tuple)

    @patch("socket.socket")
    @patch("threading.Thread")
    def test_hackchat_gui_functions_isolated(self, mock_thread, mock_socket_cls):
        mock_thread.return_value.start = MagicMock()
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b""
        mock_socket_cls.return_value = mock_sock

        with patch("tkinter.Tk"), patch("tkinter.StringVar"):
            import HackChat.HackChat as server_mod
            import HackChat.HackChat_c as client_mod

            self.assertTrue(hasattr(server_mod, "send_message"))
            self.assertTrue(hasattr(client_mod, "send_message"))


if __name__ == "__main__":
    unittest.main()
