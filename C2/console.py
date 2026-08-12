"""Non-blocking console for the C2 operator.

The original console was a raw ``input()`` call on the main thread, which
cannot coexist with a reactive Textual dashboard in the same terminal: the
TUI needs the screen while ``input()`` blocks on stdin.

``Console.prompt()`` is the single entry point used by the C2 shell:

* **Interactive terminal** (``sys.stdin.isatty()``): prompt_toolkit drives the
  prompt with up/down history (persisted to a per-user history file) and a
  command-word completer. prompt_toolkit is imported lazily so the module
  loads even where it is not installed.
* **Piped / non-TTY stdin** (tests, CI, background): falls back to plain
  ``builtins.input()``. Tests that mock ``builtins.input`` therefore keep
  working unchanged.

The dashboard never reads this console; it polls the in-memory client
snapshot on its own asyncio loop, so the console and the TUI cannot block
each other (see ``dashboard.start_dashboard``).
"""

from __future__ import annotations

import atexit
import os
import sys
from typing import Callable, Iterable, List, Optional

# Prompt history is shared by every operator prompt. Created lazily.
_HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".phantomlink_c2_history")
_MAX_HISTORY = 500

# Lazy prompt_toolkit handles — module-level so tests can patch them.
_pt_prompt = None
_WordCompleter = None
_FileHistory = None


def _load_prompt_toolkit() -> bool:
    """Import prompt_toolkit once; returns False when unavailable."""
    global _pt_prompt, _WordCompleter, _FileHistory
    if _pt_prompt is not None:
        return True
    try:
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.shortcuts import prompt as pt_prompt
    except ImportError:
        return False
    _pt_prompt, _WordCompleter, _FileHistory = pt_prompt, WordCompleter, FileHistory
    return True


class Console:
    """Operator console with interactive niceties and a test-safe fallback."""

    def __init__(
        self,
        input_fn: Optional[Callable[[str], str]] = None,
        words: Optional[Iterable[str]] = None,
        history_file: str = _HISTORY_FILE,
        max_history: int = _MAX_HISTORY,
    ) -> None:
        # Deferred so that ``builtins.input`` is resolved at call time: tests
        # that patch ``builtins.input`` after import keep working.
        self._input_fn = input_fn
        self._words: List[str] = list(words or [])
        self._history_file = history_file
        self._max_history = max_history
        self._history = None  # created lazily (prompt_toolkit may be absent)

    # -- public --------------------------------------------------------------
    def prompt(self, text: str = "") -> str:
        """Ask the operator for input. Thread-safe only in the sense that the
        C2 shell is single-threaded; do not call from the dashboard loop."""
        if not sys.stdin.isatty():
            # Non-TTY: plain input(). Keeps mocked builtins.input working.
            import builtins

            return (self._input_fn or builtins.input)(text)
        if not _load_prompt_toolkit():
            import builtins

            return (self._input_fn or builtins.input)(text)  # plain input

        if self._history is None:
            self._history = _FileHistory(self._history_file)
            self._save_history_on_exit()

        completer = _WordCompleter(self._words, ignore_case=True) if self._words else None
        try:
            return _pt_prompt(
                text,
                history=self._history,
                completer=completer,
            )
        except (EOFError, KeyboardInterrupt):
            return ""  # operator pressed ctrl-c / ctrl-d at an empty prompt

    # -- internals -----------------------------------------------------------
    def _save_history_on_exit(self) -> None:
        # prompt_toolkit persists history on each write; this is a safety net
        # for the case where the process dies without a clean exit.
        atexit.register(self._trim_history)

    def _trim_history(self) -> None:
        try:
            if not _load_prompt_toolkit():
                return
            hist = _FileHistory(self._history_file)
            entries = [e for e in hist.load_history_strings()]
            if len(entries) > self._max_history:
                os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
                with open(self._history_file, "w", encoding="utf-8") as fh:
                    fh.write("".join(entries[-self._max_history:]))
        except Exception:
            pass  # history trimming is best-effort


# Default words shown by the completer (command names). Kept out of the C2
# shell module so the console stays decoupled from the command registry.
DEFAULT_WORDS = [
    "back", "exit", "commands", "list", "connect", "broadcast", "quit",
    "screenshot", "send", "get", "camera", "record", "play", "screenrec",
    "wifi", "ip", "port", "hosts", "netscan", "worm", "ddos", "dnshijack",
    "sniff", "sys", "task", "devices", "clipboard", "browser", "info",
    "creds", "chrome_pass", "sleep", "logoff", "lock", "shutdown", "off",
    "restart", "rotate", "wallpaper", "block", "killav", "mouse", "type",
    "spam", "killmbr", "fakeupdate", "fakelogin", "user", "inject", "alert",
    "kill", "rootkit", "recycle", "ffmpeg", "keylogger", "keylog", "mine",
    "print", "selfdestruct", "update", "disable task manager",
    "enable task manager", "extract", "copy", "cut", "archive", "harvest",
    "hide", "rickroll", "sleep",
]

console = Console(words=DEFAULT_WORDS)
