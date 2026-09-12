"""Nghiệp vụ đơn hàng: tạo đơn (giá từ DB), tra cứu, xác nhận thanh toán, webhook."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import bindparam, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..core.i18n import tr
from ..core.money import checked_add, checked_vnd, exact_vnd, largest_remainder_allocate
from ..dependencies import (
    PERMISSION_RECONCILIATION,
    PERMISSION_SALE,
    STAFF_ROLE_CASHIER,
    effective_staff_role,
    has_shop_operator_access,
    require_shop_access,
    require_staff_permission,
)
from ..schemas.order import (
    CashPayment,
    CashTopup,
    DebtPayment,
    OrderCreate,
    RefundComplete,
)
from . import (
    auth_session_service,
    inventory_service,
    loyalty_service,
    payment_service,
    qr_sales_service,
    voucher_service,
)
from .log_service import log_system_action


# --- Máy trạng thái đơn hàng ---
# PENDING ------> PAID          (tiền mặt | webhook đủ tiền)
# PENDING ------> CANCELLED     (hủy đơn - A1d)
# DEBT ---------> PAID          (F4: khách trả đủ nợ, có thể qua nhiều lần trả)
# DEBT ---------> CANCELLED     (F4: hủy đơn nợ CHƯA thu đồng nào)
# CANCELLED ----> UNRECONCILED  (CHỈ webhook: tiền về sau khi đơn đã hủy)
# UNRECONCILED -> PAID          (webhook cộng dồn đủ | bù tiền mặt phần thiếu)
# PAID là trạng thái cuối. Mọi đường khác đều bị từ chối.
STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_CANCELLED = "CANCELLED"
STATUS_UNRECONCILED = "UNRECONCILED"
# F4: bán ghi nợ. CỐ Ý là một trạng thái riêng chứ không dùng lại PENDING, vì
# PENDING đang mang nghĩa "đang chờ khách trả tiền ngay bây giờ" và có hai cỗ
# máy bám vào nghĩa đó:
#   - `cancel_expired_pending_orders` hủy mọi đơn PENDING quá hạn và hoàn tồn
#     kho -> để đơn nợ ở PENDING là một ngày nào đó sổ nợ bị xóa sạch và số hàng
#     khách đã cầm về được cộng trả vào kho.
#   - `close_shift` không cho đóng ca khi còn đơn PENDING tiền mặt -> thu ngân
#     sẽ không bao giờ kết ca được, vì đơn nợ treo hàng tuần là chuyện bình thường.
# Cả hai chỗ đó lọc đúng chuỗi "PENDING" nên trạng thái riêng tự tránh được.
STATUS_DEBT = "DEBT"

HISTORY_PAGE_SIZE = 20
HISTORY_SCOPES = frozenset({"today", "7d"})
LOCAL_UTC_OFFSET = timedelta(hours=7)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _mask_customer_phone(value: Optional[str]) -> Optional[str]:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if not digits:
        return None
    if len(digits) < 7:
        return "***"
    return f"{digits[:3]} *** {digits[-4:]}"


def _history_bounds_utc(scope: str, now_utc: datetime) -> Tuple[datetime, datetime]:
    local_now = now_utc + LOCAL_UTC_OFFSET
    days_back = 0 if scope == "today" else 6
    local_start = datetime.combine(
        local_now.date() - timedelta(days=days_back), time.min
    )
    local_end = datetime.combine(local_now.date() + timedelta(days=1), time.min)
    return (
        local_start - LOCAL_UTC_OFFSET,
        local_end - LOCAL_UTC_OFFSET,
    )

MANUAL_PAY_FROM: Tuple[str, ...] = (STATUS_PENDING,)
# Trạng thái mà webhook ngân hàng ĐƯỢC PHÉP đụng vào.
#
# Trước F6 hằng số này chỉ có `PENDING` và **không chỗ nào đọc nó** - trông như
# một ràng buộc đang có hiệu lực trong khi thực tế webhook nhận mọi trạng thái.
# Hậu quả thật: chuyển khoản 40k cho đơn nợ 100k đẩy đơn từ DEBT sang
# UNRECONCILED/UNDERPAID, mà `receivable_amount` lọc đúng chuỗi "DEBT" nên 60k
# khách còn nợ biến mất khỏi sổ. Nay hằng số được kiểm thật trong
# `apply_webhook_payment`, và phải liệt kê ĐỦ các trạng thái mà máy trạng thái ở
# `_apply_bank_transaction` vốn xử lý đúng - liệt kê thiếu là chặn nhầm những
# đường đang chạy tốt:
WEBHOOK_PAY_FROM: Tuple[str, ...] = (
    STATUS_PENDING,        # ca thường: khách chuyển cho đơn vừa tạo
    STATUS_UNRECONCILED,   # chuyển thêm cho đơn còn thiếu -> đủ thì PAID
    STATUS_CANCELLED,      # tiền về sau khi hủy -> LATE_PAYMENT, KHÔNG hồi sinh
    STATUS_PAID,           # chuyển trùng -> OVERPAID, mở khoản chờ hoàn
)
CANCEL_FROM: Tuple[str, ...] = (STATUS_PENDING, STATUS_DEBT)
DEBT_PAY_FROM: Tuple[str, ...] = (STATUS_DEBT,)

PAYMENT_METHOD_TRANSFER = "transfer"
PAYMENT_METHOD_CASH = "cash"
PAYMENT_METHOD_DEBT = "debt"
# Trước F4 trường này KHÔNG được kiểm gì cả: client gửi chuỗi nào cũng lưu
# nguyên vào DB. Giờ có hình thức "ghi nợ" mang hệ quả tài chính thật thì bắt
# buộc phải chốt danh sách, nếu không client tự khai khống được.
PAYMENT_METHODS = frozenset(
    {PAYMENT_METHOD_TRANSFER, PAYMENT_METHOD_CASH, PAYMENT_METHOD_DEBT}
)

ENTRY_DEBT_CASH = "DEBT_CASH"
ENTRY_DEBT_TRANSFER = "DEBT_TRANSFER"

RECON_UNDERPAID = "UNDERPAID"
RECON_OVERPAID = "OVERPAID"
RECON_LATE_PAYMENT = "LATE_PAYMENT"
RECON_LEGACY_REVIEW = "LEGACY_REVIEW"

ENTRY_BANK = "BANK_IN"
# Tiền ĐÃ về tài khoản nhưng webhook KHÔNG ghi vào đơn (đơn đang ở trạng thái
# ngoài `WEBHOOK_PAY_FROM`, thực tế là đơn ghi nợ). Ghi lại để người bán nhìn
# thấy trên màn Đối Soát - trước đó khoản này chỉ nằm trong `SystemLog` nên tiền
# về mà không ai biết.
#
# Bút toán này KHÔNG phải một khoản thu: không cộng vào `paid_amount`, không đổi
# trạng thái, không gắn `shift_id`. Nó nằm ngoài mọi danh sách cộng tiền
# (`CASH_PAYMENT_IN_TYPES` / `CASH_PAYMENT_OUT_TYPES` của shift_service liệt kê
# tường minh) nên không có đường nào cộng nhầm nó vào két hay vào doanh thu.
ENTRY_BANK_UNAPPLIED = "BANK_UNAPPLIED"
ENTRY_CASH = "CASH_TOPUP"
ENTRY_REFUND_CASH = "REFUND_CASH"
ENTRY_REFUND_TRANSFER = "REFUND_TRANSFER"

MONEY_EPSILON = 0

_UPDATE_STATUS = (
    text(
        "UPDATE orders SET status = :to_state "
        "WHERE id = :order_id AND status IN :from_states"
    ).bindparams(bindparam("from_states", expanding=True))
)


def _so_tien(value: Any) -> int:
    """Giá trị tiền an toàn cho dữ liệu cũ có thể NULL."""
    return int(value or 0)


def payment_summary(order: models.Order) -> Dict[str, Any]:
    """Một nguồn dữ liệu thống nhất cho polling POS, hóa đơn và dashboard."""
    bank = _so_tien(order.paid_amount)
    cash = _so_tien(order.cash_paid_amount)
    received = bank + cash
    total = _so_tien(order.total_amount)
    late = order.reconciliation_reason == RECON_LATE_PAYMENT
    remaining = 0 if late else max(total - received, 0)
    overpaid = received if late else max(received - total, 0)
    refund_due = max(_so_tien(order.refund_due_amount), 0)
    refund_pending = refund_due > MONEY_EPSILON and order.refund_completed_at is None
    return {
        "bank_paid_amount": bank,
        "cash_paid_amount": cash,
        "received_amount": received,
        "remaining_amount": remaining,
        "overpaid_amount": overpaid,
        "refunded_amount": _so_tien(order.refunded_amount),
        "refund_due_amount": refund_due,
        "refund_pending": refund_pending,
        "refund_completed_at": order.refund_completed_at,
        "refund_completed_by": order.refund_completed_by,
        "refund_method": order.refund_method,
        "refund_note": order.refund_note,
        "refund_reference": order.refund_reference,
        "reconciliation_reason": order.reconciliation_reason,
        "reconciliation_pending": (
            order.status == STATUS_UNRECONCILED or refund_pending
        ),
        "invoice_issued": order.status == STATUS_PAID,
    }


def _them_nhat_ky(
    db: Session,
    user_id: Optional[int],
    action: str,
    details: str,
    *,
    shop_id: Optional[int] = None,
) -> None:
    """Thêm audit vào transaction hiện tại, KHÔNG tự commit."""
    db.add(
        models.SystemLog(
            user_id=user_id,
            shop_id=shop_id,
            auth_session_id=db.info.get("auth_session_id"),
            action=action,
            details=details,
        )
    )


def _serialize_payment(payment: models.OrderPayment) -> Dict[str, Any]:
    return {
        "id": payment.id,
        "entry_type": payment.entry_type,
        "amount": payment.amount,
        "provider": payment.provider,
        "bank_txn_id": payment.bank_txn_id,
        "account_no": payment.account_no,
        "created_by_user_id": payment.created_by_user_id,
        "shift_id": payment.shift_id,
        "note": payment.note,
        "reference": payment.reference,
        "created_at": payment.created_at,
    }


def _current_cash_shift(
    db: Session,
    current_user: models.User,
    shop_id: int,
    *,
    required_for_cashier: bool = False,
    required_for_everyone: bool = False,
    lock_for_cash_write: bool = False,
) -> Optional[models.CashShift]:
    """Ca OPEN của chính người đang thao tác trong shop.

    `/pay` cũ vẫn cho chủ shop/MANAGER thu tiền không qua ca để không phá
    client cũ; riêng CASHIER luôn phải mở ca. Các nghiệp vụ đối soát tiền mặt
    mới dùng ``required_for_everyone`` để mọi khoản thu/hoàn đều vào đúng két.
    """
    shift = (
        db.query(models.CashShift)
        .filter(
            models.CashShift.shop_id == shop_id,
            models.CashShift.opened_by_user_id == current_user.id,
            models.CashShift.status == "OPEN",
        )
        .order_by(models.CashShift.id.desc())
        .first()
    )
    cashier_must_open = (
        required_for_cashier
        and current_user.role == "STAFF"
        and effective_staff_role(current_user) == STAFF_ROLE_CASHIER
    )
    if shift is None and (required_for_everyone or cashier_must_open):
        raise HTTPException(
            status_code=409,
            detail=tr("Hãy mở ca của bạn tại POS trước khi ghi nhận tiền mặt"),
        )
    if shift is not None and lock_for_cash_write:
        # SQLite không có SELECT FOR UPDATE. No-op UPDATE lấy write lock và
        # đồng thời xác nhận ca vẫn OPEN; close_shift dùng đúng hàng rào này.
        # Nhờ vậy kết ca không thể chụp expected rồi một payment đến muộn lại
        # gắn vào chính ca CLOSED đó.
        locked = db.execute(
            text(
                "UPDATE cash_shifts SET status = status "
                "WHERE id = :shift_id AND status = 'OPEN'"
            ),
            {"shift_id": shift.id},
        )
        if locked.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr("Ca vừa được đóng; vui lòng tải lại và mở ca mới"),
            )
    if lock_for_cash_write:
        # Also fence legacy owner/manager payments allowed without a shift.
        auth_session_service.fence_live_auth_session(db)
    return shift


def read_status(db: Session, order_id: int) -> Optional[str]:
    """Đọc trạng thái hiện tại từ DB. None nếu đơn không tồn tại."""
    return db.execute(
        text("SELECT status FROM orders WHERE id = :order_id"), {"order_id": order_id}
    ).scalar()


def apply_transition(
    db: Session, order_id: int, from_states: Tuple[str, ...], to_state: str
) -> bool:
    """Như `transition_status` nhưng KHÔNG commit.

    Dùng khi việc chuyển trạng thái phải nằm chung một transaction với các
    tác dụng phụ (hoàn kho, hoàn lượt voucher) - hoặc cùng thành công, hoặc
    cùng không có gì xảy ra.
    """
    auth_session_service.fence_live_auth_session(db)
    result = db.execute(
        _UPDATE_STATUS,
        {"to_state": to_state, "order_id": order_id, "from_states": list(from_states)},
    )
    return result.rowcount == 1


def transition_status(
    db: Session, order_id: int, from_states: Tuple[str, ...], to_state: str
) -> bool:
    """Chuyển trạng thái bằng UPDATE có điều kiện.

    Trả True chỉ khi CHÍNH lời gọi này thực hiện được việc chuyển. Đọc-rồi-ghi
    sẽ bị race giữa hủy đơn / xác nhận thủ công / webhook chạy song song; ở đây
    DB tự quyết ai thắng, kẻ thua nhận False và KHÔNG được làm tác dụng phụ
    (hoàn kho, hoàn lượt voucher, ghi log thanh toán).
    """
    changed = apply_transition(db, order_id, from_states, to_state)
    db.commit()
    return changed


def _order_operation_fingerprint(order: OrderCreate) -> str:
    """Dấu vân tay phần request có ý nghĩa nghiệp vụ, bỏ giá client gửi."""
    items = sorted(
        [
            {
                "product_id": item.product_id,
                "product_name": (item.product_name or "").strip() or None,
                "quantity": item.quantity,
            }
            for item in order.items
        ],
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    payload = {
        "items": items,
        "voucher_code": (order.voucher_code or "").strip().upper() or None,
        "payment_method": order.payment_method,
        "customer_id": order.customer_id,
        "loyalty_points_to_use": int(order.loyalty_points_to_use or 0),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _create_order_response(
    db: Session, shop: models.Shop, existing: models.Order
) -> Dict[str, Any]:
    discount = _so_tien(existing.discount_amount)
    loyalty_discount = _so_tien(existing.loyalty_discount_amount)
    total = _so_tien(existing.total_amount)
    loyalty_balance = (
        loyalty_service.balance_for_customer(
            db, existing.customer_id, shop_id=existing.shop_id
        )
        if existing.customer_id is not None
        else 0
    )
    qr_intent = qr_sales_service.metadata_for_order(db, existing)
    response = {
        "order_id": existing.id,
        "status": existing.status,
        "subtotal": total + discount + loyalty_discount,
        "discount": discount,
        "loyalty_discount": loyalty_discount,
        "loyalty_points_redeemed": int(existing.loyalty_points_redeemed or 0),
        "loyalty_points_earned": int(existing.loyalty_points_earned or 0),
        "loyalty_balance": loyalty_balance,
        "total": total,
        # Contract v0/OFF stays byte-for-byte compatible.  A v1 intent never
        # exposes a browser/provider URL: I10-D will fetch its authenticated
        # same-origin render endpoint as a blob.
        "qr_url": (
            None
            if (
                qr_intent is not None
                or existing.payment_method != PAYMENT_METHOD_TRANSFER
                or not payment_service.has_transfer_account(shop)
            )
            else payment_service.build_qr_url(shop, total, existing.id)
        ),
    }
    if qr_intent is not None:
        response["qr_intent"] = qr_intent
    return response


def _existing_operation_order(
    db: Session,
    shop: models.Shop,
    current_user: models.User,
    operation_id: str,
    fingerprint: str,
) -> Optional[Dict[str, Any]]:
    existing = (
        db.query(models.Order)
        .filter(models.Order.operation_id == operation_id)
        .first()
    )
    if existing is None:
        return None
    if (
        existing.shop_id != shop.id
        or existing.created_by_user_id != current_user.id
        or existing.operation_fingerprint != fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail=tr("Mã retry tạo đơn đã được dùng cho một đơn khác"),
        )
    return _create_order_response(db, shop, existing)


def cong_no_cua_khach(db: Session, customer_id: int) -> int:
    """Tổng tiền khách còn nợ: phần chưa trả của mọi đơn đang ở trạng thái DEBT.

    Tính từ chính các đơn chứ không giữ một cột "tổng nợ" trên `customers`: cột
    tổng như vậy là nguồn sự thật thứ hai, và chỉ cần một đường ghi quên cập
    nhật là sổ nợ lệch mà không ai biết cho tới lúc đối chiếu với khách.
    """
    don_no = (
        db.query(models.Order)
        .filter(
            models.Order.customer_id == customer_id,
            models.Order.status == STATUS_DEBT,
        )
        .all()
    )
    tong = 0
    for o in don_no:
        da_tra = _so_tien(o.paid_amount) + _so_tien(o.cash_paid_amount)
        tong = checked_add(tong, max(_so_tien(o.total_amount) - da_tra, 0))
    return tong


def _kiem_ban_ghi_no(
    db: Session, khach: Optional[models.Customer], tong_don: int
) -> None:
    """Hai điều kiện để được ghi nợ, kiểm ngay trước khi tạo đơn."""
    if khach is None:
        raise HTTPException(
            status_code=400,
            detail=tr("Bán ghi nợ phải chọn khách hàng"),
        )
    if khach.credit_limit is None:
        return
    dang_no = cong_no_cua_khach(db, khach.id)
    if dang_no + tong_don > _so_tien(khach.credit_limit) + MONEY_EPSILON:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Khách '{name}' đang nợ {current} và hạn mức là {limit}; "
                "đơn này {amount} sẽ vượt hạn mức",
                name=khach.name,
                current=f"{dang_no:,.0f}đ",
                limit=f"{_so_tien(khach.credit_limit):,.0f}đ",
                amount=f"{tong_don:,.0f}đ",
            ),
        )


def _lock_shop_for_order(db: Session, shop_id: int) -> None:
    """Tuần tự hóa phần kiểm tồn/voucher và ghi đơn trong cùng một shop.

    SQLite không có ``SELECT FOR UPDATE``. No-op UPDATE này lấy write lock
    trước khi đọc tồn kho và số lượt voucher; transaction giữ lock tới commit.
    Nếu sau này đổi database có row lock, chính hàng shop này vẫn là hàng rào
    chung cho mọi thu ngân của cùng cửa hàng.
    """
    locked = db.execute(
        text("UPDATE shops SET id = id WHERE id = :shop_id"),
        {"shop_id": shop_id},
    )
    if locked.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    auth_session_service.fence_live_auth_session(db)


def _award_loyalty_paid_order(
    db: Session,
    order: models.Order,
    created_by_user_id: Optional[int],
) -> int:
    """Chốt điểm đúng một lần khi đơn lần đầu trở thành PAID.

    Tỷ lệ cộng lấy từ ảnh chụp lúc tạo đơn, nhưng chương trình phải vẫn đang
    bật tại lúc khách trả đủ. Kể cả kết quả là 0, ``loyalty_awarded_at`` vẫn
    được ghi để retry sau khi bật lại không cộng hồi tố.
    """
    if order.loyalty_awarded_at is not None:
        return int(order.loyalty_points_earned or 0)

    order.loyalty_awarded_at = datetime.utcnow()
    order.loyalty_points_earned = 0
    if (
        order.customer_id is None
        or order.loyalty_earn_amount_step is None
        or order.loyalty_earn_points_step is None
    ):
        return 0

    current_program = loyalty_service.get_program_model(db, order.shop_id)
    if current_program is None or not current_program.enabled:
        return 0

    snapshot = {
        "enabled": True,
        "earn_amount": order.loyalty_earn_amount_step,
        "earn_points": order.loyalty_earn_points_step,
    }
    points = loyalty_service.calculate_earn(snapshot, _so_tien(order.total_amount))
    order.loyalty_points_earned = points
    if points > 0:
        loyalty_service.add_entry(
            db,
            order.shop_id,
            order.customer_id,
            loyalty_service.ENTRY_EARN,
            points,
            f"earn:order:{order.id}",
            order_id=order.id,
            created_by_user_id=created_by_user_id,
            note=f"Cộng điểm khi đơn #{order.id} đã thanh toán đủ",
            expiry_days=order.loyalty_expiry_days_snapshot,
        )
    return points


def create_order(
    db: Session, current_user: models.User, shop_id: int, order: OrderCreate
) -> Dict[str, Any]:
    # Yêu cầu đăng nhập và chỉ chủ shop (hoặc admin) mới được tạo đơn cho shop này.
    shop = require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    if not order.items:
        raise HTTPException(
            status_code=400,
            detail=tr("Đơn hàng không có sản phẩm nào"),
        )
    if order.payment_method not in PAYMENT_METHODS:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Hình thức thanh toán không hợp lệ",
            ),
        )
    operation_id = (order.operation_id or "").strip() or None
    operation_fingerprint = _order_operation_fingerprint(order)
    if operation_id:
        existing_response = _existing_operation_order(
            db,
            shop,
            current_user,
            operation_id,
            operation_fingerprint,
        )
        if existing_response is not None:
            return existing_response

    # Mọi phép đọc có thể quyết định việc ghi (tồn kho, lượt voucher) phải nằm
    # sau cùng một write lock. Hai cashier có ca khác nhau không thể chỉ dựa
    # vào shift lock vì khi đó cả hai đã kịp đọc cùng snapshot tồn/lượt dùng.
    _lock_shop_for_order(db, shop_id)
    # ``require_shop_access`` may have populated the identity map before this
    # transaction obtained the shop write lock.  Refresh under that same lock
    # so the immutable bank snapshot is coherent with serialized account
    # updates.  Blocking account changes for unresolved intents is I10-C.
    db.refresh(shop)

    # Một retry có thể đã hoàn tất trong lúc request này chờ shop lock. Kiểm
    # lại ngay sau lock để không resolve/trừ kho/tăng voucher lần thứ hai.
    if operation_id:
        existing_response = _existing_operation_order(
            db,
            shop,
            current_user,
            operation_id,
            operation_fingerprint,
        )
        if existing_response is not None:
            # Chỉ có no-op UPDATE ở transaction này. Response đã là dict nên
            # rollback an toàn và nhả write lock ngay, không chờ dependency
            # đóng Session sau khi FastAPI dựng xong response.
            db.rollback()
            return existing_response

    if order.payment_method == PAYMENT_METHOD_TRANSFER:
        payment_service.require_transfer_account(shop)

    # Tính tiền TỪ DB, không tin giá client gửi.
    wanted = inventory_service.collect_quantities(order.items)
    resolved_items, subtotal = inventory_service.resolve_items(db, shop_id, wanted)

    applied_voucher, discount_amount = voucher_service.resolve_for_order(
        db, shop_id, order.voucher_code, subtotal
    )

    amount_after_voucher = max(subtotal - discount_amount, 0)
    current_shift = _current_cash_shift(
        db,
        current_user,
        shop_id,
        lock_for_cash_write=True,
    )

    # Khách hàng gắn vào đơn (tùy chọn, TRỪ đơn ghi nợ). Phải là khách của ĐÚNG
    # shop này - không cho mượn customer_id của shop khác.
    customer_id = None
    khach = None
    if order.customer_id is not None:
        khach = (
            db.query(models.Customer)
            .filter(
                models.Customer.id == order.customer_id,
                models.Customer.shop_id == shop_id,
            )
            .first()
        )
        if not khach:
            raise HTTPException(
                status_code=404,
                detail=tr("Khách hàng không tồn tại trong cửa hàng này"),
            )
        if not khach.is_active:
            raise HTTPException(
                status_code=400,
                detail=tr("Khách hàng này đã ngừng sử dụng"),
            )
        customer_id = khach.id

    loyalty_program = loyalty_service.get_program_model(db, shop_id)
    loyalty_points = 0
    loyalty_discount = 0
    # Cùng một mốc cho cả kiểm số dư và bút toán dùng điểm. Nếu gọi utcnow hai
    # lần, một lô có thể hết hạn ở giữa: khách vẫn được giảm tiền nhưng ledger
    # lại coi số điểm đó đã hết hạn và biến thành nợ âm.
    loyalty_event_at = datetime.utcnow()
    earn_amount_snapshot = None
    earn_points_snapshot = None
    expiry_days_snapshot = None
    if khach is not None and loyalty_program is not None and loyalty_program.enabled:
        earn_amount_snapshot = loyalty_program.earn_amount
        earn_points_snapshot = loyalty_program.earn_points
        expiry_days_snapshot = loyalty_program.expiry_days

    points_requested = int(order.loyalty_points_to_use or 0)
    if points_requested > 0:
        if khach is None:
            raise HTTPException(
                status_code=400,
                detail=tr("Phải chọn khách hàng trước khi dùng điểm"),
            )
        balance = loyalty_service.balance_for_customer(
            db,
            khach.id,
            as_of=loyalty_event_at,
            shop_id=shop_id,
        )
        redeemed = loyalty_service.calculate_redeem(
            loyalty_program,
            balance,
            points_requested,
            amount_after_voucher,
        )
        loyalty_points = int(redeemed["applied_points"])
        loyalty_discount = _so_tien(redeemed["discount"])

    try:
        total = checked_vnd(max(amount_after_voucher - loyalty_discount, 0))
    except ValueError:
        raise HTTPException(status_code=400, detail=tr("Tổng tiền đơn vượt giới hạn"))

    ghi_no = order.payment_method == PAYMENT_METHOD_DEBT
    if ghi_no:
        # Nợ mà không biết ai nợ thì không đòi được. Kiểm hạn mức nằm sau
        # `_lock_shop_for_order` nên hai đơn nợ cùng lúc của một khách không thể
        # cùng nhìn thấy một số dư cũ rồi cùng lọt qua.
        _kiem_ban_ghi_no(db, khach, total)

    new_order = models.Order(
        shop_id=shop_id,
        created_by_user_id=current_user.id,
        shift_id=current_shift.id if current_shift else None,
        operation_id=operation_id,
        operation_fingerprint=operation_fingerprint if operation_id else None,
        total_amount=total,
        discount_amount=discount_amount,
        loyalty_points_redeemed=loyalty_points,
        loyalty_discount_amount=loyalty_discount,
        loyalty_earn_amount_step=earn_amount_snapshot,
        loyalty_earn_points_step=earn_points_snapshot,
        loyalty_expiry_days_snapshot=expiry_days_snapshot,
        voucher_code=order.voucher_code,
        payment_method=order.payment_method,
        customer_id=customer_id,
        # Đơn được giảm về 0đ không có giao dịch ngân hàng dương để chờ.
        status=(
            STATUS_PAID
            if total == 0
            else (STATUS_DEBT if ghi_no else STATUS_PENDING)
        ),
    )
    db.add(new_order)
    try:
        db.flush()  # lấy id mà chưa commit, cùng một transaction
    except IntegrityError:
        db.rollback()
        if operation_id:
            existing_response = _existing_operation_order(
                db,
                shop,
                current_user,
                operation_id,
                operation_fingerprint,
            )
            if existing_response is not None:
                return existing_response
        raise

    if loyalty_points > 0:
        loyalty_service.add_entry(
            db,
            shop_id,
            customer_id,
            loyalty_service.ENTRY_REDEEM,
            -loyalty_points,
            f"redeem:order:{new_order.id}",
            order_id=new_order.id,
            created_by_user_id=current_user.id,
            note=f"Giữ điểm để dùng cho đơn #{new_order.id}",
            created_at=loyalty_event_at,
        )

    # Trừ kho TRƯỚC khi dựng dòng đơn: với sản phẩm theo lô, giá vốn của dòng
    # phải lấy từ đúng các lô vừa xuất, nên phải biết đã lấy lô nào rồi mới ghi
    # được dòng.
    lo_da_lay = inventory_service.deduct_stock(db, resolved_items)

    created_lines = []
    for prod, qty in resolved_items:
        da_lay = lo_da_lay.get(prod.id) or []
        known_qty, unknown_qty, cost_basis_vnd = inventory_service.allocation_totals(da_lay)
        dong = models.OrderItem(
                order_id=new_order.id,
                # Ghi kèm product_id để hoàn tồn kho chính xác khi hủy đơn (A1d).
                # product_name vẫn được giữ: nó là ảnh chụp tên tại thời điểm bán,
                # dùng cho hóa đơn và báo cáo kể cả khi sản phẩm sau này bị đổi tên/xóa.
                product_id=prod.id,
                product_name=prod.name,
                price=prod.price,
                # Ảnh chụp giá vốn, cùng lý do với product_name ở trên. Nhập lô
                # hàng giá khác sau này không được phép làm đổi lãi của đơn đã
                # bán - mà tra ngược Product.cost_price lúc làm báo cáo thì đúng
                # là như vậy, và không lấy lại được số cũ nữa.
                # Hàng theo lô lấy giá vốn của ĐÚNG các lô vừa xuất (bình quân
                # phần đã lấy nếu dòng ăn qua nhiều lô); hàng không theo lô lấy
                # giá vốn bình quân của sản phẩm như trước.
                # NULL = chưa khai giá vốn; báo cáo đếm riêng, không tính thành
                # lãi bằng cả giá bán.
                quantity=qty,
                cost_known_qty=known_qty,
                cost_unknown_qty=unknown_qty,
                cost_basis_vnd=cost_basis_vnd,
        )
        db.add(dong)
        db.flush()      # immutable destination id for allocations/tie-break
        created_lines.append((dong, int(prod.price) * qty))
        if prod.track_batches:
            # Vết lô đã xuất: không có nó thì lúc trả hàng không biết nhập lại
            # vào lô nào, và đoán bừa là hỏng cả hạn sử dụng lẫn giá vốn.
            for allocation in da_lay:
                lo = allocation.batch
                if lo is None:
                    raise HTTPException(status_code=409, detail=tr("Thiếu nguồn lô của giá vốn"))
                db.add(
                    models.OrderItemBatch(
                        order_item_id=dong.id,
                        batch_id=lo.id,
                        quantity=allocation.quantity,
                        cost_known_qty=allocation.known_qty,
                        cost_unknown_qty=allocation.unknown_qty,
                        cost_basis_vnd=allocation.cost_basis_vnd,
                    )
                )

    voucher_allocations = dict(
        largest_remainder_allocate(
            int(discount_amount),
            [(line.id, gross, line.id) for line, gross in created_lines],
        )
    )
    after_voucher = [
        (line.id, gross - voucher_allocations[line.id], line.id)
        for line, gross in created_lines
    ]
    loyalty_allocations = dict(
        largest_remainder_allocate(int(loyalty_discount), after_voucher)
    )
    for line, gross in created_lines:
        line.discount_vnd = voucher_allocations[line.id]
        line.loyalty_discount_vnd = loyalty_allocations[line.id]
        line.net_amount_vnd = gross - line.discount_vnd - line.loyalty_discount_vnd
    if sum(line.net_amount_vnd for line, _ in created_lines) != total:
        raise HTTPException(status_code=409, detail=tr("Phân bổ tổng tiền theo dòng không khớp"))

    if applied_voucher is not None:
        applied_voucher.usage_count = (applied_voucher.usage_count or 0) + 1

    if new_order.status == STATUS_PAID:
        _award_loyalty_paid_order(db, new_order, current_user.id)

    try:
        # Intent and its sanitized issuance audit are deliberately the final
        # writes before the one existing order commit. Any issuance/audit/
        # commit failure rolls back order, lines, stock, cost, voucher and
        # loyalty together. Rendering is never called from this transaction.
        qr_sales_service.issue_intent_if_enabled(db, new_order, shop)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(new_order)

    return _create_order_response(db, shop, new_order)


def get_order(db: Session, current_user: models.User, order_id: int) -> Dict[str, Any]:
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    shop = db.query(models.Shop).filter(models.Shop.id == order.shop_id).first()
    if not shop:
        raise HTTPException(
            status_code=404,
            detail=tr("Không tìm thấy cửa hàng của đơn hàng"),
        )
    if not has_shop_operator_access(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Không có quyền truy cập đơn hàng này"),
        )
    require_staff_permission(current_user, PERMISSION_SALE)
    result = {
        "id": order.id,
        "shop_id": order.shop_id,
        "status": order.status,
        "total_amount": order.total_amount,
        "payment_method": order.payment_method,
        "loyalty_points_redeemed": int(order.loyalty_points_redeemed or 0),
        "loyalty_discount_amount": _so_tien(order.loyalty_discount_amount),
        "loyalty_points_earned": int(order.loyalty_points_earned or 0),
        "loyalty_balance": (
            loyalty_service.balance_for_customer(
                db, order.customer_id, shop_id=order.shop_id
            )
            if order.customer_id is not None
            else None
        ),
    }
    result.update(payment_summary(order))
    qr_intent = qr_sales_service.metadata_for_order(db, order)
    if qr_intent is not None:
        result["qr_intent"] = qr_intent
    return result


def pay_order(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: Optional[CashPayment] = None,
) -> Dict[str, Any]:
    """Thu tiền mặt cho đơn PENDING.

    Đơn chuyển khoản phải do webhook xác nhận; không còn đường bấm tay biến
    UNDERPAID/LATE_PAYMENT thành PAID.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    if order.payment_method == PAYMENT_METHOD_DEBT:
        # Nói đúng đường phải đi, thay vì để rơi vào câu "chờ ngân hàng xác
        # nhận" ở dưới - câu đó sai hoàn toàn với đơn ghi nợ.
        raise HTTPException(
            status_code=409,
            detail=tr("Đơn ghi nợ thu tiền qua chức năng thu nợ"),
        )
    if order.payment_method != PAYMENT_METHOD_CASH:
        raise HTTPException(
            status_code=409,
            detail=tr("Đơn chuyển khoản phải chờ ngân hàng xác nhận tự động"),
        )

    if order.status == STATUS_PAID:
        return {
            "msg": "Paid successfully",
            "loyalty_points_earned": int(order.loyalty_points_earned or 0),
            "loyalty_balance": (
                loyalty_service.balance_for_customer(
                    db, order.customer_id, shop_id=order.shop_id
                )
                if order.customer_id is not None
                else 0
            ),
        }

    total_amount = _so_tien(order.total_amount)
    tendered_amount = (
        total_amount if request is None else int(request.tendered_amount)
    )
    if tendered_amount < total_amount:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Tiền khách đưa phải ít nhất {amount}đ",
                amount=f"{total_amount:,.0f}",
            ),
        )
    change_amount = tendered_amount - total_amount
    shift = _current_cash_shift(
        db,
        current_user,
        order.shop_id,
        required_for_cashier=True,
        lock_for_cash_write=True,
    )

    if not apply_transition(db, order_id, MANUAL_PAY_FROM, STATUS_PAID):
        db.rollback()
        current = read_status(db, order_id)
        if current == STATUS_PAID:
            fresh = db.query(models.Order).filter(models.Order.id == order_id).one()
            return {
                "msg": "Paid successfully",
                "loyalty_points_earned": int(fresh.loyalty_points_earned or 0),
                "loyalty_balance": (
                    loyalty_service.balance_for_customer(
                        db, fresh.customer_id, shop_id=fresh.shop_id
                    )
                    if fresh.customer_id is not None
                    else 0
                ),
            }
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Không thể xác nhận thanh toán cho đơn ở trạng thái {status}",
                status=current,
            ),
        )

    db.execute(
        text(
            "UPDATE orders SET cash_paid_vnd = :amount, "
            "cash_tendered_vnd = :tendered, "
            "cash_change_vnd = :change, "
            "shift_id = :shift_id, "
            "reconciliation_reason = NULL, refund_due_vnd = 0 "
            "WHERE id = :order_id"
        ),
        {
            "amount": total_amount,
            "tendered": tendered_amount,
            "change": change_amount,
            "shift_id": shift.id if shift else None,
            "order_id": order_id,
        },
    )
    db.add(
        models.OrderPayment(
            order_id=order_id,
            entry_type=ENTRY_CASH,
            amount=total_amount,
            created_by_user_id=current_user.id,
            shift_id=shift.id if shift else None,
            note="Thu tiền mặt khi thanh toán đơn",
        )
    )
    _them_nhat_ky(
        db,
        current_user.id,
        "PAY_ORDER",
        f"Thanh toán tiền mặt đơn #{order_id} - Tổng tiền: {total_amount:,.0f}đ",
    )
    _award_loyalty_paid_order(db, order, current_user.id)
    db.commit()
    return {
        "msg": "Paid successfully",
        "loyalty_points_earned": int(order.loyalty_points_earned or 0),
        "loyalty_balance": (
            loyalty_service.balance_for_customer(
                db, order.customer_id, shop_id=order.shop_id
            )
            if order.customer_id is not None
            else 0
        ),
    }


def _cash_topup_amount(order: models.Order, request: CashTopup) -> int:
    """Kiểm trạng thái và trả đúng số tiền mặt còn thiếu của đơn."""
    if (
        order.status != STATUS_UNRECONCILED
        or order.reconciliation_reason != RECON_UNDERPAID
    ):
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Chỉ được thu bù tiền mặt cho đơn chuyển thiếu đang chờ đối soát"
            ),
        )

    received = _so_tien(order.paid_amount) + _so_tien(order.cash_paid_amount)
    remaining = max(_so_tien(order.total_amount) - received, 0)
    if remaining <= MONEY_EPSILON:
        raise HTTPException(status_code=409, detail=tr("Đơn không còn thiếu tiền"))
    if request.amount is not None:
        requested_amount = int(request.amount)
        if requested_amount != remaining:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Tiền mặt phải bù đúng toàn bộ phần còn thiếu là {amount}đ",
                    amount=f"{remaining:,.0f}",
                ),
            )
    amount = remaining
    if amount <= MONEY_EPSILON:
        raise HTTPException(
            status_code=400,
            detail=tr("Số tiền bù phải lớn hơn 0"),
        )
    return amount


def cash_topup(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: CashTopup,
) -> Dict[str, Any]:
    """Ghi nhận tiền mặt bù cho đúng đơn đang thiếu.

    UPDATE có điều kiện bảo đảm webhook và hai cú bấm thu bù không thể cùng
    thắng dựa trên một số dư cũ. Nếu bỏ amount, thu đúng toàn bộ phần còn thiếu.
    Mọi vai trò đều phải có ca OPEN của chính mình để khoản tiền vào đúng két.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    # Trả lỗi trạng thái/số tiền rõ ràng trước; request hợp lệ mới yêu cầu ca.
    amount = _cash_topup_amount(order, request)
    shift = _current_cash_shift(
        db,
        current_user,
        order.shop_id,
        required_for_everyone=True,
        lock_for_cash_write=True,
    )
    assert shift is not None
    # Có thể đã chờ một cash write khác. Đọc lại sau lock để không thu theo số
    # dư cũ; câu UPDATE có điều kiện bên dưới vẫn là hàng rào cuối cùng.
    db.refresh(order)
    amount = _cash_topup_amount(order, request)

    result = db.execute(
        text(
            """
            UPDATE orders
            SET cash_paid_vnd = COALESCE(cash_paid_vnd, 0) + :amount,
                status = CASE
                    WHEN COALESCE(paid_vnd, 0)
                       + COALESCE(cash_paid_vnd, 0) + :amount
                         >= total_vnd
                    THEN :paid ELSE :unreconciled END,
                reconciliation_reason = CASE
                    WHEN COALESCE(paid_vnd, 0)
                       + COALESCE(cash_paid_vnd, 0) + :amount
                         >= total_vnd
                    THEN NULL ELSE :underpaid END
            WHERE id = :order_id
              AND status = :unreconciled
              AND reconciliation_reason = :underpaid
              AND COALESCE(paid_vnd, 0)
                + COALESCE(cash_paid_vnd, 0) + :amount
                  <= total_vnd
            """
        ),
        {
            "amount": amount,
            "paid": STATUS_PAID,
            "unreconciled": STATUS_UNRECONCILED,
            "underpaid": RECON_UNDERPAID,
            "order_id": order_id,
        },
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Số tiền của đơn vừa thay đổi; vui lòng tải lại trước khi thu bù"
            ),
        )

    note = (request.note or "").strip()[:500] or None
    db.add(
        models.OrderPayment(
            order_id=order_id,
            entry_type=ENTRY_CASH,
            amount=amount,
            created_by_user_id=current_user.id,
            shift_id=shift.id if shift else None,
            note=note or "Thu bù phần thiếu bằng tiền mặt",
        )
    )
    _them_nhat_ky(
        db,
        current_user.id,
        "CASH_TOPUP",
        f"Order {order_id}: thu bù tiền mặt {amount:,.0f}đ"
        + (f" - {note}" if note else ""),
    )
    _award_loyalty_paid_order(db, order, current_user.id)
    db.commit()
    db.refresh(order)
    response = {
        "msg": (
            tr("Đã thu đủ và hoàn tất đơn hàng")
            if order.status == STATUS_PAID
            else tr("Đã ghi nhận khoản tiền mặt bù thiếu")
        ),
        "id": order.id,
        "status": order.status,
        "total_amount": order.total_amount,
        "loyalty_points_earned": int(order.loyalty_points_earned or 0),
        "loyalty_balance": (
            loyalty_service.balance_for_customer(
                db, order.customer_id, shop_id=order.shop_id
            )
            if order.customer_id is not None
            else 0
        ),
    }
    response.update(payment_summary(order))
    return response


def debt_payment(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: DebtPayment,
) -> Dict[str, Any]:
    """Khách trả bớt nợ. Trả bao nhiêu cũng được, trả nhiều lần cũng được.

    Đủ tiền thì đơn tự chuyển sang `PAID` - và chỉ khi đó doanh thu mới ghi
    nhận, đúng nguyên tắc thực thu đang dùng cho cả app.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    operation_id = (request.operation_id or "").strip()
    if len(operation_id) < 8:
        raise HTTPException(
            status_code=400,
            detail=tr("Mã thao tác thu nợ không hợp lệ"),
        )
    operation_key = (
        "debt:" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    )

    so_tien = int(request.amount or 0)
    if so_tien <= MONEY_EPSILON:
        raise HTTPException(
            status_code=400,
            detail=tr("Số tiền thu phải lớn hơn 0"),
        )

    expected_entry_type = (
        ENTRY_DEBT_CASH
        if request.method == "cash"
        else ENTRY_DEBT_TRANSFER
    )

    def same_debt_request(payment: models.OrderPayment) -> bool:
        return (
            payment.order_id == order_id
            and payment.entry_type == expected_entry_type
            and abs(_so_tien(payment.amount) - so_tien) <= MONEY_EPSILON
            and (payment.note or None)
            == ((request.note or "").strip()[:500] or None)
            and (payment.reference or None)
            == ((request.reference or "").strip()[:128] or None)
        )

    da_ghi = (
        db.query(models.OrderPayment)
        .filter(models.OrderPayment.idempotency_key == operation_key)
        .first()
    )
    if da_ghi is not None:
        if same_debt_request(da_ghi):
            return _ket_qua_thu_no(db, order, lap_lai=True)
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác thu nợ đã được dùng cho một giao dịch khác"),
        )

    # Thu nợ và webhook phải xếp hàng trên cùng một write lock. Nếu không,
    # webhook có thể đọc DEBT, chờ đường thu nợ commit PAID, rồi vẫn ghi một
    # BANK_UNAPPLIED mới hơn dựa trên object cũ. Lấy lock xong phải refresh lại
    # cả trạng thái lẫn số đã thu trước khi quyết định/mutation. Nếu có két,
    # thứ tự lock toàn hệ thống là shop -> cash_shift để không tạo vòng deadlock.
    _lock_shop_for_order(db, order.shop_id)
    db.refresh(order)

    shift = None
    if request.method == "cash":
        # Tiền mặt vào két phải thuộc về ca của người đang đứng quầy, giống hệt
        # mọi khoản tiền mặt khác. Ghi ENTRY_DEBT_CASH và `shift_id` là đủ để
        # `_expected_cash` của shift_service tự cộng vào.
        shift = _current_cash_shift(
            db,
            current_user,
            order.shop_id,
            required_for_everyone=True,
            lock_for_cash_write=True,
        )
        db.refresh(order)

    # Một request cùng operation_id có thể đã hoàn tất trong lúc chờ lock.
    da_ghi = (
        db.query(models.OrderPayment)
        .filter(models.OrderPayment.idempotency_key == operation_key)
        .first()
    )
    if da_ghi is not None:
        if same_debt_request(da_ghi):
            db.rollback()
            return _ket_qua_thu_no(db, order, lap_lai=True)
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác thu nợ đã được dùng cho một giao dịch khác"),
        )

    if order.status != STATUS_DEBT:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Chỉ thu nợ được cho đơn đang ghi nợ"),
        )

    da_tra = _so_tien(order.paid_amount) + _so_tien(order.cash_paid_amount)
    con_thieu = max(_so_tien(order.total_amount) - da_tra, 0)
    if con_thieu <= MONEY_EPSILON:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Đơn này không còn nợ"))
    if so_tien > con_thieu + MONEY_EPSILON:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Khách chỉ còn nợ {amount}; không thu quá số đó",
                amount=f"{con_thieu:,.0f}",
            ),
        )

    tien_mat = request.method == "cash"
    db.add(
        models.OrderPayment(
            order_id=order_id,
            entry_type=expected_entry_type,
            amount=so_tien,
            idempotency_key=operation_key,
            created_by_user_id=current_user.id,
            shift_id=shift.id if shift else None,
            note=(request.note or "").strip()[:500] or None,
            reference=(request.reference or "").strip()[:128] or None,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        # Unique idempotency_key: một request song song cùng mã đã ghi trước.
        db.rollback()
        fresh = db.query(models.Order).filter(models.Order.id == order_id).first()
        da_ghi = (
            db.query(models.OrderPayment)
            .filter(models.OrderPayment.idempotency_key == operation_key)
            .first()
        )
        if fresh and da_ghi and same_debt_request(da_ghi):
            return _ket_qua_thu_no(db, fresh, lap_lai=True)
        if da_ghi is not None:
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Mã thao tác thu nợ đã được dùng cho một giao dịch khác"
                ),
            )
        raise

    # Cộng dồn vào đúng cột mà `payment_summary` đang đọc, thay vì dựng thêm một
    # bộ đếm riêng: hai nguồn số liệu về cùng một khoản tiền là chỉ chờ ngày lệch.
    cot = "cash_paid_vnd" if tien_mat else "paid_vnd"
    ket_qua = db.execute(
        text(
            f"UPDATE orders SET {cot} = COALESCE({cot}, 0) + :so_tien "
            "WHERE id = :order_id AND status = :dang_no "
            f"AND COALESCE(paid_vnd, 0) + COALESCE(cash_paid_vnd, 0) "
            "+ :so_tien <= total_vnd"
        ),
        {
            "so_tien": so_tien,
            "order_id": order_id,
            "dang_no": STATUS_DEBT,
        },
    )
    if ket_qua.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Số nợ vừa thay đổi; vui lòng tải lại rồi thu lại"),
        )

    db.refresh(order)
    con_thieu_moi = max(
        _so_tien(order.total_amount)
        - _so_tien(order.paid_amount)
        - _so_tien(order.cash_paid_amount),
        0,
    )
    tra_het = con_thieu_moi <= MONEY_EPSILON
    if tra_het and not apply_transition(db, order_id, DEBT_PAY_FROM, STATUS_PAID):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Trạng thái đơn vừa thay đổi; vui lòng tải lại"),
        )
    if tra_het:
        _award_loyalty_paid_order(db, order, current_user.id)

    _them_nhat_ky(
        db,
        current_user.id,
        "DEBT_PAYMENT",
        f"Order {order_id}: thu nợ {so_tien:,.0f}đ bằng "
        f"{'tiền mặt' if tien_mat else 'chuyển khoản'}"
        f", còn nợ {con_thieu_moi:,.0f}đ"
        + (" - ĐÃ TRẢ HẾT" if tra_het else ""),
    )
    db.commit()
    db.refresh(order)
    return _ket_qua_thu_no(db, order, lap_lai=False)


def _ket_qua_thu_no(
    db: Session, order: models.Order, lap_lai: bool
) -> Dict[str, Any]:
    ket_qua = {
        "msg": tr(
            "Lần thu nợ này đã được ghi nhận trước đó"
            if lap_lai
            else "Đã ghi nhận thu nợ"
        ),
        "id": order.id,
        "status": order.status,
        "total_amount": order.total_amount,
        "loyalty_points_earned": int(order.loyalty_points_earned or 0),
        "loyalty_balance": (
            loyalty_service.balance_for_customer(
                db, order.customer_id, shop_id=order.shop_id
            )
            if order.customer_id is not None
            else 0
        ),
    }
    ket_qua.update(payment_summary(order))
    return ket_qua


def complete_refund(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: RefundComplete,
) -> Dict[str, Any]:
    """Ghi nhận đã hoàn toàn bộ khoản đang chờ, không tự chuyển tiền."""
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_RECONCILIATION)

    operation_id = request.operation_id.strip()
    if len(operation_id) < 8:
        raise HTTPException(
            status_code=400,
            detail=tr("Mã thao tác hoàn tiền không hợp lệ"),
        )
    operation_key = (
        "refund:"
        + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    )
    previous_operation = (
        db.query(models.OrderPayment)
        .filter(models.OrderPayment.idempotency_key == operation_key)
        .first()
    )
    if previous_operation:
        if (
            previous_operation.order_id == order_id
            and previous_operation.entry_type
            in (ENTRY_REFUND_CASH, ENTRY_REFUND_TRANSFER)
        ):
            response = {
                "msg": tr("Lần hoàn tiền này đã được ghi nhận trước đó"),
                "id": order.id,
                "status": order.status,
                "total_amount": order.total_amount,
            }
            response.update(payment_summary(order))
            return response
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác hoàn tiền đã được dùng cho một giao dịch khác"),
        )

    due = max(_so_tien(order.refund_due_amount), 0)
    if due <= MONEY_EPSILON:
        if order.refund_completed_at is not None:
            # Bấm lặp: trả cùng kết quả nhưng tuyệt đối không ghi thêm lần hoàn.
            response = {
                "msg": tr("Khoản hoàn tiền này đã được ghi nhận trước đó"),
                "id": order.id,
                "status": order.status,
                "total_amount": order.total_amount,
            }
            response.update(payment_summary(order))
            return response
        raise HTTPException(
            status_code=409,
            detail=tr("Đơn hàng không có khoản tiền cần hoàn"),
        )

    if order.reconciliation_reason not in (RECON_OVERPAID, RECON_LATE_PAYMENT):
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Trạng thái đối soát của đơn không cho phép ghi nhận hoàn tiền"
            ),
        )

    refund_shift = None
    if request.method == "cash":
        # Hoàn bằng tiền mặt phải trừ đúng ca/két của người thao tác. Kiểm
        # idempotency ở trên trước để một retry đã thành công vẫn đọc được sau
        # khi ca cũ đã đóng.
        refund_shift = _current_cash_shift(
            db,
            current_user,
            order.shop_id,
            required_for_everyone=True,
            lock_for_cash_write=True,
        )
        assert refund_shift is not None
        # Request cùng operation_id có thể hoàn tất trong lúc chờ shift lock.
        previous_operation = (
            db.query(models.OrderPayment)
            .filter(models.OrderPayment.idempotency_key == operation_key)
            .first()
        )
        if previous_operation:
            db.refresh(order)
            if (
                previous_operation.order_id == order_id
                and previous_operation.entry_type
                in (ENTRY_REFUND_CASH, ENTRY_REFUND_TRANSFER)
            ):
                response = {
                    "msg": tr("Lần hoàn tiền này đã được ghi nhận trước đó"),
                    "id": order.id,
                    "status": order.status,
                    "total_amount": order.total_amount,
                }
                response.update(payment_summary(order))
                db.rollback()
                return response
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Mã thao tác hoàn tiền đã được dùng cho một giao dịch khác"
                ),
            )

        db.refresh(order)
        due = max(_so_tien(order.refund_due_amount), 0)
        if due <= MONEY_EPSILON:
            db.rollback()
            if order.refund_completed_at is not None:
                response = {
                    "msg": tr("Khoản hoàn tiền này đã được ghi nhận trước đó"),
                    "id": order.id,
                    "status": order.status,
                    "total_amount": order.total_amount,
                }
                response.update(payment_summary(order))
                return response
            raise HTTPException(
                status_code=409,
                detail=tr("Đơn hàng không có khoản tiền cần hoàn"),
            )
        if order.reconciliation_reason not in (
            RECON_OVERPAID,
            RECON_LATE_PAYMENT,
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Trạng thái đối soát của đơn không cho phép ghi nhận hoàn tiền"
                ),
            )

    auth_session_service.fence_live_auth_session(db)
    completed_at = datetime.utcnow()
    target_status = (
        STATUS_CANCELLED
        if order.reconciliation_reason == RECON_LATE_PAYMENT
        else STATUS_PAID
    )
    note = (request.note or "").strip()[:500] or None
    reference = (request.reference or "").strip()[:128] or None
    refund_payment = models.OrderPayment(
        order_id=order_id,
        entry_type=(
            ENTRY_REFUND_CASH
            if request.method == "cash"
            else ENTRY_REFUND_TRANSFER
        ),
        amount=due,
        idempotency_key=operation_key,
        created_by_user_id=current_user.id,
        shift_id=refund_shift.id if refund_shift else None,
        note=note,
        reference=reference,
    )
    db.add(refund_payment)
    try:
        # Unique operation key chặn cả retry đồng thời lẫn retry tới muộn sau
        # khi một chu kỳ hoàn mới đã mở.
        db.flush()
    except IntegrityError:
        db.rollback()
        previous_operation = (
            db.query(models.OrderPayment)
            .filter(models.OrderPayment.idempotency_key == operation_key)
            .first()
        )
        fresh = db.query(models.Order).filter(models.Order.id == order_id).first()
        if (
            previous_operation
            and fresh
            and previous_operation.order_id == order_id
            and previous_operation.entry_type
            in (ENTRY_REFUND_CASH, ENTRY_REFUND_TRANSFER)
        ):
            response = {
                "msg": tr("Lần hoàn tiền này đã được ghi nhận trước đó"),
                "id": fresh.id,
                "status": fresh.status,
                "total_amount": fresh.total_amount,
            }
            response.update(payment_summary(fresh))
            return response
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác hoàn tiền đã được dùng cho một giao dịch khác"),
        )

    result = db.execute(
        text(
            """
            UPDATE orders
            SET refunded_vnd = COALESCE(refunded_vnd, 0) + :due,
                refund_due_vnd = 0,
                refund_completed_at = :completed_at,
                refund_completed_by = :user_id,
                refund_method = :method,
                refund_note = :note,
                refund_reference = :reference,
                status = :target_status
            WHERE id = :order_id
              AND refund_due_vnd = :due
              AND refund_due_vnd > 0
              AND refund_completed_at IS NULL
              AND reconciliation_reason IN (:overpaid, :late_payment)
            """
        ),
        {
            "due": due,
            "completed_at": completed_at,
            "user_id": current_user.id,
            "method": request.method,
            "note": note,
            "reference": reference,
            "target_status": target_status,
            "order_id": order_id,
            "overpaid": RECON_OVERPAID,
            "late_payment": RECON_LATE_PAYMENT,
        },
    )
    if result.rowcount != 1:
        db.rollback()
        fresh = db.query(models.Order).filter(models.Order.id == order_id).first()
        if fresh and fresh.refund_completed_at is not None:
            response = {
                "msg": tr("Khoản hoàn tiền này đã được ghi nhận trước đó"),
                "id": fresh.id,
                "status": fresh.status,
                "total_amount": fresh.total_amount,
            }
            response.update(payment_summary(fresh))
            return response
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Khoản cần hoàn vừa thay đổi; vui lòng tải lại trước khi xác nhận"
            ),
        )

    detail = (
        f"Order {order_id}: đã hoàn {due:,.0f}đ bằng "
        f"{'tiền mặt' if request.method == 'cash' else 'chuyển khoản'}"
    )
    if reference:
        detail += f" - mã tham chiếu {reference}"
    if note:
        detail += f" - {note}"
    _them_nhat_ky(db, current_user.id, "REFUND_COMPLETE", detail)
    db.commit()
    db.refresh(order)
    response = {
        "msg": tr("Đã ghi nhận hoàn tiền thành công"),
        "id": order.id,
        "status": order.status,
        "total_amount": order.total_amount,
    }
    response.update(payment_summary(order))
    return response


def list_sales_history(
    db: Session,
    current_user: models.User,
    shop_id: int,
    scope: str = "today",
    q: Optional[str] = None,
    page: int = 1,
) -> Dict[str, Any]:
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)
    if scope not in HISTORY_SCOPES or page < 1:
        raise HTTPException(status_code=400, detail=tr("Bộ lọc lịch sử không hợp lệ"))

    query_text = (q or "").strip()
    base = (
        db.query(models.Order)
        .options(joinedload(models.Order.customer))
        .filter(
            models.Order.shop_id == shop_id,
            models.Order.status.in_((STATUS_PAID, STATUS_DEBT)),
        )
    )
    if query_text:
        escaped = _escape_like(query_text)
        matches = [models.Customer.name.ilike(f"%{escaped}%", escape="\\")]
        matches.append(
            db.query(models.FnbServiceCheck.id)
            .outerjoin(
                models.FnbSessionTable,
                models.FnbSessionTable.session_id == models.FnbServiceCheck.session_id,
            )
            .outerjoin(
                models.FnbTable,
                models.FnbTable.id == models.FnbSessionTable.table_id,
            )
            .filter(
                models.FnbServiceCheck.order_id == models.Order.id,
                or_(
                    models.FnbServiceCheck.label.ilike(f"%{escaped}%", escape="\\"),
                    models.FnbTable.name.ilike(f"%{escaped}%", escape="\\"),
                ),
            )
            .exists()
        )
        phone_digits = "".join(ch for ch in query_text if ch.isdigit())
        if phone_digits:
            normalized_phone = models.Customer.phone
            for separator in (" ", "-", ".", "(", ")", "+"):
                normalized_phone = func.replace(normalized_phone, separator, "")
            matches.append(normalized_phone.like(f"%{phone_digits}%", escape="\\"))
        if query_text.isdecimal():
            matches.append(models.Order.id == int(query_text))
        base = base.outerjoin(models.Customer).filter(or_(*matches))
    else:
        start_utc, end_utc = _history_bounds_utc(scope, datetime.utcnow())
        base = base.filter(
            models.Order.created_at >= start_utc,
            models.Order.created_at < end_utc,
        )

    rows = (
        base.order_by(models.Order.created_at.desc(), models.Order.id.desc())
        .offset((page - 1) * HISTORY_PAGE_SIZE)
        .limit(HISTORY_PAGE_SIZE + 1)
        .all()
    )
    has_more = len(rows) > HISTORY_PAGE_SIZE
    rows = rows[:HISTORY_PAGE_SIZE]
    fnb_contexts = _fnb_receipt_contexts(db, [row.id for row in rows])
    return {
        "orders": [{
            "id": row.id,
            "created_at": row.created_at,
            "status": row.status,
            "payment_method": row.payment_method,
            "total_amount": row.total_amount,
            "customer_name": row.customer.name if row.customer else None,
            "customer_phone_masked": _mask_customer_phone(
                row.customer.phone if row.customer else None
            ),
            **fnb_contexts.get(row.id, {"fnb_table_names": [], "fnb_check_label": None}),
        } for row in rows],
        "page": page,
        "per_page": HISTORY_PAGE_SIZE,
        "has_more": has_more,
        "searching_all_history": bool(query_text),
    }


def _fnb_receipt_contexts(db: Session, order_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not order_ids:
        return {}
    rows = (
        db.query(
            models.FnbServiceCheck.order_id,
            models.FnbServiceCheck.label,
            models.FnbTable.name,
        )
        .outerjoin(
            models.FnbSessionTable,
            models.FnbSessionTable.session_id == models.FnbServiceCheck.session_id,
        )
        .outerjoin(models.FnbTable, models.FnbTable.id == models.FnbSessionTable.table_id)
        .filter(models.FnbServiceCheck.order_id.in_(order_ids))
        .order_by(models.FnbSessionTable.id)
        .all()
    )
    result: Dict[int, Dict[str, Any]] = {}
    for order_id, label, table_name in rows:
        context = result.setdefault(
            int(order_id), {"fnb_table_names": [], "fnb_check_label": label}
        )
        if table_name and table_name not in context["fnb_table_names"]:
            context["fnb_table_names"].append(table_name)
    return result


def get_order_detail(db: Session, current_user: models.User, order_id: int) -> Dict[str, Any]:
    """Chi tiết đơn kèm từng dòng hàng, để seller đối chiếu với khách.

    Giá và tên sản phẩm lấy từ chính order_items (ảnh chụp lúc bán), không tra
    lại bảng products - nên đơn cũ vẫn hiển thị đúng giá đã bán dù sau này
    sản phẩm có đổi giá hoặc bị xóa.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    if current_user.role != "ADMIN":
        require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    shop = db.query(models.Shop).filter(models.Shop.id == order.shop_id).first()
    cashier_username = None
    if order.created_by_user_id is not None:
        cashier_username = (
            db.query(models.User.username)
            .filter(models.User.id == order.created_by_user_id)
            .scalar()
        )
    items = (
        db.query(models.OrderItem)
        .filter(models.OrderItem.order_id == order_id)
        .order_by(models.OrderItem.id)
        .all()
    )

    customer = None
    if order.customer_id is not None:
        kh = db.query(models.Customer).filter(models.Customer.id == order.customer_id).first()
        if kh:
            customer = {"id": kh.id, "name": kh.name, "phone": kh.phone}

    result = {
        "id": order.id,
        "shop_id": order.shop_id,
        "shop_name": shop.name if shop else None,
        "status": order.status,
        "created_at": order.created_at,
        "created_by_user_id": order.created_by_user_id,
        "cashier_username": cashier_username,
        "shift_id": order.shift_id,
        "payment_method": order.payment_method,
        "cash_tendered_amount": order.cash_tendered_amount,
        "cash_change_amount": order.cash_change_amount,
        "voucher_code": order.voucher_code,
        "discount_amount": order.discount_amount or 0,
        "loyalty_points_redeemed": int(order.loyalty_points_redeemed or 0),
        "loyalty_discount_amount": _so_tien(order.loyalty_discount_amount),
        "loyalty_points_earned": int(order.loyalty_points_earned or 0),
        "loyalty_balance": (
            loyalty_service.balance_for_customer(
                db, order.customer_id, shop_id=order.shop_id
            )
            if order.customer_id is not None
            else None
        ),
        "total_amount": order.total_amount,
        "customer": customer,
        **_fnb_receipt_contexts(db, [order.id]).get(
            order.id, {"fnb_table_names": [], "fnb_check_label": None}
        ),
        "subtotal": sum((i.price or 0) * (i.quantity or 0) for i in items),
        "items": [
            {
                # `id` của dòng đơn: phiếu trả hàng định danh theo dòng chứ
                # không theo sản phẩm, vì dòng mới là nơi giữ giá bán và giá vốn
                # đã chốt lúc bán.
                "id": i.id,
                "product_id": i.product_id,
                "product_name": i.product_name,
                "price": i.price,
                "quantity": i.quantity,
                "line_total": (i.price or 0) * (i.quantity or 0),
            }
            for i in items
        ],
        "payments": [
            _serialize_payment(p)
            for p in (
                db.query(models.OrderPayment)
                .filter(models.OrderPayment.order_id == order_id)
                .order_by(models.OrderPayment.created_at, models.OrderPayment.id)
                .all()
            )
        ],
    }
    result.update(payment_summary(order))
    return result


def cancel_order(db: Session, current_user: models.User, order_id: int) -> Dict[str, Any]:
    """Hủy đơn PENDING và hoàn lại tồn kho + lượt voucher.

    Toàn bộ nằm trong MỘT transaction: chuyển trạng thái, hoàn kho và hoàn
    lượt voucher cùng thành công hoặc cùng không xảy ra. Chỉ lời gọi thắng
    được UPDATE có điều kiện mới chạy phần hoàn - nên hủy hai lần (hoặc hủy
    đua với webhook) không bao giờ hoàn kho hai lần.

    Đơn đã CANCELLED: trả 200 im lặng (bấm trùng).
    Đơn PAID / UNRECONCILED: từ chối 409 - tiền đã về, phải đối soát chứ
    không được hủy để hoàn kho.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    # Dữ liệu legacy có thể còn đơn hàng sau khi shop đã bị xóa. Admin vẫn cần
    # hủy được các đơn mồ côi này để giải phóng tồn kho; seller không được phép
    # đi vòng qua kiểm tra quyền sở hữu shop.
    if current_user.role != "ADMIN":
        require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)

    # Use the same shop write barrier as sale/return/webhook before deciding
    # whether this unpaid order can be reversed.  Legacy orphan orders have no
    # Shop row; ADMIN keeps the historical recovery path and the conditional
    # order transition still acquires SQLite's writer lock for that case.
    locked_shop_id = int(order.shop_id)
    shop_exists = (
        db.query(models.Shop.id).filter(models.Shop.id == locked_shop_id).scalar()
        is not None
    )
    if shop_exists:
        db.rollback()
        inventory_service.lock_shop_for_inventory(db, locked_shop_id)
        order = db.query(models.Order).filter(models.Order.id == order_id).first()
        if order is None:
            db.rollback()
            raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))

    if order.status == STATUS_CANCELLED:
        return _ket_qua_huy(
            order_id,
            restored=0,
            unrestored=0,
            voucher_released=False,
            loyalty_restored=_diem_da_hoan_khi_huy(db, order_id),
        )

    # Đơn ghi nợ đã thu được một phần thì KHÔNG hủy được: hủy sẽ hoàn tồn kho
    # cho số hàng khách đã cầm về, và biến khoản tiền đã thu thành tiền vô chủ -
    # nằm trong két nhưng không thuộc đơn nào. Trả lại tiền cho khách trước, rồi
    # mới xử lý đơn.
    if order.status == STATUS_DEBT:
        da_thu = _so_tien(order.paid_amount) + _so_tien(order.cash_paid_amount)
        if da_thu > MONEY_EPSILON:
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Đơn nợ này đã thu {amount}; không hủy được. "
                    "Hoàn tiền cho khách trước rồi mới xử lý đơn.",
                    amount=f"{da_thu:,.0f}đ",
                ),
            )

    if order.status not in CANCEL_FROM:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Không thể hủy đơn ở trạng thái {status}",
                status=order.status,
            ),
        )

    if int(order.inventory_reversed or 0) and order.status != STATUS_CANCELLED:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Dấu hoàn kho của đơn không khớp trạng thái"))
    if any(int(item.returned_total_qty or 0) for item in order.items):
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Đơn đã có trả hàng nên không thể hủy"))

    # Giữ lại trước khi commit vì commit sẽ expire ORM object.
    shop_id = order.shop_id
    voucher_code = order.voucher_code
    discount_amount = order.discount_amount

    if not apply_transition(db, order_id, CANCEL_FROM, STATUS_CANCELLED):
        db.rollback()
        current = read_status(db, order_id)
        if current == STATUS_CANCELLED:
            return _ket_qua_huy(
                order_id,
                restored=0,
                unrestored=0,
                voucher_released=False,
                loyalty_restored=_diem_da_hoan_khi_huy(db, order_id),
            )
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Không thể hủy đơn ở trạng thái {status}",
                status=current,
            ),
        )

    loyalty_restored = _hoan_diem_khi_huy(
        db,
        order,
        created_by_user_id=current_user.id,
        cancelled_at=datetime.utcnow(),
    )
    restored, unrestored, voucher_released = _hoan_lai(
        db, order_id, shop_id, voucher_code, discount_amount
    )
    log_system_action(
        db,
        current_user.id,
        "CANCEL_ORDER",
        _mo_ta_huy(
            order_id,
            restored,
            unrestored,
            voucher_code,
            voucher_released,
            loyalty_restored,
        ),
    )
    return _ket_qua_huy(
        order_id,
        restored,
        unrestored,
        voucher_released,
        loyalty_restored,
    )


def _diem_da_hoan_khi_huy(db: Session, order_id: int) -> int:
    entries = (
        db.query(models.LoyaltyPointEntry.points_delta)
        .filter(
            models.LoyaltyPointEntry.order_id == order_id,
            models.LoyaltyPointEntry.entry_type
            == loyalty_service.ENTRY_CANCEL_RESTORE,
        )
        .all()
    )
    return sum(max(int(points or 0), 0) for (points,) in entries)


def _hoan_diem_khi_huy(
    db: Session,
    order: models.Order,
    *,
    created_by_user_id: Optional[int],
    cancelled_at: datetime,
) -> int:
    """Hoàn đúng allocation REDEEM còn hạn, chưa commit.

    Hàm được gọi sau khi UPDATE trạng thái thắng và trước `_hoan_lai` commit,
    nên trạng thái + kho + voucher + điểm cùng thành công hoặc cùng rollback.
    """
    redeemed = int(order.loyalty_points_redeemed or 0)
    if redeemed <= 0 or order.customer_id is None:
        return 0
    plan = loyalty_service.cancel_restore_plan(
        db,
        order.shop_id,
        order.customer_id,
        order.id,
        as_of=cancelled_at,
    )
    restored = 0
    for index, (points, expiry) in enumerate(plan, start=1):
        if points <= 0:
            continue
        loyalty_service.add_entry(
            db,
            order.shop_id,
            order.customer_id,
            loyalty_service.ENTRY_CANCEL_RESTORE,
            int(points),
            f"cancel-restore:order:{order.id}:slice:{index}",
            order_id=order.id,
            created_by_user_id=created_by_user_id,
            note=f"Hoàn điểm đã giữ khi hủy đơn #{order.id}",
            expires_at=expiry,
            created_at=cancelled_at,
        )
        restored += int(points)
    return restored


def _hoan_lai(
    db: Session,
    order_id: int,
    shop_id: int,
    voucher_code: Optional[str],
    discount_amount: Optional[int],
) -> Tuple[int, int, bool]:
    """Hoàn kho + trả lượt voucher rồi commit. Chỉ gọi sau khi apply_transition thắng."""
    restored, unrestored = inventory_service.restore_stock(db, order_id)
    voucher_released = voucher_service.release_usage(
        db, shop_id, voucher_code, discount_amount
    )
    db.commit()
    return restored, unrestored, voucher_released


def _mo_ta_huy(
    order_id: int,
    restored: int,
    unrestored: int,
    voucher_code: Optional[str],
    voucher_released: bool,
    loyalty_restored: int = 0,
) -> str:
    chi_tiet = f"Hủy đơn #{order_id} - hoàn kho {restored} dòng"
    if unrestored:
        chi_tiet += f", KHÔNG hoàn được {unrestored} dòng (thiếu product_id hoặc SP đã xóa)"
    if voucher_released:
        chi_tiet += f", trả lại 1 lượt voucher '{voucher_code}'"
    if loyalty_restored:
        chi_tiet += f", hoàn {loyalty_restored} điểm đã giữ"
    return chi_tiet


def cancel_expired_order(db: Session, order: models.Order) -> bool:
    """Hủy một đơn PENDING quá hạn do hệ thống tự động (không có người dùng).

    Dùng chung đúng cơ chế với hủy thủ công: UPDATE có điều kiện thắng thì mới
    hoàn kho, nên job chạy trùng lúc khách vừa thanh toán sẽ thua và không
    hoàn kho cho đơn đã PAID.
    """
    order_id = int(order.id)
    shop_id = int(order.shop_id)
    db.rollback()
    inventory_service.lock_shop_for_inventory(db, shop_id)
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        db.rollback()
        return False
    voucher_code = order.voucher_code
    discount_amount = order.discount_amount

    if not apply_transition(db, order_id, CANCEL_FROM, STATUS_CANCELLED):
        db.rollback()
        return False

    loyalty_restored = _hoan_diem_khi_huy(
        db,
        order,
        created_by_user_id=None,
        cancelled_at=datetime.utcnow(),
    )
    restored, unrestored, voucher_released = _hoan_lai(
        db, order_id, shop_id, voucher_code, discount_amount
    )
    log_system_action(
        db,
        None,  # hệ thống, không phải người dùng
        "AUTO_CANCEL_ORDER",
        "Tự động "
        + _mo_ta_huy(
            order_id,
            restored,
            unrestored,
            voucher_code,
            voucher_released,
            loyalty_restored,
        )
        + " (quá hạn thanh toán)",
    )
    return True


def _ket_qua_huy(
    order_id: int,
    restored: int,
    unrestored: int,
    voucher_released: bool,
    loyalty_restored: int = 0,
) -> Dict[str, Any]:
    return {
        "msg": "Cancelled successfully",
        "order_id": order_id,
        "restored_items": restored,
        "unrestored_items": unrestored,
        "voucher_released": voucher_released,
        "loyalty_points_restored": loyalty_restored,
    }


def apply_webhook_payment(db: Session, request_data: Dict[str, Any]) -> Dict[str, List[int]]:
    """Cộng dồn mọi giao dịch ngân hàng hợp lệ, idempotent theo từng giao dịch.

    Mỗi ledger row, tổng tiền, trạng thái và audit được commit cùng một
    transaction. Gửi lại cùng giao dịch chỉ trả lại trạng thái hiện tại; không
    cộng tiền, không ghi log và không làm frontend phát tiếng lần nữa.
    """
    transactions = payment_service.extract_transactions(request_data)
    if not transactions:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Không tìm thấy mã đơn hàng ORDERxxx trong thông tin thanh toán"
            ),
        )

    paid: set[int] = set()
    unreconciled: set[int] = set()
    rejected: set[int] = set()
    found_any = False

    # Không gộp theo order_id: một payload Casso có thể chứa 40k + 60k cho cùng
    # đơn, và cả hai khoản đều phải được ghi nhận.
    for gd in transactions:
        try:
            result = _apply_one_webhook_event(db, gd)
        except Exception as exc:
            # Mỗi vòng là một transaction độc lập. Item trước đã commit vẫn
            # durable; riêng item hiện tại phải quay về hoàn toàn rồi để lỗi
            # nổi thành 5xx, buộc provider retry cả batch.
            db.rollback()
            # HTTPException từ loyalty/helper sau khi event đã bắt đầu KHÔNG
            # còn là ingress 4xx. Chuẩn hóa nó cùng mọi persistence/unknown
            # failure để route trả 5xx và provider retry.
            raise WebhookEventPersistenceError(
                "ORDER webhook event was not durably persisted"
            ) from exc
        if result is None:
            continue
        found_any = True
        if result == "paid":
            paid.add(gd.order_id)
            unreconciled.discard(gd.order_id)
        elif result == "unreconciled":
            if gd.order_id not in paid:
                unreconciled.add(gd.order_id)
        else:
            rejected.add(gd.order_id)

    if not found_any:
        raise HTTPException(
            status_code=404,
            detail=tr("Không tìm thấy đơn hàng tương ứng"),
        )
    return {
        "paid": sorted(paid),
        "unreconciled": sorted(unreconciled),
        "rejected": sorted(rejected),
    }


def _apply_one_webhook_event(db: Session, gd: Any) -> Optional[str]:
    """Xử lý đúng một bank event và kết thúc transaction của chính event đó."""
    order = db.query(models.Order).filter(models.Order.id == gd.order_id).first()
    if order is None:
        # Chỉ có read transaction; đóng nó để item kế tiếp không dùng chung
        # snapshot với một event không tìm thấy order.
        db.rollback()
        return None

    # Account mismatch là một quyết định dựa trên cấu hình durable. Webhook và
    # update shop cùng xếp hàng trên hàng Shop; sau lock phải refresh Order và
    # đọc lại account, không dùng snapshot đã đọc trước lock.
    order_id = order.id
    shop_id = order.shop_id
    # SQLite không thể nâng một read snapshot cũ thành writer sau khi update
    # account khác đã commit. Transaction này mới chỉ nhận diện order/shop nên
    # đóng snapshot trước lock là an toàn; mọi state nghiệp vụ được nạp lại dưới
    # lock ngay sau đó.
    db.rollback()
    _lock_shop_for_order(db, shop_id)
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        db.rollback()
        return None
    db.refresh(order)
    configured_account = (
        db.query(models.Shop.bank_account_no)
        .filter(models.Shop.id == shop_id)
        .scalar()
    )
    # Payload không có account number vẫn đi đường tương thích P0.1. Account
    # CÓ MẶT nhưng sai phải bị chặn trước cả BANK_UNAPPLIED/status/refund/điểm.
    if _account_mismatch(gd.account_no, configured_account):
        _ghi_tu_choi_sai_tai_khoan(db, order, gd, configured_account)
        return "rejected"

    amount = _valid_webhook_amount(gd)
    key = _bank_idempotency_key(gd, configured_account)
    existing = _find_existing_bank_events(
        db,
        key=key,
        order_id=order.id,
        gd=gd,
    )
    if existing:
        return _duplicate_or_collision_outcome(
            db,
            order=order,
            gd=gd,
            amount=amount,
            existing=existing,
            key=key,
        )

    # Guard trạng thái đứng trước validation chiều/số tiền như hành vi P0.1:
    # tiền hợp lệ về cho DEBT thành BANK_UNAPPLIED; tiền ra/thiếu tiền chỉ log.
    if order.status not in WEBHOOK_PAY_FROM:
        return _apply_unapplied_bank_event(
            db,
            order,
            gd,
            configured_account=configured_account,
        )
    if gd.direction == "out":
        _commit_webhook_rejection(
            db,
            order,
            "giao dịch là tiền RA, không phải tiền vào",
        )
        return "rejected"
    if gd.amount is None:
        _commit_webhook_rejection(
            db,
            order,
            "payload không có số tiền nên không xác nhận được đã thu đủ",
        )
        return "rejected"
    if amount is None:
        _commit_webhook_rejection(
            db,
            order,
            "số tiền giao dịch phải lớn hơn 0 (khác với payload thiếu số tiền)",
        )
        return "rejected"

    return _apply_bank_transaction(
        db, order, gd, amount, configured_account=configured_account
    )


def _valid_webhook_amount(gd: Any) -> Optional[int]:
    """Return an exact positive integer VND amount, never a rounded value."""
    if gd.amount is None or bool(getattr(gd, "amount_invalid", False)):
        return None
    try:
        amount = exact_vnd(gd.amount)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


def _canonical_bank_txn_id(value: Any) -> Optional[str]:
    """Canonical transaction ID dùng thống nhất cho lưu, key và so sánh.

    Provider có thể thêm khoảng trắng ở envelope khác nhau. Chỉ strip hai đầu;
    không đổi hoa/thường hay đưa raw ID thành định danh toàn cục vì raw ID không
    được bảo đảm duy nhất giữa provider, account hoặc shop.
    """
    canonical = str(value).strip() if value is not None else ""
    return canonical or None


def _bank_idempotency_key(gd: Any, fallback_account: Optional[str] = None) -> str:
    """Khóa retry riêng; không biến bank_txn_id thành ràng buộc unique."""
    provider = str(gd.provider or "unknown").strip().lower()
    account = "".join(
        c
        for c in str(gd.account_no or fallback_account or "unknown").strip().upper()
        if c.isalnum()
    )
    account = account.lstrip("0") or "0"
    txn_id = _canonical_bank_txn_id(gd.txn_id)
    if txn_id:
        raw = f"txn|{provider}|{account}|{txn_id}"
    else:
        # fingerprint là hash canonical của đúng mục giao dịch từ provider.
        raw = (
            f"payload|{provider}|{account}|"
            + str(gd.payload_fingerprint or "")
        )
    return "bank:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_account_no(account_no: Any) -> str:
    """Chuẩn hóa account để so khớp, giữ tương thích luật bỏ số 0 đầu."""
    raw = str(account_no or "").strip()
    if not raw:
        return ""
    return raw.lstrip("0") or "0"


def _account_mismatch(account_no: Any, configured_account: Any) -> bool:
    """Chỉ kết luận mismatch khi payload và shop đều có account rõ ràng.

    Shop tạo mới bắt buộc có tài khoản. Nhánh cấu hình trống chỉ giữ hành vi
    legacy, tránh tự đặt một policy mới cho dữ liệu cũ trong lát cắt P0.1 này.
    """
    received = _normalize_account_no(account_no)
    configured = _normalize_account_no(configured_account)
    return bool(received and configured and received != configured)


def _classify_existing(order: models.Order) -> str:
    if order.status == STATUS_PAID:
        return "paid"
    if order.status in (STATUS_UNRECONCILED, STATUS_CANCELLED):
        return "unreconciled"
    return "rejected"


class WebhookEventPersistenceError(RuntimeError):
    """Event hợp lệ đã bắt đầu nhưng không thể kết thúc transaction durable."""


class WebhookDurableStateError(RuntimeError):
    """Durable state sau race không đủ để kết luận duplicate/collision."""


def _commit_webhook_event(db: Session) -> None:
    """Commit một event; commit lỗi phải rollback và nổi lên cho provider retry."""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _same_payment(
    existing: models.OrderPayment,
    order_id: int,
    gd: Any,
    amount: Optional[int],
) -> bool:
    if existing.entry_type not in (ENTRY_BANK, ENTRY_BANK_UNAPPLIED):
        return False
    if gd.direction == "out" or amount is None:
        return False
    if existing.order_id != order_id:
        return False
    if _so_tien(existing.amount) != amount:
        return False
    incoming_txn = _canonical_bank_txn_id(gd.txn_id)
    existing_txn = _canonical_bank_txn_id(existing.bank_txn_id)
    if existing_txn != incoming_txn:
        return False
    return True


def _find_existing_bank_events(
    db: Session,
    *,
    key: str,
    order_id: int,
    gd: Any,
) -> List[models.OrderPayment]:
    """Tìm canonical winner toàn cục và raw fallback trong đúng một order.

    Canonical key (provider + account + transaction/fingerprint) là namespace
    đủ mạnh để phát hiện cùng event bị dùng cho order khác. Raw transaction ID
    chỉ là compatibility fallback giữa BANK_IN/BANK_UNAPPLIED của chính order;
    provider/account/shop khác có thể hợp lệ dùng cùng mã raw.
    """
    rows = (
        db.query(models.OrderPayment)
        .filter(models.OrderPayment.idempotency_key == key)
        .order_by(models.OrderPayment.id)
        .all()
    )
    txn_id = _canonical_bank_txn_id(gd.txn_id)
    if txn_id:
        # Đọc bounded theo order để hỗ trợ row legacy từng lưu " TX1 " mà
        # không quét hoặc so khớp raw transaction trên toàn hệ thống.
        raw_candidates = (
            db.query(models.OrderPayment)
            .filter(
                models.OrderPayment.order_id == order_id,
                models.OrderPayment.entry_type.in_(
                    (ENTRY_BANK, ENTRY_BANK_UNAPPLIED)
                ),
            )
            .order_by(models.OrderPayment.id)
            .all()
        )
        raw_rows = [
            row
            for row in raw_candidates
            if _canonical_bank_txn_id(row.bank_txn_id) == txn_id
        ]
        seen = {row.id for row in rows}
        rows.extend(row for row in raw_rows if row.id not in seen)
    return rows


def _collision_event_ref(gd: Any, key: str) -> str:
    """Reference một chiều để dedupe audit mà không ghi transaction ID thô."""
    source = _canonical_bank_txn_id(gd.txn_id) or key
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _collision_audit_details(order_id: int, *, event_ref: str) -> str:
    # Cố ý không chứa raw payload, account, transaction ID hay exception text.
    return (
        f"Order {order_id}: định danh webhook ngân hàng đã tồn tại nhưng "
        "không tương thích order/amount/event; đã từ chối, không áp tiền "
        f"(event {event_ref})"
    )


def _commit_idempotency_collision(
    db: Session,
    *,
    order_id: int,
    shop_id: int,
    gd: Any,
    key: str,
) -> str:
    """Audit collision xác định được rồi mới trả business rejection 200."""
    details = _collision_audit_details(
        order_id,
        event_ref=_collision_event_ref(gd, key),
    )
    exists = (
        db.query(models.SystemLog.id)
        .filter(
            models.SystemLog.action == "WEBHOOK_XUNG_DOT_IDEMPOTENCY",
            models.SystemLog.details == details,
        )
        .first()
    )
    if exists is None:
        _them_nhat_ky(
            db,
            None,
            "WEBHOOK_XUNG_DOT_IDEMPOTENCY",
            details,
            shop_id=shop_id,
        )
        _commit_webhook_event(db)
    else:
        db.rollback()
    return "rejected"


def _fresh_order_outcome(db: Session, order_id: int) -> str:
    """Rollback caller xong, phân loại Order bằng một Session sạch."""
    fresh = Session(bind=db.get_bind())
    try:
        order = fresh.query(models.Order).filter(models.Order.id == order_id).first()
        if order is None:
            raise WebhookDurableStateError(
                "durable order missing while classifying webhook duplicate"
            )
        return _classify_existing(order)
    finally:
        fresh.close()


def _duplicate_or_collision_outcome(
    db: Session,
    *,
    order: models.Order,
    gd: Any,
    amount: Optional[int],
    existing: List[models.OrderPayment],
    key: str,
) -> str:
    order_id = order.id
    shop_id = order.shop_id
    compatible = all(
        _same_payment(payment, order_id, gd, amount) for payment in existing
    )
    if not compatible:
        return _commit_idempotency_collision(
            db,
            order_id=order_id,
            shop_id=shop_id,
            gd=gd,
            key=key,
        )

    # Không phân loại từ object đã được load trước winner. Đóng transaction đọc
    # (và nhả write lock nếu có), rồi đọc Order bằng Session hoàn toàn sạch.
    db.rollback()
    outcome, collision = _fresh_duplicate_outcome(
        db,
        key=key,
        order_id=order_id,
        gd=gd,
        amount=amount,
    )
    if collision:
        return _commit_idempotency_collision(
            db,
            order_id=order_id,
            shop_id=shop_id,
            gd=gd,
            key=key,
        )
    if outcome is None:
        raise WebhookDurableStateError(
            "durable webhook winner changed during duplicate classification"
        )
    return outcome


def _fresh_duplicate_outcome(
    db: Session,
    *,
    key: str,
    order_id: int,
    gd: Any,
    amount: Optional[int],
) -> Tuple[Optional[str], bool]:
    """Trả (duplicate outcome, collision) từ durable winner trong Session mới."""
    fresh = Session(bind=db.get_bind())
    try:
        existing = _find_existing_bank_events(
            fresh,
            key=key,
            order_id=order_id,
            gd=gd,
        )
        if not existing:
            return None, False
        if not all(
            _same_payment(payment, order_id, gd, amount)
            for payment in existing
        ):
            return None, True
        order = fresh.query(models.Order).filter(models.Order.id == order_id).first()
        if order is None:
            raise WebhookDurableStateError(
                "durable order missing after webhook IntegrityError"
            )
        return _classify_existing(order), False
    finally:
        fresh.close()


def _apply_bank_transaction(
    db: Session,
    order: models.Order,
    gd: Any,
    amount: int,
    *,
    configured_account: Optional[str],
) -> str:
    """Ghi một giao dịch vào ledger rồi suy ra trạng thái từ tổng lũy kế."""
    order_id = order.id
    shop_id = order.shop_id
    key = _bank_idempotency_key(gd, configured_account)

    # Tương thích dữ liệu trước khi có ledger: retry đúng mã giao dịch đã lưu
    # trên orders không được biến thành một khoản tiền mới.
    if (
        _canonical_bank_txn_id(gd.txn_id)
        and _canonical_bank_txn_id(order.bank_txn_id)
        and _canonical_bank_txn_id(gd.txn_id)
        == _canonical_bank_txn_id(order.bank_txn_id)
        and order.paid_amount is not None
        and order.status != STATUS_PENDING
    ):
        if _so_tien(order.paid_amount) != amount:
            return _commit_idempotency_collision(
                db,
                order_id=order_id,
                shop_id=shop_id,
                gd=gd,
                key=key,
            )
        db.rollback()
        return _fresh_order_outcome(db, order_id)

    payment = models.OrderPayment(
        order_id=order_id,
        entry_type=ENTRY_BANK,
        amount=amount,
        idempotency_key=key,
        provider=str(gd.provider) if gd.provider else None,
        bank_txn_id=_canonical_bank_txn_id(gd.txn_id),
        account_no=str(gd.account_no) if gd.account_no else None,
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        # Hai webhook giống nhau có thể cùng vượt qua query phía trên. Failed
        # transaction bị bỏ hoàn toàn; chỉ Session MỚI được dùng để xác nhận
        # row thắng race đã persist và tương thích order/amount/event.
        db.rollback()
        duplicate, collision = _fresh_duplicate_outcome(
            db,
            key=key,
            order_id=order_id,
            gd=gd,
            amount=amount,
        )
        if collision:
            return _commit_idempotency_collision(
                db,
                order_id=order_id,
                shop_id=shop_id,
                gd=gd,
                key=key,
            )
        if duplicate is not None:
            return duplicate
        # Không có row thắng race: đây là IntegrityError khác, không được giả
        # thành success. Nổi 5xx để provider retry và để lỗi thật được quan sát.
        raise

    # INSERT ledger đã lấy write lock của SQLite. Phải đọc lại trạng thái SAU
    # thời điểm này: cancel có thể đã thắng giữa SELECT đầu hàm và INSERT.
    db.expire(order)
    db.refresh(order)
    previous_status = order.status
    previous_reason = order.reconciliation_reason
    previous_txn = order.bank_txn_id

    # Cộng bằng SQL để hai giao dịch khác nhau không ghi đè tổng của nhau.
    db.execute(
        text(
            """
            UPDATE orders
            SET paid_vnd = COALESCE(paid_vnd, 0) + :amount,
                bank_txn_id = CASE
                    WHEN :txn IS NULL THEN bank_txn_id ELSE :txn END
            WHERE id = :order_id
            """
        ),
        {
            "amount": amount,
            "txn": _canonical_bank_txn_id(gd.txn_id),
            "order_id": order_id,
        },
    )
    db.expire(order)
    db.refresh(order)

    received = _so_tien(order.paid_amount) + _so_tien(order.cash_paid_amount)
    total = _so_tien(order.total_amount)
    refunded = _so_tien(order.refunded_amount)

    if previous_status == STATUS_CANCELLED or previous_reason == RECON_LATE_PAYMENT:
        order.status = STATUS_UNRECONCILED
        order.reconciliation_reason = RECON_LATE_PAYMENT
        order.refund_due_amount = max(received - refunded, 0)
        _reset_refund_completion(order)
        _them_nhat_ky(
            db,
            None,
            "WEBHOOK_UNRECONCILED",
            f"Order {order_id}: nhận thêm {amount:,.0f}đ sau khi đơn đã hủy; "
            f"tổng cần hoàn {order.refund_due_amount:,.0f}đ",
        )
        result = "unreconciled"
    elif previous_reason == RECON_LEGACY_REVIEW:
        # Không thể biết UNRECONCILED cũ do thiếu tiền hay do đơn từng hủy.
        # Ghi tiền nhưng tuyệt đối không tự hồi sinh.
        order.status = STATUS_UNRECONCILED
        order.reconciliation_reason = RECON_LEGACY_REVIEW
        _them_nhat_ky(
            db,
            None,
            "WEBHOOK_UNRECONCILED",
            f"Order {order_id}: nhận thêm {amount:,.0f}đ nhưng đơn đối soát cũ "
            "không đủ dữ liệu để tự kết luận",
        )
        result = "unreconciled"
    elif received < total - MONEY_EPSILON:
        order.status = STATUS_UNRECONCILED
        order.reconciliation_reason = RECON_UNDERPAID
        order.refund_due_amount = 0
        remaining = total - received
        _them_nhat_ky(
            db,
            None,
            "WEBHOOK_THIEU_TIEN",
            f"Order {order_id}: vừa nhận {amount:,.0f}đ, tổng đã nhận "
            f"{received:,.0f}đ nhưng cần {total:,.0f}đ "
            f"(thiếu {remaining:,.0f}đ) - cần đối soát",
        )
        result = "unreconciled"
    else:
        order.status = STATUS_PAID
        excess_due = max(received - total - refunded, 0)
        order.refund_due_amount = excess_due
        if excess_due > MONEY_EPSILON:
            order.reconciliation_reason = RECON_OVERPAID
            _reset_refund_completion(order)
        else:
            order.reconciliation_reason = None

        if previous_status == STATUS_PAID:
            _them_nhat_ky(
                db,
                None,
                "WEBHOOK_TRA_TRUNG",
                f"Order {order_id} đã thanh toán"
                + (f" bằng giao dịch {previous_txn}" if previous_txn else "")
                + f", nay nhận thêm giao dịch {gd.txn_id or '(không mã)'} "
                f"({amount:,.0f}đ) - cần hoàn {excess_due:,.0f}đ",
            )
        else:
            detail = (
                f"Order {order_id} marked PAID via webhook "
                f"(tổng nhận {received:,.0f}đ)"
            )
            if excess_due > MONEY_EPSILON:
                detail += f", khách chuyển DƯ {excess_due:,.0f}đ - cần trả lại"
            _them_nhat_ky(db, None, "WEBHOOK_PAYMENT", detail)
        result = "paid"

    if result == "paid":
        _award_loyalty_paid_order(db, order, None)
    _commit_webhook_event(db)
    return result


def _reset_refund_completion(order: models.Order) -> None:
    """Một khoản dư mới mở chu kỳ hoàn mới; lịch sử cũ vẫn còn trong ledger."""
    order.refund_completed_at = None
    order.refund_completed_by = None
    order.refund_method = None
    order.refund_note = None
    order.refund_reference = None


def _unapplied_audit_details(
    order: models.Order,
    *,
    amount: int,
    event_key: str,
) -> str:
    return (
        f"Order {order.id}: đơn đang ở trạng thái {order.status}, webhook không "
        "tự xử lý (đơn ghi nợ thu qua chức năng thu nợ); "
        f"BANK_UNAPPLIED {amount:,.0f}đ (event {event_key})"
    )


def _ensure_unapplied_audit(
    db: Session,
    order: models.Order,
    *,
    amount: int,
    event_key: str,
) -> bool:
    """Thêm audit của BANK_UNAPPLIED đúng một lần, chưa commit."""
    details = _unapplied_audit_details(
        order,
        amount=amount,
        event_key=event_key,
    )
    exists = (
        db.query(models.SystemLog.id)
        .filter(
            models.SystemLog.action == "WEBHOOK_TU_CHOI",
            models.SystemLog.details == details,
        )
        .first()
    )
    if exists:
        return False
    _them_nhat_ky(
        db,
        None,
        "WEBHOOK_TU_CHOI",
        details,
        shop_id=order.shop_id,
    )
    return True


def _apply_unapplied_bank_event(
    db: Session,
    order: models.Order,
    gd: Any,
    *,
    configured_account: Optional[str],
) -> str:
    """Ghi BANK_UNAPPLIED + SystemLog trong đúng một transaction.

    Chỉ ghi khi payload có số tiền hợp lệ; tiền RA hoặc payload thiếu số tiền
    thì không có gì để báo cho người bán ngoài dòng log.

    **Dùng CHUNG `_bank_idempotency_key` với bút toán thật, và đó là điều bắt
    buộc.** Nếu đặt khóa riêng thì kịch bản sau cộng tiền hai lần: khách chuyển
    100k cho đơn nợ -> webhook ghi unapplied -> người bán thu nợ tay, đơn thành
    PAID -> ngân hàng gửi lại đúng giao dịch đó (chuyện bình thường) -> lúc này
    PAID nằm trong `WEBHOOK_PAY_FROM` nên giao dịch được xử lý thật, và vì khóa
    khác nhau nên không bị coi là trùng -> đơn thành OVERPAID với 100k chờ hoàn
    không có thật. Dùng chung khóa thì lần gửi lại rơi vào nhánh trùng lặp và
    không có đồng nào được cộng.
    """
    order_id = order.id
    shop_id = order.shop_id
    if gd.direction == "out":
        _commit_webhook_rejection(
            db,
            order,
            "giao dịch là tiền RA, không phải tiền vào",
        )
        return "rejected"
    if gd.amount is None:
        _commit_webhook_rejection(
            db,
            order,
            "payload không có số tiền nên không xác nhận được đã thu đủ",
        )
        return "rejected"
    amount = _valid_webhook_amount(gd)
    if amount is None:
        _commit_webhook_rejection(
            db,
            order,
            "số tiền giao dịch phải lớn hơn 0 (khác với payload thiếu số tiền)",
        )
        return "rejected"

    key = _bank_idempotency_key(gd, configured_account)
    existing = _find_existing_bank_events(
        db,
        key=key,
        order_id=order_id,
        gd=gd,
    )
    if existing:
        return _duplicate_or_collision_outcome(
            db,
            order=order,
            gd=gd,
            amount=amount,
            existing=existing,
            key=key,
        )

    payment = models.OrderPayment(
        order_id=order.id,
        entry_type=ENTRY_BANK_UNAPPLIED,
        amount=amount,
        idempotency_key=key,
        provider=str(gd.provider) if gd.provider else None,
        bank_txn_id=_canonical_bank_txn_id(gd.txn_id),
        account_no=str(gd.account_no) if gd.account_no else None,
        note="Tiền về cho đơn ghi nợ - chưa ghi nhận, cần thu nợ thủ công",
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        # Chỉ duplicate khi một Session mới thấy row thắng race đã persist và
        # tương thích. Unknown IntegrityError/collision phải nổi 5xx.
        db.rollback()
        duplicate, collision = _fresh_duplicate_outcome(
            db,
            key=key,
            order_id=order_id,
            gd=gd,
            amount=amount,
        )
        if collision:
            return _commit_idempotency_collision(
                db,
                order_id=order_id,
                shop_id=shop_id,
                gd=gd,
                key=key,
            )
        if duplicate is None:
            raise
        return duplicate

    _ensure_unapplied_audit(
        db,
        order,
        amount=amount,
        event_key=key,
    )
    _commit_webhook_event(db)
    return "rejected"


def _commit_webhook_rejection(
    db: Session,
    order: models.Order,
    ly_do: str,
) -> None:
    """Business rejection chỉ trả 200 sau khi audit đã durable."""
    _them_nhat_ky(
        db,
        None,
        "WEBHOOK_TU_CHOI",
        f"Order {order.id}: {ly_do}",
        shop_id=order.shop_id,
    )
    _commit_webhook_event(db)


def _ghi_tu_choi_sai_tai_khoan(
    db: Session,
    order: models.Order,
    gd: Any,
    configured_account: Optional[str],
) -> None:
    """Ghi audit ACCOUNT_MISMATCH đúng một lần cho một lần chuyển/retry.

    Mã sự kiện là hash idempotency, đủ để phân biệt giao dịch nhưng không ghi
    account number hoặc raw payload vào SystemLog.
    """
    event_key = _bank_idempotency_key(gd, configured_account)
    details = (
        f"Order {order.id}: ACCOUNT_MISMATCH - tài khoản nhận không khớp "
        f"tài khoản ngân hàng cấu hình của shop (event {event_key})"
    )
    exists = (
        db.query(models.SystemLog.id)
        .filter(
            models.SystemLog.action == "WEBHOOK_TU_CHOI",
            models.SystemLog.details == details,
        )
        .first()
    )
    if exists:
        db.rollback()
        return
    _them_nhat_ky(
        db,
        None,
        "WEBHOOK_TU_CHOI",
        details,
        shop_id=order.shop_id,
    )
    _commit_webhook_event(db)
