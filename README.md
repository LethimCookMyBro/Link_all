# PhantomLink - Advanced Command & Control (C2) & Remote Administration Framework

> [!IMPORTANT]
> **Educational & Authorized Auditing Disclaimer**
> This repository contains software developed strictly for educational research, authorized penetration testing, and red teaming exercises. 
> Unauthorized deployment on systems without explicit written consent is illegal and violates local and international laws. The author assumes no liability for misuse of this software.

PhantomLink is a comprehensive, feature-rich Python-based Command and Control (C2) architecture and Remote Administration Tool (RAT). It integrates multiple stealth evasion techniques, automated antivirus killing, self-updating agents, persistent reverse shells, custom chat systems, and auxiliary security testing payloads (including keyloggers, screenshot tools, and simulated ransomware).

---

## 📁 Repository Layout

| Directory / File | Description |
| :--- | :--- |
| **[`C2/`](file:///g:/for_hack_all/Link_all/C2)** | The Command & Control server (`C2.py`), supporting multiple client connections, telemetry, latency tracking, and remote task dispatch. |
| **[`client/`](file:///g:/for_hack_all/Link_all/client)** | The PhantomLink client implant (`PhantomLink.py`) alongside local bypass tools (`av_bypass.py`, `av_killer.py`). |
| **[`anti_phantom/`](file:///g:/for_hack_all/Link_all/anti_phantom)** / **[`Anti-Phantom/`](file:///g:/for_hack_all/Link_all/Anti-Phantom)** | Disinfection tools (`Anti-Phantom.py` / `remover.py`) to scan, locate, and completely remove PhantomLink registry keys, scheduled tasks, and executables. |
| **[`HackChat/`](file:///g:/for_hack_all/Link_all/HackChat)** | Custom Tkinter graphical chat platform supporting multi-language rendering and custom right-to-left Arabic text reshaping. |
| **[`more-tools/`](file:///g:/for_hack_all/Link_all/more-tools)** | Payload library containing keyloggers, automatic screenshoters, and a GUI ransomware simulator (`its_your_ransom.py`). |
| **[`tests/`](file:///g:/for_hack_all/Link_all/tests)** | Safe testing modules and verification helpers. |
| **[`Quick Commands.txt`](file:///g:/for_hack_all/Link_all/Quick%20Commands.txt)** | Reference manual for PhantomLink's extended commands. |

---

## ⚡ Main Features & Architecture

### 1. Command & Control Server (C2)
*   **Multi-Client Handling:** Asynchronous socket manager with length-prefixed messaging protocol and secure client verification.
*   **Latency & Connection Health Analytics:** Real-time logging of network response times, command success ratios, and overall connection quality score.
*   **Telemetry Integration:** Dispatches instant client online alerts and logs to a **Telegram Bot** (`tel_logger`).
*   **Desktop Notifications:** Utilizes `notify-py` for system-tray notifications on the C2 operator's system.
*   **Group Controls:** Allows command dispatching to single, multiple, or all active bots.

### 2. PhantomLink Client (Agent)
*   **Persistent Reverse Shell:** Continual retry-and-reconnect logic if connection drops.
*   **Code Execution Engine:** Runs native Shell (CMD / PowerShell) commands and custom python scripts directly in memory.
*   **Self-Update Mechanism:** Automated update verification; terminates active processes holding the binary, overwrites itself, and spawns the updated version.
*   **Registry & Scheduler Persistence:** Injects keys into Windows startup registries (Run keys) and Task Scheduler for persistent access.

### 3. Antivirus Evasion (AV Bypass & AV Killer)
*   **Windows Defender Disabler:** Turns off Real-time Monitoring, Behavior Monitoring, IOAV protection, SmartScreen, and sample submissions using administrative PowerShell calls.
*   **Defensive Exclusions:** Automatically adds key folders (e.g. `%APPDATA%\MicrosoftUpdate`) to Windows Defender exclusion paths.
*   **Real-time Process Killer:** Spawns a background thread scanning every 30 seconds to forcefully kill standard AVs (Avast, AVG, Avira, McAfee, Malwarebytes).
*   **Evading Forensics:** Automatically clears system event logs (`Application`, `Security`, `System`, and `PowerShell`) and hides directories.

### 4. Anti-Phantom (Disinfectant/Remover)
*   **System Cleanup:** Performs registry scans to locate malicious Run keys, deletes scheduled tasks, kills matching active processes (`defender.exe`, `client.exe`, `keylogger.exe`), and restores modified local hosts files.
*   **Admin Elevation Request:** Automatically requests UAC elevation to guarantee full system disinfection.
*   **Detailed Reporting:** Generates a structured log file named `PhantomLink_Removal_Report.txt` summarizing all cleanup activities.

### 5. Auxiliary Payload Tools (`more-tools/`)
*   **Spyware (Keylogger & Screenshoter):** Injects background scripts that capture screen activity or keystrokes and deliver text logs directly via Telegram.
*   **Ransomware Simulation (`its_your_ransom.py`):** Uses symmetric `cryptography.fernet` encryption to recursively lock files across user folders (Desktop, Documents, Downloads, etc.) and external drives. Offers an interactive Tkinter GUI lockscreen and decryption mechanism using a registry-backed key storage system.

---

## 🚀 Quick Command Reference

Here is a summary of some custom remote commands supported by PhantomLink (for a complete list, check [`Quick Commands.txt`](file:///g:/for_hack_all/Link_all/Quick%20Commands.txt)):

```
[📁 File Operations]
  send        - Upload file from client to C2 server
  get         - Download files onto the client from C2
  harvest     - Auto-harvest and send specific file types to C2
  
[📷 Media]
  screenshot  - Capture client screen and send back
  camera      - Take a snapshot from the client web camera
  record      - Record microhpone audio
  screenrec   - Record screen video stream
  
[🌐 Network]
  wifi        - Recover saved Wi-Fi passwords on the target
  ip          - Extract public IP details
  worm        - Spread client executable through connected local networks
  ddos        - Launch a simulated denial-of-service attack
  dnshijack   - Modify host lookup files
  
[🧠 System Control]
  killav      - Disable Windows Defender & Windows Firewall
  browser     - Dump saved credentials, session cookies, and usernames
  chrome_pass - Decrypt Google Chrome saved passwords
  killmbr     - Wipe / destroy master boot record (Simulated destruction)
```

---

## 🛠️ Usage Instructions

### Starting the Listener (C2)
1. Ensure dependencies are installed:
   ```bash
   pip install notify-py requests
   ```
2. Configure your Telegram credentials inside [`C2/C2.py`](file:///g:/for_hack_all/Link_all/C2/C2.py):
   ```python
   CHAT_ID = "YOUR_CHAT_ID"
   # Bot token is configured inside sending functions
   ```
3. Start the server:
   ```bash
   python C2/C2.py
   ```

### Building the Client
1. Update `BOT_TOKEN` and `CHAT_ID` inside [`client/PhantomLink.py`](file:///g:/for_hack_all/Link_all/client/PhantomLink.py).
2. Package the client using PyInstaller (optional):
   ```bash
   pyinstaller --onefile --noconsole client/PhantomLink.py
   ```

### Disinfecting a Machine
If a machine was infected during authorized simulation:
1. Run [`Anti-Phantom/Anti-Phantom.py`](file:///g:/for_hack_all/Link_all/Anti-Phantom/Anti-Phantom.py) as Administrator:
   ```bash
   python Anti-Phantom/Anti-Phantom.py
   ```
2. Review the generated `PhantomLink_Removal_Report.txt` file for confirmation.

---

## ⚖️ Legal & Ethical Notice
This system must only be used on assets owned by the tester or where explicit permission has been obtained. Attempting to infect systems without auth is a serious criminal offense. Play safe, test ethically.
