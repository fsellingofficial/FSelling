from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..core.numeric_limits import MAX_SAFE_QUANTITY, MAX_SAFE_VND
from .money import ExactVND, SignedExactVND


class OrderItemCreate(BaseModel):
    """Một dòng hàng do client gửi lên.

    `product_id` là cách định danh chuẩn. `product_name` được giữ lại cho client
    cũ và chỉ dùng khi không có `product_id`: khớp theo tên không tin cậy vì tên
    có thể đổi, và trước đây hai sản phẩm trùng tên là gộp nhầm dòng.
    Phải có ít nhất một trong hai (kiểm ở `inventory_service`).

    `price` được nhận nhưng KHÔNG dùng để tính tiền - giá luôn lấy lại từ
    database, không tin giá client gửi.
    """

    product_id: Optional[int] = None
    product_name: Optional[str] = None
    price: ExactVND = Field(ge=0, le=MAX_SAFE_VND)
    # Service preserves the historical HTTP 400 contract for nonpositive and
    # oversized quantities after exact integer parsing.
    quantity: int


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    voucher_code: Optional[str] = None
    payment_method: str = "transfer"
    operation_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    # Gắn khách vào đơn (tùy chọn). Bỏ trống = khách vãng lai.
    customer_id: Optional[int] = None
    # Số điểm nguyên khách muốn dùng. Server đọc lại chương trình + số dư,
    # áp Voucher trước rồi mới tính phần giảm bằng điểm.
    loyalty_points_to_use: int = Field(default=0, ge=0, le=MAX_SAFE_QUANTITY)


class OfflineOrderItem(BaseModel):
    """Một dòng hàng trên phiếu đã bán khi mất mạng.

    `unit_price` ở đây KHÁC HẲN `price` của `OrderItemCreate`: chỗ kia bị bỏ đi
    và server tính lại từ database, còn chỗ này là **giá khách đã thật sự trả**
    và server phải tôn trọng. Tính lại theo giá hôm nay là ghi sai số tiền đã
    nằm trong két — xem bẫy 28 trong KIEN_TRUC.md.

    `product_name` là bản chụp tên lúc bán, dùng khi sản phẩm đã bị xóa giữa
    lúc bán và lúc đồng bộ: mất tên thì dòng tiền đó không còn tra được về đâu.
    """

    product_id: int
    product_name: str = Field(min_length=1, max_length=300)
    unit_price: ExactVND = Field(ge=0, le=MAX_SAFE_VND)
    quantity: int = Field(gt=0, le=MAX_SAFE_QUANTITY)


class OfflineOrderCreate(BaseModel):
    """Phiếu bán offline gửi lên khi máy có mạng trở lại.

    CỐ Ý không có `voucher_code`, `customer_id` hay `payment_method`: khi mất
    mạng chỉ bán được TIỀN MẶT. Voucher cần đếm lượt dùng trên server, ghi nợ
    cần kiểm hạn mức trên server — cả hai không kiểm được lúc offline, và đoán
    bừa thì hậu quả là tiền.
    """

    offline_uuid: str = Field(min_length=8, max_length=64)
    # Giờ bán theo UTC. Server dùng nó để chọn ca thu ngân, KHÔNG dùng giờ sync.
    sold_at: datetime
    items: List[OfflineOrderItem] = Field(min_length=1)
    # Tiền khách đưa. Nhỏ hơn tổng đơn là phiếu sai, server từ chối.
    cash_tendered: ExactVND = Field(ge=0, le=MAX_SAFE_VND)
    device_label: Optional[str] = Field(default=None, max_length=64)


class OfflineIssueAcknowledge(BaseModel):
    """Chủ shop xác nhận đã xem một vướng mắc offline.

    `state_version` là phiên bản mà máy khách đang nhìn thấy. Gửi kèm để hai
    người cùng mở màn Đối Soát không ghi đè quyết định của nhau: bản cũ bị từ
    chối 409 chứ không âm thầm thắng.
    """

    # Trimmed và bắt buộc không rỗng - service kiểm lại trước mọi side effect.
    reason: str = Field(min_length=1, max_length=500)
    state_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)


class PaymentWebhook(BaseModel):
    order_id: int
    status: Optional[str] = "PAID"
    transaction_id: Optional[str] = None
    amount: Optional[ExactVND] = Field(default=None, ge=0, le=MAX_SAFE_VND)


class CashTopup(BaseModel):
    """Khoản tiền mặt bù cho đơn chuyển thiếu.

    Server luôn thu đúng toàn bộ số còn thiếu. `amount` được giữ để client hiện
    tại gửi con số đang thấy, nhưng phải khớp phần thiếu tại lúc xử lý.
    """

    amount: Optional[SignedExactVND] = Field(default=None, le=MAX_SAFE_VND)
    note: Optional[str] = None


class CashPayment(BaseModel):
    """Tiền khách thực đưa khi thanh toán một đơn tiền mặt.

    Server tự tính tiền thừa từ tổng đơn đã chốt trong DB; client không được
    gửi hay tự quyết định số tiền phải trả lại.
    """

    tendered_amount: ExactVND = Field(ge=0, le=MAX_SAFE_VND)


class OrderReturnItemCreate(BaseModel):
    """Một dòng khách mang trả.

    Định danh bằng `order_item_id` chứ không phải `product_id`: dòng đơn mới là
    thứ giữ giá bán và giá vốn đã chốt lúc bán, và là mốc để biết còn được trả
    bao nhiêu.
    """

    order_item_id: int
    quantity: int
    # Hàng còn tốt thì cộng lại tồn kho; hàng hỏng/bẩn/hết hạn thì vẫn hoàn tiền
    # nhưng KHÔNG được quay lại kệ.
    restock: bool


class OrderReturnDraft(BaseModel):
    """Một lần nhận hàng trả. Server tự tính tiền hoàn, client không gửi số tiền.

    `method` được phép bỏ trống khi tiền hoàn bằng 0 (đơn giảm giá 100%).
    """

    items: List[OrderReturnItemCreate]
    method: Optional[Literal["cash", "transfer"]] = None
    reason: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=128)
    # Một id cho đúng MỘT lần bấm nhận trả. Retry mạng dùng lại id này nên không
    # thể vô tình tạo hai phiếu trả cho cùng một lần khách mang hàng đến.
    operation_id: str = Field(min_length=8, max_length=128)


class OrderReturnCreate(OrderReturnDraft):
    approval_token: Optional[str] = Field(default=None, min_length=32, max_length=256)


class OrderReturnApprovalCreate(OrderReturnDraft):
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approver_username: str = Field(min_length=1, max_length=100)
    pin: str = Field(pattern=r"^\d{4,6}$")


class DebtPayment(BaseModel):
    """Một lần khách trả bớt nợ. Trả bao nhiêu cũng được, nhiều lần cũng được.

    Khác `CashTopup` ở chỗ đó: `CashTopup` bắt trả trọn phần còn thiếu vì nó
    dành cho đơn chuyển khoản thiếu, còn trả nợ dần là chuyện bình thường của
    bán ghi sổ.
    """

    # Service owns the historical HTTP 400 response for zero/negative debt
    # payments.  Keep exact VND parsing at the HTTP boundary first.
    amount: SignedExactVND = Field(ge=-MAX_SAFE_VND, le=MAX_SAFE_VND)
    method: Literal["cash", "transfer"]
    note: Optional[str] = None
    reference: Optional[str] = None
    # Một id cho đúng MỘT lần bấm thu tiền. Retry mạng dùng lại id này nên không
    # ghi thành hai lần trả.
    operation_id: str = Field(min_length=8, max_length=128)


class RefundComplete(BaseModel):
    """Ghi nhận shop đã hoàn đúng toàn bộ khoản đang chờ.

    Không nhận số tiền từ client: server khóa theo `refund_due_amount`.
    """

    method: Literal["cash", "transfer"]
    note: Optional[str] = None
    reference: Optional[str] = None
    # Một id cho đúng MỘT lần bấm hoàn. Retry mạng dùng lại id này nên không thể
    # vô tình xác nhận hộ một khoản dư mới xuất hiện sau đó.
    operation_id: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Offline contract v1 schemas (I09-E+B2)
# ---------------------------------------------------------------------------


class OfflineOrderItemV1(BaseModel):
    """Một dòng hàng trong phiếu offline contract v1."""

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(strict=True, ge=1, le=MAX_SAFE_QUANTITY)
    # Canonical layer applies the 300-code-point / 900-byte limits after NFC
    # and whitespace collapse; a raw decomposed form may legitimately be longer.
    product_name: str = Field(min_length=1)
    unit_price_vnd: int = Field(strict=True, ge=0, le=MAX_SAFE_VND)
    quantity: int = Field(strict=True, ge=1, le=MAX_SAFE_QUANTITY)


class OfflineOrderCreateV1(BaseModel):
    """Phiếu bán offline contract v1 — server-time/lease-backed.

    Token chỉ ở header `X-Offline-Lease-Token`, tuyệt đối không body/query/fingerprint.
    """

    model_config = ConfigDict(extra="forbid")

    offline_contract_version: int = Field(strict=True, ge=1, le=1)
    lease_id: str = Field(min_length=26, max_length=26)
    device_id: str = Field(min_length=1, max_length=128)
    offline_session_id: str = Field(min_length=26, max_length=26)
    sequence: int = Field(strict=True, ge=1, le=MAX_SAFE_QUANTITY)
    offline_uuid: str = Field(min_length=8, max_length=64)
    # Accept an ISO-8601 wall time with or without an offset.  The fingerprint
    # layer converts the instant to fixed-width UTC-naive text before any
    # duplicate decision or persistence.
    sold_at_client_utc: str = Field(min_length=19, max_length=64)
    client_monotonic_ms: int = Field(strict=True, ge=0, le=MAX_SAFE_QUANTITY)
    monotonic_valid: bool = Field(strict=True)
    server_anchor_id: str = Field(min_length=1, max_length=64)
    catalog_version: int = Field(strict=True, ge=0, le=MAX_SAFE_QUANTITY)
    catalog_snapshot_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    client_fingerprint: str = Field(
        min_length=71, max_length=71, pattern=r"^fsofr1:[0-9a-f]{64}$"
    )
    items: List[OfflineOrderItemV1] = Field(min_length=1, max_length=200)
    cash_tendered: int = Field(strict=True, ge=0, le=MAX_SAFE_VND)


class OfflineOrderSyncResponseV1(BaseModel):
    """Response cho offline contract v1 ingest."""

    contract_version: int
    order_id: Optional[int] = None
    offline_uuid: str
    created: bool
    sold_by_user_id: int
    synced_by_user_id: int
    sold_at_effective: str
    time_confidence: str
    server_time_utc: str
