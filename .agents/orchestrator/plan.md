# Project Plan: PhantomLink Bug Fixing

## Architecture & Scope
PhantomLink is a multi-component Python application consisting of:
- `C2.py`: Server component handling keepalive, interactive commands, API endpoints, Discord logging, broadcast, etc.
- `PhantomLink.py`: Client agent connecting to C2.
- `Discord_bot.py`: Discord interface using `/api/command` endpoint.
- `HackChat/`: HackChat server (`HackChat.py`), client (`HackChat_c.py`), helper modules (`text.py`, `theme.py`).
- `anti_phantom/`: Removal tool (`remover.py`).
- `tests/`: Existing unit test suite (`test_safe_refactor_helpers.py`, etc.).

## Milestones

| # | Name | Scope | Requirements Covered | Dependencies | Status |
|---|------|-------|----------------------|--------------|--------|
| 1 | C2 Core Sync & Cleanups | Fix command_in_progress reset, keepalive race condition, dashboard import, URL placeholders, screener interactive list, duplicate discord logging | R1, R2, R3, R4, R6, R7 | None | IN_PROGRESS |
| 2 | Client & Bot Sync | Synchronize client/server version, fix/document Discord bot fire-and-forget API command response | R5, R9 | None | PLANNED |
| 3 | HackChat Deduplication | Refactor HackChat.py & HackChat_c.py to import text.py and theme.py | R8 | None | PLANNED |
| 4 | Anti-Phantom Fix | Use _cmdline and _suspicious_cmdline_indicators in kill_suspicious_processes() | R10 | None | PLANNED |

## Acceptance Criteria Checklist
- [ ] R1: `command_in_progress` reset in `finally` block or equivalent across `interact_with_client()`
- [ ] R2: Keepalive handler acquires client lock before socket access
- [ ] R3: `dashboard` import handled cleanly
- [ ] R4: Placeholder `SERVER IP` / `server IP` replaced with configured server variable
- [ ] R5: Client (`PhantomLink.py`) and Server (`C2.py`) version numbers synchronized
- [ ] R6: `screener` added to `interactive_commands` list in `C2.py`
- [ ] R7: Duplicate `discord_logger()` calls removed
- [ ] R8: `HackChat.py` and `HackChat_c.py` import `is_arabic`, `fix_arabic`, colors/fonts from `text.py` and `theme.py`
- [ ] R9: Discord bot / `/api/command` endpoint handled/fixed so command responses flow back
- [ ] R10: `_cmdline` and `_suspicious_cmdline_indicators` used in `kill_suspicious_processes()`
- [ ] Tests: `python -m pytest tests/` passes cleanly
- [ ] Syntax: `python -m py_compile` passes on all modified files
