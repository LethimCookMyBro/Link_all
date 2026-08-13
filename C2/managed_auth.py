from __future__ import annotations

import argparse
import base64
import http.server
import json
import math
import os
import select
import socket
import sqlite3
import ssl
import sys
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
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
from C2.managed_services import ManagedServer
from client import transport

__all__ = [
    "EnrollmentResponse",
    "EnrollmentServer",
    "EnrollmentService",
    "ManagedServer",
]

_MAX_JSON_SIZE = 64 * 1024
_MAX_ID_SIZE = 128
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
        registry: ManagedRegistry,
        certificate_authority: ControllerCertificateAuthority,
        *,
        now=utc_now,
    ) -> None:
        self._registry = registry
        self._certificate_authority = certificate_authority
        self._now = now

    def ca_pem(self) -> bytes:
        return self._certificate_authority.ca_pem()

    def exchange(
        self,
        token: str,
        csr_pem: bytes,
        display_name: str,
        agent_version: str,
    ) -> EnrollmentResponse:
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

    def renew(
        self, agent_id: str, fingerprint: str, csr_pem: bytes
    ) -> EnrollmentResponse:
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
            if self.path == "/v1/enroll":
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
            self._reply(400, {"error": "invalid request"})
            return
        except (SigningUnavailable, ManagedRegistryError, OSError, sqlite3.Error):
            self._reply(503, {"error": "service unavailable"})
            return
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
        self._close_lock = threading.Lock()
        self._closed = False

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        except OSError:
            if not self._server._closing.is_set():
                raise

    def shutdown(self) -> None:
        self._server.begin_shutdown()
        self._server.close_connections()
        self._server.shutdown()

    def stop_accepting(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._server.begin_shutdown()
            self._server.close_connections()
            self._server.server_close()
            self._closed = True

    def server_close(self) -> None:
        self.stop_accepting()


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
    return _database_services(Path(path) / "managed.db")


def _database_services(path: os.PathLike[str] | str):
    registry = ManagedRegistry(Path(path))
    registry.initialize()
    return registry


def _add_database_arguments(command) -> None:
    paths = command.add_mutually_exclusive_group()
    paths.add_argument("--db")
    paths.add_argument("--store", help=argparse.SUPPRESS)


def _database_path(args) -> Path:
    if args.db:
        return Path(args.db)
    if args.store:
        return Path(args.store) / "managed.db"
    configured = os.getenv("PHANTOMLINK_MANAGED_DB", "")
    if configured:
        return Path(configured)
    store = Path(os.getenv("PHANTOMLINK_MANAGED_STORE", "managed-store"))
    return store / "managed.db"


def _registry_from_args(args):
    return _store_services(args.store) if args.store else _database_services(
        _database_path(args)
    )


def _print_json(records) -> None:
    print(json.dumps([asdict(record) for record in records], separators=(",", ":")))


def _action_exit(result, success_codes) -> int:
    if result.code in success_codes:
        print(result.message)
        return 0
    if result.code == "NOT_FOUND":
        print("not found")
        return 1
    print("failed", file=sys.stderr)
    return 5


def _valid_revoke_arguments(args) -> bool:
    try:
        valid_agent_id = str(UUID(args.agent_id)) == args.agent_id
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        valid_agent_id
        and type(args.actor) is str
        and 1 <= len(args.actor) <= 128
        and args.actor.isprintable()
        and type(args.reason) is str
        and 1 <= len(args.reason) <= 512
        and args.reason.isprintable()
    )


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m C2.managed_auth")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-ca")
    initialize.add_argument("--ca-key", required=True)
    initialize.add_argument("--ca-cert", required=True)
    initialize.add_argument("--common-name", default="PhantomLink Managed CA")
    issue = commands.add_parser("issue-token")
    _add_database_arguments(issue)
    issue.add_argument("--ttl", type=float, default=600)
    listing = commands.add_parser("list-devices")
    _add_database_arguments(listing)
    audit = commands.add_parser("list-audit")
    _add_database_arguments(audit)
    audit.add_argument("--limit", type=int, default=100)
    revoke = commands.add_parser("revoke")
    _add_database_arguments(revoke)
    revoke.add_argument("--agent-id", required=True)
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.command == "init-ca":
        if any(
            type(value) is not str or not value.strip()
            for value in (args.ca_key, args.ca_cert)
        ) or (
            type(args.common_name) is not str
            or not 1 <= len(args.common_name) <= 128
            or not args.common_name.isprintable()
        ):
            return 2
        try:
            ControllerCertificateAuthority(
                Path(args.ca_key), Path(args.ca_cert)
            ).initialize(args.common_name)
        except Exception:
            print("CA initialization failed", file=sys.stderr)
            return 5
        print("CA initialized")
        return 0

    if args.command == "issue-token" and (
        not math.isfinite(args.ttl) or args.ttl <= 0
    ):
        return 2
    if args.command == "list-audit" and not 1 <= args.limit <= 1000:
        return 2
    if args.command == "revoke" and not _valid_revoke_arguments(args):
        return 2
    try:
        registry = _registry_from_args(args)
    except Exception:
        print("registry unavailable", file=sys.stderr)
        return 5
    if args.command == "issue-token":
        try:
            print(registry.issue_token(args.ttl))
        except ValueError:
            return 2
        except Exception:
            print("registry unavailable", file=sys.stderr)
            return 5
        return 0
    if args.command == "list-devices":
        try:
            _print_json(registry.list_device_records())
        except Exception:
            print("registry unavailable", file=sys.stderr)
            return 5
        return 0
    if args.command == "list-audit":
        try:
            _print_json(registry.list_audit_events(args.limit))
        except ValueError:
            return 2
        except Exception:
            print("registry unavailable", file=sys.stderr)
            return 5
        return 0
    try:
        result = registry.revoke_device(
            args.agent_id, args.actor, args.reason, str(uuid4())
        )
        return _action_exit(result, {"REVOKED", "ALREADY_REVOKED"})
    except ValueError:
        return 2
    except Exception:
        print("failed", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(_main())
