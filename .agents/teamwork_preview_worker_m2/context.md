# Worker Context: Milestone 2 (R5 & R9 in client/PhantomLink.py, C2/C2.py, discord_bot.py)

## Target Files
- `client/PhantomLink.py`
- `C2/C2.py`
- `discord_bot.py`

## Instructions & Specifications

### R5: Client/Server version synchronization
- In `client/PhantomLink.py` (line 18), update `version = 11.7` to match `C2/C2.py` (`version = 11.7`).

### R9: Discord bot / `/api/command` response flow
- In `C2/C2.py` `C2APIHandler.do_POST` (`/api/command` endpoint):
  - Update command execution so that for each target client, it sets `command_in_progress = True`, sends `CMD:<cmd>` via `_send_message(conn, ...)`, awaits the client output via `_recv_message(conn)` under lock (with appropriate timeout, e.g. 15s), resets `command_in_progress = False` in a `finally` block, decodes the response string, and returns `{"status": "success", "client_id": cid, "output": response_str}` in the JSON results array.
- In `discord_bot.py` (`_send_commands_sync`):
  - Ensure the bot extracts `res.get('output')` from the JSON response and includes the actual command output in the Discord message reply.

### Verification & Testing Requirements
- Run `python -m py_compile client/PhantomLink.py C2/C2.py discord_bot.py` to verify syntax.
- Run `python -m pytest tests/` to confirm existing test suite passes.
- Write details of all changes and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m2\handoff.md`.
