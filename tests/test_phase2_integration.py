from __future__ import annotations

import ipaddress
import shutil
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from C2.managed_auth import EnrollmentServer, EnrollmentService
from C2.managed_pki import ControllerCertificateAuthority
from C2.managed_registry import ManagedRegistry
from C2.managed_services import (
    DeviceActionService,
    DeviceQueryService,
    ManagedServer,
    SessionManager,
    _recv_frame,
    _send_frame,
)
from client.agent_config import AgentConfig
from client.agent_runtime import AgentRuntime
from client.managed_agent import EnrollmentRejected, enroll
from client.managed_identity import AgentCertificateIdentity, AgentCertificateStore


class _Protector:
    def protect(self, payload: bytes) -> bytes:
        return b"phase2-test:" + payload[::-1]

    def unprotect(self, payload: bytes) -> bytes:
        assert payload.startswith(b"phase2-test:")
        return payload.removeprefix(b"phase2-test:")[::-1]


class _CountingEnrollmentService(EnrollmentService):
    def __init__(self, *args, count_request, **kwargs):
        super().__init__(*args, **kwargs)
        self._count_request = count_request

    def exchange(self, *args, **kwargs):
        self._count_request()
        return super().exchange(*args, **kwargs)


class _AgentHandle:
    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime
        self.events: list[dict] = []
        self.error: RuntimeError | TypeError | ValueError | None = None
        runtime._event_sink = self.events.append
        self.thread = threading.Thread(target=self._run, name="phase2-agent")

    def _run(self) -> None:
        try:
            self.runtime.run()
        except (RuntimeError, TypeError, ValueError) as error:
            self.error = error

    def start(self):
        self.thread.start()
        return self

    def stop(self) -> None:
        self.runtime.stop()

    def join(self, timeout: float = 3.0) -> bool:
        self.thread.join(timeout)
        return not self.thread.is_alive()


class Phase2System:
    def __init__(
        self,
        root: Path,
        *,
        allow_loopback: bool,
        max_workers: int = 8,
        busy_timeout_ms: int = 200,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.protector = _Protector()
        self.db_path = root / "registry" / "managed.db"
        self.ca_key_path = root / "ca-key.dpapi"
        self.ca_path = root / "ca.pem"
        self.server_cert_path = root / "server.pem"
        self.server_key_path = root / "server-key.pem"
        self.identity_stores: dict[str, AgentCertificateStore] = {}
        self.agents: list[_AgentHandle] = []
        self.enrollment_request_count = 0
        self._allow_loopback = allow_loopback
        self._max_workers = max_workers
        self._busy_timeout_ms = busy_timeout_ms
        self._managed_port = 0
        self._enrollment_port = 0
        self._closed = False

        self.authority = ControllerCertificateAuthority(
            self.ca_key_path,
            self.ca_path,
            protector=self.protector,
        )
        self.authority.initialize("PhantomLink Phase 2 Integration CA")
        self._write_server_certificate()
        self._start_controller()
        self.agent = self

    def _write_server_certificate(self) -> None:
        ca_key = serialization.load_pem_private_key(
            self.protector.unprotect(self.ca_key_path.read_bytes()), password=None
        )
        ca_certificate = x509.load_pem_x509_certificate(self.ca_path.read_bytes())
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
            )
            .issuer_name(ca_certificate.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        self.server_cert_path.write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        self.server_key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.server_pin = certificate.fingerprint(hashes.SHA256()).hex()

    def _count_enrollment(self) -> None:
        self.enrollment_request_count += 1

    def _start_controller(self) -> None:
        self.registry = ManagedRegistry(
            self.db_path, busy_timeout_ms=self._busy_timeout_ms
        )
        self.registry.initialize()
        self.sessions = SessionManager(self.registry)
        self.actions = DeviceActionService(self.registry, self.sessions)
        self.queries = DeviceQueryService(self.registry, self.sessions)
        service = _CountingEnrollmentService(
            self.registry, self.authority, count_request=self._count_enrollment
        )
        self.managed = ManagedServer(
            "127.0.0.1",
            self._managed_port,
            self.server_cert_path,
            self.server_key_path,
            self.ca_path,
            self.registry,
            self.sessions,
            allow_loopback=self._allow_loopback,
            max_workers=self._max_workers,
            handshake_timeout=0.5,
            ping_interval=0.05,
            pong_timeout=0.25,
        )
        self._managed_port = self.managed.port
        self.enrollment = EnrollmentServer(
            "127.0.0.1",
            self._enrollment_port,
            self.server_cert_path,
            self.server_key_path,
            service,
            handshake_timeout=0.5,
            max_workers=self._max_workers,
        )
        self._enrollment_port = self.enrollment.port
        self.managed_stop = threading.Event()
        self.managed_thread = self.managed.start(self.managed_stop)
        self.enrollment_thread = threading.Thread(
            target=self.enrollment.serve_forever, name="phase2-enrollment"
        )
        self.enrollment_thread.start()

    def _stop_controller(self) -> None:
        self.managed_stop.set()
        self.managed.stop(2.0)
        self.managed_thread.join(2.0)
        self.enrollment.shutdown()
        self.enrollment.server_close()
        self.enrollment_thread.join(2.0)
        assert not self.managed_thread.is_alive()
        assert not self.enrollment_thread.is_alive()

    def config(self, **changes) -> AgentConfig:
        config = AgentConfig(
            controller_host="127.0.0.1",
            managed_port=self._managed_port,
            enrollment_port=self._enrollment_port,
            tls_cert_sha256=self.server_pin,
            connect_timeout=0.4,
            io_poll_interval=0.02,
            controller_ping_interval=0.05,
            controller_pong_timeout=0.25,
            agent_read_deadline=0.3,
            retry_base=0.02,
            retry_max=0.05,
            retry_jitter=0,
            display_name="phase2-agent",
            agent_version="2.0-test",
            certificate_store_path=str(self.root / "identity.dpapi"),
            log_path=str(self.root / "phase2-agent.log"),
        )
        return replace(config, **changes)

    def new_store(self, name: str | None = None) -> AgentCertificateStore:
        name = name or f"identity-{uuid4()}.dpapi"
        return AgentCertificateStore(self.root / name, protector=self.protector)

    def enroll(
        self, token: str, store: AgentCertificateStore | None = None
    ) -> AgentCertificateIdentity:
        store = store or self.new_store()
        identity = enroll(self.config(), token, store)
        self.identity_stores[identity.agent_id] = store
        return identity

    def issue_identity(
        self,
        authority: ControllerCertificateAuthority,
        agent_id: str | None = None,
        *,
        name: str | None = None,
    ) -> AgentCertificateIdentity:
        store = self.new_store(name)
        private_key, csr = store.create_csr("phase2-direct")
        agent_id = agent_id or str(uuid4())
        issued = authority.sign_device_csr(csr, agent_id)
        return store.save_enrollment(
            private_key,
            agent_id=agent_id,
            certificate_pem=issued.certificate_pem,
            chain_pem=authority.ca_pem(),
            certificate_serial=issued.serial,
            certificate_not_after=issued.certificate_not_after,
        )

    def start(self, identity: AgentCertificateIdentity) -> _AgentHandle:
        runtime = AgentRuntime(
            self.config(),
            identity,
            identity_store=self.identity_stores[identity.agent_id],
        )
        handle = _AgentHandle(runtime).start()
        self.agents.append(handle)
        return handle

    def wait_for_state(self, agent_id: str, state: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                detail = self.queries.get_device(agent_id)
            except sqlite3.Error:
                detail = None
            if detail is not None and detail.state == state:
                return True
            time.sleep(0.01)
        return False

    def wait_for_new_session(
        self, agent_id: str, previous_session_id: str, timeout: float
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sessions = [s for s in self.sessions.snapshot() if s.agent_id == agent_id]
            if sessions and sessions[0].session_id != previous_session_id:
                return True
            time.sleep(0.01)
        return False

    def wait_for_auth_rejection(self, handle: _AgentHandle, timeout: float) -> bool:
        before = len(handle.events)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.sessions.snapshot() and any(
                event["event"] == "CONNECTION_FAILURE"
                for event in handle.events[before:]
            ):
                return True
            time.sleep(0.01)
        return False

    def restart_controller(self) -> None:
        self._stop_controller()
        self._start_controller()

    def _client_context(
        self, identity: AgentCertificateIdentity | None
    ) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cafile=self.ca_path)
        if identity is None:
            return context
        directory = Path(tempfile.mkdtemp(prefix="phase2-context-", dir=self.root))
        certificate = directory / "client-chain.pem"
        key = directory / "client-key.pem"
        try:
            certificate.write_bytes(
                identity.certificate_pem.rstrip() + b"\n" + identity.chain_pem
            )
            key.write_bytes(identity.private_key_pem)
            context.load_cert_chain(certificate, key)
        finally:
            shutil.rmtree(directory)
        return context

    def open_session(self, identity: AgentCertificateIdentity) -> ssl.SSLSocket:
        raw = socket.create_connection(("127.0.0.1", self._managed_port), timeout=1)
        try:
            connection = self._client_context(identity).wrap_socket(
                raw, server_hostname="127.0.0.1"
            )
        except Exception:
            raw.close()
            raise
        connection.settimeout(1)
        assert _recv_frame(connection, 1) == b"PING"
        return connection

    def assert_certificate_rejected(
        self, identity: AgentCertificateIdentity | None
    ) -> None:
        raw = socket.create_connection(("127.0.0.1", self._managed_port), timeout=1)
        connection = raw
        try:
            connection = self._client_context(identity).wrap_socket(
                raw, server_hostname="127.0.0.1"
            )
            connection.settimeout(1)
            assert _recv_frame(connection, 1) != b"PING"
        except (ConnectionError, OSError, ssl.SSLError, TimeoutError):
            pass
        finally:
            connection.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for agent in self.agents:
            agent.stop()
        for agent in self.agents:
            assert agent.join()
            assert agent.error is None
        self._stop_controller()


@pytest.fixture
def phase2_system():
    systems: list[Phase2System] = []

    def build(tmp_path: Path, **kwargs) -> Phase2System:
        system = Phase2System(tmp_path, **kwargs)
        systems.append(system)
        return system

    try:
        yield build
    finally:
        for system in reversed(systems):
            system.close()


def _assert_peer_closed(connection: ssl.SSLSocket, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail("replaced peer socket remained open")
        connection.settimeout(remaining)
        try:
            received = connection.recv(4096)
        except (ConnectionResetError, ssl.SSLEOFError, ssl.SSLZeroReturnError):
            return
        except TimeoutError:
            pytest.fail("replaced peer socket remained open")
        if not received:
            return


def test_loopback_requires_explicit_phase2_test_flag(tmp_path):
    with pytest.raises(ValueError, match="exact managed IP"):
        Phase2System(tmp_path, allow_loopback=False)


def test_enroll_online_disconnect_reconnect_revoke_rejects(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    token = system.registry.issue_token(600)
    identity = system.agent.enroll(token)
    first = system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", timeout=5)
    original = system.sessions.snapshot()[0].session_id

    assert system.actions.disconnect(identity.agent_id, "integration", "test").code == "DISCONNECTED"
    assert system.wait_for_new_session(identity.agent_id, original, timeout=5)
    assert system.actions.revoke(identity.agent_id, "integration", "retired").code == "REVOKED"
    assert system.wait_for_state(identity.agent_id, "REVOKED", timeout=5)
    assert system.agent.wait_for_auth_rejection(first, timeout=5)


def test_controller_restart_reloads_registry_and_agent_reconnects_without_enrollment(
    tmp_path, phase2_system
):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", 5)

    system.restart_controller()

    assert system.wait_for_state(identity.agent_id, "ONLINE", 5)
    assert system.enrollment_request_count == 1


def test_wrong_ca_and_absent_client_certificate_are_rejected(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    other = ControllerCertificateAuthority(
        tmp_path / "wrong-ca-key.dpapi",
        tmp_path / "wrong-ca.pem",
        protector=system.protector,
    )
    other.initialize("Wrong Phase 2 CA")
    wrong_identity = system.issue_identity(other, name="wrong-identity.dpapi")

    system.assert_certificate_rejected(wrong_identity)
    system.assert_certificate_rejected(None)
    assert system.sessions.snapshot() == ()


def test_unknown_mismatched_and_revoked_certificates_are_rejected(
    tmp_path, phase2_system
):
    system = phase2_system(tmp_path, allow_loopback=True)
    unknown = system.issue_identity(system.authority, name="unknown.dpapi")
    system.assert_certificate_rejected(unknown)

    enrolled = system.agent.enroll(system.registry.issue_token(600))
    mismatched = system.issue_identity(
        system.authority, enrolled.agent_id, name="mismatched.dpapi"
    )
    system.assert_certificate_rejected(mismatched)
    assert system.actions.revoke(enrolled.agent_id, "integration", "retired").code == "REVOKED"
    system.assert_certificate_rejected(enrolled)
    assert system.sessions.snapshot() == ()


def test_token_replay_is_rejected_over_real_enrollment_tls(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    token = system.registry.issue_token(600)
    system.agent.enroll(token)

    with pytest.raises(EnrollmentRejected):
        system.agent.enroll(token, system.new_store("replay.dpapi"))


def test_duplicate_session_replaces_and_closes_the_previous_socket(
    tmp_path, phase2_system
):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    first = system.open_session(identity)
    _send_frame(first, b"PONG")
    first_id = system.sessions.snapshot()[0].session_id

    second = system.open_session(identity)
    try:
        _send_frame(second, b"PONG")
        assert system.wait_for_new_session(identity.agent_id, first_id, 2)
        _assert_peer_closed(first, 1)
        assert len(system.sessions.snapshot()) == 1
        assert any(
            event.action == "SESSION_REPLACED"
            for event in system.registry.list_audit_events(10)
        )
    finally:
        first.close()
        second.close()


def test_missing_pong_causes_heartbeat_timeout(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    connection = system.open_session(identity)
    try:
        deadline = time.monotonic() + 2
        while system.sessions.snapshot() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert system.sessions.snapshot() == ()
        assert any(
            event.action == "HEARTBEAT_TIMEOUT"
            for event in system.registry.list_audit_events(10)
        )
    finally:
        connection.close()


def test_registry_busy_timeout_is_bounded_and_fails_closed(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True, busy_timeout_ms=100)
    blocker = sqlite3.connect(system.db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            system.registry.issue_token(600)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert 0.08 <= time.monotonic() - started < 1


def test_signer_absence_rejects_enrollment_without_dropping_live_sessions(
    tmp_path, phase2_system
):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", 5)
    token = system.registry.issue_token(600)
    backup = system.ca_key_path.with_suffix(".backup")
    system.ca_key_path.replace(backup)
    try:
        with pytest.raises(EnrollmentRejected):
            system.agent.enroll(token, system.new_store("signer-absent.dpapi"))
        assert system.wait_for_state(identity.agent_id, "ONLINE", 1)
    finally:
        backup.replace(system.ca_key_path)

    assert system.agent.enroll(token, system.new_store("signer-restored.dpapi"))


def test_bounded_worker_saturation_rejects_before_tls_handshake(
    tmp_path, phase2_system
):
    system = phase2_system(tmp_path, allow_loopback=True, max_workers=1)
    first = socket.create_connection(("127.0.0.1", system._managed_port), timeout=1)
    second = None
    try:
        deadline = time.monotonic() + 2
        while len(system.managed._threads) != 1:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        second = socket.create_connection(("127.0.0.1", system._managed_port), timeout=1)
        try:
            assert second.recv(1) == b""
        except ConnectionError:
            pass
        assert len(system.managed._threads) == 1
    finally:
        first.close()
        if second is not None:
            second.close()


def test_clean_shutdown_releases_threads_and_listener_ports(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    handle = system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", 5)
    threads = [system.managed_thread, system.enrollment_thread, handle.thread]
    managed_port = system._managed_port
    enrollment_port = system._enrollment_port

    system.close()

    assert all(not thread.is_alive() for thread in threads)
    for port in (managed_port, enrollment_port):
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)
