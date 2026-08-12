import subprocess
import sys


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
