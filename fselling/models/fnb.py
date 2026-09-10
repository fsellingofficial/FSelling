import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from ..core.database import Base


class FnbArea(Base):
    __tablename__ = "fnb_areas"
    __table_args__ = (
        Index("ux_fnb_areas_shop_name_key", "shop_id", "name_key", unique=True),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    name_key = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class FnbTable(Base):
    __tablename__ = "fnb_tables"
    __table_args__ = (
        Index("ux_fnb_tables_area_name_key", "area_id", "name_key", unique=True),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    area_id = Column(Integer, ForeignKey("fnb_areas.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    name_key = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    state_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class FnbServiceSession(Base):
    __tablename__ = "fnb_service_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','PARTIALLY_SETTLED','PAYMENT_PENDING','CLOSED','CANCELLED')",
            name="ck_fnb_sessions_status",
        ),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="OPEN", index=True)
    revision = Column(Integer, nullable=False, default=0)
    merged_into_session_id = Column(
        Integer, ForeignKey("fnb_service_sessions.id"), nullable=True
    )
    opened_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opened_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    closed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime, nullable=True)


class FnbSessionTable(Base):
    __tablename__ = "fnb_session_tables"
    id = Column(Integer, primary_key=True)
    session_id = Column(
        Integer, ForeignKey("fnb_service_sessions.id"), nullable=False, index=True
    )
    table_id = Column(Integer, ForeignKey("fnb_tables.id"), nullable=False, index=True)
    added_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    released_at = Column(DateTime, nullable=True)


class FnbSessionLine(Base):
    __tablename__ = "fnb_session_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fnb_session_lines_quantity"),
        CheckConstraint(
            "cancelled_quantity >= 0 AND cancelled_quantity <= quantity",
            name="ck_fnb_session_lines_cancelled",
        ),
    )
    id = Column(Integer, primary_key=True)
    session_id = Column(
        Integer, ForeignKey("fnb_service_sessions.id"), nullable=False, index=True
    )
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(String(300), nullable=False)
    unit_price_vnd = Column(Integer, nullable=False)
    station = Column(String(16), nullable=False, default="DIRECT")
    note = Column(String(500), nullable=True)
    quantity = Column(Integer, nullable=False)
    cancelled_quantity = Column(Integer, nullable=False, default=0)
    sent_quantity = Column(Integer, nullable=False, default=0)
    sent_cancelled_quantity = Column(Integer, nullable=False, default=0)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    state_version = Column(Integer, nullable=False, default=0)


class FnbActionLog(Base):
    __tablename__ = "fnb_action_logs"
    __table_args__ = (
        Index(
            "ux_fnb_action_shop_operation",
            "shop_id",
            "operation_id",
            unique=True,
        ),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    session_id = Column(
        Integer, ForeignKey("fnb_service_sessions.id"), nullable=True, index=True
    )
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    auth_session_id = Column(
        String(128), ForeignKey("auth_sessions.session_id"), nullable=True
    )
    action = Column(String(64), nullable=False)
    operation_id = Column(String(128), nullable=False)
    operation_fingerprint = Column(String(64), nullable=False)
    result_json = Column(Text, nullable=False)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class FnbKitchenTicket(Base):
    __tablename__ = "fnb_kitchen_tickets"
    __table_args__ = (
        Index("ux_fnb_ticket_sequence", "shop_id", "station", "sequence", unique=True),
        Index(
            "ux_fnb_ticket_operation_station",
            "shop_id", "operation_id", "station", unique=True,
        ),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("fnb_service_sessions.id"), nullable=False)
    station = Column(String(16), nullable=False)
    sequence = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="NEW")
    operation_id = Column(String(128), nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    started_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    started_at = Column(DateTime, nullable=True)
    done_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    done_at = Column(DateTime, nullable=True)
    served_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    served_at = Column(DateTime, nullable=True)
    out_of_stock_reason = Column(String(500), nullable=True)
    state_version = Column(Integer, nullable=False, default=0)


class FnbKitchenTicketItem(Base):
    __tablename__ = "fnb_kitchen_ticket_items"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("fnb_kitchen_tickets.id"), nullable=False, index=True)
    session_line_id = Column(Integer, ForeignKey("fnb_session_lines.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    cancelled_quantity = Column(Integer, nullable=False, default=0)
    product_name = Column(String(300), nullable=False)
    note = Column(String(500), nullable=True)


class FnbStockAllocation(Base):
    __tablename__ = "fnb_stock_allocations"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("fnb_service_sessions.id"), nullable=False)
    session_line_id = Column(Integer, ForeignKey("fnb_session_lines.id"), nullable=False)
    ticket_item_id = Column(Integer, ForeignKey("fnb_kitchen_ticket_items.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=True)
    quantity = Column(Integer, nullable=False)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    state = Column(String(32), nullable=False, default="CONSUMED")
    operation_id = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolution_reason = Column(String(500), nullable=True)


class FnbManagerApproval(Base):
    # Legacy table name retained for compatibility with existing F&B approvals.
    __tablename__ = "fnb_manager_approvals"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_auth_session_id = Column(
        String(128), ForeignKey("auth_sessions.session_id"), nullable=True
    )
    action = Column(String(64), nullable=False)
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    context_fingerprint = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class FnbServiceCheck(Base):
    __tablename__ = "fnb_service_checks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','PAYING','PAYMENT_PENDING','PAID','DEBT','CANCELLED')",
            name="ck_fnb_checks_status",
        ),
        CheckConstraint(
            "discount_kind IN ('NONE','FLAT','PERCENT') AND "
            "service_charge_kind IN ('NONE','FLAT','PERCENT')",
            name="ck_fnb_checks_adjustment_kinds",
        ),
        Index("ux_fnb_checks_order_id", "order_id", unique=True),
        Index(
            "ux_fnb_checks_primary_session",
            "session_id",
            unique=True,
            sqlite_where="is_primary = 1",
        ),
    )
    id = Column(Integer, primary_key=True)
    session_id = Column(
        Integer, ForeignKey("fnb_service_sessions.id"), nullable=False, index=True
    )
    label = Column(String(100), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    status = Column(String(24), nullable=False, default="OPEN")
    revision = Column(Integer, nullable=False, default=0)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    discount_kind = Column(String(16), nullable=False, default="NONE")
    discount_value = Column(Integer, nullable=False, default=0)
    service_charge_kind = Column(String(16), nullable=False, default="NONE")
    service_charge_value = Column(Integer, nullable=False, default=0)
    subtotal_vnd = Column(Integer, nullable=False, default=0)
    discount_vnd = Column(Integer, nullable=False, default=0)
    service_charge_vnd = Column(Integer, nullable=False, default=0)
    total_vnd = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    settled_at = Column(DateTime, nullable=True)


class FnbCheckLine(Base):
    __tablename__ = "fnb_check_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fnb_check_lines_quantity"),
        Index(
            "ux_fnb_check_line",
            "check_id",
            "session_line_id",
            unique=True,
        ),
    )
    id = Column(Integer, primary_key=True)
    check_id = Column(Integer, ForeignKey("fnb_service_checks.id"), nullable=False)
    session_line_id = Column(Integer, ForeignKey("fnb_session_lines.id"), nullable=False)
    quantity = Column(Integer, nullable=False)


class FnbAllocationTransfer(Base):
    __tablename__ = "fnb_allocation_transfers"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fnb_transfer_quantity"),
        CheckConstraint(
            "cost_known_qty >= 0 AND cost_unknown_qty >= 0 AND "
            "cost_known_qty + cost_unknown_qty = quantity AND cost_basis_vnd >= 0",
            name="ck_fnb_transfer_cost",
        ),
        Index(
            "ux_fnb_transfer_allocation_order_item",
            "allocation_id",
            "order_item_id",
            unique=True,
        ),
    )
    id = Column(Integer, primary_key=True)
    allocation_id = Column(Integer, ForeignKey("fnb_stock_allocations.id"), nullable=False)
    check_id = Column(Integer, ForeignKey("fnb_service_checks.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
