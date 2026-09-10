"""Forward-only F&B table-service R1A migration."""

import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FNB_R1A = "0009_fnb_table_service_r1a"
FNB_R1B = "0010_fnb_kitchen_stock_r1b"
FNB_R1C = "0011_fnb_checkout_r1c"
PLAN2 = "0012_fnb_ticket_service_handoff"
PLAN3 = "0013_roles_returns_approval_r3"
PLAN4 = "0014_session_device_safety_r4"
EXPECTED_TABLES = {
    "fnb_areas",
    "fnb_tables",
    "fnb_service_sessions",
    "fnb_session_tables",
    "fnb_session_lines",
    "fnb_action_logs",
}


def coordinator(path):
    return MigrationCoordinator(
        path, project_root=PROJECT_ROOT, inventory_provider=StaticInventory()
    )


def _seed_domain(connection):
    connection.execute("PRAGMA foreign_keys=ON")
    for user_id, username in ((1, "owner-fnb"), (2, "owner-other")):
        connection.execute(
            "INSERT INTO users "
            "(id, username, hashed_password, role, is_verified, is_active, "
            "failed_login_count, verification_attempts) "
            "VALUES (?, ?, 'hash', 'SELLER', 1, 1, 0, 0)",
            (user_id, username),
        )
    for shop_id, owner_id, name in ((1, 1, "FNB Shop"), (2, 2, "Other Shop")):
        connection.execute(
            "INSERT INTO shops "
            "(id, name, bank_account_no, bank_code, is_active, owner_id) "
            "VALUES (?, ?, '', '', 1, ?)",
            (shop_id, name, owner_id),
        )
    for product_id, shop_id in ((1, 1), (2, 2)):
        connection.execute(
            "INSERT INTO products "
            "(id, code, name, price, price_vnd, stock, is_active, shop_id, "
            "track_batches, cost_known_qty, cost_unknown_qty, cost_basis_vnd, "
            "cost_deficit_qty, cost_state_version) "
            "VALUES (?, ?, ?, 10000, 10000, 0, 1, ?, 0, 0, 0, 0, 0, 0)",
            (product_id, f"SP-{product_id}", f"Product {product_id}", shop_id),
        )
    connection.execute(
        "INSERT INTO fnb_areas (id, shop_id, name, name_key) "
        "VALUES (1, 1, 'Tang tret', 'tang tret'), (2, 2, 'San vuon', 'san vuon')"
    )
    connection.execute(
        "INSERT INTO fnb_tables (id, shop_id, area_id, name, name_key) "
        "VALUES (1, 1, 1, 'Ban 1', 'ban 1'), (2, 2, 2, 'Ban 2', 'ban 2')"
    )
    connection.execute(
        "INSERT INTO fnb_service_sessions "
        "(id, shop_id, status, revision, opened_by_user_id) "
        "VALUES (1, 1, 'OPEN', 0, 1), (2, 1, 'OPEN', 0, 1), "
        "(3, 2, 'OPEN', 0, 2)"
    )


def test_0008_to_0009_adds_schema_and_defaults_existing_shop_off(tmp_path):
    database = tmp_path / "fnb-r1a.db"
    runner = coordinator(database)
    assert runner.init() == ["0001_legacy_9cf7106_baseline"]
    runner.upgrade("0008_purchase_orders")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id, username, hashed_password, role, is_verified, "
            "is_active, failed_login_count, verification_attempts) "
            "VALUES (1, 'owner-fnb', 'hash', 'SELLER', 1, 1, 0, 0)"
        )
        connection.execute(
            "INSERT INTO shops (id, name, bank_account_no, bank_code, is_active, owner_id) "
            "VALUES (1, 'FNB Shop', '', '', 1, 1)"
        )
    assert runner.upgrade("head") == [FNB_R1A, FNB_R1B, FNB_R1C, PLAN2, PLAN3, PLAN4]
    runner.verify()
    with sqlite3.connect(database) as connection:
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert EXPECTED_TABLES <= objects
        shop_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(shops)").fetchall()
        }
        assert {"fnb_enabled", "fnb_revision"} <= shop_columns
        assert connection.execute(
            "SELECT fnb_enabled, fnb_revision FROM shops WHERE id=1"
        ).fetchone() == (0, 0)


def test_constraints_and_verifier_protect_fnb_scope(tmp_path):
    database = tmp_path / "fnb-constraints.db"
    runner = coordinator(database)
    assert runner.init() == ["0001_legacy_9cf7106_baseline"]
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        _seed_domain(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_areas (shop_id, name, name_key) "
                "VALUES (1, 'Tang khac', 'tang tret')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_tables (shop_id, area_id, name, name_key) "
                "VALUES (1, 1, 'Ban khac', 'ban 1')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_service_sessions "
                "(shop_id, status, revision, opened_by_user_id) "
                "VALUES (1, 'BROKEN', 0, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_session_lines "
                "(session_id, product_id, product_name, unit_price_vnd, quantity, "
                "cancelled_quantity, created_by_user_id) "
                "VALUES (1, 1, 'Product 1', 10000, 0, 0, 1)"
            )

        action = (
            1,
            1,
            "OPEN_SESSION",
            "operation-0001",
            "a" * 64,
            "{}",
        )
        connection.execute(
            "INSERT INTO fnb_action_logs "
            "(shop_id, actor_user_id, action, operation_id, "
            "operation_fingerprint, result_json) VALUES (?, ?, ?, ?, ?, ?)",
            action,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_action_logs "
                "(shop_id, actor_user_id, action, operation_id, "
                "operation_fingerprint, result_json) VALUES (?, ?, ?, ?, ?, ?)",
                action,
            )

        connection.execute(
            "INSERT INTO fnb_session_tables (session_id, table_id) VALUES (1, 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_session_tables (session_id, table_id) VALUES (2, 1)"
            )
        connection.execute(
            "UPDATE fnb_session_tables SET released_at=CURRENT_TIMESTAMP WHERE id=1"
        )
        connection.execute(
            "INSERT INTO fnb_session_tables (session_id, table_id) VALUES (2, 1)"
        )

        # Foreign keys alone cannot express tenant equality across two parents.
        connection.execute(
            "INSERT INTO fnb_session_tables "
            "(session_id, table_id, released_at) VALUES (1, 2, CURRENT_TIMESTAMP)"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="FNB_VERIFY_LINK_SCOPE"):
        runner.verify()


def test_verifier_rejects_tenantless_product_line(tmp_path):
    database = tmp_path / "fnb-tenantless-product.db"
    runner = coordinator(database)
    assert runner.init() == ["0001_legacy_9cf7106_baseline"]
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        _seed_domain(connection)
        connection.execute("UPDATE products SET shop_id=NULL WHERE id=1")
        connection.execute(
            "INSERT INTO fnb_session_lines "
            "(session_id, product_id, product_name, unit_price_vnd, quantity, "
            "cancelled_quantity, created_by_user_id) "
            "VALUES (1, 1, 'Product 1', 10000, 1, 0, 1)"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="FNB_VERIFY_LINE_SCOPE"):
        runner.verify()
