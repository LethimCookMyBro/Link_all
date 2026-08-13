from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
import ssl
import struct
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
    _recv_frame,
    _send_frame,
    recv_json_frame,
    send_json_frame,
)
from client.agent_config import AgentConfig, _apply_private_acl
from client.agent_logging import start_agent_logging
from client.agent_runtime import AgentRuntime
from client.managed_agent import EnrollmentRejected, _read_token_file, enroll
from client.transport import build_proof


class _FakeProtector:
    def protect(self, payload):
        return payload

    def unprotect(self, payload):
        return payload


class _CredentialSink:
    def __init__(self):
        self.credential = None

    def save(self, credential):
        self.credential = credential


class _AgentHandle:
    def __init__(self, runtime, thread, events):
        self.runtime = runtime
        self.thread = thread
        self.events = events

    def stop(self):
        self.runtime.stop()

    def join(self, timeout):
        self.thread.join(timeout)
        return not self.thread.is_alive()


class _ManagedLoopbackServer:
    def __init__(self, cert_path, key_path, registry, port=0):
        self.registry = registry
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(cert_path, key_path)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", port))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.sessions = []
        self.threads = []
        self.accept_thread = threading.Thread(
            target=self._accept, name="integration-managed-listener"
        )

    def start(self):
        self.accept_thread.start()

    def _accept(self):
        while not self.stop_event.is_set():
            try:
                raw, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._serve, args=(raw,), name="integration-managed-session"
            )
            self.threads.append(thread)
            thread.start()

    def _serve(self, raw):
        conn = raw
        try:
            raw.settimeout(0.5)
            conn = self.context.wrap_socket(raw, server_side=True)
            conn.settimeout(0.5)
            hello = recv_json_frame(conn, timeout=0.5)
            nonce = os.urandom(32)
            send_json_frame(
                conn,
                {"type": "CHALLENGE", "nonce": base64.b64encode(nonce).decode("ascii")},
            )
            proof_message = recv_json_frame(conn, timeout=0.5)
            secret = self.registry.get(hello.get("agent_id"), hello.get("key_id"))
            try:
                proof = base64.b64decode(proof_message.get("proof", ""), validate=True)
            except ValueError:
                proof = b""
            accepted = (
                hello.get("type") == "HELLO"
                and hello.get("version") == 1
                and proof_message.get("type") == "AUTH_PROOF"
                and secret is not None
                and build_proof(
                    secret,
                    1,
                    hello["agent_id"],
                    hello["key_id"],
                    nonce,
                )
                == proof
            )
            send_json_frame(conn, {"type": "AUTH_OK" if accepted else "AUTH_REJECT"})
            if not accepted:
                return
            with self.condition:
                self.sessions.append(conn)
                self.condition.notify_all()
            while not self.stop_event.wait(0.05):
                pass
        except (ConnectionError, OSError, ssl.SSLError, ValueError):
            pass
        finally:
            if conn not in self.sessions:
                try:
                    conn.close()
                except OSError:
                    pass

    def wait_for_sessions(self, count, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.sessions) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def close_session(self, mode):
        assert self.wait_for_sessions(1, 0.5)
        conn = self.sessions[-1]
        if mode == "silent":
            return
        if mode == "rst":
            fd = conn.detach()
            raw = socket.socket(fileno=fd)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
            raw.close()
            return
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()

    def stop(self):
        self.stop_event.set()
        try:
            self.listener.close()
        except OSError:
            pass
        for conn in self.sessions:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self.accept_thread.join(0.5)
        for thread in self.threads:
            thread.join(0.5)


class LoopbackStack:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.cert_path = tmp_path / "cert.pem"
        self.key_path = tmp_path / "key.pem"
        self.pin = self._write_certificate()
        self.tokens = EnrollmentStore(
            tmp_path / "tokens.json", acl_applier=lambda _path: None
        )
        self.registry = DeviceRegistry(tmp_path / "devices.bin", _FakeProtector())
        self.service = EnrollmentService(self.tokens, self.registry)
        self.enrollment = EnrollmentServer(
            "127.0.0.1",
            0,
            self.cert_path,
            self.key_path,
            self.service,
            handshake_timeout=0.5,
        )
        self.enrollment_thread = threading.Thread(
            target=self.enrollment.serve_forever,
            name="integration-enrollment-listener",
        )
        self.enrollment_thread.start()
        self.managed = None
        self.managed_port = 0
        self.start_managed()
        self.credential = None
        self.agents = []

    def _write_certificate(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
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
            .sign(key, hashes.SHA256())
        )
        self.cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return hashlib.sha256(
            certificate.public_bytes(serialization.Encoding.DER)
        ).hexdigest()

    def config(self, **changes):
        config = AgentConfig(
            controller_host="127.0.0.1",
            managed_port=self.managed_port,
            enrollment_port=self.enrollment.port,
            tls_cert_sha256=self.pin,
            connect_timeout=0.5,
            io_poll_interval=0.05,
            controller_ping_interval=0.09,
            controller_pong_timeout=0.1,
            agent_read_deadline=0.3,
            retry_base=0.03,
            retry_max=0.08,
            retry_jitter=0,
            log_path=str(self.tmp_path / "managed-agent.log"),
            log_max_bytes=1024,
            log_backup_count=1,
        )
        return replace(config, **changes)

    def issue_token(self):
        return self.tokens.issue(60)

    def enroll(self, token):
        sink = _CredentialSink()
        credential = enroll(self.config(), token, sink)
        assert sink.credential == credential
        self.credential = credential
        return credential

    def ensure_credential(self):
        if self.credential is None:
            self.enroll(self.issue_token())
        return self.credential

    def start_agent(self, io_poll_interval=0.05, read_deadline=0.3, config=None):
        credential = self.ensure_credential()
        config = config or self.config(
            io_poll_interval=io_poll_interval,
            agent_read_deadline=read_deadline,
        )
        events = []
        runtime = AgentRuntime(config, credential, event_sink=events.append)
        thread = threading.Thread(
            target=runtime.run, name="integration-managed-agent-runtime"
        )
        handle = _AgentHandle(runtime, thread, events)
        self.agents.append(handle)
        thread.start()
        return handle

    def wait_for_authenticated_sessions(self, count, timeout):
        return self.managed.wait_for_sessions(count, timeout)

    def break_session(self, failure):
        self.managed.close_session(failure)

    def send_fragmented_ping(self, delays):
        assert self.wait_for_authenticated_sessions(1, 0.5)
        frame = len(b"PING").to_bytes(4, "big") + b"PING"
        pieces = (frame[:2], frame[2:5], frame[5:])
        for delay, piece in zip(delays, pieces, strict=True):
            time.sleep(delay)
            self.managed.sessions[-1].sendall(piece)

    def recv_frame(self, timeout):
        return _recv_frame(self.managed.sessions[-1], timeout=timeout, max_size=4)

    def capture_proof(self, credential):
        conn = self._connect_tls()
        send_json_frame(
            conn,
            {
                "type": "HELLO",
                "version": 1,
                "agent_id": credential.agent_id,
                "key_id": credential.key_id,
            },
        )
        challenge = recv_json_frame(conn, timeout=0.5)
        nonce = base64.b64decode(challenge["nonce"], validate=True)
        proof = build_proof(
            credential.secret, 1, credential.agent_id, credential.key_id, nonce
        )
        send_json_frame(
            conn,
            {"type": "AUTH_PROOF", "proof": base64.b64encode(proof).decode("ascii")},
        )
        assert recv_json_frame(conn, timeout=0.5) == {"type": "AUTH_OK"}
        conn.close()
        return proof

    def replay_proof(self, proof):
        credential = self.credential
        conn = self._connect_tls()
        try:
            send_json_frame(
                conn,
                {
                    "type": "HELLO",
                    "version": 1,
                    "agent_id": credential.agent_id,
                    "key_id": credential.key_id,
                },
            )
            recv_json_frame(conn, timeout=0.5)
            send_json_frame(
                conn,
                {
                    "type": "AUTH_PROOF",
                    "proof": base64.b64encode(proof).decode("ascii"),
                },
            )
            return recv_json_frame(conn, timeout=0.5)["type"]
        finally:
            conn.close()

    def _connect_tls(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        raw = socket.create_connection(("127.0.0.1", self.managed_port), timeout=0.5)
        conn = context.wrap_socket(raw, server_hostname="127.0.0.1")
        conn.settimeout(0.5)
        return conn

    def stop_managed(self):
        if self.managed is not None:
            self.managed.stop()
            self.managed = None

    def start_managed(self, port=None):
        chosen_port = (
            self.managed_port if port is None and self.managed_port else port or 0
        )
        self.managed = _ManagedLoopbackServer(
            self.cert_path, self.key_path, self.registry, chosen_port
        )
        self.managed_port = self.managed.port
        self.managed.start()

    def close(self):
        for agent in self.agents:
            agent.stop()
        for agent in self.agents:
            assert agent.join(2.0)
        self.stop_managed()
        self.enrollment.shutdown()
        self.enrollment.server_close()
        self.enrollment_thread.join(0.5)
        assert not self.enrollment_thread.is_alive()


@pytest.fixture
def loopback_stack(tmp_path):
    stack = LoopbackStack(tmp_path)
    try:
        yield stack
    finally:
        stack.close()


@pytest.mark.parametrize("failure", ["fin", "rst", "silent"])
def test_reconnects_after_real_transport_failure(loopback_stack, failure):
    agent = loopback_stack.start_agent()
    loopback_stack.break_session(failure)
    assert loopback_stack.wait_for_authenticated_sessions(2, timeout=3.0)
    agent.stop()
    assert agent.join(2.0)


def test_fragmented_frame_across_poll_timeout(loopback_stack):
    agent = loopback_stack.start_agent(io_poll_interval=0.05, read_deadline=0.5)
    loopback_stack.send_fragmented_ping(delays=[0.0, 0.08, 0.08])
    assert loopback_stack.recv_frame(timeout=0.5) == b"PONG"
    agent.stop()
    assert agent.join(2.0)


def test_one_time_enrollment_and_replay_rejection(loopback_stack):
    token = loopback_stack.issue_token()
    credential = loopback_stack.enroll(token)
    with pytest.raises(EnrollmentRejected):
        loopback_stack.enroll(token)
    old_proof = loopback_stack.capture_proof(credential)
    assert loopback_stack.replay_proof(old_proof) == "AUTH_REJECT"


def test_agent_starts_before_server(loopback_stack):
    loopback_stack.stop_managed()
    agent = loopback_stack.start_agent()
    time.sleep(0.12)
    loopback_stack.start_managed()
    assert loopback_stack.wait_for_authenticated_sessions(1, 3.0)
    agent.stop()
    assert agent.join(2.0)


def test_stop_while_peer_is_silent(loopback_stack):
    agent = loopback_stack.start_agent(read_deadline=0.5)
    assert loopback_stack.wait_for_authenticated_sessions(1, 0.5)
    agent.stop()
    assert agent.join(2.0)


def test_heartbeat_deadline_reconnects(loopback_stack):
    agent = loopback_stack.start_agent()
    assert loopback_stack.wait_for_authenticated_sessions(2, 3.0)
    assert any(event["event"] == "HEARTBEAT_DEADLINE" for event in agent.events)


def test_server_restarts_on_same_port(loopback_stack):
    agent = loopback_stack.start_agent()
    assert loopback_stack.wait_for_authenticated_sessions(1, 0.5)
    port = loopback_stack.managed_port
    loopback_stack.stop_managed()
    loopback_stack.start_managed(port)
    assert loopback_stack.wait_for_authenticated_sessions(1, 3.0)
    agent.stop()
    assert agent.join(2.0)


def test_authenticated_session_resets_backoff(loopback_stack):
    loopback_stack.stop_managed()
    agent = loopback_stack.start_agent()
    time.sleep(0.12)
    loopback_stack.start_managed()
    assert loopback_stack.wait_for_authenticated_sessions(1, 3.0)
    before = len(agent.events)
    loopback_stack.break_session("fin")
    assert loopback_stack.wait_for_authenticated_sessions(2, 3.0)
    retry_delays = [
        event["delay"]
        for event in agent.events[before:]
        if event["event"] == "RETRY_DELAY"
    ]
    assert retry_delays and retry_delays[0] == pytest.approx(0.03)


@pytest.mark.parametrize("payload", [b"NOPE", None])
def test_malformed_or_oversized_frame_reconnects(loopback_stack, payload):
    agent = loopback_stack.start_agent()
    assert loopback_stack.wait_for_authenticated_sessions(1, 0.5)
    conn = loopback_stack.managed.sessions[-1]
    if payload is None:
        conn.sendall((65537).to_bytes(4, "big"))
    else:
        _send_frame(conn, payload)
    assert loopback_stack.wait_for_authenticated_sessions(2, 3.0)
    assert any(
        event.get("category") == "protocol"
        for event in agent.events
        if event["event"] == "CONNECTION_FAILURE"
    )


def test_token_file_is_deleted_before_enrollment(loopback_stack, tmp_path):
    token_path = (tmp_path / "one-time-token.txt").resolve()
    token_path.write_text(loopback_stack.issue_token(), encoding="utf-8")
    _apply_private_acl(token_path)
    token = _read_token_file(str(token_path))
    assert not token_path.exists()
    assert loopback_stack.enroll(token)


def test_pinned_certificate_mismatch(loopback_stack):
    config = loopback_stack.config(tls_cert_sha256="0" * 64)
    agent = loopback_stack.start_agent(config=config)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(
        event.get("category") == "tls" for event in agent.events
    ):
        time.sleep(0.01)
    agent.stop()
    assert agent.join(2.0)
    assert any(event.get("category") == "tls" for event in agent.events)


def test_zero_runtime_and_logging_threads_after_shutdown(loopback_stack):
    config = loopback_stack.config(agent_read_deadline=0.5)
    logging_runtime = start_agent_logging(config)
    listener_thread = logging_runtime.listener._thread
    agent = loopback_stack.start_agent(config=config)
    assert loopback_stack.wait_for_authenticated_sessions(1, 0.5)
    agent.stop()
    assert agent.join(2.0)
    assert logging_runtime.stop(0.5)
    assert not agent.thread.is_alive()
    assert not listener_thread.is_alive()
