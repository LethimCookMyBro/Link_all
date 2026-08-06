# Milestone 4 (R10) Technical Analysis — Anti-Phantom Process Command-Line Inspection

## Executive Summary

Requirement R10 addresses an issue in `anti_phantom/remover.py` where process command-line indicators are retrieved and assigned to unused variables (`_cmdline` and `_suspicious_cmdline_indicators`), but never checked against running processes. Consequently, malicious or suspicious processes launched via dynamic script wrappers or generic host executables (e.g. `python.exe PhantomLink.py` or custom script launchers) evade process termination if their process binary name or binary directory path does not match static name/path filters.

This document provides the root-cause analysis, exact code evidence, proposed design fix, and verification strategy for Milestone 4 (R10).

---

## 1. Problem Definition & Code Evidence

### 1.1 Root Cause Location
- **File**: `anti_phantom/remover.py`
- **Method**: `PhantomLinkRemover.kill_suspicious_processes()`
- **Lines**: 69-70 (and surrounding process iteration block lines 65-87)

```python
65:         for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
66:             try:
67:                 name = proc.info["name"] or ""
68:                 exe_path = proc.info["exe"]
69:                 _cmdline = " ".join(proc.info["cmdline"] or [])
70:                 _suspicious_cmdline_indicators = SUSPICIOUS_CMDLINE_INDICATORS
71: 
72:                 if name.lower() in self.suspicious_names:
73:                     self.terminate_process(proc, f"Suspicious process: {name}")
74:                     killed_processes.append(name)
75:                     continue
76: 
77:                 if exe_path:
78:                     for sus_path in self.suspicious_paths:
79:                         if sus_path.lower() in exe_path.lower():
80:                             self.terminate_process(
81:                                 proc, f"Process in suspicious location: {exe_path}"
82:                             )
83:                             killed_processes.append(name)
84:                             break
85: 
86:             except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
87:                 continue
```

### 1.2 Observations
1. **Unused Variables**: `_cmdline` and `_suspicious_cmdline_indicators` are assigned at lines 69-70 inside the process iteration loop, but are never referenced downstream.
2. **Missing Inspection Stage**: The process iteration inspects `proc.info["name"]` (line 72) and `proc.info["exe"]` (lines 77-84), but performs no check against `_cmdline`.
3. **Constants Definition**: `SUSPICIOUS_CMDLINE_INDICATORS` is defined in `anti_phantom/constants.py` at line 51:
   ```python
   SUSPICIOUS_CMDLINE_INDICATORS = (
       "PhantomLink",
       "MicrosoftUpdate",
       "defender.exe",
   )
   ```
4. **Control Flow Inconsistency**: If `exe_path` matches, line 84 executes `break` to exit the `for sus_path` loop, but does not `continue` to the next process iteration. If a third check (command line) is appended without control flow adjustment, a process terminated by `exe_path` could potentially be evaluated by the command line check as well.

---

## 2. Proposed Fix Design

### 2.1 Variable Renaming & Inspection Logic
- Rename `_cmdline` to `cmdline` and `_suspicious_cmdline_indicators` to `suspicious_cmdline_indicators`.
- Perform case-insensitive matching (`indicator.lower() in cmdline.lower()`) to ensure robust detection regardless of command-line casing on Windows.
- Update control flow in `exe_path` matching: when a process is terminated via `exe_path`, set a flag (e.g. `terminated = True`) and skip remaining checks with `continue`.
- Append command-line check: if `cmdline` is non-empty and process is active, iterate over `suspicious_cmdline_indicators`. Upon first match, terminate the process, append process identifier to `killed_processes`, and break.

### 2.2 Proposed Code Replacement (`anti_phantom/remover.py`)

```python
    def kill_suspicious_processes(self):
        print("\n[*] Scanning for malicious processes...")
        killed_processes = []

        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = proc.info["name"] or ""
                exe_path = proc.info["exe"]
                cmdline = " ".join(proc.info["cmdline"] or [])
                suspicious_cmdline_indicators = SUSPICIOUS_CMDLINE_INDICATORS

                if name.lower() in self.suspicious_names:
                    self.terminate_process(proc, f"Suspicious process: {name}")
                    killed_processes.append(name)
                    continue

                terminated = False
                if exe_path:
                    for sus_path in self.suspicious_paths:
                        if sus_path.lower() in exe_path.lower():
                            self.terminate_process(
                                proc, f"Process in suspicious location: {exe_path}"
                            )
                            killed_processes.append(name)
                            terminated = True
                            break

                if terminated:
                    continue

                if cmdline:
                    for indicator in suspicious_cmdline_indicators:
                        if indicator.lower() in cmdline.lower():
                            self.terminate_process(
                                proc, f"Suspicious process command line: {cmdline}"
                            )
                            killed_processes.append(name or indicator)
                            break

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if killed_processes:
            self.log_action(f"Killed processes: {', '.join(set(killed_processes))}")
        else:
            self.log_action("No suspicious processes found")
```

---

## 3. Unit Test Design Proposal

To ensure regression safety and complete test coverage for R10, we recommend adding a test case in `tests/test_safe_refactor_helpers.py` (or a dedicated `tests/test_anti_phantom.py`):

```python
from unittest.mock import MagicMock, patch
import unittest

class DummyProcess:
    def __init__(self, pid, name, exe, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "exe": exe, "cmdline": cmdline}

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass

    def is_running(self):
        return False

    def kill(self):
        pass


class AntiPhantomCmdlineTests(unittest.TestCase):
    @patch("anti_phantom.remover.psutil.process_iter")
    @patch.object(PhantomLinkRemover, "terminate_process")
    def test_kill_suspicious_processes_detects_cmdline(self, mock_terminate, mock_process_iter):
        suspicious_proc = DummyProcess(1234, "python.exe", r"C:\Python311\python.exe", ["python.exe", "PhantomLink.py"])
        clean_proc = DummyProcess(5678, "python.exe", r"C:\Python311\python.exe", ["python.exe", "safe_script.py"])

        mock_process_iter.return_value = [suspicious_proc, clean_proc]

        remover = PhantomLinkRemover()
        remover.kill_suspicious_processes()

        mock_terminate.assert_called_once()
        args, _ = mock_terminate.call_args
        self.assertEqual(args[0], suspicious_proc)
        self.assertIn("Suspicious process command line", args[1])
```

---

## 4. Safety & Invalidation Conditions

1. **Non-Destructive Scope**:
   - Standard clean processes (e.g. system binaries, benign python scripts) will not match `SUSPICIOUS_CMDLINE_INDICATORS` (`"PhantomLink"`, `"MicrosoftUpdate"`, `"defender.exe"`).
2. **Error Resilience**:
   - `proc.info["cmdline"]` may return `None` or `[]` if access is denied or process terminates during iteration. `" ".join(proc.info["cmdline"] or [])` handles `None` safely.
   - `psutil.NoSuchProcess`, `psutil.AccessDenied`, and `psutil.ZombieProcess` are handled by the existing `try/except` block.
