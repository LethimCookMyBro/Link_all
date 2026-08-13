from __future__ import annotations

import ipaddress
import math
import select
import socket
import ssl
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from C2.managed_pki import DEVICE_URI_PREFIX
from C2.managed_registry import ManagedRegistry, utc_now
from client.transport import FrameDecoder, encode_message

_MAX_FRAME = 4


def validate_managed_bind(host: str, *, allow_loopback: bool = False) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("managed bind must be an exact managed IP") from exc
    effective = getattr(address, "ipv4_mapped", None) or address
    if (
        effective.is_unspecified
        or effective.is_multicast
        or effective.is_link_local
        or effective.is_global
        or str(effective) == "255.255.255.255"
        or (effective.is_loopback and not allow_loopback)
    ):
        raise ValueError("managed bind must be an exact managed IP")
    return str(address)


@dataclass(frozen=True)
class SessionSnapshot:
    agent_id: str
    session_id: str
    peer_ip: str
    connected_at: str
    last_heartbeat_at: str


@dataclass
class _Session:
    snapshot: SessionSnapshot
    fingerprint: str
    serial: str
    connection: socket.socket


class SessionManager:
    def __init__(self, registry: ManagedRegistry, *, now=utc_now) -> None:
        self.registry = registry
        self.now = now
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    def register(
        self,
        agent_id: str,
        fingerprint: str,
        serial: str,
        peer_ip: str,
        connection: socket.socket,
    ) -> SessionSnapshot:
        previous = None
        with self._lock:
            previous = self._sessions.get(agent_id)
            session_id = str(uuid4())
            timestamp = _format_time(self.now())
            snapshot = SessionSnapshot(
                agent_id,
                session_id,
                str(ipaddress.ip_address(peer_ip)),
                timestamp,
                timestamp,
            )
            details = {
                "peer_ip": snapshot.peer_ip,
                "session_id": session_id,
            }
            action = "CONNECTED"
            if previous is not None:
                action = "SESSION_REPLACED"
                details["previous_session_id"] = previous.snapshot.session_id
            if not self.registry.is_connection_allowed(agent_id, fingerprint, serial):
                raise PermissionError("certificate is not allowed")
            current = _Session(snapshot, fingerprint, serial, connection)
            self._sessions[agent_id] = current
            try:
                self.registry.append_audit(
                    actor="managed-listener",
                    action=action,
                    target_agent_id=agent_id,
                    result="SUCCEEDED",
                    reason=None,
                    correlation_id=session_id,
                    details=details,
                )
            except Exception:
                if previous is None:
                    del self._sessions[agent_id]
                else:
                    self._sessions[agent_id] = previous
                raise
        if previous is not None:
            _close_connection(previous.connection)
        return snapshot

    def heartbeat(self, agent_id: str, session_id: str) -> None:
        rejected = None
        with self._lock:
            current = self._sessions.get(agent_id)
            if current is None or current.snapshot.session_id != session_id:
                return
            if not self.registry.is_connection_allowed(
                agent_id, current.fingerprint, current.serial
            ):
                rejected = self._sessions.pop(agent_id)
            else:
                occurred_at = self.now()
                timestamp = _format_time(occurred_at)
                self.registry.touch_last_seen(
                    agent_id, current.snapshot.peer_ip, occurred_at
                )
                current.snapshot = replace(
                    current.snapshot, last_heartbeat_at=timestamp
                )
        if rejected is not None:
            _close_connection(rejected.connection)
            raise PermissionError("certificate is not allowed")

    def unregister(self, agent_id: str, session_id: str, reason: str) -> bool:
        with self._lock:
            current = self._sessions.get(agent_id)
            if current is None or current.snapshot.session_id != session_id:
                return False
            action = "HEARTBEAT_TIMEOUT" if reason == "HEARTBEAT_TIMEOUT" else "DISCONNECTED"
            self.registry.append_audit(
                actor="managed-listener",
                action=action,
                target_agent_id=agent_id,
                result="SUCCEEDED",
                reason=reason,
                correlation_id=session_id,
                details={
                    "peer_ip": current.snapshot.peer_ip,
                    "session_id": session_id,
                    "status_code": reason,
                },
            )
            del self._sessions[agent_id]
        _close_connection(current.connection)
        return True

    def disconnect(self, agent_id: str) -> bool:
        with self._lock:
            current = self._sessions.get(agent_id)
            if current is None:
                return False
        return self.unregister(agent_id, current.snapshot.session_id, "CONTROLLER_DISCONNECT")

    def snapshot(self) -> tuple[SessionSnapshot, ...]:
        with self._lock:
            return tuple(
                session.snapshot
                for _, session in sorted(self._sessions.items())
            )

    def close_all(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            _close_connection(session.connection)


def _format_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time source must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _close_connection(connection) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        connection.close()
    except OSError:
        pass


def _server_context(certfile: Path, keyfile: Path, ca_certfile: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    context.load_verify_locations(cafile=ca_certfile)
    context.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        context.options |= ssl.OP_NO_COMPRESSION
    return context


def _peer_identity(certificate_der: bytes) -> tuple[str, str, str]:
    certificate = x509.load_der_x509_certificate(certificate_der)
    try:
        uris = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    if len(uris) != 1 or not uris[0].startswith(DEVICE_URI_PREFIX):
        raise ValueError("peer certificate must contain exactly one agent URI")
    candidate = uris[0][len(DEVICE_URI_PREFIX) :]
    try:
        agent_id = str(UUID(candidate))
    except ValueError as exc:
        raise ValueError("peer certificate agent URI is invalid") from exc
    if candidate != agent_id:
        raise ValueError("peer certificate agent URI is not canonical")
    return (
        agent_id,
        certificate.fingerprint(hashes.SHA256()).hex(),
        str(certificate.serial_number),
    )


class ManagedServer:
    def __init__(
        self,
        host: str,
        port: int,
        certfile: Path,
        keyfile: Path,
        ca_certfile: Path,
        registry: ManagedRegistry,
        sessions: SessionManager,
        *,
        allow_loopback: bool = False,
        max_workers: int = 32,
        handshake_timeout: float = 5.0,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
    ) -> None:
        host = validate_managed_bind(host, allow_loopback=allow_loopback)
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("max_workers must be positive")
        for name, value in (
            ("handshake_timeout", handshake_timeout),
            ("ping_interval", ping_interval),
            ("pong_timeout", pong_timeout),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        self.registry = registry
        self.sessions = sessions
        self._context = _server_context(certfile, keyfile, ca_certfile)
        self._handshake_timeout = handshake_timeout
        self._ping_interval = ping_interval
        self._pong_timeout = pong_timeout
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._startup = threading.Condition()
        self._serve_requested = False
        self._accept_thread = None
        self._threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
        family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
        self._listener = socket.socket(family, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen()
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]

    def start(self, stop_event: threading.Event) -> threading.Thread:
        with self._startup:
            if self._stopped.is_set():
                raise RuntimeError("managed server is stopped")
            if self._serve_requested or self._accept_thread is not None:
                raise RuntimeError("managed server is already started")
            self._serve_requested = True
            thread = threading.Thread(
                target=self._serve_forever,
                args=(stop_event,),
                name="managed-listener",
                daemon=False,
            )
            self._accept_thread = thread
            thread.start()
            return thread

    @property
    def serve_forever(self):
        with self._startup:
            self._serve_requested = True
            self._startup.notify_all()
        return self._serve_forever

    def _serve_forever(self, stop_event: threading.Event) -> None:
        current = threading.current_thread()
        with self._startup:
            if self._accept_thread is None:
                self._accept_thread = current
            elif self._accept_thread is not current:
                raise RuntimeError("managed server is already started")
            self._startup.notify_all()
        try:
            while not self._stopped.is_set() and not stop_event.is_set():
                try:
                    connection, address = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not self._worker_slots.acquire(blocking=False):
                    _close_connection(connection)
                    continue
                thread = threading.Thread(
                    target=self._serve_connection,
                    args=(connection, address[0]),
                    name="managed-session",
                    daemon=False,
                )
                with self._lock:
                    if self._stopped.is_set() or stop_event.is_set():
                        _close_connection(connection)
                        self._worker_slots.release()
                        break
                    self._connections.add(connection)
                    self._threads.add(thread)
                    thread.start()
        finally:
            self._close_listener()

    def _serve_connection(self, raw_connection, peer_ip: str) -> None:
        connection = raw_connection
        session = None
        reason = "peer_closed"
        try:
            raw_connection.settimeout(self._handshake_timeout)
            connection = self._context.wrap_socket(raw_connection, server_side=True)
            with self._lock:
                self._connections.discard(raw_connection)
                self._connections.add(connection)
            certificate_der = connection.getpeercert(binary_form=True)
            if not certificate_der:
                raise ValueError("peer certificate unavailable")
            agent_id, fingerprint, serial = _peer_identity(certificate_der)
            session = self.sessions.register(
                agent_id, fingerprint, serial, peer_ip, connection
            )
            while not self._stopped.is_set():
                if _has_pending_input(connection):
                    raise ValueError("unexpected managed frame")
                _send_frame(connection, b"PING")
                if _recv_frame(connection, self._pong_timeout) != b"PONG":
                    raise ValueError("unexpected managed frame")
                self.sessions.heartbeat(agent_id, session.session_id)
                if self._stopped.wait(self._ping_interval):
                    break
        except TimeoutError:
            reason = "HEARTBEAT_TIMEOUT" if session is not None else "handshake_timeout"
        except (ConnectionError, OSError, ssl.SSLError, ValueError, PermissionError):
            pass
        finally:
            if session is not None:
                self.sessions.unregister(session.agent_id, session.session_id, reason)
            else:
                _close_connection(connection)
            with self._lock:
                self._connections.discard(raw_connection)
                self._connections.discard(connection)
                self._threads.discard(threading.current_thread())
            self._worker_slots.release()

    def expire_session_for_test(self, session: SessionSnapshot) -> None:
        self.sessions.unregister(session.agent_id, session.session_id, "HEARTBEAT_TIMEOUT")

    def stop(self, timeout: float = 5.0) -> None:
        deadline = monotonic() + timeout
        self._stopped.set()
        self._close_listener()
        self.sessions.close_all()
        with self._startup:
            while self._serve_requested and self._accept_thread is None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                self._startup.wait(remaining)
            accept_thread = self._accept_thread
        with self._lock:
            connections = tuple(self._connections)
            threads = tuple(self._threads)
        for connection in connections:
            _close_connection(connection)
        current = threading.current_thread()
        for thread in (*threads, accept_thread):
            if thread is not None and thread is not current:
                thread.join(max(0.0, deadline - monotonic()))

    def _close_listener(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


def _send_frame(connection, payload: bytes) -> None:
    header, body = encode_message(payload)
    connection.sendall(header + body)


def _recv_frame(connection, timeout: float) -> bytes:
    decoder = FrameDecoder(max_size=_MAX_FRAME)
    connection.settimeout(timeout)
    while True:
        chunk = connection.recv(8)
        if not chunk:
            raise ConnectionError("peer closed")
        frames = decoder.feed(chunk)
        if frames:
            if (
                len(frames) != 1
                or decoder.expected is not None
                or decoder.buffer
            ):
                raise ValueError("unexpected managed frame")
            return frames[0]


def _has_pending_input(connection) -> bool:
    pending = getattr(connection, "pending", None)
    if pending is not None and pending():
        return True
    return bool(select.select([connection], [], [], 0)[0])
