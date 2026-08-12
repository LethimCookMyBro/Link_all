"""Tests for the non-blocking console (C2/console.py) and the dashboard
thread entry point (C2/dashboard.py::start_dashboard).

Constraints honored:
* No real ports, no real sockets, no real webhooks — everything is mocked.
* ``sys.stdin`` is not a TTY under pytest, so ``Console.prompt`` must take
  the plain-``input()`` fallback path — which keeps tests that patch
  ``builtins.input`` working unchanged.
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

if "." not in sys.path:
    sys.path.insert(0, ".")


class ConsolePromptTests(unittest.TestCase):
    def test_non_tty_falls_back_to_plain_input(self):
        """Under pytest (no TTY) prompt() must call builtins.input."""
        from C2.console import Console

        with patch("builtins.input", return_value="list") as mock_input:
            result = Console().prompt("Controller> ")
        self.assertEqual(result, "list")
        mock_input.assert_called_once_with("Controller> ")

    def test_custom_input_fn_wins_over_builtins(self):
        """An explicitly injected input fn is used regardless of TTY."""
        from C2.console import Console

        fake = MagicMock(return_value="quit")
        result = Console(input_fn=fake).prompt("> ")
        self.assertEqual(result, "quit")
        fake.assert_called_once_with("> ")

    def test_interactive_uses_prompt_toolkit(self):
        """With a fake TTY stdin, prompt() must route to prompt_toolkit."""
        from C2.console import Console

        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.console._load_prompt_toolkit", return_value=True):
                with patch("C2.console._pt_prompt", return_value="screenshot") as pt:
                    with patch("C2.console._WordCompleter") as wc:
                        with patch("C2.console._FileHistory") as fh:
                            result = Console(words=["list", "quit"]).prompt("Shell[x]> ")
        self.assertEqual(result, "screenshot")
        pt.assert_called_once()
        wc.assert_called_once()
        fh.assert_called_once()

    def test_interactive_eof_is_swallowed(self):
        """Ctrl-D / Ctrl-C at an interactive prompt must not raise."""
        from C2.console import Console

        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.console._load_prompt_toolkit", return_value=True):
                with patch("C2.console._pt_prompt", side_effect=EOFError):
                    with patch("C2.console._WordCompleter"):
                        with patch("C2.console._FileHistory"):
                            result = Console().prompt("> ")
        self.assertEqual(result, "")

    def test_prompt_toolkit_missing_falls_back(self):
        """If prompt_toolkit is unavailable, fall back to builtins.input."""
        from C2.console import Console

        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.console._load_prompt_toolkit", return_value=False):
                with patch("builtins.input", return_value="back") as mock_input:
                    result = Console().prompt("> ")
        self.assertEqual(result, "back")
        mock_input.assert_called_once_with("> ")

    def test_console_singleton_is_usable(self):
        from C2.console import console
        with patch("builtins.input", return_value="list") as mock_input:
            self.assertEqual(console.prompt("> "), "list")
        mock_input.assert_called_once_with("> ")


class StartDashboardTests(unittest.TestCase):
    def test_headless_is_noop(self):
        """No TTY -> start_dashboard returns immediately, never touches UI."""
        from C2.dashboard import start_dashboard

        fake_cm = MagicMock()
        with patch("sys.stdin.isatty", return_value=False):
            with patch("C2.dashboard.build_app") as build:
                result = start_dashboard(fake_cm, 7000)
        self.assertIsNone(result)
        build.assert_not_called()

    def test_tty_builds_and_runs_app(self):
        """With a TTY, start_dashboard builds the app and runs it."""
        from C2.dashboard import start_dashboard

        fake_cm = MagicMock()
        fake_app = MagicMock()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.dashboard.build_app", return_value=fake_app) as build:
                start_dashboard(fake_cm, 7000)
        build.assert_called_once()
        fake_app.run.assert_called_once()

    def test_tty_crash_is_swallowed(self):
        """A crashing TUI must never kill the C2 shell thread."""
        from C2.dashboard import start_dashboard

        fake_cm = MagicMock()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.dashboard.build_app", side_effect=RuntimeError("ui boom")):
                result = start_dashboard(fake_cm, 7000)
        self.assertIsNone(result)

    def test_never_binds_socket(self):
        """start_dashboard must never open a port (port arg is reserved)."""
        from C2.dashboard import start_dashboard

        fake_cm = MagicMock()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("C2.dashboard.build_app") as build:
                with patch("socket.socket") as sock:
                    start_dashboard(fake_cm, 7000)
        sock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
