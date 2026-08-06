## 2026-07-29T12:47:39Z
You are teamwork_preview_worker implementing Milestone 1 (R1, R2, R3, R4, R6, R7 in C2/C2.py).
Your working directory is: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m1
Read specifications from: g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m1\context.md
Read explorer findings from: g:\for_hack_all\Link_all\.agents\teamwork_preview_explorer_m1_1\handoff.md, g:\for_hack_all\Link_all\.agents\teamwork_preview_explorer_m1_2\handoff.md, g:\for_hack_all\Link_all\.agents\teamwork_preview_explorer_m1_3\handoff.md

MANDATORY INTEGRITY WARNING: DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task:
1. Apply fixes in `C2/C2.py` for R1 (try...finally reset for command_in_progress), R2 (lock keepalive handler around command_in_progress and socket I/O), R3 (catch (ImportError, ModuleNotFoundError) for dashboard), R4 (SERVER_IP constant and f-strings for SERVER IP placeholders), R6 (verify screener in interactive_commands), R7 (remove 4 duplicate discord_logger calls).
2. Execute syntax check `python -m py_compile C2/C2.py`.
3. Run test suite `python -m pytest tests/`.
4. Document all changes, files modified, and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m1\handoff.md`.
5. Send a message to parent orchestrator when complete.
