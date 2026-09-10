"""ORM mappings for the I09 offline receipt tables.

The schema is owned by migrations 0004 through 0006.  These classes only map the
released tables so service code can persist and verify offline receipt evidence
and its issue lifecycle.  Lease behaviour belongs to later I09 slices.
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, String, text

from ..core.database import Base


class OfflineLease(Base):
    __tablename__ = "offline_leases"

    lease_id = Column(String, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    device_id = Column(String, nullable=False)
    contract_version = Column(Integer, nullable=False)
    catalog_version = Column(Integer, nullable=False)
    catalog_snapshot_digest = Column(String, nullable=False)
    secret_sha256 = Column(String, nullable=False)
    server_anchor_id = Column(String, nullable=False)
    anchor_server_time_utc = Column(String(26), nullable=False)
    issued_at = Column(String(26), nullable=False)
    expires_at = Column(String(26), nullable=False)
    state_version = Column(Integer, nullable=False, server_default=text("0"))
    revoked_at = Column(String(26), nullable=True)
    revoke_reason = Column(String, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    issued_by_auth_session_id = Column(
        String(128), ForeignKey("auth_sessions.session_id"), nullable=True
    )


class OfflineReceiptRegistry(Base):
    __tablename__ = "offline_receipt_registry"

    offline_uuid = Column(String, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    server_fingerprint = Column(String, nullable=False)
    contract_version = Column(Integer, nullable=False)
    state = Column(String, nullable=False)
    superseded_by_offline_uuid = Column(
        String,
        ForeignKey("offline_receipt_registry.offline_uuid"),
        nullable=True,
    )
    created_at = Column(String(26), nullable=False)
    updated_at = Column(String(26), nullable=False)
    state_version = Column(Integer, nullable=False, server_default=text("0"))


class OfflineReceipt(Base):
    __tablename__ = "offline_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    offline_uuid = Column(
        String,
        ForeignKey("offline_receipt_registry.offline_uuid"),
        nullable=False,
    )
    contract_version = Column(Integer, nullable=False)
    lease_id = Column(String, ForeignKey("offline_leases.lease_id"), nullable=True)
    device_id = Column(String, nullable=True)
    offline_session_id = Column(String, nullable=True)
    sequence = Column(Integer, nullable=True)
    server_fingerprint = Column(String, nullable=False)
    client_fingerprint = Column(String, nullable=True)
    client_fingerprint_mismatch = Column(Integer, nullable=False)
    sold_by_claimed_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    synced_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    attribution_kind = Column(String, nullable=False)
    sold_at_effective = Column(String(26), nullable=False)
    sold_at_client_utc = Column(String(26), nullable=False)
    sold_at_upper_bound = Column(String(26), nullable=True)
    time_confidence = Column(String, nullable=False)
    client_monotonic_ms = Column(Integer, nullable=True)
    server_anchor_id = Column(String, nullable=True)
    ingested_at = Column(String(26), nullable=False)


class OfflineReceiptItem(Base):
    """Canonical contract-v1 line evidence, separate from catalog identity.

    ``claimed_product_id`` is exactly what the offline client signed.  Only a
    verified same-shop catalog row may also appear in ``OrderItem.product_id``.
    The ordinal preserves canonical duplicate multiplicity one row at a time.
    """

    __tablename__ = "offline_receipt_items"
    __table_args__ = (
        Index(
            "ux_offline_receipt_items_receipt_ordinal",
            "receipt_id",
            "item_ordinal",
            unique=True,
        ),
        Index(
            "ux_offline_receipt_items_order_item",
            "order_item_id",
            unique=True,
        ),
        Index(
            "ix_offline_receipt_items_claimed_product",
            "claimed_product_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_id = Column(Integer, ForeignKey("offline_receipts.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    item_ordinal = Column(Integer, nullable=False)
    claimed_product_id = Column(Integer, nullable=False)
    product_name = Column(String, nullable=False)
    unit_price_vnd = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)


class OfflineStockDeficit(Base):
    """Exact per-line evidence that non-batch goods left with no stock behind.

    The tracked twin is ``OfflineBatchStockDeficit``; this one covers products
    without ``track_batches``, where the only other trace would be the aggregate
    ``products.cost_deficit_qty`` - a number that can never be attributed back
    to an order line, so it can never say which issue a stocktake just closed.

    Only a positive stocktake reconciliation may shrink ``remaining_quantity``:
    an owner acknowledgement is deliberately not a resolution kind, because a
    click cannot make the missing quantity reappear.  Migration 0005 enforces
    that in triggers, so a bypassed service is still not a bypassed invariant.
    """

    __tablename__ = "offline_stock_deficits"
    __table_args__ = (
        Index("ux_offline_stock_deficits_order_item", "order_item_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    deficit_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    resolution_kind = Column(String(32), nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(String(26), nullable=True)
    resolution_reason = Column(String, nullable=True)
    state_version = Column(Integer, nullable=False, server_default=text("0"))


class OfflineReceiptIssue(Base):
    """One durable problem found while ingesting an offline receipt.

    ``orders.offline_issue`` stays as a compatibility/history mirror, but it is
    a comma-joined string: it cannot say which line, how much is still missing,
    or who acknowledged what.  This table is the source of truth for the Đối
    Soát screen.

    ``evidence_id`` is a typed pointer, not a foreign key: ``OFFLINE_BATCH_DEFICIT``
    points at the I05 table and ``OFFLINE_STOCK_DEFICIT`` at the one above.
    """

    __tablename__ = "offline_receipt_issues"
    __table_args__ = (
        Index("ix_offline_receipt_issues_open", "state", "severity", "order_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    issue_code = Column(String, nullable=False)
    evidence_kind = Column(String, nullable=False)
    evidence_id = Column(Integer, nullable=True)
    severity = Column(String, nullable=False)
    state = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    opened_at = Column(String(26), nullable=False)
    resolved_at = Column(String(26), nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_kind = Column(String, nullable=True)
    state_version = Column(Integer, nullable=False, server_default=text("0"))


class OfflineRecoveryAction(Base):
    """Durable owner-recovery decision paired with a transaction-local audit.

    Migration 0004 owns the table and deliberately requires ``system_log_id``.
    The ORM mapping is added only when the G1 service starts writing the already
    released schema; it does not create or alter database objects.
    """

    __tablename__ = "offline_recovery_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    action_kind = Column(String, nullable=False)
    original_offline_uuid = Column(String, nullable=False)
    original_fingerprint = Column(String, nullable=True)
    replacement_offline_uuid = Column(String, nullable=True)
    file_digest = Column(String(64), nullable=True)
    reason = Column(String, nullable=False)
    performed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    performed_at = Column(String(26), nullable=False)
    system_log_id = Column(Integer, ForeignKey("system_logs.id"), nullable=False)
