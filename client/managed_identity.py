from __future__ import annotations

import base64
import json
import shutil
import ssl
import tempfile
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from client.agent_config import (
    AclApplier,
    AclInspector,
    _DpapiProtector,
    _apply_private_acl,
    _atomic_private_write,
    _read_private_file,
    validate_private_file,
)


DEVICE_URI_PREFIX = "urn:phantomlink:agent:"
_CERTIFICATE_LIFETIME = timedelta(days=90)


@dataclass(frozen=True)
class AgentCertificateIdentity:
    agent_id: str
    certificate_pem: bytes
    chain_pem: bytes
    private_key_pem: bytes
    certificate_serial: str
    certificate_not_after: str


class AgentCertificateStore:
    def __init__(
        self,
        path: Path,
        *,
        protector=None,
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

    def create_csr(self, display_name: str) -> tuple[bytes, bytes]:
        display_name = _display_name(display_name)
        key = ec.generate_private_key(ec.SECP256R1())
        private_key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, display_name)]))
            .sign(key, hashes.SHA256())
        )
        return private_key_pem, csr.public_bytes(serialization.Encoding.PEM)

    def save_enrollment(
        self,
        private_key_pem: bytes,
        *,
        agent_id: str,
        certificate_pem: bytes,
        chain_pem: bytes,
        certificate_serial: str,
        certificate_not_after: str,
    ) -> AgentCertificateIdentity:
        identity = _validated_identity(
            AgentCertificateIdentity(
                agent_id,
                certificate_pem,
                chain_pem,
                private_key_pem,
                certificate_serial,
                certificate_not_after,
            )
        )
        raw = json.dumps(
            {
                "agent_id": identity.agent_id,
                "certificate_not_after": identity.certificate_not_after,
                "certificate_pem": _encode(identity.certificate_pem),
                "certificate_serial": identity.certificate_serial,
                "chain_pem": _encode(identity.chain_pem),
                "private_key_pem": _encode(identity.private_key_pem),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_private_write(
            self.path, self._protector.protect(raw), self._acl_applier
        )
        return identity

    def load(self) -> AgentCertificateIdentity | None:
        if not self.path.exists():
            return None
        protected = (
            self.path.read_bytes()
            if self._test_boundary and self._acl_inspector is None
            else _read_private_file(self.path, self._acl_inspector)
        )
        try:
            data = json.loads(self._protector.unprotect(protected).decode("utf-8"))
            identity = AgentCertificateIdentity(
                agent_id=data["agent_id"],
                certificate_pem=_decode(data["certificate_pem"]),
                chain_pem=_decode(data["chain_pem"]),
                private_key_pem=_decode(data["private_key_pem"]),
                certificate_serial=data["certificate_serial"],
                certificate_not_after=data["certificate_not_after"],
            )
            return _validated_identity(identity)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid certificate identity file: {self.path}") from exc

    def delete(self) -> None:
        if self.path.exists() and (
            not self._test_boundary or self._acl_inspector is not None
        ):
            validate_private_file(self.path, self._acl_inspector)
        self.path.unlink(missing_ok=True)

    def client_context(self, identity: AgentCertificateIdentity) -> ssl.SSLContext:
        identity = _validated_identity(identity)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=identity.chain_pem.decode("ascii"))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(prefix=".managed-tls-", dir=self.path.parent)
        )
        certificate_path = directory / "certificate-chain.pem"
        key_path = directory / "private-key.pem"
        try:
            if self._acl_applier is not None:
                self._acl_applier(directory)
            certificate_chain = identity.certificate_pem.rstrip() + b"\n" + identity.chain_pem
            _atomic_private_write(
                certificate_path, certificate_chain, self._acl_applier
            )
            _atomic_private_write(key_path, identity.private_key_pem, self._acl_applier)
            context.load_cert_chain(certificate_path, key_path)
            self._after_load((certificate_path, key_path, directory))
            return context
        finally:
            shutil.rmtree(directory)

    def _after_load(self, paths: tuple[Path, ...]) -> None:
        pass


def build_enrollment_request(
    token: str, display_name: str, agent_version: str, csr_pem: bytes
) -> bytes:
    token = _text("token", token, 512)
    display_name = _display_name(display_name)
    agent_version = _agent_version(agent_version)
    if type(csr_pem) is not bytes:
        raise ValueError("csr_pem must be PEM bytes")
    try:
        x509.load_pem_x509_csr(csr_pem)
        csr_text = csr_pem.decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("csr_pem must contain a valid PEM CSR") from exc
    return json.dumps(
        {
            "agent_version": agent_version,
            "csr_pem": csr_text,
            "display_name": display_name,
            "token": token,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validated_identity(identity: AgentCertificateIdentity) -> AgentCertificateIdentity:
    if not isinstance(identity, AgentCertificateIdentity):
        raise TypeError("identity must be an AgentCertificateIdentity")
    agent_id = _agent_id(identity.agent_id)
    try:
        private_key = serialization.load_pem_private_key(
            identity.private_key_pem, password=None
        )
        certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
        ca_certificate = x509.load_pem_x509_certificate(identity.chain_pem)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid certificate identity PEM") from exc
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
        private_key.curve, ec.SECP256R1
    ):
        raise ValueError("private key must be EC P-256")
    if _public_bytes(private_key.public_key()) != _public_bytes(certificate.public_key()):
        raise ValueError("certificate does not match private key")
    if certificate.issuer != ca_certificate.subject:
        raise ValueError("certificate CA signature is invalid")
    if ca_certificate.public_bytes(serialization.Encoding.PEM) != identity.chain_pem:
        raise ValueError("chain_pem must contain exactly one canonical CA certificate")
    ca_key = _validate_ca_profile(ca_certificate)
    try:
        ca_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(certificate.signature_hash_algorithm),
        )
    except InvalidSignature as exc:
        raise ValueError("certificate CA signature is invalid") from exc
    _validate_leaf_profile(certificate, ca_certificate, agent_id)
    if identity.certificate_serial != str(certificate.serial_number):
        raise ValueError("certificate_serial does not match certificate")
    if identity.certificate_not_after != _certificate_not_after(certificate):
        raise ValueError("certificate_not_after does not match certificate")
    return identity


def _validate_ca_profile(certificate: x509.Certificate) -> ec.EllipticCurvePublicKey:
    public_key = certificate.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise ValueError("certificate CA profile is invalid")
    if (
        certificate.subject != certificate.issuer
        or not _single_common_name(certificate.subject)
        or not isinstance(certificate.signature_hash_algorithm, hashes.SHA256)
    ):
        raise ValueError("certificate CA profile is invalid")
    _require_exact_extensions(
        certificate,
        {
            ExtensionOID.BASIC_CONSTRAINTS: (
                True,
                x509.BasicConstraints(ca=True, path_length=0),
            ),
            ExtensionOID.KEY_USAGE: (True, _ca_key_usage()),
            ExtensionOID.SUBJECT_KEY_IDENTIFIER: (
                False,
                x509.SubjectKeyIdentifier.from_public_key(public_key),
            ),
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER: (
                False,
                x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
            ),
        },
        "certificate CA profile is invalid",
    )
    try:
        public_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(certificate.signature_hash_algorithm),
        )
    except InvalidSignature as exc:
        raise ValueError("certificate CA profile is invalid") from exc
    return public_key


def _validate_leaf_profile(
    certificate: x509.Certificate,
    ca_certificate: x509.Certificate,
    agent_id: str,
) -> None:
    if (
        not _single_common_name(certificate.subject)
        or not isinstance(certificate.signature_hash_algorithm, hashes.SHA256)
        or certificate.not_valid_after - certificate.not_valid_before
        != _CERTIFICATE_LIFETIME
    ):
        raise ValueError("certificate profile is invalid")
    try:
        uris = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    if uris and uris != [f"{DEVICE_URI_PREFIX}{agent_id}"]:
        raise ValueError("certificate agent URI does not match agent_id")
    _require_exact_extensions(
        certificate,
        {
            ExtensionOID.BASIC_CONSTRAINTS: (
                True,
                x509.BasicConstraints(ca=False, path_length=None),
            ),
            ExtensionOID.KEY_USAGE: (True, _leaf_key_usage()),
            ExtensionOID.EXTENDED_KEY_USAGE: (
                False,
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            ),
            ExtensionOID.SUBJECT_KEY_IDENTIFIER: (
                False,
                x509.SubjectKeyIdentifier.from_public_key(certificate.public_key()),
            ),
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER: (
                False,
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_certificate.public_key()
                ),
            ),
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME: (
                False,
                x509.SubjectAlternativeName(
                    [
                        x509.UniformResourceIdentifier(
                            f"{DEVICE_URI_PREFIX}{agent_id}"
                        )
                    ]
                ),
            ),
        },
        "certificate profile is invalid",
    )


def _require_exact_extensions(certificate, expected, message: str) -> None:
    actual = {extension.oid: extension for extension in certificate.extensions}
    if set(actual) != set(expected):
        raise ValueError(message)
    for oid, (critical, value) in expected.items():
        extension = actual[oid]
        if extension.critical is not critical or extension.value != value:
            raise ValueError(message)


def _single_common_name(subject: x509.Name) -> bool:
    attributes = list(subject)
    return (
        len(attributes) == 1
        and attributes[0].oid == NameOID.COMMON_NAME
        and 1 <= len(attributes[0].value) <= 128
        and attributes[0].value.isprintable()
    )


def _leaf_key_usage() -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )


def _ca_key_usage() -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )


def _agent_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("agent_id must be a canonical version 4 UUID") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("agent_id must be a canonical version 4 UUID")
    return value


def _display_name(value: str) -> str:
    return _text("display_name", value, 128)


def _agent_version(value: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 32 or any(
        not 0x21 <= ord(character) <= 0x7E for character in value
    ):
        raise ValueError("agent_version must be 1 to 32 visible ASCII characters")
    return value


def _text(name: str, value: str, limit: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= limit or not value.isprintable():
        raise ValueError(f"{name} must be 1 to {limit} printable characters")
    return value


def _encode(value: bytes) -> str:
    if type(value) is not bytes:
        raise ValueError("certificate identity PEM values must be bytes")
    return base64.b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    if type(value) is not str:
        raise ValueError("certificate identity bundle values must be strings")
    return base64.b64decode(value, validate=True)


def _public_bytes(key) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _certificate_not_after(certificate: x509.Certificate) -> str:
    value = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
