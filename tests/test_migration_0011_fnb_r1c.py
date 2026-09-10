"""Forward-only F&B check, checkout and provenance schema."""

import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


ROOT = Path(__file__).resolve().parents[1]
R1C = "0011_fnb_checkout_r1c"
PLAN2 = "0012_fnb_ticket_service_handoff"
PLAN3 = "0013_roles_returns_approval_r3"
PLAN4 = "0014_session_device_safety_r4"


def _runner(path):
    return MigrationCoordinator(path, project_root=ROOT, inventory_provider=StaticInventory())


def test_0010_to_0011_adds_check_and_transfer_schema(tmp_path):
    database = tmp_path / "fnb-r1c.db"
    runner = _runner(database)
    runner.init()
    runner.upgrade("0010_fnb_kitchen_stock_r1b")
    assert runner.upgrade("head") == [R1C, PLAN2, PLAN3, PLAN4]
    runner.verify()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "fnb_service_checks",
            "fnb_check_lines",
            "fnb_allocation_transfers",
        } <= tables


def test_r1c_constraints_reject_invalid_check_state(tmp_path):
    database = tmp_path / "fnb-r1c-constraints.db"
    runner = _runner(database)
    runner.init()
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO fnb_service_checks "
                "(session_id, label, status, revision, discount_kind, discount_value, "
                "service_charge_kind, service_charge_value, subtotal_vnd, discount_vnd, "
                "service_charge_vnd, total_vnd) VALUES "
                "(1, 'Sai', 'REFUNDED', 0, 'NONE', 0, 'NONE', 0, 0, 0, 0, 0)"
            )


def test_r1c_models_expose_checkout_provenance():
    from fselling import models

    assert models.FnbServiceCheck.order_id is not None
    assert models.FnbCheckLine.session_line_id is not None
    assert models.FnbAllocationTransfer.order_item_id is not None
