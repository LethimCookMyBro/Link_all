import base64
import hashlib
import http.client
import json
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import monotonic
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from C2.managed_auth import (
    DeviceRegistry,
    EnrollmentServer,
    EnrollmentService,
    EnrollmentStore,
    ManagedServer,
    _main,
    build_proof,
    recv_json_frame,
    send_json_frame,
    verify_proof,
)


class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        assert data.startswith(b"protected:")
        return data[len(b"protected:") :][::-1]


@pytest.fixture
def tls_material(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _tls_client(port):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context.wrap_socket(
        socket.create_connection(("127.0.0.1", port), timeout=2),
        server_hostname="localhost",
    )


def _authenticate(conn, credential):
    send_json_frame(
        conn,
        {
            "type": "HELLO",
            "version": 1,
            "agent_id": credential.agent_id,
            "key_id": credential.key_id,
        },
    )
    challenge = recv_json_frame(conn)
    nonce = base64.b64decode(challenge["nonce"], validate=True)
    send_json_frame(
        conn,
        {
            "type": "AUTH_PROOF",
            "proof": base64.b64encode(
                build_proof(
                    credential.secret,
                    1,
                    credential.agent_id,
                    credential.key_id,
                    nonce,
                )
            ).decode("ascii"),
        },
    )
    return recv_json_frame(conn)["type"]


def _recv_raw_frame(conn):
    size = int.from_bytes(conn.recv(4), "big")
    payload = b""
    while len(payload) < size:
        payload += conn.recv(size - len(payload))
    return payload


def _send_raw_frame(conn, payload):
    conn.sendall(len(payload).to_bytes(4, "big") + payload)


def test_token_is_consumed_once_and_never_stored_verbatim(tmp_path):
    path = tmp_path / "tokens.json"
    store = EnrollmentStore(path, now=lambda: 1000)

    token = store.issue(ttl_seconds=60)

    assert store.consume(token) is True
    assert store.consume(token) is False
    raw = path.read_text("utf-8")
    assert token not in raw
    assert hashlib.sha256(token.encode("ascii")).hexdigest() in raw


def test_legacy_token_records_consume_and_upgrade_without_resetting_consumed(tmp_path):
    path = tmp_path / "tokens.json"
    store = EnrollmentStore(path, now=lambda: 1000)
    issued = base64.urlsafe_b64encode(b"i" * 32).rstrip(b"=").decode("ascii")
    consumed = base64.urlsafe_b64encode(b"c" * 32).rstrip(b"=").decode("ascii")
    issued_digest = hashlib.sha256(issued.encode("ascii")).hexdigest()
    consumed_digest = hashlib.sha256(consumed.encode("ascii")).hexdigest()
    store.issue(ttl_seconds=60)  # Establish the production-private file ACL.
    path.write_text(
        json.dumps(
            {
                issued_digest: {"expires_at": 1060, "consumed": False},
                consumed_digest: {"expires_at": 1060, "consumed": True},
            }
        ),
        "utf-8",
    )

    assert store.consume(issued) is True

    upgraded = json.loads(path.read_text("utf-8"))
    assert upgraded[issued_digest] == {
        "expires_at": 1060,
        "consumed": True,
        "pending": False,
    }
    assert upgraded[consumed_digest] == {
        "expires_at": 1060,
        "consumed": True,
        "pending": False,
    }
    assert store.consume(consumed) is False


@pytest.mark.parametrize(
    "record",
    [
        {"expires_at": 1060, "consumed": False, "pending": "false"},
        {"expires_at": 1060, "consumed": False, "unexpected": False},
    ],
)
def test_token_migration_rejects_wrong_present_types_and_unknown_fields(
    tmp_path, record
):
    path = tmp_path / "tokens.json"
    store = EnrollmentStore(path, now=lambda: 1000)
    token = base64.urlsafe_b64encode(b"t" * 32).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    store.issue(ttl_seconds=60)
    path.write_text(json.dumps({digest: record}), "utf-8")

    with pytest.raises(ValueError, match="invalid enrollment store"):
        store.consume(token)


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


def test_legacy_device_records_get_revoke_and_upgrade_remaining_device(tmp_path):
    path = tmp_path / "devices.bin"
    protector = FakeProtector()
    secret_a = b"a" * 32
    secret_b = b"b" * 32
    legacy = {
        "devices": [
            {
                "active": True,
                "agent_id": "legacy-a",
                "key_id": "key-a",
                "secret": base64.b64encode(secret_a).decode("ascii"),
            },
            {
                "active": True,
                "agent_id": "legacy-b",
                "key_id": "key-b",
                "secret": base64.b64encode(secret_b).decode("ascii"),
            },
        ]
    }
    path.write_bytes(protector.protect(json.dumps(legacy).encode("utf-8")))
    registry = DeviceRegistry(path, protector)

    assert registry.get("legacy-a", "key-a") == secret_a
    assert registry.revoke("legacy-a", "key-a") is True

    upgraded = json.loads(protector.unprotect(path.read_bytes()))
    assert upgraded == {
        "devices": [
            {
                "active": True,
                "agent_id": "legacy-b",
                "key_id": "key-b",
                "pending_digest": None,
                "secret": base64.b64encode(secret_b).decode("ascii"),
            }
        ]
    }
    assert registry.get("legacy-b", "key-b") == secret_b


def test_reconciliation_upgrades_legacy_devices_and_drops_inactive_record(tmp_path):
    token_path = tmp_path / "tokens.json"
    registry_path = tmp_path / "devices.bin"
    protector = FakeProtector()
    secret = b"a" * 32
    legacy = {
        "devices": [
            {
                "active": True,
                "agent_id": "active-agent",
                "key_id": "active-key",
                "secret": base64.b64encode(secret).decode("ascii"),
            },
            {
                "active": False,
                "agent_id": "inactive-agent",
                "key_id": "inactive-key",
                "secret": base64.b64encode(b"i" * 32).decode("ascii"),
            },
        ]
    }
    registry_path.write_bytes(protector.protect(json.dumps(legacy).encode("utf-8")))
    registry = DeviceRegistry(registry_path, protector)

    EnrollmentService(
        EnrollmentStore(token_path, now=lambda: 1000), registry
    ).reconcile()

    upgraded = json.loads(protector.unprotect(registry_path.read_bytes()))
    assert upgraded["devices"] == [
        {
            "active": True,
            "agent_id": "active-agent",
            "key_id": "active-key",
            "pending_digest": None,
            "secret": base64.b64encode(secret).decode("ascii"),
        }
    ]
    assert registry.get("active-agent", "active-key") == secret
    assert registry.get("inactive-agent", "inactive-key") is None


def test_registry_rejects_corrupt_or_wrong_length_secrets(tmp_path):
    path = tmp_path / "devices.bin"
    protector = FakeProtector()
    invalid = json.dumps({"agent": {"key": "eA=="}}).encode("utf-8")
    path.write_bytes(protector.protect(invalid))

    with pytest.raises(ValueError, match="invalid device registry"):
        DeviceRegistry(path, protector).get("agent", "key")


@pytest.mark.parametrize(
    "extra",
    [{"pending_digest": 1}, {"unexpected": False}],
)
def test_device_migration_rejects_wrong_present_types_and_unknown_fields(
    tmp_path, extra
):
    path = tmp_path / "devices.bin"
    protector = FakeProtector()
    device = {
        "active": True,
        "agent_id": "agent",
        "key_id": "key",
        "secret": base64.b64encode(b"s" * 32).decode("ascii"),
        **extra,
    }
    path.write_bytes(
        protector.protect(json.dumps({"devices": [device]}).encode("utf-8"))
    )

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


def test_json_frame_helpers_round_trip_and_reject_oversize():
    left, right = socket.socketpair()
    try:
        send_json_frame(left, {"type": "HELLO", "agent_id": "agent"})
        assert recv_json_frame(right) == {"type": "HELLO", "agent_id": "agent"}

        left.sendall((65537).to_bytes(4, "big"))
        with pytest.raises(ValueError, match="frame too large"):
            recv_json_frame(right)
    finally:
        left.close()
        right.close()


def test_managed_handshake_returns_explicit_results(tls_material, tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    credential = registry.enroll()
    cert, key = tls_material
    server = ManagedServer("127.0.0.1", 0, cert, key, registry)
    stop_event = threading.Event()
    thread = threading.Thread(target=server.serve_forever, args=(stop_event,))
    thread.start()
    clients = []
    try:
        good = _tls_client(server.port)
        clients.append(good)
        assert _authenticate(good, credential) == "AUTH_OK"

        bad = _tls_client(server.port)
        clients.append(bad)
        wrong = type(credential)(credential.agent_id, credential.key_id, b"z" * 32)
        assert _authenticate(bad, wrong) == "AUTH_REJECT"
        assert good.version() == "TLSv1.3" or good.version() == "TLSv1.2"
    finally:
        for client in clients:
            client.close()
        stop_event.set()
        server.stop(timeout=2)
        thread.join(2)
    assert not thread.is_alive()


def test_managed_server_sends_ping_and_accepts_pong(tls_material, tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    credential = registry.enroll()
    cert, key = tls_material
    server = ManagedServer(
        "127.0.0.1",
        0,
        cert,
        key,
        registry,
        initial_ping_delay=0.05,
        ping_interval=0.05,
        pong_timeout=0.2,
    )
    stop_event = threading.Event()
    thread = threading.Thread(target=server.serve_forever, args=(stop_event,))
    thread.start()
    client = _tls_client(server.port)
    try:
        assert _authenticate(client, credential) == "AUTH_OK"
        assert _recv_raw_frame(client) == b"PING"
        _send_raw_frame(client, b"PONG")
        assert server.wait_for_heartbeat(credential.agent_id, 0.5)
    finally:
        client.close()
        stop_event.set()
        server.stop(timeout=2)
        thread.join(2)
    assert not thread.is_alive()


def test_managed_server_rejects_wrong_message_fields(tls_material, tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    cert, key = tls_material
    server = ManagedServer("127.0.0.1", 0, cert, key, registry)
    stop_event = threading.Event()
    thread = threading.Thread(target=server.serve_forever, args=(stop_event,))
    thread.start()
    client = _tls_client(server.port)
    try:
        send_json_frame(
            client,
            {
                "type": "HELLO",
                "version": 1,
                "agent_id": "agent",
                "key_id": "key",
                "extra": True,
            },
        )
        assert recv_json_frame(client) == {"type": "AUTH_REJECT"}
    finally:
        client.close()
        stop_event.set()
        server.stop(timeout=2)
        thread.join(2)


def test_managed_stop_closes_idle_authenticated_tls_session(tls_material, tmp_path):
    cert, key = tls_material
    server = ManagedServer(
        "127.0.0.1",
        0,
        cert,
        key,
        DeviceRegistry(tmp_path / "devices.bin", FakeProtector()),
    )
    stop_event = threading.Event()
    thread = threading.Thread(target=server.serve_forever, args=(stop_event,))
    thread.start()
    client = _tls_client(server.port)
    started = monotonic()
    try:
        server.stop(timeout=1)
        thread.join(1)
    finally:
        client.close()
    assert not thread.is_alive()
    assert not any(worker.is_alive() for worker in server._threads)
    assert monotonic() - started < 2


def test_enrollment_https_consumes_token_once(tls_material, tmp_path):
    tokens = EnrollmentStore(tmp_path / "tokens.json", acl_applier=lambda _: None)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    service = EnrollmentService(tokens, registry)
    token = tokens.issue(60)
    cert, key = tls_material
    server = EnrollmentServer("127.0.0.1", 0, cert, key, service)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        body = json.dumps({"token": token})
        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(
            "POST",
            "/v1/enroll",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        enrolled = json.loads(response.read())
        assert response.status == 201
        assert set(enrolled) == {"agent_id", "key_id", "secret"}
        assert len(base64.b64decode(enrolled["secret"], validate=True)) == 32
        connection.close()

        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(
            "POST",
            "/v1/enroll",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 401
        assert response.read() == b'{"error":"invalid enrollment token"}'
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("GET", "/v1/enroll", None, 405),
        ("POST", "/wrong", "{}", 404),
        ("POST", "/v1/enroll", "not-json", 400),
        ("POST", "/v1/enroll", json.dumps({"token": "x", "extra": 1}), 400),
    ],
)
def test_enrollment_https_rejects_unexpected_requests(
    tls_material, tmp_path, method, path, body, status
):
    tokens = EnrollmentStore(tmp_path / "tokens.json", acl_applier=lambda _: None)
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    cert, key = tls_material
    server = EnrollmentServer(
        "127.0.0.1", 0, cert, key, EnrollmentService(tokens, registry)
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(method, path, body=body)
        response = connection.getresponse()
        assert response.status == status
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_enrollment_shutdown_closes_idle_tcp_session(tls_material, tmp_path):
    cert, key = tls_material
    service = EnrollmentService(
        EnrollmentStore(tmp_path / "tokens.json", acl_applier=lambda _: None),
        DeviceRegistry(tmp_path / "devices.bin", FakeProtector()),
    )
    server = EnrollmentServer("127.0.0.1", 0, cert, key, service)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    stalled = socket.create_connection(("127.0.0.1", server.port), timeout=2)
    try:
        deadline = monotonic() + 1
        while not server._server._connections and monotonic() < deadline:
            threading.Event().wait(0.01)
        started = monotonic()
        server.shutdown()
        server.server_close()
        thread.join(2)
    finally:
        stalled.close()
    assert not thread.is_alive()
    assert monotonic() - started < 2


def test_operator_cli_prints_token_once_and_not_found_is_nonzero(tmp_path, capsys):
    store = tmp_path / "store"

    assert _main(["issue-token", "--store", str(store), "--ttl", "60"]) == 0
    token_output = capsys.readouterr().out.strip().splitlines()
    assert len(token_output) == 1
    assert len(base64.urlsafe_b64decode(token_output[0] + "=")) == 32

    assert _main(["list-devices", "--store", str(store)]) == 0
    assert capsys.readouterr().out.strip() == "[]"
    assert (
        _main(
            [
                "revoke",
                "--store",
                str(store),
                "--agent-id",
                "agent-test",
                "--key-id",
                "key-test",
            ]
        )
        == 1
    )
    assert capsys.readouterr().out.strip() == "not found"


def test_controller_keeps_managed_services_disabled_without_certificates(capsys):
    import C2.C2 as controller

    legacy_socket = MagicMock()
    with (
        patch.object(controller, "MANAGED_TLS_CERT", ""),
        patch.object(controller, "MANAGED_TLS_KEY", ""),
        patch.object(controller.socket, "socket", return_value=legacy_socket),
        patch.object(controller.threading, "Thread") as thread_type,
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        controller.main()

    assert capsys.readouterr().out.count("Managed services disabled") == 1
    legacy_socket.bind.assert_called_once_with((controller.HOST, controller.PORT))
    assert thread_type.called


def test_controller_starts_and_cleans_up_managed_services(tls_material, tmp_path):
    import C2.C2 as controller

    cert, key = tls_material
    legacy_socket = MagicMock()
    managed = MagicMock()
    enrollment = MagicMock()
    with (
        patch.object(controller, "MANAGED_TLS_CERT", str(cert)),
        patch.object(controller, "MANAGED_TLS_KEY", str(key)),
        patch.object(controller, "MANAGED_STORE", str(tmp_path / "store")),
        patch.object(controller, "ManagedServer", return_value=managed),
        patch.object(controller, "EnrollmentServer", return_value=enrollment),
        patch.object(controller, "EnrollmentStore"),
        patch.object(controller, "DeviceRegistry"),
        patch.object(controller, "EnrollmentService"),
        patch.object(controller.socket, "socket", return_value=legacy_socket),
        patch.object(controller.threading, "Thread"),
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        controller.main()

    managed.stop.assert_called_once_with(timeout=5)
    enrollment.shutdown.assert_called_once_with()
    enrollment.server_close.assert_called_once_with()
    legacy_socket.close.assert_called_once_with()
