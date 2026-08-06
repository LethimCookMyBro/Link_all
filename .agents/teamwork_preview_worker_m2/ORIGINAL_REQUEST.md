## 2026-07-29T19:47:40Z
<USER_REQUEST>
You are teamwork_preview_worker implementing Milestone 2 (R5 & R9 in client/PhantomLink.py, C2/C2.py, discord_bot.py).
Your working directory is: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m2
Read specifications from: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m2\context.md
Read explorer findings from: g:\for_hack_all\Link_all\.agents\teamwork_preview_explorer_m2\handoff.md

MANDATORY INTEGRITY WARNING: DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task:
1. Synchronize version = 11.7 in `client/PhantomLink.py` (R5).
2. Update `/api/command` in `C2/C2.py` to await socket command responses under lock and return `'output'` in JSON response, and update `discord_bot.py` to format and return the command output to Discord (R9).
3. Execute syntax checks `python -m py_compile client/PhantomLink.py C2/C2.py discord_bot.py`.
4. Run test suite `python -m pytest tests/`.
5. Document all changes, files modified, and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m2\handoff.md`.
6. Send a message to parent orchestrator when complete.
</USER_REQUEST>
