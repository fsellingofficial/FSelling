"""I09-E migration 0006: claimed item snapshots and tenant-safe identity."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from fselling.migration.coordinator import MigrationCoordinator
from fselling.migration.schema import CONTROL_SCHEMA_FINGERPRINT
from fselling.migration.topology import StaticInventory
from test_migration_0004_offline import _seed_business_rows

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

RELEASED = {
    ROOT: "5bdcb5e297ba9eba83474c5415371129c9c3d1498280ee481e37c27fd37e7a5c",
    I04: "811595d51abd3ba12d1dd10ab9602960766567310281fabd00a042be4bdb797b",
    I05: "d0abe1f5df1d729678dbd258e9ff40848588437c26e3d05c8b88a8e47d492539",
    I09: "572c969914b6517f84a2b5a2bd9c3477e79f9deb88f50fe2c77b3384d487d506",
    I09C: "c4ffcb31bb6f50786ee228cead5202e716abc50eebac93dfba4813de457c4716",
}
CONTROL = "1be2c54a0e8ccce8c35e61509eeb142047fc91ca42f4704033e179e553c26b62"


def _coordinator(path: Path, **kwargs) -> MigrationCoordinator:
    return MigrationCoordinator(
        path,
        project_root=PROJECT_ROOT,
        inventory_provider=StaticInventory(),
        **kwargs,
    )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _module(coordinator: MigrationCoordinator):
    return next(x.module for x in coordinator._graph().revisions if x.revision == I09E)


def _index_shape(connection: sqlite3.Connection) -> dict:
    result = {}
    for row in connection.execute("PRAGMA index_list(offline_receipt_items)"):
        name = row[1]
        if name.startswith("sqlite_autoindex_"):
            continue
        columns = tuple(
            item[2]
            for item in sorted(
                connection.execute(f'PRAGMA index_info("{name}")'),
                key=lambda item: item[0],
            )
        )
        result[name] = (row[2], columns, row[4])
    return result


def _normalized_digest(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode()).hexdigest()


def _at_0005(path: Path, **kwargs) -> MigrationCoordinator:
    coordinator = _coordinator(path, **kwargs)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade(I09C) == [I04, I05, I09, I09C]
    return coordinator


def _seed_v1_receipt(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO offline_leases
           (lease_id,shop_id,user_id,device_id,contract_version,catalog_version,
            catalog_snapshot_digest,secret_sha256,server_anchor_id,
            anchor_server_time_utc,issued_at,expires_at,state_version)
           VALUES ('lease-1',1,1,'device-1',1,7,?,?,'anchor-1',?,?,?,0)""",
        ("a" * 64, "b" * 64, "2026-08-01 02:00:00.000000",
         "2026-08-01 02:00:00.000000", "2026-08-01 14:00:00.000000"),
    )
    connection.execute(
        """INSERT INTO offline_receipt_registry
           (offline_uuid,shop_id,order_id,server_fingerprint,contract_version,
            state,created_at,updated_at,state_version)
           VALUES ('uuid-v1',1,1,?,1,'INGESTED',?,?,0)""",
        ("f" * 64, "2026-08-01 04:00:00.000000", "2026-08-01 04:00:00.000000"),
    )
    connection.execute(
        """INSERT INTO offline_receipts
           (id,order_id,offline_uuid,contract_version,lease_id,device_id,
            offline_session_id,sequence,server_fingerprint,client_fingerprint,
            client_fingerprint_mismatch,sold_by_claimed_user_id,synced_by_user_id,
            attribution_kind,sold_at_effective,sold_at_client_utc,
            sold_at_upper_bound,time_confidence,client_monotonic_ms,
            server_anchor_id,ingested_at)
           VALUES (1,1,'uuid-v1',1,'lease-1','device-1','lease-1',1,?,?,0,1,2,
                   'LEASE_CLAIM',?,?,?,'ANCHORED_CLIENT',3600000,'anchor-1',?)""",
        ("f" * 64, "f" * 64, "2026-08-01 03:00:00.000000",
         "2026-08-01 03:00:00.000000", "2026-08-01 04:00:00.000000",
         "2026-08-01 04:00:00.000000"),
    )


def _seed_valid_v1_after_0006(path: Path) -> MigrationCoordinator:
    coordinator = _coordinator(path)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    with _connect(path) as connection:
        _seed_business_rows(connection)
        connection.execute("UPDATE orders SET offline_issue=NULL")
        connection.execute("UPDATE order_items SET product_name='Z' WHERE id=2")
        _seed_v1_receipt(connection)
        connection.execute(
            """INSERT INTO offline_receipt_items
               (receipt_id, order_item_id, item_ordinal, claimed_product_id,
                product_name, unit_price_vnd, quantity)
               VALUES (1, 1, 1, 1, 'Hàng offline', 100, 1)"""
        )
        connection.execute(
            """INSERT INTO offline_receipt_items
               (receipt_id, order_item_id, item_ordinal, claimed_product_id,
                product_name, unit_price_vnd, quantity)
               VALUES (1, 2, 2, 1, 'Z', 100, 1)"""
        )
    return coordinator


def test_fresh_0001_to_0006_restart_and_exact_shape(tmp_path):
    database = tmp_path / "fresh.db"
    coordinator = _coordinator(database)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    report = coordinator.verify()
    assert report.current_revision == report.head_revision == PLAN3
    assert report.revision_count == 13
    assert coordinator.upgrade("head") == []
    assert coordinator.verify().database_uuid == report.database_uuid

    with _connect(database) as connection:
        columns = [(r[1], r[2], r[3], r[5]) for r in connection.execute(
            "PRAGMA table_info(offline_receipt_items)"
        )]
        indexes = _index_shape(connection)
    module = _module(coordinator)
    assert tuple(columns) == module.EXPECTED_COLUMNS_0006
    assert indexes == {
        name: (unique, key_columns, partial)
        for name, unique, key_columns, partial in module.EXPECTED_INDEXES_0006
    }


def test_0005_to_0006_empty_v1_is_forward_only_and_checksummed(tmp_path):
    database = tmp_path / "step.db"
    coordinator = _at_0005(database)
    assert coordinator.upgrade(I09E) == [I09E]
    with _connect(database) as connection:
        _module(coordinator).verify(connection)

    graph = coordinator._graph()
    assert [x.revision for x in graph.revisions] == [
        ROOT, I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3
    ]
    spec = next(x for x in graph.revisions if x.revision == I09E)
    assert spec.down_revision == I09C
    path = PROJECT_ROOT / f"migrations/versions/{I09E}.py"
    digest = _normalized_digest(path)
    manifest = json.loads((PROJECT_ROOT / "migrations/checksums.json").read_text())
    assert manifest["revisions"][I09E] == {
        "down_revision": I09C,
        "path": f"versions/{I09E}.py",
        "sha256": digest,
    }
    with pytest.raises(RuntimeError, match="forward-only"):
        spec.module.downgrade()


def test_released_sources_and_control_fingerprint_unchanged():
    manifest = json.loads((PROJECT_ROOT / "migrations/checksums.json").read_text())
    for revision, expected in RELEASED.items():
        path = PROJECT_ROOT / f"migrations/versions/{revision}.py"
        assert _normalized_digest(path) == expected
        assert manifest["revisions"][revision]["sha256"] == expected
    assert CONTROL_SCHEMA_FINGERPRINT == CONTROL


def test_0006_refuses_to_fabricate_preexisting_v1(tmp_path):
    database = tmp_path / "preexisting.db"
    coordinator = _at_0005(database)
    with _connect(database) as connection:
        _seed_business_rows(connection)
        _seed_v1_receipt(connection)
    with pytest.raises(RuntimeError, match="I09E_PREEXISTING_V1_RECEIPTS"):
        coordinator.upgrade(I09E)
    with _connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (I09C,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='offline_receipt_items'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("stage", ["after_ddl", "after_journal", "after_verify"])
def test_0006_rolls_back_as_one_unit(tmp_path, stage):
    database = tmp_path / f"rollback-{stage}.db"

    def fail(selected, revision):
        if selected == stage and revision == I09E:
            raise RuntimeError(stage)

    coordinator = _at_0005(database, fault_hook=fail)
    with pytest.raises(RuntimeError, match=stage):
        coordinator.upgrade("head")
    with _connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (I09C,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='offline_receipt_items'"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE offline_receipt_items SET item_ordinal=3 WHERE id=2", "I09E_VERIFY_ITEM_ORDINALS"),
        ("UPDATE offline_receipt_items SET item_ordinal=3 WHERE id=1; UPDATE offline_receipt_items SET item_ordinal=1 WHERE id=2; UPDATE offline_receipt_items SET item_ordinal=2 WHERE id=1", "I09E_VERIFY_ITEM_CANONICAL_ORDER"),
        ("UPDATE offline_receipt_items SET order_item_id=3 WHERE id=2", "I09E_VERIFY_ITEM_SCOPE"),
        ("UPDATE offline_receipt_items SET product_name='Sai' WHERE id=1", "I09E_VERIFY_ITEM_SNAPSHOT"),
        ("UPDATE offline_receipt_items SET unit_price_vnd=101 WHERE id=1", "I09E_VERIFY_ITEM_SNAPSHOT"),
        ("UPDATE offline_receipt_items SET quantity=2 WHERE id=1", "I09E_VERIFY_ITEM_SNAPSHOT"),
        ("UPDATE offline_receipt_items SET claimed_product_id=2", "I09E_VERIFY_VERIFIED_PRODUCT_SCOPE"),
        ("UPDATE order_items SET product_id=NULL WHERE id=1; DELETE FROM offline_receipt_items WHERE id=1", "I09E_VERIFY_V1_CARDINALITY"),
    ],
)
def test_verifier_fails_closed_on_snapshot_corruption(tmp_path, statement, code):
    database = tmp_path / (code + ".db")
    coordinator = _seed_valid_v1_after_0006(database)
    with _connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        for sql in statement.split("; "):
            connection.execute(sql)
    with pytest.raises(RuntimeError, match=code):
        coordinator.verify()


def test_verifier_accepts_null_missing_or_cross_shop_and_rejects_wrong_verified_fk(tmp_path):
    database = tmp_path / "scope.db"
    coordinator = _seed_valid_v1_after_0006(database)
    with _connect(database) as connection:
        connection.execute(
            "UPDATE offline_receipt_items SET claimed_product_id=999999 WHERE id=1"
        )
        connection.execute("UPDATE offline_receipt_items SET item_ordinal=3 WHERE id=1")
        connection.execute("UPDATE offline_receipt_items SET item_ordinal=1 WHERE id=2")
        connection.execute("UPDATE offline_receipt_items SET item_ordinal=2 WHERE id=1")
        connection.execute("UPDATE order_items SET product_id=NULL WHERE id=1")
    coordinator.verify()

    with _connect(database) as connection:
        connection.execute(
            "INSERT INTO shops (id,name,is_active,owner_id) VALUES (2,'B',1,1)"
        )
        connection.execute(
            """INSERT INTO products
               (id,code,name,price,price_vnd,stock,is_active,shop_id,track_batches,
                cost_known_qty,cost_unknown_qty,cost_basis_vnd,cost_deficit_qty,cost_state_version)
               VALUES (2,'SP-2','B',100,100,0,1,2,0,0,0,0,0,0)"""
        )
        connection.execute("UPDATE offline_receipt_items SET claimed_product_id=2 WHERE id=1")
    coordinator.verify()

    with _connect(database) as connection:
        connection.execute("UPDATE order_items SET product_id=2 WHERE id=1")
    with pytest.raises(RuntimeError, match="I09E_VERIFY_VERIFIED_PRODUCT_SCOPE"):
        coordinator.verify()


def test_verifier_rejects_nullable_column_shape(tmp_path):
    database = tmp_path / "nullable-shape.db"
    coordinator = _coordinator(database)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    module = _module(coordinator)
    with _connect(database) as connection:
        connection.execute("DROP TABLE offline_receipt_items")
        connection.execute(
            module.DDL[0].replace(
                "product_name TEXT NOT NULL",
                "product_name TEXT",
            )
        )
        for statement in module.DDL[1:]:
            connection.execute(statement)
    with _connect(database) as connection, pytest.raises(
        RuntimeError, match="I09E_VERIFY_SCHEMA_SHAPE"
    ):
        module.verify(connection)


@pytest.mark.parametrize(
    ("name", "replacement"),
    [
        (
            "ux_offline_receipt_items_receipt_ordinal",
            "CREATE INDEX ux_offline_receipt_items_receipt_ordinal "
            "ON offline_receipt_items (receipt_id, item_ordinal)",
        ),
        (
            "ux_offline_receipt_items_receipt_ordinal",
            "CREATE UNIQUE INDEX ux_offline_receipt_items_receipt_ordinal "
            "ON offline_receipt_items (item_ordinal, receipt_id)",
        ),
        (
            "ix_offline_receipt_items_claimed_product",
            "CREATE INDEX ix_offline_receipt_items_claimed_product "
            "ON offline_receipt_items (quantity)",
        ),
        (
            "ix_offline_receipt_items_claimed_product",
            "CREATE INDEX ix_offline_receipt_items_claimed_product "
            "ON offline_receipt_items (claimed_product_id) "
            "WHERE claimed_product_id > 0",
        ),
    ],
    ids=["unique", "column-order", "key-column", "partial"],
)
def test_verifier_rejects_named_index_with_wrong_shape(tmp_path, name, replacement):
    database = tmp_path / f"index-{name}-{hashlib.sha256(replacement.encode()).hexdigest()[:8]}.db"
    coordinator = _coordinator(database)
    assert coordinator.init() == [ROOT]
    assert coordinator.upgrade("head") == [I04, I05, I09, I09C, I09E, I10A, PO, FNB, R1B, R1C, PLAN2, PLAN3]
    module = _module(coordinator)
    with _connect(database) as connection:
        connection.execute(f'DROP INDEX "{name}"')
        connection.execute(replacement)
    with _connect(database) as connection, pytest.raises(
        RuntimeError, match="I09E_VERIFY_SCHEMA_SHAPE"
    ):
        module.verify(connection)
