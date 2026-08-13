import base64
import hashlib
import http.client
import json
import os
import ssl
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from C2.managed_auth import (
    EnrollmentServer,
    EnrollmentService,
    SigningUnavailable,
    _main,
)
from C2.managed_pki import ControllerCertificateAuthority
from C2.managed_registry import (
    CertificateRejected,
    EnrollmentTokenRejected,
    ManagedRegistry,
)
from client.managed_identity import AgentCertificateStore


class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        assert data.startswith(b"protected:")
        return data[len(b"protected:") :][::-1]


@pytest.fixture
def managed_registry(tmp_path):
    registry = ManagedRegistry(tmp_path / "managed.db")
    registry.initialize()
    return registry


@pytest.fixture
def certificate_authority(tmp_path):
    authority = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi",
        tmp_path / "ca.pem",
        protector=FakeProtector(),
    )
    authority.initialize("PhantomLink Test CA")
    return authority


@pytest.fixture
def csr_pem(tmp_path):
    return AgentCertificateStore(tmp_path / "identity.dpapi").create_csr("pc-01")[1]


@pytest.fixture
def controller_tls_material(tmp_path, certificate_authority):
    ca_key = serialization.load_pem_private_key(
        FakeProtector().unprotect(certificate_authority.key_path.read_bytes()),
        password=None,
    )
    ca_certificate = x509.load_pem_x509_certificate(certificate_authority.ca_pem())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = tmp_path / "controller.crt"
    key_path = tmp_path / "controller.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, certificate


def test_enrollment_consumes_token_and_returns_only_public_certificate_material(
    managed_registry, certificate_authority, csr_pem
):
    token = managed_registry.issue_token(600)
    service = EnrollmentService(managed_registry, certificate_authority)

    response = service.exchange(token, csr_pem, "pc-01", "2.0")

    assert response.agent_id
    assert response.certificate_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert response.chain_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    certificate = x509.load_pem_x509_certificate(response.certificate_pem)
    uri = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier)
    assert uri == [f"urn:phantomlink:agent:{response.agent_id}"]
    assert not hasattr(response, "secret")
    with pytest.raises(EnrollmentTokenRejected):
        service.exchange(token, csr_pem, "pc-01", "2.0")


def test_signing_failure_does_not_consume_token(managed_registry, csr_pem):
    class FailingAuthority:
        def sign_device_csr(self, _csr_pem, _agent_id):
            raise OSError("signer unavailable")

        def ca_pem(self):
            return b"unused"

    token = managed_registry.issue_token(600)
    service = EnrollmentService(managed_registry, FailingAuthority())
    with pytest.raises(SigningUnavailable, match="signer unavailable"):
        service.exchange(token, csr_pem, "pc-01", "2.0")
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with managed_registry._connection() as connection:
        row = connection.execute(
            "SELECT consumed_at FROM enrollment_tokens WHERE token_digest = ?",
            (digest,),
        ).fetchone()
    assert row["consumed_at"] is None


def test_renewal_rejects_revoked_device(
    managed_registry, certificate_authority, csr_pem, tmp_path
):
    service = EnrollmentService(managed_registry, certificate_authority)
    enrolled = service.exchange(
        managed_registry.issue_token(600), csr_pem, "pc-01", "2.0"
    )
    current = managed_registry.get_device(enrolled.agent_id)
    managed_registry.revoke_device(enrolled.agent_id, "operator", "retired", "corr-r")
    renewal_csr = AgentCertificateStore(tmp_path / "renewal.dpapi").create_csr("pc-01")[
        1
    ]

    with pytest.raises(CertificateRejected):
        service.renew(enrolled.agent_id, current.certificate_fingerprint, renewal_csr)


def test_certificate_https_uses_public_response_and_requires_peer_for_renewal(
    managed_registry, certificate_authority, csr_pem, controller_tls_material
):
    class CountingAuthority:
        def __init__(self, authority):
            self.authority = authority
            self.sign_calls = 0

        def sign_device_csr(self, csr, agent_id):
            self.sign_calls += 1
            return self.authority.sign_device_csr(csr, agent_id)

        def renew_device_csr(self, csr, agent_id):
            return self.authority.renew_device_csr(csr, agent_id)

        def ca_pem(self):
            return self.authority.ca_pem()

    cert, key, _ = controller_tls_material
    token = managed_registry.issue_token(600)
    counting_authority = CountingAuthority(certificate_authority)
    server = EnrollmentServer(
        "127.0.0.1",
        0,
        cert,
        key,
        EnrollmentService(managed_registry, counting_authority),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    csr_der = x509.load_pem_x509_csr(csr_pem).public_bytes(serialization.Encoding.DER)
    body = json.dumps(
        {
            "agent_version": "2.0",
            "csr_pem": base64.b64encode(csr_der).decode("ascii"),
            "display_name": "pc-01",
            "token": token,
        }
    )
    try:
        malformed = json.loads(body)
        malformed["token"] = "not-canonical"
        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(
            "POST",
            "/v1/enroll",
            body=json.dumps(malformed),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.close()
        assert counting_authority.sign_calls == 0

        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(
            "POST",
            "/v1/enroll",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 201
        assert set(payload) == {
            "agent_id",
            "certificate_pem",
            "chain_pem",
            "certificate_serial",
            "certificate_not_after",
        }
        connection.close()
        assert counting_authority.sign_calls == 1

        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request(
            "POST",
            "/v1/enroll",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
        assert counting_authority.sign_calls == 2

        connection = http.client.HTTPSConnection(
            "127.0.0.1", server.port, context=context, timeout=2
        )
        connection.request("POST", "/v1/renew", body=json.dumps({"csr_pem": "x"}))
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_agent_enrolls_and_renews_with_local_keys_and_pinned_server(
    managed_registry,
    certificate_authority,
    controller_tls_material,
    tmp_path,
    monkeypatch,
):
    from client import managed_agent
    from client.agent_config import AgentConfig

    cert, key, server_certificate = controller_tls_material
    server = EnrollmentServer(
        "127.0.0.1",
        0,
        cert,
        key,
        EnrollmentService(managed_registry, certificate_authority),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    config = AgentConfig(
        controller_host="127.0.0.1",
        managed_port=1,
        enrollment_port=server.port,
        tls_cert_sha256=server_certificate.fingerprint(hashes.SHA256()).hex(),
        display_name="pc-01",
    )
    store = AgentCertificateStore(
        tmp_path / "identity.dpapi",
        protector=FakeProtector(),
        acl_applier=lambda _: None,
    )
    monkeypatch.setattr(managed_agent, "_apply_private_acl", lambda _: None)
    try:
        identity = managed_agent.enroll(
            config, managed_registry.issue_token(600), store
        )
        previous_key = identity.private_key_pem
        renewed = managed_agent.renew(config, identity, store)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

    assert renewed.agent_id == identity.agent_id
    assert renewed.certificate_serial != identity.certificate_serial
    assert renewed.private_key_pem != previous_key
    assert store.load() == renewed
    assert (
        managed_registry.get_device(renewed.agent_id).certificate_serial
        == renewed.certificate_serial
    )


def test_operator_cli_prints_token_once_and_not_found_is_nonzero(tmp_path, capsys):
    store = tmp_path / "store"

    assert _main(["issue-token", "--store", str(store), "--ttl", "60"]) == 0
    token_output = capsys.readouterr().out.strip().splitlines()
    assert len(token_output) == 1
    assert len(base64.urlsafe_b64decode(token_output[0] + "=")) == 32

    assert _main(["list-devices", "--store", str(store)]) == 0
    assert capsys.readouterr().out.strip() == "[]"
    assert (
        _main(
            [
                "revoke",
                "--store",
                str(store),
                "--agent-id",
                "00000000-0000-4000-8000-000000000001",
                "--actor",
                "operator-test",
                "--reason",
                "test request",
            ]
        )
        == 1
    )
    assert capsys.readouterr().out.strip() == "not found"


def test_phase2_cli_exit_codes_and_required_operator_fields(tmp_path, capsys):
    database = tmp_path / "managed.db"

    assert _main(["issue-token", "--db", str(database), "--ttl", "0"]) == 2
    assert _main(["list-audit", "--db", str(database)]) == 0
    assert json.loads(capsys.readouterr().out.strip()) == []
    assert _main(["revoke", "--db", str(database), "--agent-id", "missing"]) == 2


def test_phase2_cli_has_no_separate_process_disconnect(capsys):
    assert _main(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert "{init-ca,issue-token,list-devices,list-audit,revoke}" in help_text
    assert "disconnect" not in help_text


@pytest.mark.parametrize(
    "arguments",
    [
        ["--agent-id", "not-a-uuid", "--actor", "operator", "--reason", "retired"],
        ["--agent-id", "00000000-0000-4000-8000-000000000001", "--actor", "", "--reason", "retired"],
        ["--agent-id", "00000000-0000-4000-8000-000000000001", "--actor", "operator", "--reason", ""],
    ],
)
def test_phase2_cli_rejects_invalid_revoke_arguments_before_storage(arguments):
    with patch("C2.managed_auth._registry_from_args") as registry:
        assert _main(["revoke", "--db", "ignored.db", *arguments]) == 2
    registry.assert_not_called()


def test_phase2_cli_maps_revoke_storage_failure_to_exit_five(capsys):
    registry = MagicMock()
    registry.revoke_device.side_effect = RuntimeError("private detail")
    with patch("C2.managed_auth._registry_from_args", return_value=registry):
        assert (
            _main(
                [
                    "revoke",
                    "--db",
                    "ignored.db",
                    "--agent-id",
                    "00000000-0000-4000-8000-000000000001",
                    "--actor",
                    "operator",
                    "--reason",
                    "retired",
                ]
            )
            == 5
        )
    assert capsys.readouterr().err.strip() == "failed"


def test_phase2_cli_initializes_ca_without_printing_credentials(tmp_path, capsys):
    authority = MagicMock()
    with patch("C2.managed_auth.ControllerCertificateAuthority", return_value=authority):
        assert (
            _main(
                [
                    "init-ca",
                    "--ca-key",
                    str(tmp_path / "ca.key.dpapi"),
                    "--ca-cert",
                    str(tmp_path / "ca.crt"),
                ]
            )
            == 0
        )
    authority.initialize.assert_called_once_with("PhantomLink Managed CA")
    output = capsys.readouterr().out
    assert "BEGIN CERTIFICATE" not in output
    assert "PRIVATE KEY" not in output


def test_phase2_cli_maps_argument_and_storage_failures(tmp_path, capsys):
    assert (
        _main(
            [
                "init-ca",
                "--ca-key",
                str(tmp_path / "key"),
                "--ca-cert",
                str(tmp_path / "cert"),
                "--common-name",
                "",
            ]
        )
        == 2
    )
    authority = MagicMock()
    authority.initialize.side_effect = OSError("disk")
    with patch("C2.managed_auth.ControllerCertificateAuthority", return_value=authority):
        assert (
            _main(
                [
                    "init-ca",
                    "--ca-key",
                    str(tmp_path / "key"),
                    "--ca-cert",
                    str(tmp_path / "cert"),
                ]
            )
            == 5
        )
    assert capsys.readouterr().err.strip() == "CA initialization failed"
    with patch("C2.managed_auth._registry_from_args", side_effect=OSError("disk")):
        assert _main(["list-devices", "--db", str(tmp_path / "managed.db")]) == 5
    assert capsys.readouterr().err.strip() == "registry unavailable"


def test_phase2_cli_revokes_existing_device(
    managed_registry, certificate_authority, csr_pem, capsys
):
    agent_id = "00000000-0000-4000-8000-000000000001"
    token = managed_registry.issue_token(60)
    certificate = certificate_authority.sign_device_csr(csr_pem, agent_id)
    managed_registry.consume_token_and_enroll(
        token,
        certificate,
        "operator-device",
        "2.0",
        "test",
        "cli-fixture",
        agent_id=agent_id,
    )
    database = str(managed_registry.path)

    assert (
        _main(
            [
                "revoke",
                "--db",
                database,
                "--agent-id",
                agent_id,
                "--actor",
                "operator-test",
                "--reason",
                "retired",
            ]
        )
        == 0
    )
    assert "revoked" in capsys.readouterr().out.lower()
    assert _main(["list-audit", "--db", database]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)
    assert "BEGIN CERTIFICATE" not in output
    assert "token_digest" not in output


def test_startup_backs_up_phase1_files_before_database_creation(tmp_path):
    import C2.C2 as controller

    store = tmp_path / "managed-store"
    store.mkdir()
    (store / "devices.bin").write_bytes(b"legacy")
    calls = []
    registry = MagicMock()
    registry.initialize.side_effect = lambda: calls.append("registry.initialize")
    authority = MagicMock()
    authority._load_ca.side_effect = lambda: calls.append("authority.load")
    sessions = MagicMock()
    queries = MagicMock()
    actions = MagicMock()

    with (
        patch.object(controller, "MANAGED_HOST", "10.8.0.1"),
        patch.object(controller, "MANAGED_DB", str(tmp_path / "managed.db"), create=True),
        patch.object(controller, "MANAGED_STORE", str(store)),
        patch.object(controller, "MANAGED_CA_CERT", str(tmp_path / "ca.crt"), create=True),
        patch.object(controller, "MANAGED_CA_KEY", str(tmp_path / "ca.key"), create=True),
        patch.object(controller, "MANAGED_TLS_CERT", str(tmp_path / "server.crt")),
        patch.object(controller, "MANAGED_TLS_KEY", str(tmp_path / "server.key")),
        patch.object(controller, "validate_managed_bind", side_effect=lambda host: calls.append("validate_managed_bind") or host, create=True),
        patch.object(controller, "backup_phase1_stores", side_effect=lambda *_: calls.append("backup_phase1_stores"), create=True),
        patch.object(controller, "ManagedRegistry", return_value=registry, create=True),
        patch.object(controller, "ControllerCertificateAuthority", return_value=authority, create=True),
        patch.object(controller, "SessionManager", side_effect=lambda *_: calls.append("sessions") or sessions, create=True),
        patch.object(controller, "DeviceQueryService", side_effect=lambda *_: calls.append("queries") or queries, create=True),
        patch.object(controller, "DeviceActionService", side_effect=lambda *_: calls.append("actions") or actions, create=True),
        patch.object(controller, "ManagedDashboardData", side_effect=lambda *_: calls.append("dashboard_data") or MagicMock(), create=True),
        patch.object(controller, "ManagedServer", side_effect=lambda *_: calls.append("managed_server") or MagicMock()),
        patch.object(controller, "EnrollmentServer", side_effect=lambda *_: calls.append("enrollment_server") or MagicMock()),
        patch.object(controller, "EnrollmentService", side_effect=lambda *_: calls.append("enrollment_service") or MagicMock()),
    ):
        controller._build_managed_runtime()

    assert calls == [
        "validate_managed_bind",
        "backup_phase1_stores",
        "registry.initialize",
        "authority.load",
        "sessions",
        "queries",
        "actions",
        "managed_server",
        "enrollment_service",
        "enrollment_server",
        "dashboard_data",
    ]


def test_token_file_rejects_utf8_bom():
    from client.managed_agent import _read_token_file

    with patch("client.managed_agent._read_private_file", return_value=b"\xef\xbb\xbftoken"):
        with patch("pathlib.Path.unlink"):
            with pytest.raises(ValueError, match="without BOM"):
                _read_token_file("C:/private/token.txt")


def test_phase2_runbook_records_resolved_operator_workflow():
    runbook = (
        Path("docs/runbooks/managed-agent-phase2-private-network.md")
        .read_text(encoding="utf-8")
    )

    assert "managed_auth disconnect" not in runbook
    assert "first 8 characters (short ID)" in runbook
    assert "next durable heartbeat authorization check" in runbook
    assert "$Devices = @($DeviceJson | ConvertFrom-Json)" in runbook
    assert "if ($Devices.Count -ne 1)" in runbook
    assert "[Text.Encoding]::UTF8.GetString" in runbook
    assert "$CurrentAside = \"$env:PHANTOMLINK_MANAGED_DB.pre-recovery-$RecoveryStamp\"" in runbook
    assert "Copy-Item -LiteralPath $SelectedDatabaseBackup" in runbook
    assert "DATABASE_INTEGRITY=ok" in runbook
    assert "RECOVERY_RESTART=PASS" in runbook


def test_phase2_recovery_guards_copy_and_acl_before_restart_pass():
    runbook = Path(
        "docs/runbooks/managed-agent-phase2-private-network.md"
    ).read_text(encoding="utf-8")
    recovery = runbook.split("Recover a chosen Phase 2 SQLite backup", 1)[1].split(
        "Then repeat Sections 3, 8, and 10.", 1
    )[0]

    assert "$ErrorActionPreference = 'Stop'" in recovery
    assert (
        "Copy-Item -LiteralPath $SelectedDatabaseBackup "
        "-Destination $env:PHANTOMLINK_MANAGED_DB -Force -ErrorAction Stop"
    ) in recovery
    assert (
        "Wait-Process -Id $ControllerPid -Timeout 10 -ErrorAction Stop" in recovery
    )
    assert (
        "Get-Process -Id $ControllerPid -ErrorAction SilentlyContinue" in recovery
    )
    assert "if ($ControllerStillRunning)" in recovery
    assert "icacls" not in recovery
    assert "$Acl = [Security.AccessControl.FileSecurity]::new()" in recovery
    assert "$Acl.SetAccessRuleProtection($true, $false)" in recovery
    assert "[IO.File]::SetAccessControl($Path, $Acl)" in recovery
    assert "$AllowedSidValues = @(" in recovery
    assert "'S-1-5-18'" in recovery
    assert "'S-1-5-32-544'" in recovery
    assert "if ($Rule.AccessControlType -ne $Allow)" in recovery
    assert "if ($Rule.FileSystemRights -ne $FullControl)" in recovery
    assert "if ($AllowedSidValues -notcontains $RuleSid)" in recovery
    assert "if ($Rule.IsInherited)" in recovery
    assert "DATABASE_ACL_PRIVATE=PASS" in recovery
    assert "$SelectedDatabaseHash = (Get-FileHash" in recovery
    assert "$RestoredDatabaseHash = (Get-FileHash" in recovery
    assert "if ($RestoredDatabaseHash -ne $SelectedDatabaseHash)" in recovery
    assert recovery.index("Get-Process -Id $ControllerPid") < recovery.index(
        "'CONTROLLER_STOPPED=PASS'"
    )
    assert recovery.index("'CONTROLLER_STOPPED=PASS'") < recovery.index(
        "Copy-Item -LiteralPath $SelectedDatabaseBackup"
    )
    assert recovery.index("if ($RestoredDatabaseHash -ne $SelectedDatabaseHash)") < recovery.index(
        "Set-PhantomLinkPrivateAcl -LiteralPath @($env:PHANTOMLINK_MANAGED_DB"
    )
    assert recovery.index("'DATABASE_ACL_PRIVATE=PASS'") < recovery.index(
        "PRAGMA integrity_check"
    )
    assert recovery.index("PRAGMA integrity_check") < recovery.index(
        "Start-Process -FilePath $Python"
    )
    assert recovery.index("Start-Process -FilePath $Python") < recovery.index(
        "'RECOVERY_RESTART=PASS'"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL probe")
def test_phase2_recovery_acl_reset_removes_everyone_rule(tmp_path):
    runbook = Path(
        "docs/runbooks/managed-agent-phase2-private-network.md"
    ).read_text(encoding="utf-8")
    helper = runbook.split("# ACL-RESET-HELPER-BEGIN", 1)[1].split(
        "# ACL-RESET-HELPER-END", 1
    )[0]
    probe = tmp_path / "acl-probe.db"
    probe.write_bytes(b"probe")
    environment = os.environ.copy()
    environment["PHANTOMLINK_ACL_PROBE"] = str(probe)
    script = f"""
$ErrorActionPreference = 'Stop'
{helper}
$Path = $env:PHANTOMLINK_ACL_PROBE
$Everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
$Acl = [Security.AccessControl.FileSecurity]::new()
$Acl.SetAccessRuleProtection($true, $false)
$Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $Everyone,
    [Security.AccessControl.FileSystemRights]::FullControl,
    [Security.AccessControl.AccessControlType]::Allow
))
[IO.File]::SetAccessControl($Path, $Acl)
Set-PhantomLinkPrivateAcl -LiteralPath @($Path)
$After = Get-Acl -LiteralPath $Path -ErrorAction Stop
$AfterSids = @($After.Access | ForEach-Object {{
    $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
}})
if ($AfterSids -contains 'S-1-1-0') {{ throw 'Everyone rule remains' }}
'ACL_PROBE=PASS'
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ACL_PROBE=PASS"


def test_controller_keeps_managed_services_disabled_without_certificates(capsys):
    import C2.C2 as controller

    legacy_socket = MagicMock()
    with (
        patch.object(controller, "managed_phase2_enabled", return_value=False),
        patch.object(controller, "managed_phase2_configured", return_value=False),
        patch.object(controller, "_build_managed_runtime") as build,
        patch.object(controller.socket, "socket", return_value=legacy_socket),
        patch.object(controller.threading, "Thread") as thread_type,
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        controller.main()

    assert capsys.readouterr().out.count("Managed services disabled") == 1
    build.assert_not_called()
    legacy_socket.bind.assert_called_once_with((controller.HOST, controller.PORT))
    assert thread_type.called


def test_partial_phase2_configuration_starts_neither_listener(capsys):
    import C2.C2 as controller

    with (
        patch.object(controller, "managed_phase2_enabled", return_value=False),
        patch.object(controller, "managed_phase2_configured", return_value=True),
        patch.object(controller, "_build_managed_runtime") as build,
        patch.object(controller, "_start_managed_runtime") as start,
        patch.object(controller.socket, "socket", return_value=MagicMock()),
        patch.object(controller.threading, "Thread"),
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        controller.main()

    output = capsys.readouterr().out
    assert output.count(
        "[!] Managed Phase 2 configuration incomplete; managed listeners not started"
    ) == 1
    build.assert_not_called()
    start.assert_not_called()


def test_managed_bind_is_deferred_to_exact_private_address_validation():
    environment = os.environ.copy()
    environment.pop("PHANTOMLINK_MANAGED_HOST", None)
    default = subprocess.run(
        [sys.executable, "-c", "import config; print(config.MANAGED_HOST)"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert default.returncode == 0
    assert default.stdout.strip() == ""
    environment["PHANTOMLINK_MANAGED_HOST"] = "10.8.0.1"
    private = subprocess.run(
        [sys.executable, "-c", "import config; print(config.MANAGED_HOST)"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert private.returncode == 0
    assert private.stdout.strip() == "10.8.0.1"


def test_controller_starts_dashboard_with_managed_data_and_cleans_up():
    import C2.C2 as controller

    legacy_socket = MagicMock()
    runtime = MagicMock()
    runtime.dashboard_data = object()
    order = []
    with (
        patch.object(controller, "managed_phase2_enabled", return_value=True),
        patch.object(controller, "_build_managed_runtime", return_value=runtime),
        patch.object(
            controller,
            "_start_managed_runtime",
            side_effect=lambda *_: order.extend(
                ["managed-listener", "enrollment-listener"]
            ),
        ) as start,
        patch.object(controller, "_stop_managed_runtime", return_value=[]) as stop,
        patch.object(controller.socket, "socket", return_value=legacy_socket),
        patch.object(controller.threading, "Thread") as thread_type,
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        thread_type.return_value.start.side_effect = lambda: order.append(
            "dashboard-thread"
        )
        controller.main()

    start.assert_called_once_with(runtime)
    stop.assert_called_once_with(runtime)
    dashboard_call = thread_type.call_args_list[0]
    assert dashboard_call.kwargs["target"] is controller.start_dashboard
    assert dashboard_call.kwargs["args"][4] is runtime.dashboard_data
    assert order[:3] == [
        "managed-listener",
        "enrollment-listener",
        "dashboard-thread",
    ]
    legacy_socket.close.assert_called_once_with()


def test_dashboard_start_failure_is_nonfatal_after_listeners_start(capsys):
    import C2.C2 as controller

    runtime = MagicMock(dashboard_data=object())
    legacy_socket = MagicMock()
    with (
        patch.object(controller, "managed_phase2_enabled", return_value=True),
        patch.object(controller, "_build_managed_runtime", return_value=runtime),
        patch.object(controller, "_start_managed_runtime") as start,
        patch.object(controller, "_stop_managed_runtime", return_value=[]) as stop,
        patch.object(controller.socket, "socket", return_value=legacy_socket),
        patch.object(controller.threading, "Thread") as thread_type,
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        starts = iter([RuntimeError("dashboard boom"), None, None])
        def start_thread():
            result = next(starts, None)
            if isinstance(result, Exception):
                raise result
        thread_type.return_value.start.side_effect = start_thread
        controller.main()

    start.assert_called_once_with(runtime)
    stop.assert_called_once_with(runtime)
    assert "[!] Dashboard failed to start; managed services remain active" in capsys.readouterr().out


def test_listener_start_failure_cleans_up_before_dashboard_start(capsys):
    import C2.C2 as controller

    runtime = MagicMock(dashboard_data=object())
    with (
        patch.object(controller, "managed_phase2_enabled", return_value=True),
        patch.object(controller, "_build_managed_runtime", return_value=runtime),
        patch.object(controller, "_start_managed_runtime", side_effect=RuntimeError("bind")),
        patch.object(controller, "_stop_managed_runtime", return_value=[]) as stop,
        patch.object(controller.socket, "socket", return_value=MagicMock()),
        patch.object(controller.threading, "Thread") as thread_type,
        patch.object(controller.time, "sleep"),
        patch.object(controller._console, "prompt", return_value="quit"),
    ):
        controller.main()

    stop.assert_called_once_with(runtime)
    dashboard_threads = [
        call for call in thread_type.call_args_list
        if call.kwargs.get("target") is controller.start_dashboard
    ]
    assert dashboard_threads == []
    assert "[!] Managed Phase 2 startup failed; managed listeners not started" in capsys.readouterr().out


def test_managed_runtime_shutdown_is_reverse_and_bounded():
    import C2.C2 as controller

    calls = []
    managed = MagicMock()
    managed.stop.side_effect = lambda timeout: calls.append(("managed.stop", timeout))
    enrollment = MagicMock()
    enrollment.shutdown.side_effect = lambda: calls.append(("enrollment.shutdown",))
    enrollment.stop_accepting.side_effect = lambda: calls.append(
        ("enrollment.stop_accepting",)
    )
    enrollment_thread = MagicMock(ident=1, name="enrollment-listener")
    enrollment_thread.is_alive.side_effect = [True, False]
    managed_thread = MagicMock(ident=2, name="managed-listener")
    managed_thread.is_alive.return_value = False
    enrollment_thread.join.side_effect = lambda timeout: calls.append(
        ("enrollment.join", timeout)
    )
    managed_thread.join.side_effect = lambda timeout: calls.append(
        ("managed.join", timeout)
    )
    runtime = MagicMock(
        enrollment_server=enrollment,
        managed_server=managed,
        enrollment_thread=enrollment_thread,
        managed_thread=managed_thread,
        stop_event=threading.Event(),
    )

    assert controller._stop_managed_runtime(runtime) == []

    assert calls == [
        ("enrollment.shutdown",),
        ("enrollment.stop_accepting",),
        ("managed.stop", 5),
        ("enrollment.join", 5),
        ("managed.join", 5),
    ]
    assert runtime.stop_event.is_set()


def test_managed_runtime_shutdown_continues_after_stuck_enrollment_shutdown():
    import C2.C2 as controller

    enrollment = MagicMock()
    managed = MagicMock()
    worker = MagicMock()
    worker.is_alive.return_value = True
    runtime = MagicMock(
        enrollment_server=enrollment,
        managed_server=managed,
        enrollment_thread=MagicMock(ident=1, name="enrollment-listener"),
        managed_thread=MagicMock(ident=2, name="managed-listener"),
        stop_event=threading.Event(),
    )
    runtime.enrollment_thread.is_alive.return_value = True
    with patch.object(controller.threading, "Thread", return_value=worker) as thread_type:
        errors = controller._stop_managed_runtime(runtime)

    enrollment.stop_accepting.assert_called_once_with()
    assert thread_type.call_args.kwargs["name"] == "enrollment-shutdown"
    assert thread_type.call_args.kwargs["daemon"] is True
    worker.start.assert_called_once_with()
    assert worker.join.call_count == 2
    worker.join.assert_any_call(timeout=5)
    managed.stop.assert_called_once_with(timeout=5)
    assert "enrollment shutdown timed out" in errors
