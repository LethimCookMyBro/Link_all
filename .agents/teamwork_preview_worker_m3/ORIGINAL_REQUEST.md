## 2026-07-29T19:47:40Z
<USER_REQUEST>
You are teamwork_preview_worker implementing Milestone 3 (R8 in HackChat/HackChat.py & HackChat/HackChat_c.py).
Your working directory is: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m3
Read specifications from: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m3\context.md
Read explorer findings from: g:\for_hack_all\Link_all\.agents\teamwork_preview_explorer_m3\handoff.md

MANDATORY INTEGRITY WARNING: DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task:
1. Import `is_arabic`, `fix_arabic`, `has_bidi_support` from `text.py` and theme colors/fonts from `theme.py` in `HackChat/HackChat.py` and `HackChat/HackChat_c.py` (with try/except fallback for root/local execution).
2. Remove inline function redefinitions of `is_arabic()` and `fix_arabic()` and replace hardcoded colors/fonts with theme constants (R8).
3. Execute syntax checks `python -m py_compile HackChat/HackChat.py HackChat/HackChat_c.py HackChat/text.py HackChat/theme.py`.
4. Run test suite `python -m pytest tests/`.
5. Document all changes, files modified, and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m3\handoff.md`.
6. Send a message to parent orchestrator when complete.
</USER_REQUEST>
