# Worker Context: Milestone 3 (R8 in HackChat/HackChat.py & HackChat/HackChat_c.py)

## Target Files
- `HackChat/HackChat.py`
- `HackChat/HackChat_c.py`
- `HackChat/text.py`
- `HackChat/theme.py`

## Instructions & Specifications

### R8: Deduplicate HackChat text and theme modules
- In `HackChat/HackChat.py` and `HackChat/HackChat_c.py`:
  - Add fallback import handling for running either from project root or inside `HackChat/`:
    ```python
    try:
        from HackChat.text import is_arabic, fix_arabic, has_bidi_support
        from HackChat.theme import BACKGROUND, PANEL, CHAT_BACKGROUND, ENTRY_BACKGROUND, ACCENT, INCOMING, ERROR, MUTED, SYSTEM, MONO, BOLD
    except ImportError:
        from text import is_arabic, fix_arabic, has_bidi_support
        from theme import BACKGROUND, PANEL, CHAT_BACKGROUND, ENTRY_BACKGROUND, ACCENT, INCOMING, ERROR, MUTED, SYSTEM, MONO, BOLD
    ```
  - Remove inline definitions of `is_arabic()` and `fix_arabic()` in `HackChat.py` and `HackChat_c.py`.
  - Replace inline font tuple definitions (`MONO`, `BOLD`) and hardcoded color strings (`#0d0d0d`, `#111`, `#0a0a0a`, `#1a1a1a`, `#00ff88`, `#00ccff`, `#ff4444`, `#555`, `#444`) with imported theme constants.

### Verification & Testing Requirements
- Run `python -m py_compile HackChat/HackChat.py HackChat/HackChat_c.py HackChat/text.py HackChat/theme.py` to verify syntax.
- Run `python -m pytest tests/` to confirm existing test suite passes.
- Write details of all changes and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m3\handoff.md`.
