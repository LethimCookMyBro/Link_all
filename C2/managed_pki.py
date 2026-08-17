from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from C2.managed_registry import IssuedDeviceCertificate, utc_now
from client.agent_config import (
    _DpapiProtector,
    _apply_private_acl,
    _atomic_private_write,
    _read_private_file,
)


DEVICE_URI_PREFIX = "urn:phantomlink:agent:"
CERTIFICATE_LIFETIME = timedelta(days=90)
RENEWAL_WINDOW = timedelta(days=30)
_CA_LIFETIME = timedelta(days=3650)


class ControllerCertificateAuthority:
    def __init__(
        self,
        key_path: Path,
        certificate_path: Path,
        *,
        protector=None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.key_path = Path(key_path)
        self.certificate_path = Path(certificate_path)
        self._test_boundary = protector is not None
        self._protector = protector or _DpapiProtector()
        self._now = now

    def initialize(self, common_name: str) -> None:
        common_name = _common_name(common_name)
        existing = self.key_path.exists(), self.certificate_path.exists()
        if existing == (True, True):
            _, certificate = self._load_ca()
            expected = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            if certificate.subject != expected:
                raise ValueError("controller CA common name does not match existing state")
            return
        if any(existing):
            raise ValueError("incomplete controller CA state")

        key = ec.generate_private_key(ec.SECP256R1())
        now = _aware_now(self._now())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + _CA_LIFETIME)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        acl_applier = None if self._test_boundary else _apply_private_acl
        _atomic_private_write(
            self.key_path, self._protector.protect(key_pem), acl_applier
        )
        _atomic_private_write(
            self.certificate_path,
            certificate.public_bytes(serialization.Encoding.PEM),
            None,
        )

    def sign_device_csr(self, csr_pem: bytes, agent_id: str) -> IssuedDeviceCertificate:
        return self._issue(csr_pem, agent_id)

    def renew_device_csr(self, csr_pem: bytes, agent_id: str) -> IssuedDeviceCertificate:
        return self._issue(csr_pem, agent_id)

    def ca_pem(self) -> bytes:
        try:
            pem = self.certificate_path.read_bytes()
            x509.load_pem_x509_certificate(pem)
            return pem
        except (OSError, ValueError) as exc:
            raise ValueError("controller CA certificate is unavailable or invalid") from exc

    def _issue(self, csr_pem: bytes, agent_id: str) -> IssuedDeviceCertificate:
        agent_id = _agent_id(agent_id)
        csr = _csr(csr_pem)
        key, ca_certificate = self._load_ca()
        now = _aware_now(self._now())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(ca_certificate.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + CERTIFICATE_LIFETIME)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"{DEVICE_URI_PREFIX}{agent_id}")]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        pem = certificate.public_bytes(serialization.Encoding.PEM)
        return IssuedDeviceCertificate(
            certificate_pem=pem,
            fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
            serial=str(certificate.serial_number),
            certificate_not_after=_format_time(now + CERTIFICATE_LIFETIME),
        )

    def _load_ca(self):
        if not self.key_path.exists():
            raise ValueError("controller CA protected key is missing")
        try:
            protected = (
                self.key_path.read_bytes()
                if self._test_boundary
                else _read_private_file(self.key_path, None)
            )
            key = serialization.load_pem_private_key(
                self._protector.unprotect(protected), password=None
            )
            certificate = x509.load_pem_x509_certificate(self.ca_pem())
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("controller CA protected key is unavailable or invalid") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("controller CA key must be EC P-256")
        if _public_bytes(key.public_key()) != _public_bytes(certificate.public_key()):
            raise ValueError("controller CA key does not match certificate")
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound as exc:
            raise ValueError("controller CA certificate profile is invalid") from exc
        if (
            certificate.subject != certificate.issuer
            or not constraints.ca
            or not usage.key_cert_sign
            or usage.digital_signature
        ):
            raise ValueError("controller CA certificate profile is invalid")
        try:
            certificate.public_key().verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                ec.ECDSA(certificate.signature_hash_algorithm),
            )
        except InvalidSignature as exc:
            raise ValueError("controller CA certificate signature is invalid") from exc
        return key, certificate


def _csr(csr_pem: bytes) -> x509.CertificateSigningRequest:
    if type(csr_pem) is not bytes:
        raise ValueError("csr_pem must be PEM bytes")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise ValueError("invalid CSR") from exc
    if not csr.is_signature_valid:
        raise ValueError("invalid CSR signature")
    public_key = csr.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise ValueError("CSR public key must be EC P-256")
    attributes = list(csr.subject)
    if len(attributes) != 1 or attributes[0].oid != NameOID.COMMON_NAME:
        raise ValueError("CSR subject must contain only a common name")
    _common_name(attributes[0].value)
    return csr


def _common_name(value: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or not value.isprintable():
        raise ValueError("common name must be 1 to 128 printable characters")
    return value


def _agent_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("agent_id must be a canonical version 4 UUID") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("agent_id must be a canonical version 4 UUID")
    return value


def _public_bytes(key) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _aware_now(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("now must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
