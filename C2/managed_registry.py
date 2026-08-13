from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import shutil
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from client.agent_config import _apply_private_acl

SCHEMA_VERSION = 1
AUDIT_DETAIL_KEYS = frozenset(
    {
        "agent_version",
        "certificate_fingerprint",
        "certificate_serial",
        "peer_ip",
        "previous_session_id",
        "session_id",
        "status_code",
    }
)
AUDIT_DETAIL_LIMIT = 4096

__all__ = [
    "AUDIT_DETAIL_KEYS",
    "AUDIT_DETAIL_LIMIT",
    "ActionResult",
    "AuditEvent",
    "CertificateRejected",
    "DeviceDetail",
    "DeviceSummary",
    "EnrollmentTokenRejected",
    "IssuedDeviceCertificate",
    "LegacyStoreBackup",
    "ManagedRegistry",
    "ManagedRegistryError",
    "SchemaVersionRejected",
    "backup_phase1_stores",
    "utc_now",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DeviceSummary:
    agent_id: str
    display_name: str
    state: str
    last_vpn_ip: str | None
    last_seen_at: str | None
    certificate_not_after: str
    agent_version: str


@dataclass(frozen=True)
class DeviceDetail:
    agent_id: str
    display_name: str
    state: str
    last_vpn_ip: str | None
    last_seen_at: str | None
    certificate_not_after: str
    agent_version: str
    certificate_fingerprint: str
    certificate_serial: str
    enrolled_at: str
    revoked_at: str | None
    revocation_reason: str | None


@dataclass(frozen=True)
class AuditEvent:
    id: int
    occurred_at: str
    actor: str
    action: str
    target_agent_id: str | None
    result: str
    reason: str | None
    correlation_id: str
    details: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ActionResult:
    code: str
    message: str
    correlation_id: str


@dataclass(frozen=True)
class LegacyStoreBackup:
    source_name: str
    backup_path: Path
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class IssuedDeviceCertificate:
    certificate_pem: bytes
    fingerprint: str
    serial: str
    certificate_not_after: str


class ManagedRegistryError(Exception):
    pass


class SchemaVersionRejected(ManagedRegistryError):
    pass


class EnrollmentTokenRejected(ManagedRegistryError):
    pass


class CertificateRejected(ManagedRegistryError):
    pass


class ManagedRegistry:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = utc_now,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if (
            type(busy_timeout_ms) is not int
            or busy_timeout_ms <= 0
            or busy_timeout_ms > 5000
        ):
            raise ValueError("busy_timeout_ms must be from 1 through 5000")
        self.path = Path(path)
        self.now = now
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _apply_private_acl(self.path.parent)
        if self.path.exists():
            _apply_private_acl(self.path)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                highest = connection.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
                if highest is not None and highest > SCHEMA_VERSION:
                    raise SchemaVersionRejected(
                        f"database schema version {highest} is newer than supported version {SCHEMA_VERSION}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS devices (
                        agent_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        certificate_fingerprint TEXT UNIQUE NOT NULL,
                        certificate_serial TEXT UNIQUE NOT NULL,
                        certificate_not_after TEXT NOT NULL,
                        agent_version TEXT NOT NULL,
                        last_vpn_ip TEXT,
                        enrolled_at TEXT NOT NULL,
                        last_seen_at TEXT,
                        revoked_at TEXT,
                        revocation_reason TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS enrollment_tokens (
                        token_digest TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        consumed_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target_agent_id TEXT,
                        result TEXT NOT NULL,
                        reason TEXT,
                        correlation_id TEXT NOT NULL,
                        details_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS audit_events_occurred_at_idx ON audit_events(occurred_at DESC, id DESC)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, _format_time(self.now())),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        for path in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if path.exists():
                _apply_private_acl(path)

    def issue_token(self, ttl_seconds: float = 600.0) -> str:
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be positive")
        created = self.now()
        created_at = _format_time(created)
        expires_at = _format_time(created + timedelta(seconds=ttl_seconds))
        while True:
            token = secrets.token_urlsafe(32)
            try:
                with self._connection() as connection, _write_transaction(connection):
                    connection.execute(
                        "INSERT INTO enrollment_tokens VALUES (?, ?, ?, NULL)",
                        (_token_digest(token), created_at, expires_at),
                    )
                return token
            except sqlite3.IntegrityError:
                continue

    def consume_token_and_enroll(
        self,
        token: str,
        certificate: IssuedDeviceCertificate,
        display_name: str,
        agent_version: str,
        actor: str,
        correlation_id: str,
    ) -> DeviceDetail:
        try:
            digest = _token_digest(token)
        except ValueError as exc:
            raise EnrollmentTokenRejected("invalid or expired enrollment token") from exc
        _validate_certificate(certificate)
        display_name = _require_text("display_name", display_name, 128)
        agent_version = _require_text("agent_version", agent_version, 128)
        actor = _require_text("actor", actor, 128)
        correlation_id = _require_text("correlation_id", correlation_id, 128)
        occurred_at = _format_time(self.now())
        agent_id = str(uuid4())
        details_json = _audit_json(
            {
                "agent_version": agent_version,
                "certificate_fingerprint": certificate.fingerprint,
                "certificate_serial": certificate.serial,
            }
        )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = connection.execute(
                    "SELECT expires_at, consumed_at FROM enrollment_tokens WHERE token_digest = ?",
                    (digest,),
                ).fetchone()
                if (
                    record is None
                    or record["consumed_at"] is not None
                    or _parse_time(record["expires_at"]) <= self.now()
                ):
                    raise EnrollmentTokenRejected(
                        "invalid or expired enrollment token"
                    )
                connection.execute(
                    """
                    INSERT INTO devices(
                        agent_id, display_name, certificate_fingerprint,
                        certificate_serial, certificate_not_after, agent_version,
                        last_vpn_ip, enrolled_at, last_seen_at, revoked_at,
                        revocation_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL)
                    """,
                    (
                        agent_id,
                        display_name,
                        certificate.fingerprint,
                        certificate.serial,
                        certificate.certificate_not_after,
                        agent_version,
                        occurred_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        occurred_at, actor, action, target_agent_id, result,
                        reason, correlation_id, details_json
                    ) VALUES (?, ?, 'ENROLLMENT_SUCCEEDED', ?, 'SUCCEEDED', NULL, ?, ?)
                    """,
                    (occurred_at, actor, agent_id, correlation_id, details_json),
                )
                connection.execute(
                    "UPDATE enrollment_tokens SET consumed_at = ? WHERE token_digest = ?",
                    (occurred_at, digest),
                )
                detail = _device_from_row(
                    connection.execute(
                        "SELECT * FROM devices WHERE agent_id = ?", (agent_id,)
                    ).fetchone()
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise CertificateRejected(
                    "certificate identity is already enrolled"
                ) from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return detail

    def renew_certificate(
        self,
        agent_id: str,
        current_fingerprint: str,
        certificate: IssuedDeviceCertificate,
        actor: str,
        correlation_id: str,
    ) -> DeviceDetail:
        _validate_certificate(certificate)
        agent_id = _require_text("agent_id", agent_id, 128)
        current_fingerprint = _require_text(
            "current_fingerprint", current_fingerprint, 512
        )
        actor = _require_text("actor", actor, 128)
        correlation_id = _require_text("correlation_id", correlation_id, 128)
        occurred_at = _format_time(self.now())
        details_json = _audit_json(
            {
                "certificate_fingerprint": certificate.fingerprint,
                "certificate_serial": certificate.serial,
            }
        )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = connection.execute(
                    "SELECT certificate_fingerprint, revoked_at FROM devices WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                if record is None:
                    raise CertificateRejected("device not found")
                if record["revoked_at"] is not None:
                    raise CertificateRejected("device is revoked")
                if record["certificate_fingerprint"] != current_fingerprint:
                    raise CertificateRejected("current certificate does not match")
                connection.execute(
                    """
                    UPDATE devices
                    SET certificate_fingerprint = ?, certificate_serial = ?,
                        certificate_not_after = ?
                    WHERE agent_id = ?
                    """,
                    (
                        certificate.fingerprint,
                        certificate.serial,
                        certificate.certificate_not_after,
                        agent_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        occurred_at, actor, action, target_agent_id, result,
                        reason, correlation_id, details_json
                    ) VALUES (?, ?, 'CERTIFICATE_RENEWED', ?, 'SUCCEEDED', NULL, ?, ?)
                    """,
                    (occurred_at, actor, agent_id, correlation_id, details_json),
                )
                detail = _device_from_row(
                    connection.execute(
                        "SELECT * FROM devices WHERE agent_id = ?", (agent_id,)
                    ).fetchone()
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise CertificateRejected(
                    "certificate identity is already enrolled"
                ) from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return detail

    def get_device(self, agent_id: str) -> DeviceDetail | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM devices WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        return None if row is None else _device_from_row(row)

    def list_device_records(self) -> tuple[DeviceDetail, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM devices ORDER BY agent_id"
            ).fetchall()
        return tuple(_device_from_row(row) for row in rows)

    def list_audit_events(self, limit: int = 100) -> tuple[AuditEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 through 1000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_audit_from_row(row) for row in rows)

    def append_audit(
        self,
        *,
        actor: str,
        action: str,
        target_agent_id: str | None,
        result: str,
        reason: str | None,
        correlation_id: str,
        details: Mapping[str, str | int | float | bool | None],
    ) -> AuditEvent:
        actor = _require_text("actor", actor, 128)
        action = _require_text("action", action, 128)
        if target_agent_id is not None:
            target_agent_id = _require_text("target_agent_id", target_agent_id, 128)
        result = _require_text("result", result, 128)
        if reason is not None:
            reason = _require_text("reason", reason, 512, allow_empty=True)
        correlation_id = _require_text("correlation_id", correlation_id, 128)
        occurred_at = _format_time(self.now())
        details_json = _audit_json(details)
        with self._connection() as connection, _write_transaction(connection):
            cursor = connection.execute(
                """
                INSERT INTO audit_events(
                    occurred_at, actor, action, target_agent_id, result, reason,
                    correlation_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at,
                    actor,
                    action,
                    target_agent_id,
                    result,
                    reason,
                    correlation_id,
                    details_json,
                ),
            )
            event_id = cursor.lastrowid
        return AuditEvent(
            event_id,
            occurred_at,
            actor,
            action,
            target_agent_id,
            result,
            reason,
            correlation_id,
            _display_details(details_json),
        )

    def revoke_device(
        self, agent_id: str, actor: str, reason: str, correlation_id: str
    ) -> ActionResult:
        agent_id = _require_text("agent_id", agent_id, 128)
        actor = _require_text("actor", actor, 128)
        reason = _require_text("reason", reason, 512)
        correlation_id = _require_text("correlation_id", correlation_id, 128)
        occurred_at = _format_time(self.now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = connection.execute(
                    "SELECT revoked_at FROM devices WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                if record is None:
                    code = "NOT_FOUND"
                    message = "Device not found."
                elif record["revoked_at"] is not None:
                    code = "ALREADY_REVOKED"
                    message = "Device is already revoked."
                else:
                    connection.execute(
                        "UPDATE devices SET revoked_at = ?, revocation_reason = ? WHERE agent_id = ?",
                        (occurred_at, reason, agent_id),
                    )
                    code = "REVOKED"
                    message = "Device revoked."
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        occurred_at, actor, action, target_agent_id, result,
                        reason, correlation_id, details_json
                    ) VALUES (?, ?, 'REVOKED', ?, ?, ?, ?, '{}')
                    """,
                    (occurred_at, actor, agent_id, code, reason, correlation_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return ActionResult(code, message, correlation_id)

    def touch_last_seen(
        self, agent_id: str, vpn_ip: str, occurred_at: datetime | None = None
    ) -> None:
        agent_id = _require_text("agent_id", agent_id, 128)
        vpn_ip = _require_text("vpn_ip", vpn_ip, 128)
        timestamp = _format_time(self.now() if occurred_at is None else occurred_at)
        with self._connection() as connection, _write_transaction(connection):
            connection.execute(
                """
                UPDATE devices SET last_vpn_ip = ?, last_seen_at = ?
                WHERE agent_id = ? AND revoked_at IS NULL
                """,
                (vpn_ip, timestamp, agent_id),
            )

    def is_connection_allowed(
        self, agent_id: str, fingerprint: str, serial: str
    ) -> bool:
        with self._connection() as connection:
            return (
                connection.execute(
                    """
                    SELECT 1 FROM devices
                    WHERE agent_id = ? AND certificate_fingerprint = ?
                      AND certificate_serial = ? AND revoked_at IS NULL
                    """,
                    (agent_id, fingerprint, serial),
                ).fetchone()
                is not None
            )


def _format_time(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("time source must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("time must be UTC RFC3339")
    parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("time must be UTC RFC3339")
    return parsed


def _token_digest(token: str) -> str:
    if type(token) is not str or len(token) != 43:
        raise ValueError("token must be a canonical 32-byte URL-safe value")
    try:
        encoded = token.encode("ascii")
        decoded = base64.b64decode(encoded + b"=", altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("token must be a canonical 32-byte URL-safe value") from exc
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b"=") != encoded:
        raise ValueError("token must be a canonical 32-byte URL-safe value")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(
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


def _validate_certificate(certificate: IssuedDeviceCertificate) -> None:
    if not isinstance(certificate, IssuedDeviceCertificate):
        raise TypeError("certificate must be IssuedDeviceCertificate")
    if type(certificate.certificate_pem) is not bytes or not certificate.certificate_pem:
        raise ValueError("certificate_pem must be non-empty bytes")
    _require_text("fingerprint", certificate.fingerprint, 512)
    _require_text("serial", certificate.serial, 256)
    _parse_time(certificate.certificate_not_after)


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _validated_audit_details(
    details: Mapping[str, str | int | float | bool | None],
) -> dict[str, str | int | float | bool | None]:
    if not isinstance(details, Mapping):
        raise TypeError("audit details must be a mapping")
    if any(type(key) is not str or key not in AUDIT_DETAIL_KEYS for key in details):
        raise ValueError("forbidden audit detail")
    for value in details.values():
        if type(value) not in (str, int, float, bool, type(None)) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError("audit detail values must be scalar")
        if isinstance(value, str) and not value.isprintable():
            raise ValueError("audit detail strings must be display-safe")
    return dict(details)


def _audit_json(details: Mapping[str, str | int | float | bool | None]) -> str:
    payload = json.dumps(
        _validated_audit_details(details),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > AUDIT_DETAIL_LIMIT:
        raise ValueError("audit details too large")
    return payload


def _display_details(payload: str) -> tuple[tuple[str, str], ...]:
    def display(value) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    details = _validated_audit_details(json.loads(payload))
    return tuple((key, display(value)) for key, value in details.items())


def _device_from_row(row: sqlite3.Row) -> DeviceDetail:
    state = (
        "REVOKED"
        if row["revoked_at"] is not None
        else "OFFLINE"
        if row["last_seen_at"] is not None
        else "ENROLLED"
    )
    return DeviceDetail(
        row["agent_id"],
        row["display_name"],
        state,
        row["last_vpn_ip"],
        row["last_seen_at"],
        row["certificate_not_after"],
        row["agent_version"],
        row["certificate_fingerprint"],
        row["certificate_serial"],
        row["enrolled_at"],
        row["revoked_at"],
        row["revocation_reason"],
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        row["id"],
        row["occurred_at"],
        row["actor"],
        row["action"],
        row["target_agent_id"],
        row["result"],
        row["reason"],
        row["correlation_id"],
        _display_details(row["details_json"]),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_phase1_stores(
    store_root: Path,
    backup_root: Path,
    *,
    now: Callable[[], datetime] = utc_now,
) -> tuple[LegacyStoreBackup, ...]:
    store_root = Path(store_root)
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    _apply_private_acl(backup_root)
    backups = []
    manifest_stores = []
    for source_name in ("devices.bin", "tokens.json"):
        source = store_root / source_name
        if not source.is_file():
            continue
        destination = backup_root / source_name
        if source.resolve() == destination.resolve():
            raise ValueError("backup destination must differ from source")
        shutil.copyfile(source, destination)
        source_digest = _sha256(source)
        destination_digest = _sha256(destination)
        if source_digest != destination_digest:
            destination.unlink(missing_ok=True)
            raise OSError(f"backup verification failed for {source_name}")
        _apply_private_acl(destination)
        byte_count = destination.stat().st_size
        backup = LegacyStoreBackup(
            source_name, destination, byte_count, destination_digest
        )
        backups.append(backup)
        manifest_stores.append(
            {
                "backup_path": destination.relative_to(backup_root).as_posix(),
                "byte_count": byte_count,
                "sha256": destination_digest,
                "source_name": source_name,
            }
        )
    manifest = json.dumps(
        {"created_at": _format_time(now()), "stores": manifest_stores},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_manifest = backup_root / "manifest.json.tmp"
    temporary_manifest.write_bytes(manifest)
    _apply_private_acl(temporary_manifest)
    temporary_manifest.replace(backup_root / "manifest.json")
    _apply_private_acl(backup_root / "manifest.json")
    return tuple(backups)
