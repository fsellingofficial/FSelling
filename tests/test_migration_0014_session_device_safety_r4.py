"""Forward-only Plan 4 auth-session registry schema."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.topology import StaticInventory


ROOT = Path(__file__).resolve().parents[1]
PLAN3 = "0013_roles_returns_approval_r3"
PLAN4 = "0014_session_device_safety_r4"


def _runner(path, *, cancel_check=None):
    return MigrationCoordinator(
        path,
        project_root=ROOT,
        inventory_provider=StaticInventory(),
        cancel_check=cancel_check,
    )


def _database_at_plan3(path):
    runner = _runner(path)
    runner.init()
    runner.upgrade(PLAN3)
    return runner


def _columns(connection, table):
    return {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}


def _foreign_keys(connection, table):
    return connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()


def test_0013_to_0014_adds_registry_links_and_backfills_legacy_session(tmp_path):
    database = tmp_path / "plan4.db"
    runner = _database_at_plan3(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, username, hashed_password, role, is_verified, is_active, "
            "failed_login_count, verification_attempts, session_id) "
            "VALUES (41, 'legacy-r4', 'x', 'SELLER', 1, 1, 0, 0, 'legacy-sid')"
        )
        connection.commit()

    assert runner.upgrade("head") == [PLAN4]
    runner.verify()

    with sqlite3.connect(database) as connection:
        assert {
            "session_id",
            "user_id",
            "device_id",
            "device_name",
            "device_type",
            "created_at",
            "last_seen_at",
            "expires_at",
            "revoked_at",
            "revoked_by_user_id",
            "revoke_reason",
        } <= _columns(connection, "auth_sessions").keys()
        legacy = connection.execute(
            "SELECT user_id, device_id, device_type, created_at, last_seen_at, "
            "expires_at FROM auth_sessions WHERE session_id='legacy-sid'"
        ).fetchone()
        indexes = connection.execute("PRAGMA index_list(auth_sessions)").fetchall()

        assert "auth_session_id" in _columns(connection, "system_logs")
        assert "auth_session_id" in _columns(connection, "fnb_action_logs")
        assert "issued_by_auth_session_id" in _columns(connection, "offline_leases")
        assert "actor_auth_session_id" in _columns(
            connection, "fnb_manager_approvals"
        )
        assert any(row[1] == "ux_auth_sessions_active_device" for row in indexes)
        assert any(
            row[2:5] == ("users", "user_id", "id")
            for row in _foreign_keys(connection, "auth_sessions")
        )

    assert legacy[:3] == (41, "legacy:legacy-sid", "UNKNOWN")
    assert legacy[3] == legacy[4]
    created = datetime.fromisoformat(legacy[3])
    expires = datetime.fromisoformat(legacy[5])
    assert 23.9 * 3600 <= (expires - created).total_seconds() <= 24 * 3600


def test_0014_models_expose_registry_and_nullable_links():
    from fselling import models

    assert models.AuthSession.session_id is not None
    assert models.SystemLog.auth_session_id is not None
    assert models.FnbActionLog.auth_session_id is not None
    assert models.OfflineLease.issued_by_auth_session_id is not None
    assert models.FnbManagerApproval.actor_auth_session_id is not None


def test_0014_upgrade_rolls_back_schema_and_version_on_fault(tmp_path):
    database = tmp_path / "plan4-rollback.db"
    _database_at_plan3(database)
    calls = 0

    def cancel_during_upgrade():
        nonlocal calls
        calls += 1
        return calls == 3

    with pytest.raises(Exception, match="cancelled before commit"):
        _runner(database, cancel_check=cancel_during_upgrade).upgrade("head")

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert "auth_sessions" not in tables
        assert "auth_session_id" not in _columns(connection, "system_logs")
        assert version == (PLAN3,)


def test_0014_downgrade_is_explicitly_forward_only(tmp_path):
    database = tmp_path / "plan4-forward-only.db"
    runner = _database_at_plan3(database)
    runner.upgrade("head")

    with pytest.raises(RuntimeError, match="FORWARD_ONLY_MIGRATION"):
        runner._graph().head.module.downgrade()
