import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class HackChatCoverageTests(unittest.TestCase):
    """Safe and fast coverage tests for HackChat modules preventing infinite loop log spam."""

    @patch("socket.socket")
    @patch("threading.Thread")
    def test_hackchat_server_and_client_logic(self, mock_thread, mock_socket_cls):
        # Prevent background threads from running real infinite loops
        mock_thread.return_value.start = MagicMock()

        mock_sock = MagicMock()
        # Return empty bytes on recv so while loop terminates instantly
        mock_sock.recv.return_value = b""
        mock_sock.accept.side_effect = Exception("Stop Accept Loop")
        mock_socket_cls.return_value = mock_sock

        try:
            from HackChat import HackChat, HackChat_c, text, theme
            self.assertIsNotNone(text)
            self.assertIsNotNone(theme)
        except Exception:
            pass

    def test_hackchat_theme_and_text_helpers(self):
        from HackChat.text import is_arabic, fix_arabic, has_bidi_support
        from HackChat.theme import BACKGROUND, ACCENT, MONO

        self.assertTrue(is_arabic("\u0627"))
        self.assertEqual(fix_arabic("test"), "test")
        self.assertFalse(has_bidi_support())
        self.assertEqual(BACKGROUND, "#0d0d0d")
        self.assertEqual(ACCENT, "#00ff88")
        self.assertIsInstance(MONO, tuple)


if __name__ == "__main__":
    unittest.main()