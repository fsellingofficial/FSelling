"""Plan 4 durable authentication sessions and device linkage."""

from alembic import op


revision = "0014_session_device_safety_r4"
down_revision = "0013_roles_returns_approval_r3"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE auth_sessions (
        session_id VARCHAR(128) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        device_id VARCHAR(128) NOT NULL,
        device_name VARCHAR(80) NOT NULL,
        device_type VARCHAR(16) NOT NULL,
        created_at DATETIME NOT NULL,
        last_seen_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        revoked_at DATETIME,
        revoked_by_user_id INTEGER REFERENCES users(id),
        revoke_reason VARCHAR(48)
    )""",
    "CREATE UNIQUE INDEX ux_auth_sessions_active_device ON auth_sessions "
    "(user_id, device_id) WHERE revoked_at IS NULL",
    "CREATE INDEX ix_auth_sessions_user_state ON auth_sessions "
    "(user_id, revoked_at, expires_at)",
    "ALTER TABLE system_logs ADD COLUMN auth_session_id VARCHAR(128) "
    "REFERENCES auth_sessions(session_id)",
    "CREATE INDEX ix_system_logs_auth_session_id ON system_logs(auth_session_id)",
    "ALTER TABLE offline_leases ADD COLUMN issued_by_auth_session_id VARCHAR(128) "
    "REFERENCES auth_sessions(session_id)",
    "ALTER TABLE fnb_action_logs ADD COLUMN auth_session_id VARCHAR(128) "
    "REFERENCES auth_sessions(session_id)",
    "ALTER TABLE fnb_manager_approvals ADD COLUMN actor_auth_session_id VARCHAR(128) "
    "REFERENCES auth_sessions(session_id)",
)


def _execute(statement, parameters=None):
    callback = op.get_context().config.attributes.get("before_statement")
    if callback is not None:
        callback()
    bind = op.get_bind()
    if parameters is None:
        bind.exec_driver_sql(statement)
    else:
        bind.exec_driver_sql(statement, parameters)


def _columns(execute, table):
    return {row[1]: row for row in execute(f"PRAGMA table_info({table})").fetchall()}


def _foreign_keys(execute, table):
    return execute(f"PRAGMA foreign_key_list({table})").fetchall()


def upgrade():
    execute = op.get_bind().exec_driver_sql
    if "session_id" not in _columns(execute, "users"):
        raise RuntimeError("PLAN4_LEGACY_SCHEMA_MISMATCH")
    for statement in DDL:
        _execute(statement)

    _execute(
        """INSERT INTO auth_sessions (
               session_id, user_id, device_id, device_name, device_type,
               created_at, last_seen_at, expires_at
           )
           SELECT session_id, id, 'legacy:' || session_id,
                  'Thiết bị trước Plan 4', 'UNKNOWN',
                  datetime('now'), datetime('now'), datetime('now', '+24 hours')
           FROM users WHERE session_id IS NOT NULL""",
    )


def verify(connection):
    execute = getattr(connection, "exec_driver_sql", connection.execute)
    required = {
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
    }
    if not required <= _columns(execute, "auth_sessions").keys():
        raise RuntimeError("PLAN4_VERIFY_AUTH_SESSION_COLUMNS")

    expected_links = {
        "system_logs": ("auth_session_id", "auth_sessions"),
        "offline_leases": ("issued_by_auth_session_id", "auth_sessions"),
        "fnb_action_logs": ("auth_session_id", "auth_sessions"),
        "fnb_manager_approvals": ("actor_auth_session_id", "auth_sessions"),
    }
    for table, (column, target) in expected_links.items():
        if column not in _columns(execute, table):
            raise RuntimeError("PLAN4_VERIFY_LINK_COLUMN")
        if not any(row[2:5] == (target, column, "session_id") for row in _foreign_keys(execute, table)):
            raise RuntimeError("PLAN4_VERIFY_LINK_FOREIGN_KEY")

    indexes = execute("PRAGMA index_list(auth_sessions)").fetchall()
    if not any(row[1] == "ux_auth_sessions_active_device" and row[2] and row[4] for row in indexes):
        raise RuntimeError("PLAN4_VERIFY_ACTIVE_DEVICE_INDEX")
    if execute(
        """SELECT COUNT(*) FROM auth_sessions
           WHERE last_seen_at < created_at OR expires_at <= created_at
              OR device_type NOT IN ('DESKTOP','TABLET','MOBILE','KDS','UNKNOWN')"""
    ).fetchone()[0]:
        raise RuntimeError("PLAN4_VERIFY_AUTH_SESSION_STATE")
    if execute(
        """SELECT COUNT(*) FROM (
               SELECT user_id, device_id FROM auth_sessions
               WHERE revoked_at IS NULL GROUP BY user_id, device_id HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]:
        raise RuntimeError("PLAN4_VERIFY_ACTIVE_DEVICE_DUPLICATE")
    if execute(
        """SELECT COUNT(*) FROM fnb_manager_approvals a
           LEFT JOIN auth_sessions s ON s.session_id=a.actor_auth_session_id
           WHERE a.actor_auth_session_id IS NOT NULL AND s.session_id IS NULL"""
    ).fetchone()[0]:
        raise RuntimeError("PLAN4_VERIFY_APPROVAL_AUTH_SESSION")


def downgrade():
    raise RuntimeError("FORWARD_ONLY_MIGRATION")
