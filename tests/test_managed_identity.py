import json
import ssl
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from C2.managed_pki import ControllerCertificateAuthority
from client.managed_identity import (
    AgentCertificateIdentity,
    AgentCertificateStore,
    build_enrollment_request,
)


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
def identity_store(tmp_path, fake_protector):
    return AgentCertificateStore(
        tmp_path / "identity.dpapi",
        protector=fake_protector,
        acl_applier=lambda _: None,
    )


@pytest.fixture
def issued_identity(tmp_path, identity_store):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=FakeProtector(),
        now=lambda: now,
    )
    ca.initialize("PhantomLink Test CA")
    private_key_pem, csr_pem = identity_store.create_csr("pc-01")
    issued = ca.sign_device_csr(csr_pem, AGENT_ID)
    return identity_store.save_enrollment(
        private_key_pem,
        agent_id=AGENT_ID,
        certificate_pem=issued.certificate_pem,
        chain_pem=ca.ca_pem(),
        certificate_serial=issued.serial,
        certificate_not_after=issued.certificate_not_after,
    )


def test_agent_store_keeps_private_key_out_of_enrollment_request(tmp_path, fake_protector):
    store = AgentCertificateStore(tmp_path / "identity.dpapi", protector=fake_protector)
    private_key_pem, csr_pem = store.create_csr("pc-01")
    request = build_enrollment_request("token-value", "pc-01", "2.0", csr_pem)
    assert private_key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert private_key_pem not in request
    assert b"PRIVATE KEY" not in request
    assert b"CERTIFICATE REQUEST" in request
    assert json.loads(request) == {
        "agent_version": "2.0",
        "csr_pem": csr_pem.decode("ascii"),
        "display_name": "pc-01",
        "token": "token-value",
    }


def test_save_load_round_trip_keeps_bundle_protected_and_out_of_config(
    tmp_path, identity_store, issued_identity
):
    config_path = tmp_path / "agent.json"
    config_path.write_text('{"certificate_store_path":"identity.dpapi"}', encoding="utf-8")
    loaded = identity_store.load()
    protected = identity_store.path.read_bytes()

    assert loaded == issued_identity
    assert protected.startswith(b"protected:")
    assert issued_identity.private_key_pem not in protected
    assert b"PRIVATE KEY" not in protected
    config_bytes = config_path.read_bytes()
    assert issued_identity.private_key_pem not in config_bytes
    assert issued_identity.certificate_pem not in config_bytes
    assert issued_identity.chain_pem not in config_bytes


def test_save_rejects_private_key_or_agent_uri_mismatch(tmp_path, fake_protector):
    store = AgentCertificateStore(
        tmp_path / "identity.dpapi", protector=fake_protector, acl_applier=lambda _: None
    )
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi", tmp_path / "ca.pem", protector=fake_protector
    )
    ca.initialize("PhantomLink Test CA")
    private_key_pem, csr_pem = store.create_csr("pc-01")
    issued = ca.sign_device_csr(csr_pem, AGENT_ID)
    other_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    args = dict(
        agent_id=AGENT_ID,
        certificate_pem=issued.certificate_pem,
        chain_pem=ca.ca_pem(),
        certificate_serial=issued.serial,
        certificate_not_after=issued.certificate_not_after,
    )
    with pytest.raises(ValueError, match="private key"):
        store.save_enrollment(other_key, **args)
    with pytest.raises(ValueError, match="agent URI"):
        store.save_enrollment(
            private_key_pem,
            **{**args, "agent_id": "22222222-2222-4222-8222-222222222222"},
        )
    assert not store.path.exists()


def test_save_rejects_certificate_from_untrusted_chain(tmp_path, fake_protector):
    store = AgentCertificateStore(
        tmp_path / "identity.dpapi", protector=fake_protector, acl_applier=lambda _: None
    )
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-one-key.dpapi", tmp_path / "ca-one.pem", protector=fake_protector
    )
    other_ca = ControllerCertificateAuthority(
        tmp_path / "ca-two-key.dpapi", tmp_path / "ca-two.pem", protector=fake_protector
    )
    ca.initialize("CA one")
    other_ca.initialize("CA two")
    private_key_pem, csr_pem = store.create_csr("pc-01")
    issued = ca.sign_device_csr(csr_pem, AGENT_ID)
    with pytest.raises(ValueError, match="CA signature"):
        store.save_enrollment(
            private_key_pem,
            agent_id=AGENT_ID,
            certificate_pem=issued.certificate_pem,
            chain_pem=other_ca.ca_pem(),
            certificate_serial=issued.serial,
            certificate_not_after=issued.certificate_not_after,
        )


def test_client_context_loads_cert_chain_then_removes_temporary_files(
    identity_store, issued_identity, monkeypatch
):
    observed = []
    monkeypatch.setattr(identity_store, "_after_load", lambda paths: observed.extend(paths))
    context = identity_store.client_context(issued_identity)
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert observed and all(not path.exists() for path in observed)


def test_client_context_removes_temporary_files_when_post_load_step_fails(
    identity_store, issued_identity, monkeypatch
):
    observed = []

    def fail(paths):
        observed.extend(paths)
        raise RuntimeError("post-load failure")

    monkeypatch.setattr(identity_store, "_after_load", fail)
    with pytest.raises(RuntimeError, match="post-load failure"):
        identity_store.client_context(issued_identity)
    assert observed and all(not path.exists() for path in observed)


def test_delete_removes_identity(identity_store, issued_identity):
    assert identity_store.load() == issued_identity
    identity_store.delete()
    assert identity_store.load() is None


def test_identity_is_frozen(issued_identity):
    with pytest.raises(Exception):
        issued_identity.agent_id = "changed"


def test_identity_module_does_not_import_legacy_client():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import client.managed_identity; "
            "raise SystemExit('client.PhantomLink' in sys.modules)",
        ],
        check=False,
    )
    assert result.returncode == 0
