"""Forward-only F&B Kitchen/Bar and stock-provenance schema."""

import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


ROOT = Path(__file__).resolve().parents[1]
R1B = "0010_fnb_kitchen_stock_r1b"
R1C = "0011_fnb_checkout_r1c"
PLAN2 = "0012_fnb_ticket_service_handoff"


def _runner(path):
    return MigrationCoordinator(path, project_root=ROOT, inventory_provider=StaticInventory())


def test_0009_to_0010_adds_r1b_schema(tmp_path):
    database = tmp_path / "fnb-r1b.db"
    runner = _runner(database)
    runner.init()
    runner.upgrade("0009_fnb_table_service_r1a")
    assert runner.upgrade("head") == [R1B, R1C, PLAN2]
    runner.verify()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "fnb_kitchen_tickets",
            "fnb_kitchen_ticket_items",
            "fnb_stock_allocations",
            "fnb_manager_approvals",
        } <= tables
        product_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(products)")
        }
        line_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_session_lines)")
        }
        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
        assert "fnb_station" in product_columns
        assert {"station", "sent_quantity", "sent_cancelled_quantity"} <= line_columns
        assert "fnb_manager_pin_hash" in user_columns
        ticket_item_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_kitchen_ticket_items)")
        }
        assert "cancelled_quantity" in ticket_item_columns


def test_r1b_database_constraints_reject_invalid_states(tmp_path):
    database = tmp_path / "fnb-r1b-constraints.db"
    runner = _runner(database)
    runner.init()
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO products "
                "(name, price_vnd, stock, is_active, track_batches, "
                "cost_known_qty, cost_unknown_qty, cost_basis_vnd, "
                "cost_deficit_qty, cost_state_version, fnb_station) "
                "VALUES ('Invalid station', 0, 0, 1, 0, 0, 0, 0, 0, 0, 'PRINTER')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_kitchen_tickets "
                "(shop_id, session_id, station, sequence, status, operation_id, "
                "created_by_user_id) VALUES (1, 1, 'DIRECT', 1, 'NEW', "
                "'operation-r1b-bad', 1)"
            )


def test_r1b_models_expose_required_provenance():
    from fselling import models

    assert models.Product.fnb_station is not None
    assert models.FnbSessionLine.station is not None
    assert models.FnbSessionLine.sent_quantity is not None
    assert models.FnbKitchenTicket.station is not None
    assert models.FnbKitchenTicketItem.session_line_id is not None
    assert models.FnbKitchenTicketItem.cancelled_quantity is not None
    assert models.FnbStockAllocation.cost_basis_vnd is not None
    assert models.FnbManagerApproval.token_hash is not None
