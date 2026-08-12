# Runbook: Test suite hangs forever (combined_launcher)

- **Symptom**: `python -m pytest tests` never completes (runs >10 minutes). The
  suite appears to hang on `tests/test_combined_launcher.py`.
- **Root cause**: `combined_launcher.main()` reads `sys.argv` and mapped unknown
  arguments to choice `"1"` (`mode_map.get(arg, "1")`). Under pytest,
  `sys.argv[1]` is a pytest node id, which is not a recognized keyword, so the
  choice silently became `"1"` — the patched `builtins.input()` was never
  reached, and test blocks 3–4 (which do not mock `_get_c2_module` /
  `threading.Thread`) launched the **real** C2 server (socket accept loop on
  `0.0.0.0:5000`, API `serve_forever`, and a real Discord gateway connection).
- **Detection**: the suite timing out; or `netstat` showing `0.0.0.0:5000`
  listening while tests run; or Discord gateway threads in a faulthandler dump.
- **Fix**: unknown CLI arguments now fall back to the interactive menu
  (`_prompt_choice`) instead of defaulting to `"1"`. Recognized keywords
  (`--combined`, `--server`, `--bot`, ...) are unchanged.
- **Regression test**: `tests/test_combined_launcher.py`
  (`test_combined_launcher_menu_options`, `test_combined_launcher_cli_arguments`)
  — now genuinely exercise the menu choices and complete in ~1.5s.
