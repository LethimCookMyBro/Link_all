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

from cryptography import x509
from cryptography.hazmat.primitives import serialization

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.agent_config import (
    DeviceCredential,
    _apply_private_acl,
    _atomic_private_write,
    _read_private_file,
    load_config,
    write_identity,
)
from client.agent_logging import start_agent_logging
from client.agent_runtime import AgentRuntime, AuthRejected
from client.managed_identity import AgentCertificateIdentity, AgentCertificateStore
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


def _certificate_from_response(payload):
    fields = {
        "agent_id",
        "certificate_pem",
        "chain_pem",
        "certificate_serial",
        "certificate_not_after",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise EnrollmentRejected("invalid enrollment response")
    if any(type(payload[name]) is not str or not payload[name] for name in fields):
        raise EnrollmentRejected("invalid enrollment response")
    try:
        certificate_pem = base64.b64decode(payload["certificate_pem"], validate=True)
        chain_pem = base64.b64decode(payload["chain_pem"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EnrollmentRejected("invalid enrollment response") from exc
    if len(certificate_pem) > 65536 or len(chain_pem) > 65536:
        raise EnrollmentRejected("invalid enrollment response")
    return {
        "agent_id": payload["agent_id"],
        "certificate_pem": certificate_pem,
        "chain_pem": chain_pem,
        "certificate_serial": payload["certificate_serial"],
        "certificate_not_after": payload["certificate_not_after"],
    }


def _encoded_csr(csr_pem):
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except (TypeError, ValueError) as exc:
        raise EnrollmentRejected("invalid local CSR") from exc
    return base64.b64encode(csr.public_bytes(serialization.Encoding.DER)).decode(
        "ascii"
    )


def _certificate_request(config, path, body, context):
    connection = http.client.HTTPSConnection(
        config.controller_host,
        config.enrollment_port,
        timeout=config.connect_timeout,
        context=context,
    )
    try:
        connection.connect()
        certificate = connection.sock.getpeercert(binary_form=True)
        if not certificate or not hmac.compare_digest(
            hashlib.sha256(certificate).hexdigest(), config.tls_cert_sha256
        ):
            raise EnrollmentRejected("certificate pin mismatch")
        connection.request(
            "POST",
            path,
            body=encode_json_payload(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload_bytes = response.read(65537)
        if response.status != 201 or len(payload_bytes) > 65536:
            raise EnrollmentRejected("enrollment rejected")
        return _certificate_from_response(
            decode_json_payload(payload_bytes, max_size=65536)
        )
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


def enroll(config, token: str, store):
    if not isinstance(token, str) or not token:
        raise ValueError("enrollment token is required")
    if hasattr(store, "create_csr"):
        private_key_pem, csr_pem = store.create_csr(config.display_name)
        response = _certificate_request(
            config,
            "/v1/enroll",
            {
                "agent_version": config.agent_version,
                "csr_pem": _encoded_csr(csr_pem),
                "display_name": config.display_name,
                "token": token,
            },
            _tls_context(),
        )
        identity = store.save_enrollment(private_key_pem, **response)
        identity_path = _identity_path(store)
        if identity_path is not None:
            _atomic_private_write(
                identity_path,
                json.dumps(
                    {"agent_id": identity.agent_id},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                _apply_private_acl,
            )
        return identity

    # Retained until the Phase 1 integration fixture moves to certificate identity.
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


def renew(
    config, identity: AgentCertificateIdentity, store: AgentCertificateStore
) -> AgentCertificateIdentity:
    private_key_pem, csr_pem = store.create_csr(config.display_name)
    response = _certificate_request(
        config,
        "/v1/renew",
        {"csr_pem": _encoded_csr(csr_pem)},
        store.client_context(identity),
    )
    if response["agent_id"] != identity.agent_id:
        raise EnrollmentRejected("renewal identity changed")
    return store.save_enrollment(private_key_pem, **response)


def _read_token_file(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("token file path must be absolute")
    try:
        raw = _read_private_file(path, None)
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("token file must be UTF-8 without BOM")
        token = raw.decode("utf-8").strip()
    finally:
        path.unlink(missing_ok=True)
    if not token:
        raise ValueError("enrollment token is required")
    return token


def _is_platform_failure(error: Exception) -> bool:
    return (
        isinstance(error, (OSError, RuntimeError))
        or error.__class__.__module__ == "pywintypes"
    )


def _is_pythonw() -> bool:
    return Path(sys.executable).name.lower().startswith("pythonw")


def parser():
    root = argparse.ArgumentParser(prog="python -m client.managed_agent")
    commands = root.add_subparsers(dest="command", required=True)
    enroll_cmd = commands.add_parser("enroll")
    enroll_cmd.add_argument("--config", default=str(default_config_path()))
    enroll_cmd.add_argument("--token-file")
    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("--config", default=str(default_config_path()))
    return root


def _certificate_store_path(config_path, configured_path):
    path = Path(configured_path)
    return path if path.is_absolute() else Path(config_path).resolve().parent / path


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
        except (EOFError, ValueError):
            return 2
        except Exception as error:
            if _is_platform_failure(error):
                return 5
            raise
        if not token:
            return 2
        try:
            config = load_config(args.config)
        except ValueError:
            return 4
        except Exception as error:
            if _is_platform_failure(error):
                return 5
            raise
        try:
            logging_runtime = start_agent_logging(config)
        except Exception as error:
            if _is_platform_failure(error):
                return 5
            raise
        try:
            store = AgentCertificateStore(
                _certificate_store_path(args.config, config.certificate_store_path)
            )
            enroll(config, token, store)
            logging_runtime.emit(
                {"event": "ENROLLMENT_SUCCESS", "state": "STOPPED", "attempt": 0}
            )
            return 0
        except (EnrollmentRejected, ValueError):
            logging_runtime.emit(
                {"event": "ENROLLMENT_REJECTED", "state": "STOPPED", "attempt": 0}
            )
            return 5
        except Exception as error:
            if not _is_platform_failure(error):
                raise
            logging_runtime.emit(
                {
                    "event": "ENROLLMENT_STORAGE_FAILURE",
                    "state": "STOPPED",
                    "attempt": 0,
                }
            )
            return 5
        finally:
            logging_runtime.stop(1.0)

    try:
        config = load_config(args.config)
    except ValueError:
        return 4
    except Exception as error:
        if _is_platform_failure(error):
            return 5
        raise
    try:
        logging_runtime = start_agent_logging(config)
    except Exception as error:
        if _is_platform_failure(error):
            return 5
        raise
    try:
        store = AgentCertificateStore(
            _certificate_store_path(args.config, config.certificate_store_path)
        )
        credential = store.load()
        if credential is None:
            logging_runtime.emit(
                {"event": "ENROLLMENT_REQUIRED", "state": "STOPPED", "attempt": 0}
            )
            return 3
        auth_rejected = False

        def observe(event):
            nonlocal auth_rejected
            auth_rejected = auth_rejected or event.get("event") == "AUTH_REJECTED"
            logging_runtime.emit(event)

        runtime = AgentRuntime(
            config,
            credential,
            identity_store=store,
            renewer=renew,
            event_sink=observe,
        )
        try:
            runtime.run()
        except KeyboardInterrupt:
            runtime.stop()
            return 0
        return 5 if auth_rejected else 0
    except (AuthRejected, ValueError):
        logging_runtime.emit(
            {"event": "CREDENTIAL_INVALID", "state": "STOPPED", "attempt": 0}
        )
        return 5
    except Exception as error:
        if not _is_platform_failure(error):
            raise
        logging_runtime.emit(
            {"event": "CREDENTIAL_STORE_FAILURE", "state": "STOPPED", "attempt": 0}
        )
        return 5
    finally:
        logging_runtime.stop(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
