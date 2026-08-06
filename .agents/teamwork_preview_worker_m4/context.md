# Worker Context: Milestone 4 (R10 in anti_phantom/remover.py)

## Target File
`anti_phantom/remover.py`

## Instructions & Specifications

### R10: Use `_cmdline` and `_suspicious_cmdline_indicators` in `kill_suspicious_processes()`
- In `anti_phantom/remover.py` (`kill_suspicious_processes()`):
  - Rename/use `cmdline = " ".join(proc.info["cmdline"] or [])` and `suspicious_cmdline_indicators = SUSPICIOUS_CMDLINE_INDICATORS`.
  - Add logic to inspect process command line against suspicious indicators:
    ```python
    if cmdline and not terminated:
        for indicator in suspicious_cmdline_indicators:
            if indicator.lower() in cmdline.lower():
                self.terminate_process(
                    proc, f"Process with suspicious command line indicator '{indicator}': {cmdline}"
                )
                killed_processes.append(name)
                terminated = True
                break
    ```
  - Ensure loop control flow avoids duplicate process termination or array entry.

### Verification & Testing Requirements
- Run `python -m py_compile anti_phantom/remover.py anti_phantom/constants.py` to verify syntax.
- Run `python -m pytest tests/` to confirm existing test suite passes.
- Write details of all changes and test results in `g:\for_hack_all\Link_all\.agents\teamwork_preview_worker_m4\handoff.md`.
