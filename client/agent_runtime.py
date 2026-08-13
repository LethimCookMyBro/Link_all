from __future__ import annotations

import base64
import hashlib
import hmac
import random as _random
import socket
import ssl
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from time import monotonic

from client.agent_config import AgentConfig, DeviceCredential
from client.managed_identity import AgentCertificateIdentity
from client.transport import (
    FrameDecoder,
    build_proof,
    decode_json_payload,
    encode_json_payload,
    encode_message,
)

_MAX_MANAGED_FRAME = 64 * 1024
_PROTOCOL_VERSION = 1


class AgentState(Enum):
    STARTING = auto()
    CONNECTING = auto()
    ONLINE = auto()
    BACKOFF = auto()
    STOPPED = auto()


class AuthRejected(Exception):
    pass


class _HeartbeatDeadline(TimeoutError):
    pass


class RetryPolicy:
    def __init__(self, base, maximum, jitter, random=None):
        self.base = base
        self.maximum = maximum
        self.jitter = jitter
        self.random = random or _random.random
        self.failures = 0
        self._nominal = base

    def next_delay(self):
        nominal = min(self._nominal, self.maximum)
        self._nominal = min(nominal * 2, self.maximum)
        self.failures += 1
        spread = nominal * self.jitter
        return nominal - spread + (2 * spread * self.random())

    def reset(self):
        self.failures = 0
        self._nominal = self.base


def send_frame(conn, payload) -> None:
    header, body = encode_message(payload)
    conn.sendall(header + body)


def _tls_client_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _set_deadline_timeout(conn, deadline, clock) -> None:
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("authentication deadline")
    conn.settimeout(remaining)


class _FrameReader:
    def __init__(self, conn, deadline, clock):
        self.conn = conn
        self.deadline = deadline
        self.clock = clock
        self.decoder = FrameDecoder(max_size=_MAX_MANAGED_FRAME)
        self.pending = deque()

    def read(self):
        while not self.pending:
            _set_deadline_timeout(self.conn, self.deadline, self.clock)
            missing = (
                4 - len(self.decoder.buffer)
                if self.decoder.expected is None
                else self.decoder.expected - len(self.decoder.buffer)
            )
            chunk = self.conn.recv(missing)
            if self.clock() >= self.deadline:
                raise TimeoutError("authentication deadline")
            if not chunk:
                raise ConnectionError("peer closed")
            self.pending.extend(self.decoder.feed(chunk))
        return self.pending.popleft()


class ManagedConnector:
    def __init__(
        self,
        *,
        socket_factory=socket.socket,
        context_factory=_tls_client_context,
        clock=monotonic,
    ):
        self._socket_factory = socket_factory
        self._context_factory = context_factory
        self.clock = clock
        self._publish_socket = lambda _current, _previous=None: True
        self._clear_socket = lambda _current: None

    def set_socket_hooks(self, publish, clear) -> None:
        self._publish_socket = publish
        self._clear_socket = clear

    def connect(self, config: AgentConfig, credential: DeviceCredential):
        raw = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        conn = raw
        try:
            raw.settimeout(config.connect_timeout)
            if self._publish_socket(raw, None) is False:
                raise OSError("connection stopped")
            raw.connect((config.controller_host, config.managed_port))

            context = self._context_factory()
            conn = context.wrap_socket(
                raw,
                server_hostname=config.controller_host,
                do_handshake_on_connect=False,
            )
            if self._publish_socket(conn, raw) is False:
                raise OSError("connection stopped")
            conn.settimeout(config.connect_timeout)
            conn.do_handshake()
            certificate = conn.getpeercert(binary_form=True)
            if not isinstance(certificate, (bytes, bytearray, memoryview)):
                raise ssl.SSLError("peer certificate unavailable")
            fingerprint = hashlib.sha256(certificate).hexdigest()
            if not hmac.compare_digest(fingerprint, config.tls_cert_sha256):
                raise ssl.SSLError("peer certificate pin mismatch")

            self._authenticate(conn, credential, config.connect_timeout)
            return conn
        except Exception:
            try:
                conn.close()
            except OSError:
                pass
            finally:
                self._clear_socket(conn)
            raise

    def _authenticate(self, conn, credential: DeviceCredential, timeout: float) -> None:
        deadline = self.clock() + timeout
        _set_deadline_timeout(conn, deadline, self.clock)
        send_frame(
            conn,
            encode_json_payload(
                {
                    "type": "HELLO",
                    "version": _PROTOCOL_VERSION,
                    "agent_id": credential.agent_id,
                    "key_id": credential.key_id,
                }
            ),
        )
        reader = _FrameReader(conn, deadline, self.clock)
        challenge = _decode_auth_message(reader.read())
        if set(challenge) != {"type", "nonce"} or challenge["type"] != "CHALLENGE":
            raise ValueError("invalid CHALLENGE")
        nonce_text = challenge["nonce"]
        if not isinstance(nonce_text, str) or len(nonce_text) > 64:
            raise ValueError("invalid CHALLENGE")
        nonce = base64.b64decode(nonce_text, validate=True)
        if len(nonce) != 32 or base64.b64encode(nonce).decode("ascii") != nonce_text:
            raise ValueError("invalid CHALLENGE")

        proof = build_proof(
            credential.secret,
            _PROTOCOL_VERSION,
            credential.agent_id,
            credential.key_id,
            nonce,
        )
        _set_deadline_timeout(conn, deadline, self.clock)
        send_frame(
            conn,
            encode_json_payload(
                {
                    "type": "AUTH_PROOF",
                    "proof": base64.b64encode(proof).decode("ascii"),
                }
            ),
        )
        result = _decode_auth_message(reader.read())
        if result == {"type": "AUTH_OK"}:
            return
        if result == {"type": "AUTH_REJECT"}:
            raise AuthRejected()
        raise ValueError("invalid authentication result")


def _decode_auth_message(payload):
    try:
        return decode_json_payload(payload)
    except RecursionError as exc:
        raise ValueError("invalid authentication message") from exc


def _validate_credential(credential: DeviceCredential) -> None:
    if not isinstance(credential, DeviceCredential):
        raise TypeError("credential must be a DeviceCredential")
    for name in ("agent_id", "key_id"):
        value = getattr(credential, name)
        if not isinstance(value, str) or not 0 < len(value) <= 128:
            raise ValueError(f"credential {name} is invalid")
    if not isinstance(credential.secret, bytes) or len(credential.secret) != 32:
        raise ValueError("credential secret is invalid")


def _failure_category(exc) -> str:
    if isinstance(exc, _HeartbeatDeadline):
        return "heartbeat"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ValueError):
        return "protocol"
    return "network"


def _discard_event(_event) -> None:
    pass


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        credential: DeviceCredential,
        *,
        connector=None,
        retry_policy=None,
        stop_event=None,
        clock=monotonic,
        event_sink=None,
        identity_store=None,
        renewer=None,
    ) -> None:
        self.config = config
        self.credential = credential
        self.stop_event = stop_event or threading.Event()
        self.clock = clock
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._state = AgentState.STARTING
        self._attempt = 0
        self._connection = None
        self._pending_close_category = None
        self._event_sink = event_sink if event_sink is not None else _discard_event
        self.identity_store = identity_store
        self.renewer = renewer
        self._renewal_in_progress_serial = None
        self._renewal_succeeded_for_serial = None
        self.connector = connector or ManagedConnector()
        bind = getattr(self.connector, "set_socket_hooks", None)
        if bind is not None:
            bind(self._publish_connection, self._clear_connection)
        self.retry_policy = retry_policy or RetryPolicy(
            config.retry_base,
            config.retry_max,
            config.retry_jitter,
        )

    def prepare_identity(self, *, now=None) -> bool:
        now = datetime.now(timezone.utc) if now is None else now
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(timezone.utc)
        with self._lock:
            identity = self.credential
            if not isinstance(identity, AgentCertificateIdentity):
                return True
            expiry = _parse_certificate_time(identity.certificate_not_after)
            if expiry <= now:
                expired = True
            elif expiry - now > timedelta(days=30):
                return True
            else:
                expired = False
                serial = identity.certificate_serial
                if (
                    self._renewal_succeeded_for_serial == serial
                    or self._renewal_in_progress_serial == serial
                ):
                    return True
                self._renewal_in_progress_serial = serial
        if expired:
            self._emit("CERTIFICATE_EXPIRED")
            return False
        try:
            if self.renewer is None or self.identity_store is None:
                raise RuntimeError("certificate renewer unavailable")
            renewed = self.renewer(self.config, identity, self.identity_store)
            if not isinstance(renewed, AgentCertificateIdentity):
                raise TypeError("renewer returned invalid identity")
            if renewed.agent_id != identity.agent_id:
                raise ValueError("renewer changed agent identity")
            with self._lock:
                if self.credential is not identity or (
                    self.credential.certificate_serial != serial
                ):
                    if self._renewal_in_progress_serial == serial:
                        self._renewal_in_progress_serial = None
                    return True
                self.credential = renewed
                self._renewal_in_progress_serial = None
                self._renewal_succeeded_for_serial = serial
            self._emit("CERTIFICATE_RENEWED")
        except Exception:  # noqa: BLE001 - valid current identity remains usable
            with self._lock:
                if self._renewal_in_progress_serial == serial:
                    self._renewal_in_progress_serial = None
            self._emit("CERTIFICATE_RENEWAL_FAILED")
        return True

    @property
    def state(self):
        with self._lock:
            return self._state

    def _set_state(self, state) -> None:
        with self._lock:
            if self._state is state:
                return
            self._state = state
        self._emit("STATE_TRANSITION", state=state)

    def _emit(self, event, *, state=None, category=None, delay=None) -> None:
        current = state or self.state
        record = {
            "event": event,
            "state": current.name,
            "attempt": self._attempt,
        }
        if category is not None:
            record["category"] = category
        if delay is not None:
            record["delay"] = delay
        try:
            self._event_sink(record)
        except Exception:  # noqa: BLE001 - observability must not kill the owner loop
            return

    def _publish_connection(self, current, previous=None):
        with self._lock:
            if previous is None or self._connection in (None, previous, current):
                self._connection = current
            stopped = self.stop_event.is_set()
        if stopped:
            self._shutdown(current)
        return not stopped

    def _clear_connection(self, current) -> None:
        with self._lock:
            if self._connection is current:
                self._connection = None
                self._pending_close_category = (
                    "forced" if self.stop_event.is_set() else "clean"
                )

    def _emit_pending_close(self) -> None:
        with self._lock:
            category = self._pending_close_category
            self._pending_close_category = None
        if category is not None:
            self._emit("SOCKET_CLOSE", category=category)

    @staticmethod
    def _shutdown(conn) -> None:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _close_connection(self, conn=None) -> None:
        if conn is None:
            with self._lock:
                conn = self._connection
        if conn is None:
            return
        try:
            conn.close()
        except OSError:
            pass
        finally:
            self._clear_connection(conn)

    def stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            conn = self._connection
        if conn is not None:
            self._shutdown(conn)

    def run_one_session(self, conn) -> None:
        conn.settimeout(self.config.io_poll_interval)
        decoder = FrameDecoder(max_size=_MAX_MANAGED_FRAME)
        deadline = self.clock() + self.config.agent_read_deadline
        while not self.stop_event.is_set():
            try:
                chunk = conn.recv(_MAX_MANAGED_FRAME)
            except TimeoutError:
                chunk = None
            if chunk is not None:
                if not chunk:
                    raise ConnectionError("peer closed")
                for payload in decoder.feed(chunk):
                    if payload != b"PING":
                        raise ValueError("unexpected online frame")
                    deadline = self.clock() + self.config.agent_read_deadline
                    send_frame(conn, b"PONG")
            if self.stop_event.is_set():
                return
            if self.clock() >= deadline:
                self._emit("HEARTBEAT_DEADLINE")
                raise _HeartbeatDeadline("heartbeat deadline")

    def run(self) -> None:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("agent runtime is already running")
        if self.state is AgentState.STOPPED:
            self._run_lock.release()
            return
        try:
            self._emit("PROCESS_START")
            try:
                if isinstance(self.credential, DeviceCredential):
                    _validate_credential(self.credential)
                elif not isinstance(self.credential, AgentCertificateIdentity):
                    raise TypeError("credential must be a DeviceCredential")
            except (TypeError, ValueError):
                self._emit("CONNECTION_FAILURE", category="credential")
                raise
            while not self.stop_event.is_set():
                if not self.prepare_identity():
                    break
                self._attempt += 1
                self._set_state(AgentState.CONNECTING)
                if self.stop_event.is_set():
                    break
                self._emit("CONNECTION_ATTEMPT")
                if self.stop_event.is_set():
                    break
                conn = None
                transient = False
                try:
                    conn = self.connector.connect(self.config, self.credential)
                    self._emit("AUTH_ACCEPTED")
                    self._emit("CONNECTION_SUCCESS")
                    if not self._publish_connection(conn):
                        continue
                    self.retry_policy.reset()
                    self._set_state(AgentState.ONLINE)
                    self.run_one_session(conn)
                except AuthRejected:
                    self._emit("AUTH_REJECTED")
                    self._emit("CONNECTION_FAILURE", category="auth")
                    break
                except (OSError, ValueError) as exc:
                    transient = True
                    if not self.stop_event.is_set():
                        self._emit(
                            "CONNECTION_FAILURE",
                            category=_failure_category(exc),
                        )
                finally:
                    self._close_connection(conn)
                    self._emit_pending_close()

                if self.stop_event.is_set():
                    break
                if transient:
                    self._set_state(AgentState.BACKOFF)
                    delay = self.retry_policy.next_delay()
                    self._emit("RETRY_DELAY", delay=delay)
                    if self.stop_event.wait(delay):
                        break
        finally:
            try:
                self._close_connection()
                self._emit_pending_close()
                self._set_state(AgentState.STOPPED)
                self._emit("PROCESS_STOP")
            finally:
                self._run_lock.release()


def _parse_certificate_time(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("certificate expiry must be UTC RFC3339")
    parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("certificate expiry must be UTC RFC3339")
    return parsed
