import socket
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import getpass
import time
import ctypes
import sys
from pathlib import Path

version = 1.0 #22/2/2026

_CURRENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _CURRENT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CURRENT_DIR))

try:
    from HackChat.text import is_arabic, fix_arabic, has_bidi_support
    from HackChat.theme import (
        BACKGROUND, PANEL, CHAT_BACKGROUND, ENTRY_BACKGROUND,
        ACCENT, INCOMING, ERROR, MUTED, SYSTEM, MONO, BOLD
    )
except ImportError:
    from text import is_arabic, fix_arabic, has_bidi_support
    from theme import (
        BACKGROUND, PANEL, CHAT_BACKGROUND, ENTRY_BACKGROUND,
        ACCENT, INCOMING, ERROR, MUTED, SYSTEM, MONO, BOLD
    )

BIDI_AVAILABLE = has_bidi_support()

if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("myapp.chat.client")

HOST = "127.0.0.1"  # Change this to your Chat server IP address
PORT = 8080

username = getpass.getuser()

def on_close_attempt():
    popup = tk.Toplevel(root)
    popup.configure(bg=BACKGROUND)
    popup.resizable(False, False)
    popup.overrideredirect(True)
    popup.grab_set()

    root.update_idletasks()
    rx = root.winfo_x() + root.winfo_width()  // 2
    ry = root.winfo_y() + root.winfo_height() // 2
    pw, ph = 320, 150
    popup.geometry(f"{pw}x{ph}+{rx - pw // 2}+{ry - ph // 2}")

    border = tk.Frame(popup, bg=ACCENT, padx=1, pady=1)
    border.pack(fill="both", expand=True)

    inner2 = tk.Frame(border, bg=BACKGROUND)
    inner2.pack(fill="both", expand=True)

    title_bar2 = tk.Frame(inner2, bg=PANEL, height=28)
    title_bar2.pack(fill="x")
    title_bar2.pack_propagate(False)
    tk.Label(title_bar2, text="Hold on!", fg=ACCENT, bg=PANEL,
             font=BOLD, padx=10).pack(side="left", pady=6)

    tk.Label(inner2, text="You are hacked anyway, don't try to close it\nEven if you close it or the program closes, you are still hacked\nI just wanted to tell you a few words",
             fg="#cccccc", bg=BACKGROUND,
             font=("Consolas", 10), justify="center").pack(expand=True, pady=(12, 8))

    def close_popup():
        popup.grab_release()
        popup.destroy()

    ok_btn = tk.Button(inner2, text="OK", command=close_popup,
                       bg="#1a2e22", fg=ACCENT,
                       activebackground="#1e3a2c", activeforeground=ACCENT,
                       relief="flat", bd=0, font=BOLD,
                       cursor="hand2", width=10)
    ok_btn.pack(pady=(0, 14))
    popup.bind("<Return>", lambda e: close_popup())
    popup.bind("<Escape>", lambda e: close_popup())
    ok_btn.focus_set()

_drag_x = 0
_drag_y = 0

def start_drag(event):
    global _drag_x, _drag_y
    _drag_x = event.x
    _drag_y = event.y

def do_drag(event):
    x = root.winfo_x() + event.x - _drag_x
    y = root.winfo_y() + event.y - _drag_y
    root.geometry(f"+{x}+{y}")

def gui_log(msg, tag=""):
    def _do():
        chat_box.config(state=tk.NORMAL)
        ts = time.strftime("%H:%M")
        full = f"{ts}  {fix_arabic(msg)}\n"
        if is_arabic(msg):
            chat_box.insert(tk.END, full, (tag, "rtl"))
        else:
            chat_box.insert(tk.END, full, (tag, "ltr"))
        chat_box.see(tk.END)
        chat_box.config(state=tk.DISABLED)
    root.after(0, _do)

def set_status(text, color=MUTED):
    root.after(0, lambda: (status_var.set(text), status_lbl.config(fg=color)))

def enable_input():
    entry.config(state=tk.NORMAL)
    send_btn.config(state=tk.NORMAL)
    entry.focus()

def disable_input():
    entry.config(state=tk.DISABLED)
    send_btn.config(state=tk.DISABLED)

def on_key_release(event):
    text = entry.get()
    if is_arabic(text):
        entry.config(justify="right")
    else:
        entry.config(justify="left")

client = None

def receive_messages():
    while True:
        try:
            data = client.recv(1024)
            if not data:
                break
            gui_log(f"[Hacker]: {data.decode()}", "incoming")
        except OSError:
            break

    gui_log("[!] Disconnected from server.", "error")
    set_status("Disconnected — retrying…", ERROR)
    root.after(0, disable_input)
    threading.Thread(target=reconnect_loop, daemon=True).start()


def reconnect_loop():
    global client
    attempt = 1
    while True:
        set_status(f"Reconnecting… (attempt {attempt})", "#ffaa00")
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((HOST, PORT))
            client.send(username.encode())
            gui_log(f"[*] Reconnected to server as {username}", "system")
            set_status(f"Connected  ·  {HOST}:{PORT}", ACCENT)
            root.after(0, enable_input)
            threading.Thread(target=receive_messages, daemon=True).start()
            return
        except (ConnectionRefusedError, OSError):
            try:
                client.close()
            except OSError:
                pass
            attempt += 1
            time.sleep(3)


def connect():
    global client
    set_status("Connecting…", MUTED)
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((HOST, PORT))
        client.send(username.encode())
        gui_log(f"[*] Connected to server as {username}", "system")
        set_status(f"Connected  ·  {HOST}:{PORT}", ACCENT)
        root.after(0, enable_input)
        threading.Thread(target=receive_messages, daemon=True).start()
    except (ConnectionRefusedError, OSError):
        try:
            client.close()
        except OSError:
            pass
        gui_log("[!] Could not reach server. Retrying…", "error")
        threading.Thread(target=reconnect_loop, daemon=True).start()


def send_message(event=None):
    msg = entry.get().strip()
    if not msg:
        return
    try:
        client.send(msg.encode())
        gui_log(f"[You]: {msg}", "outgoing")
        entry.delete(0, tk.END)
        entry.config(justify="left")
    except OSError:
        gui_log("[!] Send failed — not connected.", "error")

def main():
    global root, border, inner, title_bar, close_btn, status_bar, status_var, status_lbl, bottom, entry, send_btn, chat_box
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("540x460+400+200")
    root.configure(bg=BACKGROUND)

    #Outer border
    border = tk.Frame(root, bg=ACCENT, padx=1, pady=1)
    border.pack(fill="both", expand=True)

    inner = tk.Frame(border, bg=BACKGROUND)
    inner.pack(fill="both", expand=True)

    #Title bar
    title_bar = tk.Frame(inner, bg=PANEL, height=32)
    title_bar.pack(fill="x")
    title_bar.pack_propagate(False)

    tk.Label(title_bar, text=f"Chat  ·  |Hacker|", fg=ACCENT, bg=PANEL,
             font=("Consolas", 10, "bold"), padx=10).pack(side="left", pady=6)

    close_btn = tk.Button(title_bar, text="✕", command=on_close_attempt,
                          bg=PANEL, fg=MUTED,
                          activebackground="#3a1a1a", activeforeground=ERROR,
                          relief="flat", bd=0, font=("Consolas", 11, "bold"),
                          cursor="hand2", padx=10)
    close_btn.pack(side="right", fill="y")

    title_bar.bind("<ButtonPress-1>", start_drag)
    title_bar.bind("<B1-Motion>",     do_drag)

    #Status bar
    status_bar = tk.Frame(inner, bg=PANEL, height=24)
    status_bar.pack(fill="x")
    status_bar.pack_propagate(False)
    status_var = tk.StringVar(value="Connecting…")
    status_lbl = tk.Label(status_bar, textvariable=status_var, fg=MUTED, bg=PANEL,
                          font=("Consolas", 8), padx=10)
    status_lbl.pack(side="right", pady=4)

    #Input bar
    bottom = tk.Frame(inner, bg=PANEL, height=45)
    bottom.pack(fill="x", side="bottom")
    bottom.pack_propagate(False)

    entry = tk.Entry(bottom, bg=ENTRY_BACKGROUND, fg="#e0e0e0", insertbackground=ACCENT,
                     font=("Consolas", 11), relief="flat", bd=0, state=tk.DISABLED,
                     justify="left")
    entry.place(x=8, y=8, relwidth=1.0, width=-90, height=29)
    entry.bind("<Return>", send_message)
    entry.bind("<KeyRelease>", on_key_release)

    send_btn = tk.Button(bottom, text="SEND →", bg="#1a2e22", fg=ACCENT,
                         activebackground="#1e3a2c", activeforeground=ACCENT,
                         relief="flat", bd=0, font=BOLD, cursor="hand2",
                         state=tk.DISABLED, command=send_message)
    send_btn.place(relx=1.0, x=-82, y=8, width=74, height=29)

    #Chat box
    chat_box = ScrolledText(inner, bg=CHAT_BACKGROUND, fg="#ccc", font=MONO,
                            state=tk.DISABLED, relief="flat", bd=0,
                            wrap=tk.WORD, padx=8, pady=6)
    chat_box.pack(fill="both", expand=True, padx=6, pady=(4, 4))

    #Color tags
    chat_box.tag_config("system",   foreground=SYSTEM)
    chat_box.tag_config("incoming", foreground=INCOMING)
    chat_box.tag_config("outgoing", foreground=ACCENT)
    chat_box.tag_config("error",    foreground=ERROR)
    chat_box.tag_config("rtl",      justify="right")
    chat_box.tag_config("ltr",      justify="left")

    root.bind("<Alt-F4>", lambda e: on_close_attempt())

    threading.Thread(target=connect, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()