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
        self._burned: set[str] = set()
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
        try:
            return hashlib.sha256(token.encode("ascii")).hexdigest()
        except (AttributeError, UnicodeEncodeError):
            if invalid is ...:
                raise ValueError("token must be an ASCII string")
            return invalid

    def _is_valid_unlocked(self, records: Mapping[str, dict], digest: str) -> bool:
        record = records.get(digest)
        return bool(
            record
            and digest not in self._burned
            and not record["consumed"]
            and self._now() < record["expires_at"]
        )

    def _burn_unlocked(self, records: dict[str, dict], digest: str) -> None:
        self._burned.add(digest)
        records[digest]["consumed"] = True

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
                    or set(record) != {"expires_at", "consumed"}
                    or not isinstance(record["expires_at"], (int, float))
                    or isinstance(record["expires_at"], bool)
                    or not math.isfinite(record["expires_at"])
                    or type(record["consumed"]) is not bool
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
        self._blocked: set[tuple[str, str]] = set()
        self._acl_inspector = acl_inspector
        self._acl_applier = (
            acl_applier
            if acl_applier is not None
            else (None if self._test_boundary else _apply_private_acl)
        )

    def enroll(self) -> DeviceCredential:
        return self._create(active=True)

    def get(self, agent_id: str, key_id: str) -> bytes | None:
        identity = (agent_id, key_id)
        with self._lock:
            if identity in self._blocked:
                return None
            record = self._read_unlocked().get(identity)
            if record is None or not record["active"]:
                return None
            return record["secret"]

    def revoke(self, agent_id: str, key_id: str) -> bool:
        identity = (agent_id, key_id)
        with self._lock:
            records = self._read_unlocked()
            if identity not in records:
                return False
            self._blocked.add(identity)
            del records[identity]
            self._write_unlocked(records)
            return True

    def _stage(self) -> DeviceCredential:
        return self._create(active=False)

    def _create(self, *, active: bool) -> DeviceCredential:
        credential = DeviceCredential(
            str(uuid4()), str(uuid4()), secrets.token_bytes(32)
        )
        with self._lock:
            records = self._read_unlocked()
            records[(credential.agent_id, credential.key_id)] = {
                "secret": credential.secret,
                "active": active,
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
                    "secret",
                }:
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
                secret = base64.b64decode(device["secret"], validate=True)
                if len(secret) != 32 or (agent_id, key_id) in records:
                    raise ValueError
                records[(agent_id, key_id)] = {"secret": secret, "active": active}
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

    def exchange(self, token: str) -> DeviceCredential:
        digest = self._tokens._token_hash(token, invalid=None)
        if digest is None:
            raise ValueError("invalid enrollment token")
        with self._lock, self._tokens._lock:
            records = self._tokens._read_unlocked()
            if not self._tokens._is_valid_unlocked(records, digest):
                raise ValueError("invalid or expired enrollment token")
            try:
                credential = self._registry._stage()
            except Exception as error:
                cleanup_error = self._burn_after_failed_exchange(records, digest)
                self._note_cleanup_error(error, cleanup_error)
                raise

            self._tokens._burn_unlocked(records, digest)
            try:
                self._tokens._write_unlocked(records)
            except Exception as error:
                cleanup_error = self._revoke_after_failure(credential)
                self._note_cleanup_error(error, cleanup_error)
                raise

            try:
                self._registry._activate(credential)
            except Exception as error:
                cleanup_error = self._revoke_after_failure(credential)
                self._note_cleanup_error(error, cleanup_error)
                raise
            return credential

    def _burn_after_failed_exchange(
        self, records: dict[str, dict], digest: str
    ) -> Exception | None:
        self._tokens._burn_unlocked(records, digest)
        try:
            self._tokens._write_unlocked(records)
        except Exception as error:  # noqa: BLE001 - preserve the enrollment failure
            return error
        return None

    def _revoke_after_failure(self, credential: DeviceCredential) -> Exception | None:
        try:
            self._registry.revoke(credential.agent_id, credential.key_id)
        except Exception as error:  # noqa: BLE001 - preserve the token-store failure
            return error
        return None

    @staticmethod
    def _note_cleanup_error(error: Exception, cleanup_error: Exception | None) -> None:
        if cleanup_error is not None and hasattr(error, "add_note"):
            error.add_note(f"enrollment cleanup also failed: {cleanup_error}")
