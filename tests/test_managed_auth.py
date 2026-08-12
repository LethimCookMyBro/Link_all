import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from C2.managed_auth import (
    DeviceRegistry,
    EnrollmentService,
    EnrollmentStore,
    build_proof,
    verify_proof,
)


class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        assert data.startswith(b"protected:")
        return data[len(b"protected:") :][::-1]


def test_token_is_consumed_once_and_never_stored_verbatim(tmp_path):
    path = tmp_path / "tokens.json"
    store = EnrollmentStore(path, now=lambda: 1000)

    token = store.issue(ttl_seconds=60)

    assert store.consume(token) is True
    assert store.consume(token) is False
    raw = path.read_text("utf-8")
    assert token not in raw
    assert hashlib.sha256(token.encode("ascii")).hexdigest() in raw


def test_expired_token_is_rejected(tmp_path):
    current_time = [1000]
    store = EnrollmentStore(tmp_path / "tokens.json", now=lambda: current_time[0])
    token = store.issue(ttl_seconds=60)

    current_time[0] = 1060

    assert store.consume(token) is False


def test_concurrent_token_consumers_have_one_winner(tmp_path):
    store = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    token = store.issue(ttl_seconds=60)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(store.consume, [token] * 8))

    assert results.count(True) == 1


def test_auth_proof_is_bound_to_nonce():
    secret = b"s" * 32
    proof = build_proof(secret, 1, "agent", "key", b"nonce-a")

    assert verify_proof(secret, 1, "agent", "key", b"nonce-a", proof)
    assert not verify_proof(secret, 1, "agent", "key", b"nonce-b", proof)


def test_registry_round_trip_is_protected_and_revoke_removes_device(tmp_path):
    path = tmp_path / "devices.bin"
    registry = DeviceRegistry(path, FakeProtector())

    credential = registry.enroll()

    assert len(credential.secret) == 32
    assert registry.get(credential.agent_id, credential.key_id) == credential.secret
    assert credential.secret not in path.read_bytes()
    assert registry.revoke(credential.agent_id, credential.key_id) is True
    assert registry.get(credential.agent_id, credential.key_id) is None


def test_registry_rejects_corrupt_or_wrong_length_secrets(tmp_path):
    path = tmp_path / "devices.bin"
    protector = FakeProtector()
    invalid = json.dumps({"agent": {"key": "eA=="}}).encode("utf-8")
    path.write_bytes(protector.protect(invalid))

    with pytest.raises(ValueError, match="invalid device registry"):
        DeviceRegistry(path, protector).get("agent", "key")


def test_exchange_persists_device_then_consumes_token(tmp_path):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)

    credential = service.exchange(token)

    assert registry.get(credential.agent_id, credential.key_id) == credential.secret
    assert tokens.consume(token) is False


def test_exchange_rejects_invalid_token_without_creating_device(tmp_path):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())

    with pytest.raises(ValueError, match="enrollment token"):
        EnrollmentService(tokens, registry).exchange("not-issued")

    assert not (tmp_path / "devices.bin").exists()


def test_exchange_rolls_back_device_and_burns_token_when_consume_write_fails(
    monkeypatch, tmp_path
):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)
    original_write = tokens._write_unlocked
    original_stage = registry._stage
    enrolled = []
    failed_once = False

    def capture_enrollment():
        credential = original_stage()
        enrolled.append(credential)
        return credential

    def fail_consumed_write(records):
        nonlocal failed_once
        if not failed_once and any(record["consumed"] for record in records.values()):
            failed_once = True
            raise OSError("token write failed")
        original_write(records)

    monkeypatch.setattr(tokens, "_write_unlocked", fail_consumed_write)
    monkeypatch.setattr(registry, "_stage", capture_enrollment)

    with pytest.raises(OSError, match="token write failed"):
        service.exchange(token)

    assert registry.get(enrolled[0].agent_id, enrolled[0].key_id) is None
    assert tokens.consume(token) is False


def test_failed_exchange_blocks_orphan_even_when_registry_rollback_write_fails(
    monkeypatch, tmp_path
):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)
    token_write = tokens._write_unlocked
    registry_write = registry._write_unlocked
    original_stage = registry._stage
    enrolled = []
    device_writes = 0

    def capture_enrollment():
        credential = original_stage()
        enrolled.append(credential)
        return credential

    def fail_consumed_write(records):
        if any(record["consumed"] for record in records.values()):
            raise OSError("token write failed")
        token_write(records)

    def fail_rollback_write(records):
        nonlocal device_writes
        device_writes += 1
        if device_writes == 2:
            raise OSError("rollback write failed")
        registry_write(records)

    monkeypatch.setattr(tokens, "_write_unlocked", fail_consumed_write)
    monkeypatch.setattr(registry, "_write_unlocked", fail_rollback_write)
    monkeypatch.setattr(registry, "_stage", capture_enrollment)

    with pytest.raises(OSError, match="token write failed"):
        service.exchange(token)

    assert registry.get(enrolled[0].agent_id, enrolled[0].key_id) is None
    assert tokens.consume(token) is False
