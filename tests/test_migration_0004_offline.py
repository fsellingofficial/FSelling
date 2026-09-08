"""I09-A migration 0004 contract tests; every database is temporary.

Scope is the migration only: schema shape, atomicity and the fail-closed
verifier. Lease issuance, ingest, issue lifecycle and recovery behaviour belong
to I09-B and are deliberately not exercised here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.errors import RevisionStateError
from fselling.migration.schema import CONTROL_SCHEMA_FINGERPRINT
from fselling.migration.topology import StaticInventory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROOT = "0001_legacy_9cf7106_baseline"
I04 = "0002_i04_operational_tables"
I05 = "0003_i05_integer_vnd_cost_basis"
I09 = "0004_i09_offline_receipts"
I09C = "0005_i09c_offline_issue_lifecycle"
I09E = "0006_i09e_offline_receipt_items"
I10A = "0007_i10a_qr_payment_domain"
PO = "0008_purchase_orders"
FNB = "0009_fnb_table_service_r1a"
R1B = "0010_fnb_kitchen_stock_r1b"
R1C = "0011_fnb_checkout_r1c"
PLAN2 = "0012_fnb_ticket_service_handoff"
PLAN3 = "0013_roles_returns_approval_r3"
LATER_INDEX = "0006_test_later_index"

# Pinned so an edit to a released revision fails here instead of silently
# changing what every managed database already applied.
RELEASED_CHECKSUMS = {
    ROOT: "5bdcb5e297ba9eba83474c5415371129c9c3d1498280ee481e37c27fd37e7a5c",
    I04: "811595d51abd3ba12d1dd10ab9602960766567310281fabd00a042be4bdb797b",
    I05: "d0abe1f5df1d729678dbd258e9ff40848588437c26e3d05c8b88a8e47d492539",
    I09: "572c969914b6517f84a2b5a2bd9c3477e79f9deb88f50fe2c77b3384d487d506",
}
RELEASED_CONTROL_FINGERPRINT = (
    "1be2c54a0e8ccce8c35e61509eeb142047fc91ca42f4704033e179e553c26b62"
)

EXPECTED_TABLES = {
    "offline_leases",
    "offline_receipt_registry",
    "offline_receipts",
    "offline_stock_deficits",
    "offline_receipt_issues",
    "offline_recovery_actions",
}
EXPECTED_INDEXES = {
    "ix_offline_leases_shop_user",
    "ix_offline_leases_expires",
    "ux_offline_receipts_order_id",
    "ux_offline_receipts_offline_uuid",
    "ux_offline_receipts_lease_sequence",
    "ix_offline_receipts_sold_at",
    "ux_offline_stock_deficits_order_item",
    "ix_offline_stock_deficits_open",
    "ux_offline_receipt_issues_scope",
    "ix_offline_receipt_issues_open",
    "ix_offline_receipt_registry_shop",
    "ix_offline_recovery_actions_shop",
    "ix_offline_recovery_actions_uuid",
}

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
FINGERPRINT_V1 = "f" * 64
FINGERPRINT_V0 = "e" * 64


def _coordinator(path: Path, **kwargs) -> MigrationCoordinator:
    return MigrationCoordinator(
        path,
        project_root=kwargs.pop("project_root", PROJECT_ROOT),
        inventory_provider=StaticInventory(),
        **kwargs,
    )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _objects(path: Path) -> dict[str, set[str]]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'"""
        ).fetchall()
    finally:
        connection.close()
    return {
        "tables": {name for kind, name, _t in rows if kind == "table"},
        "indexes": {name for kind, name, _t in rows if kind == "index"},
        "offline_indexes": {
            name
            for kind, name, table in rows
            if kind == "index" and table in EXPECTED_TABLES
        },
    }


def _normalized_checksum(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _at_i05(path: Path, **kwargs) -> MigrationCoordinator:
    coordinator = _coordinator(path, **kwargs)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I05) == [I04, I05]
    return coordinator


def _module(coordinator: MigrationCoordinator, revision: str = I09):
    """Address a revision by id: 0004 stopped being head when 0005 landed."""
    return next(
        item.module
        for item in coordinator._graph().revisions
        if item.revision == revision
    )


def _verify_revision(coordinator: MigrationCoordinator, path: Path) -> None:
    """Call the durable 0004 verifier directly on a real file connection."""
    connection = sqlite3.connect(path)
    try:
        _module(coordinator).verify(connection)
    finally:
        connection.close()


def _pre_0004_root(tmp_path: Path) -> Path:
    """An isolated copy of the graph that has never heard of 0004."""
    root = tmp_path / "binary-pre-0004"
    root.mkdir()
    shutil.copy2(PROJECT_ROOT / "alembic.ini", root / "alembic.ini")
    shutil.copytree(PROJECT_ROOT / "migrations", root / "migrations")
    (root / "migrations/versions/0004_i09_offline_receipts.py").unlink()
    (root / "migrations/versions/0005_i09c_offline_issue_lifecycle.py").unlink()
    (root / "migrations/versions/0006_i09e_offline_receipt_items.py").unlink()
    (root / "migrations/versions/0007_i10a_qr_payment_domain.py").unlink()
    (root / "migrations/versions/0008_purchase_orders.py").unlink()
    (root / "migrations/versions/0009_fnb_table_service_r1a.py").unlink()
    (root / "migrations/versions/0010_fnb_kitchen_stock_r1b.py").unlink()
    (root / "migrations/versions/0011_fnb_checkout_r1c.py").unlink()
    (root / "migrations/versions/0012_fnb_ticket_service_handoff.py").unlink()
    (root / "migrations/versions/0013_roles_returns_approval_r3.py").unlink()
    manifest_path = root / "migrations/checksums.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["revisions"][I09]
    del manifest["revisions"][I09C]
    del manifest["revisions"][I09E]
    del manifest["revisions"][I10A]
    del manifest["revisions"][PO]
    del manifest["revisions"][FNB]
    del manifest["revisions"][R1B]
    del manifest["revisions"][R1C]
    del manifest["revisions"][PLAN2]
    del manifest["revisions"][PLAN3]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _later_index_root(tmp_path: Path) -> Path:
    """An isolated valid successor proving 0004 permits later-owned indexes."""
    root = tmp_path / "binary-with-later-index"
    root.mkdir()
    shutil.copy2(PROJECT_ROOT / "alembic.ini", root / "alembic.ini")
    shutil.copytree(PROJECT_ROOT / "migrations", root / "migrations")
    (root / "migrations/versions/0006_i09e_offline_receipt_items.py").unlink()
    (root / "migrations/versions/0007_i10a_qr_payment_domain.py").unlink()
    (root / "migrations/versions/0008_purchase_orders.py").unlink()
    (root / "migrations/versions/0009_fnb_table_service_r1a.py").unlink()
    (root / "migrations/versions/0010_fnb_kitchen_stock_r1b.py").unlink()
    (root / "migrations/versions/0011_fnb_checkout_r1c.py").unlink()
    (root / "migrations/versions/0012_fnb_ticket_service_handoff.py").unlink()
    (root / "migrations/versions/0013_roles_returns_approval_r3.py").unlink()
    source = f'''from alembic import op

revision = "{LATER_INDEX}"
down_revision = "{I09C}"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_offline_leases_extra ON offline_leases (device_id)"
    )


def verify(connection):
    execute = getattr(connection, "exec_driver_sql", connection.execute)
    row = execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='index' AND name='ix_offline_leases_extra'"
    ).fetchone()
    if row is None:
        raise RuntimeError("TEST_LATER_INDEX_MISSING")


def downgrade():
    raise RuntimeError("test revision is forward-only")
'''
    revision_path = root / f"migrations/versions/{LATER_INDEX}.py"
    revision_path.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    manifest_path = root / "migrations/checksums.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["revisions"][I09E]
    del manifest["revisions"][I10A]
    del manifest["revisions"][PO]
    del manifest["revisions"][FNB]
    del manifest["revisions"][R1B]
    del manifest["revisions"][R1C]
    del manifest["revisions"][PLAN2]
    del manifest["revisions"][PLAN3]
    manifest["revisions"][LATER_INDEX] = {
        "down_revision": I09C,
        "path": f"versions/{LATER_INDEX}.py",
        "sha256": digest,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


# --------------------------------------------------------------------- seeds


def _seed_business_rows(connection: sqlite3.Connection) -> None:
    """Minimal I05-consistent business state the offline rows can hang from."""
    for user_id, username in ((1, "i09-seller"), (2, "i09-syncer")):
        connection.execute(
            """INSERT INTO users
               (id, username, hashed_password, role, is_verified, is_active,
                failed_login_count, verification_attempts)
               VALUES (?, ?, 'x', 'SELLER', 1, 1, 0, 0)""",
            (user_id, username),
        )
    connection.execute(
        "INSERT INTO shops (id, name, is_active, owner_id) VALUES (1, 'I09 shop', 1, 1)"
    )
    connection.execute(
        """INSERT INTO products
           (id, code, name, price, price_vnd, stock, is_active, shop_id,
            track_batches, cost_known_qty, cost_unknown_qty, cost_basis_vnd,
            cost_deficit_qty, cost_state_version)
           VALUES (1, 'SP-1', 'Hàng offline', 100, 100, 0, 1, 1, 0, 0, 0, 0, 0, 0)"""
    )
    for order_id, uuid, total, issue in (
        (1, "uuid-v1", 200, "TON_AM"), (2, "uuid-replacement", 100, "SP_KHONG_CON")
    ):
        connection.execute(
            """INSERT INTO orders
               (id, shop_id, total_amount, discount_amount, payment_method, status,
                created_at, cash_paid_amount, refunded_amount, refund_due_amount,
                offline_uuid, sold_offline_at, offline_issue,
                loyalty_points_redeemed,
                loyalty_discount_amount, loyalty_points_earned, total_vnd,
                discount_vnd, cash_paid_vnd, refunded_vnd, refund_due_vnd,
                loyalty_discount_vnd, inventory_reversed, inventory_reversal_version)
               VALUES (?, 1, ?, 0, 'cash', 'PAID', '2026-08-01 03:00:00', ?, 0, 0,
                       ?, '2026-08-01 03:00:00', ?, 0, 0, 0, ?, 0, ?, 0, 0, 0, 0, 0)""",
            (order_id, float(total), float(total), uuid, issue, total, total),
        )
    for item_id, order_id in ((1, 1), (2, 1), (3, 2)):
        connection.execute(
            """INSERT INTO order_items
               (id, order_id, product_id, product_name, price, quantity,
                unit_price_vnd, discount_vnd, loyalty_discount_vnd, net_amount_vnd,
                cost_known_qty, cost_unknown_qty, cost_basis_vnd, returned_total_qty,
                returned_known_qty, returned_unknown_qty, returned_cost_basis_vnd,
                returned_refund_vnd, cost_return_version, inventory_reversed,
                inventory_reversal_version)
               VALUES (?, ?, 1, 'Hàng offline', 100, 1, 100, 0, 0, 100,
                       0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0)""",
            (item_id, order_id),
        )
    connection.execute(
        """INSERT INTO system_logs (id, user_id, shop_id, action, details, created_at)
           VALUES (1, 1, 1, 'OFFLINE_RECOVERY', 'phiếu offline', '2026-08-01 04:00:00')"""
    )


def _seed_offline_rows(connection: sqlite3.Connection) -> None:
    """One valid row per 0004 table, covering v1, v0 and every registry state."""
    connection.execute(
        """INSERT INTO offline_leases
           (lease_id, shop_id, user_id, device_id, contract_version, catalog_version,
            catalog_snapshot_digest, secret_sha256, server_anchor_id,
            anchor_server_time_utc, issued_at, expires_at, state_version)
           VALUES ('lease-1', 1, 1, 'device-1', 1, 7, ?, ?, 'anchor-1',
                   '2026-08-01 02:00:00.000000', '2026-08-01 02:00:00.000000',
                   '2026-08-01 14:00:00.000000', 0)""",
        (DIGEST_A, DIGEST_B),
    )
    connection.execute(
        """INSERT INTO offline_receipt_registry
           (offline_uuid, shop_id, order_id, server_fingerprint, contract_version,
            state, created_at, updated_at, state_version)
           VALUES ('uuid-v1', 1, 1, ?, 1, 'INGESTED',
                   '2026-08-01 04:00:00.000000',
                   '2026-08-01 04:00:00.000000', 0)""",
        (FINGERPRINT_V1,),
    )
    connection.execute(
        """INSERT INTO offline_receipt_registry
           (offline_uuid, shop_id, order_id, server_fingerprint, contract_version,
            state, created_at, updated_at, state_version)
           VALUES ('uuid-replacement', 1, 2, ?, 0, 'INGESTED',
                   '2026-08-01 05:00:00.000000',
                   '2026-08-01 05:00:00.000000', 0)""",
        (FINGERPRINT_V0,),
    )
    connection.execute(
        """INSERT INTO offline_receipt_registry
           (offline_uuid, shop_id, order_id, server_fingerprint, contract_version,
            state, superseded_by_offline_uuid, created_at, updated_at, state_version)
           VALUES ('uuid-corrected', 1, NULL, ?, 0, 'SUPERSEDED', 'uuid-replacement',
                   '2026-08-01 05:00:00.000000',
                   '2026-08-01 05:30:00.000000', 1)""",
        (FINGERPRINT_V0,),
    )
    connection.execute(
        """INSERT INTO offline_receipts
           (id, order_id, offline_uuid, contract_version, lease_id, device_id,
            offline_session_id, sequence, server_fingerprint, client_fingerprint,
            client_fingerprint_mismatch, sold_by_claimed_user_id, synced_by_user_id,
            attribution_kind, sold_at_effective, sold_at_client_utc,
            sold_at_upper_bound, time_confidence, client_monotonic_ms,
            server_anchor_id, ingested_at)
           VALUES (1, 1, 'uuid-v1', 1, 'lease-1', 'device-1', 'lease-1', 1, ?, ?,
                   0, 1, 2, 'LEASE_CLAIM', '2026-08-01 03:00:00.000000',
                   '2026-08-01 03:00:00.000000',
                   '2026-08-01 04:00:00.000000', 'ANCHORED_CLIENT', 3600000,
                   'anchor-1', '2026-08-01 04:00:00.000000')""",
        (FINGERPRINT_V1, FINGERPRINT_V1),
    )
    connection.execute(
        """INSERT INTO offline_receipts
           (id, order_id, offline_uuid, contract_version, device_id,
            server_fingerprint, client_fingerprint_mismatch,
            sold_by_claimed_user_id, synced_by_user_id, attribution_kind,
            sold_at_effective, sold_at_client_utc, time_confidence, ingested_at)
           VALUES (2, 2, 'uuid-replacement', 0, 'legacy-label', ?, 0, 1, 2,
                   'LEGACY_UNKNOWN', '2026-07-20 03:00:00.000000',
                   '2026-07-20 03:00:00.000000', 'LEGACY',
                   '2026-08-01 05:00:00.000000')""",
        (FINGERPRINT_V0,),
    )
    connection.execute(
        """INSERT INTO offline_stock_deficits
           (id, order_item_id, product_id, deficit_quantity, remaining_quantity,
            state_version)
           VALUES (1, 1, 1, 1, 1, 0)"""
    )
    connection.execute(
        """INSERT INTO offline_stock_deficits
           (id, order_item_id, product_id, deficit_quantity, remaining_quantity,
            resolution_kind, resolved_by_user_id, resolved_at, resolution_reason,
            state_version)
           VALUES (2, 2, 1, 1, 0, 'STOCKTAKE', 1, '2026-08-02 01:00:00.000000',
                   'kiểm kê bù đủ', 1)"""
    )
    connection.execute(
        """INSERT INTO offline_receipt_issues
           (id, order_id, order_item_id, product_id, issue_code, evidence_kind,
            evidence_id, severity, state, opened_at, state_version)
           VALUES (1, 1, 1, 1, 'TON_AM', 'OFFLINE_STOCK_DEFICIT', 1, 'ACTION',
                   'OPEN', '2026-08-01 04:00:00.000000', 0)"""
    )
    connection.execute(
        """INSERT INTO offline_receipt_issues
           (id, order_id, product_id, issue_code, evidence_kind, severity, state,
            reason, opened_at, resolved_at, resolved_by_user_id, resolution_kind,
            state_version)
           VALUES (2, 2, 1, 'SP_KHONG_CON', 'CATALOG', 'ACTION', 'ACKNOWLEDGED',
                   'chủ shop chấp nhận giá vốn chưa biết',
                   '2026-08-01 05:00:00.000000',
                   '2026-08-02 02:00:00.000000', 1, NULL, 1)"""
    )
    # Bằng chứng đã đóng vẫn phải có đúng issue của nó: 0005 cấm một khoản thiếu
    # tồn tại mà không ai từng được giao xử lý.
    connection.execute(
        """INSERT INTO offline_receipt_issues
           (id, order_id, order_item_id, product_id, issue_code, evidence_kind,
            evidence_id, severity, state, opened_at, resolved_at,
            resolved_by_user_id, resolution_kind, state_version)
           VALUES (3, 1, 2, 1, 'TON_AM', 'OFFLINE_STOCK_DEFICIT', 2, 'ACTION',
                   'RESOLVED', '2026-08-01 04:00:00.000000',
                   '2026-08-02 01:00:00.000000', 1, 'STOCKTAKE', 1)"""
    )
    connection.execute(
        """INSERT INTO offline_recovery_actions
           (id, shop_id, action_kind, original_offline_uuid, original_fingerprint,
            replacement_offline_uuid, file_digest, reason, performed_by_user_id,
            performed_at, system_log_id)
           VALUES (1, 1, 'CORRECT', 'uuid-corrected', ?, 'uuid-replacement', ?,
                   'sửa phiếu sai sản phẩm', 1, '2026-08-01 05:30:00.000000', 1)""",
        (FINGERPRINT_V0, DIGEST_A),
    )
    connection.execute(
        """INSERT INTO offline_recovery_actions
           (id, shop_id, action_kind, original_offline_uuid, reason,
            performed_by_user_id, performed_at, system_log_id)
           VALUES (2, 1, 'ABANDON', 'uuid-never-ingested',
                   'máy mất, bỏ phiếu chưa đồng bộ', 1,
                   '2026-08-01 06:00:00.000000', 1)"""
    )


def _seeded(tmp_path: Path, name: str) -> tuple[MigrationCoordinator, Path]:
    database = tmp_path / name
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade(I09)
    connection = _connect(database)
    try:
        _seed_business_rows(connection)
        _seed_offline_rows(connection)
        connection.commit()
    finally:
        connection.close()
    return coordinator, database


# ------------------------------------------------------------------ upgrades


def test_fresh_root_to_0004_and_restart_verify_are_stable(tmp_path):
    database = tmp_path / "fresh-0004.db"
    coordinator = _coordinator(database)

    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    report = coordinator.verify()
    assert report.current_revision == report.head_revision == PLAN3
    assert report.revision_count == 13

    # Restart is a no-op and verification stays green on the same database.
    assert coordinator.upgrade("head") == []
    assert coordinator.verify().database_uuid == report.database_uuid
    assert coordinator.check().classification == "READY"


def test_upgrade_from_0003_applies_only_0004(tmp_path):
    database = tmp_path / "step-0003-to-0004.db"
    coordinator = _at_i05(database)
    before = _objects(database)
    assert not (before["tables"] & EXPECTED_TABLES)

    assert coordinator.upgrade(I09) == [I09]
    _verify_revision(coordinator, database)

    after = _objects(database)
    assert EXPECTED_TABLES <= after["tables"]
    assert after["offline_indexes"] == EXPECTED_INDEXES
    # 0004 owns nothing in the control namespace and touches no existing table.
    new_objects = (after["tables"] | after["indexes"]) - (
        before["tables"] | before["indexes"]
    )
    assert new_objects == EXPECTED_TABLES | EXPECTED_INDEXES
    assert not any(name.startswith("fs_migration_") for name in new_objects)


def test_released_revisions_and_control_fingerprint_are_untouched():
    for revision, digest in RELEASED_CHECKSUMS.items():
        path = PROJECT_ROOT / f"migrations/versions/{revision}.py"
        assert _normalized_checksum(path) == digest, revision
    assert CONTROL_SCHEMA_FINGERPRINT == RELEASED_CONTROL_FINGERPRINT

    manifest = json.loads(
        (PROJECT_ROOT / "migrations/checksums.json").read_text(encoding="utf-8")
    )
    for revision, digest in RELEASED_CHECKSUMS.items():
        assert manifest["revisions"][revision]["sha256"] == digest


def test_0004_is_linear_self_contained_and_checksummed(tmp_path):
    graph = _coordinator(tmp_path / "unused.db")._graph()
    assert [item.revision for item in graph.revisions] == [
        ROOT, I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3
    ]
    spec = next(item for item in graph.revisions if item.revision == I09)
    assert spec.down_revision == I05

    revision_path = PROJECT_ROOT / f"migrations/versions/{I09}.py"
    digest = _normalized_checksum(revision_path)
    manifest = json.loads(
        (PROJECT_ROOT / "migrations/checksums.json").read_text(encoding="utf-8")
    )
    assert manifest["revisions"][I09] == {
        "down_revision": I05,
        "path": f"versions/{I09}.py",
        "sha256": digest,
    }
    assert spec.checksum == digest

    source = revision_path.read_text(encoding="utf-8")
    assert "from alembic import op" in source
    assert "import fselling" not in source
    assert "executescript" not in source
    assert "fs_migration_" not in source
    module = spec.module
    assert set(module.EXPECTED_TABLES_0004) == EXPECTED_TABLES
    assert len(module.EXPECTED_INDEXES_0004) == 13
    assert set(module.EXPECTED_INDEXES_0004) == EXPECTED_INDEXES
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()


@pytest.mark.parametrize("stage", ["after_ddl", "after_journal", "after_verify"])
def test_0004_rolls_back_as_one_unit(tmp_path, stage):
    database = tmp_path / f"rollback-{stage}.db"

    def fail(selected, revision):
        if selected == stage and revision == I09:
            raise RuntimeError(f"fault at {stage}")

    coordinator = _at_i05(database, fault_hook=fail)
    with pytest.raises(RuntimeError, match=stage):
        coordinator.upgrade(I09)

    objects = _objects(database)
    assert not (objects["tables"] & EXPECTED_TABLES)
    assert not (objects["indexes"] & EXPECTED_INDEXES)
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (I05,)
        assert connection.execute(
            "SELECT COUNT(*) FROM fs_migration_revision_journal WHERE revision=?",
            (I09,),
        ).fetchone() == (0,)
        assert connection.execute(
            """SELECT state FROM fs_migration_attempts
               WHERE operation_key=? ORDER BY attempt_no""",
            (f"revision:{I09}",),
        ).fetchall() == [("FAILED_RETRYABLE",)]
    finally:
        connection.close()

    # The same database still upgrades cleanly once the fault is gone.
    assert _coordinator(database).upgrade("head") == [I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    _coordinator(database).verify()


def test_legacy_offline_orders_without_0004_rows_still_upgrade(tmp_path):
    database = tmp_path / "legacy-offline-orders.db"
    coordinator = _at_i05(database)
    connection = _connect(database)
    try:
        _seed_business_rows(connection)
        connection.execute(
            "UPDATE orders SET offline_issue='TON_AM,CA_DA_CHOT' WHERE id=1"
        )
        connection.commit()
    finally:
        connection.close()

    assert coordinator.upgrade(I09) == [I09]
    _verify_revision(coordinator, database)
    connection = sqlite3.connect(database)
    try:
        # No inference, no backfill: a pre-I09 offline sale gets no lease,
        # no registry row and no fabricated receipt.
        for table in sorted(EXPECTED_TABLES):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone() == (0,)
        assert connection.execute(
            "SELECT offline_uuid FROM orders WHERE id=1"
        ).fetchone() == ("uuid-v1",)
    finally:
        connection.close()


# ------------------------------------------------------------- constraints


def test_declared_constraints_reject_impossible_rows(tmp_path):
    _coordinator_, database = _seeded(tmp_path, "constraints.db")
    connection = _connect(database)
    try:
        # UNIQUE: one receipt per order and per UUID.
        with pytest.raises(
            sqlite3.IntegrityError, match=r"UNIQUE.*offline_receipts\.order_id"
        ):
            connection.execute(
                """INSERT INTO offline_receipts
                   (order_id, offline_uuid, contract_version, device_id,
                    server_fingerprint, client_fingerprint_mismatch,
                    sold_by_claimed_user_id, synced_by_user_id, attribution_kind,
                    sold_at_effective, sold_at_client_utc, time_confidence,
                    ingested_at)
                   VALUES (1, 'uuid-corrected', 0, NULL, ?, 0, 1, 2,
                           'LEGACY_UNKNOWN', '2026-08-01 03:00:00.000000',
                           '2026-08-01 03:00:00.000000', 'LEGACY',
                           '2026-08-01 04:00:00.000000')""",
                (FINGERPRINT_V0,),
            )
        connection.rollback()

        # Partial UNIQUE: the same sequence may not repeat inside one lease,
        # while v0 rows with a NULL lease never collide with each other.
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"UNIQUE.*offline_receipts\.lease_id, offline_receipts\.sequence",
        ):
            connection.execute(
                """INSERT INTO offline_receipts
                   (order_id, offline_uuid, contract_version, lease_id, device_id,
                    offline_session_id, sequence, server_fingerprint,
                    client_fingerprint_mismatch, sold_by_claimed_user_id,
                    synced_by_user_id, attribution_kind, sold_at_effective,
                    sold_at_client_utc, time_confidence, server_anchor_id,
                    ingested_at)
                   VALUES (2, 'uuid-corrected', 1, 'lease-1', 'device-1', 'lease-1',
                           1, ?, 0, 1, 2, 'LEASE_CLAIM',
                           '2026-08-01 03:00:00.000000',
                           '2026-08-01 03:00:00.000000', 'ANCHORED_CLIENT',
                           'anchor-1', '2026-08-01 04:00:00.000000')""",
                (FINGERPRINT_V1,),
            )
        connection.rollback()

        # Expression UNIQUE: one open issue per (order, code, item, product).
        with pytest.raises(
            sqlite3.IntegrityError, match="ux_offline_receipt_issues_scope"
        ):  # an expression index is named in the error, not its columns
            connection.execute(
                """INSERT INTO offline_receipt_issues
                   (order_id, order_item_id, product_id, issue_code, evidence_kind,
                    evidence_id, severity, state, opened_at, state_version)
                   VALUES (1, 1, 1, 'TON_AM', 'OFFLINE_STOCK_DEFICIT', 1, 'INFO',
                           'OPEN', '2026-08-01 04:00:00.000000', 0)"""
            )
        connection.rollback()

        # FK: a receipt cannot point at a UUID with no registry row.
        connection.execute(
            """INSERT INTO orders
               (id, shop_id, total_amount, discount_amount, payment_method, status,
                created_at, cash_paid_amount, refunded_amount, refund_due_amount,
                loyalty_points_redeemed, loyalty_discount_amount,
                loyalty_points_earned, total_vnd, cash_paid_vnd)
               VALUES (3, 1, 0, 0, 'cash', 'PAID', '2026-08-03 03:00:00', 0, 0, 0,
                       0, 0, 0, 0, 0)"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                """INSERT INTO offline_receipts
                   (order_id, offline_uuid, contract_version, server_fingerprint,
                    client_fingerprint_mismatch, sold_by_claimed_user_id,
                    synced_by_user_id, attribution_kind, sold_at_effective,
                    sold_at_client_utc, time_confidence, ingested_at)
                   VALUES (3, 'uuid-unknown', 0, ?, 0, 1, 2, 'LEGACY_UNKNOWN',
                           '2026-08-03 03:00:00.000000',
                           '2026-08-03 03:00:00.000000', 'LEGACY',
                           '2026-08-03 04:00:00.000000')""",
                (FINGERPRINT_V0,),
            )
        connection.rollback()

        # Partial index still serves the open-deficit lookup.
        plan = connection.execute(
            """EXPLAIN QUERY PLAN
               SELECT id FROM offline_stock_deficits
               WHERE remaining_quantity > 0 AND product_id = 1"""
        ).fetchall()
        assert any("ix_offline_stock_deficits_open" in str(row) for row in plan)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement,parameters,constraint_name",
    [
        # A lease secret must be a real SHA-256 digest, never a raw token.
        (
            """UPDATE offline_leases SET secret_sha256='plain-token'
               WHERE lease_id='lease-1'""",
            (),
            "ck_offline_leases_secret_digest",
        ),
        (
            """UPDATE offline_leases SET secret_sha256=?
               WHERE lease_id='lease-1'""",
            ("Z" * 64,),
            "ck_offline_leases_secret_digest",
        ),
        # A lease cannot expire before it was issued.
        (
            """UPDATE offline_leases
               SET expires_at='2026-07-01 00:00:00.000000'
               WHERE lease_id='lease-1'""",
            (),
            "ck_offline_leases_expiry",
        ),
        # Revocation is all three columns or none.
        (
            """UPDATE offline_leases
               SET revoked_at='2026-08-01 09:00:00.000000'
               WHERE lease_id='lease-1'""",
            (),
            "ck_offline_leases_revocation",
        ),
        # A v1 receipt without its lease binding.
        (
            "UPDATE offline_receipts SET lease_id=NULL WHERE id=1",
            (),
            "ck_offline_receipts_v1_binding",
        ),
        # The session id must be the lease id.
        (
            "UPDATE offline_receipts SET offline_session_id='other' WHERE id=1",
            (),
            "ck_offline_receipts_v1_binding",
        ),
        # A legacy v0 receipt may not acquire a lease after the fact.
        (
            """UPDATE offline_receipts SET lease_id='lease-1',
                   offline_session_id='lease-1', sequence=2,
                   server_anchor_id='anchor-1' WHERE id=2""",
            (),
            "ck_offline_receipts_v1_binding",
        ),
        # v0 is legacy attribution and legacy time confidence, nothing else.
        (
            "UPDATE offline_receipts SET time_confidence='ANCHORED_CLIENT' WHERE id=2",
            (),
            "ck_offline_receipts_legacy_contract",
        ),
        # OWNER_RECOVERY and RECOVERED only ever appear together.
        (
            "UPDATE offline_receipts SET attribution_kind='OWNER_RECOVERY' WHERE id=1",
            (),
            "ck_offline_receipts_owner_recovery",
        ),
        # An owner acknowledgement can never close exact deficit evidence.
        (
            """UPDATE offline_stock_deficits
               SET remaining_quantity=0, resolution_kind='OWNER_ACK',
                   resolved_by_user_id=1,
                   resolved_at='2026-08-02 01:00:00.000000', state_version=1
               WHERE id=1""",
            (),
            "ck_offline_stock_deficits_resolution_kind",
        ),
        # A closed deficit needs the stocktake actor and a bumped version.
        (
            """UPDATE offline_stock_deficits
               SET remaining_quantity=0, resolution_kind='STOCKTAKE'
               WHERE id=1""",
            (),
            "ck_offline_stock_deficits_closed",
        ),
        # INGESTED means there is an order; the other states mean there is not.
        (
            """UPDATE offline_receipt_registry SET state='ABANDONED'
               WHERE offline_uuid='uuid-v1'""",
            (),
            "ck_offline_receipt_registry_ingested_order",
        ),
        # A supersession must point somewhere other than itself.
        (
            """UPDATE offline_receipt_registry
               SET superseded_by_offline_uuid='uuid-corrected'
               WHERE offline_uuid='uuid-corrected'""",
            (),
            "ck_offline_receipt_registry_supersede",
        ),
        # A resolved issue must name who resolved it.
        (
            "UPDATE offline_receipt_issues SET state='RESOLVED' WHERE id=1",
            (),
            "ck_offline_receipt_issues_resolved_actor",
        ),
        # An acknowledgement without a reason is not an acknowledgement.
        (
            "UPDATE offline_receipt_issues SET reason=NULL WHERE id=2",
            (),
            "ck_offline_receipt_issues_ack_reason",
        ),
        # Only the two deficit kinds may carry an evidence pointer.
        (
            """UPDATE offline_receipt_issues SET evidence_id=1 WHERE id=2""",
            (),
            "ck_offline_receipt_issues_evidence_link",
        ),
        # A recovery reason must actually say something.
        (
            "UPDATE offline_recovery_actions SET reason='ngan' WHERE id=1",
            (),
            "ck_offline_recovery_actions_reason",
        ),
        # A correction without a replacement loses the corrected receipt.
        (
            """UPDATE offline_recovery_actions SET replacement_offline_uuid=NULL
               WHERE id=1""",
            (),
            "ck_offline_recovery_actions_replacement",
        ),
    ],
)
def test_check_constraints_reject_invalid_states(
    tmp_path, statement, parameters, constraint_name
):
    _coordinator_, database = _seeded(tmp_path, "check-constraints.db")
    connection = _connect(database)
    try:
        with pytest.raises(
            sqlite3.IntegrityError,
            match=rf"CHECK constraint failed: {constraint_name}$",
        ):
            connection.execute(statement, parameters)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement,constraint_name",
    [
        (
            """UPDATE offline_receipt_registry
               SET created_at='2026-08-01T04:00:00Z'
               WHERE offline_uuid='uuid-v1'""",
            "ck_offline_receipt_registry_time_format",
        ),
        (
            """UPDATE offline_recovery_actions
               SET performed_at='2026-08-01 05:30:00+07:00' WHERE id=1""",
            "ck_offline_recovery_actions_time_format",
        ),
        (
            """UPDATE offline_receipt_issues
               SET opened_at='2026-08-01 04:00:00' WHERE id=1""",
            "ck_offline_receipt_issues_time_format",
        ),
        (
            # Lexicographically T sorts after space, so the old expiry CHECK
            # would accept this actually-reversed mixed-format interval.
            """UPDATE offline_leases
               SET issued_at='2026-08-01 10:00:00.000000',
                   expires_at='2026-08-01T09:00:00.000000'
               WHERE lease_id='lease-1'""",
            "ck_offline_leases_time_format",
        ),
    ],
)
def test_time_checks_reject_noncanonical_text(tmp_path, statement, constraint_name):
    _coordinator_, database = _seeded(tmp_path, "time-check.db")
    connection = _connect(database)
    try:
        with pytest.raises(
            sqlite3.IntegrityError,
            match=rf"CHECK constraint failed: {constraint_name}$",
        ):
            connection.execute(statement)
    finally:
        connection.close()


# --------------------------------------------------------------- verifier


def test_valid_offline_rows_pass_every_verifier(tmp_path):
    coordinator, database = _seeded(tmp_path, "valid-rows.db")
    # The whole checked-in graph, not only 0004: offline rows must not break
    # the I04 baseline or the I05 money/cost verifiers either.
    assert coordinator.upgrade(I09C) == [I09C]
    _module(coordinator, I09C).verify(sqlite3.connect(database))
    _verify_revision(coordinator, database)


def test_verifier_allows_indexes_added_by_later_revisions(tmp_path):
    _coordinator_, database = _seeded(tmp_path, "later-index.db")
    later = _coordinator(database, project_root=_later_index_root(tmp_path))
    assert later.upgrade("head") == [I09C, LATER_INDEX]

    revision_0004 = next(
        item.module for item in later._graph().revisions if item.revision == I09
    )
    connection = sqlite3.connect(database)
    try:
        revision_0004.verify(connection)
    finally:
        connection.close()
    assert later.verify().current_revision == LATER_INDEX


@pytest.mark.parametrize(
    "statement",
    [
        """UPDATE offline_receipt_registry
           SET created_at='2026-08-01T04:00:00Z'
           WHERE offline_uuid='uuid-v1'""",
        """UPDATE offline_recovery_actions
           SET performed_at='2026-08-01 05:30:00+07:00' WHERE id=1""",
        "UPDATE offline_receipt_issues SET opened_at='2026-08-01 04:00:00' WHERE id=1",
        """UPDATE offline_leases
           SET issued_at='2026-08-01 10:00:00.000000',
               expires_at='2026-08-01T09:00:00.000000'
           WHERE lease_id='lease-1'""",
    ],
)
def test_time_verifier_rejects_noncanonical_corruption(tmp_path, statement):
    coordinator, database = _seeded(tmp_path, "time-verifier.db")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(statement)
        connection.commit()
        with pytest.raises(RuntimeError, match=r"^I09_VERIFY_TIME_FORMAT$"):
            _module(coordinator).verify(connection)
    finally:
        connection.close()

    with pytest.raises(Exception):
        coordinator.verify()


@pytest.mark.parametrize(
    "corruption,error_code",
    [
        ("drop_index", "I09_VERIFY_SCHEMA_OBJECTS"),
        ("lease_digest", "I09_VERIFY_LEASE_SHAPE"),
        ("lease_revocation", "I09_VERIFY_LEASE_REVOCATION"),
        ("lease_orphan_shop", "I09_VERIFY_LEASE_PRINCIPALS"),
        ("registry_state", "I09_VERIFY_REGISTRY_STATE"),
        ("registry_shop", "I09_VERIFY_REGISTRY_SCOPE"),
        ("registry_without_receipt", "I09_VERIFY_REGISTRY_RECEIPT_CARDINALITY"),
        ("tombstone_keeps_receipt", "I09_VERIFY_REGISTRY_RECEIPT_CARDINALITY"),
        ("receipt_shape", "I09_VERIFY_RECEIPT_SHAPE"),
        ("receipt_contract", "I09_VERIFY_RECEIPT_CONTRACT"),
        ("receipt_order_uuid", "I09_VERIFY_RECEIPT_DOCUMENT"),
        ("receipt_fingerprint", "I09_VERIFY_RECEIPT_DOCUMENT"),
        ("receipt_lease_user", "I09_VERIFY_RECEIPT_LEASE"),
        ("receipt_lease_anchor", "I09_VERIFY_RECEIPT_LEASE"),
        ("deficit_product", "I09_VERIFY_DEFICIT_EVIDENCE"),
        ("deficit_without_receipt", "I09_VERIFY_DEFICIT_EVIDENCE"),
        ("deficit_state", "I09_VERIFY_DEFICIT_STATE"),
        ("issue_state", "I09_VERIFY_ISSUE_STATE"),
        ("issue_scope", "I09_VERIFY_ISSUE_SCOPE"),
        ("issue_evidence_item", "I09_VERIFY_ISSUE_EVIDENCE"),
        ("issue_fake_evidence", "I09_VERIFY_ISSUE_EVIDENCE"),
        ("recovery_actor", "I09_VERIFY_RECOVERY_AUDIT"),
        ("recovery_missing_log", "I09_VERIFY_RECOVERY_AUDIT"),
        ("recovery_correction", "I09_VERIFY_RECOVERY_CORRECTION"),
    ],
)
def test_verifier_fails_closed_on_durable_corruption(tmp_path, corruption, error_code):
    coordinator, database = _seeded(tmp_path, f"corrupt-{corruption}.db")
    mutations = {
        "drop_index": ["DROP INDEX ux_offline_receipts_offline_uuid"],
        "lease_digest": [
            "UPDATE offline_leases SET secret_sha256='short' WHERE lease_id='lease-1'"
        ],
        "lease_revocation": [
            """UPDATE offline_leases
               SET revoked_at='2026-08-01 09:00:00.000000'
               WHERE lease_id='lease-1'"""
        ],
        "lease_orphan_shop": ["UPDATE offline_leases SET shop_id=99"],
        "registry_state": [
            """UPDATE offline_receipt_registry SET state='ABANDONED'
               WHERE offline_uuid='uuid-v1'"""
        ],
        "registry_shop": [
            "UPDATE offline_receipt_registry SET shop_id=2 WHERE offline_uuid='uuid-v1'"
        ],
        "registry_without_receipt": ["DELETE FROM offline_receipts WHERE id=1"],
        "tombstone_keeps_receipt": [
            """UPDATE offline_receipts SET offline_uuid='uuid-corrected'
               WHERE id=2""",
        ],
        "receipt_shape": [
            """UPDATE offline_receipts
               SET client_fingerprint_mismatch=1, client_fingerprint=NULL
               WHERE id=1"""
        ],
        "receipt_contract": [
            "UPDATE offline_receipts SET time_confidence='BOUNDED' WHERE id=2"
        ],
        "receipt_order_uuid": ["UPDATE orders SET offline_uuid='uuid-other' WHERE id=1"],
        "receipt_fingerprint": [
            """UPDATE offline_receipts
               SET server_fingerprint='cccccccccccccccccccccccccccccccc' WHERE id=1"""
        ],
        "receipt_lease_user": ["UPDATE offline_leases SET user_id=2"],
        "receipt_lease_anchor": [
            "UPDATE offline_receipts SET server_anchor_id='anchor-2' WHERE id=1"
        ],
        "deficit_product": [
            """INSERT INTO products
               (id, code, name, price, price_vnd, stock, is_active, shop_id,
                track_batches, cost_known_qty, cost_unknown_qty, cost_basis_vnd,
                cost_deficit_qty, cost_state_version)
               VALUES (2, 'SP-2', 'Hàng khác', 100, 100, 0, 1, 1, 0, 0, 0, 0, 0, 0)""",
            "UPDATE offline_stock_deficits SET product_id=2 WHERE id=1",
        ],
        # Deficit evidence only means something on a line that really was sold
        # offline; hang it off an ordinary order and it proves nothing.
        "deficit_without_receipt": [
            """INSERT INTO orders
               (id, shop_id, total_amount, discount_amount, payment_method, status,
                created_at, cash_paid_amount, refunded_amount, refund_due_amount,
                loyalty_points_redeemed, loyalty_discount_amount,
                loyalty_points_earned, total_vnd, cash_paid_vnd)
               VALUES (3, 1, 100, 0, 'cash', 'PAID', '2026-08-03 03:00:00', 100, 0, 0,
                       0, 0, 0, 100, 100)""",
            """INSERT INTO order_items
               (id, order_id, product_id, product_name, price, quantity,
                unit_price_vnd, net_amount_vnd, cost_unknown_qty)
               VALUES (4, 3, 1, 'Hàng offline', 100, 1, 100, 100, 1)""",
            "UPDATE offline_stock_deficits SET order_item_id=4 WHERE id=1",
        ],
        "deficit_state": [
            """UPDATE offline_stock_deficits
               SET remaining_quantity=0, resolution_kind=NULL WHERE id=2"""
        ],
        "issue_state": [
            "UPDATE offline_receipt_issues SET resolved_by_user_id=NULL WHERE id=2"
        ],
        "issue_scope": ["UPDATE offline_receipt_issues SET order_item_id=3 WHERE id=1"],
        "issue_evidence_item": [
            "UPDATE offline_receipt_issues SET evidence_id=2 WHERE id=1"
        ],
        "issue_fake_evidence": [
            "UPDATE offline_receipt_issues SET evidence_id=1 WHERE id=2"
        ],
        "recovery_actor": [
            "UPDATE offline_recovery_actions SET performed_by_user_id=2 WHERE id=1"
        ],
        "recovery_missing_log": [
            "UPDATE offline_recovery_actions SET system_log_id=99 WHERE id=1"
        ],
        "recovery_correction": [
            """UPDATE offline_receipt_registry SET state='ABANDONED',
                   superseded_by_offline_uuid=NULL WHERE offline_uuid='uuid-corrected'"""
        ],
    }[corruption]

    connection = sqlite3.connect(database)
    try:
        # A restored backup or a legacy writer can hold rows the declared
        # constraints would refuse, so the durable verifier must catch them too.
        connection.execute("PRAGMA ignore_check_constraints = ON")
        for statement in mutations:
            connection.execute(statement)
        connection.commit()
        with pytest.raises(RuntimeError, match=error_code):
            _module(coordinator).verify(connection)
    finally:
        connection.close()

    # The coordinator's startup gate fails closed on the same database.
    with pytest.raises(Exception):
        coordinator.verify()


def test_verifier_does_not_depend_on_wall_clock(tmp_path):
    coordinator, _database = _seeded(tmp_path, "clock-independent.db")
    assert coordinator.upgrade(I09C) == [I09C]
    source = (
        PROJECT_ROOT / f"migrations/versions/{I09}.py"
    ).read_text(encoding="utf-8")
    assert "CURRENT_TIMESTAMP" not in source
    assert "datetime(" not in source
    # An already-expired lease is a runtime state, never schema corruption.
    _verify_revision(coordinator, _database)


# ---------------------------------------------------------- version matrix


def test_version_matrix_is_fail_closed_in_both_directions(tmp_path):
    """DB 0003 vs 0004 against a 0004-aware and a pre-0004 binary."""
    old_root = _pre_0004_root(tmp_path)

    # Cell 1: new binary, database still at 0003 -> lifespan verification
    # raises, so the process fails boot/restarts before any listener can return
    # a JSON 503.  The operator must run the migration CLI first.
    pending = tmp_path / "new-binary-old-db.db"
    coordinator = _at_i05(pending)
    assert coordinator.check().classification == "MANAGED_PENDING"
    assert coordinator.check().pending_revisions == (I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3)
    with pytest.raises(RevisionStateError, match="not at the checked-in head"):
        coordinator.verify()

    # Cell 2: new binary, database at head -> ready.
    assert coordinator.upgrade("head") == [I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    assert coordinator.verify().current_revision == PLAN3

    # Cell 3: old binary against a 0004 database -> refuses to boot rather than
    # serving a schema it does not know.
    with pytest.raises(RevisionStateError):
        _coordinator(pending, project_root=old_root).verify()

    # Cell 4: old binary against its own 0003 database -> still ready, which is
    # why the binary must be rolled back before the database ever reaches 0004.
    legacy = tmp_path / "old-binary-old-db.db"
    old_coordinator = _coordinator(legacy, project_root=old_root)
    old_coordinator.init()
    assert old_coordinator.upgrade("head") == [I04, I05]
    assert old_coordinator.verify().current_revision == I05
