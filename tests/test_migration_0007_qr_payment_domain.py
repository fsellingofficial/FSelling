"""I10-A migration 0007: immutable sales-QR and bank-evidence domain."""
from __future__ import annotations

import ast
import hashlib
import json
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
PLAN3 = "0013_roles_returns_approval_r3"
PLAN4 = "0014_session_device_safety_r4"
HEAD_PATH = [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]

RELEASED_0001_TO_0006 = {
    ROOT: "5bdcb5e297ba9eba83474c5415371129c9c3d1498280ee481e37c27fd37e7a5c",
    I04: "811595d51abd3ba12d1dd10ab9602960766567310281fabd00a042be4bdb797b",
    I05: "d0abe1f5df1d729678dbd258e9ff40848588437c26e3d05c8b88a8e47d492539",
    I09: "572c969914b6517f84a2b5a2bd9c3477e79f9deb88f50fe2c77b3384d487d506",
    I09C: "c4ffcb31bb6f50786ee228cead5202e716abc50eebac93dfba4813de457c4716",
    I09E: "086e1c747b27923cf274773d8ab041f52e44a9e81d8efc569d0af1ce1971f104",
}
CONTROL = "1be2c54a0e8ccce8c35e61509eeb142047fc91ca42f4704033e179e553c26b62"
TIME_0 = "2026-08-01 05:00:00.000000"
TIME_1 = "2026-08-01 05:01:00.000000"
TIME_2 = "2026-08-01 05:02:00.000000"


def _coordinator(path: Path, **kwargs) -> MigrationCoordinator:
    return MigrationCoordinator(
        path,
        project_root=PROJECT_ROOT,
        inventory_provider=StaticInventory(),
        **kwargs,
    )


def _connect(path: Path, *, foreign_keys: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return connection


def _module(coordinator: MigrationCoordinator):
    return next(
        item.module
        for item in coordinator._graph().revisions
        if item.revision == I10A
    )


def _at_0006(path: Path, **kwargs) -> MigrationCoordinator:
    coordinator = _coordinator(path, **kwargs)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I09E) == HEAD_PATH[:5]
    return coordinator


def _at_head(path: Path, *, seed: bool = False) -> MigrationCoordinator:
    coordinator = _coordinator(path)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == HEAD_PATH
    if seed:
        with _connect(path) as connection:
            _seed_business_rows(connection)
    return coordinator


def _normalized_digest(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _seed_business_rows(connection: sqlite3.Connection) -> None:
    """Two shops and online/offline/cash orders, all valid under I05."""
    for user_id, username, role in (
        (1, "owner-one", "SELLER"),
        (2, "admin", "ADMIN"),
        (5, "owner-two", "SELLER"),
    ):
        connection.execute(
            """INSERT INTO users
               (id,username,hashed_password,role,is_verified,is_active,
                failed_login_count,verification_attempts)
               VALUES (?,?,'x',?,1,1,0,0)""",
            (user_id, username, role),
        )
    connection.execute(
        """INSERT INTO shops
           (id,name,bank_account_no,bank_account_name,bank_code,is_active,owner_id)
           VALUES (1,'Shop One','001122','NGUYEN A','VCB',1,1)"""
    )
    connection.execute(
        """INSERT INTO shops
           (id,name,bank_account_no,bank_account_name,bank_code,is_active,owner_id)
           VALUES (2,'Shop Two','998877','SHOP TWO','ACB',1,5)"""
    )
    for user_id, username, shop_id in (
        (3, "manager-one", 1),
        (4, "manager-two", 2),
    ):
        connection.execute(
            """INSERT INTO users
               (id,username,hashed_password,role,is_verified,is_active,
                failed_login_count,verification_attempts,staff_shop_id,staff_role)
               VALUES (?,?,'x','STAFF',1,1,0,0,?,'MANAGER')""",
            (user_id, username, shop_id),
        )
    for product_id, shop_id in ((1, 1), (2, 2)):
        connection.execute(
            """INSERT INTO products
               (id,code,name,price,price_vnd,stock,is_active,shop_id,track_batches,
                cost_known_qty,cost_unknown_qty,cost_basis_vnd,cost_deficit_qty,
                cost_state_version)
               VALUES (?,?,?,100,100,0,1,?,0,0,0,0,0,0)""",
            (product_id, f"SP-{product_id}", f"Product {product_id}", shop_id),
        )

    orders = (
        (1, 1, 100, "transfer", None),
        (2, 1, 100, "cash", None),
        (3, 1, 100, "transfer", "offline-order-3"),
        (4, 2, 100, "transfer", None),
        (5, 1, 200, "transfer", None),
        (6, 1, 0, "transfer", None),
    )
    for order_id, shop_id, total, method, offline_uuid in orders:
        connection.execute(
            """INSERT INTO orders
               (id,shop_id,total_amount,discount_amount,payment_method,status,
                created_at,cash_paid_amount,refunded_amount,refund_due_amount,
                offline_uuid,loyalty_points_redeemed,loyalty_discount_amount,
                loyalty_points_earned,total_vnd,discount_vnd,cash_paid_vnd,
                refunded_vnd,refund_due_vnd,loyalty_discount_vnd,
                inventory_reversed,inventory_reversal_version)
               VALUES (?,?,?,?,?,'PENDING','2026-08-01 04:00:00',0,0,0,?,
                       0,0,0,?,0,0,0,0,0,0,0)""",
            (order_id, shop_id, float(total), 0.0, method, offline_uuid, total),
        )
        if total:
            product_id = 2 if shop_id == 2 else 1
            connection.execute(
                """INSERT INTO order_items
                   (id,order_id,product_id,product_name,price,quantity,
                    unit_price_vnd,discount_vnd,loyalty_discount_vnd,net_amount_vnd,
                    cost_known_qty,cost_unknown_qty,cost_basis_vnd,
                    returned_total_qty,returned_known_qty,returned_unknown_qty,
                    returned_cost_basis_vnd,returned_refund_vnd,cost_return_version,
                    inventory_reversed,inventory_reversal_version)
                   VALUES (?,?,?,?,?,1,?,0,0,?,0,1,0,0,0,0,0,0,0,0,0)""",
                (
                    order_id,
                    order_id,
                    product_id,
                    f"Order line {order_id}",
                    float(total),
                    total,
                    total,
                ),
            )


def _insert_intent(
    connection: sqlite3.Connection,
    *,
    intent_id: int = 1,
    order_id: int = 1,
    shop_id: int = 1,
    reference: str = "SALE.0001",
    expected_vnd: object = 100,
    bank_code: str = "VCB",
    account_no: str = "001122",
    account_name: str = "NGUYEN A",
    adapter_profile_id: str = "mock.v1",
    issued_at: str = TIME_0,
    display_expires_at: str | None = None,
    cancel_after: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO qr_payment_intents
           (id,contract_version,order_id,shop_id,canonical_reference,expected_vnd,
            bank_code,account_no,account_name,adapter_profile_id,issued_at,
            display_expires_at,cancel_after)
           VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            intent_id,
            order_id,
            shop_id,
            reference,
            expected_vnd,
            bank_code,
            account_no,
            account_name,
            adapter_profile_id,
            issued_at,
            display_expires_at,
            cancel_after,
        ),
    )


def _digest(seed: int) -> str:
    return hashlib.sha256(f"i10a-test-{seed}".encode()).hexdigest()


def _insert_event(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    provider: str = "mock_bank",
    provider_event_id: str | None = None,
    idempotency_key: str | None = None,
    normalized_account_no: str | None = "001122",
    direction: str = "IN",
    amount_vnd: object = 100,
    reference_state: str = "EXACT",
    normalized_reference: str | None = "SALE.0001",
    normalized_sha256: str | None = None,
    envelope_sha256: str | None = None,
    intent_id: int | None = None,
    order_id: int | None = None,
    shop_id: int | None = None,
    reason_code: str = "UNMATCHED",
    received_at: str = TIME_0,
    updated_at: str = TIME_0,
) -> None:
    connection.execute(
        """INSERT INTO bank_webhook_events
           (id,provider,provider_event_id,idempotency_key,normalized_account_no,
            direction,amount_vnd,reference_state,normalized_reference,
            normalized_sha256,envelope_sha256,intent_id,order_id,shop_id,
            payment_id,disposition,reason_code,received_at,updated_at,state_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,'UNAPPLIED',?,?,?,0)""",
        (
            event_id,
            provider,
            provider_event_id or f"event-{event_id}",
            idempotency_key or _digest(event_id),
            normalized_account_no,
            direction,
            amount_vnd,
            reference_state,
            normalized_reference,
            normalized_sha256 or _digest(1000 + event_id),
            envelope_sha256 or _digest(2000 + event_id),
            intent_id,
            order_id,
            shop_id,
            reason_code,
            received_at,
            updated_at,
        ),
    )


def _insert_payment(
    connection: sqlite3.Connection,
    *,
    payment_id: int,
    event_id: int,
    order_id: int = 1,
    amount_vnd: int = 100,
) -> None:
    row = connection.execute(
        """SELECT provider,provider_event_id,idempotency_key,normalized_account_no
           FROM bank_webhook_events WHERE id=?""",
        (event_id,),
    ).fetchone()
    assert row is not None
    connection.execute(
        """INSERT INTO order_payments
           (id,order_id,entry_type,amount,idempotency_key,provider,bank_txn_id,
            account_no,created_at,amount_vnd)
           VALUES (?,?,'BANK_IN',?,?,?,?,?,'2026-08-01 05:00:00',?)""",
        (payment_id, order_id, float(amount_vnd), row[2], row[0], row[1], row[3], amount_vnd),
    )


def _transition_event(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    disposition: str,
    state_version: int = 1,
    updated_at: str = TIME_1,
    intent_id: int | None = None,
    order_id: int | None = None,
    shop_id: int | None = None,
    payment_id: int | None = None,
    reason_code: str,
) -> None:
    connection.execute(
        """UPDATE bank_webhook_events
           SET disposition=?,state_version=?,updated_at=?,intent_id=?,order_id=?,
               shop_id=?,payment_id=?,reason_code=? WHERE id=?""",
        (
            disposition,
            state_version,
            updated_at,
            intent_id,
            order_id,
            shop_id,
            payment_id,
            reason_code,
            event_id,
        ),
    )


def _insert_log(
    connection: sqlite3.Connection,
    *,
    log_id: int,
    user_id: int,
    shop_id: int | None,
) -> None:
    connection.execute(
        """INSERT INTO system_logs
           (id,user_id,shop_id,action,details,created_at)
           VALUES (?, ?, ?, 'BANK_RECONCILIATION', 'normalized evidence decision', ?)""",
        (log_id, user_id, shop_id, TIME_2),
    )


def _insert_action(
    connection: sqlite3.Connection,
    *,
    action_id: int,
    event_id: int,
    event_state_version: int,
    action_kind: str,
    performed_by_user_id: int,
    actor_role: str,
    system_log_id: int,
    intent_id: int | None = None,
    order_id: int | None = None,
    shop_id: int | None = None,
    payment_id: int | None = None,
    note: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO bank_reconciliation_actions
           (id,event_id,event_state_version,action_kind,intent_id,order_id,shop_id,
            payment_id,performed_by_user_id,actor_role,note,performed_at,system_log_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            action_id,
            event_id,
            event_state_version,
            action_kind,
            intent_id,
            order_id,
            shop_id,
            payment_id,
            performed_by_user_id,
            actor_role,
            note,
            TIME_2,
            system_log_id,
        ),
    )


def _seed_valid_domain(connection: sqlite3.Connection) -> None:
    """All three terminal outcomes, each atomically driven by its audit action."""
    _seed_business_rows(connection)
    _insert_intent(connection)

    _insert_event(connection, event_id=1, intent_id=1, order_id=1, shop_id=1)
    _insert_payment(connection, payment_id=1, event_id=1)
    _insert_log(connection, log_id=1, user_id=1, shop_id=1)
    _insert_action(
        connection,
        action_id=1,
        event_id=1,
        event_state_version=1,
        action_kind="MAP_AND_APPLY",
        intent_id=1,
        order_id=1,
        shop_id=1,
        payment_id=1,
        performed_by_user_id=1,
        actor_role="OWNER",
        system_log_id=1,
    )

    _insert_event(
        connection,
        event_id=2,
        normalized_account_no=None,
        direction="UNKNOWN",
        amount_vnd=None,
        reference_state="MISSING",
        normalized_reference=None,
        reason_code="MISSING_REFERENCE",
    )
    _insert_log(connection, log_id=2, user_id=2, shop_id=None)
    _insert_action(
        connection,
        action_id=2,
        event_id=2,
        event_state_version=1,
        action_kind="REJECT_NOT_OURS",
        performed_by_user_id=2,
        actor_role="ADMIN",
        system_log_id=2,
        note="Confirmed unrelated external transfer",
    )

    _insert_event(
        connection,
        event_id=3,
        intent_id=1,
        order_id=1,
        shop_id=1,
        reason_code="EXTERNAL_REFUND",
    )
    _insert_log(connection, log_id=3, user_id=1, shop_id=1)
    _insert_action(
        connection,
        action_id=3,
        event_id=3,
        event_state_version=1,
        action_kind="MARK_REFUNDED_EXTERNALLY",
        intent_id=1,
        order_id=1,
        shop_id=1,
        performed_by_user_id=1,
        actor_role="OWNER",
        system_log_id=3,
        note="Refund confirmed outside F-Selling",
    )


def test_fresh_0001_to_0007_restart_exact_shape_and_no_raw_body(tmp_path):
    database = tmp_path / "fresh.db"
    coordinator = _at_head(database)
    report = coordinator.verify()
    assert report.current_revision == report.head_revision == PLAN4
    assert report.revision_count == 14
    assert coordinator.upgrade("head") == []
    assert coordinator.verify().database_uuid == report.database_uuid

    module = _module(coordinator)
    with _connect(database) as connection:
        for table, expected in module.EXPECTED_COLUMNS.items():
            columns = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
                for row in connection.execute(f"PRAGMA table_info('{table}')")
            )
            assert columns == expected
            lowered = {name.lower() for name, *_rest in columns}
            assert not any("raw" in name or "body" in name or "payload" in name for name in lowered)
        assert connection.execute("SELECT COUNT(*) FROM qr_payment_intents").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM bank_webhook_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM bank_reconciliation_actions").fetchone() == (0,)
        module.verify(connection)

    from fselling.models import (  # noqa: PLC0415 - proves released registry mapping
        BankReconciliationAction,
        BankWebhookEvent,
        QrPaymentIntent,
    )

    assert QrPaymentIntent.__tablename__ == "qr_payment_intents"
    assert BankWebhookEvent.__tablename__ == "bank_webhook_events"
    assert BankReconciliationAction.__tablename__ == "bank_reconciliation_actions"
    assert "ux_i10a_orders_id_shop" in {
        item.name for item in QrPaymentIntent.metadata.tables["orders"].indexes
    }
    assert "ux_i10a_order_payments_id_order" in {
        item.name
        for item in QrPaymentIntent.metadata.tables["order_payments"].indexes
    }


def test_0006_to_0007_is_ddl_only_and_never_synthesizes_legacy_v0(tmp_path):
    database = tmp_path / "upgrade.db"
    coordinator = _at_0006(database)
    with _connect(database) as connection:
        _seed_business_rows(connection)
        connection.execute(
            """INSERT INTO order_payments
               (id,order_id,entry_type,amount,idempotency_key,provider,bank_txn_id,
                account_no,note,reference,created_at,amount_vnd)
               VALUES (90,1,'BANK_IN',100,'legacy-key','legacy','legacy-txn',
                       '001122','legacy evidence','ORDER1','2026-07-01 00:00:00',100)"""
        )
        before = connection.execute(
            """SELECT o.id,o.total_vnd,o.bank_txn_id,s.bank_account_no,p.reference
               FROM orders o JOIN shops s ON s.id=o.shop_id
               JOIN order_payments p ON p.order_id=o.id WHERE o.id=1"""
        ).fetchone()

    assert coordinator.upgrade("head") == [I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3, PLAN4]
    with _connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM qr_payment_intents").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM bank_webhook_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM bank_reconciliation_actions").fetchone() == (0,)
        after = connection.execute(
            """SELECT o.id,o.total_vnd,o.bank_txn_id,s.bank_account_no,p.reference
               FROM orders o JOIN shops s ON s.id=o.shop_id
               JOIN order_payments p ON p.order_id=o.id WHERE o.id=1"""
        ).fetchone()
        assert after == before
    coordinator.verify()


def test_linear_graph_checksum_control_and_self_contained_source_are_exact():
    manifest = json.loads(
        (PROJECT_ROOT / "migrations/checksums.json").read_text(encoding="utf-8")
    )
    for revision, expected in RELEASED_0001_TO_0006.items():
        path = PROJECT_ROOT / f"migrations/versions/{revision}.py"
        assert _normalized_digest(path) == expected
        assert manifest["revisions"][revision]["sha256"] == expected
    assert CONTROL_SCHEMA_FINGERPRINT == CONTROL

    path = PROJECT_ROOT / f"migrations/versions/{I10A}.py"
    digest = _normalized_digest(path)
    assert manifest["revisions"][I10A] == {
        "down_revision": I09E,
        "path": f"versions/{I10A}.py",
        "sha256": digest,
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1
    assert isinstance(imports[0], ast.ImportFrom)
    assert imports[0].module == "alembic"
    source = path.read_text(encoding="utf-8").lower()
    assert "current_timestamp" not in source
    assert "datetime('now" not in source
    assert "strftime('%" not in source
    assert "time.time" not in source


@pytest.mark.parametrize("stage", ["after_ddl", "after_journal", "after_verify"])
def test_0007_rolls_back_as_one_unit_under_fault_injection(tmp_path, stage):
    database = tmp_path / f"rollback-{stage}.db"

    def fail(selected, revision):
        if selected == stage and revision == I10A:
            raise RuntimeError(stage)

    coordinator = _at_0006(database, fault_hook=fail)
    with pytest.raises(RuntimeError, match=stage):
        coordinator.upgrade("head")
    with _connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (I09E,)
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%i10a%' "
                "OR name IN ('qr_payment_intents','bank_webhook_events',"
                "'bank_reconciliation_actions')"
            )
        }
        assert names == set()
        assert connection.execute(
            "SELECT COUNT(*) FROM fs_migration_revision_journal WHERE revision=?",
            (I10A,),
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"order_id": 2}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"order_id": 3}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"order_id": 6, "expected_vnd": 0}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"expected_vnd": 101}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"expected_vnd": 100.5}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"shop_id": 2}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"bank_code": "ACB"}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"account_no": "998877"}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"account_name": "OTHER"}, "I10A_QR_INTENT_ORDER_SNAPSHOT"),
        ({"reference": " sale 1 "}, "CHECK constraint failed"),
        ({"reference": "sale.0001"}, "CHECK constraint failed"),
        ({"issued_at": "2026-02-29 05:00:00.000000"}, "CHECK constraint failed"),
        ({"issued_at": "2026-08-01T05:00:00Z"}, "CHECK constraint failed"),
        (
            {"display_expires_at": "2026-08-01 04:59:59.999999"},
            "CHECK constraint failed",
        ),
        (
            {
                "display_expires_at": TIME_2,
                "cancel_after": TIME_1,
            },
            "CHECK constraint failed",
        ),
    ],
    ids=[
        "cash",
        "offline",
        "zero",
        "total-mismatch",
        "non-integer-vnd",
        "cross-shop",
        "bank-code-snapshot",
        "account-snapshot",
        "account-name-snapshot",
        "reference-whitespace",
        "reference-case",
        "invalid-calendar-day",
        "timezone-format",
        "expiry-before-issue",
        "cancel-before-display-expiry",
    ],
)
def test_intent_insert_contract_fails_closed(tmp_path, kwargs, match):
    database = tmp_path / "intent-invalid.db"
    _at_head(database, seed=True)
    with _connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match=match):
        _insert_intent(connection, **kwargs)


def test_one_intent_per_order_reference_global_and_core_is_immutable(tmp_path):
    database = tmp_path / "intent-immutable.db"
    coordinator = _at_head(database, seed=True)
    module = _module(coordinator)
    with _connect(database) as connection:
        _insert_intent(
            connection,
            display_expires_at=TIME_1,
            cancel_after=TIME_2,
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_intent(connection, intent_id=2, reference="SALE.0002")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_intent(
                connection,
                intent_id=2,
                order_id=5,
                expected_vnd=200,
                reference="SALE.0001",
            )
        for statement, code in (
            ("UPDATE qr_payment_intents SET expected_vnd=101 WHERE id=1", "IMMUTABLE"),
            ("DELETE FROM qr_payment_intents WHERE id=1", "IMMUTABLE"),
            ("UPDATE orders SET total_vnd=101 WHERE id=1", "ORDER_IMMUTABLE"),
            ("UPDATE orders SET payment_method='cash' WHERE id=1", "ORDER_IMMUTABLE"),
            ("UPDATE orders SET offline_uuid='late' WHERE id=1", "ORDER_IMMUTABLE"),
            ("DELETE FROM orders WHERE id=1", "LINKED_ORDER_IMMUTABLE"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=code):
                connection.execute(statement)

        # Account-change locking is I10-C.  The v1 instruction remains bound to
        # its immutable snapshot and is not rewritten from current shop state.
        connection.execute(
            """UPDATE shops SET bank_code='TCB',bank_account_no='777',
               bank_account_name='NEW ACCOUNT' WHERE id=1"""
        )
        assert connection.execute(
            "SELECT bank_code,account_no,account_name FROM qr_payment_intents WHERE id=1"
        ).fetchone() == ("VCB", "001122", "NGUYEN A")
        module.verify(connection)


def test_owned_parent_guards_hold_with_runtime_foreign_keys_off(tmp_path):
    database = tmp_path / "parent-guards-fk-off.db"
    coordinator = _at_head(database)
    module = _module(coordinator)
    with _connect(database, foreign_keys=False) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        _seed_valid_domain(connection)

        for statement, code in (
            ("UPDATE orders SET id=101 WHERE id=1", "I10A_.*ORDER_IMMUTABLE"),
            ("UPDATE orders SET payment_method='cash' WHERE id=1", "I10A_.*ORDER_IMMUTABLE"),
            ("DELETE FROM orders WHERE id=1", "I10A_LINKED_ORDER_IMMUTABLE"),
            ("UPDATE shops SET id=101 WHERE id=1", "I10A_LINKED_SHOP_IMMUTABLE"),
            ("DELETE FROM shops WHERE id=1", "I10A_LINKED_SHOP_IMMUTABLE"),
            ("UPDATE system_logs SET id=101 WHERE id=1", "I10A_LINKED_AUDIT_IMMUTABLE"),
            ("UPDATE system_logs SET details='changed' WHERE id=1", "I10A_LINKED_AUDIT_IMMUTABLE"),
            ("DELETE FROM system_logs WHERE id=1", "I10A_LINKED_AUDIT_IMMUTABLE"),
            ("UPDATE users SET id=101 WHERE id=1", "I10A_LINKED_ACTOR_IMMUTABLE"),
            ("DELETE FROM users WHERE id=1", "I10A_LINKED_ACTOR_IMMUTABLE"),
            ("UPDATE order_payments SET id=101 WHERE id=1", "I10A_LINKED_PAYMENT_IMMUTABLE"),
            ("DELETE FROM order_payments WHERE id=1", "I10A_LINKED_PAYMENT_IMMUTABLE"),
            ("UPDATE bank_webhook_events SET id=101 WHERE id=1", "I10A_BANK_EVENT_TRANSITION"),
            ("DELETE FROM bank_webhook_events WHERE id=1", "I10A_BANK_EVENT_IMMUTABLE"),
            ("UPDATE qr_payment_intents SET id=101 WHERE id=1", "I10A_QR_INTENT_IMMUTABLE"),
            ("DELETE FROM qr_payment_intents WHERE id=1", "I10A_QR_INTENT_IMMUTABLE"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=code):
                connection.execute(statement)

        # Conditional guards do not change legacy behavior when no 0007 row
        # owns the relationship.
        connection.execute("UPDATE orders SET id=20 WHERE id=2")
        connection.execute("UPDATE orders SET id=2 WHERE id=20")
        connection.execute(
            """INSERT INTO shops
               (id,name,bank_account_no,bank_account_name,bank_code,is_active,owner_id)
               VALUES (99,'Unlinked Shop',NULL,NULL,NULL,1,5)"""
        )
        connection.execute("UPDATE shops SET id=100 WHERE id=99")
        connection.execute("DELETE FROM shops WHERE id=100")
        connection.execute(
            """INSERT INTO users
               (id,username,hashed_password,role,is_verified,is_active,
                failed_login_count,verification_attempts)
               VALUES (99,'unlinked-user','x','SELLER',1,1,0,0)"""
        )
        connection.execute("UPDATE users SET id=100 WHERE id=99")
        connection.execute("DELETE FROM users WHERE id=100")
        connection.execute(
            """INSERT INTO system_logs
               (id,user_id,shop_id,action,details,created_at)
               VALUES (99,2,NULL,'OTHER_AUDIT','unlinked log',?)""",
            (TIME_2,),
        )
        connection.execute("UPDATE system_logs SET details='still unlinked' WHERE id=99")
        connection.execute("DELETE FROM system_logs WHERE id=99")
        module.verify(connection)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"idempotency_key": "A" * 64}, "CHECK constraint failed"),
        ({"idempotency_key": "a" * 63}, "CHECK constraint failed"),
        ({"normalized_sha256": "a" * 63 + "g"}, "CHECK constraint failed"),
        ({"envelope_sha256": "F" * 64}, "CHECK constraint failed"),
        ({"amount_vnd": -1}, "CHECK constraint failed"),
        ({"amount_vnd": 100.5}, "CHECK constraint failed"),
        ({"reference_state": "MISSING", "normalized_reference": "SALE.0001"}, "CHECK constraint failed"),
        ({"reference_state": "EXACT", "normalized_reference": None}, "CHECK constraint failed"),
        ({"normalized_reference": "bad ref"}, "CHECK constraint failed"),
        ({"received_at": "2026-04-31 05:00:00.000000"}, "CHECK constraint failed"),
        ({"updated_at": "2026-07-31 23:59:59.999999"}, "CHECK constraint failed"),
        ({"order_id": 4, "shop_id": 1}, "I10A_BANK_EVENT_INSERT_INVALID"),
        ({"order_id": 2, "shop_id": 1}, "I10A_BANK_EVENT_INSERT_INVALID"),
        ({"order_id": 3, "shop_id": 1}, "I10A_BANK_EVENT_INSERT_INVALID"),
        ({"order_id": 6, "shop_id": 1}, "I10A_BANK_EVENT_INSERT_INVALID"),
        ({"order_id": 999, "shop_id": 1}, "I10A_BANK_EVENT_INSERT_INVALID"),
        ({"intent_id": 1, "order_id": 4, "shop_id": 2}, "I10A_BANK_EVENT_INSERT_INVALID"),
    ],
    ids=[
        "uppercase-key",
        "short-key",
        "normalized-invalid-hex",
        "envelope-uppercase",
        "negative-vnd",
        "non-integer-vnd",
        "missing-with-reference",
        "exact-without-reference",
        "noncanonical-reference",
        "invalid-calendar-day",
        "updated-before-received",
        "cross-shop-order",
        "cash-order",
        "offline-order",
        "zero-total-order",
        "orphan-order",
        "cross-shop-intent",
    ],
)
def test_event_evidence_constraints_fail_closed(tmp_path, overrides, match):
    database = tmp_path / "event-invalid.db"
    _at_head(database, seed=True)
    with _connect(database) as connection:
        _insert_intent(connection)
        kwargs = {"event_id": 1, **overrides}
        with pytest.raises(sqlite3.IntegrityError, match=match):
            _insert_event(connection, **kwargs)


def test_durable_unapplied_evidence_collision_idempotency_and_bounded_query(tmp_path):
    database = tmp_path / "inbox.db"
    coordinator = _at_head(database, seed=True)
    module = _module(coordinator)
    with _connect(database) as connection:
        _insert_intent(connection)
        cases = (
            (1, "EXACT", "UNKNOWN.REFERENCE", "UNKNOWN_REFERENCE", 1),
            (2, "MISSING", None, "MISSING_REFERENCE", None),
            (3, "TRUNCATED", "SALE.00", "TRUNCATED_REFERENCE", 1),
            (4, "MULTIPLE", None, "MULTIPLE_REFERENCES", 1),
            (5, "INVALID", None, "INVALID_EVIDENCE", None),
        )
        for event_id, state, reference, reason, shop_id in cases:
            _insert_event(
                connection,
                event_id=event_id,
                provider_event_id="provider-collision",
                reference_state=state,
                normalized_reference=reference,
                reason_code=reason,
                shop_id=shop_id,
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_webhook_events WHERE disposition='UNAPPLIED'"
        ).fetchone() == (5,)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_webhook_events WHERE provider_event_id='provider-collision'"
        ).fetchone() == (5,)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_event(
                connection,
                event_id=6,
                provider_event_id="another-envelope-id",
                idempotency_key=_digest(1),
            )
        plan = " ".join(
            str(row)
            for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT id FROM bank_webhook_events
                   WHERE shop_id=? AND disposition='UNAPPLIED' AND received_at>=?
                   ORDER BY received_at,id LIMIT 50""",
                (1, "2026-01-01 00:00:00.000000"),
            )
        )
        assert "ix_bank_webhook_events_unapplied_shop_received" in plan
        module.verify(connection)


def test_event_core_is_immutable_and_transition_is_monotonic(tmp_path):
    database = tmp_path / "event-transition.db"
    _at_head(database, seed=True)
    with _connect(database) as connection:
        _insert_event(connection, event_id=1)
        for statement, code in (
            ("UPDATE bank_webhook_events SET amount_vnd=101 WHERE id=1", "BANK_EVENT_TRANSITION"),
            (
                "UPDATE bank_webhook_events SET id=2,state_version=1,"
                "updated_at='2026-08-01 05:01:00.000000' WHERE id=1",
                "BANK_EVENT_TRANSITION",
            ),
            ("UPDATE bank_webhook_events SET state_version=2,updated_at='2026-08-01 05:01:00.000000' WHERE id=1", "BANK_EVENT_TRANSITION"),
            ("UPDATE bank_webhook_events SET state_version=1,updated_at=received_at WHERE id=1", "BANK_EVENT_TRANSITION"),
            ("DELETE FROM bank_webhook_events WHERE id=1", "BANK_EVENT_IMMUTABLE"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=code):
                connection.execute(statement)

        # Reviewer repro: an unmapped event cannot be made terminal directly,
        # even when the UPDATE itself otherwise has a valid monotonic shape.
        with pytest.raises(sqlite3.IntegrityError, match="BANK_EVENT_TRANSITION"):
            _transition_event(
                connection,
                event_id=1,
                disposition="REJECTED_NOT_OURS",
                reason_code="REJECT_NOT_OURS",
            )
        assert connection.execute(
            "SELECT disposition,state_version FROM bank_webhook_events WHERE id=1"
        ).fetchone() == ("UNAPPLIED", 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_reconciliation_actions WHERE event_id=1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM system_logs WHERE action='BANK_RECONCILIATION'"
        ).fetchone() == (0,)

        _insert_log(connection, log_id=1, user_id=2, shop_id=None)
        _insert_action(
            connection,
            action_id=1,
            event_id=1,
            event_state_version=1,
            action_kind="REJECT_NOT_OURS",
            performed_by_user_id=2,
            actor_role="ADMIN",
            system_log_id=1,
            note="Confirmed unrelated external transfer",
        )
        assert connection.execute(
            """SELECT disposition,reason_code,state_version,updated_at
               FROM bank_webhook_events WHERE id=1"""
        ).fetchone() == ("REJECTED_NOT_OURS", "REJECT_NOT_OURS", 1, TIME_2)
        with pytest.raises(sqlite3.IntegrityError, match="BANK_EVENT_TRANSITION"):
            connection.execute(
                """UPDATE bank_webhook_events SET disposition='UNAPPLIED',
                   state_version=2,updated_at=? WHERE id=1""",
                (TIME_2,),
            )

        # Reviewer repro: MULTIPLE/unmatched evidence plus an otherwise valid
        # payment cannot be attached by a direct APPLIED UPDATE.
        _insert_intent(connection)
        _insert_event(
            connection,
            event_id=2,
            reference_state="MULTIPLE",
            normalized_reference=None,
            reason_code="AMBIGUOUS",
        )
        _insert_payment(connection, payment_id=2, event_id=2)
        with pytest.raises(sqlite3.IntegrityError, match="BANK_EVENT_TRANSITION"):
            _transition_event(
                connection,
                event_id=2,
                disposition="APPLIED",
                order_id=1,
                shop_id=1,
                payment_id=2,
                reason_code="MAP_AND_APPLY",
            )
        assert connection.execute(
            "SELECT disposition,state_version,payment_id FROM bank_webhook_events WHERE id=2"
        ).fetchone() == ("UNAPPLIED", 0, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_reconciliation_actions WHERE event_id=2"
        ).fetchone() == (0,)
        _insert_log(connection, log_id=2, user_id=1, shop_id=1)
        _insert_action(
            connection,
            action_id=2,
            event_id=2,
            event_state_version=1,
            action_kind="MAP_AND_APPLY",
            intent_id=1,
            order_id=1,
            shop_id=1,
            payment_id=2,
            performed_by_user_id=1,
            actor_role="OWNER",
            system_log_id=2,
        )
        assert connection.execute(
            "SELECT disposition,state_version,payment_id FROM bank_webhook_events WHERE id=2"
        ).fetchone() == ("APPLIED", 1, 2)

        # Validation failure rolls back both the action row and its owned
        # transition; no semantically mismatched terminal state can remain.
        _insert_event(
            connection,
            event_id=3,
            reference_state="MULTIPLE",
            normalized_reference=None,
            reason_code="AMBIGUOUS",
        )
        _insert_payment(connection, payment_id=3, event_id=3, amount_vnd=99)
        _insert_log(connection, log_id=3, user_id=1, shop_id=1)
        with pytest.raises(sqlite3.IntegrityError, match="RECON_ACTION_INVALID"):
            _insert_action(
                connection,
                action_id=3,
                event_id=3,
                event_state_version=1,
                action_kind="MAP_AND_APPLY",
                intent_id=1,
                order_id=1,
                shop_id=1,
                payment_id=3,
                performed_by_user_id=1,
                actor_role="OWNER",
                system_log_id=3,
            )
        assert connection.execute(
            "SELECT disposition,state_version,payment_id FROM bank_webhook_events WHERE id=3"
        ).fetchone() == ("UNAPPLIED", 0, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_reconciliation_actions WHERE event_id=3"
        ).fetchone() == (0,)


def test_terminal_action_insert_rolls_back_if_owned_transition_fails(tmp_path):
    database = tmp_path / "action-transition-rollback.db"
    _at_head(database, seed=True)
    with _connect(database) as connection:
        _insert_event(connection, event_id=1)
        _insert_log(connection, log_id=1, user_id=2, shop_id=None)
        connection.execute(
            """CREATE TRIGGER trg_test_block_i10a_transition
               BEFORE UPDATE ON bank_webhook_events FOR EACH ROW
               WHEN OLD.id = 1
               BEGIN SELECT RAISE(ABORT, 'TEST_TRANSITION_FAIL'); END"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="TEST_TRANSITION_FAIL"):
            _insert_action(
                connection,
                action_id=1,
                event_id=1,
                event_state_version=1,
                action_kind="REJECT_NOT_OURS",
                performed_by_user_id=2,
                actor_role="ADMIN",
                system_log_id=1,
                note="Confirmed unrelated external transfer",
            )
        assert connection.execute(
            "SELECT disposition,state_version FROM bank_webhook_events WHERE id=1"
        ).fetchone() == ("UNAPPLIED", 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_reconciliation_actions WHERE event_id=1"
        ).fetchone() == (0,)


def test_all_four_reconciliation_actions_scope_audit_and_payment_rules(tmp_path):
    database = tmp_path / "actions.db"
    coordinator = _at_head(database, seed=True)
    module = _module(coordinator)
    with _connect(database) as connection:
        _insert_intent(connection)

        # Unmapped evidence is admin-only.
        _insert_event(connection, event_id=1, shop_id=None, order_id=None, intent_id=None)
        _insert_log(connection, log_id=10, user_id=1, shop_id=None)
        with pytest.raises(sqlite3.IntegrityError, match="RECON_ACTION_INVALID"):
            _insert_action(
                connection,
                action_id=10,
                event_id=1,
                event_state_version=0,
                action_kind="KEEP_OPEN",
                performed_by_user_id=1,
                actor_role="OWNER",
                system_log_id=10,
            )
        connection.execute(
            """INSERT INTO system_logs
               (id,user_id,shop_id,action,details,created_at)
               VALUES (9,2,NULL,'OTHER_AUDIT','unrelated log',?)""",
            (TIME_2,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="RECON_ACTION_INVALID"):
            _insert_action(
                connection,
                action_id=9,
                event_id=1,
                event_state_version=0,
                action_kind="KEEP_OPEN",
                performed_by_user_id=2,
                actor_role="ADMIN",
                system_log_id=9,
            )
        _insert_log(connection, log_id=11, user_id=2, shop_id=None)
        _insert_action(
            connection,
            action_id=11,
            event_id=1,
            event_state_version=0,
            action_kind="KEEP_OPEN",
            performed_by_user_id=2,
            actor_role="ADMIN",
            system_log_id=11,
        )
        assert connection.execute(
            """SELECT disposition,state_version,payment_id,updated_at
               FROM bank_webhook_events WHERE id=1"""
        ).fetchone() == ("UNAPPLIED", 0, None, TIME_0)

        # Mapped owner and manager can keep evidence open in their own shop.
        _insert_event(connection, event_id=2, intent_id=1, order_id=1, shop_id=1)
        _insert_log(connection, log_id=12, user_id=1, shop_id=1)
        _insert_action(
            connection,
            action_id=12,
            event_id=2,
            event_state_version=0,
            action_kind="KEEP_OPEN",
            intent_id=1,
            order_id=1,
            shop_id=1,
            performed_by_user_id=1,
            actor_role="OWNER",
            system_log_id=12,
        )
        _insert_event(connection, event_id=3, intent_id=1, order_id=1, shop_id=1)
        _insert_log(connection, log_id=13, user_id=3, shop_id=1)
        _insert_action(
            connection,
            action_id=13,
            event_id=3,
            event_state_version=0,
            action_kind="KEEP_OPEN",
            intent_id=1,
            order_id=1,
            shop_id=1,
            performed_by_user_id=3,
            actor_role="MANAGER",
            system_log_id=13,
        )
        _insert_log(connection, log_id=14, user_id=4, shop_id=1)
        with pytest.raises(sqlite3.IntegrityError, match="RECON_ACTION_INVALID"):
            _insert_action(
                connection,
                action_id=14,
                event_id=3,
                event_state_version=0,
                action_kind="KEEP_OPEN",
                intent_id=1,
                order_id=1,
                shop_id=1,
                performed_by_user_id=4,
                actor_role="MANAGER",
                system_log_id=14,
            )

        # MAP_AND_APPLY is the only reconciliation action carrying a payment.
        _insert_event(
            connection,
            event_id=4,
            reference_state="MULTIPLE",
            normalized_reference=None,
            normalized_account_no=None,
            reason_code="AMBIGUOUS",
        )
        _insert_payment(connection, payment_id=4, event_id=4)
        _insert_log(connection, log_id=15, user_id=1, shop_id=1)
        _insert_action(
            connection,
            action_id=15,
            event_id=4,
            event_state_version=1,
            action_kind="MAP_AND_APPLY",
            intent_id=1,
            order_id=1,
            shop_id=1,
            payment_id=4,
            performed_by_user_id=1,
            actor_role="OWNER",
            system_log_id=15,
            note="Validated ambiguous evidence",
        )
        assert connection.execute(
            """SELECT disposition,reason_code,state_version,intent_id,order_id,
                      shop_id,payment_id,updated_at
               FROM bank_webhook_events WHERE id=4"""
        ).fetchone() == (
            "APPLIED",
            "MAP_AND_APPLY",
            1,
            1,
            1,
            1,
            4,
            TIME_2,
        )

        _insert_event(connection, event_id=5, shop_id=1, reason_code="NOT_OURS")
        _insert_log(connection, log_id=16, user_id=3, shop_id=1)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_action(
                connection,
                action_id=16,
                event_id=5,
                event_state_version=1,
                action_kind="REJECT_NOT_OURS",
                shop_id=1,
                performed_by_user_id=3,
                actor_role="MANAGER",
                system_log_id=16,
            )
        _insert_action(
            connection,
            action_id=16,
            event_id=5,
            event_state_version=1,
            action_kind="REJECT_NOT_OURS",
            shop_id=1,
            performed_by_user_id=3,
            actor_role="MANAGER",
            system_log_id=16,
            note="Confirmed not our transfer",
        )
        assert connection.execute(
            """SELECT disposition,reason_code,state_version,shop_id,payment_id
               FROM bank_webhook_events WHERE id=5"""
        ).fetchone() == ("REJECTED_NOT_OURS", "REJECT_NOT_OURS", 1, 1, None)

        _insert_event(connection, event_id=6, shop_id=1, reason_code="REFUNDED")
        _insert_log(connection, log_id=17, user_id=1, shop_id=1)
        _insert_action(
            connection,
            action_id=17,
            event_id=6,
            event_state_version=1,
            action_kind="MARK_REFUNDED_EXTERNALLY",
            shop_id=1,
            performed_by_user_id=1,
            actor_role="OWNER",
            system_log_id=17,
            note="External refund confirmed",
        )
        assert connection.execute(
            """SELECT disposition,reason_code,state_version,shop_id,payment_id
               FROM bank_webhook_events WHERE id=6"""
        ).fetchone() == (
            "REFUNDED",
            "MARK_REFUNDED_EXTERNALLY",
            1,
            1,
            None,
        )

        # A terminal event has one deterministic terminal action. A duplicate
        # insert cannot append a second action or mutate the completed event.
        _insert_log(connection, log_id=18, user_id=2, shop_id=None)
        with pytest.raises(sqlite3.IntegrityError, match="RECON_ACTION_INVALID"):
            _insert_action(
                connection,
                action_id=18,
                event_id=6,
                event_state_version=2,
                action_kind="REJECT_NOT_OURS",
                performed_by_user_id=2,
                actor_role="ADMIN",
                system_log_id=18,
                note="Duplicate terminal decision",
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_reconciliation_actions WHERE event_id=6"
        ).fetchone() == (1,)

        assert connection.execute(
            """SELECT action_kind,payment_id FROM bank_reconciliation_actions
               ORDER BY id"""
        ).fetchall() == [
            ("KEEP_OPEN", None),
            ("KEEP_OPEN", None),
            ("KEEP_OPEN", None),
            ("MAP_AND_APPLY", 4),
            ("REJECT_NOT_OURS", None),
            ("MARK_REFUNDED_EXTERNALLY", None),
        ]
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            connection.execute(
                "UPDATE bank_reconciliation_actions SET note='changed' WHERE id=11"
            )
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            connection.execute("DELETE FROM bank_reconciliation_actions WHERE id=11")
        with pytest.raises(sqlite3.IntegrityError, match="LINKED_PAYMENT_IMMUTABLE"):
            connection.execute("UPDATE order_payments SET note='changed' WHERE id=4")
        with pytest.raises(sqlite3.IntegrityError, match="LINKED_PAYMENT_IMMUTABLE"):
            connection.execute("DELETE FROM order_payments WHERE id=4")
        module.verify(connection)


@pytest.mark.parametrize(
    ("corruption", "code"),
    [
        ("intent-total", "I10A_VERIFY_INTENT_ORDER"),
        ("order-method", "I10A_VERIFY_INTENT_ORDER"),
        ("cross-shop-event", "I10A_VERIFY_EVENT_LINKS"),
        ("event-digest", "I10A_VERIFY_EVENT_SHAPE"),
        ("event-time", "I10A_VERIFY_EVENT_TIME"),
        ("applied-payment", "I10A_VERIFY_APPLIED_PAYMENT"),
        ("action-audit", "I10A_VERIFY_ACTION_SCOPE_AUDIT"),
        ("missing-terminal-action", "I10A_VERIFY_TERMINAL_ACTION"),
        ("order-id-orphan", "I10A_VERIFY_INTENT_ORDER"),
        ("shop-delete-orphan", "I10A_VERIFY_INTENT_ORDER"),
        ("audit-delete-orphan", "I10A_VERIFY_ACTION_SCOPE_AUDIT"),
        ("actor-delete-orphan", "I10A_VERIFY_ACTION_SCOPE_AUDIT"),
        ("payment-delete-orphan", "I10A_VERIFY_EVENT_LINKS"),
    ],
)
def test_independent_verifier_rejects_corrupt_rows(tmp_path, corruption, code):
    database = tmp_path / f"corrupt-{corruption}.db"
    coordinator = _at_head(database)
    module = _module(coordinator)
    with _connect(database) as connection:
        _seed_valid_domain(connection)

    with _connect(database, foreign_keys=False) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        restore_triggers = []
        if corruption == "intent-total":
            restore_triggers = ["trg_i10a_qr_intent_no_update"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("UPDATE qr_payment_intents SET expected_vnd=101 WHERE id=1")
        elif corruption == "order-method":
            restore_triggers = [
                "trg_i10a_order_intent_update_guard",
                "trg_i10a_order_link_update_guard",
            ]
            for trigger in restore_triggers:
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute("UPDATE orders SET payment_method='cash' WHERE id=1")
        elif corruption == "cross-shop-event":
            restore_triggers = ["trg_i10a_bank_event_update_guard"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute(
                "UPDATE bank_webhook_events SET order_id=4,shop_id=1 WHERE id=2"
            )
        elif corruption == "event-digest":
            restore_triggers = ["trg_i10a_bank_event_update_guard"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute(
                "UPDATE bank_webhook_events SET normalized_sha256=? WHERE id=2",
                ("a" * 63 + "g",),
            )
        elif corruption == "event-time":
            restore_triggers = ["trg_i10a_bank_event_update_guard"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute(
                "UPDATE bank_webhook_events SET received_at='2026-02-30 00:00:00.000000' WHERE id=2"
            )
        elif corruption == "applied-payment":
            restore_triggers = ["trg_i10a_linked_payment_no_update"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("UPDATE order_payments SET amount_vnd=99 WHERE id=1")
        elif corruption == "action-audit":
            connection.execute(
                """INSERT INTO system_logs
                   (id,user_id,shop_id,action,details,created_at)
                   VALUES (99,1,1,'OTHER_AUDIT','wrong action audit',?)""",
                (TIME_2,),
            )
            restore_triggers = ["trg_i10a_reconciliation_action_no_update"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute(
                "UPDATE bank_reconciliation_actions SET system_log_id=99 WHERE id=3"
            )
        elif corruption == "missing-terminal-action":
            restore_triggers = ["trg_i10a_reconciliation_action_no_delete"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("DELETE FROM bank_reconciliation_actions WHERE id=2")
        elif corruption == "order-id-orphan":
            restore_triggers = [
                "trg_i10a_order_intent_update_guard",
                "trg_i10a_order_link_update_guard",
            ]
            for trigger in restore_triggers:
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute("UPDATE orders SET id=101 WHERE id=1")
        elif corruption == "shop-delete-orphan":
            restore_triggers = ["trg_i10a_linked_shop_no_delete"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("DELETE FROM shops WHERE id=1")
        elif corruption == "audit-delete-orphan":
            restore_triggers = ["trg_i10a_linked_system_log_no_delete"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("DELETE FROM system_logs WHERE id=1")
        elif corruption == "actor-delete-orphan":
            restore_triggers = ["trg_i10a_linked_actor_no_delete"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("DELETE FROM users WHERE id=1")
        elif corruption == "payment-delete-orphan":
            restore_triggers = ["trg_i10a_linked_payment_no_delete"]
            connection.execute(f"DROP TRIGGER {restore_triggers[0]}")
            connection.execute("DELETE FROM order_payments WHERE id=1")
        else:  # pragma: no cover - param list is exhaustive
            raise AssertionError(corruption)
        assert restore_triggers
        for trigger in restore_triggers:
            connection.execute(module.EXPECTED_TRIGGER_SQL[trigger])

    with _connect(database) as connection, pytest.raises(RuntimeError, match=code):
        module.verify(connection)


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("index", "I10A_VERIFY_SCHEMA_INDEX"),
        ("trigger", "I10A_VERIFY_SCHEMA_TRIGGER"),
        ("table", "I10A_VERIFY_SCHEMA_TABLE"),
        ("table-literal-case", "I10A_VERIFY_SCHEMA_TABLE"),
        ("column-collation", "I10A_VERIFY_SCHEMA_TABLE"),
    ],
)
def test_independent_verifier_rejects_owned_schema_drift(tmp_path, kind, code):
    database = tmp_path / f"schema-{kind}.db"
    coordinator = _at_head(database)
    module = _module(coordinator)
    with _connect(database) as connection:
        if kind == "index":
            connection.execute("DROP INDEX ux_qr_payment_intents_reference")
            connection.execute(
                "CREATE INDEX ux_qr_payment_intents_reference "
                "ON qr_payment_intents (canonical_reference)"
            )
        elif kind == "trigger":
            connection.execute("DROP TRIGGER trg_i10a_qr_intent_no_update")
            connection.execute(
                """CREATE TRIGGER trg_i10a_qr_intent_no_update
                   BEFORE UPDATE ON qr_payment_intents FOR EACH ROW
                   BEGIN SELECT RAISE(ABORT, 'weakened'); END"""
            )
        else:
            connection.execute("DROP TABLE bank_reconciliation_actions")
            if kind == "table":
                replacement = "actor_role IN ('ADMIN', 'OWNER', 'MANAGER', 'CASHIER')"
                changed = module.ACTION_DDL.replace(
                    "actor_role IN ('ADMIN', 'OWNER', 'MANAGER')",
                    replacement,
                )
            elif kind == "table-literal-case":
                replacement = "actor_role IN ('admin', 'OWNER', 'MANAGER')"
                changed = module.ACTION_DDL.replace(
                    "actor_role IN ('ADMIN', 'OWNER', 'MANAGER')",
                    replacement,
                )
            else:
                changed = module.ACTION_DDL.replace(
                    "actor_role TEXT NOT NULL",
                    "actor_role TEXT NOT NULL COLLATE NOCASE",
                )
            connection.execute(changed)
    with _connect(database) as connection, pytest.raises(RuntimeError, match=code):
        module.verify(connection)


@pytest.mark.parametrize(
    "replacement",
    [
        "(provider COLLATE NOCASE, idempotency_key)",
        "(provider, idempotency_key DESC)",
        "(idempotency_key, provider)",
        "(provider, lower(idempotency_key))",
    ],
    ids=["collation", "descending", "key-order", "expression"],
)
def test_verifier_rejects_index_xinfo_semantic_drift(tmp_path, replacement):
    database = tmp_path / "index-xinfo-drift.db"
    coordinator = _at_head(database)
    module = _module(coordinator)
    with _connect(database) as connection:
        connection.execute("DROP INDEX ux_bank_webhook_events_provider_idempotency")
        connection.execute(
            "CREATE UNIQUE INDEX ux_bank_webhook_events_provider_idempotency "
            "ON bank_webhook_events " + replacement
        )
        with pytest.raises(
            RuntimeError,
            match="I10A_VERIFY_SCHEMA_INDEX:ux_bank_webhook_events_provider_idempotency",
        ):
            module.verify(connection)


def test_verifier_tolerates_following_revision_owned_columns_indexes_and_triggers(tmp_path):
    database = tmp_path / "following.db"
    coordinator = _at_head(database, seed=True)
    module = _module(coordinator)
    with _connect(database) as connection:
        _insert_intent(
            connection,
            issued_at="0001-01-01 00:00:00.000000",
            display_expires_at="9999-12-31 23:59:59.999999",
            cancel_after="9999-12-31 23:59:59.999999",
        )
        connection.execute(
            "ALTER TABLE bank_webhook_events ADD COLUMN future_note TEXT"
        )
        connection.execute(
            "CREATE INDEX ix_future_bank_event_reason "
            "ON bank_webhook_events (reason_code, id)"
        )
        connection.execute(
            """CREATE TRIGGER trg_future_bank_event_observer
               AFTER UPDATE ON bank_webhook_events FOR EACH ROW WHEN 0
               BEGIN SELECT 1; END"""
        )
        module.verify(connection)
