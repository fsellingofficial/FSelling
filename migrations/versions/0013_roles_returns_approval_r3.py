"""Plan 3 roles, returns and approval linkage."""

from alembic import op


revision = "0013_roles_returns_approval_r3"
down_revision = "0012_fnb_ticket_service_handoff"
branch_labels = None
depends_on = None

DDL = (
    "ALTER TABLE fnb_manager_approvals ADD COLUMN "
    "context_fingerprint VARCHAR(64)",
    "ALTER TABLE order_returns ADD COLUMN manager_approval_id INTEGER "
    "REFERENCES fnb_manager_approvals(id)",
)

REQUIRED_COLUMNS = {
    "fnb_manager_approvals": {
        "id",
        "shop_id",
        "approver_user_id",
        "actor_user_id",
        "action",
        "entity_type",
        "entity_id",
        "revision",
        "token_hash",
        "expires_at",
        "used_at",
        "created_at",
    },
    "order_returns": {
        "id",
        "order_id",
        "shop_id",
        "created_by_user_id",
    },
}


def _execute(statement):
    callback = op.get_context().config.attributes.get("before_statement")
    if callback is not None:
        callback()
    op.execute(statement)


def _columns(execute, table):
    return {row[1]: row for row in execute(f"PRAGMA table_info({table})").fetchall()}


def upgrade():
    execute = op.get_bind().exec_driver_sql
    for table, required in REQUIRED_COLUMNS.items():
        if not required <= _columns(execute, table).keys():
            raise RuntimeError("PLAN3_LEGACY_SCHEMA_MISMATCH")
    for statement in DDL:
        _execute(statement)


def verify(connection):
    execute = getattr(connection, "exec_driver_sql", connection.execute)
    approval_columns = _columns(execute, "fnb_manager_approvals")
    return_columns = _columns(execute, "order_returns")
    if approval_columns.get("context_fingerprint", (None, None, None, None))[2:4] != (
        "VARCHAR(64)",
        0,
    ):
        raise RuntimeError("PLAN3_VERIFY_APPROVAL_COLUMN")
    if return_columns.get("manager_approval_id", (None, None, None, None))[2:4] != (
        "INTEGER",
        0,
    ):
        raise RuntimeError("PLAN3_VERIFY_RETURN_COLUMN")

    foreign_keys = execute("PRAGMA foreign_key_list(order_returns)").fetchall()
    if not any(
        row[2:5] == ("fnb_manager_approvals", "manager_approval_id", "id")
        for row in foreign_keys
    ):
        raise RuntimeError("PLAN3_VERIFY_RETURN_FOREIGN_KEY")

    invalid_fingerprints = execute(
        """SELECT COUNT(*) FROM fnb_manager_approvals
           WHERE (action='ORDER_RETURN_EXCEPTION' AND context_fingerprint IS NULL)
              OR (context_fingerprint IS NOT NULL AND (
                    typeof(context_fingerprint)<>'text'
                 OR length(context_fingerprint)<>64
                 OR length(trim(context_fingerprint, '0123456789abcdef'))<>0
              ))"""
    ).fetchone()[0]
    if invalid_fingerprints:
        raise RuntimeError("PLAN3_VERIFY_APPROVAL_FINGERPRINT")

    invalid_links = execute(
        """SELECT COUNT(*)
           FROM order_returns r
           LEFT JOIN fnb_manager_approvals a ON a.id=r.manager_approval_id
           WHERE r.manager_approval_id IS NOT NULL
             AND (a.id IS NULL
               OR a.shop_id<>r.shop_id
               OR a.action<>'ORDER_RETURN_EXCEPTION'
               OR a.entity_type<>'ORDER'
               OR a.entity_id<>r.order_id
               OR a.revision<>0
               OR a.context_fingerprint IS NULL)"""
    ).fetchone()[0]
    if invalid_links:
        raise RuntimeError("PLAN3_VERIFY_RETURN_APPROVAL_LINK")


def downgrade():
    raise RuntimeError("FORWARD_ONLY_MIGRATION")
