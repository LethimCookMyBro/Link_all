import hashlib
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone

import pytest

from C2.managed_registry import (
    AuditEvent,
    CertificateRejected,
    DeviceSummary,
    EnrollmentTokenRejected,
    IssuedDeviceCertificate,
    ManagedRegistry,
    SchemaVersionRejected,
    backup_phase1_stores,
)

FIXED_NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def issued_certificate():
    return IssuedDeviceCertificate(
        b"public-certificate",
        "AA:BB:CC",
        "1001",
        "2027-08-13T06:00:00Z",
    )


@pytest.fixture
def registry(tmp_path):
    value = ManagedRegistry(tmp_path / "managed.db", now=lambda: FIXED_NOW)
    value.initialize()
    return value


def test_initialize_creates_exact_version_one_schema(tmp_path):
    registry = ManagedRegistry(tmp_path / "managed.db")
    registry.initialize()
    with registry._connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "schema_version",
            "devices",
            "enrollment_tokens",
            "audit_events",
        } <= tables
        assert [
            tuple(row)
            for row in connection.execute("SELECT version FROM schema_version")
        ] == [(1,)]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_display_models_are_frozen():
    field_names = tuple(field.name for field in fields(DeviceSummary))
    assert field_names == (
        "agent_id",
        "display_name",
        "state",
        "last_vpn_ip",
        "last_seen_at",
        "certificate_not_after",
        "agent_version",
    )
    with pytest.raises(FrozenInstanceError):
        DeviceSummary(
            "a", "pc", "ENROLLED", None, None, "2026-01-01T00:00:00Z", "2"
        ).state = "ONLINE"


def test_initialize_rejects_a_newer_schema(tmp_path):
    path = tmp_path / "managed.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_version VALUES (2, '2026-08-13T06:00:00Z')"
        )

    with pytest.raises(SchemaVersionRejected, match="newer than supported"):
        ManagedRegistry(path, now=lambda: FIXED_NOW).initialize()


def test_token_is_atomic_single_use_across_registry_instances(
    tmp_path, issued_certificate
):
    path = tmp_path / "managed.db"
    first = ManagedRegistry(path, now=lambda: FIXED_NOW)
    second = ManagedRegistry(path, now=lambda: FIXED_NOW)
    first.initialize()
    token = first.issue_token(600)
    barrier = threading.Barrier(2)
    outcomes = []

    def consume(current_registry):
        barrier.wait()
        try:
            outcomes.append(
                current_registry.consume_token_and_enroll(
                    token,
                    issued_certificate,
                    "pc-01",
                    "2.0",
                    "enrollment",
                    "corr-1",
                ).agent_id
            )
        except EnrollmentTokenRejected:
            outcomes.append("rejected")

    threads = [
        threading.Thread(target=consume, args=(current_registry,))
        for current_registry in (first, second)
    ]
    [thread.start() for thread in threads]
    [thread.join(2) for thread in threads]
    assert len([value for value in outcomes if value != "rejected"]) == 1
    assert outcomes.count("rejected") == 1


def test_expired_or_malformed_token_is_rejected(tmp_path, issued_certificate):
    current = [FIXED_NOW]
    registry = ManagedRegistry(tmp_path / "managed.db", now=lambda: current[0])
    registry.initialize()
    token = registry.issue_token(1)
    current[0] += timedelta(seconds=1)

    for rejected in (token, "not-a-token"):
        with pytest.raises(EnrollmentTokenRejected):
            registry.consume_token_and_enroll(
                rejected,
                issued_certificate,
                "pc-01",
                "2.0",
                "enrollment",
                "corr-expired",
            )


@pytest.mark.parametrize("ttl", [0, -1, True, float("inf")])
def test_token_ttl_must_be_positive_finite(registry, ttl):
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        registry.issue_token(ttl)


def test_enrollment_persists_public_metadata_and_audit(registry, issued_certificate):
    token = registry.issue_token()
    detail = registry.consume_token_and_enroll(
        token,
        issued_certificate,
        "pc-01",
        "2.0",
        "enrollment",
        "corr-1",
    )

    assert detail == registry.get_device(detail.agent_id)
    assert detail.state == "ENROLLED"
    assert detail.display_name == "pc-01"
    assert detail.certificate_fingerprint == "AA:BB:CC"
    assert issued_certificate.certificate_pem not in registry.path.read_bytes()
    assert registry.list_audit_events() == (
        AuditEvent(
            1,
            "2026-08-13T06:00:00.000000Z",
            "enrollment",
            "ENROLLMENT_SUCCEEDED",
            detail.agent_id,
            "SUCCEEDED",
            None,
            "corr-1",
            (
                ("agent_version", "2.0"),
                ("certificate_fingerprint", "AA:BB:CC"),
                ("certificate_serial", "1001"),
            ),
        ),
    )


@pytest.mark.parametrize(
    "forbidden", ["token", "private_key", "certificate_bundle", "dpapi_blob", "secret"]
)
def test_audit_details_reject_credential_fields(registry, forbidden):
    with pytest.raises(ValueError, match="forbidden audit detail"):
        registry.append_audit(
            actor="test",
            action="TEST",
            target_agent_id=None,
            result="FAILED",
            reason=None,
            correlation_id="corr-1",
            details={forbidden: "value"},
        )


def test_audit_details_are_canonical_bounded_and_display_safe(registry):
    event = registry.append_audit(
        actor="test",
        action="AUTH",
        target_agent_id=None,
        result="SUCCEEDED",
        reason=None,
        correlation_id="corr-2",
        details={"status_code": 200, "peer_ip": "10.0.0.2"},
    )
    assert event.details == (("peer_ip", "10.0.0.2"), ("status_code", "200"))
    with registry._connection() as connection:
        assert connection.execute(
            "SELECT details_json FROM audit_events WHERE id = ?", (event.id,)
        ).fetchone()[0] == '{"peer_ip":"10.0.0.2","status_code":200}'

    with pytest.raises(ValueError, match="audit details too large"):
        registry.append_audit(
            actor="test",
            action="AUTH",
            target_agent_id=None,
            result="FAILED",
            reason=None,
            correlation_id="corr-3",
            details={"session_id": "x" * 4096},
        )


def test_certificate_renewal_replaces_identity_and_preserves_agent(
    registry, issued_certificate
):
    token = registry.issue_token()
    original = registry.consume_token_and_enroll(
        token, issued_certificate, "pc", "2.0", "enrollment", "corr-enroll"
    )
    renewed_certificate = IssuedDeviceCertificate(
        b"renewed-public-certificate",
        "DD:EE:FF",
        "1002",
        "2028-08-13T06:00:00Z",
    )

    renewed = registry.renew_certificate(
        original.agent_id,
        original.certificate_fingerprint,
        renewed_certificate,
        "renewal",
        "corr-renew",
    )

    assert renewed.agent_id == original.agent_id
    assert renewed.certificate_fingerprint == "DD:EE:FF"
    assert not registry.is_connection_allowed(original.agent_id, "AA:BB:CC", "1001")
    assert registry.is_connection_allowed(original.agent_id, "DD:EE:FF", "1002")

    with pytest.raises(CertificateRejected, match="current certificate"):
        registry.renew_certificate(
            original.agent_id,
            "wrong",
            issued_certificate,
            "renewal",
            "corr-rejected",
        )


def test_last_seen_and_revoke_are_durable_and_idempotent(registry, issued_certificate):
    device = registry.consume_token_and_enroll(
        registry.issue_token(),
        issued_certificate,
        "pc",
        "2.0",
        "enrollment",
        "corr-enroll",
    )
    seen_at = FIXED_NOW + timedelta(minutes=1)
    registry.touch_last_seen(device.agent_id, "10.0.0.8", seen_at)
    touched = registry.get_device(device.agent_id)
    assert touched.state == "OFFLINE"
    assert touched.last_vpn_ip == "10.0.0.8"
    assert touched.last_seen_at == "2026-08-13T06:01:00.000000Z"

    first = registry.revoke_device(device.agent_id, "operator", "retired", "corr-1")
    second = registry.revoke_device(device.agent_id, "operator", "retired", "corr-2")
    assert first.code == "REVOKED"
    assert second.code == "ALREADY_REVOKED"
    assert not registry.is_connection_allowed(device.agent_id, "AA:BB:CC", "1001")
    assert registry.get_device(device.agent_id).state == "REVOKED"


def test_missing_device_operations_are_stable(registry, issued_certificate):
    assert registry.get_device("missing") is None
    assert registry.revoke_device("missing", "operator", "retired", "corr-1").code == "NOT_FOUND"
    assert not registry.is_connection_allowed("missing", "AA", "1")
    with pytest.raises(CertificateRejected, match="not found"):
        registry.renew_certificate(
            "missing", "AA", issued_certificate, "renewal", "corr-renew"
        )


def test_phase1_backup_hashes_bytes_without_importing_credentials(tmp_path):
    store = tmp_path / "managed-store"
    store.mkdir()
    (store / "tokens.json").write_bytes(b'{"legacy":true}')
    (store / "devices.bin").write_bytes(b"protected-secret-data")

    backups = backup_phase1_stores(
        store, tmp_path / "backup", now=lambda: FIXED_NOW
    )

    assert {item.source_name for item in backups} == {"tokens.json", "devices.bin"}
    assert all(
        item.sha256 == hashlib.sha256(item.backup_path.read_bytes()).hexdigest()
        for item in backups
    )
    assert not (store / "managed.db").exists()
    manifest_bytes = (tmp_path / "backup" / "manifest.json").read_bytes()
    assert b"protected-secret-data" not in manifest_bytes
    manifest = json.loads(manifest_bytes)
    assert manifest["created_at"] == "2026-08-13T06:00:00.000000Z"
    assert [item["source_name"] for item in manifest["stores"]] == [
        "devices.bin",
        "tokens.json",
    ]
    assert (store / "tokens.json").exists()
    assert (store / "devices.bin").exists()


def test_runtime_store_factory_uses_only_durable_sqlite(tmp_path):
    from C2.managed_auth import _store_services

    store = tmp_path / "store"
    registry = _store_services(store)

    assert registry.path == store / "managed.db"
    assert registry.list_device_records() == ()
    assert not (store / "tokens.json").exists()
    assert not (store / "devices.bin").exists()
