from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import http.server
import json
import math
import os
import secrets
import select
import socket
import sqlite3
import ssl
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic, time
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from C2.managed_pki import ControllerCertificateAuthority
from C2.managed_registry import (
    CertificateRejected,
    EnrollmentTokenRejected,
    ManagedRegistry,
    ManagedRegistryError,
    _token_digest,
    utc_now,
)
from client import transport
from client.agent_config import (
    DeviceCredential,
    _apply_private_acl,
    _atomic_private_write,
    _DpapiProtector,
    _read_private_file,
)
from client.transport import build_proof, canonical_auth_input, verify_proof

__all__ = [
    "DeviceRegistry",
    "EnrollmentResponse",
    "EnrollmentServer",
    "EnrollmentService",
    "EnrollmentStore",
    "ManagedServer",
    "build_proof",
    "canonical_auth_input",
    "recv_json_frame",
    "send_json_frame",
    "verify_proof",
]

_MAX_JSON_SIZE = 64 * 1024
_MAX_ID_SIZE = 128
_PROCESS_LOCK_TIMEOUT = 5.0
_local_locks_guard = threading.Lock()
_local_locks = {}


class _ProcessFileLock:
    """Serialize one store across threads and processes without dependencies."""

    def __init__(self, data_path, timeout=_PROCESS_LOCK_TIMEOUT):
        self.path = Path(f"{data_path}.lock")
        self.timeout = timeout
        key = str(self.path.resolve())
        with _local_locks_guard:
            self._thread_lock = _local_locks.setdefault(key, threading.Lock())
        self._file = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as lock_file:
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()

    def __enter__(self):
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise TimeoutError("store lock timed out")
        try:
            self._file = self.path.open("r+b")
            deadline = monotonic() + self.timeout
            while True:
                try:
                    self._lock_byte()
                    return self
                except OSError as exc:
                    if (
                        exc.errno not in (errno.EACCES, errno.EAGAIN, 13, 36)
                        or monotonic() >= deadline
                    ):
                        raise TimeoutError("store lock timed out") from exc
                    threading.Event().wait(min(0.01, deadline - monotonic()))
        except Exception:
            if self._file is not None:
                self._file.close()
                self._file = None
            self._thread_lock.release()
            raise

    def _lock_byte(self):
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *_):
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self._thread_lock.release()


def _recv_exactly(conn, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = conn.recv(size - len(payload))
        if not chunk:
            raise ConnectionError("connection closed")
        payload.extend(chunk)
    return bytes(payload)


def _send_frame(conn, payload: bytes, *, max_size: int = _MAX_JSON_SIZE) -> None:
    if len(payload) > max_size:
        raise ValueError("frame too large")
    conn.sendall(len(payload).to_bytes(4, "big") + payload)


def _recv_frame(conn, *, timeout: float, max_size: int = _MAX_JSON_SIZE) -> bytes:
    previous_timeout = conn.gettimeout()
    conn.settimeout(timeout)
    try:
        size = int.from_bytes(_recv_exactly(conn, 4), "big")
        if size > max_size:
            raise ValueError("frame too large")
        return _recv_exactly(conn, size)
    finally:
        conn.settimeout(previous_timeout)


def send_json_frame(conn, message, *, max_size: int = _MAX_JSON_SIZE) -> None:
    _send_frame(conn, transport.encode_json_payload(message), max_size=max_size)


def recv_json_frame(
    conn, timeout: float = 10.0, max_size: int = _MAX_JSON_SIZE
) -> dict:
    payload = _recv_frame(conn, timeout=timeout, max_size=max_size)
    return transport.decode_json_payload(payload, max_size=max_size)


class EnrollmentStore:
    def __init__(
        self,
        path: os.PathLike[str] | str,
        now: Callable[[], float] = time,
        *,
        acl_inspector=None,
        acl_applier=None,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self._lock = _ProcessFileLock(self.path)
        self._needs_migration = False
        self._acl_inspector = acl_inspector
        self._acl_applier = acl_applier or _apply_private_acl

    def issue(self, ttl_seconds: float) -> str:
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be positive")
        token = secrets.token_urlsafe(32)
        digest = self._token_hash(token)
        with self._lock:
            records = self._read_unlocked()
            records[digest] = {
                "expires_at": self._now() + ttl_seconds,
                "consumed": False,
                "pending": False,
            }
            self._write_unlocked(records)
        return token

    def consume(self, token: str) -> bool:
        digest = self._token_hash(token, invalid=None)
        if digest is None:
            return False
        with self._lock:
            records = self._read_unlocked()
            if not self._is_valid_unlocked(records, digest):
                return False
            self._burn_unlocked(records, digest)
            self._write_unlocked(records)
            return True

    @staticmethod
    def _token_hash(token: str, invalid=...):
        if type(token) is not str or len(token) != 43:
            return EnrollmentStore._invalid_token(invalid)
        try:
            encoded = token.encode("ascii")
            decoded = base64.b64decode(encoded + b"=", altchars=b"-_", validate=True)
        except (UnicodeEncodeError, ValueError):
            return EnrollmentStore._invalid_token(invalid)
        if (
            len(decoded) != 32
            or base64.urlsafe_b64encode(decoded).rstrip(b"=") != encoded
        ):
            return EnrollmentStore._invalid_token(invalid)
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _invalid_token(invalid):
        if invalid is ...:
            raise ValueError("token must be a canonical 32-byte URL-safe value")
        return invalid

    def _is_valid_unlocked(self, records: Mapping[str, dict], digest: str) -> bool:
        record = records.get(digest)
        return bool(
            record
            and not record["consumed"]
            and not record["pending"]
            and self._now() < record["expires_at"]
        )

    @staticmethod
    def _mark_pending_unlocked(records: dict[str, dict], digest: str) -> None:
        records[digest]["pending"] = True

    def _burn_unlocked(self, records: dict[str, dict], digest: str) -> None:
        records[digest]["consumed"] = True
        records[digest]["pending"] = False

    def _read_unlocked(self) -> dict[str, dict]:
        if not self.path.exists():
            self._needs_migration = False
            return {}
        try:
            raw = _read_private_file(self.path, self._acl_inspector)
            records = json.loads(raw.decode("utf-8"))
            if not isinstance(records, dict):
                raise TypeError
            needs_migration = False
            for digest, record in records.items():
                fields = set(record) if isinstance(record, dict) else set()
                if fields == {"expires_at", "consumed"}:
                    record["pending"] = False
                    needs_migration = True
                elif fields != {"expires_at", "consumed", "pending"}:
                    raise ValueError
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                    or not isinstance(record, dict)
                    or not isinstance(record["expires_at"], (int, float))
                    or isinstance(record["expires_at"], bool)
                    or not math.isfinite(record["expires_at"])
                    or type(record["consumed"]) is not bool
                    or type(record["pending"]) is not bool
                    or (record["consumed"] and record["pending"])
                ):
                    raise ValueError
            self._needs_migration = needs_migration
            return records
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(f"invalid enrollment store: {self.path}") from exc

    def _write_unlocked(self, records: Mapping[str, dict]) -> None:
        payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        _atomic_private_write(self.path, payload, self._acl_applier)
        self._needs_migration = False


class DeviceRegistry:
    def __init__(
        self,
        path: os.PathLike[str] | str,
        protector=None,
        *,
        acl_inspector=None,
        acl_applier=None,
    ) -> None:
        self.path = Path(path)
        self._test_boundary = protector is not None
        self._protector = protector or _DpapiProtector()
        self._lock = _ProcessFileLock(self.path)
        self._needs_migration = False
        self._acl_inspector = acl_inspector
        self._acl_applier = (
            acl_applier
            if acl_applier is not None
            else (None if self._test_boundary else _apply_private_acl)
        )

    def enroll(self) -> DeviceCredential:
        return self._create(active=True, pending_digest=None)

    def get(self, agent_id: str, key_id: str) -> bytes | None:
        identity = (agent_id, key_id)
        with self._lock:
            record = self._read_unlocked().get(identity)
            if (
                record is None
                or not record["active"]
                or record["pending_digest"] is not None
            ):
                return None
            return record["secret"]

    def revoke(self, agent_id: str, key_id: str) -> bool:
        identity = (agent_id, key_id)
        with self._lock:
            records = self._read_unlocked()
            if identity not in records:
                return False
            records[identity]["active"] = False
            self._write_unlocked(records)
            del records[identity]
            self._write_unlocked(records)
            return True

    def list_devices(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"agent_id": agent_id, "key_id": key_id}
                for (agent_id, key_id), record in sorted(self._read_unlocked().items())
                if record["active"] and record["pending_digest"] is None
            ]

    def _stage(self, digest: str) -> DeviceCredential:
        return self._create(active=False, pending_digest=digest)

    def _create(self, *, active: bool, pending_digest: str | None) -> DeviceCredential:
        credential = DeviceCredential(
            str(uuid4()), str(uuid4()), secrets.token_bytes(32)
        )
        with self._lock:
            records = self._read_unlocked()
            records[(credential.agent_id, credential.key_id)] = {
                "secret": credential.secret,
                "active": active,
                "pending_digest": pending_digest,
            }
            self._write_unlocked(records)
        return credential

    def _activate(self, credential: DeviceCredential) -> None:
        identity = (credential.agent_id, credential.key_id)
        with self._lock:
            records = self._read_unlocked()
            record = records.get(identity)
            if record is None or record["secret"] != credential.secret:
                raise ValueError("staged device credential is unavailable")
            record["active"] = True
            self._write_unlocked(records)

    def _finalize(self, credential: DeviceCredential) -> None:
        identity = (credential.agent_id, credential.key_id)
        with self._lock:
            records = self._read_unlocked()
            record = records.get(identity)
            if (
                record is None
                or not record["active"]
                or record["secret"] != credential.secret
                or record["pending_digest"] is None
            ):
                raise ValueError("staged device credential is unavailable")
            record["pending_digest"] = None
            self._write_unlocked(records)

    def _discard_unfinished(self) -> None:
        with self._lock:
            records = self._read_unlocked()
            active_records = {
                identity: record
                for identity, record in records.items()
                if record["active"] and record["pending_digest"] is None
            }
            if len(active_records) != len(records) or self._needs_migration:
                self._write_unlocked(active_records)

    def _read_unlocked(self) -> dict[tuple[str, str], dict]:
        if not self.path.exists():
            self._needs_migration = False
            return {}
        if not self._test_boundary or self._acl_inspector is not None:
            protected = _read_private_file(self.path, self._acl_inspector)
        else:
            protected = self.path.read_bytes()
        try:
            payload = json.loads(self._protector.unprotect(protected).decode("utf-8"))
            devices = payload["devices"]
            if (
                not isinstance(payload, dict)
                or set(payload) != {"devices"}
                or not isinstance(devices, list)
            ):
                raise ValueError
            records = {}
            needs_migration = False
            for device in devices:
                current_fields = {
                    "active",
                    "agent_id",
                    "key_id",
                    "pending_digest",
                    "secret",
                }
                legacy_fields = current_fields - {"pending_digest"}
                fields = set(device) if isinstance(device, dict) else set()
                if fields == legacy_fields:
                    pending_digest = None
                    needs_migration = True
                elif fields == current_fields:
                    pending_digest = device["pending_digest"]
                else:
                    raise ValueError
                agent_id = device["agent_id"]
                key_id = device["key_id"]
                active = device["active"]
                if (
                    not isinstance(agent_id, str)
                    or not agent_id
                    or not isinstance(key_id, str)
                    or not key_id
                ):
                    raise ValueError
                if type(active) is not bool:
                    raise ValueError
                if pending_digest is not None and (
                    not isinstance(pending_digest, str)
                    or len(pending_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in pending_digest
                    )
                ):
                    raise ValueError
                secret = base64.b64decode(device["secret"], validate=True)
                if len(secret) != 32 or (agent_id, key_id) in records:
                    raise ValueError
                records[(agent_id, key_id)] = {
                    "secret": secret,
                    "active": active,
                    "pending_digest": pending_digest,
                }
            self._needs_migration = needs_migration
            return records
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(f"invalid device registry: {self.path}") from exc

    def _write_unlocked(self, records: Mapping[tuple[str, str], dict]) -> None:
        devices = [
            {
                "active": record["active"],
                "agent_id": agent_id,
                "key_id": key_id,
                "pending_digest": record["pending_digest"],
                "secret": base64.b64encode(record["secret"]).decode("ascii"),
            }
            for (agent_id, key_id), record in sorted(records.items())
        ]
        raw = json.dumps(
            {"devices": devices}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _atomic_private_write(
            self.path, self._protector.protect(raw), self._acl_applier
        )
        self._needs_migration = False


@dataclass(frozen=True)
class EnrollmentResponse:
    agent_id: str
    certificate_pem: bytes
    chain_pem: bytes
    certificate_serial: str
    certificate_not_after: str


class SigningUnavailable(Exception):
    pass


class EnrollmentService:
    def __init__(
        self,
        registry: ManagedRegistry | EnrollmentStore,
        certificate_authority: ControllerCertificateAuthority | DeviceRegistry,
        *,
        now=utc_now,
    ) -> None:
        self._legacy = isinstance(registry, EnrollmentStore)
        self._registry = certificate_authority if self._legacy else registry
        self._tokens = registry if self._legacy else None
        self._certificate_authority = None if self._legacy else certificate_authority
        self._now = now
        self._lock = threading.Lock()
        if self._legacy:
            self.reconcile()

    def ca_pem(self) -> bytes | None:
        return None if self._legacy else self._certificate_authority.ca_pem()

    def reconcile(self) -> None:
        with self._lock, self._tokens._lock:
            self._reconcile_unlocked()

    def _reconcile_unlocked(self) -> dict[str, dict]:
        records = self._tokens._read_unlocked()
        pending = [digest for digest, record in records.items() if record["pending"]]
        if pending or self._tokens._needs_migration:
            for digest in pending:
                self._tokens._burn_unlocked(records, digest)
            self._tokens._write_unlocked(records)
        self._registry._discard_unfinished()
        return records

    def exchange(
        self,
        token: str,
        csr_pem: bytes | None = None,
        display_name: str | None = None,
        agent_version: str | None = None,
    ) -> EnrollmentResponse | DeviceCredential:
        if not self._legacy:
            _token_digest(token)
            csr_pem = _validated_device_csr(csr_pem)
            display_name = _bounded_text("display_name", display_name, 128)
            agent_version = _bounded_text(
                "agent_version", agent_version, 32, visible_ascii=True
            )
            agent_id = str(uuid4())
            try:
                certificate = self._certificate_authority.sign_device_csr(
                    csr_pem, agent_id
                )
                chain_pem = self._certificate_authority.ca_pem()
            except (OSError, ValueError) as exc:
                raise SigningUnavailable("signer unavailable") from exc
            detail = self._registry.consume_token_and_enroll(
                token,
                certificate,
                display_name,
                agent_version,
                "enrollment-service",
                str(uuid4()),
                agent_id=agent_id,
            )
            return EnrollmentResponse(
                detail.agent_id,
                certificate.certificate_pem,
                chain_pem,
                certificate.serial,
                certificate.certificate_not_after,
            )

        return self._legacy_exchange(token)

    def renew(
        self, agent_id: str, fingerprint: str, csr_pem: bytes
    ) -> EnrollmentResponse:
        if self._legacy:
            raise CertificateRejected("certificate renewal is unavailable")
        csr_pem = _validated_device_csr(csr_pem)
        current = self._registry.get_device(agent_id)
        if current is None:
            raise CertificateRejected("device not found")
        if current.revoked_at is not None:
            raise CertificateRejected("device is revoked")
        if current.certificate_fingerprint != fingerprint:
            raise CertificateRejected("current certificate does not match")
        try:
            certificate = self._certificate_authority.renew_device_csr(
                csr_pem, agent_id
            )
            chain_pem = self._certificate_authority.ca_pem()
        except (OSError, ValueError) as exc:
            raise SigningUnavailable("signer unavailable") from exc
        detail = self._registry.renew_certificate(
            agent_id,
            fingerprint,
            certificate,
            "certificate-renewal",
            str(uuid4()),
        )
        return EnrollmentResponse(
            detail.agent_id,
            certificate.certificate_pem,
            chain_pem,
            certificate.serial,
            certificate.certificate_not_after,
        )

    def _legacy_exchange(self, token: str) -> DeviceCredential:
        digest = self._tokens._token_hash(token, invalid=None)
        if digest is None:
            raise ValueError("invalid enrollment token")
        with self._lock, self._tokens._lock:
            records = self._reconcile_unlocked()
            if not self._tokens._is_valid_unlocked(records, digest):
                raise ValueError("invalid or expired enrollment token")
            self._tokens._mark_pending_unlocked(records, digest)
            self._tokens._write_unlocked(records)
            credential = None
            try:
                credential = self._registry._stage(digest)
                self._tokens._burn_unlocked(records, digest)
                self._tokens._write_unlocked(records)
                self._registry._activate(credential)
                self._registry._finalize(credential)
            except Exception as error:
                if credential is not None and self._credential_is_active(credential):
                    return credential
                cleanup_error = self._discard_after_failure()
                self._note_cleanup_error(error, cleanup_error)
                raise
            return credential

    def _discard_after_failure(self) -> Exception | None:
        try:
            self._registry._discard_unfinished()
        except Exception as error:  # noqa: BLE001 - preserve the token-store failure
            return error
        return None

    def _credential_is_active(self, credential: DeviceCredential) -> bool:
        try:
            return (
                self._registry.get(credential.agent_id, credential.key_id)
                == credential.secret
            )
        except Exception:  # noqa: BLE001 - storage uncertainty stays fail closed
            return False

    @staticmethod
    def _note_cleanup_error(error: Exception, cleanup_error: Exception | None) -> None:
        if cleanup_error is not None and hasattr(error, "add_note"):
            error.add_note(f"enrollment cleanup also failed: {cleanup_error}")


def _bounded_text(name, value, limit, *, visible_ascii=False):
    valid = type(value) is str and 0 < len(value) <= limit
    if visible_ascii:
        valid = valid and all(0x21 <= ord(character) <= 0x7E for character in value)
    else:
        valid = valid and value.isprintable()
    if not valid:
        raise ValueError(f"invalid {name}")
    return value


def _validated_device_csr(csr_pem):
    if type(csr_pem) is not bytes or len(csr_pem) > _MAX_JSON_SIZE:
        raise ValueError("invalid CSR")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise ValueError("invalid CSR") from exc
    key = csr.public_key()
    attributes = list(csr.subject)
    if (
        not csr.is_signature_valid
        or not isinstance(key, ec.EllipticCurvePublicKey)
        or not isinstance(key.curve, ec.SECP256R1)
        or len(attributes) != 1
        or attributes[0].oid != NameOID.COMMON_NAME
        or not 1 <= len(attributes[0].value) <= 128
        or not attributes[0].value.isprintable()
    ):
        raise ValueError("invalid CSR")
    return csr.public_bytes(serialization.Encoding.PEM)


def _server_context(certfile: os.PathLike[str] | str, keyfile: os.PathLike[str] | str):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    return context


def _valid_identity(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MAX_ID_SIZE


class ManagedServer:
    def __init__(
        self,
        host: str,
        port: int,
        certfile: os.PathLike[str] | str,
        keyfile: os.PathLike[str] | str,
        registry: DeviceRegistry,
        *,
        initial_ping_delay: float = 10.0,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        max_workers: int = 32,
        handshake_timeout: float = 0.5,
    ) -> None:
        for name, value in (
            ("initial_ping_delay", initial_ping_delay),
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
        self._context = _server_context(certfile, keyfile)
        self._registry = registry
        self._initial_ping_delay = initial_ping_delay
        self._ping_interval = ping_interval
        self._pong_timeout = pong_timeout
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if not isinstance(handshake_timeout, (int, float)) or handshake_timeout <= 0:
            raise ValueError("handshake_timeout must be positive")
        self._handshake_timeout = handshake_timeout
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._startup = threading.Condition()
        self._serve_requested = False
        self._connections: set[socket.socket] = set()
        self._threads: set[threading.Thread] = set()
        self._heartbeats: dict[str, threading.Event] = {}
        self._accept_thread: threading.Thread | None = None
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    def serve_forever(self) -> Callable[[threading.Event], None]:
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
                    conn, _ = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not self._worker_slots.acquire(blocking=False):
                    conn.close()
                    continue
                thread = threading.Thread(
                    target=self._serve_connection,
                    args=(conn,),
                    name="managed-session",
                    daemon=True,
                )
                with self._lock:
                    if self._stopped.is_set() or stop_event.is_set():
                        conn.close()
                        break
                    self._connections.add(conn)
                    self._threads.add(thread)
                    thread.start()
        finally:
            self._close_listener()

    def _serve_connection(self, raw_conn: socket.socket) -> None:
        conn = raw_conn
        try:
            raw_conn.settimeout(self._handshake_timeout)
            conn = self._context.wrap_socket(raw_conn, server_side=True)
            with self._lock:
                self._connections.discard(raw_conn)
                if self._stopped.is_set():
                    conn.close()
                    return
                self._connections.add(conn)
            self._authenticate_and_heartbeat(conn)
        except (ConnectionError, OSError, ssl.SSLError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self._lock:
                self._connections.discard(raw_conn)
                self._connections.discard(conn)
                self._threads.discard(threading.current_thread())
            self._worker_slots.release()

    def _authenticate_and_heartbeat(self, conn) -> None:
        try:
            hello = recv_json_frame(conn)
            if (
                set(hello) != {"type", "version", "agent_id", "key_id"}
                or hello["type"] != "HELLO"
                or type(hello["version"]) is not int
                or hello["version"] != 1
                or not _valid_identity(hello["agent_id"])
                or not _valid_identity(hello["key_id"])
            ):
                raise ValueError("invalid HELLO")

            nonce = secrets.token_bytes(32)
            send_json_frame(
                conn,
                {
                    "type": "CHALLENGE",
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                },
            )
            proof_message = recv_json_frame(conn)
            if (
                set(proof_message) != {"type", "proof"}
                or proof_message["type"] != "AUTH_PROOF"
                or not isinstance(proof_message["proof"], str)
                or len(proof_message["proof"]) > 64
            ):
                raise ValueError("invalid AUTH_PROOF")
            proof = base64.b64decode(proof_message["proof"], validate=True)
            if len(proof) != 32:
                raise ValueError("invalid AUTH_PROOF")
            secret = self._registry.get(hello["agent_id"], hello["key_id"])
            accepted = secret is not None and verify_proof(
                secret,
                1,
                hello["agent_id"],
                hello["key_id"],
                nonce,
                proof,
            )
        except (KeyError, TypeError, ValueError):
            accepted = False
            hello = None

        send_json_frame(conn, {"type": "AUTH_OK" if accepted else "AUTH_REJECT"})
        if not accepted:
            return

        heartbeat = threading.Event()
        with self._lock:
            self._heartbeats[hello["agent_id"]] = heartbeat
        try:
            if self._stopped.wait(self._initial_ping_delay):
                return
            while not self._stopped.is_set():
                _send_frame(conn, b"PING", max_size=4)
                if _recv_frame(conn, timeout=self._pong_timeout, max_size=4) != b"PONG":
                    return
                heartbeat.set()
                if self._stopped.wait(self._ping_interval):
                    return
        finally:
            with self._lock:
                if self._heartbeats.get(hello["agent_id"]) is heartbeat:
                    del self._heartbeats[hello["agent_id"]]

    def wait_for_heartbeat(self, agent_id: str, timeout: float) -> bool:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            with self._lock:
                heartbeat = self._heartbeats.get(agent_id)
            if heartbeat is not None:
                return heartbeat.wait(max(0.0, deadline - monotonic()))
            if self._stopped.wait(min(0.01, max(0.0, deadline - monotonic()))):
                return False
        return False

    def stop(self, timeout: float = 5.0) -> None:
        deadline = monotonic() + timeout
        self._stopped.set()
        self._close_listener()
        with self._startup:
            while self._serve_requested and self._accept_thread is None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                self._startup.wait(remaining)
            accept_thread = self._accept_thread
        with self._lock:
            connections = list(self._connections)
            threads = list(self._threads)
        if accept_thread is not None:
            threads.append(accept_thread)
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        current = threading.current_thread()
        for thread in threads:
            if thread is not current:
                thread.join(max(0.0, deadline - monotonic()))

    def _close_listener(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


class _EnrollmentHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        address,
        handler,
        context,
        enrollment_service,
        handshake_timeout,
        max_workers,
    ):
        self._context = context
        self.enrollment_service = enrollment_service
        self._handshake_timeout = handshake_timeout
        self._closing = threading.Event()
        self._connections = set()
        self._connections_lock = threading.Lock()
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def get_request(self):
        conn, address = super().get_request()
        try:
            conn = self._context.wrap_socket(
                conn, server_side=True, do_handshake_on_connect=False
            )
        except (OSError, ValueError):
            conn.close()
            raise
        with self._connections_lock:
            self._connections.add(conn)
        return conn, address

    def finish_handshake(self, conn):
        conn.setblocking(False)
        deadline = monotonic() + self._handshake_timeout
        while not self._closing.is_set():
            try:
                conn.do_handshake()
                conn.settimeout(10.0)
                return
            except ssl.SSLWantReadError:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("TLS handshake timed out")
                select.select([conn], [], [], min(0.1, remaining))
            except ssl.SSLWantWriteError:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("TLS handshake timed out")
                select.select([], [conn], [], min(0.1, remaining))
        raise ConnectionAbortedError("server is shutting down")

    def handle_error(self, request, client_address):
        pass

    def begin_shutdown(self):
        self._closing.set()

    def close_request(self, request):
        with self._connections_lock:
            self._connections.discard(request)
        super().close_request(request)

    def close_connections(self):
        with self._connections_lock:
            connections = list(self._connections)
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def server_close(self):
        self.begin_shutdown()
        self.close_connections()
        super().server_close()


class _EnrollmentHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Enrollment"
    sys_version = ""

    def setup(self):
        self.server.finish_handshake(self.request)
        super().setup()

    def do_GET(self):
        self._reply(405, {"error": "method not allowed"})

    def do_POST(self):
        if self.path not in {"/v1/enroll", "/v1/renew"}:
            self._reply(404, {"error": "not found"})
            return
        legacy = self.server.enrollment_service._legacy
        peer = None
        if self.path == "/v1/renew":
            try:
                peer = _peer_device_identity(self.connection)
            except ValueError:
                self._reply(403, {"error": "certificate rejected"})
                return
        try:
            if (
                self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                != "application/json"
            ):
                raise ValueError
            content_length = int(self.headers.get("Content-Length", ""))
            if not 0 < content_length <= _MAX_JSON_SIZE:
                raise ValueError
            payload = transport.decode_json_payload(
                self.rfile.read(content_length), max_size=_MAX_JSON_SIZE
            )
            if legacy:
                if self.path != "/v1/enroll" or set(payload) != {"token"}:
                    raise ValueError
            else:
                expected = (
                    {"agent_version", "csr_pem", "display_name", "token"}
                    if self.path == "/v1/enroll"
                    else {"csr_pem"}
                )
                if set(payload) != expected:
                    raise ValueError
                csr_pem = _decode_csr(payload["csr_pem"])
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid request"})
            return
        try:
            if legacy:
                response = self.server.enrollment_service.exchange(payload["token"])
            elif self.path == "/v1/enroll":
                response = self.server.enrollment_service.exchange(
                    payload["token"],
                    csr_pem,
                    payload["display_name"],
                    payload["agent_version"],
                )
            else:
                response = self.server.enrollment_service.renew(*peer, csr_pem)
        except EnrollmentTokenRejected:
            self._reply(403, {"error": "invalid enrollment token"})
            return
        except CertificateRejected:
            self._reply(403, {"error": "certificate rejected"})
            return
        except ValueError:
            self._reply(
                401 if legacy else 400,
                {"error": "invalid enrollment token" if legacy else "invalid request"},
            )
            return
        except (SigningUnavailable, ManagedRegistryError, OSError, sqlite3.Error):
            self._reply(503, {"error": "service unavailable"})
            return
        if legacy:
            message = {
                "agent_id": response.agent_id,
                "key_id": response.key_id,
                "secret": base64.b64encode(response.secret).decode("ascii"),
            }
        else:
            message = {
                "agent_id": response.agent_id,
                "certificate_pem": base64.b64encode(response.certificate_pem).decode(
                    "ascii"
                ),
                "chain_pem": base64.b64encode(response.chain_pem).decode("ascii"),
                "certificate_serial": response.certificate_serial,
                "certificate_not_after": response.certificate_not_after,
            }
        self._reply(
            201,
            message,
        )

    def _reply(self, status: int, message: Mapping) -> None:
        payload = transport.encode_json_payload(message)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class EnrollmentServer:
    def __init__(
        self,
        host: str,
        port: int,
        certfile: os.PathLike[str] | str,
        keyfile: os.PathLike[str] | str,
        service: EnrollmentService,
        *,
        handshake_timeout: float = 10.0,
        max_workers: int = 32,
    ) -> None:
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if (
            not isinstance(handshake_timeout, (int, float))
            or isinstance(handshake_timeout, bool)
            or not math.isfinite(handshake_timeout)
            or handshake_timeout <= 0
        ):
            raise ValueError("handshake_timeout must be positive")
        self._server = _EnrollmentHTTPServer(
            (host, port),
            _EnrollmentHandler,
            _enrollment_server_context(certfile, keyfile, service.ca_pem()),
            service,
            handshake_timeout,
            max_workers,
        )
        self.port = self._server.server_address[1]

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.begin_shutdown()
        self._server.close_connections()
        self._server.shutdown()

    def server_close(self) -> None:
        self._server.server_close()


def _enrollment_server_context(certfile, keyfile, ca_pem):
    context = _server_context(certfile, keyfile)
    if ca_pem is not None:
        context.load_verify_locations(cadata=ca_pem.decode("ascii"))
        context.verify_mode = ssl.CERT_OPTIONAL
    return context


def _decode_csr(value):
    if type(value) is not str or len(value) > _MAX_JSON_SIZE:
        raise ValueError("invalid CSR")
    try:
        der = base64.b64decode(value.encode("ascii"), validate=True)
        if not der or len(der) > _MAX_JSON_SIZE:
            raise ValueError
        csr = x509.load_der_x509_csr(der)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("invalid CSR") from exc
    return csr.public_bytes(serialization.Encoding.PEM)


def _peer_device_identity(connection):
    der = connection.getpeercert(binary_form=True)
    if not der:
        raise ValueError("client certificate required")
    try:
        certificate = x509.load_der_x509_certificate(der)
        uris = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.UniformResourceIdentifier)
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise ValueError("invalid client certificate") from exc
    prefix = "urn:phantomlink:agent:"
    if len(uris) != 1 or not uris[0].startswith(prefix):
        raise ValueError("invalid client certificate")
    agent_id = uris[0][len(prefix) :]
    try:
        if str(UUID(agent_id)) != agent_id:
            raise ValueError
    except (AttributeError, ValueError) as exc:
        raise ValueError("invalid client certificate") from exc
    return agent_id, certificate.fingerprint(hashes.SHA256()).hex()


def _store_services(path: os.PathLike[str] | str):
    root = Path(path)
    registry = ManagedRegistry(root / "managed.db")
    registry.initialize()
    return registry


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m C2.managed_auth")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue-token")
    issue.add_argument("--store", default="managed-store")
    issue.add_argument("--ttl", type=float, default=600)
    listing = commands.add_parser("list-devices")
    listing.add_argument("--store", default="managed-store")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--store", default="managed-store")
    revoke.add_argument("--agent-id", required=True)
    revoke.add_argument("--key-id", help="retained Phase 1 argument; ignored")
    revoke.add_argument("--reason", default="operator CLI request")
    args = parser.parse_args(argv)

    registry = _store_services(args.store)
    if args.command == "issue-token":
        print(registry.issue_token(args.ttl))
        return 0
    if args.command == "list-devices":
        print(
            json.dumps(
                [asdict(device) for device in registry.list_device_records()],
                separators=(",", ":"),
            )
        )
        return 0
    result = registry.revoke_device(
        args.agent_id, "operator-cli", args.reason, str(uuid4())
    )
    if result.code in {"REVOKED", "ALREADY_REVOKED"}:
        print("revoked")
        return 0
    print("not found")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
