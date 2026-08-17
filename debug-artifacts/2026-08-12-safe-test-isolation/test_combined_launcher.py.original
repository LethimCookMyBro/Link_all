import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class CombinedLauncherUnitTests(unittest.TestCase):
    """Unit tests for Control Center combined_launcher.py options."""

    def test_get_c2_module(self):
        import combined_launcher
        mod = combined_launcher._get_c2_module()
        self.assertTrue(hasattr(mod, "main"))

    def test_combined_launcher_menu_options(self):
        import combined_launcher

        mock_c2_mod = MagicMock()

        # Test choice 1 (Combined)
        with patch("builtins.input", return_value="1"):
            with patch("threading.Thread") as mock_thread:
                with patch("combined_launcher._get_c2_module", return_value=mock_c2_mod):
                    combined_launcher.main()
                    self.assertTrue(mock_thread.called)
                    self.assertTrue(mock_c2_mod.main.called)

        # Test choice 2 (C2 Only)
        with patch("builtins.input", return_value="2"):
            with patch("combined_launcher._get_c2_module", return_value=mock_c2_mod):
                combined_launcher.main()
                self.assertTrue(mock_c2_mod.main.called)

        # Test choice 3 (Bot Only)
        with patch("builtins.input", return_value="3"):
            with patch("discord_bot.main") as mock_bot_main:
                with patch("asyncio.run") as mock_async_run:
                    combined_launcher.main()
                    self.assertTrue(mock_async_run.called)

        # Test choice 4 (Exit)
        with patch("builtins.input", return_value="4"):
            combined_launcher.main()

    def test_combined_launcher_cli_arguments(self):
        import combined_launcher

        mock_c2_mod = MagicMock()

        # Test --combined CLI argument
        with patch.object(sys, "argv", ["combined_launcher.py", "--combined"]):
            with patch("threading.Thread") as mock_thread:
                with patch("combined_launcher._get_c2_module", return_value=mock_c2_mod):
                    combined_launcher.main()
                    self.assertTrue(mock_thread.called)
                    self.assertTrue(mock_c2_mod.main.called)


if __name__ == "__main__":
    unittest.main()
