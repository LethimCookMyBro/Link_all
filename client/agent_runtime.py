from __future__ import annotations

import hashlib
import hmac
import random as _random
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from time import monotonic

from client.agent_config import AgentConfig
from client.managed_identity import AgentCertificateIdentity
from client.transport import FrameDecoder, encode_message

_MAX_MANAGED_FRAME = 64 * 1024


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


class ManagedConnector:
    def __init__(
        self,
        *,
        certificate_store=None,
        socket_factory=socket.socket,
        context_factory=None,
        clock=monotonic,
    ):
        self._certificate_store = certificate_store
        self._socket_factory = socket_factory
        self._context_factory = context_factory
        self.clock = clock
        self._publish_socket = lambda _current, _previous=None: True
        self._clear_socket = lambda _current: None

    def set_socket_hooks(self, publish, clear) -> None:
        self._publish_socket = publish
        self._clear_socket = clear

    def connect(self, config: AgentConfig, identity: AgentCertificateIdentity):
        raw = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        conn = raw
        try:
            raw.settimeout(config.connect_timeout)
            if self._publish_socket(raw, None) is False:
                raise OSError("connection stopped")
            raw.connect((config.controller_host, config.managed_port))

            if self._context_factory is not None:
                context = self._context_factory()
            elif self._certificate_store is not None:
                context = self._certificate_store.client_context(identity)
            else:
                raise RuntimeError("certificate store is required")
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

            return conn
        except Exception:
            try:
                conn.close()
            except OSError:
                pass
            finally:
                self._clear_socket(conn)
            raise

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
        credential: AgentCertificateIdentity,
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
        self.connector = connector or ManagedConnector(certificate_store=identity_store)
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
                if not isinstance(self.credential, AgentCertificateIdentity):
                    raise TypeError("credential must be an AgentCertificateIdentity")
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
