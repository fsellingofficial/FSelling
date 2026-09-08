"""Forward-only F&B ticket service-handoff schema."""

import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


ROOT = Path(__file__).resolve().parents[1]
R1C = "0011_fnb_checkout_r1c"
PLAN2 = "0012_fnb_ticket_service_handoff"
PLAN3 = "0013_roles_returns_approval_r3"


def _runner(path, *, fault_hook=None):
    return MigrationCoordinator(
        path,
        project_root=ROOT,
        inventory_provider=StaticInventory(),
        fault_hook=fault_hook,
    )


def _database_at_r1c(path):
    runner = _runner(path)
    runner.init()
    runner.upgrade(R1C)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, username, hashed_password, role, is_verified, is_active, "
            "failed_login_count, verification_attempts) "
            "VALUES (1, 'plan2-owner', 'x', 'SELLER', 1, 1, 0, 0)"
        )
        connection.execute(
            "INSERT INTO shops (id, name, is_active, owner_id) "
            "VALUES (1, 'Plan 2 shop', 1, 1)"
        )
        connection.execute(
            "INSERT INTO fnb_service_sessions "
            "(id, shop_id, status, revision, opened_by_user_id, opened_at) "
            "VALUES (1, 1, 'OPEN', 0, 1, '2026-09-07 08:00:00')"
        )
        connection.execute(
            "INSERT INTO fnb_kitchen_tickets "
            "(id, shop_id, session_id, station, sequence, status, operation_id, "
            "created_by_user_id, created_at, state_version) "
            "VALUES (1, 1, 1, 'KITCHEN', 1, 'NEW', 'existing-ticket', 1, "
            "'2026-09-07 08:01:00', 0)"
        )
        connection.commit()
    return runner


def test_0011_to_0012_adds_nullable_handoff_fields_without_rewriting_ticket(tmp_path):
    database = tmp_path / "fnb-plan2.db"
    runner = _database_at_r1c(database)

    assert runner.upgrade("head") == [PLAN2, PLAN3]
    runner.verify()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_kitchen_tickets)")
        }
        ticket = connection.execute(
            "SELECT status, operation_id, served_by_user_id, served_at "
            "FROM fnb_kitchen_tickets WHERE id=1"
        ).fetchone()
    assert {"served_by_user_id", "served_at"} <= columns
    assert ticket == ("NEW", "existing-ticket", None, None)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fnb_kitchen_tickets SET served_by_user_id=1 WHERE id=1",
        "UPDATE fnb_kitchen_tickets SET served_at='2026-09-07 08:10:00' WHERE id=1",
        "UPDATE fnb_kitchen_tickets SET served_by_user_id=1, "
        "served_at='2026-09-07 08:10:00' WHERE id=1",
    ],
)
def test_0012_verifier_rejects_incomplete_or_non_done_handoff(tmp_path, statement):
    database = tmp_path / "fnb-plan2-invalid.db"
    runner = _database_at_r1c(database)
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        connection.execute(statement)
        connection.commit()

    with pytest.raises(RuntimeError, match="FNB_PLAN2_VERIFY_TICKET_HANDOFF"):
        runner.verify()


def test_0012_upgrade_rolls_back_columns_and_version_on_fault(tmp_path):
    database = tmp_path / "fnb-plan2-rollback.db"
    _database_at_r1c(database)

    def fail(stage, revision):
        if stage == "after_ddl" and revision == PLAN2:
            raise RuntimeError("plan2 injected fault")

    with pytest.raises(RuntimeError, match="plan2 injected fault"):
        _runner(database, fault_hook=fail).upgrade(PLAN2)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_kitchen_tickets)")
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert "served_by_user_id" not in columns
    assert "served_at" not in columns
    assert version == (R1C,)


def test_0012_models_expose_ticket_handoff_provenance():
    from fselling import models

    assert models.FnbKitchenTicket.served_by_user_id is not None
    assert models.FnbKitchenTicket.served_at is not None


def test_0012_upgrade_fails_closed_before_ddl_for_done_out_of_stock(tmp_path):
    database = tmp_path / "fnb-plan2-legacy-invalid.db"
    runner = _database_at_r1c(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE fnb_kitchen_tickets "
            "SET status='DONE', out_of_stock_reason='legacy conflict' WHERE id=1"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="FNB_PLAN2_LEGACY_DONE_OUT_OF_STOCK"):
        runner.upgrade(PLAN2)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fnb_kitchen_tickets)")
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert "served_by_user_id" not in columns
    assert version == (R1C,)


def test_0012_verifier_rejects_done_out_of_stock_legacy_row(tmp_path):
    database = tmp_path / "fnb-plan2-verify-invalid.db"
    runner = _database_at_r1c(database)
    runner.upgrade("head")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE fnb_kitchen_tickets "
            "SET status='DONE', out_of_stock_reason='legacy conflict' WHERE id=1"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="FNB_PLAN2_VERIFY_TICKET_LIFECYCLE"):
        runner.verify()


def test_0012_downgrade_is_explicitly_forward_only(tmp_path):
    database = tmp_path / "fnb-plan2-forward-only.db"
    runner = _database_at_r1c(database)
    runner.upgrade(PLAN2)
    graph = runner._graph()
    spec = graph.revisions[graph.index(PLAN2)]

    with pytest.raises(RuntimeError, match="FORWARD_ONLY_MIGRATION"):
        spec.module.downgrade()
