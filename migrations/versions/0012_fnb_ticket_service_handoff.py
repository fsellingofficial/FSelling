"""F&B ticket service handoff provenance."""

from alembic import op


revision = "0012_fnb_ticket_service_handoff"
down_revision = "0011_fnb_checkout_r1c"
branch_labels = None
depends_on = None

DDL = (
    "ALTER TABLE fnb_kitchen_tickets ADD COLUMN served_by_user_id INTEGER "
    "REFERENCES users(id)",
    "ALTER TABLE fnb_kitchen_tickets ADD COLUMN served_at DATETIME",
)


def _execute(statement):
    callback = op.get_context().config.attributes.get("before_statement")
    if callback is not None:
        callback()
    op.execute(statement)


def upgrade():
    invalid = op.get_bind().exec_driver_sql(
        "SELECT COUNT(*) FROM fnb_kitchen_tickets "
        "WHERE status='DONE' AND out_of_stock_reason IS NOT NULL"
    ).scalar()
    if invalid:
        raise RuntimeError("FNB_PLAN2_LEGACY_DONE_OUT_OF_STOCK")
    for statement in DDL:
        _execute(statement)


def verify(connection):
    execute = getattr(connection, "exec_driver_sql", connection.execute)
    columns = {
        row[1]
        for row in execute("PRAGMA table_info(fnb_kitchen_tickets)").fetchall()
    }
    if not {"served_by_user_id", "served_at"} <= columns:
        raise RuntimeError("FNB_PLAN2_VERIFY_TICKET_COLUMNS")
    invalid_lifecycle = execute(
        "SELECT COUNT(*) FROM fnb_kitchen_tickets "
        "WHERE status='DONE' AND out_of_stock_reason IS NOT NULL"
    ).fetchone()[0]
    if invalid_lifecycle:
        raise RuntimeError("FNB_PLAN2_VERIFY_TICKET_LIFECYCLE")
    invalid = execute(
        """SELECT COUNT(*)
           FROM fnb_kitchen_tickets t
           LEFT JOIN users u ON u.id=t.served_by_user_id
           WHERE (t.served_by_user_id IS NULL)<>(t.served_at IS NULL)
              OR (t.served_at IS NOT NULL AND t.status<>'DONE')
              OR (t.served_by_user_id IS NOT NULL AND u.id IS NULL)"""
    ).fetchone()[0]
    if invalid:
        raise RuntimeError("FNB_PLAN2_VERIFY_TICKET_HANDOFF")


def downgrade():
    raise RuntimeError("FORWARD_ONLY_MIGRATION")
