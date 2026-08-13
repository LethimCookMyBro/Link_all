import base64
import hashlib
import json
import os
import socket
import sys

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import client.agent_config as agent_config
from client.agent_config import (
    AgentConfig,
    DeviceCredential,
    DpapiCredentialStore,
    load_config,
    validate_private_file,
    write_identity,
)


class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        assert data.startswith(b"protected:")
        return data[len(b"protected:") :][::-1]


def valid_config(tmp_path):
    return {
        "controller_host": "127.0.0.1",
        "managed_port": 5443,
        "enrollment_port": 5444,
        "tls_cert_sha256": "ab" * 32,
        "io_poll_interval": 1.0,
        "controller_ping_interval": 30.0,
        "controller_pong_timeout": 10.0,
        "agent_read_deadline": 90.0,
        "connect_timeout": 5.0,
        "retry_base": 1.0,
        "retry_max": 30.0,
        "retry_jitter": 0.2,
        "log_path": str(tmp_path / "agent.log"),
        "log_max_bytes": 1048576,
        "log_backup_count": 5,
    }


def test_rejects_poll_interval_over_one(tmp_path):
    data = valid_config(tmp_path)
    data["io_poll_interval"] = 1.01
    with pytest.raises(ValueError, match="io_poll_interval"):
        AgentConfig.from_mapping(data)


def test_rejects_short_read_deadline(tmp_path):
    data = valid_config(tmp_path)
    data["agent_read_deadline"] = 89
    with pytest.raises(ValueError, match="agent_read_deadline"):
        AgentConfig.from_mapping(data)


def test_credential_store_round_trip(tmp_path):
    protector = FakeProtector()
    store = DpapiCredentialStore(tmp_path / "credential.bin", protector)
    expected = DeviceCredential("agent-1", "key-1", b"x" * 32)
    store.save(expected)
    assert store.load() == expected
    assert b"x" * 32 not in (tmp_path / "credential.bin").read_bytes()


def test_config_rejects_world_writable_acl(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")
    with pytest.raises(ValueError, match="ACL"):
        load_config(path, acl_inspector=lambda _: {"owner": True, "world_write": True})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("controller_host", ""),
        ("managed_port", 0),
        ("managed_port", 65536),
        ("enrollment_port", 0),
        ("tls_cert_sha256", "AB" * 32),
        ("tls_cert_sha256", "ab" * 31),
        ("connect_timeout", 0),
        ("io_poll_interval", 0),
        ("controller_ping_interval", 0),
        ("controller_pong_timeout", 0),
        ("agent_read_deadline", 0),
        ("retry_base", 0),
        ("retry_max", 0.5),
        ("retry_jitter", -0.01),
        ("retry_jitter", 1.01),
    ],
)
def test_rejects_invalid_config_values(tmp_path, field, value):
    data = valid_config(tmp_path)
    data[field] = value
    with pytest.raises(ValueError, match=field):
        AgentConfig.from_mapping(data)


def test_config_is_immutable(tmp_path):
    config = AgentConfig.from_mapping(valid_config(tmp_path))
    with pytest.raises(FrozenInstanceError):
        config.managed_port = 1


@pytest.mark.parametrize(
    "acl",
    [
        {"owner": False},
        {"owner": True, "everyone_write": True},
        {"owner": True, "builtin_users_write": True},
        {"owner": True, "authenticated_users_write": True},
    ],
)
def test_rejects_non_private_acl(tmp_path, acl):
    path = tmp_path / "agent.json"
    path.write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="ACL"):
        validate_private_file(path, acl_inspector=lambda _: acl)


def test_loads_valid_private_config(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")
    assert load_config(path, acl_inspector=lambda _: {"owner": True}) == AgentConfig.from_mapping(
        valid_config(tmp_path)
    )


def test_rejects_non_file_before_acl_inspection(tmp_path):
    inspected = False

    def inspect(_):
        nonlocal inspected
        inspected = True
        return {"owner": True}

    with pytest.raises(ValueError, match="regular file"):
        validate_private_file(tmp_path, inspect)
    assert not inspected


def test_credential_store_applies_acl_and_deletes(tmp_path):
    applied = []
    path = tmp_path / "credential.bin"
    store = DpapiCredentialStore(path, FakeProtector(), acl_applier=applied.append)
    store.save(DeviceCredential("agent-1", "key-1", b"secret"))
    assert applied[-1] == path
    store.delete()
    assert store.load() is None


def test_write_identity_is_atomic_and_private(tmp_path):
    applied = []
    path = tmp_path / "identity.json"
    write_identity(path, "agent-1", "key-1", acl_applier=applied.append)
    assert json.loads(path.read_text("utf-8")) == {"agent_id": "agent-1", "key_id": "key-1"}
    assert applied[-1] == path


@pytest.mark.skipif(sys.platform != "win32", reason="requires Current User DPAPI")
def test_production_dpapi_and_acl_round_trip(tmp_path):
    store = DpapiCredentialStore(tmp_path / "credential.bin")
    expected = DeviceCredential("agent-1", "key-1", b"current-user-secret")
    store.save(expected)
    assert store.load() == expected


def fake_windows_modules(ace_type, ace_sid):
    class FakeDacl:
        def GetAceCount(self):
            return 1

        def GetAce(self, _):
            return ((ace_type, 0), 0x2, ace_sid, b"callback condition")

    class FakeDescriptor:
        def GetSecurityDescriptorOwner(self):
            return "current-user"

        def GetSecurityDescriptorDacl(self):
            return FakeDacl()

    security = SimpleNamespace(
        SE_FILE_OBJECT=1,
        OWNER_SECURITY_INFORMATION=1,
        DACL_SECURITY_INFORMATION=2,
        WinWorldSid=1,
        WinBuiltinUsersSid=2,
        WinAuthenticatedUserSid=3,
        TokenUser=4,
        ACCESS_ALLOWED_ACE_TYPE=0,
        ACCESS_ALLOWED_OBJECT_ACE_TYPE=5,
        GetNamedSecurityInfo=lambda *_: FakeDescriptor(),
        GetSecurityInfo=lambda *_: FakeDescriptor(),
        CreateWellKnownSid=lambda sid, _: {1: "everyone", 2: "users", 3: "authenticated"}[sid],
        OpenProcessToken=lambda *_: object(),
        GetTokenInformation=lambda *_: ("current-user", None),
    )
    ntsecuritycon = SimpleNamespace(
        FILE_WRITE_DATA=0x2,
        FILE_APPEND_DATA=0x4,
        FILE_WRITE_EA=0x10,
        FILE_WRITE_ATTRIBUTES=0x100,
        DELETE=0x10000,
        WRITE_DAC=0x40000,
        WRITE_OWNER=0x80000,
        ACCESS_ALLOWED_ACE_TYPE=0,
        ACCESS_ALLOWED_CALLBACK_ACE_TYPE=9,
        ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE=11,
        ACCESS_ALLOWED_COMPOUND_ACE_TYPE=4,
        ACCESS_ALLOWED_OBJECT_ACE_TYPE=5,
        ACCESS_DENIED_ACE_TYPE=1,
        ACCESS_DENIED_CALLBACK_ACE_TYPE=10,
        ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE=12,
        ACCESS_DENIED_OBJECT_ACE_TYPE=6,
        SYSTEM_ALARM_ACE_TYPE=3,
        SYSTEM_ALARM_CALLBACK_ACE_TYPE=14,
        SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE=16,
        SYSTEM_ALARM_OBJECT_ACE_TYPE=8,
        SYSTEM_AUDIT_ACE_TYPE=2,
        SYSTEM_AUDIT_CALLBACK_ACE_TYPE=13,
        SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE=15,
        SYSTEM_AUDIT_OBJECT_ACE_TYPE=7,
        SYSTEM_MANDATORY_LABEL_ACE_TYPE=17,
    )
    win32api = SimpleNamespace(GetCurrentProcess=lambda: object())
    win32con = SimpleNamespace(TOKEN_QUERY=1, GENERIC_WRITE=0x40000000, GENERIC_ALL=0x10000000)
    return ntsecuritycon, win32api, win32con, object(), security


@pytest.mark.parametrize(
    ("ace_type", "ace_sid", "result_key"),
    [
        (9, "everyone", "everyone_write"),
        (11, "everyone", "everyone_write"),
        (99, "current-user", "unknown_allow_write"),
    ],
)
def test_write_capable_callback_and_unknown_aces_fail_closed(
    monkeypatch, ace_type, ace_sid, result_key
):
    monkeypatch.setattr(
        agent_config,
        "_windows_modules",
        lambda: fake_windows_modules(ace_type, ace_sid),
    )
    assert agent_config._inspect_windows_acl(Path(os.devnull))[result_key] is True


def test_callback_object_ace_is_rejected_end_to_end(monkeypatch, tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")
    monkeypatch.setattr(
        agent_config,
        "_windows_modules",
        lambda: fake_windows_modules(11, "everyone"),
    )

    with pytest.raises(ValueError, match="ACL"):
        load_config(path, acl_inspector=lambda _: agent_config._inspect_windows_acl(path))


def test_load_config_inspects_open_handle_not_aba_path(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")

    def aba_inspector(target):
        # A pathname query can observe a transient safe B after A is opened and
        # before A is restored; only inspecting the open A handle is binding.
        if hasattr(target, "fileno"):
            return {"owner": True, "world_write": True}
        return {"owner": True}

    with pytest.raises(ValueError, match="ACL"):
        load_config(path, acl_inspector=aba_inspector)


def test_credential_load_inspects_open_handle(tmp_path):
    path = tmp_path / "credential.bin"
    DpapiCredentialStore(path, FakeProtector()).save(
        DeviceCredential("agent-1", "key-1", b"secret")
    )
    inspected = []

    def inspect(target):
        inspected.append(hasattr(target, "fileno"))
        return {"owner": True}

    assert DpapiCredentialStore(path, FakeProtector(), acl_inspector=inspect).load()
    assert inspected == [True]


def test_load_config_rejects_path_replacement_during_acl_check(tmp_path):
    path = tmp_path / "agent.json"
    replacement = tmp_path / "replacement.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")
    changed = valid_config(tmp_path)
    changed["controller_host"] = "replacement"
    replacement.write_text(json.dumps(changed), "utf-8")

    def replace_during_inspection(_):
        os.replace(replacement, path)
        return {"owner": True}

    with pytest.raises(ValueError, match="changed during ACL validation|unavailable"):
        load_config(path, acl_inspector=replace_during_inspection)


def test_credential_load_rejects_path_replacement_during_acl_check(tmp_path):
    path = tmp_path / "credential.bin"
    replacement = tmp_path / "replacement.bin"
    DpapiCredentialStore(path, FakeProtector()).save(
        DeviceCredential("agent-1", "key-1", b"original")
    )
    DpapiCredentialStore(replacement, FakeProtector()).save(
        DeviceCredential("agent-2", "key-2", b"replacement")
    )

    def replace_during_inspection(_):
        os.replace(replacement, path)
        return {"owner": True}

    store = DpapiCredentialStore(path, FakeProtector(), acl_inspector=replace_during_inspection)
    with pytest.raises(ValueError, match="changed during ACL validation|unavailable"):
        store.load()


def test_atomic_write_secures_temp_before_replace(monkeypatch, tmp_path):
    path = tmp_path / "identity.json"
    events = []
    real_replace = os.replace

    def apply_acl(candidate):
        events.append(("acl", Path(candidate)))

    def replace(source, destination):
        events.append(("replace", Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(agent_config.os, "replace", replace)
    write_identity(path, "agent-1", "key-1", acl_applier=apply_acl)

    assert events[0][0] == "acl"
    assert events[1] == ("replace", events[0][1], path)
    assert events[2] == ("acl", path)


def test_atomic_write_removes_temp_after_replace_failure(monkeypatch, tmp_path):
    path = tmp_path / "identity.json"
    temporary_paths = []

    def fail_replace(source, _):
        temporary_paths.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(agent_config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_identity(path, "agent-1", "key-1", acl_applier=lambda _: None)

    assert temporary_paths and not temporary_paths[0].exists()


def test_enroll_requires_tty_without_token_file(monkeypatch):
    from client.managed_agent import main

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["enroll"]) == 2


def test_run_without_credential_is_not_retried(tmp_path, monkeypatch, caplog):
    from client import managed_agent
    from client.agent_config import AgentConfig

    config_path = tmp_path / "agent.json"
    config_path.write_text("{}", "utf-8")
    config = AgentConfig.from_mapping(valid_config(tmp_path))

    class EmptyStore:
        def load(self):
            return None

    monkeypatch.setattr(managed_agent, "load_config", lambda _: config)
    monkeypatch.setattr(managed_agent, "DpapiCredentialStore", lambda _: EmptyStore())
    assert managed_agent.main(["run", "--config", str(config_path)]) == 3
    assert "ENROLLMENT_REQUIRED" in caplog.text


def test_token_file_is_consumed_and_deleted_before_enrollment(tmp_path, monkeypatch):
    from client import managed_agent

    token_path = tmp_path / "token.txt"
    token_path.write_text("token-value\n", "utf-8")
    monkeypatch.setattr(managed_agent, "_read_private_file", lambda *_: b"token-value\n")

    assert managed_agent._read_token_file(str(token_path)) == "token-value"
    assert not token_path.exists()


def test_enroll_invalid_config_returns_four(monkeypatch):
    from client import managed_agent

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(managed_agent.getpass, "getpass", lambda _: "token")
    monkeypatch.setattr(managed_agent, "load_config", lambda _: (_ for _ in ()).throw(ValueError()))

    assert managed_agent.main(["enroll"]) == 4


def test_enroll_saves_credential_before_identity(tmp_path, monkeypatch):
    from client import managed_agent
    from client.agent_config import AgentConfig

    events = []

    class Response:
        status = 201

        def read(self, _):
            return json.dumps(
                {"agent_id": "agent", "key_id": "key", "secret": base64.b64encode(b"s" * 32).decode()}
            ).encode()

    class Connection:
        def __init__(self, *_args, **_kwargs):
            self.sock = self

        def connect(self):
            events.append("connect")

        def getpeercert(self, binary_form):
            assert binary_form is True
            return b"certificate"

        def request(self, method, path, body, headers):
            events.append((method, path, json.loads(body), headers["Content-Type"]))

        def getresponse(self):
            return Response()

        def close(self):
            events.append("close")

    class Store:
        path = tmp_path / "credential.bin"

        def save(self, credential):
            events.append(("save", credential))

    monkeypatch.setattr(managed_agent.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(managed_agent, "write_identity", lambda *_: events.append("identity"))
    config = AgentConfig.from_mapping(
        {
            **valid_config(tmp_path),
            "tls_cert_sha256": hashlib.sha256(b"certificate").hexdigest(),
        }
    )

    credential = managed_agent.enroll(config, "token", Store())

    assert credential.secret == b"s" * 32
    assert events == [
        "connect",
        ("POST", "/v1/enroll", {"token": "token"}, "application/json"),
        ("save", credential),
        "identity",
        "close",
    ]


def test_run_returns_five_after_observed_auth_rejection(tmp_path, monkeypatch):
    from client import managed_agent
    from client.agent_config import AgentConfig, DeviceCredential

    config = AgentConfig.from_mapping(valid_config(tmp_path))

    class Store:
        def load(self):
            return DeviceCredential("agent", "key", b"s" * 32)

    class RejectedRuntime:
        def __init__(self, _config, _credential, event_sink):
            self.event_sink = event_sink

        def run(self):
            self.event_sink({"event": "AUTH_REJECTED", "state": "CONNECTING", "attempt": 1})

    monkeypatch.setattr(managed_agent, "load_config", lambda _: config)
    monkeypatch.setattr(managed_agent, "DpapiCredentialStore", lambda _: Store())
    monkeypatch.setattr(managed_agent, "AgentRuntime", RejectedRuntime)

    assert managed_agent.main(["run", "--config", str(tmp_path / "agent.json")]) == 5


def test_ctrl_c_stops_runtime_and_flushes_logging(tmp_path, monkeypatch):
    from client import managed_agent
    from client.agent_config import AgentConfig, DeviceCredential

    config = AgentConfig.from_mapping(valid_config(tmp_path))
    stopped = []
    flushed = []

    class Store:
        def load(self):
            return DeviceCredential("agent", "key", b"s" * 32)

    class InterruptedRuntime:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            raise KeyboardInterrupt

        def stop(self):
            stopped.append(True)

    class Logging:
        def emit(self, _event):
            pass

        def stop(self, timeout):
            flushed.append(timeout)

    monkeypatch.setattr(managed_agent, "load_config", lambda _: config)
    monkeypatch.setattr(managed_agent, "DpapiCredentialStore", lambda _: Store())
    monkeypatch.setattr(managed_agent, "AgentRuntime", InterruptedRuntime)
    monkeypatch.setattr(managed_agent, "start_agent_logging", lambda _: Logging())

    assert managed_agent.main(["run", "--config", str(tmp_path / "agent.json")]) == 0
    assert stopped == [True]
    assert flushed == [1.0]


def test_token_file_uses_same_handle_reader_and_deletes_file(tmp_path, monkeypatch):
    from client import managed_agent

    token_path = tmp_path / "token.txt"
    token_path.write_text("stale", "utf-8")
    calls = []

    def read_validated(path, acl_inspector):
        calls.append((path, acl_inspector))
        return b"fresh-token\n"

    monkeypatch.setattr(managed_agent, "_read_private_file", read_validated)
    assert managed_agent._read_token_file(str(token_path)) == "fresh-token"
    assert calls == [(token_path, None)]
    assert not token_path.exists()


@pytest.mark.parametrize("error", [RuntimeError("dpapi unavailable"), OSError("acl unavailable")])
def test_storage_platform_failure_returns_five(tmp_path, monkeypatch, error):
    from client import managed_agent
    from client.agent_config import AgentConfig

    config = AgentConfig.from_mapping(valid_config(tmp_path))
    monkeypatch.setattr(managed_agent, "load_config", lambda _: config)
    monkeypatch.setattr(
        managed_agent,
        "DpapiCredentialStore",
        lambda _: (_ for _ in ()).throw(error),
    )

    assert managed_agent.main(["run", "--config", str(tmp_path / "agent.json")]) == 5


def test_acl_platform_failure_during_token_read_returns_five(tmp_path, monkeypatch):
    from client import managed_agent

    token_path = tmp_path / "token.txt"
    token_path.write_text("token", "utf-8")
    monkeypatch.setattr(
        managed_agent,
        "_read_private_file",
        lambda *_: (_ for _ in ()).throw(RuntimeError("acl unavailable")),
    )

    assert managed_agent.main(["enroll", "--token-file", str(token_path)]) == 5
    assert not token_path.exists()


def test_enroll_pywin_storage_failure_returns_five(tmp_path, monkeypatch):
    from client import managed_agent
    from client.agent_config import AgentConfig

    class Error(Exception):
        pass

    Error.__module__ = "pywintypes"
    config = AgentConfig.from_mapping(valid_config(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(managed_agent.getpass, "getpass", lambda _: "token")
    monkeypatch.setattr(managed_agent, "load_config", lambda _: config)
    monkeypatch.setattr(
        managed_agent,
        "DpapiCredentialStore",
        lambda _: (_ for _ in ()).throw(Error("dpapi unavailable")),
    )

    assert managed_agent.main(["enroll", "--config", str(tmp_path / "agent.json")]) == 5


def test_managed_certificate_config_defaults_and_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "pc-default")
    config = AgentConfig.from_mapping(valid_config(tmp_path))
    assert config.display_name == "pc-default"
    assert config.agent_version == "2.0"
    assert config.certificate_store_path == "managed-identity.dpapi"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "x" * 129),
        ("display_name", "bad\nname"),
        ("agent_version", ""),
        ("agent_version", "x" * 33),
        ("agent_version", "v\u00e9"),
        ("certificate_store_path", ""),
    ],
)
def test_rejects_invalid_managed_certificate_config(tmp_path, field, value):
    data = valid_config(tmp_path)
    data[field] = value
    with pytest.raises(ValueError, match=field):
        AgentConfig.from_mapping(data)

def test_config_platform_failure_returns_five(monkeypatch):
    from client import managed_agent

    monkeypatch.setattr(
        managed_agent,
        "load_config",
        lambda _: (_ for _ in ()).throw(RuntimeError("ACL API unavailable")),
    )

    assert managed_agent.main(["run", "--config", "agent.json"]) == 5
