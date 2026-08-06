# Forensic Auditor Context — Integrity Audit

## Target Codebase
`g:\for_hack_all\Link_all`

## Audit Mission
Perform a complete forensic integrity audit across all modified code files:
- `C2/C2.py`
- `client/PhantomLink.py`
- `discord_bot.py`
- `HackChat/HackChat.py`
- `HackChat/HackChat_c.py`
- `anti_phantom/remover.py`
- `tests/test_safe_refactor_helpers.py`

Verify:
1. NO hardcoded test results, expected strings, or fake mock values bypassing real logic.
2. NO facade/dummy implementations.
3. NO cheated verifications.
4. Genuine implementation of all 10 requirements (R1 through R10).
5. Output binary verdict: CLEAN or INTEGRITY VIOLATION.

Write audit report to `g:\for_hack_all\Link_all\.agents\teamwork_preview_auditor_1\handoff.md`.
