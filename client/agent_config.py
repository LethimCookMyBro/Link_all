from __future__ import annotations

import base64
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping


AclInspector = Callable[[Path | BinaryIO], Mapping[str, bool]]
AclApplier = Callable[[Path], None]
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


@dataclass(frozen=True)
class DeviceCredential:
    agent_id: str
    key_id: str
    secret: bytes


@dataclass(frozen=True)
class AgentConfig:
    controller_host: str
    managed_port: int
    enrollment_port: int
    tls_cert_sha256: str
    agent_id: str = ""
    key_id: str = ""
    connect_timeout: float = 5.0
    io_poll_interval: float = 1.0
    controller_ping_interval: float = 30.0
    controller_pong_timeout: float = 10.0
    agent_read_deadline: float = 90.0
    retry_base: float = 1.0
    retry_max: float = 30.0
    retry_jitter: float = 0.2
    log_path: str = "managed-agent.log"
    log_max_bytes: int = 1048576
    log_backup_count: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.controller_host, str) or not self.controller_host.strip():
            raise ValueError("controller_host must not be empty")
        for name in ("managed_port", "enrollment_port"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError(f"{name} must be in 1..65535")
        if not isinstance(self.tls_cert_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.tls_cert_sha256
        ):
            raise ValueError("tls_cert_sha256 must be 64 lowercase hexadecimal characters")
        for name in (
            "connect_timeout",
            "controller_ping_interval",
            "controller_pong_timeout",
            "agent_read_deadline",
            "retry_base",
            "retry_max",
        ):
            if not _is_finite_number(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not _is_finite_number(self.io_poll_interval) or not 0 < self.io_poll_interval <= 1:
            raise ValueError("io_poll_interval must be in (0, 1]")
        if self.retry_max < self.retry_base:
            raise ValueError("retry_max must be at least retry_base")
        if not _is_finite_number(self.retry_jitter) or not 0 <= self.retry_jitter <= 1:
            raise ValueError("retry_jitter must be in [0, 1]")
        if self.agent_read_deadline < 3 * self.controller_ping_interval:
            raise ValueError("agent_read_deadline must be at least three controller_ping_interval values")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AgentConfig:
        if not isinstance(data, Mapping):
            raise ValueError("config must be a mapping")
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise ValueError(f"invalid config fields: {exc}") from exc


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _windows_modules():
    try:
        import ntsecuritycon
        import win32api
        import win32con
        import win32crypt
        import win32security
    except ImportError as exc:
        raise RuntimeError("Windows DPAPI and ACL APIs are unavailable") from exc
    return ntsecuritycon, win32api, win32con, win32crypt, win32security


def _current_user_sid(win32api, win32con, win32security):
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    return win32security.GetTokenInformation(token, win32security.TokenUser)[0]


def _inspect_windows_acl(path: Path) -> Mapping[str, bool]:
    modules = _windows_modules()
    win32security = modules[4]
    descriptor = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    return _summarize_windows_acl(descriptor, modules)


def _inspect_windows_handle(file: BinaryIO) -> Mapping[str, bool]:
    try:
        import msvcrt
    except ImportError as exc:
        raise RuntimeError("Windows handle APIs are unavailable") from exc
    modules = _windows_modules()
    win32security = modules[4]
    descriptor = win32security.GetSecurityInfo(
        msvcrt.get_osfhandle(file.fileno()),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    return _summarize_windows_acl(descriptor, modules)


def _summarize_windows_acl(descriptor, modules) -> Mapping[str, bool]:
    ntsecuritycon, win32api, win32con, _, win32security = modules
    owner = descriptor.GetSecurityDescriptorOwner()
    dacl = descriptor.GetSecurityDescriptorDacl()
    broad_sids = {
        "everyone_write": win32security.CreateWellKnownSid(win32security.WinWorldSid, None),
        "builtin_users_write": win32security.CreateWellKnownSid(
            win32security.WinBuiltinUsersSid, None
        ),
        "authenticated_users_write": win32security.CreateWellKnownSid(
            win32security.WinAuthenticatedUserSid, None
        ),
    }
    result = {"owner": owner == _current_user_sid(win32api, win32con, win32security)}
    if dacl is None:
        result.update({name: True for name in broad_sids})
        return result

    write_mask = (
        ntsecuritycon.FILE_WRITE_DATA
        | ntsecuritycon.FILE_APPEND_DATA
        | ntsecuritycon.FILE_WRITE_EA
        | ntsecuritycon.FILE_WRITE_ATTRIBUTES
        | ntsecuritycon.DELETE
        | ntsecuritycon.WRITE_DAC
        | ntsecuritycon.WRITE_OWNER
        | win32con.GENERIC_WRITE
        | win32con.GENERIC_ALL
    )
    allowed_types = {
        ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE,
        ntsecuritycon.ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
        ntsecuritycon.ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
        ntsecuritycon.ACCESS_ALLOWED_COMPOUND_ACE_TYPE,
        ntsecuritycon.ACCESS_ALLOWED_OBJECT_ACE_TYPE,
    }
    denied_types = {
        ntsecuritycon.ACCESS_DENIED_ACE_TYPE,
        ntsecuritycon.ACCESS_DENIED_CALLBACK_ACE_TYPE,
        ntsecuritycon.ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
        ntsecuritycon.ACCESS_DENIED_OBJECT_ACE_TYPE,
    }
    non_grant_types = {
        ntsecuritycon.SYSTEM_ALARM_ACE_TYPE,
        ntsecuritycon.SYSTEM_ALARM_CALLBACK_ACE_TYPE,
        ntsecuritycon.SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE,
        ntsecuritycon.SYSTEM_ALARM_OBJECT_ACE_TYPE,
        ntsecuritycon.SYSTEM_AUDIT_ACE_TYPE,
        ntsecuritycon.SYSTEM_AUDIT_CALLBACK_ACE_TYPE,
        ntsecuritycon.SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE,
        ntsecuritycon.SYSTEM_AUDIT_OBJECT_ACE_TYPE,
        ntsecuritycon.SYSTEM_MANDATORY_LABEL_ACE_TYPE,
    }
    result.update({name: False for name in broad_sids})
    result["unknown_allow_write"] = False
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        ace_type = ace[0][0]
        if not ace[1] & write_mask or ace_type in denied_types | non_grant_types:
            continue
        for name, broad_sid in broad_sids.items():
            if any(value == broad_sid for value in ace[2:]):
                result[name] = True
        if ace_type not in allowed_types:
            result["unknown_allow_write"] = True
    return result


def _apply_private_acl(path: Path) -> None:
    ntsecuritycon, win32api, win32con, _, win32security = _windows_modules()
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        ntsecuritycon.FILE_ALL_ACCESS,
        _current_user_sid(win32api, win32con, win32security),
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )


def _validate_acl(path: Path, acl: Mapping[str, bool]) -> None:
    broad_write = any(
        acl.get(name, False)
        for name in (
            "world_write",
            "everyone_write",
            "builtin_users_write",
            "authenticated_users_write",
            "unknown_allow_write",
        )
    )
    if acl.get("owner") is not True or broad_write:
        raise ValueError(f"ACL is not private to the current Windows user: {path}")


def validate_private_file(path: os.PathLike[str] | str, acl_inspector: AclInspector | None = None) -> None:
    file_path = Path(path)
    try:
        mode = file_path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"private file is unavailable: {file_path}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"private path must be a regular file: {file_path}")
    acl = (acl_inspector or _inspect_windows_acl)(file_path)
    _validate_acl(file_path, acl)


def _read_private_file(path: Path, acl_inspector: AclInspector | None) -> bytes:
    try:
        with path.open("rb") as file:
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"private path must be a regular file: {path}")
            acl = (acl_inspector or _inspect_windows_handle)(file)
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or not os.path.samestat(opened, current):
                raise ValueError(f"private file changed during ACL validation: {path}")
            _validate_acl(path, acl)
            return file.read()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"private file is unavailable: {path}") from exc


def load_config(
    path: os.PathLike[str] | str, acl_inspector: AclInspector | None = None
) -> AgentConfig:
    file_path = Path(path)
    try:
        data = json.loads(_read_private_file(file_path, acl_inspector).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid config file: {file_path}") from exc
    return AgentConfig.from_mapping(data)


def _atomic_private_write(path: Path, data: bytes, acl_applier: AclApplier | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if acl_applier is not None:
            acl_applier(temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None
        if acl_applier is not None:
            acl_applier(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_identity(
    path: os.PathLike[str] | str,
    agent_id: str,
    key_id: str,
    *,
    acl_applier: AclApplier | None = None,
) -> None:
    if not agent_id or not key_id:
        raise ValueError("agent_id and key_id must not be empty")
    payload = json.dumps(
        {"agent_id": agent_id, "key_id": key_id}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _atomic_private_write(Path(path), payload, acl_applier or _apply_private_acl)


class _DpapiProtector:
    def __init__(self) -> None:
        self._win32crypt = _windows_modules()[3]

    def protect(self, data: bytes) -> bytes:
        return self._win32crypt.CryptProtectData(
            data,
            "Managed background agent credential",
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
        )

    def unprotect(self, data: bytes) -> bytes:
        return self._win32crypt.CryptUnprotectData(
            data, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN
        )[1]


class DpapiCredentialStore:
    def __init__(
        self,
        path: os.PathLike[str] | str,
        protector=None,
        *,
        acl_inspector: AclInspector | None = None,
        acl_applier: AclApplier | None = None,
    ) -> None:
        self.path = Path(path)
        self._test_boundary = protector is not None
        self._protector = protector or _DpapiProtector()
        self._acl_inspector = acl_inspector
        self._acl_applier = acl_applier if acl_applier is not None else (
            None if self._test_boundary else _apply_private_acl
        )

    def load(self) -> DeviceCredential | None:
        if not self.path.exists():
            return None
        if not self._test_boundary or self._acl_inspector is not None:
            protected = _read_private_file(self.path, self._acl_inspector)
        else:
            protected = self.path.read_bytes()
        try:
            raw = self._protector.unprotect(protected)
            data = json.loads(raw.decode("utf-8"))
            return DeviceCredential(
                agent_id=data["agent_id"],
                key_id=data["key_id"],
                secret=base64.b64decode(data["secret"], validate=True),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid credential file: {self.path}") from exc

    def save(self, credential: DeviceCredential) -> None:
        if not isinstance(credential, DeviceCredential):
            raise TypeError("credential must be a DeviceCredential")
        raw = json.dumps(
            {
                "agent_id": credential.agent_id,
                "key_id": credential.key_id,
                "secret": base64.b64encode(credential.secret).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_private_write(self.path, self._protector.protect(raw), self._acl_applier)

    def delete(self) -> None:
        if self.path.exists() and (not self._test_boundary or self._acl_inspector is not None):
            validate_private_file(self.path, self._acl_inspector)
        self.path.unlink(missing_ok=True)
