import subprocess
import sys
import importlib


_MANAGED_ENV = (
    "PHANTOMLINK_MANAGED_HOST",
    "PHANTOMLINK_MANAGED_DB",
    "PHANTOMLINK_CA_CERT",
    "PHANTOMLINK_CA_KEY",
    "PHANTOMLINK_TLS_CERT",
    "PHANTOMLINK_TLS_KEY",
    "PHANTOMLINK_MANAGED_STORE",
    "PHANTOMLINK_MANAGED_PORT",
    "PHANTOMLINK_ENROLLMENT_PORT",
)


def _run_isolated(code):
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )


def test_config_import_falls_back_when_root_config_is_missing():
    result = _run_isolated(
        "import sys; "
        "sys.modules['config'] = None; "
        "import client.PhantomLink as phantom; "
        "assert hasattr(phantom, 'DISCORD_WEBHOOK'); "
        "assert phantom.DISCORD_WEBHOOK is not None"
    )
    assert result.returncode == 0, result.stderr


def test_transport_import_does_not_import_phantomlink():
    result = _run_isolated(
        "import sys; import client.transport; "
        "assert 'client.PhantomLink' not in sys.modules"
    )
    assert result.returncode == 0, result.stderr


def test_controller_supports_script_import_mode_without_package_shadowing():
    result = _run_isolated(
        "import runpy; runpy.run_path('C2/C2.py', run_name='fixture_script_import')"
    )
    assert result.returncode == 0, result.stderr


def test_missing_transitive_dependency_is_not_mislabeled_as_transport():
    result = _run_isolated(
        "import builtins; "
        "original = builtins.__import__; "
        "builtins.__import__ = lambda name, *a, **k: "
        "(_ for _ in ()).throw(ModuleNotFoundError(\"No module named 'nacl'\", name='nacl')) "
        "if name == 'nacl.exceptions' else original(name, *a, **k); "
        "import client.PhantomLink"
    )
    assert result.returncode != 0
    assert "No module named 'nacl'" in result.stderr
    assert "No module named 'transport'" not in result.stderr


def test_managed_services_require_complete_phase2_configuration(monkeypatch, tmp_path):
    for name in _MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
    paths = {
        "PHANTOMLINK_MANAGED_DB": tmp_path / "managed.db",
        "PHANTOMLINK_CA_CERT": tmp_path / "ca.crt",
        "PHANTOMLINK_TLS_CERT": tmp_path / "server.crt",
        "PHANTOMLINK_TLS_KEY": tmp_path / "server.key",
    }
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
        if name != "PHANTOMLINK_MANAGED_DB":
            path.write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("PHANTOMLINK_MANAGED_HOST", "10.8.0.1")
    monkeypatch.setenv("PHANTOMLINK_MANAGED_STORE", str(tmp_path / "legacy"))
    monkeypatch.delenv("PHANTOMLINK_CA_KEY", raising=False)

    import config

    loaded = importlib.reload(config)
    assert loaded.managed_phase2_enabled() is False
    assert loaded.managed_phase2_configured() is True
    monkeypatch.undo()
    importlib.reload(config)


def test_managed_services_enable_only_with_all_files(monkeypatch, tmp_path):
    for name in _MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PHANTOMLINK_MANAGED_HOST", "10.8.0.1")
    monkeypatch.setenv("PHANTOMLINK_MANAGED_DB", str(tmp_path / "managed.db"))
    monkeypatch.setenv("PHANTOMLINK_MANAGED_STORE", str(tmp_path / "legacy"))
    for name in (
        "PHANTOMLINK_CA_CERT",
        "PHANTOMLINK_CA_KEY",
        "PHANTOMLINK_TLS_CERT",
        "PHANTOMLINK_TLS_KEY",
    ):
        path = tmp_path / name.lower()
        path.write_text("fixture", encoding="utf-8")
        monkeypatch.setenv(name, str(path))

    import config

    loaded = importlib.reload(config)
    assert loaded.managed_phase2_enabled() is True
    (tmp_path / "phantomlink_ca_key").unlink()
    assert loaded.managed_phase2_enabled() is False
    monkeypatch.undo()
    importlib.reload(config)
