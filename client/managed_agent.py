from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import http.client
import json
import os
import ssl
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.agent_config import (
    DeviceCredential,
    DpapiCredentialStore,
    load_config,
    validate_private_file,
    write_identity,
)
from client.agent_logging import start_agent_logging
from client.agent_runtime import AgentRuntime, AuthRejected
from client.transport import decode_json_payload, encode_json_payload


class EnrollmentRejected(Exception):
    pass


def default_config_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "PhantomLink" / "managed-agent.json"


def _credential_path(config_path: str | os.PathLike[str]) -> Path:
    path = Path(config_path)
    return path.with_name(f"{path.stem}.credential")


def _identity_path(store) -> Path | None:
    path = getattr(store, "path", None)
    return None if path is None else Path(path).with_suffix(".identity.json")


def _tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _credential_from_response(payload) -> DeviceCredential:
    if not isinstance(payload, dict) or set(payload) != {
        "agent_id",
        "key_id",
        "secret",
    }:
        raise EnrollmentRejected("invalid enrollment response")
    agent_id, key_id, encoded_secret = (
        payload["agent_id"],
        payload["key_id"],
        payload["secret"],
    )
    if (
        not isinstance(agent_id, str)
        or not 0 < len(agent_id) <= 128
        or not isinstance(key_id, str)
        or not 0 < len(key_id) <= 128
        or not isinstance(encoded_secret, str)
    ):
        raise EnrollmentRejected("invalid enrollment response")
    try:
        secret = base64.b64decode(encoded_secret.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise EnrollmentRejected("invalid enrollment response") from exc
    if len(secret) != 32:
        raise EnrollmentRejected("invalid enrollment response")
    return DeviceCredential(agent_id, key_id, secret)


def enroll(config, token: str, store) -> DeviceCredential:
    if not isinstance(token, str) or not token:
        raise ValueError("enrollment token is required")
    connection = http.client.HTTPSConnection(
        config.controller_host,
        config.enrollment_port,
        timeout=config.connect_timeout,
        context=_tls_context(),
    )
    try:
        connection.connect()
        certificate = connection.sock.getpeercert(binary_form=True)
        pin = hashlib.sha256(certificate).hexdigest()
        if not hmac.compare_digest(pin, config.tls_cert_sha256):
            raise EnrollmentRejected("certificate pin mismatch")
        connection.request(
            "POST",
            "/v1/enroll",
            body=encode_json_payload({"token": token}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload_bytes = response.read(65537)
        if response.status != 201 or len(payload_bytes) > 65536:
            raise EnrollmentRejected("enrollment rejected")
        credential = _credential_from_response(
            decode_json_payload(payload_bytes, max_size=65536)
        )
        store.save(credential)
        identity = _identity_path(store)
        if identity is not None:
            write_identity(identity, credential.agent_id, credential.key_id)
        return credential
    except EnrollmentRejected:
        raise
    except (
        OSError,
        ssl.SSLError,
        ValueError,
        json.JSONDecodeError,
        http.client.HTTPException,
    ) as exc:
        raise EnrollmentRejected("enrollment failed") from exc
    finally:
        connection.close()


def _read_token_file(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("token file path must be absolute")
    validate_private_file(path)
    try:
        token = path.read_text("utf-8").strip()
    finally:
        path.unlink(missing_ok=True)
    if not token:
        raise ValueError("enrollment token is required")
    return token


def _is_pythonw() -> bool:
    return Path(sys.executable).name.lower().startswith("pythonw")


def parser():
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    enroll_cmd = commands.add_parser("enroll")
    enroll_cmd.add_argument("--config", default=str(default_config_path()))
    enroll_cmd.add_argument("--token-file")
    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("--config", default=str(default_config_path()))
    return root


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.command == "enroll":
        if _is_pythonw() or (not args.token_file and not sys.stdin.isatty()):
            return 2
        try:
            token = (
                _read_token_file(args.token_file)
                if args.token_file
                else getpass.getpass("Enrollment token: ")
            )
        except (EOFError, OSError, ValueError):
            return 2
        if not token:
            return 2
        try:
            config = load_config(args.config)
        except (OSError, RuntimeError, ValueError):
            return 4
        logging_runtime = start_agent_logging(config)
        try:
            store = DpapiCredentialStore(_credential_path(args.config))
            enroll(config, token, store)
            logging_runtime.emit(
                {"event": "ENROLLMENT_SUCCESS", "state": "STOPPED", "attempt": 0}
            )
            return 0
        except EnrollmentRejected:
            logging_runtime.emit(
                {"event": "ENROLLMENT_REJECTED", "state": "STOPPED", "attempt": 0}
            )
            return 5
        finally:
            logging_runtime.stop(1.0)

    try:
        config = load_config(args.config)
    except (OSError, RuntimeError, ValueError):
        return 4
    logging_runtime = start_agent_logging(config)
    try:
        store = DpapiCredentialStore(_credential_path(args.config))
        credential = store.load()
        if credential is None:
            logging_runtime.emit(
                {"event": "ENROLLMENT_REQUIRED", "state": "STOPPED", "attempt": 0}
            )
            return 3
        AgentRuntime(config, credential, event_sink=logging_runtime.emit).run()
        return 0
    except (AuthRejected, ValueError):
        logging_runtime.emit(
            {"event": "CREDENTIAL_INVALID", "state": "STOPPED", "attempt": 0}
        )
        return 5
    finally:
        logging_runtime.stop(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
