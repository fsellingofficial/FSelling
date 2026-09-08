"""Forward-only Plan 3 approval linkage schema."""

import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


ROOT = Path(__file__).resolve().parents[1]
PLAN2 = "0012_fnb_ticket_service_handoff"
PLAN3 = "0013_roles_returns_approval_r3"
FINGERPRINT = "a" * 64


def _runner(path, *, cancel_check=None):
    return MigrationCoordinator(
        path,
        project_root=ROOT,
        inventory_provider=StaticInventory(),
        cancel_check=cancel_check,
    )


def _database_at_plan2(path):
    runner = _runner(path)
    runner.init()
    runner.upgrade(PLAN2)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO users "
            "(id, username, hashed_password, role, is_verified, is_active, "
            "failed_login_count, verification_attempts, staff_role) "
            "VALUES (?, ?, 'x', ?, 1, 1, 0, 0, ?)",
            [
                (1, "plan3-owner", "SELLER", None),
                (2, "plan3-cashier", "STAFF", "CASHIER"),
            ],
        )
        connection.executemany(
            "INSERT INTO shops (id, name, is_active, owner_id) VALUES (?, ?, 1, 1)",
            [(1, "Plan 3 shop"), (2, "Other shop")],
        )
        connection.executemany(
            "INSERT INTO orders "
            "(id, shop_id, payment_method, status, cash_paid_amount, "
            "refunded_amount, refund_due_amount, loyalty_points_redeemed, "
            "loyalty_discount_amount, loyalty_points_earned) "
            "VALUES (?, ?, 'cash', 'PAID', 100, 0, 0, 0, 0, 0)",
            [(1, 1), (2, 1), (3, 2)],
        )
        connection.execute(
            "INSERT INTO fnb_manager_approvals "
            "(id, shop_id, approver_user_id, actor_user_id, action, entity_type, "
            "entity_id, revision, token_hash, expires_at) "
            "VALUES (1, 1, 1, 2, 'CANCEL_SENT_LINE', 'SESSION', 1, 0, ?, "
            "'2026-09-08 12:00:00')",
            ("b" * 64,),
        )
        connection.execute(
            "INSERT INTO order_returns "
            "(id, order_id, shop_id, refund_amount, created_by_user_id, created_at, "
            "loyalty_points_restored, loyalty_points_reversed) "
            "VALUES (1, 1, 1, 100, 2, '2026-09-08 08:00:00', 0, 0)"
        )
        connection.commit()
    return runner


def _insert_valid_return_approval(connection):
    connection.execute(
        "INSERT INTO fnb_manager_approvals "
        "(id, shop_id, approver_user_id, actor_user_id, action, entity_type, "
        "entity_id, revision, token_hash, expires_at, context_fingerprint) "
        "VALUES (2, 1, 1, 2, 'ORDER_RETURN_EXCEPTION', 'ORDER', 1, 0, ?, "
        "'2026-09-08 12:00:00', ?)",
        ("c" * 64, FINGERPRINT),
    )
    connection.execute(
        "UPDATE order_returns SET manager_approval_id=2 WHERE id=1"
    )
    connection.commit()


def test_0012_to_0013_adds_nullable_links_without_rewriting_legacy_rows(tmp_path):
    database = tmp_path / "plan3.db"
    runner = _database_at_plan2(database)

    assert runner.upgrade("head") == [PLAN3]
    runner.verify()

    with sqlite3.connect(database) as connection:
        approval_columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(fnb_manager_approvals)"
            )
        }
        return_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(order_returns)")
        }
        return_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(order_returns)"
        ).fetchall()
        approval = connection.execute(
            "SELECT action, context_fingerprint FROM fnb_manager_approvals WHERE id=1"
        ).fetchone()
        returned = connection.execute(
            "SELECT refund_amount, manager_approval_id FROM order_returns WHERE id=1"
        ).fetchone()

    assert approval_columns["context_fingerprint"][2:4] == ("VARCHAR(64)", 0)
    assert return_columns["manager_approval_id"][2:4] == ("INTEGER", 0)
    assert any(
        row[2:5] == ("fnb_manager_approvals", "manager_approval_id", "id")
        for row in return_foreign_keys
    )
    assert approval == ("CANCEL_SENT_LINE", None)
    assert returned == (100.0, None)


@pytest.mark.parametrize("fingerprint", ["A" * 64, "a" * 63, "g" * 64])
def test_0013_verifier_rejects_non_lowercase_hex_fingerprint(tmp_path, fingerprint):
    database = tmp_path / "plan3-bad-fingerprint.db"
    runner = _database_at_plan2(database)
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE fnb_manager_approvals SET context_fingerprint=? WHERE id=1",
            (fingerprint,),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="PLAN3_VERIFY_APPROVAL_FINGERPRINT"):
        runner.verify()


def test_0013_verifier_rejects_orphan_return_approval(tmp_path):
    database = tmp_path / "plan3-orphan.db"
    runner = _database_at_plan2(database)
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE order_returns SET manager_approval_id=999 WHERE id=1"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="PLAN3_VERIFY_RETURN_APPROVAL_LINK"):
        runner.verify()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE fnb_manager_approvals SET shop_id=2 WHERE id=2",
        "UPDATE fnb_manager_approvals SET entity_type='SESSION' WHERE id=2",
        "UPDATE fnb_manager_approvals SET entity_id=2 WHERE id=2",
        "UPDATE fnb_manager_approvals SET action='CANCEL_SENT_LINE' WHERE id=2",
    ],
)
def test_0013_verifier_rejects_mismatched_return_approval(tmp_path, mutation):
    database = tmp_path / "plan3-mismatch.db"
    runner = _database_at_plan2(database)
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        _insert_valid_return_approval(connection)
        connection.execute(mutation)
        connection.commit()

    with pytest.raises(RuntimeError, match="PLAN3_VERIFY_RETURN_APPROVAL_LINK"):
        runner.verify()


def test_0013_upgrade_rolls_back_first_column_and_version_on_fault(tmp_path):
    database = tmp_path / "plan3-rollback.db"
    _database_at_plan2(database)
    calls = 0

    def cancel_after_first_statement():
        nonlocal calls
        calls += 1
        return calls == 2

    with pytest.raises(Exception, match="cancelled before commit"):
        _runner(database, cancel_check=cancel_after_first_statement).upgrade("head")

    with sqlite3.connect(database) as connection:
        approval_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_manager_approvals)")
        }
        return_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(order_returns)")
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert "context_fingerprint" not in approval_columns
    assert "manager_approval_id" not in return_columns
    assert version == (PLAN2,)


def test_0013_models_expose_nullable_approval_linkage():
    from fselling import models

    assert models.FnbManagerApproval.context_fingerprint is not None
    assert models.OrderReturn.manager_approval_id is not None
    assert models.OrderReturn.manager_approval is not None


def test_0013_downgrade_is_explicitly_forward_only(tmp_path):
    database = tmp_path / "plan3-forward-only.db"
    runner = _database_at_plan2(database)
    runner.upgrade("head")
    spec = runner._graph().head

    with pytest.raises(RuntimeError, match="FORWARD_ONLY_MIGRATION"):
        spec.module.downgrade()
