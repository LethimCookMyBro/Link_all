# PhantomLink - Advanced Command & Control (C2) & Remote Administration Framework

> [!IMPORTANT]
> **Educational & Authorized Auditing Disclaimer**
> This repository contains software developed strictly for educational research, authorized penetration testing, and red teaming exercises. 
> Unauthorized deployment on systems without explicit written consent is illegal and violates local and international laws. The author assumes no liability for misuse of this software.

PhantomLink is a Python-based Command and Control (C2) architecture and Remote Administration Tool (RAT). It integrates security auditing features, real-time process monitoring, self-updating agents, persistent shell handling, custom GUI chat tools, and security simulation components (including keyloggers, screenshoters, and file encryption simulators).

---

## Repository Layout

| Directory / File | Description |
| :--- | :--- |
| **[`C2/`](file:///g:/for_hack_all/Link_all/C2)** | Command & Control server (`C2.py`) with support for multiple active sessions, response time statistics, system command broadcasts, and Telegram/Discord webhook logging. |
| **[`client/`](file:///g:/for_hack_all/Link_all/client)** | Client binary sources (`PhantomLink.py`) alongside helper mechanisms for process termination and exemption paths (`av_bypass.py`, `av_killer.py`). |
| **[`anti_phantom/`](file:///g:/for_hack_all/Link_all/anti_phantom)** / **[`Anti-Phantom/`](file:///g:/for_hack_all/Link_all/Anti-Phantom)** | Disinfection scripts (`Anti-Phantom.py` / `remover.py`) designed to clean registry startup entries, disable execution schedules, and restore system settings. |
| **[`HackChat/`](file:///g:/for_hack_all/Link_all/HackChat)** | Tkinter GUI chat module featuring custom text reshaping, right-to-left layout adjustments, and multi-language support. |
| **[`more-tools/`](file:///g:/for_hack_all/Link_all/more-tools)** | Payload directory hosting modular keyloggers, screen capturers, and a Python GUI-based file encryption testing framework (`its_your_ransom.py`). |
| **[`tests/`](file:///g:/for_hack_all/Link_all/tests)** | Automated unit testing suites and verification utilities. |
| **[`Quick Commands.md`](file:///g:/for_hack_all/Link_all/Quick%20Commands.md)** | Reference manual for PhantomLink's non-CMD commands in markdown format. |

---

## Features & Architecture

### 1. Command & Control Server (C2)
*   **Session Management:** Multi-client socket engine utilizing a custom length-prefixed communication protocol.
*   **Latency Monitoring:** Continual performance health tracking with latency measuring, successful execution logging, and connection stability scoring.
*   **Notification Pipes:** Integrated Discord webhook messaging system (`discord_logger`) that pipes notifications for system events and active command logs.
*   **Desktop Alerts:** Integrates with system tray engines (`notify-py`) to prompt notifications to local operator consoles.
*   **Broadcast Control:** Allows operators to target individual terminals or run bulk command sets.

### 2. PhantomLink Client (Agent)
*   **Persistence Handlers:** Runs continuous reconnection threads. Embeds entries in local registry run paths (`Run`) and configures system task scheduler entries.
*   **Modular Payloads:** Launches independent executable keylogging and screenshot modules during execution, communicating back to the central Discord webhook.
*   **Memory Injection:** Capable of loading and running scripts directly in-memory or wrapping shell scripts.
*   **Updater Module:** Terminates the current running binary, pulls the target update from a local server, replaces itself, and spawns the new process instance.
*   **Unprivileged Execution:** Supports execution without administrative privileges, bypassing specific components (UAC bypass, AV killer) and proceeding with core functions.

### 3. Cleanup & Disinfection (Anti-Phantom)
*   **Automated Purge:** Detects and deletes configuration folders, scheduled tasks, and registry configurations. Terminates active executables and restores the system `hosts` lookup file.
*   **Permission Requests:** Requests UAC administrative access during startup to ensure all system paths can be safely restored.
*   **Audit Logging:** Outputs execution results to a local file named `PhantomLink_Removal_Report.txt` to verify state changes.

---

## Quick Command Reference

For a complete description of commands, see [`Quick Commands.md`](file:///g:/for_hack_all/Link_all/Quick%20Commands.md). Below is a summary of standard functions:

| Module | Command | Purpose |
| :--- | :--- | :--- |
| **File Operations** | send / get / harvest | Remote file transfers and folder content extraction. |
| **Media Capture** | screenshot / camera / record | Remote camera snaps, microphone recording, and screen capturing. |
| **Network Tools** | wifi / ip / netscan / worm | Wi-Fi credential recovery, internal subnet scanning, and port checks. |
| **System Controls** | disable task manager / sleep / logoff | Basic control functions and system configuration modifications. |
| **Danger Zone** | selfdestruct | Complete removal of agent processes and workspace configs. |

---

## Usage Instructions

### Running the C2 Listener
1. Install system prerequisites:
   ```bash
   pip install notify-py requests discord.py
   ```
2. Configure the logging channel inside [`C2/C2.py`](file:///g:/for_hack_all/Link_all/C2/C2.py):
   ```python
   DISCORD_WEBHOOK = "YOUR_DISCORD_WEBHOOK_URL"
   ```
3. Run the controller script:
   ```bash
   python C2/C2.py
   ```

### Packing the Client Agent
1. Configure webhook paths in [`client/PhantomLink.py`](file:///g:/for_hack_all/Link_all/client/PhantomLink.py).
2. Generate single binaries using PyInstaller:
   ```bash
   pyinstaller --onefile --noconsole client/PhantomLink.py
   ```

---

## Legal & Ethical Notice
This system must only be used on assets owned by the tester or where explicit permission has been obtained. Attempting to infect systems without authorization is a serious criminal offense.
