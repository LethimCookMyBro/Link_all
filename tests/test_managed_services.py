import socket
import ssl
import threading
import time
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from C2.managed_registry import IssuedDeviceCertificate, ManagedRegistry, RegistryUnavailable
from C2.managed_services import (
    DeviceActionService,
    DeviceQueryService,
    ManagedServer,
    SessionManager,
    _recv_frame,
    _server_context,
    validate_managed_bind,
)
from client.transport import FrameDecoder, encode_message

NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.shutdown_how = None

    def shutdown(self, how):
        self.shutdown_how = how

    def close(self):
        self.closed = True


class AuditCheckingConnection(FakeConnection):
    def __init__(self, registry, required_action):
        super().__init__()
        self.registry = registry
        self.required_action = required_action

    def close(self):
        assert self.required_action in {
            event.action for event in self.registry.list_audit_events(100)
        }
        super().close()


@pytest.fixture
def registry(tmp_path):
    value = ManagedRegistry(tmp_path / "managed.db", now=lambda: NOW)
    value.initialize()
    return value


@pytest.fixture
def enrolled_device(registry):
    certificate = IssuedDeviceCertificate(
        b"certificate", "aa" * 32, "1001", "2027-08-13T06:00:00Z"
    )
    return registry.consume_token_and_enroll(
        registry.issue_token(), certificate, "pc-01", "2.0", "enrollment", "corr"
    )


def enroll_device(registry, agent_id, display_name, serial):
    certificate = IssuedDeviceCertificate(
        b"certificate",
        f"{serial:064x}",
        str(serial),
        "2027-08-13T06:00:00Z",
    )
    return registry.consume_token_and_enroll(
        registry.issue_token(),
        certificate,
        display_name,
        "2.0",
        "enrollment",
        f"corr-{serial}",
        agent_id=agent_id,
    )


def test_query_derives_all_four_states_and_sorts_deterministically(registry):
    never_connected = enroll_device(
        registry, "11111111-1111-4111-8111-111111111111", "zulu", 1001
    )
    online = enroll_device(
        registry, "22222222-2222-4222-8222-222222222222", "Alpha", 1002
    )
    offline = enroll_device(
        registry, "33333333-3333-4333-8333-333333333333", "alpha", 1003
    )
    revoked = enroll_device(
        registry, "44444444-4444-4444-8444-444444444444", "bravo", 1004
    )
    sessions = SessionManager(registry)
    registry.touch_last_seen(online.agent_id, "10.8.0.21")
    sessions.register(
        online.agent_id,
        online.certificate_fingerprint,
        online.certificate_serial,
        "10.8.0.21",
        FakeConnection(),
    )
    registry.touch_last_seen(offline.agent_id, "10.8.0.22")
    registry.revoke_device(revoked.agent_id, "operator", "retired", "corr-r")

    devices = DeviceQueryService(registry, sessions).list_devices()

    assert [item.agent_id for item in devices] == [
        online.agent_id,
        offline.agent_id,
        revoked.agent_id,
        never_connected.agent_id,
    ]
    assert {item.agent_id: item.state for item in devices} == {
        never_connected.agent_id: "ENROLLED",
        online.agent_id: "ONLINE",
        offline.agent_id: "OFFLINE",
        revoked.agent_id: "REVOKED",
    }


def test_query_get_merges_online_state_without_exposing_session(registry, enrolled_device):
    sessions = SessionManager(registry)
    sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        FakeConnection(),
    )

    detail = DeviceQueryService(registry, sessions).get_device(enrolled_device.agent_id)

    assert detail.state == "ONLINE"
    assert not hasattr(detail, "connection")


@pytest.mark.parametrize("method,args", [
    ("list_devices", ()),
    ("get_device", ("11111111-1111-4111-8111-111111111111",)),
    ("list_audit_events", (1,)),
])
def test_query_labels_registry_read_failures(registry, monkeypatch, method, args):
    sessions = SessionManager(registry)
    target = {
        "list_devices": "list_device_records",
        "get_device": "get_device",
        "list_audit_events": "list_audit_events",
    }[method]
    monkeypatch.setattr(
        registry,
        target,
        lambda *_args: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    with pytest.raises(RegistryUnavailable, match="registry unavailable"):
        getattr(DeviceQueryService(registry, sessions), method)(*args)


@pytest.mark.parametrize("value", ["bad", "11111111-1111-4111-8111-11111111111", 1])
def test_query_rejects_invalid_agent_ids(registry, value):
    with pytest.raises((TypeError, ValueError), match="UUID"):
        DeviceQueryService(registry, SessionManager(registry)).get_device(value)


@pytest.mark.parametrize("limit", [True, 0, 1001, 1.0])
def test_query_rejects_invalid_audit_limits(registry, limit):
    with pytest.raises(ValueError, match="1 through 1000"):
        DeviceQueryService(registry, SessionManager(registry)).list_audit_events(limit)


def test_disconnect_commits_request_before_closing_socket(registry, enrolled_device):
    connection = AuditCheckingConnection(registry, "DISCONNECT_REQUESTED")
    sessions = SessionManager(registry)
    sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )

    result = DeviceActionService(registry, sessions).disconnect(
        enrolled_device.agent_id, "operator", "maintenance"
    )

    assert result.code == "DISCONNECTED"
    assert connection.closed is True
    assert [event.action for event in registry.list_audit_events(3)] == [
        "DISCONNECT_SUCCEEDED",
        "DISCONNECTED",
        "DISCONNECT_REQUESTED",
    ]
    assert len({event.correlation_id for event in registry.list_audit_events(3)}) == 2
    assert registry.list_audit_events(3)[0].correlation_id == result.correlation_id
    assert registry.list_audit_events(3)[2].correlation_id == result.correlation_id


def test_disconnect_audit_failure_leaves_socket_untouched(
    registry, enrolled_device, monkeypatch
):
    connection = FakeConnection()
    sessions = SessionManager(registry)
    sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    original = registry.append_audit

    def fail_request(**kwargs):
        if kwargs["action"] == "DISCONNECT_REQUESTED":
            raise OSError("audit unavailable")
        return original(**kwargs)

    monkeypatch.setattr(registry, "append_audit", fail_request)

    result = DeviceActionService(registry, sessions).disconnect(
        enrolled_device.agent_id, "operator", "maintenance"
    )

    assert result.code == "FAILED"
    assert connection.closed is False
    assert len(sessions.snapshot()) == 1


def test_disconnect_stable_not_found_and_already_offline_codes(registry, enrolled_device):
    actions = DeviceActionService(registry, SessionManager(registry))
    missing = actions.disconnect(
        "99999999-9999-4999-8999-999999999999", "operator", ""
    )
    offline = actions.disconnect(enrolled_device.agent_id, "operator", "")
    missing_revoke = actions.revoke(
        "99999999-9999-4999-8999-999999999999", "operator", "retired"
    )

    assert (missing.code, offline.code, missing_revoke.code) == (
        "NOT_FOUND",
        "ALREADY_OFFLINE",
        "NOT_FOUND",
    )


def test_actions_reject_invalid_agent_ids(registry):
    actions = DeviceActionService(registry, SessionManager(registry))
    with pytest.raises(ValueError, match="UUID"):
        actions.disconnect("bad", "operator", "")
    with pytest.raises(ValueError, match="UUID"):
        actions.revoke("bad", "operator", "retired")


def test_revoke_is_durable_idempotent_and_closes_live_session(registry, enrolled_device):
    connection = FakeConnection()
    sessions = SessionManager(registry)
    sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    actions = DeviceActionService(registry, sessions)

    first = actions.revoke(enrolled_device.agent_id, "operator", "retired")
    second = actions.revoke(enrolled_device.agent_id, "operator", "different")

    assert (first.code, second.code) == ("REVOKED", "ALREADY_REVOKED")
    assert connection.closed is True
    detail = registry.get_device(enrolled_device.agent_id)
    assert detail.revocation_reason == "retired"
    assert registry.is_connection_allowed(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
    ) is False


def test_revoke_wins_connect_race(registry, enrolled_device, monkeypatch):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    check_started = threading.Event()
    release_check = threading.Event()
    original = registry.is_connection_allowed

    def paused_check(*args):
        check_started.set()
        assert release_check.wait(2)
        return original(*args)

    monkeypatch.setattr(registry, "is_connection_allowed", paused_check)
    errors = []

    def connect():
        try:
            sessions.register(
                enrolled_device.agent_id,
                enrolled_device.certificate_fingerprint,
                enrolled_device.certificate_serial,
                "10.8.0.21",
                connection,
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=connect)
    thread.start()
    assert check_started.wait(2)
    results = []

    def revoke():
        results.append(
            DeviceActionService(registry, sessions).revoke(
                enrolled_device.agent_id, "operator", "retired"
            )
        )

    revoke_thread = threading.Thread(target=revoke)
    revoke_thread.start()
    deadline = time.monotonic() + 2
    while registry.get_device(enrolled_device.agent_id).revoked_at is None:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    release_check.set()
    thread.join(2)
    revoke_thread.join(2)

    assert not thread.is_alive() and not revoke_thread.is_alive()
    assert results[0].code == "REVOKED"
    assert sessions.snapshot() == ()
    assert connection.closed is True
    assert len(errors) == 1 and isinstance(errors[0], PermissionError)


@pytest.mark.parametrize(
    "actor,reason",
    [("", "reason"), ("operator\n", "reason"), ("x" * 129, "reason"), ("operator", "x" * 513)],
)
def test_actions_validate_actor_and_reason(registry, enrolled_device, actor, reason):
    actions = DeviceActionService(registry, SessionManager(registry))
    with pytest.raises(ValueError):
        actions.disconnect(enrolled_device.agent_id, actor, reason)
    with pytest.raises(ValueError):
        actions.revoke(enrolled_device.agent_id, actor, reason)


def test_revoke_requires_reason_and_has_no_unrevoke(registry, enrolled_device):
    actions = DeviceActionService(registry, SessionManager(registry))
    with pytest.raises(ValueError):
        actions.revoke(enrolled_device.agent_id, "operator", "")
    assert not hasattr(actions, "unrevoke")


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "255.255.255.255",
        "169.254.1.1",
        "fe80::1",
        "::ffff:169.254.1.1",
        "::ffff:0.0.0.0",
        "::ffff:255.255.255.255",
        "192.0.2.1",
        "2001:db8::1",
        "240.0.0.1",
        "::ffff:192.0.2.1",
        "::ffff:240.0.0.1",
        "8.8.8.8",
        "localhost",
        "vpn.example",
    ],
)
def test_production_bind_rejects_non_exact_private_interface(host):
    with pytest.raises(ValueError, match="exact managed IP"):
        validate_managed_bind(host)


@pytest.mark.parametrize("host", ["10.8.0.1", "172.16.0.1", "192.168.1.1", "100.64.0.1", "fd00::1"])
def test_production_bind_accepts_exact_private_or_shared_vpn_ip(host):
    assert validate_managed_bind(host) == host


@pytest.mark.parametrize(
    "host",
    [True, 0x0A080001, b"\x0a\x08\x00\x01", bytearray(b"\x0a\x08\x00\x01")],
)
def test_production_bind_rejects_non_string_ip_representations(host):
    with pytest.raises(ValueError, match="exact managed IP"):
        validate_managed_bind(host)


def test_mapped_private_and_explicit_test_loopback_are_canonicalized():
    assert validate_managed_bind("::ffff:10.8.0.1") == "::ffff:10.8.0.1"
    assert validate_managed_bind("FD00:0:0:0:0:0:0:1") == "fd00::1"
    assert (
        validate_managed_bind("::ffff:127.0.0.1", allow_loopback=True)
        == "::ffff:127.0.0.1"
    )


def test_loopback_requires_explicit_test_flag():
    for flag in (False, 1, "yes"):
        with pytest.raises(ValueError):
            validate_managed_bind("127.0.0.1", allow_loopback=flag)
    assert validate_managed_bind("127.0.0.1", allow_loopback=True) == "127.0.0.1"
    assert validate_managed_bind("::1", allow_loopback=True) == "::1"
    with pytest.raises(ValueError):
        validate_managed_bind("::ffff:127.0.0.1")


def test_new_session_atomically_replaces_old_and_stale_unregister_cannot_remove_new(
    registry, enrolled_device
):
    sessions = SessionManager(registry)
    old_conn, new_conn = FakeConnection(), FakeConnection()
    old = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        old_conn,
    )
    new = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        new_conn,
    )

    assert old_conn.shutdown_how == socket.SHUT_RDWR
    assert old_conn.closed is True
    assert sessions.unregister(enrolled_device.agent_id, old.session_id, "peer_closed") is False
    assert sessions.snapshot() == (new,)
    assert [event.action for event in registry.list_audit_events(2)] == [
        "SESSION_REPLACED",
        "CONNECTED",
    ]


def test_register_rechecks_durable_certificate_and_rejects_revoked_device(
    registry, enrolled_device
):
    sessions = SessionManager(registry)
    registry.revoke_device(enrolled_device.agent_id, "operator", "retired", "revoke")

    connection = FakeConnection()
    with pytest.raises(PermissionError, match="certificate"):
        sessions.register(
            enrolled_device.agent_id,
            enrolled_device.certificate_fingerprint,
            enrolled_device.certificate_serial,
            "10.8.0.21",
            connection,
        )
    assert sessions.snapshot() == ()
    assert connection.closed


def test_register_audit_failure_rolls_back_and_closes_candidate(
    registry, enrolled_device, monkeypatch
):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    monkeypatch.setattr(
        registry,
        "append_audit",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
    )

    with pytest.raises(OSError, match="audit unavailable"):
        sessions.register(
            enrolled_device.agent_id,
            enrolled_device.certificate_fingerprint,
            enrolled_device.certificate_serial,
            "10.8.0.21",
            connection,
        )

    assert sessions.snapshot() == ()
    assert connection.closed


def test_replacement_audit_failure_preserves_old_and_closes_candidate(
    registry, enrolled_device, monkeypatch
):
    sessions = SessionManager(registry)
    old_connection, candidate = FakeConnection(), FakeConnection()
    old = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        old_connection,
    )
    monkeypatch.setattr(
        registry,
        "append_audit",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
    )

    with pytest.raises(OSError, match="audit unavailable"):
        sessions.register(
            enrolled_device.agent_id,
            enrolled_device.certificate_fingerprint,
            enrolled_device.certificate_serial,
            "10.8.0.22",
            candidate,
        )

    assert sessions.snapshot() == (old,)
    assert not old_connection.closed
    assert candidate.closed


def test_register_durable_check_failure_closes_candidate(
    registry, enrolled_device, monkeypatch
):
    sessions = SessionManager(registry)
    candidate = FakeConnection()
    monkeypatch.setattr(
        registry,
        "is_connection_allowed",
        lambda *_args: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    with pytest.raises(OSError, match="registry unavailable"):
        sessions.register(
            enrolled_device.agent_id,
            enrolled_device.certificate_fingerprint,
            enrolled_device.certificate_serial,
            "10.8.0.21",
            candidate,
        )

    assert sessions.snapshot() == ()
    assert candidate.closed


@pytest.mark.parametrize("failure_point", ["allowed", "touch"])
def test_heartbeat_registry_failure_removes_and_closes_current_session(
    registry, enrolled_device, monkeypatch, failure_point
):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    method = "is_connection_allowed" if failure_point == "allowed" else "touch_last_seen"
    monkeypatch.setattr(
        registry,
        method,
        lambda *_args: (_ for _ in ()).throw(OSError(f"{failure_point} unavailable")),
    )

    with pytest.raises(OSError, match=f"{failure_point} unavailable"):
        sessions.heartbeat(enrolled_device.agent_id, session.session_id)

    assert sessions.snapshot() == ()
    assert connection.closed


def test_heartbeat_updates_memory_and_durable_last_seen(registry, enrolled_device):
    sessions = SessionManager(registry, now=lambda: NOW)
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        FakeConnection(),
    )

    sessions.heartbeat(enrolled_device.agent_id, session.session_id)

    assert sessions.snapshot()[0].last_heartbeat_at == "2026-08-13T06:00:00.000000Z"
    detail = registry.get_device(enrolled_device.agent_id)
    assert detail.last_seen_at == "2026-08-13T06:00:00.000000Z"
    assert detail.last_vpn_ip == "10.8.0.21"


def test_heartbeat_rechecks_revocation_and_closes_session(registry, enrolled_device):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    registry.revoke_device(enrolled_device.agent_id, "operator", "retired", "revoke")

    with pytest.raises(PermissionError, match="certificate"):
        sessions.heartbeat(enrolled_device.agent_id, session.session_id)

    assert sessions.snapshot() == ()
    assert connection.closed


def test_disconnect_and_close_all_shutdown_owned_sockets(registry, enrolled_device):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )

    assert sessions.unregister(enrolled_device.agent_id, session.session_id, "peer_closed")
    assert connection.closed
    assert sessions.snapshot() == ()
    assert registry.list_audit_events(1)[0].action == "DISCONNECTED"


def test_heartbeat_timeout_removes_session_and_audits(registry, enrolled_device):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )

    assert sessions.unregister(
        enrolled_device.agent_id, session.session_id, "HEARTBEAT_TIMEOUT"
    )
    assert sessions.snapshot() == ()
    assert connection.closed
    assert registry.list_audit_events(1)[0].action == "HEARTBEAT_TIMEOUT"


def test_audit_failure_during_unregister_still_closes_and_removes_session(
    registry, enrolled_device, monkeypatch
):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    session = sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    monkeypatch.setattr(
        registry,
        "append_audit",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
    )

    with pytest.raises(OSError, match="audit unavailable"):
        sessions.unregister(enrolled_device.agent_id, session.session_id, "peer_closed")

    assert sessions.snapshot() == ()
    assert connection.closed


def test_disconnect_removes_current_session_without_stale_unregister_window(
    registry, enrolled_device, monkeypatch
):
    sessions = SessionManager(registry)
    connection = FakeConnection()
    sessions.register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        connection,
    )
    monkeypatch.setattr(
        sessions,
        "unregister",
        lambda *_args: pytest.fail("disconnect must remove under its original lock"),
    )

    assert sessions.disconnect(enrolled_device.agent_id)
    assert sessions.snapshot() == ()
    assert connection.closed


def test_session_snapshots_are_frozen(registry, enrolled_device):
    session = SessionManager(registry).register(
        enrolled_device.agent_id,
        enrolled_device.certificate_fingerprint,
        enrolled_device.certificate_serial,
        "10.8.0.21",
        FakeConnection(),
    )
    with pytest.raises(FrozenInstanceError):
        session.peer_ip = "10.8.0.22"


def _tls_material(tmp_path, agent_id="11111111-1111-4111-8111-111111111111"):
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(datetime(2036, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    def leaf(name, usage, san=None):
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(datetime(2027, 8, 13, tzinfo=timezone.utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        )
        if san is not None:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san)]),
                critical=False,
            )
        return key, builder.sign(ca_key, hashes.SHA256())

    server_key, server = leaf("controller", ExtendedKeyUsageOID.SERVER_AUTH)
    client_key, client = leaf(
        "agent",
        ExtendedKeyUsageOID.CLIENT_AUTH,
        f"urn:phantomlink:agent:{agent_id}",
    )
    paths = {}
    for name, value in (("ca", ca), ("server", server), ("client", client)):
        paths[name] = tmp_path / f"{name}.pem"
        paths[name].write_bytes(value.public_bytes(serialization.Encoding.PEM))
    for name, value in (("server_key", server_key), ("client_key", client_key)):
        paths[name] = tmp_path / f"{name}.pem"
        paths[name].write_bytes(
            value.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return paths, client


def test_managed_server_context_requires_client_certificate(tmp_path):
    paths, _ = _tls_material(tmp_path)
    context = _server_context(paths["server"], paths["server_key"], paths["ca"])
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        assert context.options & ssl.OP_NO_COMPRESSION


def test_managed_server_rejects_session_manager_for_different_registry(
    tmp_path, registry
):
    other = ManagedRegistry(tmp_path / "other" / "managed.db", now=lambda: NOW)
    other.initialize()

    with pytest.raises(ValueError, match="same registry"):
        ManagedServer(
            "127.0.0.1",
            0,
            tmp_path / "missing-server.pem",
            tmp_path / "missing-key.pem",
            tmp_path / "missing-ca.pem",
            other,
            SessionManager(registry),
            allow_loopback=True,
        )


def test_server_frame_reader_rejects_queued_extra_frame():
    first_header, first_body = encode_message(b"PONG")
    extra_header, extra_body = encode_message(b"PONG")

    class Connection:
        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return first_header + first_body + extra_header + extra_body

    with pytest.raises(ValueError, match="unexpected managed frame"):
        _recv_frame(Connection(), 1)


def test_managed_server_rejects_saturation_before_second_tls_handshake(tmp_path):
    paths, _ = _tls_material(tmp_path)
    registry = ManagedRegistry(tmp_path / "registry" / "managed.db")
    registry.initialize()
    sessions = SessionManager(registry)
    server = ManagedServer(
        "127.0.0.1",
        0,
        paths["server"],
        paths["server_key"],
        paths["ca"],
        registry,
        sessions,
        allow_loopback=True,
        max_workers=1,
        handshake_timeout=1,
    )
    stop = threading.Event()
    thread = server.start(stop)
    first = socket.create_connection(("127.0.0.1", server.port), timeout=2)
    second = None
    try:
        deadline = time.monotonic() + 2
        while len(server._threads) != 1:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert all(not worker.daemon for worker in server._threads)
        second = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        try:
            assert second.recv(1) == b""
        except ConnectionError:
            pass
        assert len(server._threads) == 1
    finally:
        first.close()
        if second is not None:
            second.close()
        stop.set()
        server.stop()
        thread.join(2)
    assert not thread.is_alive()


def test_certificate_only_mtls_session_starts_with_ping_and_records_pong(tmp_path):
    tls_root = tmp_path / "tls"
    tls_root.mkdir()
    paths, client_certificate = _tls_material(tls_root)
    registry = ManagedRegistry(tmp_path / "registry" / "managed.db")
    registry.initialize()
    enrolled = registry.consume_token_and_enroll(
        registry.issue_token(),
        IssuedDeviceCertificate(
            client_certificate.public_bytes(serialization.Encoding.PEM),
            client_certificate.fingerprint(hashes.SHA256()).hex(),
            str(client_certificate.serial_number),
            "2027-08-13T00:00:00Z",
        ),
        "pc-01",
        "2.0",
        "enrollment",
        "corr",
        agent_id="11111111-1111-4111-8111-111111111111",
    )
    sessions = SessionManager(registry)
    server = ManagedServer(
        "127.0.0.1",
        0,
        paths["server"],
        paths["server_key"],
        paths["ca"],
        registry,
        sessions,
        allow_loopback=True,
        ping_interval=0.05,
        pong_timeout=0.5,
    )
    stop = threading.Event()
    thread = server.start(stop)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=paths["ca"])
    context.load_cert_chain(paths["client"], paths["client_key"])
    connection = context.wrap_socket(
        socket.create_connection(("127.0.0.1", server.port), timeout=2),
        server_hostname="controller",
    )
    try:
        decoder = FrameDecoder(max_size=4)
        frames = []
        while not frames:
            frames.extend(decoder.feed(connection.recv(4)))
        assert frames == [b"PING"]
        header, body = encode_message(b"PONG")
        connection.sendall(header + body)
        deadline = time.monotonic() + 2
        while registry.get_device(enrolled.agent_id).last_seen_at is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert sessions.snapshot()[0].agent_id == enrolled.agent_id
        assert registry.list_audit_events(2)[0].action == "CONNECTED"
    finally:
        connection.close()
        stop.set()
        server.stop()
        thread.join(2)
    assert not thread.is_alive()
