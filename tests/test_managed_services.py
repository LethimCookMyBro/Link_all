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

from C2.managed_registry import IssuedDeviceCertificate, ManagedRegistry
from C2.managed_services import (
    ManagedServer,
    SessionManager,
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


def test_loopback_requires_explicit_test_flag():
    with pytest.raises(ValueError):
        validate_managed_bind("127.0.0.1")
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

    with pytest.raises(PermissionError, match="certificate"):
        sessions.register(
            enrolled_device.agent_id,
            enrolled_device.certificate_fingerprint,
            enrolled_device.certificate_serial,
            "10.8.0.21",
            FakeConnection(),
        )
    assert sessions.snapshot() == ()


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
