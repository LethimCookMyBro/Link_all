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
from C2.managed_registry import (
    ActionResult,
    AuditEvent,
    DeviceDetail,
    DeviceSummary,
    ManagedRegistry,
    RegistryUnavailable,
    utc_now,
)
from client.transport import FrameDecoder, encode_message

_MAX_FRAME = 4
_PRIVATE_V4 = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")


def validate_managed_bind(host: str, *, allow_loopback: bool = False) -> str:
    if type(host) is not str:
        raise ValueError("managed bind must be an exact managed IP")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("managed bind must be an exact managed IP") from exc
    effective = getattr(address, "ipv4_mapped", None) or address
    allowed = effective.is_loopback and allow_loopback is True
    if isinstance(effective, ipaddress.IPv4Address) and not effective.is_loopback:
        allowed = any(effective in network for network in _PRIVATE_V4)
    elif isinstance(effective, ipaddress.IPv6Address) and not effective.is_loopback:
        allowed = effective in _PRIVATE_V6
    if not allowed:
        raise ValueError("managed bind must be an exact managed IP")
    return address.compressed


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
        try:
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
        except Exception:
            _close_connection(connection)
            raise
        if previous is not None:
            _close_connection(previous.connection)
        return snapshot

    def heartbeat(self, agent_id: str, session_id: str) -> None:
        removed = None
        try:
            with self._lock:
                current = self._sessions.get(agent_id)
                if current is None or current.snapshot.session_id != session_id:
                    return
                try:
                    if not self.registry.is_connection_allowed(
                        agent_id, current.fingerprint, current.serial
                    ):
                        raise PermissionError("certificate is not allowed")
                    occurred_at = self.now()
                    timestamp = _format_time(occurred_at)
                    self.registry.touch_last_seen(
                        agent_id, current.snapshot.peer_ip, occurred_at
                    )
                    current.snapshot = replace(
                        current.snapshot, last_heartbeat_at=timestamp
                    )
                except Exception:
                    if self._sessions.get(agent_id) is current:
                        removed = self._sessions.pop(agent_id)
                    raise
        finally:
            if removed is not None:
                _close_connection(removed.connection)

    def unregister(self, agent_id: str, session_id: str, reason: str) -> bool:
        return self._remove(agent_id, session_id, reason)

    def disconnect(self, agent_id: str) -> bool:
        return self._remove(agent_id, None, "CONTROLLER_DISCONNECT")

    def _remove(
        self, agent_id: str, session_id: str | None, reason: str
    ) -> bool:
        current = None
        try:
            with self._lock:
                found = self._sessions.get(agent_id)
                if found is None or (
                    session_id is not None
                    and found.snapshot.session_id != session_id
                ):
                    return False
                current = found
                correlation_id = found.snapshot.session_id
                action = (
                    "HEARTBEAT_TIMEOUT"
                    if reason == "HEARTBEAT_TIMEOUT"
                    else "DISCONNECTED"
                )
                try:
                    self.registry.append_audit(
                        actor="managed-listener",
                        action=action,
                        target_agent_id=agent_id,
                        result="SUCCEEDED",
                        reason=reason,
                        correlation_id=correlation_id,
                        details={
                            "peer_ip": current.snapshot.peer_ip,
                            "session_id": correlation_id,
                            "status_code": reason,
                        },
                    )
                finally:
                    del self._sessions[agent_id]
        finally:
            if current is not None:
                _close_connection(current.connection)
        return True

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


class DeviceQueryService:
    def __init__(self, registry: ManagedRegistry, sessions: SessionManager) -> None:
        if sessions.registry is not registry:
            raise ValueError("sessions and queries must use the same registry")
        self.registry = registry
        self.sessions = sessions

    def list_devices(self) -> tuple[DeviceSummary, ...]:
        online = {session.agent_id for session in self.sessions.snapshot()}
        try:
            records = self.registry.list_device_records()
        except Exception as exc:
            raise RegistryUnavailable("registry unavailable") from exc
        devices = tuple(
            DeviceSummary(
                record.agent_id,
                record.display_name,
                _merged_state(record, online),
                record.last_vpn_ip,
                record.last_seen_at,
                record.certificate_not_after,
                record.agent_version,
            )
            for record in records
        )
        return tuple(
            sorted(devices, key=lambda item: (item.display_name.casefold(), item.agent_id))
        )

    def get_device(self, agent_id: str) -> DeviceDetail | None:
        agent_id = _validated_uuid(agent_id)
        online = {session.agent_id for session in self.sessions.snapshot()}
        try:
            record = self.registry.get_device(agent_id)
        except Exception as exc:
            raise RegistryUnavailable("registry unavailable") from exc
        if record is None:
            return None
        return replace(record, state=_merged_state(record, online))

    def list_audit_events(self, limit: int = 100) -> tuple[AuditEvent, ...]:
        _validated_limit(limit)
        try:
            return self.registry.list_audit_events(limit)
        except Exception as exc:
            raise RegistryUnavailable("registry unavailable") from exc


class DeviceActionService:
    def __init__(self, registry: ManagedRegistry, sessions: SessionManager) -> None:
        if sessions.registry is not registry:
            raise ValueError("sessions and actions must use the same registry")
        self.registry = registry
        self.sessions = sessions

    def disconnect(self, agent_id: str, actor: str, reason: str) -> ActionResult:
        agent_id = _validated_uuid(agent_id)
        actor = _validated_text("actor", actor, 128)
        reason = _validated_text("reason", reason, 512, allow_empty=True)
        correlation_id = str(uuid4())
        try:
            if self.registry.get_device(agent_id) is None:
                return ActionResult("NOT_FOUND", "Device not found.", correlation_id)
            self.registry.append_audit(
                actor=actor,
                action="DISCONNECT_REQUESTED",
                target_agent_id=agent_id,
                result="REQUESTED",
                reason=reason,
                correlation_id=correlation_id,
                details={},
            )
        except Exception:
            return ActionResult("FAILED", "Disconnect request failed.", correlation_id)

        try:
            disconnected = self.sessions.disconnect(agent_id)
        except Exception:
            return ActionResult("FAILED", "Disconnect failed.", correlation_id)
        code = "DISCONNECTED" if disconnected else "ALREADY_OFFLINE"
        action = "DISCONNECT_SUCCEEDED" if disconnected else "DISCONNECT_ALREADY_OFFLINE"
        message = "Device disconnected." if disconnected else "Device is already offline."
        try:
            self.registry.append_audit(
                actor=actor,
                action=action,
                target_agent_id=agent_id,
                result=code,
                reason=reason,
                correlation_id=correlation_id,
                details={},
            )
        except Exception:
            return ActionResult("FAILED", "Disconnect result audit failed.", correlation_id)
        return ActionResult(code, message, correlation_id)

    def revoke(self, agent_id: str, actor: str, reason: str) -> ActionResult:
        agent_id = _validated_uuid(agent_id)
        actor = _validated_text("actor", actor, 128)
        reason = _validated_text("reason", reason, 512)
        correlation_id = str(uuid4())
        try:
            result = self.registry.revoke_device(
                agent_id, actor, reason, correlation_id
            )
        except Exception:
            return ActionResult("FAILED", "Revoke failed.", correlation_id)
        if result.code in {"REVOKED", "ALREADY_REVOKED"}:
            try:
                self.sessions.disconnect(agent_id)
            except Exception:
                # SessionManager removes and closes the owned socket even when its
                # lifecycle audit fails; durable revocation remains authoritative.
                pass
        return result


def _merged_state(record: DeviceDetail, online: set[str]) -> str:
    if record.state == "REVOKED":
        return "REVOKED"
    if record.agent_id in online:
        return "ONLINE"
    return record.state


def _validated_uuid(value: str) -> str:
    if type(value) is not str:
        raise ValueError("agent_id must be a UUID")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("agent_id must be a UUID") from exc


def _validated_text(
    name: str, value: str, limit: int, *, allow_empty: bool = False
) -> str:
    if (
        type(value) is not str
        or len(value) > limit
        or (not allow_empty and not value)
        or (value and not value.isprintable())
    ):
        raise ValueError(f"{name} must be printable text up to {limit} characters")
    return value


def _validated_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 through 1000")
    return limit


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
        if sessions.registry is not registry:
            raise ValueError("sessions and server must use the same registry")
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
            try:
                if session is not None:
                    self.sessions.unregister(
                        session.agent_id, session.session_id, reason
                    )
                else:
                    _close_connection(connection)
            finally:
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
