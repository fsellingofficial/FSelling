"""I09-C migration 0005 contract tests; every database is temporary.

0004 released the offline deficit/issue tables with no guard and no rows. This
revision adds the durable guards and backfills `orders.offline_issue` - a
comma-joined string that cannot say which line broke or how much is still
missing - into real issue rows.

Scope is the migration only: triggers, backfill, verifier and atomicity. Ingest,
stocktake reconciliation and the acknowledge API belong to the service tests.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
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

EXPECTED_TRIGGERS = {
    "trg_i09c_offline_stock_deficit_insert",
    "trg_i09c_offline_stock_deficit_update",
    "trg_i09c_offline_stock_deficit_delete",
    "trg_i09c_offline_issue_insert",
    "trg_i09c_offline_issue_update",
    "trg_i09c_offline_issue_delete",
}

TS = "2026-08-01 04:00:00.000000"
FINGERPRINT = "e" * 64


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


def _normalized_checksum(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _module(coordinator: MigrationCoordinator, revision: str = I09C):
    return next(
        item.module
        for item in coordinator._graph().revisions
        if item.revision == revision
    )


def _verify_0005(coordinator: MigrationCoordinator, path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        _module(coordinator).verify(connection)
    finally:
        connection.close()


def _rows(path: Path, sql: str, params=()):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _at_0004(path: Path, **kwargs) -> MigrationCoordinator:
    coordinator = _coordinator(path, **kwargs)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I09) == [I04, I05, I09]
    return coordinator


# --------------------------------------------------------------------- seeds


def _seed_baseline(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO users
           (id, username, hashed_password, role, is_verified, is_active,
            failed_login_count, verification_attempts)
           VALUES (1, 'i09c-owner', 'x', 'SELLER', 1, 1, 0, 0)"""
    )
    connection.execute(
        "INSERT INTO shops (id, name, is_active, owner_id) VALUES (1, 'Shop', 1, 1)"
    )
    for product_id, tracked in ((1, 0), (2, 1)):
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, price_vnd, stock, is_active, shop_id,
                track_batches, cost_known_qty, cost_unknown_qty, cost_basis_vnd,
                cost_deficit_qty, cost_state_version)
               VALUES (?, ?, ?, 100, 100, 0, 1, 1, ?, 0, 0, 0, 0, 0)""",
            (product_id, f"SP-{product_id}", f"Hang {product_id}", tracked),
        )


def _seed_offline_order(
    connection: sqlite3.Connection,
    order_id: int,
    issue: str,
    *,
    product_id: int = 1,
    sold_at: str = "2026-08-01 03:00:00",
    missing_product: bool = False,
    with_receipt: bool = True,
    quantity: int = 1,
) -> int:
    """One legacy offline order plus one line; returns the order_item id."""
    connection.execute(
        """INSERT INTO orders
           (id, shop_id, total_amount, discount_amount, payment_method, status,
            created_at, created_by_user_id, cash_paid_amount, refunded_amount,
            refund_due_amount, offline_uuid, sold_offline_at, offline_issue,
            loyalty_points_redeemed, loyalty_discount_amount, loyalty_points_earned,
            total_vnd, discount_vnd, cash_paid_vnd, refunded_vnd, refund_due_vnd,
            loyalty_discount_vnd, inventory_reversed, inventory_reversal_version)
           VALUES (?, 1, ?, 0, 'cash', 'PAID', ?, 1, ?, 0, 0, ?, ?, ?,
                   0, 0, 0, ?, 0, ?, 0, 0, 0, 0, 0)""",
        (order_id, float(100 * quantity), float(100 * quantity), sold_at,
         f"uuid-{order_id}", sold_at, issue, 100 * quantity, 100 * quantity),
    )
    item_id = order_id * 10
    connection.execute(
        """INSERT INTO order_items
           (id, order_id, product_id, product_name, price, quantity,
            unit_price_vnd, discount_vnd, loyalty_discount_vnd, net_amount_vnd,
            cost_known_qty, cost_unknown_qty, cost_basis_vnd, returned_total_qty,
            returned_known_qty, returned_unknown_qty, returned_cost_basis_vnd,
            returned_refund_vnd, cost_return_version, inventory_reversed,
            inventory_reversal_version)
           VALUES (?, ?, ?, 'Hang', 100, ?, 100, 0, 0, ?,
                   0, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0)""",
        (item_id, order_id, None if missing_product else product_id,
         quantity, 100 * quantity, quantity),
    )
    if with_receipt:
        connection.execute(
            """INSERT INTO offline_receipt_registry
               (offline_uuid, shop_id, order_id, server_fingerprint,
                contract_version, state, created_at, updated_at, state_version)
               VALUES (?, 1, ?, ?, 0, 'INGESTED', ?, ?, 0)""",
            (f"uuid-{order_id}", order_id, FINGERPRINT, TS, TS),
        )
        connection.execute(
            """INSERT INTO offline_receipts
               (order_id, offline_uuid, contract_version, server_fingerprint,
                client_fingerprint_mismatch, sold_by_claimed_user_id,
                synced_by_user_id, attribution_kind, sold_at_effective,
                sold_at_client_utc, time_confidence, ingested_at)
               VALUES (?, ?, 0, ?, 0, 1, 1, 'LEGACY_UNKNOWN',
                       '2026-08-01 03:00:00.000000',
                       '2026-08-01 03:00:00.000000', 'LEGACY', ?)""",
            (order_id, f"uuid-{order_id}", FINGERPRINT, TS),
        )
    return item_id


def _second_line(
    connection: sqlite3.Connection, *, order_id: int, product_id: int, quantity: int
) -> int:
    """A second short line on an existing order, kept I05-consistent."""
    item_id = order_id * 10 + 1
    connection.execute(
        """INSERT INTO order_items
           (id, order_id, product_id, product_name, price, quantity,
            unit_price_vnd, discount_vnd, loyalty_discount_vnd, net_amount_vnd,
            cost_known_qty, cost_unknown_qty, cost_basis_vnd, returned_total_qty,
            returned_known_qty, returned_unknown_qty, returned_cost_basis_vnd,
            returned_refund_vnd, cost_return_version, inventory_reversed,
            inventory_reversal_version)
           VALUES (?, ?, ?, 'Hang', 100, ?, 100, 0, 0, ?,
                   0, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0)""",
        (item_id, order_id, product_id, quantity, 100 * quantity, quantity),
    )
    connection.execute(
        """UPDATE orders SET total_amount = total_amount + ?,
                             cash_paid_amount = cash_paid_amount + ?,
                             total_vnd = total_vnd + ?, cash_paid_vnd = cash_paid_vnd + ?
            WHERE id = ?""",
        (float(100 * quantity), float(100 * quantity), 100 * quantity,
         100 * quantity, order_id),
    )
    return item_id


# ------------------------------------------------------------------ upgrades


def test_fresh_and_restart_verify_are_stable(tmp_path):
    database = tmp_path / "fresh.db"
    coordinator = _coordinator(database)

    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
    report = coordinator.verify()
    assert report.current_revision == report.head_revision == PLAN2
    assert report.revision_count == 12

    assert coordinator.upgrade("head") == []
    assert coordinator.verify().database_uuid == report.database_uuid
    assert coordinator.check().classification == "READY"


def test_upgrade_from_0004_adds_only_triggers(tmp_path):
    database = tmp_path / "step.db"
    coordinator = _at_0004(database)
    before = {row[0] for row in _rows(database, "SELECT name FROM sqlite_master")}

    assert coordinator.upgrade(I09C) == [I09C]
    _verify_0005(coordinator, database)

    after = {row[0] for row in _rows(database, "SELECT name FROM sqlite_master")}
    assert after - before == EXPECTED_TRIGGERS
    assert not any(name.startswith("fs_migration_") for name in after - before)


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


def test_0005_is_linear_self_contained_and_checksummed(tmp_path):
    graph = _coordinator(tmp_path / "unused.db")._graph()
    assert [item.revision for item in graph.revisions] == [
        ROOT, I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2
    ]
    spec = next(item for item in graph.revisions if item.revision == I09C)
    assert spec.down_revision == I09

    revision_path = PROJECT_ROOT / f"migrations/versions/{I09C}.py"
    digest = _normalized_checksum(revision_path)
    manifest = json.loads(
        (PROJECT_ROOT / "migrations/checksums.json").read_text(encoding="utf-8")
    )
    assert manifest["revisions"][I09C] == {
        "down_revision": I09,
        "path": f"versions/{I09C}.py",
        "sha256": digest,
    }
    assert spec.checksum == digest

    source = revision_path.read_text(encoding="utf-8")
    assert "from alembic import op" in source
    assert "import fselling" not in source
    assert "executescript" not in source
    assert "fs_migration_" not in source
    with pytest.raises(RuntimeError, match="forward-only"):
        spec.module.downgrade()


@pytest.mark.parametrize("stage", ["after_ddl", "after_journal", "after_verify"])
def test_0005_rolls_back_as_one_unit(tmp_path, stage):
    database = tmp_path / f"rollback-{stage}.db"

    def fail(selected, revision):
        if selected == stage and revision == I09C:
            raise RuntimeError(f"fault at {stage}")

    coordinator = _at_0004(database, fault_hook=fail)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        _seed_offline_order(connection, 1, "CA_DA_CHOT")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match=stage):
        coordinator.upgrade("head")

    names = {row[0] for row in _rows(database, "SELECT name FROM sqlite_master")}
    assert not (names & EXPECTED_TRIGGERS)
    # Neither the triggers nor a single backfilled row survive a failed attempt.
    assert _rows(database, "SELECT COUNT(*) FROM offline_receipt_issues") == [(0,)]
    assert _rows(database, "SELECT version_num FROM alembic_version") == [(I09,)]

    assert _coordinator(database).upgrade(I09C) == [I09C]
    _verify_0005(_coordinator(database), database)
    assert _rows(database, "SELECT COUNT(*) FROM offline_receipt_issues") == [(1,)]


# ------------------------------------------------------------------ backfill


def _backfilled(tmp_path: Path, name: str, seeds) -> tuple[MigrationCoordinator, Path]:
    database = tmp_path / name
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        seeds(connection)
        connection.commit()
    finally:
        connection.close()
    assert coordinator.upgrade("head") == [I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
    coordinator.verify()
    return coordinator, database


def test_backfill_maps_every_legacy_code_to_its_own_shape(tmp_path):
    def seeds(connection):
        _seed_offline_order(connection, 1, "GIA_DOI")
        _seed_offline_order(connection, 2, "CA_DA_CHOT")
        _seed_offline_order(connection, 3, "KHONG_CO_CA")
        _seed_offline_order(connection, 4, "SP_KHONG_CON", missing_product=True)

    _coordinator_, database = _backfilled(tmp_path, "codes.db", seeds)
    rows = _rows(
        database,
        """SELECT order_id, issue_code, evidence_kind, severity, state,
                  resolution_kind, order_item_id, resolved_by_user_id
           FROM offline_receipt_issues ORDER BY order_id""",
    )
    assert rows == [
        # Đơn ghi đúng giá khách đã trả; không có gì phải làm.
        (1, "GIA_DOI", "CATALOG", "INFO", "RESOLVED",
         "MIGRATION_INFORMATIONAL", None, 1),
        (2, "CA_DA_CHOT", "SHIFT", "ACTION", "OPEN", None, None, None),
        (3, "KHONG_CO_CA", "SHIFT", "ACTION", "OPEN", None, None, None),
        # `product_id IS NULL` is a checkable fact about the line, not a guess.
        (4, "SP_KHONG_CON", "CATALOG", "ACTION", "OPEN", None, 40, None),
    ]


def test_ton_am_with_tracked_evidence_links_each_exact_row(tmp_path):
    def seeds(connection):
        item_open = _seed_offline_order(
            connection, 1, "TON_AM", product_id=2, quantity=3
        )
        item_done = _seed_offline_order(
            connection, 2, "TON_AM", product_id=2, quantity=2
        )
        # The deliberate Product.stock < SUM(batch) gap the open evidence stands for.
        connection.execute("UPDATE products SET stock = -3 WHERE id = 2")
        connection.execute(
            """INSERT INTO offline_batch_stock_deficits
               (id, order_item_id, product_id, deficit_quantity,
                remaining_quantity, resolution_kind, state_version)
               VALUES (1, ?, 2, 3, 3, NULL, 0)""",
            (item_open,),
        )
        connection.execute(
            """INSERT INTO offline_batch_stock_deficits
               (id, order_item_id, product_id, deficit_quantity,
                remaining_quantity, resolution_kind, state_version)
               VALUES (2, ?, 2, 2, 0, 'MIGRATION_RECONCILED', 0)""",
            (item_done,),
        )

    _coordinator_, database = _backfilled(tmp_path, "tracked.db", seeds)
    rows = _rows(
        database,
        """SELECT order_id, order_item_id, product_id, evidence_kind, evidence_id,
                  state, resolution_kind
           FROM offline_receipt_issues ORDER BY order_id""",
    )
    # State follows the evidence, never the other way round.
    assert rows == [
        (1, 10, 2, "OFFLINE_BATCH_DEFICIT", 1, "OPEN", None),
        (2, 20, 2, "OFFLINE_BATCH_DEFICIT", 2, "RESOLVED", "MIGRATION_RECONCILED"),
    ]


def test_ton_am_without_exact_evidence_stays_legacy_ambiguous(tmp_path):
    def seeds(connection):
        _seed_offline_order(connection, 1, "TON_AM")
        # An aggregate that cannot be attributed to any line. Reading it here
        # would be inventing an allocation for goods that really went missing.
        connection.execute(
            "UPDATE products SET cost_deficit_qty = 7, stock = -7 WHERE id = 1"
        )

    _coordinator_, database = _backfilled(tmp_path, "ambiguous.db", seeds)
    assert _rows(
        database,
        """SELECT issue_code, evidence_kind, evidence_id, order_item_id, state
           FROM offline_receipt_issues""",
    ) == [("TON_AM", "LEGACY_AMBIGUOUS", None, None, "OPEN")]
    assert _rows(database, "SELECT COUNT(*) FROM offline_stock_deficits") == [(0,)]


def test_backfill_handles_multiple_codes_and_legacy_date_only_time(tmp_path):
    def seeds(connection):
        _seed_offline_order(
            connection, 1, "TON_AM,CA_DA_CHOT, GIA_DOI", sold_at="2026-01-01"
        )

    _coordinator_, database = _backfilled(tmp_path, "mixed.db", seeds)
    rows = _rows(
        database,
        "SELECT issue_code, opened_at FROM offline_receipt_issues ORDER BY id",
    )
    assert [row[0] for row in rows] == ["TON_AM", "CA_DA_CHOT", "GIA_DOI"]
    # A date-only legacy value becomes that day's midnight; nothing invented.
    assert {row[1] for row in rows} == {"2026-01-01 00:00:00.000000"}


def test_backfill_never_borrows_an_account_from_another_shop(tmp_path):
    """No owner and no creator means nobody is accountable - so nothing is signed.

    Naming an unrelated account on a resolved row is a false statement about who
    took responsibility for somebody else's money.
    """
    database = tmp_path / "no-actor.db"
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        connection.execute(
            """INSERT INTO users
               (id, username, hashed_password, role, is_verified, is_active,
                failed_login_count, verification_attempts)
               VALUES (2, 'nguoi-shop-khac', 'x', 'SELLER', 1, 1, 0, 0)"""
        )
        connection.execute("UPDATE shops SET owner_id = NULL WHERE id = 1")
        _seed_offline_order(connection, 1, "GIA_DOI")
        connection.execute("UPDATE orders SET created_by_user_id = NULL WHERE id = 1")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I09C_BACKFILL_NO_ACCOUNTABLE_ACTOR"):
        coordinator.upgrade("head")
    assert _rows(database, "SELECT version_num FROM alembic_version") == [(I09,)]
    assert _rows(database, "SELECT COUNT(*) FROM offline_receipt_issues") == [(0,)]


def test_backfill_falls_back_to_the_order_creator_not_the_oldest_account(tmp_path):
    def seeds(connection):
        connection.execute(
            """INSERT INTO users
               (id, username, hashed_password, role, is_verified, is_active,
                failed_login_count, verification_attempts)
               VALUES (2, 'nguoi-ban', 'x', 'SELLER', 1, 1, 0, 0)"""
        )
        connection.execute("UPDATE shops SET owner_id = NULL WHERE id = 1")
        _seed_offline_order(connection, 1, "GIA_DOI")
        connection.execute("UPDATE orders SET created_by_user_id = 2 WHERE id = 1")

    _coordinator_, database = _backfilled(tmp_path, "creator.db", seeds)
    assert _rows(
        database, "SELECT resolved_by_user_id FROM offline_receipt_issues"
    ) == [(2,)]


@pytest.mark.parametrize("issue", ["KHONG_HIEU", "TON_AM,LA_MA", " "])
def test_unknown_legacy_code_blocks_the_migration(tmp_path, issue):
    database = tmp_path / "unknown.db"
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        _seed_offline_order(connection, 1, issue)
        connection.commit()
    finally:
        connection.close()

    if issue.strip():
        with pytest.raises(RuntimeError, match="I09C_BACKFILL_UNKNOWN_ISSUE_CODE"):
            coordinator.upgrade("head")
        assert _rows(database, "SELECT version_num FROM alembic_version") == [(I09,)]
    else:
        # Whitespace-only carries no claim at all, so it is simply nothing.
        assert coordinator.upgrade("head") == [I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
        assert _rows(database, "SELECT COUNT(*) FROM offline_receipt_issues") == [(0,)]


def test_backfill_completes_partial_issue_coverage(tmp_path):
    """One existing row must not hide the scopes still missing beside it.

    Two exact deficits on one order, one already represented: skipping by
    `(order_id, issue_code)` would leave the second piece of evidence with no
    issue at all - a real shortfall that never reaches the Đối Soát screen.
    """
    database = tmp_path / "partial.db"
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        # ONE order, two short lines: the case a per-code skip cannot see.
        item_a = _seed_offline_order(connection, 1, "TON_AM", product_id=2, quantity=3)
        item_b = _second_line(connection, order_id=1, product_id=2, quantity=2)
        connection.execute("UPDATE products SET stock = -5 WHERE id = 2")
        for evidence_id, item_id, quantity in ((1, item_a, 3), (2, item_b, 2)):
            connection.execute(
                """INSERT INTO offline_batch_stock_deficits
                   (id, order_item_id, product_id, deficit_quantity,
                    remaining_quantity, resolution_kind, state_version)
                   VALUES (?, ?, 2, ?, ?, NULL, 0)""",
                (evidence_id, item_id, quantity, quantity),
            )
        # Only the first evidence row already has its issue.
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, order_item_id, product_id, issue_code, evidence_kind,
                evidence_id, severity, state, opened_at, state_version)
               VALUES (1, ?, 2, 'TON_AM', 'OFFLINE_BATCH_DEFICIT', 1, 'ACTION',
                       'OPEN', ?, 0)""",
            (item_a, TS),
        )
        connection.commit()
    finally:
        connection.close()

    assert coordinator.upgrade("head") == [I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
    coordinator.verify()
    assert _rows(
        database,
        """SELECT order_id, order_item_id, evidence_kind, evidence_id, issue_code
           FROM offline_receipt_issues ORDER BY evidence_id""",
    ) == [
        (1, item_a, "OFFLINE_BATCH_DEFICIT", 1, "TON_AM"),
        (1, item_b, "OFFLINE_BATCH_DEFICIT", 2, "TON_AM"),
    ]


def test_backfill_refuses_to_reuse_a_scope_that_says_something_else(tmp_path):
    """Same durable scope, different evidence: two claims about one problem."""
    database = tmp_path / "conflict.db"
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        _seed_offline_order(connection, 1, "CA_DA_CHOT")
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, issue_code, evidence_kind, severity, state, opened_at,
                state_version)
               VALUES (1, 'CA_DA_CHOT', 'TIME', 'INFO', 'OPEN', ?, 0)""",
            (TS,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I09C_BACKFILL_SCOPE_CONFLICT"):
        coordinator.upgrade("head")
    assert _rows(database, "SELECT version_num FROM alembic_version") == [(I09,)]


def test_backfill_is_a_no_op_when_rows_already_exist(tmp_path):
    """A retried attempt, or a newer binary that already wrote the rows."""
    database = tmp_path / "rerun.db"
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        _seed_offline_order(connection, 1, "CA_DA_CHOT")
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, issue_code, evidence_kind, severity, state, opened_at,
                state_version)
               VALUES (1, 'CA_DA_CHOT', 'SHIFT', 'ACTION', 'OPEN', ?, 0)""",
            (TS,),
        )
        connection.commit()
    finally:
        connection.close()

    assert coordinator.upgrade("head") == [I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
    coordinator.verify()
    assert _rows(database, "SELECT COUNT(*) FROM offline_receipt_issues") == [(1,)]

    # Running the planner/backfill a second time on the same database changes
    # nothing either.
    module = _module(coordinator)
    connection = _connect(database)
    try:
        assert module._plan_backfill(connection)
    finally:
        connection.close()


# ------------------------------------------------------------------ triggers


def _guarded(tmp_path: Path, name: str):
    database = tmp_path / name
    coordinator = _at_0004(database)
    connection = _connect(database)
    try:
        _seed_baseline(connection)
        item_id = _seed_offline_order(connection, 1, "TON_AM")
        connection.execute(
            """INSERT INTO offline_stock_deficits
               (id, order_item_id, product_id, deficit_quantity,
                remaining_quantity, state_version)
               VALUES (1, ?, 1, 5, 5, 0)""",
            (item_id,),
        )
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (id, order_id, order_item_id, product_id, issue_code,
                evidence_kind, evidence_id, severity, state, opened_at,
                state_version)
               VALUES (1, 1, ?, 1, 'TON_AM', 'OFFLINE_STOCK_DEFICIT', 1,
                       'ACTION', 'OPEN', ?, 0)""",
            (item_id, TS),
        )
        connection.commit()
    finally:
        connection.close()
    assert coordinator.upgrade("head") == [I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2]
    coordinator.verify()
    return coordinator, database


@pytest.mark.parametrize(
    "statement,code",
    [
        # Immutable identity and provenance.
        ("UPDATE offline_stock_deficits SET order_item_id = 99, state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        ("UPDATE offline_stock_deficits SET deficit_quantity = 9, state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        # No silent reduction: the version trail is what proves a change happened.
        ("UPDATE offline_stock_deficits SET remaining_quantity = 4 WHERE id = 1",
         "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        # No reopen, no top-up.
        ("UPDATE offline_stock_deficits SET remaining_quantity = 5,"
         " state_version = 1 WHERE id = 1", "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        # Closing without naming who counted, or when.
        ("UPDATE offline_stock_deficits SET remaining_quantity = 0,"
         " resolution_kind = 'STOCKTAKE', state_version = 1 WHERE id = 1",
         "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        ("UPDATE offline_stock_deficits SET remaining_quantity = 0,"
         " resolution_kind = 'STOCKTAKE', resolved_by_user_id = 1,"
         " resolved_at = '2026-08-02', state_version = 1 WHERE id = 1",
         "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        # A click cannot make missing goods reappear.
        ("UPDATE offline_stock_deficits SET remaining_quantity = 0,"
         " resolution_kind = 'OWNER_ACK', resolved_by_user_id = 1,"
         " resolved_at = '2026-08-02 01:00:00.000000', state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        ("DELETE FROM offline_stock_deficits WHERE id = 1",
         "I09C_OFFLINE_DEFICIT_IMMUTABLE"),
        # Issues: scope and evidence are what the row IS.
        ("UPDATE offline_receipt_issues SET evidence_kind = 'SHIFT',"
         " state = 'RESOLVED', resolution_kind = 'X', state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_ISSUE_TRANSITION"),
        ("UPDATE offline_receipt_issues SET issue_code = 'GIA_DOI',"
         " state = 'RESOLVED', resolution_kind = 'X', state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_ISSUE_TRANSITION"),
        # Acknowledging without a reason, or without a version bump.
        ("UPDATE offline_receipt_issues SET state = 'ACKNOWLEDGED',"
         " resolved_by_user_id = 1, resolved_at = ?, state_version = 1"
         " WHERE id = 1", "I09C_OFFLINE_ISSUE_TRANSITION"),
        ("UPDATE offline_receipt_issues SET state = 'ACKNOWLEDGED',"
         " reason = 'da xem', resolved_by_user_id = 1, resolved_at = ?"
         " WHERE id = 1", "I09C_OFFLINE_ISSUE_TRANSITION"),
        ("DELETE FROM offline_receipt_issues WHERE id = 1",
         "I09C_OFFLINE_ISSUE_IMMUTABLE"),
    ],
)
def test_guards_reject_impossible_writes(tmp_path, statement, code):
    _coordinator_, database = _guarded(tmp_path, "guard.db")
    connection = _connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=code):
            connection.execute(statement, (TS,) if "?" in statement else ())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        # Born already closed: nobody counted anything.
        "INSERT INTO offline_stock_deficits"
        " (order_item_id, product_id, deficit_quantity, remaining_quantity,"
        "  resolution_kind, resolved_by_user_id, resolved_at, state_version)"
        " VALUES (10, 1, 3, 0, 'STOCKTAKE', 1, '2026-08-02 01:00:00.000000', 1)",
        # Born half-consumed.
        "INSERT INTO offline_stock_deficits"
        " (order_item_id, product_id, deficit_quantity, remaining_quantity,"
        "  state_version) VALUES (10, 1, 3, 2, 0)",
    ],
)
def test_a_deficit_must_be_born_open(tmp_path, statement):
    _coordinator_, database = _guarded(tmp_path, "born.db")
    connection = _connect(database)
    try:
        with pytest.raises(
            sqlite3.IntegrityError, match="I09C_OFFLINE_DEFICIT_INSERT_NOT_OPEN"
        ):
            connection.execute(statement)
    finally:
        connection.close()


def test_an_acknowledgement_must_be_an_audited_transition(tmp_path):
    _coordinator_, database = _guarded(tmp_path, "ack-insert.db")
    connection = _connect(database)
    try:
        with pytest.raises(
            sqlite3.IntegrityError, match="I09C_OFFLINE_ISSUE_INSERT_INVALID"
        ):
            connection.execute(
                """INSERT INTO offline_receipt_issues
                   (order_id, issue_code, evidence_kind, severity, state, reason,
                    opened_at, resolved_at, resolved_by_user_id, state_version)
                   VALUES (1, 'CA_DA_CHOT', 'SHIFT', 'ACTION', 'ACKNOWLEDGED',
                           'da xem', ?, ?, 1, 0)""",
                (TS, TS),
            )
    finally:
        connection.close()


def test_the_legal_lifecycle_is_still_reachable(tmp_path):
    """Guards must not brick the paths recovery and stocktake actually need."""
    coordinator, database = _guarded(tmp_path, "legal.db")
    connection = _connect(database)
    try:
        connection.execute(
            "UPDATE offline_stock_deficits SET remaining_quantity = 2,"
            " state_version = state_version + 1 WHERE id = 1"
        )
        connection.execute(
            "UPDATE offline_stock_deficits SET remaining_quantity = 0,"
            " resolution_kind = 'STOCKTAKE', resolved_by_user_id = 1,"
            " resolved_at = ?, state_version = state_version + 1 WHERE id = 1",
            (TS,),
        )
        connection.execute(
            "UPDATE offline_receipt_issues SET state = 'RESOLVED',"
            " resolution_kind = 'STOCKTAKE', resolved_by_user_id = 1,"
            " resolved_at = ?, state_version = state_version + 1 WHERE id = 1",
            (TS,),
        )
        connection.commit()
    finally:
        connection.close()
    coordinator.verify()
    assert _rows(database, "SELECT state_version FROM offline_stock_deficits") == [(2,)]


# ------------------------------------------------------------------ verifier


@pytest.mark.parametrize(
    "corruption,code",
    [
        # A reduction with no version trail: a bypassed trigger or a restore.
        ("UPDATE offline_stock_deficits SET remaining_quantity = 3 WHERE id = 1",
         "I09C_VERIFY_DEFICIT_VERSION_TRAIL"),
        ("UPDATE offline_stock_deficits SET remaining_quantity = 0,"
         " resolution_kind = 'STOCKTAKE', resolved_by_user_id = 1,"
         " resolved_at = '2026-08-02', state_version = 1 WHERE id = 1",
         "I09C_VERIFY_DEFICIT_RESOLUTION"),
        ("UPDATE offline_stock_deficits SET resolution_kind = 'STOCKTAKE'"
         " WHERE id = 1", "I09C_VERIFY_DEFICIT_RESOLUTION"),
        # An issue closed while its evidence is still open.
        ("UPDATE offline_receipt_issues SET state = 'RESOLVED',"
         " resolution_kind = 'STOCKTAKE', resolved_by_user_id = 1,"
         " resolved_at = '2026-08-02 01:00:00.000000', state_version = 1"
         " WHERE id = 1", "I09C_VERIFY_ISSUE_EVIDENCE_STATE"),
        # An acknowledgement dressed up as a resolution.
        ("UPDATE offline_receipt_issues SET state = 'ACKNOWLEDGED',"
         " reason = 'x', resolution_kind = 'OWNER_ACK', resolved_by_user_id = 1,"
         " resolved_at = '2026-08-02 01:00:00.000000', state_version = 1"
         " WHERE id = 1", "I09C_VERIFY_ISSUE_LIFECYCLE"),
        # The compatibility mirror drifting away from the issue table.
        ("UPDATE orders SET offline_issue = 'CA_DA_CHOT' WHERE id = 1",
         "I09C_VERIFY_MIRROR_HAS_EVERY_ISSUE"),
        # A token nobody carries is an orphan whether or not the code is known:
        # the check must not need a list of every code that will ever exist.
        ("UPDATE orders SET offline_issue = 'TON_AM,GIA_DOI' WHERE id = 1",
         "I09C_VERIFY_MIRROR_HAS_NO_ORPHAN_CODE"),
        ("UPDATE orders SET offline_issue = 'TON_AM,DIEU_LA' WHERE id = 1",
         "I09C_VERIFY_MIRROR_HAS_NO_ORPHAN_CODE"),
    ],
)
def test_verifier_fails_closed_on_durable_corruption(tmp_path, corruption, code):
    coordinator, database = _guarded(tmp_path, "corrupt.db")
    connection = sqlite3.connect(database)
    try:
        # `ignore_check_constraints` is exactly how a bad restore or a legacy
        # writer gets past the declared constraints, so the verifier must not
        # lean on them.
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("DROP TRIGGER trg_i09c_offline_stock_deficit_update")
        connection.execute("DROP TRIGGER trg_i09c_offline_issue_update")
        connection.execute(corruption)
        for statement in _module(coordinator).DDL:
            if "_update" in statement and "CREATE TRIGGER" in statement:
                connection.execute(statement)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match=code):
        _verify_0005(coordinator, database)


# ------------------------------------------- extensibility of the mirror check


def _add_future_issue(connection, *, mirror: bool, issue: bool) -> None:
    """A durable issue kind a later I09 slice may introduce (time drift)."""
    if mirror:
        connection.execute("UPDATE orders SET offline_issue = ? WHERE id = 1",
                           ("TON_AM,DONG_HO_LECH",))
    if issue:
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, issue_code, evidence_kind, severity, state, opened_at,
                state_version)
               VALUES (1, 'DONG_HO_LECH', 'TIME', 'ACTION', 'OPEN', ?, 0)""",
            (TS,),
        )


def test_a_future_issue_code_with_its_mirror_verifies(tmp_path):
    """I09-D/E/G must be able to add a durable issue without editing 0005.

    Freezing the code list here would mean the next slice can satisfy neither
    direction of the mirror check: no mirror fails one, a mirror fails the other.
    """
    coordinator, database = _guarded(tmp_path, "future-code.db")
    connection = _connect(database)
    try:
        _add_future_issue(connection, mirror=True, issue=True)
        connection.commit()
    finally:
        connection.close()

    _verify_0005(coordinator, database)
    assert coordinator.verify().current_revision == PLAN2


@pytest.mark.parametrize(
    "mirror,issue,code",
    [
        (True, False, "I09C_VERIFY_MIRROR_HAS_NO_ORPHAN_CODE"),
        (False, True, "I09C_VERIFY_MIRROR_HAS_EVERY_ISSUE"),
    ],
)
def test_a_future_issue_code_still_needs_both_sides(tmp_path, mirror, issue, code):
    coordinator, database = _guarded(tmp_path, "future-half.db")
    connection = _connect(database)
    try:
        _add_future_issue(connection, mirror=mirror, issue=issue)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match=code):
        _verify_0005(coordinator, database)


# ------------------------------------------------ every evidence owns an issue


def test_exact_evidence_without_its_issue_fails_verification(tmp_path):
    """The reviewer's second diagnostic: a shortfall nobody is told about."""
    coordinator, database = _guarded(tmp_path, "orphan-evidence.db")
    connection = _connect(database)
    try:
        item_id = _seed_offline_order(connection, 2, "TON_AM", quantity=4)
        connection.execute(
            """INSERT INTO offline_stock_deficits
               (id, order_item_id, product_id, deficit_quantity,
                remaining_quantity, state_version)
               VALUES (2, ?, 1, 4, 4, 0)""",
            (item_id,),
        )
        # Order 2 carries the mirror code, so only the evidence link is missing.
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, issue_code, evidence_kind, severity, state, opened_at,
                state_version)
               VALUES (2, 'TON_AM', 'LEGACY_AMBIGUOUS', 'ACTION', 'OPEN', ?, 0)""",
            (TS,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I09C_VERIFY_EVIDENCE_HAS_ISSUE"):
        _verify_0005(coordinator, database)


def test_tracked_evidence_without_its_issue_fails_verification(tmp_path):
    coordinator, database = _guarded(tmp_path, "orphan-tracked.db")
    connection = _connect(database)
    try:
        item_id = _seed_offline_order(
            connection, 2, "TON_AM", product_id=2, quantity=3
        )
        connection.execute("UPDATE products SET stock = -3 WHERE id = 2")
        connection.execute(
            """INSERT INTO offline_batch_stock_deficits
               (id, order_item_id, product_id, deficit_quantity,
                remaining_quantity, resolution_kind, state_version)
               VALUES (9, ?, 2, 3, 3, NULL, 0)""",
            (item_id,),
        )
        connection.execute(
            """INSERT INTO offline_receipt_issues
               (order_id, issue_code, evidence_kind, severity, state, opened_at,
                state_version)
               VALUES (2, 'TON_AM', 'LEGACY_AMBIGUOUS', 'ACTION', 'OPEN', ?, 0)""",
            (TS,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I09C_VERIFY_EVIDENCE_HAS_ISSUE"):
        _verify_0005(coordinator, database)


@pytest.mark.parametrize(
    "column,value,mirror,code",
    [
        # A different code on the link is a different claim about the money, so
        # it must never stand in for the TON_AM the evidence actually needs.
        ("issue_code", "'GIA_DOI'", "TON_AM,GIA_DOI",
         "I09C_VERIFY_ISSUE_EVIDENCE_STATE"),
        ("order_item_id", "999", None, "I09C_VERIFY_EVIDENCE_HAS_ISSUE"),
        ("product_id", "2", None, "I09C_VERIFY_EVIDENCE_HAS_ISSUE"),
    ],
)
def test_a_link_that_points_somewhere_else_fails_verification(
    tmp_path, column, value, mirror, code
):
    """A wrong code or a wrong line is not a link, it is a different claim."""
    coordinator, database = _guarded(tmp_path, "wrong-link.db")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("DROP TRIGGER trg_i09c_offline_issue_update")
        connection.execute(f"UPDATE offline_receipt_issues SET {column} = {value}"
                           " WHERE id = 1")
        if mirror is not None:
            # Keep the mirror consistent so the failure proves the link check
            # itself, not a mirror drift the corruption happened to cause.
            connection.execute(
                "UPDATE orders SET offline_issue = ? WHERE id = 1", (mirror,)
            )
        for statement in _module(coordinator).DDL:
            if "_issue_update" in statement:
                connection.execute(statement)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match=code):
        _verify_0005(coordinator, database)


def test_verifier_fails_closed_when_a_guard_is_missing(tmp_path):
    coordinator, database = _guarded(tmp_path, "missing-trigger.db")
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TRIGGER trg_i09c_offline_issue_delete")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I09C_VERIFY_SCHEMA_OBJECTS"):
        _verify_0005(coordinator, database)


def test_verifier_does_not_depend_on_wall_clock(tmp_path):
    coordinator, _database = _guarded(tmp_path, "clock.db")
    source = (PROJECT_ROOT / f"migrations/versions/{I09C}.py").read_text(
        encoding="utf-8"
    )
    assert "CURRENT_TIMESTAMP" not in source
    assert "datetime(" not in source
    coordinator.verify()
