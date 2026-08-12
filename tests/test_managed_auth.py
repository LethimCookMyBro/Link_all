import base64
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


@pytest.mark.parametrize(
    "token",
    [
        None,
        b"a" * 43,
        "a" * 42,
        "a" * 44,
        pytest.param("a" * 10000, id="oversize"),
        "a" * 43 + "=",
        "+" + "a" * 42,
        "/" + "a" * 42,
        "é" * 43,
        base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode("ascii")[:-1] + "h",
    ],
)
def test_token_validation_rejects_noncanonical_or_unbounded_input(tmp_path, token):
    store = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)

    assert store._token_hash(token, invalid=None) is None
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


def test_revoke_remains_inactive_after_restart_when_delete_write_fails(
    monkeypatch, tmp_path
):
    path = tmp_path / "devices.bin"
    protector = FakeProtector()
    registry = DeviceRegistry(path, protector)
    credential = registry.enroll()
    original_write = registry._write_unlocked
    writes = 0

    def fail_delete(records):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("delete failed")
        original_write(records)

    monkeypatch.setattr(registry, "_write_unlocked", fail_delete)

    with pytest.raises(OSError, match="delete failed"):
        registry.revoke(credential.agent_id, credential.key_id)

    restarted = DeviceRegistry(path, protector)
    assert restarted.get(credential.agent_id, credential.key_id) is None


def test_exchange_persists_device_then_consumes_token(tmp_path):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)

    credential = service.exchange(token)

    assert registry.get(credential.agent_id, credential.key_id) == credential.secret
    assert tokens.consume(token) is False


def test_exchange_persists_pending_digest_before_staging(monkeypatch, tmp_path):
    token_path = tmp_path / "tokens.json"
    tokens = EnrollmentStore(token_path, now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    original_stage = registry._stage

    def assert_pending_then_stage(pending_digest):
        records = json.loads(token_path.read_text("utf-8"))
        assert records[digest]["pending"] is True
        return original_stage(pending_digest)

    monkeypatch.setattr(registry, "_stage", assert_pending_then_stage)

    assert service.exchange(token)


def test_exchange_accepts_verified_final_write_after_post_write_error(
    monkeypatch, tmp_path
):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)
    original_finalize = registry._finalize

    def persist_then_fail(credential):
        original_finalize(credential)
        raise OSError("post-write failure")

    monkeypatch.setattr(registry, "_finalize", persist_then_fail)

    credential = service.exchange(token)

    assert registry.get(credential.agent_id, credential.key_id) == credential.secret


def test_concurrent_exchanges_have_one_winner_and_one_active_device(tmp_path):
    tokens = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    protector = FakeProtector()
    registry = DeviceRegistry(tmp_path / "devices.bin", protector)
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)

    def exchange():
        try:
            return service.exchange(token)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: exchange(), range(2)))

    payload = json.loads(protector.unprotect((tmp_path / "devices.bin").read_bytes()))
    assert sum(result is not None for result in results) == 1
    assert sum(device["active"] for device in payload["devices"]) == 1
    assert len(payload["devices"]) == 1


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

    def capture_enrollment(digest):
        credential = original_stage(digest)
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

    def capture_enrollment(digest):
        credential = original_stage(digest)
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


def test_restart_reconciles_pending_exchange_after_token_and_cleanup_write_failures(
    monkeypatch, tmp_path
):
    token_path = tmp_path / "tokens.json"
    registry_path = tmp_path / "devices.bin"
    protector = FakeProtector()
    tokens = EnrollmentStore(token_path, now=lambda: 1000)
    registry = DeviceRegistry(registry_path, protector)
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(ttl_seconds=60)
    token_write = tokens._write_unlocked
    registry_write = registry._write_unlocked
    device_writes = 0

    def fail_consumed_write(records):
        if any(record["consumed"] for record in records.values()):
            raise OSError("token write failed")
        token_write(records)

    def fail_cleanup_write(records):
        nonlocal device_writes
        device_writes += 1
        if device_writes == 2:
            raise OSError("cleanup write failed")
        registry_write(records)

    monkeypatch.setattr(tokens, "_write_unlocked", fail_consumed_write)
    monkeypatch.setattr(registry, "_write_unlocked", fail_cleanup_write)

    with pytest.raises(OSError, match="token write failed"):
        service.exchange(token)

    restarted_tokens = EnrollmentStore(token_path, now=lambda: 1000)
    restarted_registry = DeviceRegistry(registry_path, protector)
    restarted_service = EnrollmentService(restarted_tokens, restarted_registry)

    with pytest.raises(ValueError, match="enrollment token"):
        restarted_service.exchange(token)
    payload = json.loads(protector.unprotect(registry_path.read_bytes()))
    assert payload["devices"] == []
