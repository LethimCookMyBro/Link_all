from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from C2.managed_pki import ControllerCertificateAuthority, DEVICE_URI_PREFIX


AGENT_ID = "11111111-1111-4111-8111-111111111111"


class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        if not data.startswith(b"protected:"):
            raise ValueError("unprotected input")
        return data[len(b"protected:") :][::-1]


@pytest.fixture
def fake_protector():
    return FakeProtector()


@pytest.fixture
def fixed_now():
    return datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def make_agent_csr(common_name, *, key=None, extra_subject=(), san=None):
    key = key or ec.generate_private_key(ec.SECP256R1())
    subject = [x509.NameAttribute(NameOID.COMMON_NAME, common_name), *extra_subject]
    builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(subject))
    if san is not None:
        builder = builder.add_extension(san, critical=False)
    csr = builder.sign(key, hashes.SHA256())
    return key, csr.public_bytes(serialization.Encoding.PEM)


def aware(value):
    return value.replace(tzinfo=timezone.utc)


def test_ca_signs_90_day_client_certificate_with_agent_uri(
    tmp_path, fake_protector, fixed_now
):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    private_key, csr = make_agent_csr("pc-01")

    issued = ca.sign_device_csr(csr, AGENT_ID)

    certificate = x509.load_pem_x509_certificate(issued.certificate_pem)
    assert aware(certificate.not_valid_after) - aware(certificate.not_valid_before) == timedelta(days=90)
    assert ExtendedKeyUsageOID.CLIENT_AUTH in certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    assert {value.value for value in certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value} == {f"{DEVICE_URI_PREFIX}{AGENT_ID}"}
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert key_usage.digital_signature and not key_usage.key_cert_sign
    assert issued.fingerprint == certificate.fingerprint(hashes.SHA256()).hex()
    assert issued.serial == str(certificate.serial_number)
    assert issued.certificate_not_after == "2026-11-11T12:00:00Z"
    assert private_key.private_numbers().private_value > 0


def test_ca_key_is_protected_and_absent_from_registry_database(
    tmp_path, fake_protector, fixed_now
):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    protected = (tmp_path / "ca-key.dpapi").read_bytes()
    private_key_pem = fake_protector.unprotect(protected)
    registry_bytes = b"SQLite format 3\x00public registry fixture"
    (tmp_path / "managed.db").write_bytes(registry_bytes)

    assert protected.startswith(b"protected:")
    assert b"PRIVATE KEY" not in protected
    assert private_key_pem not in registry_bytes
    assert b"PRIVATE KEY" not in registry_bytes


def test_ca_certificate_is_for_certificate_signing_only(tmp_path, fake_protector, fixed_now):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    certificate = x509.load_pem_x509_certificate(ca.ca_pem())
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and usage.crl_sign
    assert not usage.digital_signature
    with pytest.raises(x509.ExtensionNotFound):
        certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)


def test_signing_rejects_invalid_csr_signature_and_non_p256_key(
    tmp_path, fake_protector, fixed_now
):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    _, valid_pem = make_agent_csr("pc-01")
    der = bytearray(x509.load_pem_x509_csr(valid_pem).public_bytes(serialization.Encoding.DER))
    der[-1] ^= 1
    invalid_pem = x509.load_der_x509_csr(bytes(der)).public_bytes(serialization.Encoding.PEM)
    _, rsa_pem = make_agent_csr("pc-01", key=rsa.generate_private_key(65537, 2048))

    with pytest.raises(ValueError, match="signature"):
        ca.sign_device_csr(invalid_pem, AGENT_ID)
    with pytest.raises(ValueError, match="P-256"):
        ca.sign_device_csr(rsa_pem, AGENT_ID)


@pytest.mark.parametrize("agent_id", ["", "not-a-uuid", "11111111-1111-1111-8111-111111111111"])
def test_signing_rejects_invalid_agent_id(tmp_path, fake_protector, fixed_now, agent_id):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    _, csr = make_agent_csr("pc-01")
    with pytest.raises(ValueError, match="agent_id"):
        ca.sign_device_csr(csr, agent_id)


def test_signing_fails_closed_when_protected_key_is_missing(tmp_path, fake_protector, fixed_now):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    (tmp_path / "ca-key.dpapi").unlink()
    _, csr = make_agent_csr("pc-01")
    with pytest.raises(ValueError, match="key"):
        ca.sign_device_csr(csr, AGENT_ID)


def test_signing_rejects_extra_subject_fields_and_does_not_copy_csr_extensions(
    tmp_path, fake_protector, fixed_now
):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    _, extra_subject_csr = make_agent_csr(
        "pc-01", extra_subject=(x509.NameAttribute(NameOID.ORGANIZATION_NAME, "untrusted"),)
    )
    with pytest.raises(ValueError, match="subject"):
        ca.sign_device_csr(extra_subject_csr, AGENT_ID)

    _, csr = make_agent_csr(
        "pc-01", san=x509.SubjectAlternativeName([x509.DNSName("untrusted.example")])
    )
    certificate = x509.load_pem_x509_certificate(ca.sign_device_csr(csr, AGENT_ID).certificate_pem)
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert list(san.get_values_for_type(x509.DNSName)) == []


def test_renewal_uses_the_same_validated_profile(tmp_path, fake_protector, fixed_now):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=fake_protector,
        now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    _, csr = make_agent_csr("pc-01")
    renewed = x509.load_pem_x509_certificate(ca.renew_device_csr(csr, AGENT_ID).certificate_pem)
    assert aware(renewed.not_valid_after) - aware(renewed.not_valid_before) == timedelta(days=90)
