from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from time import time
from uuid import uuid4

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
    "EnrollmentService",
    "EnrollmentStore",
    "build_proof",
    "canonical_auth_input",
    "verify_proof",
]


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
        self._lock = threading.Lock()
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
            return {}
        try:
            raw = _read_private_file(self.path, self._acl_inspector)
            records = json.loads(raw.decode("utf-8"))
            if not isinstance(records, dict):
                raise TypeError
            for digest, record in records.items():
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                    or not isinstance(record, dict)
                    or set(record) != {"expires_at", "consumed", "pending"}
                    or not isinstance(record["expires_at"], (int, float))
                    or isinstance(record["expires_at"], bool)
                    or not math.isfinite(record["expires_at"])
                    or type(record["consumed"]) is not bool
                    or type(record["pending"]) is not bool
                    or (record["consumed"] and record["pending"])
                ):
                    raise ValueError
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
        self._lock = threading.Lock()
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
            if len(active_records) != len(records):
                self._write_unlocked(active_records)

    def _read_unlocked(self) -> dict[tuple[str, str], dict]:
        if not self.path.exists():
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
            for device in devices:
                if not isinstance(device, dict) or set(device) != {
                    "active",
                    "agent_id",
                    "key_id",
                    "pending_digest",
                    "secret",
                }:
                    raise ValueError
                agent_id = device["agent_id"]
                key_id = device["key_id"]
                active = device["active"]
                pending_digest = device["pending_digest"]
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


class EnrollmentService:
    def __init__(self, tokens: EnrollmentStore, registry: DeviceRegistry) -> None:
        self._tokens = tokens
        self._registry = registry
        self._lock = threading.Lock()
        self.reconcile()

    def reconcile(self) -> None:
        with self._lock, self._tokens._lock:
            self._reconcile_unlocked()

    def _reconcile_unlocked(self) -> dict[str, dict]:
        records = self._tokens._read_unlocked()
        pending = [digest for digest, record in records.items() if record["pending"]]
        if pending:
            for digest in pending:
                self._tokens._burn_unlocked(records, digest)
            self._tokens._write_unlocked(records)
        self._registry._discard_unfinished()
        return records

    def exchange(self, token: str) -> DeviceCredential:
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
