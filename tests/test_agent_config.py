import json
import sys

from dataclasses import FrozenInstanceError

import pytest

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
