import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship, validates

from ..core.database import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ux_orders_operation_id", "operation_id", unique=True),
        # I10-A composite parent key for tenant-safe QR intent/event FKs.
        Index("ux_i10a_orders_id_shop", "id", "shop_id", unique=True),
    )
    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"))
    legacy_total_amount = Column("total_amount", Float)
    total_amount = Column("total_vnd", Integer, nullable=False, default=0)
    legacy_discount_amount = Column("discount_amount", Float, default=0)
    discount_amount = Column("discount_vnd", Integer, nullable=False, default=0)
    voucher_code = Column(String, nullable=True)
    payment_method = Column(String, default="transfer")  # 'transfer' or 'cash'
    status = Column(String, default="PENDING")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Mã do web client giữ lại khi retry tạo đơn. Unique ở DB để lỗi mạng hoặc
    # double-click không thể trừ kho/tăng voucher hai lần.
    operation_id = Column(String(128), nullable=True)
    operation_fingerprint = Column(String(64), nullable=True)
    # Người tạo và ca bán hàng là dấu vết server-side. Nullable để giữ được
    # đơn legacy; service tạo đơn mới sẽ luôn gắn từ current_user/ca đang mở.
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)
    # Tiền khách đưa và tiền thối là ảnh chụp của lần thu tiền mặt. Số thực giữ
    # lại cho đơn vẫn nằm ở cash_paid_amount/OrderPayment.
    legacy_cash_tendered_amount = Column("cash_tendered_amount", Float, nullable=True)
    cash_tendered_amount = Column("cash_tendered_vnd", Integer, nullable=True)
    legacy_cash_change_amount = Column("cash_change_amount", Float, nullable=True)
    cash_change_amount = Column("cash_change_vnd", Integer, nullable=True)
    # Khách hàng gắn vào đơn (tùy chọn). NULL với đơn khách vãng lai. (C2a)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    # D1: tổng tiền THỰC NHẬN qua webhook ngân hàng và mã giao dịch gần nhất.
    # `bank_txn_id` cố ý KHÔNG unique: retry webhook là bình thường; chống xử
    # lý lặp nằm ở `OrderPayment.idempotency_key`.
    legacy_paid_amount = Column("paid_amount", Float, nullable=True)
    paid_amount = Column("paid_vnd", Integer, nullable=True)
    bank_txn_id = Column(String(128), nullable=True, index=True)
    # D4: tiền bù mặt được tách khỏi tiền ngân hàng để hóa đơn và đối soát nêu
    # đúng nguồn. Số đã hoàn là tổng lũy kế; refund_due_amount chỉ là khoản còn
    # phải hoàn ở thời điểm hiện tại.
    legacy_cash_paid_amount = Column("cash_paid_amount", Float, nullable=False, default=0)
    cash_paid_amount = Column("cash_paid_vnd", Integer, nullable=False, default=0)
    legacy_refunded_amount = Column("refunded_amount", Float, nullable=False, default=0)
    refunded_amount = Column("refunded_vnd", Integer, nullable=False, default=0)
    legacy_refund_due_amount = Column("refund_due_amount", Float, nullable=False, default=0)
    refund_due_amount = Column("refund_due_vnd", Integer, nullable=False, default=0)
    refund_completed_at = Column(DateTime, nullable=True)
    refund_completed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    refund_method = Column(String(20), nullable=True)
    refund_note = Column(String(500), nullable=True)
    refund_reference = Column(String(128), nullable=True)
    # UNDERPAID | OVERPAID | LATE_PAYMENT | LEGACY_REVIEW | NULL (không vướng).
    reconciliation_reason = Column(String(32), nullable=True, index=True)

    # --- G2: đơn bán khi mất mạng ---
    # CỐ Ý không dùng lại `reconciliation_reason` ở trên: cột đó mang nghĩa đối
    # soát NGÂN HÀNG và đang lái màn Đối Soát lẫn `_don_co_tien_ve_chua_ghi_nhan`.
    # Nhồi giá trị mới vào một khái niệm tiền đang chạy tốt là đúng cái sai mà
    # bẫy 25 trong KIEN_TRUC.md đã trả giá.
    offline_uuid = Column(String(64), nullable=True, unique=True, index=True)
    # Giờ BÁN, không phải giờ sync. Ca thu ngân được chọn theo cột này.
    sold_offline_at = Column(DateTime, nullable=True)
    # Danh sách vướng mắc nối bằng dấu phẩy (TON_AM, CA_KHONG_KHOP, ...).
    offline_issue = Column(String(120), nullable=True, index=True)
    offline_device = Column(String(64), nullable=True)

    # --- H1: tích điểm khách thân thiết ---
    # `discount_amount` phía trên CỐ Ý vẫn chỉ mang nghĩa voucher. Tách phần
    # giảm bằng điểm để hóa đơn, trả hàng và kiểm toán không nhập nhằng hai loại.
    loyalty_points_redeemed = Column(Integer, nullable=False, default=0)
    legacy_loyalty_discount_amount = Column("loyalty_discount_amount", Float, nullable=False, default=0)
    loyalty_discount_amount = Column("loyalty_discount_vnd", Integer, nullable=False, default=0)
    # Ảnh chụp luật CỘNG tại lúc bán. Đơn nợ/chuyển khoản có thể thanh toán sau
    # khi chủ shop đã đổi chương trình; lịch sử của đơn không được đổi theo.
    legacy_loyalty_earn_amount_step = Column("loyalty_earn_amount_step", Float, nullable=True)
    loyalty_earn_amount_step = Column("loyalty_earn_amount_step_vnd", Integer, nullable=True)
    loyalty_earn_points_step = Column(Integer, nullable=True)
    loyalty_expiry_days_snapshot = Column(Integer, nullable=True)
    loyalty_points_earned = Column(Integer, nullable=False, default=0)
    # Kể cả kết quả cộng là 0 (chương trình vừa tắt), mốc này vẫn chốt rằng lần
    # chuyển PAID đã được xét; retry sau khi bật lại không được cộng hồi tố.
    loyalty_awarded_at = Column(DateTime, nullable=True)

    # Cancellation reversal is independent from return provenance.  The status
    # transition is not reused as the inventory idempotency marker.
    inventory_reversed = Column(Integer, nullable=False, default=0)
    inventory_reversal_key = Column(String(128), nullable=True)
    inventory_reversal_version = Column(Integer, nullable=False, default=0)

    shop = relationship("Shop", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    customer = relationship("Customer")
    payments = relationship("OrderPayment", back_populates="order")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    shift = relationship("CashShift", back_populates="orders")
    returns = relationship("OrderReturn", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    # Tham chiếu sản phẩm gốc. Nullable vì các dòng tạo trước migration A1a
    # chỉ có product_name; backfill khớp được đến đâu thì điền đến đó.
    # Dùng để hoàn tồn kho chính xác khi hủy đơn (A1d) - khớp theo tên là
    # không tin cậy vì sản phẩm có thể bị đổi tên hoặc xóa.
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    product_name = Column(String)
    legacy_price = Column("price", Float)
    price = Column("unit_price_vnd", Integer, nullable=False, default=0)
    # Ảnh chụp giá vốn tại thời điểm bán, giống cách `product_name` chụp tên.
    # Tra ngược Product.cost_price lúc làm báo cáo thì mỗi lần nhập lô hàng giá
    # khác là lãi của các tháng trước tự đổi số - và không cứu lại được nữa.
    # NULL = bán trước khi có giá vốn; báo cáo loại ra chứ không tính lãi bằng
    # cả giá bán.
    legacy_cost_price = Column("cost_price", Float, nullable=True)
    quantity = Column(Integer)
    discount_vnd = Column(Integer, nullable=False, default=0)
    loyalty_discount_vnd = Column(Integer, nullable=False, default=0)
    net_amount_vnd = Column(Integer, nullable=False, default=0)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    returned_total_qty = Column(Integer, nullable=False, default=0)
    returned_known_qty = Column(Integer, nullable=False, default=0)
    returned_unknown_qty = Column(Integer, nullable=False, default=0)
    returned_cost_basis_vnd = Column(Integer, nullable=False, default=0)
    returned_refund_vnd = Column(Integer, nullable=False, default=0)
    cost_return_version = Column(Integer, nullable=False, default=0)
    inventory_reversed = Column(Integer, nullable=False, default=0)
    inventory_reversal_version = Column(Integer, nullable=False, default=0)
    order = relationship("Order", back_populates="items")

    @property
    def cost_price(self):
        known = int(self.cost_known_qty or 0)
        if known <= 0 or int(self.cost_unknown_qty or 0) > 0:
            return None
        return Decimal(int(self.cost_basis_vnd or 0)) / Decimal(known)


class OrderReturn(Base):
    """Một lần khách mang hàng trả lại. Một đơn có thể có nhiều lần.

    CỐ Ý tách khỏi cụm `refund_*` trên `orders`: cụm đó là chu kỳ hoàn khoản
    khách CHUYỂN THỪA (một lần duy nhất, `refund_due_amount` là số vô hướng).
    Trả hàng là chuyện khác hẳn và xảy ra nhiều lần, nên phải có bảng riêng.

    Đơn giữ nguyên trạng thái `PAID`: hóa đơn đã xuất là sự thật lịch sử, việc
    khách trả lại là một sự kiện xảy ra SAU đó chứ không xóa đi lần bán.

    `shop_id` lặp lại từ đơn để báo cáo lọc theo shop + ngày trả mà không phải
    join; ngày ở đây là ngày TRẢ, không phải ngày bán.
    """

    __tablename__ = "order_returns"
    __table_args__ = (
        Index("ix_order_returns_order_id", "order_id"),
        Index("ix_order_returns_shop_id_created_at", "shop_id", "created_at"),
        Index(
            "ux_order_returns_idempotency_key", "idempotency_key", unique=True
        ),
    )
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    # Khóa chống bấm lặp nằm ở ĐÂY chứ không mượn của `order_payments`: phiếu
    # trả có tiền hoàn bằng 0 (đơn giảm giá 100%) không sinh dòng ledger nào,
    # mà vẫn phải chặn được lần bấm thứ hai.
    idempotency_key = Column(String(128), nullable=True)
    # Cùng operation_id chỉ là retry khi TOÀN BỘ yêu cầu vật chất giống nhau.
    # Nếu không có fingerprint, đổi số lượng/cách hoàn vẫn bị trả 200 như thể
    # yêu cầu mới đã làm xong.
    operation_fingerprint = Column(String(64), nullable=True)
    # Tiền thực hoàn cho khách, ĐÃ trừ phần giảm giá voucher phân bổ cho các
    # dòng bị trả. Hoàn theo giá niêm yết là shop chịu trọn phần đã giảm.
    legacy_refund_amount = Column("refund_amount", Float, nullable=False, default=0)
    refund_amount = Column("refund_vnd", Integer, nullable=False, default=0)
    refund_method = Column(String(20), nullable=True)   # cash | transfer | None khi 0đ
    reason = Column(String(200), nullable=True)
    note = Column(String(500), nullable=True)
    reference = Column(String(128), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_approval_id = Column(
        Integer, ForeignKey("fnb_manager_approvals.id"), nullable=True
    )
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    # Điểm điều chỉnh của RIÊNG lần trả này. Nhiều lần trả cộng dồn theo tỷ lệ
    # lũy kế; hai cột giúp lịch sử và retry không phải suy đoán lại.
    loyalty_points_restored = Column(Integer, nullable=False, default=0)
    loyalty_points_reversed = Column(Integer, nullable=False, default=0)

    order = relationship("Order", back_populates="returns")
    manager_approval = relationship("FnbManagerApproval")
    items = relationship("OrderReturnItem", back_populates="parent_return")

    @validates("refund_amount")
    def _mirror_refund_hint(self, _key, value):
        self.legacy_refund_amount = value
        return value


class OrderReturnItem(Base):
    """Một dòng hàng trong phiếu trả.

    `unit_price` và `cost_price` đều là ảnh chụp lấy từ `order_items` lúc trả,
    cùng lý do với ảnh chụp lúc bán: giá bán và giá vốn sau này đổi thì con số
    của lần trả này không được đổi theo.

    `restocked` phải là quyết định của TỪNG DÒNG. Áo khách mặc bẩn, sữa hết hạn
    hay hộp móp thì vẫn hoàn tiền nhưng KHÔNG được cộng lại vào tồn bán được -
    nếu không POS sẽ bán tiếp món đó cho người khác.
    """

    __tablename__ = "order_return_items"
    __table_args__ = (
        Index("ix_order_return_items_return_id", "return_id"),
        Index("ix_order_return_items_order_item_id", "order_item_id"),
    )
    id = Column(Integer, primary_key=True)
    return_id = Column(Integer, ForeignKey("order_returns.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    product_name = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False)
    legacy_unit_price = Column("unit_price", Float, nullable=False)
    unit_price = Column("unit_price_vnd", Integer, nullable=False, default=0)
    # Tiền hoàn của riêng dòng này sau khi phân bổ giảm giá. Tổng các dòng bằng
    # đúng `refund_amount` của phiếu.
    legacy_refund_amount = Column("refund_amount", Float, nullable=False, default=0)
    refund_amount = Column("refund_vnd", Integer, nullable=False, default=0)
    legacy_cost_price = Column("cost_price", Float, nullable=True)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    restocked = Column(Integer, nullable=False, default=1)

    parent_return = relationship("OrderReturn", back_populates="items")
    batch_allocations = relationship("OrderReturnItemBatch", back_populates="return_item")

    @validates("unit_price", "refund_amount")
    def _mirror_money_hints(self, key, value):
        if key == "unit_price":
            self.legacy_unit_price = value
        else:
            self.legacy_refund_amount = value
        return value

    @property
    def cost_price(self):
        known = int(self.cost_known_qty or 0)
        if known <= 0 or int(self.cost_unknown_qty or 0) > 0:
            return None
        return Decimal(int(self.cost_basis_vnd or 0)) / Decimal(known)


class OrderItemBatch(Base):
    """Dòng đơn này đã lấy bao nhiêu từ lô nào.

    Không có bảng này thì lúc khách trả hàng hoặc hủy đơn, hệ thống không biết
    nhập lại vào lô nào — đoán bừa là làm hỏng cả hạn sử dụng lẫn giá vốn. Một
    dòng đơn có thể ăn qua NHIỀU lô nên đây là quan hệ nhiều-nhiều thật sự.
    """

    __tablename__ = "order_item_batches"
    __table_args__ = (
        Index("ix_order_item_batches_order_item_id", "order_item_id"),
        Index("ix_order_item_batches_batch_id", "batch_id"),
    )
    id = Column(Integer, primary_key=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    # Ảnh chụp giá vốn của lô tại thời điểm xuất, cùng lý do với mọi ảnh chụp
    # khác trong dự án.
    legacy_cost_price = Column("cost_price", Float, nullable=True)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    returned_total_qty = Column(Integer, nullable=False, default=0)
    returned_known_qty = Column(Integer, nullable=False, default=0)
    returned_unknown_qty = Column(Integer, nullable=False, default=0)
    returned_cost_basis_vnd = Column(Integer, nullable=False, default=0)
    cost_return_version = Column(Integer, nullable=False, default=0)
    inventory_reversed = Column(Integer, nullable=False, default=0)
    inventory_reversal_version = Column(Integer, nullable=False, default=0)

    @property
    def cost_price(self):
        known = int(self.cost_known_qty or 0)
        if known <= 0 or int(self.cost_unknown_qty or 0) > 0:
            return None
        return Decimal(int(self.cost_basis_vnd or 0)) / Decimal(known)


class OfflineBatchStockDeficit(Base):
    """Durable evidence for the source-less tail of a tracked offline sale.

    ``deficit_quantity`` is immutable historical provenance for the outbound
    line. ``remaining_quantity`` is the part still represented by the deliberate
    Product.stock-vs-batches gap. A batch stocktake closes it without inventing
    a source batch; return/cancel therefore continue to fail closed for the
    original source-less quantity even after inventory reconciliation.
    """

    __tablename__ = "offline_batch_stock_deficits"
    __table_args__ = (
        Index(
            "ux_offline_batch_stock_deficits_order_item",
            "order_item_id",
            unique=True,
        ),
        Index(
            "ix_offline_batch_stock_deficits_product",
            "product_id",
        ),
    )
    id = Column(Integer, primary_key=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    deficit_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    resolution_kind = Column(String(32), nullable=True)
    state_version = Column(Integer, nullable=False, default=0)


class OrderReturnItemBatch(Base):
    """Return provenance pointing to one immutable outbound batch allocation."""

    __tablename__ = "order_return_item_batches"
    __table_args__ = (
        Index("ix_order_return_item_batches_return_item", "return_item_id"),
        Index("ix_order_return_item_batches_source", "source_order_item_batch_id"),
        Index(
            "ux_order_return_item_batches_event_source",
            "return_item_id",
            "source_order_item_batch_id",
            unique=True,
        ),
    )
    id = Column(Integer, primary_key=True)
    return_item_id = Column(Integer, ForeignKey("order_return_items.id"), nullable=False)
    source_order_item_batch_id = Column(
        Integer, ForeignKey("order_item_batches.id"), nullable=False
    )
    batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    cost_known_qty = Column(Integer, nullable=False, default=0)
    cost_unknown_qty = Column(Integer, nullable=False, default=0)
    cost_basis_vnd = Column(Integer, nullable=False, default=0)
    restocked = Column(Integer, nullable=False, default=1)

    return_item = relationship("OrderReturnItem", back_populates="batch_allocations")


class OrderPayment(Base):
    """Sổ bất biến của mọi khoản tiền vào và lần ghi nhận hoàn tiền.

    `bank_txn_id` chỉ để tra cứu và cố ý không unique. `idempotency_key` mới là
    khóa chống ngân hàng gửi lặp; các thao tác thủ công để NULL.
    """

    __tablename__ = "order_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_order_payments_amount_positive"),
        Index("ux_order_payments_idempotency_key", "idempotency_key", unique=True),
        # I10-A composite parent key for tenant-safe event/action payment FKs.
        Index(
            "ux_i10a_order_payments_id_order",
            "id",
            "order_id",
            unique=True,
        ),
        Index("ix_order_payments_order_id", "order_id"),
        Index("ix_order_payments_bank_txn_id", "bank_txn_id"),
    )

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    entry_type = Column(String(24), nullable=False)  # BANK_IN | CASH_TOPUP | REFUND_*
    legacy_amount = Column("amount", Float, nullable=False)
    amount = Column("amount_vnd", Integer, nullable=False)
    idempotency_key = Column(String(128), nullable=True)
    provider = Column(String(32), nullable=True)
    bank_txn_id = Column(String(128), nullable=True)
    account_no = Column(String(64), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Ca thực sự nhận/chi khoản tiền này. Có thể khác ca tạo đơn (ví dụ thu bù
    # hoặc hoàn tiền vào ngày sau), và nullable cho dữ liệu legacy/ngân hàng.
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)
    note = Column(String(500), nullable=True)
    reference = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    order = relationship("Order", back_populates="payments")
    shift = relationship("CashShift", back_populates="order_payments")

    @validates("amount")
    def _mirror_amount_hint(self, _key, value):
        # Released SQLite constraints still validate the legacy evidence
        # column.  Mirror the exact integer on write, but never read it back as
        # canonical money.
        self.legacy_amount = value
        return value
