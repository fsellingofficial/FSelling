"""I04/D02 migration framework contract tests; every database is temporary."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.errors import (
    BackupVerificationError,
    ChecksumError,
    ControlSchemaError,
    GraphError,
    LeaseBusyError,
    RevisionStateError,
    SchemaMismatchError,
    StaleFenceError,
    TopologyError,
)
from fselling.migration.schema import (
    CONTROL_DDL,
    CONTROL_SCHEMA_FINGERPRINT,
    CONTROL_TABLES,
    CONTROL_TRIGGERS,
    schema_fingerprint,
    verify_control_schema,
)
from fselling.migration.topology import StaticInventory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "9cf710606e55005766a0c7d790a366e0611695f6"
PLAN3 = "0013_roles_returns_approval_r3"
PLAN4 = "0014_session_device_safety_r4"


def _coordinator(path: Path, **kwargs) -> MigrationCoordinator:
    return MigrationCoordinator(
        path,
        project_root=PROJECT_ROOT,
        inventory_provider=kwargs.pop("inventory_provider", StaticInventory()),
        **kwargs,
    )


def _legacy_fixture(path: Path) -> None:
    """Build legacy state by executing baseline code, never new revision DDL."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", BASELINE_COMMIT],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory(
        prefix="fselling_9cf7106_", dir=path.parent
    ) as extracted:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(extracted, filter="data")
        env = os.environ.copy()
        env.update(
            {
                "DB_PATH": str(path),
                "LOG_FILE": str(path.with_suffix(".legacy.log")),
                "UPLOAD_DIR": str(path.parent / "legacy-uploads"),
                "PYTHONPATH": extracted,
                "SECRET_KEY": "test-only",
                "PAYMENT_WEBHOOK_SECRET": "test-only",
                "ADMIN_INITIAL_PASSWORD": "",
                "SMTP_HOST": "",
                "GEMINI_API_KEY": "",
                "FLY_APP_NAME": "",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from fselling.core.bootstrap import initialize; initialize()",
            ],
            cwd=extracted,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr


def _objects(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def _normalized_checksum(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_fresh_root_to_head_and_restart_noop(tmp_path):
    database = tmp_path / "fresh.db"
    coordinator = _coordinator(database)

    assert coordinator.check().classification == "MISSING"
    assert coordinator.init() == ["0001_legacy_9cf7106_baseline"]
    assert coordinator.status().current_revision == "0001_legacy_9cf7106_baseline"
    assert coordinator.upgrade("head") == [
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]
    report = coordinator.verify()
    assert report.current_revision == report.head_revision

    assert coordinator.init() == []
    assert coordinator.upgrade("head") == []
    assert coordinator.verify().database_uuid == report.database_uuid


def _insert_fixed_subscription_principals(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO users (
               id, username, hashed_password, role, is_verified, is_active,
               failed_login_count, verification_attempts
           ) VALUES (1, 'fixed-clock-owner', 'x', 'SELLER', 1, 1, 0, 0)"""
    )
    connection.execute("INSERT INTO shops (id, name, is_active) VALUES (1, 'S1', 1)")
    connection.execute("INSERT INTO shops (id, name, is_active) VALUES (2, 'S2', 1)")


def _insert_fixed_checkout(
    connection: sqlite3.Connection,
    *,
    checkout_id: int,
    shop_id: int,
    status: str,
    activated_at: str | None = None,
    entitlement_starts_at: str | None = None,
    entitlement_ends_at: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO subscription_checkouts (
               id, shop_id, reference_code, cycle, amount_due_vnd,
               duration_days, status, received_amount_vnd,
               refund_due_amount_vnd, operation_id, operation_fingerprint,
               created_by_user_id, created_at, expires_at, activated_at,
               entitlement_starts_at, entitlement_ends_at
           ) VALUES (?, ?, ?, 'MONTHLY', 1000, 30, ?, 0, 0, ?, ?, 1,
                     '1999-12-01 00:00:00', '2000-01-01 00:00:00', ?, ?, ?)""",
        (
            checkout_id,
            shop_id,
            f"FIXED{checkout_id}",
            status,
            f"op-fixed-{checkout_id}",
            f"fp-fixed-{checkout_id}",
            activated_at,
            entitlement_starts_at,
            entitlement_ends_at,
        ),
    )


def test_startup_verify_ignores_runtime_expiry_of_open_checkouts(tmp_path):
    from fselling.migration.coordinator import verify_database_for_startup

    database = tmp_path / "runtime-expiry-is-not-corruption.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()
    coordinator.verify()

    connection = sqlite3.connect(database)
    try:
        _insert_fixed_subscription_principals(connection)
        _insert_fixed_checkout(
            connection, checkout_id=1, shop_id=1, status="PENDING"
        )
        _insert_fixed_checkout(
            connection, checkout_id=2, shop_id=2, status="UNDERPAID"
        )
        connection.commit()
    finally:
        connection.close()

    report = verify_database_for_startup(
        database, inventory_provider=StaticInventory()
    )
    assert report.current_revision == PLAN4
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT status FROM subscription_checkouts ORDER BY id"
        ).fetchall() == [("PENDING",), ("UNDERPAID",)]
    finally:
        connection.close()


def test_startup_verify_staff_without_role_fails_closed(tmp_path):
    database = tmp_path / "staff-role-null-is-corruption.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """INSERT INTO users (
                   id, username, hashed_password, role, is_verified, is_active,
                   failed_login_count, verification_attempts, staff_role
               ) VALUES (1, 'invalid-staff', 'x', 'STAFF', 1, 1, 0, 0, NULL)"""
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="historical baseline invariant"):
        coordinator.verify()


@pytest.mark.parametrize(
    "mutation,expected_error,match",
    [
        ("missing_entitlement", RuntimeError, "entitlement"),
        ("reversed_entitlement", RuntimeError, "entitlement"),
        ("invalid_product_code", RuntimeError, "product code"),
        ("missing_schema_index", SchemaMismatchError, "fingerprint mismatch"),
        ("journal_mismatch", RevisionStateError, "Journal"),
    ],
)
def test_startup_verify_stable_invariants_remain_fail_closed(
    tmp_path, mutation, expected_error, match
):
    database = tmp_path / f"stable-invariant-{mutation}.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()

    connection = sqlite3.connect(database)
    try:
        if mutation in {
            "missing_entitlement",
            "reversed_entitlement",
            "invalid_product_code",
        }:
            _insert_fixed_subscription_principals(connection)
        if mutation == "missing_entitlement":
            _insert_fixed_checkout(
                connection,
                checkout_id=1,
                shop_id=1,
                status="PAID",
                activated_at="2000-01-02 00:00:00",
            )
        elif mutation == "reversed_entitlement":
            # Deliberately inject corruption that the durable DB CHECK normally
            # prevents, so startup verification itself is still exercised.
            connection.execute("PRAGMA ignore_check_constraints = ON")
            _insert_fixed_checkout(
                connection,
                checkout_id=1,
                shop_id=1,
                status="PAID",
                activated_at="2000-01-02 00:00:00",
                entitlement_starts_at="2000-02-01 00:00:00",
                entitlement_ends_at="2000-01-01 00:00:00",
            )
        elif mutation == "invalid_product_code":
            connection.execute(
                """INSERT INTO products
                   (id, code, name, price, stock, is_active, shop_id, track_batches)
                   VALUES (1, '', 'P', 10, 1, 1, 1, 0)"""
            )
        elif mutation == "missing_schema_index":
            connection.execute("DROP INDEX ix_fs_migration_work_items_state")
        elif mutation == "journal_mismatch":
            database_uuid = connection.execute(
                "SELECT database_uuid FROM fs_migration_control WHERE singleton=1"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO fs_migration_revision_journal (
                       revision, down_revision, checksum, database_uuid,
                       operation_key, attempt_no, fence_token, applied_at
                   ) VALUES ('tampered', '0002_i04_operational_tables', ?, ?,
                             'tampered-journal', 1, 1, '2000-01-01T00:00:00Z')""",
                ("0" * 64, database_uuid),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(expected_error, match=match):
        coordinator.verify()


def test_exact_legacy_9cf7106_adoption_requires_backup_then_upgrades(tmp_path):
    database = tmp_path / "legacy.db"
    backup = tmp_path / "legacy.verified.db"
    _legacy_fixture(database)
    coordinator = _coordinator(database)

    assert coordinator.check().classification == "LEGACY_EXACT"
    assert not backup.exists()
    result = coordinator.adopt_legacy(backup)
    assert result.path == backup
    assert result.size > 0 and len(result.sha256) == 64
    assert coordinator.status().current_revision == "0001_legacy_9cf7106_baseline"
    assert coordinator.upgrade() == [
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]
    coordinator.verify()


def test_adopt_rejects_live_database_as_backup_before_mutation(tmp_path):
    database = tmp_path / "legacy-live-path.db"
    _legacy_fixture(database)

    with pytest.raises(BackupVerificationError, match="live database"):
        _coordinator(database).adopt_legacy(database.parent / "." / database.name)

    assert not CONTROL_TABLES.intersection(_objects(database))


def test_first_adopt_rejects_existing_same_schema_database_before_mutation(tmp_path):
    database = tmp_path / "legacy-live.db"
    unrelated = tmp_path / "unrelated-same-schema.db"
    _legacy_fixture(database)
    _legacy_fixture(unrelated)
    unrelated_digest = hashlib.sha256(unrelated.read_bytes()).hexdigest()

    with pytest.raises(BackupVerificationError, match="existing adoption backup"):
        _coordinator(database).adopt_legacy(unrelated)

    assert not CONTROL_TABLES.intersection(_objects(database))
    assert hashlib.sha256(unrelated.read_bytes()).hexdigest() == unrelated_digest


@pytest.mark.parametrize("mutation", ["partial", "extra"])
def test_partial_or_mismatched_legacy_fails_closed_before_control(tmp_path, mutation):
    database = tmp_path / f"legacy-{mutation}.db"
    _legacy_fixture(database)
    connection = sqlite3.connect(database)
    try:
        if mutation == "partial":
            connection.execute("DROP INDEX ux_order_payments_idempotency_key")
        else:
            connection.execute("CREATE TABLE unexpected_legacy_table (id INTEGER)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SchemaMismatchError):
        _coordinator(database).check()
    assert not CONTROL_TABLES.intersection(_objects(database))


def _control_memory(version=1, fingerprint=CONTROL_SCHEMA_FINGERPRINT):
    connection = sqlite3.connect(":memory:")
    for statement in CONTROL_DDL:
        connection.execute(statement)
    if version != 1:
        connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute(
        """INSERT INTO fs_migration_control
           (singleton, schema_version, schema_fingerprint, database_uuid, created_at)
           VALUES (1, ?, ?, 'db-test', '2026-08-10T00:00:00Z')""",
        (version, fingerprint),
    )
    connection.execute(
        """INSERT INTO fs_migration_lock
           (lock_name, owner_id, lease_expires_at, fence_token, updated_at)
           VALUES ('schema', NULL, NULL, 0, '2026-08-10T00:00:00Z')"""
    )
    return connection


@pytest.mark.parametrize(
    "version,fingerprint",
    [(2, CONTROL_SCHEMA_FINGERPRINT), (1, "0" * 64)],
)
def test_control_version_or_stored_fingerprint_mismatch(version, fingerprint):
    connection = _control_memory(version, fingerprint)
    try:
        with pytest.raises(ControlSchemaError):
            verify_control_schema(connection)
    finally:
        connection.close()


def test_control_shape_mismatch_is_rejected():
    connection = _control_memory()
    try:
        connection.execute("DROP TRIGGER trg_fs_migration_attempts_no_delete")
        with pytest.raises(ControlSchemaError):
            verify_control_schema(connection)
    finally:
        connection.close()


def test_revision_campaign_and_attempt_state_are_independent(tmp_path):
    database = tmp_path / "states.db"
    cancelled = _coordinator(database, cancel_check=lambda: True)
    with pytest.raises(Exception, match="cancelled"):
        cancelled.init()

    coordinator = _coordinator(database)
    assert coordinator.init() == ["0001_legacy_9cf7106_baseline"]
    coordinator.upgrade()
    lease = coordinator.acquire_lease("campaign-owner")
    try:
        coordinator.create_campaign(lease, "i04-test")
        coordinator.advance_campaign(
            lease,
            "i04-test",
            expected_phase="PREFLIGHT",
            expected_version=0,
            next_phase="EXPAND",
        )
    finally:
        coordinator.release_lease(lease)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            PLAN4
        )
        assert connection.execute(
            "SELECT phase, phase_version FROM fs_migration_campaigns WHERE campaign_key='i04-test'"
        ).fetchone() == ("EXPAND", 1)
        attempts = connection.execute(
            """SELECT attempt_no, state FROM fs_migration_attempts
               WHERE operation_key='revision:0001_legacy_9cf7106_baseline'
               ORDER BY attempt_no"""
        ).fetchall()
        assert attempts == [(1, "FAILED_RETRYABLE"), (2, "SUCCEEDED")]
    finally:
        connection.close()


def _copy_graph(tmp_path: Path) -> Path:
    import shutil

    root = tmp_path / "project"
    root.mkdir()
    shutil.copy2(PROJECT_ROOT / "alembic.ini", root / "alembic.ini")
    shutil.copytree(PROJECT_ROOT / "migrations", root / "migrations")
    return root


def test_linear_graph_and_checksum_manifest(tmp_path):
    graph = _coordinator(tmp_path / "unused.db")._graph()
    assert [item.down_revision for item in graph.revisions] == [
        None,
        "0001_legacy_9cf7106_baseline",
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
            "0009_fnb_table_service_r1a",
            "0010_fnb_kitchen_stock_r1b",
            "0011_fnb_checkout_r1c",
            "0012_fnb_ticket_service_handoff",
            PLAN3,
        ]
    assert graph.root.revision == "0001_legacy_9cf7106_baseline"
    assert graph.head.revision == PLAN4

    copied = _copy_graph(tmp_path)
    revision = copied / "migrations/versions/0002_i04_operational_tables.py"
    revision.write_text(revision.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    with pytest.raises(ChecksumError):
        MigrationCoordinator(
            tmp_path / "unused2.db",
            project_root=copied,
            inventory_provider=StaticInventory(),
        )._graph()


def test_forbidden_mutable_helper_import_is_rejected_before_db_side_effect(tmp_path):
    copied = _copy_graph(tmp_path)
    revision = copied / "migrations/versions/0002_i04_operational_tables.py"
    revision.write_text(
        "import fselling.models\n" + revision.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest_path = copied / "migrations/checksums.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revisions"]["0002_i04_operational_tables"]["sha256"] = (
        _normalized_checksum(revision)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database = tmp_path / "must-not-exist.db"

    with pytest.raises(GraphError):
        MigrationCoordinator(
            database,
            project_root=copied,
            inventory_provider=StaticInventory(),
        ).init()
    assert not database.exists()


def test_nontransactional_operation_is_rejected_before_db_side_effect(tmp_path):
    copied = _copy_graph(tmp_path)
    revision = copied / "migrations/versions/0002_i04_operational_tables.py"
    revision.write_text(
        revision.read_text(encoding="utf-8") + '\nFORBIDDEN = "VACUUM"\n',
        encoding="utf-8",
    )
    manifest_path = copied / "migrations/checksums.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revisions"]["0002_i04_operational_tables"]["sha256"] = (
        _normalized_checksum(revision)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database = tmp_path / "must-not-exist-nontransactional.db"

    with pytest.raises(GraphError, match="non-transactional"):
        MigrationCoordinator(
            database,
            project_root=copied,
            inventory_provider=StaticInventory(),
        ).init()
    assert not database.exists()


@pytest.mark.parametrize("stage", ["after_ddl", "after_version", "after_journal", "after_verify"])
def test_ddl_version_journal_and_success_roll_back_together(tmp_path, stage):
    database = tmp_path / f"rollback-{stage}.db"

    def fail(selected, _revision):
        if selected == stage:
            raise RuntimeError(f"fault at {stage}")

    coordinator = _coordinator(database, fault_hook=fail)
    with pytest.raises(RuntimeError, match=stage):
        coordinator.init()

    objects = _objects(database)
    assert "users" not in objects
    assert "alembic_version" not in objects
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM fs_migration_revision_journal"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM fs_migration_attempts"
        ).fetchone()[0] == "FAILED_RETRYABLE"
    finally:
        connection.close()


def test_crash_after_commit_before_cli_response_reruns_noop(tmp_path):
    database = tmp_path / "after-commit.db"

    def crash(stage, _revision):
        if stage == "after_commit":
            raise RuntimeError("simulated CLI response crash")

    with pytest.raises(RuntimeError, match="response crash"):
        _coordinator(database, fault_hook=crash).init(request_id="request-1")

    coordinator = _coordinator(database)
    assert coordinator.init(request_id="request-1") == []
    assert coordinator.upgrade() == [
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]
    coordinator.verify()


def test_two_processes_on_same_file_only_one_active_fence_wins(tmp_path):
    database = tmp_path / "fence.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    code = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[4])
from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory
c = MigrationCoordinator(sys.argv[1], project_root=sys.argv[4], inventory_provider=StaticInventory())
lease = c.acquire_lease('process-one')
Path(sys.argv[2]).write_text(str(lease.fence_token), encoding='utf-8')
deadline = time.time() + 15
while not Path(sys.argv[3]).exists() and time.time() < deadline:
    time.sleep(0.05)
c.release_lease(lease)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(database), str(ready), str(release), str(PROJECT_ROOT)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), process.stderr.read() if process.poll() is not None else ""
        with pytest.raises(LeaseBusyError):
            coordinator.acquire_lease("process-two")
    finally:
        release.write_text("go", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr


def test_stale_runner_cannot_checkpoint_or_write(tmp_path):
    database = tmp_path / "stale.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()
    stale = coordinator.acquire_lease("old-runner")
    coordinator.release_lease(stale)
    winner = coordinator.acquire_lease("new-runner")
    try:
        with pytest.raises(StaleFenceError):
            coordinator.write_checkpoint(
                stale,
                workset_id=1,
                checkpoint_key="cursor",
                value={"id": 10},
            )
        coordinator.write_checkpoint(
            winner,
            workset_id=1,
            checkpoint_key="cursor",
            value={"id": 11},
        )
    finally:
        coordinator.release_lease(winner)


def test_missing_unverified_backup_and_unsupported_topology_are_blocked(tmp_path):
    database = tmp_path / "legacy.db"
    _legacy_fixture(database)
    coordinator = _coordinator(database)
    with pytest.raises(BackupVerificationError):
        coordinator.adopt_legacy(None)
    with pytest.raises(BackupVerificationError):
        coordinator.adopt_legacy(tmp_path / "missing-dir" / "backup.db")
    unverified = tmp_path / "unverified-existing.db"
    unverified.write_bytes(b"not-a-verified-sqlite-backup")
    with pytest.raises(BackupVerificationError):
        coordinator.adopt_legacy(unverified)
    assert not CONTROL_TABLES.intersection(_objects(database))

    unsupported = _coordinator(
        tmp_path / "unused.db",
        inventory_provider=StaticInventory(active_machines=2, sqlite_files=1),
    )
    with pytest.raises(TopologyError):
        unsupported.check()
    assert not (tmp_path / "unused.db").exists()


def test_import_and_failed_web_startup_never_create_or_alter_schema(tmp_path, monkeypatch):
    database = tmp_path / "web-must-not-create.db"
    env = os.environ.copy()
    env.update(
        {
            "DB_PATH": str(database),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "PYTHONPATH": str(PROJECT_ROOT),
            "SECRET_KEY": "test-only",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import fselling.main; print('imported')"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert not database.exists()

    from fselling import main as main_module

    seed = Mock()
    scheduler = Mock()
    monkeypatch.setattr(
        main_module,
        "verify_database_for_startup",
        Mock(side_effect=SchemaMismatchError("test mismatch")),
    )
    monkeypatch.setattr(main_module.bootstrap, "initialize_application_data", seed)
    monkeypatch.setattr(main_module, "BackgroundScheduler", scheduler)
    application = main_module.create_app()

    async def enter_lifespan():
        async with main_module.lifespan(application):
            pass

    with pytest.raises(SchemaMismatchError, match="test mismatch"):
        asyncio.run(enter_lifespan())
    assert application.state.schema_ready is False
    seed.assert_not_called()
    scheduler.assert_not_called()


def test_verified_web_startup_runs_seed_and_scheduler_after_verify(monkeypatch):
    from fselling import main as main_module

    events = []
    report = Mock()
    report.as_dict.return_value = {"current_revision": "0003_i05_integer_vnd_cost_basis"}
    report.current_revision = "0003_i05_integer_vnd_cost_basis"

    def verify(_path):
        events.append("verify")
        return report

    class FakeScheduler:
        def __init__(self, **_kwargs):
            events.append("scheduler-created")

        def add_job(self, *_args, **_kwargs):
            events.append("job-added")

        def start(self):
            events.append("scheduler-started")

        def shutdown(self):
            events.append("scheduler-stopped")

    monkeypatch.setattr(main_module, "verify_database_for_startup", verify)
    monkeypatch.setattr(
        main_module.bootstrap,
        "initialize_application_data",
        lambda: events.append("seed"),
    )
    monkeypatch.setattr(main_module, "BackgroundScheduler", FakeScheduler)
    application = main_module.create_app()

    async def enter_lifespan():
        async with main_module.lifespan(application):
            assert application.state.schema_ready is True
            events.append("serving")

    asyncio.run(enter_lifespan())
    assert events.index("verify") < events.index("seed") < events.index("scheduler-started")
    assert events.index("scheduler-started") < events.index("serving")
    assert events[-1] == "scheduler-stopped"
    assert application.state.schema_ready is False


def test_control_fingerprint_is_checked_in_and_matches_shape():
    connection = _control_memory()
    try:
        actual = schema_fingerprint(
            connection,
            include_names=CONTROL_TABLES | CONTROL_TRIGGERS,
        )
        assert actual == CONTROL_SCHEMA_FINGERPRINT
        verify_control_schema(connection)
    finally:
        connection.close()


def test_real_alembic_receives_external_transaction_and_owns_version(tmp_path, monkeypatch):
    from fselling.migration import coordinator as coordinator_module

    observed = []
    real_upgrade = coordinator_module.command.upgrade

    def wrapped(config, target):
        connection = config.attributes["connection"]
        observed.append(
            (
                target,
                type(connection).__name__,
                connection.in_transaction(),
                connection.connection.driver_connection.in_transaction,
            )
        )
        return real_upgrade(config, target)

    monkeypatch.setattr(coordinator_module.command, "upgrade", wrapped)
    database = tmp_path / "real-alembic.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()

    assert [item[0] for item in observed if not str(item[0]).endswith("baseline")] == [
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]
    assert all(item[1] == "Connection" and item[2] and item[3] for item in observed)
    source = (PROJECT_ROOT / "fselling/migration/coordinator.py").read_text(encoding="utf-8")
    assert ".module.upgrade(" not in source
    assert "INSERT INTO alembic_version" not in source
    assert "UPDATE alembic_version" not in source


def test_alembic_env_rejects_non_coordinator_execution(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "coordinator-injected SQLAlchemy Connection" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_adopt_resumes_after_control_init_and_replays_post_commit_success(tmp_path):
    database = tmp_path / "legacy-resume.db"
    backup = tmp_path / "legacy-resume.backup.db"
    _legacy_fixture(database)

    def crash_before_stamp(stage, _revision):
        if stage == "after_control_init":
            raise RuntimeError("crash before root stamp")

    with pytest.raises(RuntimeError, match="before root stamp"):
        _coordinator(database, fault_hook=crash_before_stamp).adopt_legacy(
            backup, request_id="adopt-resume"
        )
    assert _coordinator(database).check().classification == "ADOPTION_RESUMABLE"
    assert _coordinator(database).adopt_legacy(
        backup, request_id="adopt-resume"
    ).sha256

    replay_database = tmp_path / "legacy-replay.db"
    replay_backup = tmp_path / "legacy-replay.backup.db"
    _legacy_fixture(replay_database)

    def crash_after_commit(stage, _revision):
        if stage == "after_commit":
            raise RuntimeError("lost CLI response")

    with pytest.raises(RuntimeError, match="lost CLI response"):
        _coordinator(replay_database, fault_hook=crash_after_commit).adopt_legacy(
            replay_backup, request_id="adopt-replay"
        )
    assert _coordinator(replay_database).adopt_legacy(
        replay_backup, request_id="adopt-replay"
    ).sha256

    ambiguous_database = tmp_path / "legacy-no-intent.db"
    ambiguous_backup = tmp_path / "legacy-no-intent.backup.db"
    _legacy_fixture(ambiguous_database)
    ambiguous = _coordinator(ambiguous_database)
    connection = sqlite3.connect(ambiguous_database, isolation_level=None)
    try:
        ambiguous._ensure_control_initialized(connection, allow_business=True)
    finally:
        connection.close()
    with pytest.raises(RevisionStateError, match="no durable adoption intent"):
        ambiguous.adopt_legacy(ambiguous_backup)
    assert not ambiguous_backup.exists()


def test_adopt_replay_requires_matching_digest_request_and_succeeded_intent(tmp_path):
    database = tmp_path / "legacy-intent.db"
    backup = tmp_path / "legacy-intent.backup.db"
    different_backup = tmp_path / "different-live-same-schema.db"
    _legacy_fixture(database)

    def crash_before_stamp(stage, _revision):
        if stage == "after_control_init":
            raise RuntimeError("crash before root stamp")

    with pytest.raises(RuntimeError, match="before root stamp"):
        _coordinator(database, fault_hook=crash_before_stamp).adopt_legacy(
            backup, request_id="adopt-bound-intent"
        )

    _legacy_fixture(different_backup)
    connection = sqlite3.connect(different_backup)
    try:
        connection.execute(
            "INSERT INTO shops (name, is_active) VALUES ('foreign snapshot', 1)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RevisionStateError, match="intent"):
        _coordinator(database).adopt_legacy(
            different_backup, request_id="adopt-bound-intent"
        )
    with pytest.raises(RevisionStateError, match="intent"):
        _coordinator(database).adopt_legacy(
            backup, request_id="different-request"
        )

    _coordinator(database).adopt_legacy(
        backup, request_id="adopt-bound-intent"
    )

    fresh_database = tmp_path / "fresh-root-not-adopted.db"
    fresh = _coordinator(fresh_database)
    assert fresh.init() == ["0001_legacy_9cf7106_baseline"]
    with pytest.raises(RevisionStateError, match="no durable adoption intent"):
        fresh.adopt_legacy(backup, request_id="fake-adoption")


def test_adopt_transient_stamp_failure_retries_with_new_immutable_attempt(
    tmp_path, monkeypatch
):
    from fselling.migration import coordinator as coordinator_module

    database = tmp_path / "legacy-transient.db"
    backup = tmp_path / "legacy-transient.backup.db"
    _legacy_fixture(database)
    real_stamp = coordinator_module.command.stamp
    calls = 0
    raw_error = r"transient C:\private\customer.db customer@example.com"

    def flaky_stamp(config, revision):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(raw_error)
        return real_stamp(config, revision)

    monkeypatch.setattr(coordinator_module.command, "stamp", flaky_stamp)
    with pytest.raises(RuntimeError, match="transient"):
        _coordinator(database).adopt_legacy(
            backup, request_id="adopt-retryable"
        )

    connection = sqlite3.connect(database)
    try:
        request_after_failure = connection.execute(
            """SELECT state, error_code, error_digest, input_digest
               FROM fs_migration_requests WHERE request_id='adopt-retryable'"""
        ).fetchone()
        attempt_one = connection.execute(
            """SELECT attempt_no, state, error_code, error_digest
               FROM fs_migration_attempts
               WHERE operation_key='adopt:0001_legacy_9cf7106_baseline'"""
        ).fetchone()
        assert request_after_failure[0:3] == ("RUNNING", None, None)
        assert len(request_after_failure[3]) == 64
        assert attempt_one[0:3] == (1, "FAILED_RETRYABLE", "UNEXPECTED_ERROR")
        assert len(attempt_one[3]) == 64
        assert raw_error not in "|".join(str(value) for value in attempt_one if value)
        assert connection.execute(
            "SELECT COUNT(*) FROM fs_migration_revision_journal"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    assert "alembic_version" not in _objects(database)

    result = _coordinator(database).adopt_legacy(
        backup, request_id="adopt-retryable"
    )
    assert calls == 2

    connection = sqlite3.connect(database)
    try:
        attempts = connection.execute(
            """SELECT attempt_no, state, error_code, error_digest, request_id
               FROM fs_migration_attempts
               WHERE operation_key='adopt:0001_legacy_9cf7106_baseline'
               ORDER BY attempt_no"""
        ).fetchall()
        request = connection.execute(
            """SELECT state, target_revision, input_digest, error_code, error_digest
               FROM fs_migration_requests WHERE request_id='adopt-retryable'"""
        ).fetchone()
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        journal = connection.execute(
            """SELECT revision, operation_key, attempt_no
               FROM fs_migration_revision_journal"""
        ).fetchone()
    finally:
        connection.close()

    assert attempts[0] == (*attempt_one, "adopt-retryable")
    assert attempts[1] == (2, "SUCCEEDED", None, None, "adopt-retryable")
    assert request == (
        "SUCCEEDED",
        "0001_legacy_9cf7106_baseline",
        result.sha256,
        None,
        None,
    )
    assert version == ("0001_legacy_9cf7106_baseline",)
    assert journal == (
        "0001_legacy_9cf7106_baseline",
        "adopt:0001_legacy_9cf7106_baseline",
        2,
    )


def test_adopt_blocked_attempt_terminates_intent_without_rewriting_attempt(
    tmp_path, monkeypatch
):
    from fselling.migration import coordinator as coordinator_module

    database = tmp_path / "legacy-blocked.db"
    backup = tmp_path / "legacy-blocked.backup.db"
    _legacy_fixture(database)
    raw_error = r"blocked C:\private\customer.db customer@example.com"
    monkeypatch.setattr(
        coordinator_module.command,
        "stamp",
        Mock(side_effect=SchemaMismatchError(raw_error)),
    )

    with pytest.raises(SchemaMismatchError, match="blocked"):
        _coordinator(database).adopt_legacy(
            backup, request_id="adopt-blocked"
        )

    connection = sqlite3.connect(database)
    try:
        attempt = connection.execute(
            """SELECT attempt_no, state, error_code, error_digest
               FROM fs_migration_attempts
               WHERE operation_key='adopt:0001_legacy_9cf7106_baseline'"""
        ).fetchone()
        request = connection.execute(
            """SELECT state, error_code, error_digest
               FROM fs_migration_requests WHERE request_id='adopt-blocked'"""
        ).fetchone()
    finally:
        connection.close()

    assert attempt[0:3] == (1, "FAILED_BLOCKED", "SCHEMA_MISMATCH")
    assert request[0:2] == ("FAILED_BLOCKED", "SCHEMA_MISMATCH")
    assert len(attempt[3]) == len(request[2]) == 64
    assert raw_error not in "|".join(str(value) for value in (*attempt, *request))

    with pytest.raises(RevisionStateError, match="intent"):
        _coordinator(database).adopt_legacy(
            backup, request_id="adopt-blocked"
        )
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            """SELECT COUNT(*) FROM fs_migration_attempts
               WHERE operation_key='adopt:0001_legacy_9cf7106_baseline'"""
        ).fetchone()[0] == 1
    finally:
        connection.close()


def _insert_legacy_invariant_rows(database: Path, *, ambiguous: bool = False) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """INSERT INTO users (
                   id, username, hashed_password, role, is_verified, is_active,
                   failed_login_count, verification_attempts, staff_shop_id, staff_role
               ) VALUES (1, 'legacy-staff', 'x', 'STAFF', 1, 1, 0, 0, 1, NULL)"""
        )
        connection.execute("INSERT INTO shops (id, name, is_active) VALUES (1, 'S', 1)")
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, stock, is_active, shop_id, track_batches)
               VALUES (1, '', 'P', 10, 1, 1, 1, 0)"""
        )
        connection.execute(
            """INSERT INTO orders (
                   id, shop_id, total_amount, discount_amount,
                   payment_method, status, created_at,
                   cash_paid_amount, refunded_amount, refund_due_amount,
                   loyalty_points_redeemed, loyalty_discount_amount,
                   loyalty_points_earned, reconciliation_reason
               ) VALUES (2, 1, 10, 0, 'bank', 'UNRECONCILED', '2026-01-01',
                         0, 0, 0, 0, 0, 0, NULL)"""
        )
        connection.execute(
            """INSERT INTO orders (
                   id, shop_id, total_amount, discount_amount,
                   payment_method, status, created_at,
                   paid_amount, bank_txn_id, cash_paid_amount, refunded_amount,
                   refund_due_amount, loyalty_points_redeemed,
                   loyalty_discount_amount, loyalty_points_earned
               ) VALUES (1, 1, 10, 0, 'bank', 'PAID', '2026-01-01', 10, 'txn-1',
                         0, 0, 0, 0, 0, 0)"""
        )
        connection.execute(
            """INSERT INTO order_items
               (id, order_id, product_id, product_name, price, quantity)
               VALUES (1, 1, NULL, 'P', 10, 1)"""
        )
        connection.execute(
            """INSERT INTO order_items
               (id, order_id, product_id, product_name, price, quantity)
               VALUES (2, 2, NULL, 'P', 10, 1)"""
        )
        activated_at = "not-a-date" if ambiguous else "2026-01-01 00:00:00"
        connection.execute(
            """INSERT INTO subscription_checkouts (
                   id, shop_id, reference_code, cycle, amount_due_vnd,
                   duration_days, status, received_amount_vnd,
                   refund_due_amount_vnd, operation_id, operation_fingerprint,
                   created_by_user_id, created_at, expires_at, activated_at,
                   entitlement_starts_at, entitlement_ends_at, paid_until_after
               ) VALUES (1, 1, 'REF1', 'MONTHLY', 1000, 30, 'PAID', 1000, 0,
                         'op-1', 'fp-1', 1, '2026-01-01', '2026-01-02', ?,
                         NULL, NULL, NULL)""",
            (activated_at,),
        )
        connection.execute(
            """INSERT INTO subscription_checkouts (
                   id, shop_id, reference_code, cycle, amount_due_vnd,
                   duration_days, status, received_amount_vnd,
                   refund_due_amount_vnd, operation_id, operation_fingerprint,
                   created_by_user_id, created_at, expires_at, activated_at,
                   entitlement_starts_at, entitlement_ends_at, paid_until_after
               ) VALUES (2, 1, 'REF2', 'MONTHLY', 1000, 30, 'PENDING', 0, 0,
                         'op-2', 'fp-2', 1, '2025-01-01', '2025-01-02', NULL,
                         NULL, NULL, NULL)"""
        )
        connection.commit()
    finally:
        connection.close()


def test_baseline_invariants_are_repaired_by_production_revision(tmp_path):
    database = tmp_path / "legacy-repair.db"
    backup = tmp_path / "legacy-repair.backup.db"
    _legacy_fixture(database)
    _insert_legacy_invariant_rows(database)
    coordinator = _coordinator(database)
    assert coordinator.check().classification == "LEGACY_EXACT"
    coordinator.adopt_legacy(backup)
    coordinator.upgrade()
    coordinator.verify()

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT code FROM products WHERE id=1").fetchone()[0] == "SP-1"
        assert connection.execute(
            "SELECT product_id FROM order_items WHERE id=1"
        ).fetchone()[0] == 1
        starts, ends = connection.execute(
            """SELECT entitlement_starts_at, entitlement_ends_at
               FROM subscription_checkouts WHERE id=1"""
        ).fetchone()
        assert starts and ends and ends > starts
        assert connection.execute(
            "SELECT paid_until FROM shop_subscriptions WHERE shop_id=1"
        ).fetchone()[0] == ends
        assert connection.execute(
            "SELECT idempotency_key FROM order_payments WHERE order_id=1"
        ).fetchone()[0] == "legacy-order:1"
        assert connection.execute(
            "SELECT staff_role FROM users WHERE id=1"
        ).fetchone()[0] == "MANAGER"
        assert connection.execute(
            "SELECT reconciliation_reason FROM orders WHERE id=2"
        ).fetchone()[0] == "LEGACY_REVIEW"
        assert connection.execute(
            "SELECT status FROM subscription_checkouts WHERE id=2"
        ).fetchone()[0] == "EXPIRED"
    finally:
        connection.close()
    assert "legacy_bootstrap_support" not in (
        PROJECT_ROOT / "fselling/migration/coordinator.py"
    ).read_text(encoding="utf-8")


def test_ambiguous_legacy_entitlement_fails_closed_before_control(tmp_path):
    database = tmp_path / "legacy-ambiguous.db"
    _legacy_fixture(database)
    _insert_legacy_invariant_rows(database, ambiguous=True)
    with pytest.raises(SchemaMismatchError, match="data invariants"):
        _coordinator(database).check()
    assert not CONTROL_TABLES.intersection(_objects(database))


class _FakeClock:
    def __init__(self):
        self.value = datetime(2026, 8, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


def test_long_transaction_and_heartbeat_fencing_use_fake_clock(tmp_path):
    database = tmp_path / "long-lease.db"
    clock = _FakeClock()

    def exceed_lease(stage, _revision):
        if stage == "after_ddl":
            clock.advance(5)

    coordinator = _coordinator(
        database, lease_seconds=1, clock=clock, fault_hook=exceed_lease
    )
    # Expiry during the held BEGIN IMMEDIATE cannot invalidate its own commit.
    coordinator.init()
    coordinator.upgrade()
    coordinator.verify()

    lease = coordinator.acquire_lease("worker")
    clock.advance(2)
    with pytest.raises(StaleFenceError):
        coordinator.write_checkpoint(
            lease, workset_id=1, checkpoint_key="before-heartbeat", value={"n": 1}
        )
    lease = coordinator.renew_lease(lease)
    coordinator.write_checkpoint(
        lease, workset_id=1, checkpoint_key="after-heartbeat", value={"n": 2}
    )
    clock.advance(2)
    winner = coordinator.acquire_lease("winner")
    try:
        with pytest.raises(StaleFenceError):
            coordinator.renew_lease(lease)
        with pytest.raises(StaleFenceError):
            coordinator.write_checkpoint(
                lease, workset_id=1, checkpoint_key="stale", value={"n": 3}
            )
    finally:
        coordinator.release_lease(winner)


def test_public_readiness_payload_is_minimal(monkeypatch):
    from fselling import main as main_module

    application = main_module.create_app()
    application.state.schema_ready = True
    application.state.schema_revision = "0003_i05_integer_vnd_cost_basis"
    application.state.schema_verification = {
        "database_path": "C:/secret/customer.db",
        "database_uuid": "private-uuid",
        "business_fingerprint": "private-fingerprint",
        "topology_source": "private-source",
    }
    response = TestClient(application).get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "revision": "0003_i05_integer_vnd_cost_basis",
    }


def test_request_id_spans_multi_revision_and_errors_are_digest_only(tmp_path):
    database = tmp_path / "multi-request.db"

    def stop_after_control(stage, _revision):
        if stage == "after_control_init":
            raise RuntimeError("expected control-only crash")

    with pytest.raises(RuntimeError, match="control-only"):
        _coordinator(database, fault_hook=stop_after_control).init()
    coordinator = _coordinator(database)
    assert coordinator.upgrade(request_id="multi-revision-request") == [
        "0001_legacy_9cf7106_baseline",
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]
    assert coordinator.upgrade(request_id="multi-revision-request") == []

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """SELECT target_revision, request_id, state
               FROM fs_migration_attempts WHERE request_id=? ORDER BY id""",
            ("multi-revision-request",),
        ).fetchall()
        assert rows == [
            ("0001_legacy_9cf7106_baseline", "multi-revision-request", "SUCCEEDED"),
            ("0002_i04_operational_tables", "multi-revision-request", "SUCCEEDED"),
            ("0003_i05_integer_vnd_cost_basis", "multi-revision-request", "SUCCEEDED"),
            ("0004_i09_offline_receipts", "multi-revision-request", "SUCCEEDED"),
            ("0005_i09c_offline_issue_lifecycle", "multi-revision-request", "SUCCEEDED"),
            ("0006_i09e_offline_receipt_items", "multi-revision-request", "SUCCEEDED"),
            ("0007_i10a_qr_payment_domain", "multi-revision-request", "SUCCEEDED"),
            ("0008_purchase_orders", "multi-revision-request", "SUCCEEDED"),
            ("0009_fnb_table_service_r1a", "multi-revision-request", "SUCCEEDED"),
            ("0010_fnb_kitchen_stock_r1b", "multi-revision-request", "SUCCEEDED"),
            ("0011_fnb_checkout_r1c", "multi-revision-request", "SUCCEEDED"),
            ("0012_fnb_ticket_service_handoff", "multi-revision-request", "SUCCEEDED"),
            (PLAN3, "multi-revision-request", "SUCCEEDED"),
            (PLAN4, "multi-revision-request", "SUCCEEDED"),
        ]
        assert connection.execute(
            "SELECT state FROM fs_migration_requests WHERE request_id=?",
            ("multi-revision-request",),
        ).fetchone() == ("SUCCEEDED",)
    finally:
        connection.close()

    resume_database = tmp_path / "multi-request-resume.db"
    with pytest.raises(RuntimeError, match="control-only"):
        _coordinator(resume_database, fault_hook=stop_after_control).init()
    committed_once = False

    def lose_first_revision_response(stage, revision):
        nonlocal committed_once
        if stage == "after_commit" and revision.endswith("baseline") and not committed_once:
            committed_once = True
            raise RuntimeError("lost response after first revision")

    with pytest.raises(RuntimeError, match="first revision"):
        _coordinator(
            resume_database, fault_hook=lose_first_revision_response
        ).upgrade(request_id="multi-resume-request")
    assert _coordinator(resume_database).upgrade(
        request_id="multi-resume-request"
    ) == [
        "0002_i04_operational_tables",
        "0003_i05_integer_vnd_cost_basis",
        "0004_i09_offline_receipts",
        "0005_i09c_offline_issue_lifecycle",
        "0006_i09e_offline_receipt_items",
        "0007_i10a_qr_payment_domain",
        "0008_purchase_orders",
        "0009_fnb_table_service_r1a",
        "0010_fnb_kitchen_stock_r1b",
        "0011_fnb_checkout_r1c",
        "0012_fnb_ticket_service_handoff",
        PLAN3,
        PLAN4,
    ]

    error_database = tmp_path / "sanitized-error.db"
    private_text = f"{tmp_path / 'customer-secret.db'} owner@example.test"

    def fail_with_private_text(stage, _revision):
        if stage == "after_ddl":
            raise RuntimeError(private_text)

    with pytest.raises(RuntimeError, match="owner@example"):
        _coordinator(error_database, fault_hook=fail_with_private_text).init(
            request_id="sanitized-request"
        )
    connection = sqlite3.connect(error_database)
    try:
        columns = [row[1] for row in connection.execute(
            "PRAGMA table_info(fs_migration_attempts)"
        )]
        assert "error_message" not in columns
        attempt = connection.execute(
            """SELECT error_code, error_digest FROM fs_migration_attempts
               WHERE request_id='sanitized-request'"""
        ).fetchone()
        request = connection.execute(
            """SELECT error_code, error_digest FROM fs_migration_requests
               WHERE request_id='sanitized-request'"""
        ).fetchone()
        assert attempt[0] == request[0] == "UNEXPECTED_ERROR"
        assert len(attempt[1]) == len(request[1]) == 64
        stored = repr(connection.execute(
            "SELECT * FROM fs_migration_attempts"
        ).fetchall()) + repr(connection.execute(
            "SELECT * FROM fs_migration_requests"
        ).fetchall())
        assert private_text not in stored
        assert "owner@example.test" not in stored
        assert str(tmp_path) not in stored
    finally:
        connection.close()
