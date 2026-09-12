"""Nghiệp vụ voucher: CRUD + tính giảm giá."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models
from ..core import thoi_gian
from ..core.i18n import tr
from ..core.money import ExactMoneyError, exact_vnd, percentage_to_bps, round_percentage_vnd
from ..dependencies import (
    PERMISSION_SALE,
    PERMISSION_VOUCHER,
    has_shop_operator_access,
    require_shop_access,
    require_staff_permission,
)
from ..schemas.catalog import VoucherCreate
from . import auth_session_service
from .log_service import log_system_action


def is_expired(voucher: models.Voucher, today: Optional[date] = None) -> bool:
    """Voucher hết hạn khi expires_at (YYYY-MM-DD) đã qua.
    Chuỗi rỗng/None/sai định dạng -> coi như không có hạn (an toàn, giữ hành vi cũ)."""
    raw = (voucher.expires_at or "").strip()
    if not raw:
        return False
    try:
        expiry = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return False
    # `date.today()` đọc theo múi giờ của MÁY chạy app: đúng trên máy dev ở Việt
    # Nam, sai ngay khi deploy (container mặc định UTC) - voucher sống thêm 7
    # tiếng so với ngày ghi trên giao diện.
    return (today or thoi_gian.hom_nay_vn()) > expiry


def is_usage_exhausted(voucher: models.Voucher) -> bool:
    return voucher.usage_limit != -1 and (voucher.usage_count or 0) >= voucher.usage_limit


def _validate(v: VoucherCreate) -> str:
    code_stripped = v.code.strip() if v.code else ""
    if not code_stripped:
        raise HTTPException(status_code=400, detail=tr("Mã voucher không được để trống"))
    if v.discount_value < 1:
        raise HTTPException(
            status_code=400,
            detail=tr("Giá trị giảm tối thiểu phải là 1"),
        )
    if v.discount_type == "percentage" and (v.discount_value <= 0 or v.discount_value > 100):
        raise HTTPException(
            status_code=400,
            detail=tr("Giá trị giảm phần trăm phải từ 1% đến 100%"),
        )
    if v.min_order_value < 0:
        raise HTTPException(status_code=400, detail=tr("Đơn tối thiểu không được âm"))
    if v.max_discount < 0:
        raise HTTPException(status_code=400, detail=tr("Mức giảm tối đa không được âm"))
    try:
        if v.discount_type == "percentage":
            percentage_to_bps(v.discount_value)
        elif v.discount_type == "flat":
            exact_vnd(v.discount_value)
    except ExactMoneyError:
        raise HTTPException(
            status_code=400,
            detail=tr("Giá trị voucher phải biểu diễn chính xác bằng VND hoặc basis points"),
        )
    return code_stripped


def compute_discount(voucher: models.Voucher, subtotal: int) -> int:
    """Số tiền giảm. max_discount chỉ áp dụng cho loại 'percentage'."""
    if voucher.discount_type == "percentage":
        calc = round_percentage_vnd(int(subtotal), int(voucher.discount_bps or 0))
        if voucher.max_discount and voucher.max_discount > 0 and calc > voucher.max_discount:
            calc = int(voucher.max_discount)
        return calc
    return min(int(voucher.discount_value_vnd or 0), int(subtotal))


def create_voucher(
    db: Session, current_user: models.User, shop_id: int, v: VoucherCreate
) -> models.Voucher:
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_VOUCHER)
    code_stripped = _validate(v)

    existing = (
        db.query(models.Voucher)
        .filter(models.Voucher.code == code_stripped, models.Voucher.shop_id == shop_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=tr("Mã voucher này đã tồn tại trong cửa hàng"),
        )

    discount_bps = (
        percentage_to_bps(v.discount_value)
        if v.discount_type == "percentage"
        else None
    )
    discount_value_vnd = (
        exact_vnd(v.discount_value) if v.discount_type == "flat" else None
    )
    db_v = models.Voucher(
        code=code_stripped,
        shop_id=shop_id,
        discount_type=v.discount_type,
        discount_bps=discount_bps,
        discount_value_vnd=discount_value_vnd,
        min_order_value=int(v.min_order_value),
        max_discount=int(v.max_discount),
        usage_limit=v.usage_limit,
        expires_at=v.expires_at,
    )
    auth_session_service.fence_live_auth_session(db)
    db.add(db_v)
    db.commit()
    unit = "%" if v.discount_type == "percentage" else "đ"
    log_system_action(
        db,
        current_user.id,
        "CREATE_VOUCHER",
        f"Tạo Voucher '{code_stripped}' - Giảm {v.discount_value}{unit}, "
        f"Đơn tối thiểu: {v.min_order_value:,.0f}đ",
    )
    db.refresh(db_v)
    return db_v


def update_voucher(
    db: Session, current_user: models.User, voucher_id: int, v: VoucherCreate
) -> models.Voucher:
    db_v = db.query(models.Voucher).filter(models.Voucher.id == voucher_id).first()
    if not db_v:
        raise HTTPException(status_code=404, detail=tr("Voucher không tồn tại"))

    # Chỉ đúng chủ shop mới được sửa (giữ nguyên hành vi cũ: ADMIN cũng nhận 403)
    shop = db.query(models.Shop).filter(models.Shop.id == db_v.shop_id).first()
    if not shop or not has_shop_operator_access(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Không có quyền chỉnh sửa voucher của cửa hàng này"),
        )
    require_staff_permission(current_user, PERMISSION_VOUCHER)

    code_stripped = _validate(v)
    existing = (
        db.query(models.Voucher)
        .filter(
            models.Voucher.code == code_stripped,
            models.Voucher.shop_id == db_v.shop_id,
            models.Voucher.id != voucher_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=tr("Mã voucher này đã tồn tại trong cửa hàng"),
        )

    auth_session_service.fence_live_auth_session(db)
    db_v.code = code_stripped
    db_v.discount_type = v.discount_type
    db_v.discount_bps = (
        percentage_to_bps(v.discount_value)
        if v.discount_type == "percentage"
        else None
    )
    db_v.discount_value_vnd = (
        exact_vnd(v.discount_value) if v.discount_type == "flat" else None
    )
    db_v.min_order_value = int(v.min_order_value)
    db_v.max_discount = int(v.max_discount)
    db_v.usage_limit = v.usage_limit
    db_v.expires_at = v.expires_at
    db.commit()
    unit = "%" if db_v.discount_type == "percentage" else "đ"
    log_system_action(
        db,
        current_user.id,
        "UPDATE_VOUCHER",
        f"Cập nhật Voucher '{db_v.code}' - Giảm {db_v.discount_value}{unit}",
    )
    db.refresh(db_v)
    return db_v


def delete_voucher(db: Session, current_user: models.User, voucher_id: int) -> Dict[str, str]:
    db_v = db.query(models.Voucher).filter(models.Voucher.id == voucher_id).first()
    if not db_v:
        raise HTTPException(status_code=404, detail=tr("Voucher không tồn tại"))
    require_shop_access(db, db_v.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_VOUCHER)
    code = db_v.code
    auth_session_service.fence_live_auth_session(db)
    db.delete(db_v)
    db.commit()
    log_system_action(db, current_user.id, "DELETE_VOUCHER", f"Xóa Voucher '{code}'")
    return {"msg": "Deleted"}


def list_vouchers(
    db: Session, current_user: models.User, shop_id: int
) -> List[models.Voucher]:
    """Danh sách voucher của shop. Cùng quyền với tạo/xóa: `PERMISSION_VOUCHER`.

    Trước F6 endpoint này mở cho mọi người, và nó trả về MÃ voucher kèm giá trị
    giảm và số lượt còn lại - tức là ai đoán được `shop_id` thì lấy mã của một
    cửa hàng lạ rồi đem dùng thẳng ở POS của họ. Lỗ này mất tiền thật, khác với
    danh sách sản phẩm chỉ mất thông tin.

    Dùng đúng `PERMISSION_VOUCHER` như `create_voucher`/`delete_voucher` để
    khớp với giao diện: tab Khuyến Mãi trong `seller.js` chỉ hiện cho MANAGER.
    Thu ngân không cần danh sách - họ gõ mã khách đưa rồi gọi `apply_voucher`.
    """
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_VOUCHER)
    return db.query(models.Voucher).filter(models.Voucher.shop_id == shop_id).all()


def apply_voucher(
    db: Session, current_user: models.User, shop_id: int, subtotal: int,
    voucher_code: str
) -> Dict[str, int]:
    """Thử áp một mã voucher lên tạm tính, trả về số tiền giảm.

    Quyền là `PERMISSION_SALE` chứ KHÔNG phải `PERMISSION_VOUCHER`: đây là thao
    tác của người đứng quầy, còn `PERMISSION_VOUCHER` là quyền quản lý mã.

    Trước F6 endpoint này cũng mở: gõ mã bừa vào đây là dò ra mã nào có thật của
    shop bất kỳ, không cần tài khoản. POS đã gửi sẵn `Authorization` từ trước
    nên siết lại không đổi gì ở giao diện.
    """
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)
    voucher = (
        db.query(models.Voucher)
        .filter(models.Voucher.code == voucher_code, models.Voucher.shop_id == shop_id)
        .first()
    )
    if not voucher:
        raise HTTPException(status_code=404, detail=tr("Mã giảm giá không tồn tại"))

    if voucher.min_order_value > subtotal:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Đơn hàng phải từ {amount} ₫ để áp dụng",
                amount=f"{voucher.min_order_value:,.0f}",
            ),
        )

    if is_usage_exhausted(voucher):
        raise HTTPException(
            status_code=400,
            detail=tr("Mã giảm giá đã hết lượt sử dụng"),
        )

    # BEHAVIOR FIX: trước đây expires_at không bao giờ được kiểm tra.
    if is_expired(voucher):
        raise HTTPException(
            status_code=400,
            detail=tr("Mã giảm giá đã hết hạn sử dụng"),
        )

    discount_amount = compute_discount(voucher, subtotal)
    return {"discount_amount": discount_amount, "new_total": max(0, subtotal - discount_amount)}


_RELEASE_USAGE = text(
    "UPDATE vouchers SET usage_count = usage_count - 1 "
    "WHERE code = :code AND shop_id = :shop_id AND usage_count > 0"
)


def release_usage(
    db: Session, shop_id: int, voucher_code: Optional[str], discount_amount: Optional[int]
) -> bool:
    """Trả lại 1 lượt dùng voucher khi đơn bị hủy.

    Chỉ trả lượt khi voucher THỰC SỰ đã được áp dụng: `create_order` lưu
    `voucher_code` lên đơn kể cả khi voucher bị bỏ qua (hết hạn/hết lượt/không
    đạt đơn tối thiểu), nên chỉ dựa vào `voucher_code` sẽ trả nhầm lượt cho
    voucher chưa từng được dùng. Điều kiện đúng là có giảm giá thực tế.

    `usage_count > 0` nằm ngay trong câu UPDATE nên không bao giờ xuống âm,
    kể cả khi có hai lời gọi chạy song song.

    Không commit - caller giữ nguyên một transaction duy nhất.
    """
    if not voucher_code or not discount_amount or discount_amount <= 0:
        return False
    result = db.execute(_RELEASE_USAGE, {"code": voucher_code, "shop_id": shop_id})
    return result.rowcount == 1


def resolve_for_order(
    db: Session, shop_id: int, voucher_code: Optional[str], subtotal: int
):
    """Dùng khi tạo đơn: trả (voucher, discount_amount).
    Voucher không hợp lệ -> bỏ qua giảm giá (giữ nguyên hành vi cũ, không báo lỗi)."""
    if not voucher_code:
        return None, 0
    voucher = (
        db.query(models.Voucher)
        .filter(models.Voucher.code == voucher_code, models.Voucher.shop_id == shop_id)
        .first()
    )
    if not voucher:
        return None, 0
    if voucher.min_order_value > subtotal:
        return None, 0
    if is_usage_exhausted(voucher):
        return None, 0
    # BEHAVIOR FIX: chặn voucher hết hạn khi tạo đơn.
    if is_expired(voucher):
        return None, 0
    return voucher, compute_discount(voucher, subtotal)
