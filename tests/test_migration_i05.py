"""I05 migration contract tests; every database is temporary."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fselling import models
from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.errors import ChecksumError
from fselling.migration.schema import schema_fingerprint
from fselling.migration.topology import StaticInventory
from fselling.services import report_service, write_off_service


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
PLAN4 = "0014_session_device_safety_r4"


def _i05_module(coordinator: MigrationCoordinator):
    """The I05 revision is no longer head; assert against it by id."""
    return next(
        item.module
        for item in coordinator._graph().revisions
        if item.revision == I05
    )


def _coordinator(path: Path, *, project_root: Path = PROJECT_ROOT) -> MigrationCoordinator:
    return MigrationCoordinator(
        path,
        project_root=project_root,
        inventory_provider=StaticInventory(),
    )


def _at_i04(path: Path) -> MigrationCoordinator:
    coordinator = _coordinator(path)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I04) == [I04]
    return coordinator


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _insert_shop(connection: sqlite3.Connection) -> None:
    # A shop with a real owner: 0005 names an accountable principal on any
    # issue row it backfills as already resolved.
    connection.execute(
        """INSERT INTO users
           (id, username, hashed_password, role, is_verified, is_active,
            failed_login_count, verification_attempts)
           VALUES (1, 'i05-owner', 'x', 'SELLER', 1, 1, 0, 0)"""
    )
    connection.execute(
        "INSERT INTO shops (id, name, is_active, owner_id)"
        " VALUES (1, 'I05 temp shop', 1, 1)"
    )


def _insert_i04_tracked_offline_gap(
    connection: sqlite3.Connection,
    *,
    reconciled: bool = False,
    wrong_batch_product: bool = False,
    source_quantity: int = 2,
) -> None:
    _insert_shop(connection)
    connection.execute(
        """INSERT INTO products
           (id, code, name, price, stock, is_active, shop_id, track_batches)
           VALUES (1, 'OFF-I05', 'Tracked offline', 100, ?, 1, 1, 1)""",
        (0 if reconciled else -(3 - source_quantity),),
    )
    batch_product_id = 2 if wrong_batch_product else 1
    if wrong_batch_product:
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, stock, is_active, shop_id, track_batches)
               VALUES (2, 'OTHER-I05', 'Other tracked product', 100, 0, 1, 1, 1)"""
        )
    connection.execute(
        """INSERT INTO product_batches
           (id, product_id, shop_id, expiry_date, quantity, created_at)
           VALUES (1, ?, 1, '2027-01-01', 0, '2026-01-01')""",
        (batch_product_id,),
    )
    connection.execute(
        """INSERT INTO orders
           (id, shop_id, total_amount, discount_amount, payment_method, status,
            created_at, cash_paid_amount, refunded_amount, refund_due_amount,
            offline_uuid, sold_offline_at, offline_issue,
            loyalty_points_redeemed, loyalty_discount_amount,
            loyalty_points_earned)
           VALUES (1, 1, 300, 0, 'cash', 'PAID', '2026-01-01', 300, 0, 0,
                   'offline-i05-gap', '2026-01-01', 'TON_AM', 0, 0, 0)"""
    )
    connection.execute(
        """INSERT INTO order_items
           (id, order_id, product_id, product_name, price, quantity)
           VALUES (1, 1, 1, 'Tracked offline', 100, 3)"""
    )
    if source_quantity:
        connection.execute(
            """INSERT INTO order_item_batches
               (id, order_item_id, batch_id, quantity)
               VALUES (1, 1, 1, ?)""",
            (source_quantity,),
        )


def _insert_i04_legacy_write_off(connection: sqlite3.Connection) -> None:
    _insert_shop(connection)
    connection.execute(
        """INSERT INTO products
           (id, code, name, price, stock, is_active, shop_id, track_batches)
           VALUES (1, 'WO-I05', 'Legacy write-off product', 100, 4, 1, 1, 0)"""
    )
    connection.execute(
        """INSERT INTO stock_write_offs
           (id, shop_id, reason, total_quantity, idempotency_key, created_at)
           VALUES (1, 1, 'DAMAGED', 3, 'legacy-write-off', '2026-01-01')"""
    )
    connection.execute(
        """INSERT INTO stock_write_off_items
           (id, write_off_id, product_id, product_name, quantity, cost_price)
           VALUES (1, 1, 1, 'Legacy write-off product', 3, 91.25)"""
    )


def test_fresh_0001_0002_0003_and_i04_upgrade_are_linear(tmp_path):
    database = tmp_path / "fresh-linear.db"
    coordinator = _coordinator(database)

    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I04) == [I04]
    assert coordinator.status().current_revision == I04
    assert coordinator.upgrade("head") == [I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]
    report = coordinator.verify()
    assert report.current_revision == report.head_revision == PLAN4
    assert report.revision_count == 14
    assert coordinator.upgrade("head") == []

    connection = _connect(database)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PLAN4,)
        assert connection.execute(
            "SELECT type FROM pragma_table_info('orders') WHERE name='total_vnd'"
        ).fetchone() == ("INTEGER",)
    finally:
        connection.close()


def test_exact_legacy_conversion_allocation_and_unknown_cost(tmp_path):
    database = tmp_path / "exact-conversion.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_shop(connection)
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, cost_price, stock, is_active, shop_id, track_batches)
               VALUES (1, 'P-I05', 'Legacy product', 123.0, 91.25, 7, 1, 1, 0)"""
        )
        connection.execute(
            """INSERT INTO orders
               (id, shop_id, total_amount, discount_amount, payment_method, status,
                cash_paid_amount, refunded_amount, refund_due_amount,
                loyalty_points_redeemed, loyalty_discount_amount, loyalty_points_earned)
               VALUES (1, 1, 368.0, 1.0, 'cash', 'PAID', 368.0, 0.0, 0.0,
                       0, 0.0, 0)"""
        )
        for item_id in (1, 2, 3):
            connection.execute(
                """INSERT INTO order_items
                   (id, order_id, product_id, product_name, price, cost_price, quantity)
                   VALUES (?, 1, 1, 'Legacy product', 123.0, 91.25, 1)""",
                (item_id,),
            )
        connection.execute(
            """INSERT INTO vouchers
               (id, code, shop_id, discount_type, discount_value,
                min_order_value, max_discount, usage_limit, usage_count)
               VALUES (1, 'PCT', 1, 'percentage', 12.34, 10.0, 50.0, 5, 0)"""
        )
        connection.commit()
    finally:
        connection.close()

    assert coordinator.upgrade() == [I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]
    coordinator.verify()
    connection = _connect(database)
    try:
        assert connection.execute(
            """SELECT price_vnd, cost_known_qty, cost_unknown_qty,
                      cost_basis_vnd, cost_deficit_qty
               FROM products WHERE id=1"""
        ).fetchone() == (123, 0, 7, 0, 0)
        # Three equal remainders: immutable line id 1 receives the extra đồng.
        assert connection.execute(
            "SELECT id, discount_vnd, net_amount_vnd FROM order_items ORDER BY id"
        ).fetchall() == [(1, 1, 122), (2, 0, 123), (3, 0, 123)]
        assert connection.execute(
            "SELECT total_vnd, typeof(total_vnd) FROM orders WHERE id=1"
        ).fetchone() == (368, "integer")
        assert connection.execute(
            "SELECT discount_value_vnd, discount_bps, min_order_vnd, max_discount_vnd FROM vouchers"
        ).fetchone() == (None, 1234, 10, 50)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "reconciled,expected_remaining,expected_resolution,expected_version",
    [
        (False, 1, None, 0),
        (True, 0, "MIGRATION_RECONCILED", 0),
    ],
)
def test_i04_tracked_ton_am_gap_migrates_with_exact_durable_evidence(
    tmp_path,
    reconciled,
    expected_remaining,
    expected_resolution,
    expected_version,
):
    database = tmp_path / f"offline-gap-{reconciled}.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_tracked_offline_gap(connection, reconciled=reconciled)
        connection.commit()
    finally:
        connection.close()

    assert coordinator.upgrade() == [I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]
    coordinator.verify()
    connection = _connect(database)
    try:
        assert connection.execute(
            """SELECT order_item_id, product_id, deficit_quantity,
                      remaining_quantity, resolution_kind, state_version
               FROM offline_batch_stock_deficits"""
        ).fetchone() == (
            1,
            1,
            1,
            expected_remaining,
            expected_resolution,
            expected_version,
        )
        assert connection.execute(
            """SELECT quantity, cost_known_qty, cost_unknown_qty, cost_basis_vnd
               FROM order_items WHERE id=1"""
        ).fetchone() == (3, 0, 3, 0)
        assert connection.execute(
            """SELECT quantity, cost_known_qty, cost_unknown_qty, cost_basis_vnd
               FROM order_item_batches WHERE id=1"""
        ).fetchone() == (2, 0, 2, 0)
    finally:
        connection.close()


def test_i04_wrong_product_batch_source_fails_atomically(tmp_path):
    database = tmp_path / "offline-wrong-product-source.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_tracked_offline_gap(connection, wrong_batch_product=True)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="I05_SOURCE_PRODUCT_PROVENANCE"):
        coordinator.upgrade()
    connection = _connect(database)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (I04,)
        assert connection.execute(
            "SELECT 1 FROM pragma_table_info('products') WHERE name='price_vnd'"
        ).fetchone() is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "corruption,error_code",
    [
        ("missing", "I05_VERIFY_BATCH_STOCK_DEFICIT"),
        ("wrong_quantity", "I05_VERIFY_OFFLINE_BATCH_DEFICIT"),
        ("wrong_amount", "I05_VERIFY_BATCH_STOCK_DEFICIT"),
        ("wrong_direction", "I05_VERIFY_BATCH_STOCK_DEFICIT"),
    ],
)
def test_offline_deficit_verifier_rejects_unproven_or_inexact_mismatch(
    tmp_path, corruption, error_code
):
    database = tmp_path / f"offline-corrupt-{corruption}.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_tracked_offline_gap(connection)
        connection.commit()
    finally:
        connection.close()
    coordinator.upgrade()

    connection = _connect(database)
    try:
        trigger_sql = {
            name: sql
            for name, sql in connection.execute(
                """SELECT name, sql FROM sqlite_master
                   WHERE type='trigger' AND name IN
                   ('trg_i05_offline_batch_deficit_update',
                    'trg_i05_offline_batch_deficit_delete')"""
            ).fetchall()
        }
        connection.execute("DROP TRIGGER trg_i05_offline_batch_deficit_update")
        connection.execute("DROP TRIGGER trg_i05_offline_batch_deficit_delete")
        if corruption == "missing":
            connection.execute("DELETE FROM offline_batch_stock_deficits")
        elif corruption == "wrong_quantity":
            connection.execute(
                """UPDATE offline_batch_stock_deficits
                   SET deficit_quantity=2, remaining_quantity=1"""
            )
        elif corruption == "wrong_amount":
            connection.execute(
                """UPDATE offline_batch_stock_deficits
                   SET remaining_quantity=0, resolution_kind='STOCKTAKE',
                       state_version=1"""
            )
        else:
            connection.execute("UPDATE products SET stock=1 WHERE id=1")
        for sql in trigger_sql.values():
            connection.execute(sql)
        connection.commit()
        with pytest.raises(RuntimeError, match=error_code):
            _i05_module(coordinator).verify(connection)
    finally:
        connection.close()


def test_closed_offline_evidence_cannot_be_reopened_or_deleted(tmp_path):
    database = tmp_path / "offline-evidence-immutable.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_tracked_offline_gap(
            connection, reconciled=True, source_quantity=0
        )
        connection.commit()
    finally:
        connection.close()
    coordinator.upgrade()
    connection = _connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="I05_OFFLINE_DEFICIT_IMMUTABLE"):
            connection.execute(
                """UPDATE offline_batch_stock_deficits
                   SET remaining_quantity=1, resolution_kind=NULL,
                       state_version=1"""
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="I05_OFFLINE_DEFICIT_IMMUTABLE"):
            connection.execute("DELETE FROM offline_batch_stock_deficits")
    finally:
        connection.close()


def test_closed_zero_source_gap_still_requires_historical_evidence(tmp_path):
    database = tmp_path / "offline-closed-evidence-required.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_tracked_offline_gap(
            connection, reconciled=True, source_quantity=0
        )
        connection.commit()
    finally:
        connection.close()
    coordinator.upgrade()
    connection = _connect(database)
    try:
        trigger_sql = connection.execute(
            """SELECT sql FROM sqlite_master
               WHERE type='trigger'
                 AND name='trg_i05_offline_batch_deficit_delete'"""
        ).fetchone()[0]
        connection.execute("DROP TRIGGER trg_i05_offline_batch_deficit_delete")
        connection.execute("DELETE FROM offline_batch_stock_deficits")
        connection.execute(trigger_sql)
        connection.commit()
        with pytest.raises(
            RuntimeError, match="I05_VERIFY_OFFLINE_BATCH_DEFICIT_REQUIRED"
        ):
            _i05_module(coordinator).verify(connection)
    finally:
        connection.close()


def test_legacy_write_off_is_unknown_and_reported_missing_cost(tmp_path):
    database = tmp_path / "legacy-write-off-unknown.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_legacy_write_off(connection)
        connection.commit()
    finally:
        connection.close()
    coordinator.upgrade()
    coordinator.verify()

    connection = _connect(database)
    try:
        assert connection.execute(
            """SELECT cost_known_qty, cost_unknown_qty, cost_basis_vnd,
                      typeof(cost_known_qty), typeof(cost_unknown_qty),
                      typeof(cost_basis_vnd)
               FROM stock_write_off_items WHERE id=1"""
        ).fetchone() == (0, 3, 0, "integer", "integer", "integer")
    finally:
        connection.close()

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    session = sessionmaker(bind=engine)()
    try:
        write_off = session.get(models.StockWriteOff, 1)
        result = write_off_service._ket_qua(session, write_off)
        assert result["total_cost"] is None
        assert result["items"][0]["cost_price"] is None
        assert report_service._huy_hang_anh_huong_lai(
            session, 1, None, None
        ) == {
            "written_off_quantity": 3,
            "write_off_loss": 0,
            "write_offs_missing_cost": 1,
        }
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "assignment,error_code",
    [
        (
            "cost_known_qty=1, cost_unknown_qty=1",
            "I05_VERIFY_WRITE_OFF_ALLOCATION",
        ),
        (
            "cost_known_qty=0, cost_unknown_qty=3, cost_basis_vnd=1",
            "I05_VERIFY_WRITE_OFF_BASIS",
        ),
        ("cost_unknown_qty='bad'", "I05_VERIFY_QUANTITY_RANGE"),
        ("cost_basis_vnd='bad'", "I05_VERIFY_MONEY_RANGE"),
    ],
)
def test_write_off_verifier_rejects_corrupt_canonical_provenance(
    tmp_path, assignment, error_code
):
    database = tmp_path / ("write-off-corrupt-" + error_code + ".db")
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_i04_legacy_write_off(connection)
        connection.commit()
    finally:
        connection.close()
    coordinator.upgrade()
    connection = _connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE stock_write_off_items SET " + assignment + " WHERE id=1"
        )
        connection.commit()
        with pytest.raises(RuntimeError, match=error_code):
            _i05_module(coordinator).verify(connection)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "legacy_price,error_code",
    [
        ("1.5", "I05_FRACTIONAL_VND:products.price"),
        ("9000000000000001", "I05_VND_OVERFLOW:products.price"),
        ("NaN", "I05_CORRUPT_NUMERIC:products.price"),
        ("Inf", "I05_CORRUPT_NUMERIC:products.price"),
    ],
)
def test_invalid_legacy_money_fails_closed_and_rolls_back_atomically(
    tmp_path, legacy_price, error_code
):
    database = tmp_path / ("invalid-" + legacy_price.replace(".", "_") + ".db")
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_shop(connection)
        # CAST AS TEXT prevents the test fixture itself from losing precision.
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, stock, is_active, shop_id, track_batches)
               VALUES (1, 'BAD', 'Bad legacy amount', CAST(? AS TEXT), 1, 1, 1, 0)""",
            (legacy_price,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match=error_code.replace(".", r"\.")):
        coordinator.upgrade()

    connection = _connect(database)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (I04,)
        assert connection.execute(
            "SELECT 1 FROM pragma_table_info('products') WHERE name='price_vnd'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM fs_migration_revision_journal WHERE revision=?",
            (I05,),
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_i05_checksum_fingerprint_and_verifier_are_fail_closed(tmp_path):
    revision_path = PROJECT_ROOT / "migrations/versions/0003_i05_integer_vnd_cost_basis.py"
    normalized = revision_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    manifest = json.loads(
        (PROJECT_ROOT / "migrations/checksums.json").read_text(encoding="utf-8")
    )
    assert manifest["revisions"][I05]["sha256"] == digest

    database = tmp_path / "verifier.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()
    report = coordinator.verify()

    connection = _connect(database)
    try:
        before = schema_fingerprint(connection, business_only=True)
        assert before == report.business_fingerprint
        connection.execute("DROP INDEX ux_orders_inventory_reversal_key")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="I05_VERIFY_SCHEMA_OBJECTS"):
        # Call the durable revision verifier directly so this assertion remains
        # specific even though coordinator fingerprinting also fails closed.
        connection = _connect(database)
        try:
            _i05_module(coordinator).verify(connection)
        finally:
            connection.close()
    with pytest.raises(Exception, match="fingerprint mismatch"):
        coordinator.verify()


def test_nonempty_positive_ledgers_backfill_and_required_triggers_are_durable(tmp_path):
    database = tmp_path / "nonempty-ledgers.db"
    coordinator = _at_i04(database)
    connection = _connect(database)
    try:
        _insert_shop(connection)
        connection.execute(
            """INSERT INTO orders
               (id, shop_id, total_amount, discount_amount, payment_method, status,
                cash_paid_amount, refunded_amount, refund_due_amount,
                loyalty_points_redeemed, loyalty_discount_amount, loyalty_points_earned)
               VALUES (1, 1, 0, 0, 'cash', 'PAID', 0, 0, 0, 0, 0, 0)"""
        )
        connection.execute(
            """INSERT INTO order_payments
               (id, order_id, entry_type, amount, idempotency_key, created_at)
               VALUES (1, 1, 'BANK_IN', 17.0, 'i05-existing-payment', '2026-01-01')"""
        )
        connection.execute(
            """INSERT INTO cash_shifts
               (id, shop_id, status, opening_cash_amount, opened_by_user_id, opened_at)
               VALUES (1, 1, 'OPEN', 0, 1, '2026-01-01')"""
        )
        connection.execute(
            """INSERT INTO cash_movements
               (id, shift_id, movement_type, direction, amount, operation_id,
                note, created_by_user_id, created_at)
               VALUES (1, 1, 'PAY_IN', 'IN', 19.0, 'i05-existing-movement',
                       'test', 1, '2026-01-01')"""
        )
        connection.commit()
    finally:
        connection.close()


    assert coordinator.upgrade() == [I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]
    coordinator.verify()
    connection = _connect(database)
    try:
        assert connection.execute(
            "SELECT amount_vnd, typeof(amount_vnd) FROM order_payments WHERE id=1"
        ).fetchone() == (17, "integer")
        assert connection.execute(
            "SELECT amount_vnd, typeof(amount_vnd) FROM cash_movements WHERE id=1"
        ).fetchone() == (19, "integer")

        with pytest.raises(sqlite3.IntegrityError, match="I05_REQUIRED_VND"):
            connection.execute(
                """INSERT INTO order_payments
                   (order_id, entry_type, amount, idempotency_key, created_at)
                   VALUES (1, 'BANK_IN', 23, 'i05-missing-canonical', '2026-01-01')"""
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="I05_REQUIRED_VND"):
            connection.execute(
                "UPDATE cash_movements SET amount_vnd=NULL WHERE id=1"
            )
    finally:
        connection.close()


def test_durable_verifier_rejects_noninteger_canonical_storage(tmp_path):
    database = tmp_path / "corrupt-canonical-storage.db"
    coordinator = _coordinator(database)
    coordinator.init()
    coordinator.upgrade()
    connection = _connect(database)
    try:
        _insert_shop(connection)
        connection.execute(
            """INSERT INTO products
               (id, code, name, price, price_vnd, stock, is_active, shop_id,
                track_batches, cost_known_qty, cost_unknown_qty,
                cost_basis_vnd, cost_deficit_qty, cost_state_version)
               VALUES (1, 'CORRUPT-I05', 'Verifier fixture', 1, 1, 0, 1, 1,
                       0, 0, 0, 0, 0, 0)"""
        )
        connection.commit()
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE products SET price_vnd='not-an-integer' WHERE id=1")
        connection.commit()
        with pytest.raises(
            RuntimeError,
            match=r"I05_VERIFY_MONEY_RANGE:products\.price_vnd",
        ):
            _i05_module(coordinator).verify(connection)
    finally:
        connection.close()
