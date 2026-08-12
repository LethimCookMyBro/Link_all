"""
PhantomLink Control Center Launcher
Unified Control Center for C2 Server & Discord Bot.
"""
import sys
import os
import threading
import time

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


def _get_c2_module():
    """Robustly load C2 module whether running as Python script or PyInstaller EXE."""
    try:
        from C2 import C2 as c2_mod
        if hasattr(c2_mod, "main"):
            return c2_mod
    except Exception:
        pass

    try:
        import C2 as c2_mod
        if hasattr(c2_mod, "main"):
            return c2_mod
    except Exception:
        pass

    import importlib.util
    base_dir = getattr(sys, "_MEIPASS", _ROOT_DIR)
    possible_paths = [
        os.path.join(base_dir, "C2", "C2.py"),
        os.path.join(base_dir, "C2.py"),
        os.path.join(_ROOT_DIR, "C2", "C2.py"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("c2_module_dynamic", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

    raise RuntimeError("Could not locate C2 module")


def _run_discord_bot_bg():
    """Runs Discord bot in a background thread"""
    try:
        import discord_bot
        import asyncio
        print("[*] Launching Discord Bot in background thread...")
        asyncio.run(discord_bot.main())
    except Exception as e:
        print(f"[!] Background Discord Bot error: {e}")


def show_menu():
    print("=" * 60)
    print("   PHANTOMLINK CONTROL CENTER")
    print("=" * 60)
    print(" [1] Start C2 Server + Discord Bot (Combined - Recommended)")
    print(" [2] Start C2 Server Only")
    print(" [3] Start Discord Bot Only")
    print(" [4] Exit")
    print("=" * 60)


def _prompt_choice():
    """Show the menu and read the operator's choice (1-4)."""
    show_menu()
    try:
        return input("Select option (1-4): ").strip()
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)


def main():
    choice = None
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower().strip("-")
        mode_map = {
            "combined": "1", "all": "1", "1": "1",
            "server": "2", "c2": "2", "2": "2",
            "bot": "3", "discord": "3", "3": "3"
        }
        choice = mode_map.get(arg)
        if choice is None:
            # Unknown CLI argument (e.g. pytest's argv when this module is
            # imported): fall back to the interactive menu instead of silently
            # launching the C2 server + Discord bot.
            print(f"[*] Unknown argument '{sys.argv[1]}' - showing menu.")
    if choice is None:
        choice = _prompt_choice()

    if choice == "1":
        print("[*] Starting C2 Server + Discord Bot (Combined)...")
        bot_thread = threading.Thread(target=_run_discord_bot_bg, daemon=True)
        bot_thread.start()
        c2_mod = _get_c2_module()
        c2_mod.main()
    elif choice == "2":
        print("[*] Starting C2 Server Only...")
        c2_mod = _get_c2_module()
        c2_mod.main()
    elif choice == "3":
        print("[*] Starting Discord Bot Only...")
        import discord_bot
        import asyncio
        asyncio.run(discord_bot.main())
    else:
        print("[*] Exiting PhantomLink Control Center.")


if __name__ == "__main__":
    main()
