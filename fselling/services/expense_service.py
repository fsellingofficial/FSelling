"""K1: chi phí vận hành - danh mục, mẫu nhắc nhở và sổ chi đã trả.

Đây là mảnh còn thiếu để trả lời câu "cuối tháng túi tôi còn bao nhiêu". Trước
bản này báo cáo dừng ở LÃI GỘP: doanh thu trừ giá vốn, đã trừ hàng trả và hàng
hủy. Tiền thuê mặt bằng, điện nước, lương nhân viên không có chỗ nào để khai,
nên con số lãi trên Dashboard luôn cao hơn số tiền thật sự vào túi - và sai theo
hướng làm người ta yên tâm, đúng kiểu sai khó nghi nhất.

**Lãi ròng và dòng tiền là HAI con số khác nhau, đừng gộp.** Nhập hàng 10 triệu
trả tiền ngay mà chưa bán món nào: dòng tiền -10 triệu, lãi ròng không đổi (hàng
còn trong kho, chỉ thành giá vốn lúc bán). Bán 5 triệu ghi nợ: lãi tăng, dòng
tiền đứng yên. File này lo phần chi phí của cả hai; `report_service` ghép lại.

Phân bổ trả trước tính THEO NGÀY bằng công thức LŨY KẾ, không chia theo tháng:

    phần rơi vào kỳ = (lũy kế đến cuối kỳ) - (lũy kế đến trước đầu kỳ)

Chia thẳng cho số tháng thì 10.000.000đ / 3 làm tròn ba lần ra 9.999.999đ, và
một đồng lệch trong sổ tiền là thứ không bao giờ tìm lại được. Cách lũy kế khử
sai số vì các lần trừ triệt tiêu nhau: cộng mọi kỳ liền nhau luôn ra ĐÚNG số
tiền đã chi, và chạy đúng với mọi khoảng ngày người dùng lọc, kể cả kỳ lẻ như
15/8 đến 20/8 hay hợp đồng thuê bắt đầu giữa tháng.
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..core import thoi_gian
from ..core.i18n import tr
from ..dependencies import require_cost_visibility, require_shop_access
from ..schemas.expense import (
    ExpenseCategoryCreate,
    ExpenseCategoryUpdate,
    ExpenseCreate,
    ExpenseTemplateCreate,
    ExpenseTemplateUpdate,
)
from . import auth_session_service, shift_service

_VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

METHOD_CASH_SHIFT = "CASH_SHIFT"
METHOD_TRANSFER = "TRANSFER"
METHOD_OUTSIDE = "OUTSIDE"
METHODS = frozenset({METHOD_CASH_SHIFT, METHOD_TRANSFER, METHOD_OUTSIDE})

MAX_AMORTIZE_MONTHS = 120
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Danh mục tạo sẵn lúc shop mở màn Chi Phí lần đầu. Có sẵn thì người bận rộn
# không phải khai gì; sửa/thêm/ẩn được nên người kỹ tính vẫn tùy biến được.
# Bỏ trắng hoàn toàn là mở đường cho "Điện", "Tiền điện", "điện nước" thành ba
# dòng riêng trong biểu đồ chi phí, lúc đó biểu đồ hết tác dụng.
#
# CỐ Ý KHÔNG có danh mục "Hao hụt / Hàng hỏng". Hàng hết hạn, vỡ, thất thoát đã
# đi qua phiếu hủy (`write_off_service`) và ĐÃ bị trừ vào lãi gộp theo đúng giá
# vốn của lô. Có thêm một ô để gõ lại số đó là mời người dùng trừ hai lần, và
# lãi ròng sẽ thấp hơn sự thật. Dòng "Tổn thất khác" nói rõ "không phải hàng
# hóa" vì lý do đó.
DEFAULT_CATEGORIES = (
    "Thuê mặt bằng",
    "Điện nước",
    "Internet / Điện thoại",
    "Lương nhân viên",
    "Phí ship",
    "Marketing / Quảng cáo",
    "Sửa chữa / Mua sắm đồ dùng",
    "Thuế, phí nhà nước",
    "Vệ sinh, bảo vệ",
    "Tổn thất khác (không phải hàng hóa)",
    "Chi phí khác",
)


# ---------------------------------------------------------------------------
# Ngày tháng
# ---------------------------------------------------------------------------

def today_vn() -> date:
    """Ngày nghiệp vụ Việt Nam, không phụ thuộc timezone máy deploy."""
    return thoi_gian.hom_nay_vn()


def parse_ngay(chuoi: Optional[str], ten_truong: str) -> Optional[date]:
    if not chuoi or not chuoi.strip():
        return None
    try:
        return datetime.strptime(chuoi.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=tr("{field} phải theo định dạng YYYY-MM-DD", field=ten_truong),
        )


def cong_thang(moc: date, so_thang: int) -> date:
    """Cùng ngày ở N tháng sau; tháng thiếu ngày thì lùi về ngày cuối tháng.

    31/01 + 1 tháng = 28/02 (hoặc 29/02 năm nhuận). Đây là quy tắc duy nhất
    trong hệ thống; giao diện chỉ hiện lại kết quả để người dùng nhìn thấy mà
    bắt lỗi, còn số được lưu luôn do server tính.
    """
    tong = moc.month - 1 + so_thang
    nam = moc.year + tong // 12
    thang = tong % 12 + 1
    return date(nam, thang, min(moc.day, calendar.monthrange(nam, thang)[1]))


def moc_ket_thuc_phan_bo(bat_dau: date, so_thang: int) -> date:
    """Trả trước N tháng kể từ `bat_dau` thì phục vụ đến hết ngày nào."""
    return cong_thang(bat_dau, so_thang) - timedelta(days=1)


# ---------------------------------------------------------------------------
# Phân bổ theo ngày (công thức lũy kế)
# ---------------------------------------------------------------------------

def _luy_ke_den(expense: models.OperatingExpense, moc: date) -> int:
    """Tổng chi phí của khoản này đã được tính đến HẾT ngày ``moc``.

    Dùng chia lấy nguyên trên số LŨY KẾ chứ không làm tròn từng kỳ. Nhờ vậy
    tổng mọi kỳ liền nhau luôn khớp tuyệt đối với số tiền đã chi: các phép trừ
    triệt tiêu nhau, và ngày cuối cùng luôn trả về đúng cả số tiền.
    """
    bat_dau = datetime.strptime(expense.amortize_start_date, "%Y-%m-%d").date()
    ket_thuc = datetime.strptime(expense.amortize_end_date, "%Y-%m-%d").date()
    tong = int(expense.amount)
    if moc < bat_dau:
        return 0
    if moc >= ket_thuc:
        return tong
    so_ngay = (ket_thuc - bat_dau).days + 1
    da_qua = (moc - bat_dau).days + 1
    return (tong * da_qua) // so_ngay


def phan_bo_trong_ky(
    expense: models.OperatingExpense, tu: Optional[date], den: date
) -> int:
    """Phần chi phí của khoản này rơi vào khoảng [tu, den] (tính cả hai đầu)."""
    truoc = _luy_ke_den(expense, tu - timedelta(days=1)) if tu else 0
    return _luy_ke_den(expense, den) - truoc


def con_tra_truoc(expense: models.OperatingExpense, moc: date) -> int:
    """Tiền đã trả nhưng CHƯA được tính vào chi phí, tại thời điểm ``moc``.

    Đây là con số bắt buộc phải hiện cạnh lãi ròng. Không có nó thì tháng đóng
    tiền nhà 3 tháng một lần, chủ shop thấy lãi đẹp và quên mất két đã bay 30
    triệu - đúng cái sai mà việc phân bổ đang cố tránh, chỉ đổi hướng.
    """
    return int(expense.amount) - _luy_ke_den(expense, moc)


# ---------------------------------------------------------------------------
# Phân quyền và tiện ích
# ---------------------------------------------------------------------------

def _authorize(
    db: Session, current_user: models.User, shop_id: int
) -> models.Shop:
    """Chỉ chủ shop và ADMIN.

    Cùng ranh giới với giá vốn (`has_cost_visibility`) chứ KHÔNG theo
    PERMISSION_REPORT: biết chi phí và lãi ròng là suy ngược ra được giá vốn,
    và lương nhân viên thì càng không phải thứ để nhân viên khác đọc. Nới ra
    sau này dễ; thu lại thì dữ liệu đã lộ rồi.
    """
    shop = require_shop_access(db, shop_id, current_user)
    require_cost_visibility(shop, current_user)
    return shop


def _clean(value: Optional[str], maximum: int) -> Optional[str]:
    return (value or "").strip()[:maximum] or None


def _operation(value: str) -> str:
    result = (value or "").strip()
    if len(result) < 8 or len(result) > 128:
        raise HTTPException(status_code=400, detail=tr("Mã thao tác không hợp lệ"))
    return result


def _key(prefix: str, operation_id: str) -> str:
    return f"{prefix}:{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()}"


def _audit(
    db: Session, user_id: Optional[int], shop_id: int, action: str, details: str
) -> None:
    """Thêm log vào transaction hiện tại; KHÔNG dùng log_system_action (nó commit)."""
    db.add(
        models.SystemLog(
            user_id=user_id,
            shop_id=shop_id,
            action=action,
            details=details[:2000],
        )
    )


# ---------------------------------------------------------------------------
# Danh mục chi phí
# ---------------------------------------------------------------------------

def ensure_categories(
    db: Session, shop_id: int, user_id: Optional[int]
) -> None:
    """Tạo sẵn danh mục mặc định cho shop chưa có danh mục nào.

    Chạy lặp lại được: shop đã có dù chỉ một danh mục (kể cả đã ẩn hết) thì
    không đụng vào nữa. Không seed lại là có chủ ý - người đã dọn sạch danh mục
    mặc định không muốn thấy chúng mọc lại sau mỗi lần mở màn hình.
    """
    da_co = (
        db.query(models.ExpenseCategory.id)
        .filter(models.ExpenseCategory.shop_id == shop_id)
        .first()
    )
    if da_co is not None:
        return
    for thu_tu, ten in enumerate(DEFAULT_CATEGORIES):
        db.add(
            models.ExpenseCategory(
                shop_id=shop_id,
                name=ten,
                is_active=True,
                sort_order=thu_tu,
                created_by_user_id=user_id,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        # Hai request cùng mở màn Chi Phí lần đầu. Unique (shop_id, name) chặn
        # bản sao; bên thua chỉ cần bỏ qua vì bên kia đã seed xong.
        db.rollback()


def _category_out(row: models.ExpenseCategory, dang_dung: int = 0) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "is_active": bool(row.is_active),
        "sort_order": int(row.sort_order or 0),
        # Danh mục đã có khoản chi thì giao diện không được hiện nút xóa.
        "expense_count": dang_dung,
    }


def list_categories(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    _authorize(db, current_user, shop_id)
    ensure_categories(db, shop_id, current_user.id)
    rows = (
        db.query(models.ExpenseCategory)
        .filter(models.ExpenseCategory.shop_id == shop_id)
        .order_by(
            models.ExpenseCategory.sort_order, models.ExpenseCategory.id
        )
        .all()
    )
    dem = dict(
        db.query(
            models.OperatingExpense.category_id,
            func.count(models.OperatingExpense.id),
        )
        .filter(models.OperatingExpense.shop_id == shop_id)
        .group_by(models.OperatingExpense.category_id)
        .all()
    )
    return {
        "categories": [_category_out(r, int(dem.get(r.id, 0))) for r in rows]
    }


def _get_category(
    db: Session, shop_id: int, category_id: int
) -> models.ExpenseCategory:
    row = (
        db.query(models.ExpenseCategory)
        .filter(
            models.ExpenseCategory.id == category_id,
            models.ExpenseCategory.shop_id == shop_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=tr("Không tìm thấy loại chi phí")
        )
    return row


def create_category(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: ExpenseCategoryCreate,
) -> Dict[str, Any]:
    _authorize(db, current_user, shop_id)
    ten = _clean(request.name, 120)
    if not ten:
        raise HTTPException(
            status_code=400, detail=tr("Tên loại chi phí không được để trống")
        )
    lon_nhat = (
        db.query(func.max(models.ExpenseCategory.sort_order))
        .filter(models.ExpenseCategory.shop_id == shop_id)
        .scalar()
    )
    row = models.ExpenseCategory(
        shop_id=shop_id,
        name=ten,
        is_active=True,
        sort_order=int(lon_nhat or 0) + 1,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Cửa hàng đã có loại chi phí tên này"),
        )
    _audit(
        db,
        current_user.id,
        shop_id,
        "CREATE_EXPENSE_CATEGORY",
        f"Thêm loại chi phí '{ten}'",
    )
    db.commit()
    db.refresh(row)
    return _category_out(row)


def update_category(
    db: Session,
    current_user: models.User,
    shop_id: int,
    category_id: int,
    request: ExpenseCategoryUpdate,
) -> Dict[str, Any]:
    """Đổi tên hoặc ẩn/hiện một loại chi phí.

    KHÔNG có đường xóa vật lý. SQLite bản production không bật khóa ngoại (bẫy
    32) nên xóa một danh mục đang được dùng sẽ không báo lỗi gì cả, chỉ để lại
    các khoản chi cũ trỏ vào hư không và báo cáo tháng trước mất tên.

    Đổi tên thì báo cáo cũ đổi nhãn theo - hợp lý khi sửa "Marketing" thành
    "Quảng cáo Facebook", nhưng sẽ làm lịch sử đổi nghĩa nếu ai đó sửa thành
    một loại khác hẳn. Vì vậy mọi lần đổi tên đều ghi lại tên cũ vào nhật ký.
    """
    _authorize(db, current_user, shop_id)
    row = _get_category(db, shop_id, category_id)
    ten_cu = row.name
    thay_doi: List[str] = []

    if request.name is not None:
        ten = _clean(request.name, 120)
        if not ten:
            raise HTTPException(
                status_code=400,
                detail=tr("Tên loại chi phí không được để trống"),
            )
        if ten != ten_cu:
            row.name = ten
            thay_doi.append(f"đổi tên '{ten_cu}' -> '{ten}'")

    if request.is_active is not None and bool(request.is_active) != bool(
        row.is_active
    ):
        row.is_active = bool(request.is_active)
        thay_doi.append("hiện lại" if row.is_active else "ẩn đi")

    if not thay_doi:
        return _category_out(row)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Cửa hàng đã có loại chi phí tên này"),
        )
    _audit(
        db,
        current_user.id,
        shop_id,
        "UPDATE_EXPENSE_CATEGORY",
        f"Loại chi phí #{category_id}: {', '.join(thay_doi)}",
    )
    db.commit()
    db.refresh(row)
    return _category_out(row)


# ---------------------------------------------------------------------------
# Mẫu chi phí định kỳ
# ---------------------------------------------------------------------------

def _template_out(
    row: models.ExpenseTemplate, ten_loai: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "id": row.id,
        "category_id": row.category_id,
        "category_name": ten_loai,
        "name": row.name,
        "amount": int(row.amount or 0),
        "day_of_month": int(row.day_of_month or 1),
        "is_active": bool(row.is_active),
        "note": row.note,
    }


def _ten_loai_theo_id(db: Session, shop_id: int) -> Dict[int, str]:
    return {
        cid: ten
        for cid, ten in db.query(
            models.ExpenseCategory.id, models.ExpenseCategory.name
        ).filter(models.ExpenseCategory.shop_id == shop_id)
    }


def list_templates(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    _authorize(db, current_user, shop_id)
    ten_loai = _ten_loai_theo_id(db, shop_id)
    rows = (
        db.query(models.ExpenseTemplate)
        .filter(models.ExpenseTemplate.shop_id == shop_id)
        .order_by(
            models.ExpenseTemplate.is_active.desc(),
            models.ExpenseTemplate.day_of_month,
            models.ExpenseTemplate.id,
        )
        .all()
    )
    return {
        "templates": [
            _template_out(r, ten_loai.get(r.category_id)) for r in rows
        ]
    }


def create_template(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: ExpenseTemplateCreate,
) -> Dict[str, Any]:
    _authorize(db, current_user, shop_id)
    loai = _get_category(db, shop_id, request.category_id)
    ten = _clean(request.name, 200) or loai.name
    row = models.ExpenseTemplate(
        shop_id=shop_id,
        category_id=loai.id,
        name=ten,
        amount=int(request.amount),
        day_of_month=int(request.day_of_month),
        is_active=True,
        note=_clean(request.note, 500),
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        current_user.id,
        shop_id,
        "CREATE_EXPENSE_TEMPLATE",
        f"Thêm chi phí cố định '{ten}' {int(request.amount):,}đ/tháng",
    )
    db.commit()
    db.refresh(row)
    return _template_out(row, loai.name)


def update_template(
    db: Session,
    current_user: models.User,
    shop_id: int,
    template_id: int,
    request: ExpenseTemplateUpdate,
) -> Dict[str, Any]:
    """Sửa mẫu nhắc nhở. Không đụng tới các khoản chi ĐÃ ghi từ mẫu này.

    Mẫu chỉ là lời nhắc. Sửa số tiền thuê từ 5 lên 6 triệu không được phép sửa
    ngược các tháng đã trả 5 triệu - đó là chứng từ đã phát sinh.
    """
    _authorize(db, current_user, shop_id)
    row = (
        db.query(models.ExpenseTemplate)
        .filter(
            models.ExpenseTemplate.id == template_id,
            models.ExpenseTemplate.shop_id == shop_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=tr("Không tìm thấy chi phí cố định")
        )

    if request.category_id is not None:
        row.category_id = _get_category(db, shop_id, request.category_id).id
    if request.name is not None:
        row.name = _clean(request.name, 200) or row.name
    if request.amount is not None:
        row.amount = int(request.amount)
    if request.day_of_month is not None:
        row.day_of_month = int(request.day_of_month)
    if request.note is not None:
        row.note = _clean(request.note, 500)
    if request.is_active is not None:
        row.is_active = bool(request.is_active)

    db.flush()
    _audit(
        db,
        current_user.id,
        shop_id,
        "UPDATE_EXPENSE_TEMPLATE",
        f"Sửa chi phí cố định #{template_id} '{row.name}'",
    )
    db.commit()
    db.refresh(row)
    return _template_out(row, _ten_loai_theo_id(db, shop_id).get(row.category_id))


# ---------------------------------------------------------------------------
# Sổ chi phí
# ---------------------------------------------------------------------------

def _expense_query(db: Session, shop_id: int):
    """Mọi câu hỏi về chi phí đi qua đây, nên bộ lọc "chưa gỡ" không thể quên."""
    return db.query(models.OperatingExpense).filter(
        models.OperatingExpense.shop_id == shop_id,
        models.OperatingExpense.voided_at.is_(None),
    )


def _expense_out(
    row: models.OperatingExpense,
    ten_loai: Optional[str] = None,
    ten_nguoi: Optional[str] = None,
) -> Dict[str, Any]:
    tra_truoc = row.amortize_end_date > row.amortize_start_date
    return {
        "id": row.id,
        "category_id": row.category_id,
        "category_name": ten_loai,
        "template_id": row.template_id,
        "amount": int(row.amount),
        "expense_date": row.expense_date,
        "amortize_start_date": row.amortize_start_date,
        "amortize_end_date": row.amortize_end_date,
        "is_amortized": tra_truoc,
        "method": row.method,
        "shift_id": row.shift_id,
        "note": row.note,
        "reference": row.reference,
        "created_at": row.created_at,
        "created_by": ten_nguoi,
        # Khoản đã rút tiền từ két thì không gỡ được: `cash_movements` là sổ
        # chỉ-ghi-thêm và ca có thể đã đóng với số tiền đếm tay khớp rồi.
        "can_void": row.cash_movement_id is None,
    }


def list_expenses(
    db: Session,
    current_user: models.User,
    shop_id: int,
    tu_ngay: Optional[str] = None,
    den_ngay: Optional[str] = None,
    page: int = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Các khoản đã chi, lọc theo NGÀY CHI (không phải khoảng phân bổ)."""
    _authorize(db, current_user, shop_id)
    if page < 1:
        raise HTTPException(status_code=400, detail=tr("page phải >= 1"))
    if per_page < 1 or per_page > MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=tr("per_page phải từ 1 đến {maximum}", maximum=MAX_PAGE_SIZE),
        )

    query = _expense_query(db, shop_id)
    bat_dau = parse_ngay(tu_ngay, "tu_ngay")
    ket_thuc = parse_ngay(den_ngay, "den_ngay")
    if bat_dau and ket_thuc and bat_dau > ket_thuc:
        raise HTTPException(
            status_code=400, detail=tr("tu_ngay không được lớn hơn den_ngay")
        )
    if bat_dau:
        query = query.filter(
            models.OperatingExpense.expense_date >= bat_dau.isoformat()
        )
    if ket_thuc:
        query = query.filter(
            models.OperatingExpense.expense_date <= ket_thuc.isoformat()
        )

    tong = query.count()
    tong_tien = sum(
        int(amount or 0)
        for (amount,) in query.with_entities(models.OperatingExpense.amount).all()
    )
    rows = (
        query.order_by(
            models.OperatingExpense.expense_date.desc(),
            models.OperatingExpense.id.desc(),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    ten_loai = _ten_loai_theo_id(db, shop_id)
    ten_nguoi = {
        uid: ten
        for uid, ten in db.query(models.User.id, models.User.username).filter(
            models.User.id.in_([r.created_by_user_id for r in rows if r.created_by_user_id])
        )
    } if any(r.created_by_user_id for r in rows) else {}
    return {
        "expenses": [
            _expense_out(
                r,
                ten_loai.get(r.category_id),
                ten_nguoi.get(r.created_by_user_id),
            )
            for r in rows
        ],
        "page": page,
        "per_page": per_page,
        "total": tong,
        "has_more": page * per_page < tong,
        # Tổng tiền ĐÃ CHI trong khoảng (dòng tiền), khác hẳn phần được tính
        # vào lãi ròng khi có khoản trả trước.
        "total_paid": int(tong_tien or 0),
    }


def create_expense(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: ExpenseCreate,
) -> Dict[str, Any]:
    """Ghi một khoản chi phí đã trả.

    Tiền mặt lấy từ ca sinh ĐÚNG MỘT `CashMovement` hướng OUT, trong cùng
    transaction với khoản chi (bẫy 32). Vì vậy không thể có cảnh két đã trừ mà
    sổ chi phí chưa ghi, hay ngược lại. Chuyển khoản và tiền ngoài không đụng
    két nên không sinh chuyển động nào - thêm vào là trừ hai lần.
    """
    _authorize(db, current_user, shop_id)
    operation_id = _operation(request.operation_id)
    amount = int(request.amount)
    if amount <= 0:
        raise HTTPException(
            status_code=400, detail=tr("Số tiền chi phải lớn hơn 0")
        )
    if request.method not in METHODS:
        raise HTTPException(
            status_code=400, detail=tr("Phương thức trả tiền không hợp lệ")
        )
    note = _clean(request.note, 500)
    if request.method == METHOD_OUTSIDE and note is None:
        raise HTTPException(
            status_code=400,
            detail=tr("Trả bằng tiền ngoài két phải nhập ghi chú"),
        )

    ngay_chi = parse_ngay(request.expense_date, "expense_date") or today_vn()
    bat_dau = parse_ngay(request.amortize_start_date, "amortize_start_date") or ngay_chi
    if request.amortize_months is None:
        ket_thuc = bat_dau
    else:
        so_thang = int(request.amortize_months)
        if so_thang < 1 or so_thang > MAX_AMORTIZE_MONTHS:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Số tháng phân bổ phải từ 1 đến {maximum}",
                    maximum=MAX_AMORTIZE_MONTHS,
                ),
            )
        ket_thuc = moc_ket_thuc_phan_bo(bat_dau, so_thang)
    if ket_thuc < bat_dau:
        raise HTTPException(
            status_code=400,
            detail=tr("Ngày kết thúc phân bổ không được trước ngày bắt đầu"),
        )

    # Idempotency: bấm hai lần là trừ két hai lần. Kiểm trước cho đường thường,
    # unique index bên dưới lo hai request chạy song song.
    truoc = (
        db.query(models.OperatingExpense)
        .filter(models.OperatingExpense.idempotency_key == operation_id)
        .first()
    )
    if truoc is not None:
        if truoc.shop_id != shop_id:
            raise HTTPException(
                status_code=409,
                detail=tr("Mã thao tác đã được dùng cho một cửa hàng khác"),
            )
        ten_loai = _ten_loai_theo_id(db, shop_id)
        result = _expense_out(truoc, ten_loai.get(truoc.category_id))
        result["repeated"] = True
        return result

    loai = _get_category(db, shop_id, request.category_id)
    if not loai.is_active:
        raise HTTPException(
            status_code=400,
            detail=tr("Loại chi phí này đang bị ẩn; hãy hiện lại hoặc chọn loại khác"),
        )

    template_id = None
    if request.template_id is not None:
        mau = (
            db.query(models.ExpenseTemplate)
            .filter(
                models.ExpenseTemplate.id == request.template_id,
                models.ExpenseTemplate.shop_id == shop_id,
            )
            .first()
        )
        if mau is None:
            raise HTTPException(
                status_code=404, detail=tr("Không tìm thấy chi phí cố định")
            )
        template_id = mau.id

    auth_session_service.fence_live_auth_session(db)
    row = models.OperatingExpense(
        shop_id=shop_id,
        category_id=loai.id,
        template_id=template_id,
        amount=amount,
        expense_date=ngay_chi.isoformat(),
        amortize_start_date=bat_dau.isoformat(),
        amortize_end_date=ket_thuc.isoformat(),
        method=request.method,
        note=note,
        reference=_clean(request.reference, 128),
        idempotency_key=operation_id,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        lap = (
            db.query(models.OperatingExpense)
            .filter(
                models.OperatingExpense.idempotency_key == operation_id,
                models.OperatingExpense.shop_id == shop_id,
            )
            .first()
        )
        if lap is None:
            raise
        ten_loai = _ten_loai_theo_id(db, shop_id)
        result = _expense_out(lap, ten_loai.get(lap.category_id))
        result["repeated"] = True
        return result

    if request.method == METHOD_CASH_SHIFT:
        movement, _ = shift_service.add_external_cash_out(
            db,
            current_user,
            shop_id,
            amount=amount,
            operation_id=_key("expense-cash", operation_id),
            # Ghi chú chung, KHÔNG kèm loại chi phí: thu ngân và MANAGER xem
            # được chi tiết ca để đối chiếu két, mà "Lương nhân viên 8.000.000đ"
            # hiện ở đó là lộ đúng thứ `require_cost_visibility` đang giữ kín.
            note="Chi phí vận hành",
        )
        row.shift_id = movement.shift_id
        row.cash_movement_id = movement.id
        db.flush()

    _audit(
        db,
        current_user.id,
        shop_id,
        "CREATE_OPERATING_EXPENSE",
        (
            f"Chi phí '{loai.name}' {amount:,}đ ngày {ngay_chi.isoformat()} "
            f"({request.method})"
            + (
                f", phân bổ {bat_dau.isoformat()} -> {ket_thuc.isoformat()}"
                if ket_thuc > bat_dau
                else ""
            )
        ),
    )
    db.commit()
    db.refresh(row)
    return _expense_out(row, loai.name, current_user.username)


def void_expense(
    db: Session, current_user: models.User, shop_id: int, expense_id: int
) -> Dict[str, Any]:
    """Gỡ một khoản chi ghi nhầm.

    CHỈ gỡ được khoản KHÔNG rút tiền từ ca. Két là sổ chỉ-ghi-thêm; ca có thể
    đã đóng với số tiền đếm tay khớp đúng, và gỡ ngược sẽ làm số đã chốt đó sai
    vĩnh viễn. Ghi nhầm khoản tiền mặt thì bù bằng chức năng Thu tiền vào ca có
    sẵn, kèm ghi chú - đường đó để lại dấu vết, đường xóa thì không.
    """
    _authorize(db, current_user, shop_id)
    row = (
        db.query(models.OperatingExpense)
        .filter(
            models.OperatingExpense.id == expense_id,
            models.OperatingExpense.shop_id == shop_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=tr("Không tìm thấy khoản chi")
        )
    if row.voided_at is not None:
        return {"ok": True, "repeated": True}
    if row.cash_movement_id is not None:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Khoản này đã lấy tiền từ ca nên không gỡ được. "
                "Hãy dùng Thu tiền vào ca để bù lại, kèm ghi chú lý do."
            ),
        )

    auth_session_service.fence_live_auth_session(db)
    row.voided_at = datetime.utcnow()
    row.voided_by_user_id = current_user.id
    _audit(
        db,
        current_user.id,
        shop_id,
        "VOID_OPERATING_EXPENSE",
        f"Gỡ khoản chi #{expense_id} {int(row.amount):,}đ ngày {row.expense_date}",
    )
    db.commit()
    return {"ok": True, "repeated": False}


# ---------------------------------------------------------------------------
# Nhắc nhở chi phí cố định
# ---------------------------------------------------------------------------

def _bien_thang(thang: Optional[str]) -> tuple[str, str]:
    """'YYYY-MM' -> (ngày đầu tháng, ngày cuối tháng)."""
    if thang and thang.strip():
        try:
            moc = datetime.strptime(thang.strip(), "%Y-%m").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=tr("Tháng phải theo định dạng YYYY-MM"),
            )
    else:
        hom_nay = today_vn()
        moc = date(hom_nay.year, hom_nay.month, 1)
    cuoi = date(
        moc.year, moc.month, calendar.monthrange(moc.year, moc.month)[1]
    )
    return moc.isoformat(), cuoi.isoformat()


def reminders(
    db: Session,
    current_user: models.User,
    shop_id: int,
    thang: Optional[str] = None,
) -> Dict[str, Any]:
    """Chi phí cố định của tháng này còn thiếu bao nhiêu.

    So theo TỔNG ĐÃ GHI trong tháng chứ không theo cờ đã-ghi/chưa-ghi. Lý do:
    lương hay trả làm hai đợt (tạm ứng giữa tháng rồi trả nốt), tiền điện cũng
    có tháng đóng làm hai lần. Dùng cờ thì lời nhắc tắt ngay sau lần tạm ứng và
    phần còn lại bị quên luôn.
    """
    _authorize(db, current_user, shop_id)
    dau_thang, cuoi_thang = _bien_thang(thang)

    mau = (
        db.query(models.ExpenseTemplate)
        .filter(
            models.ExpenseTemplate.shop_id == shop_id,
            models.ExpenseTemplate.is_active.is_(True),
        )
        .order_by(models.ExpenseTemplate.day_of_month, models.ExpenseTemplate.id)
        .all()
    )
    if not mau:
        return {"month": dau_thang[:7], "items": [], "total_missing": 0}

    paid_rows = (
        _expense_query(db, shop_id)
        .with_entities(
            models.OperatingExpense.template_id,
            models.OperatingExpense.amount,
        )
        .filter(
            models.OperatingExpense.template_id.in_([m.id for m in mau]),
            models.OperatingExpense.expense_date >= dau_thang,
            models.OperatingExpense.expense_date <= cuoi_thang,
        )
        .all()
    )
    da_ghi: Dict[int, int] = {}
    for template_id, amount in paid_rows:
        da_ghi[int(template_id)] = da_ghi.get(int(template_id), 0) + int(amount or 0)
    ten_loai = _ten_loai_theo_id(db, shop_id)

    items: List[Dict[str, Any]] = []
    for m in mau:
        # Mẫu vừa khai hôm nay thì không đi nhắc các tháng đã qua: khoản đó
        # chưa từng tồn tại lúc ấy, nhắc là bịa ra một món nợ.
        if m.created_at is not None and m.created_at.date().isoformat() > cuoi_thang:
            continue
        can = int(m.amount or 0)
        ghi = int(da_ghi.get(m.id, 0) or 0)
        thieu = max(can - ghi, 0)
        if thieu <= 0:
            continue
        items.append({
            "template_id": m.id,
            "category_id": m.category_id,
            "category_name": ten_loai.get(m.category_id),
            "name": m.name,
            "expected_amount": can,
            "paid_amount": ghi,
            "missing_amount": thieu,
            "day_of_month": int(m.day_of_month or 1),
        })
    return {
        "month": dau_thang[:7],
        "items": items,
        "total_missing": sum(i["missing_amount"] for i in items),
    }


# ---------------------------------------------------------------------------
# Số liệu cho báo cáo (report_service gọi)
# ---------------------------------------------------------------------------

def _khoan_giao_voi_ky(
    db: Session, shop_id: int, tu: Optional[date], den: date
) -> Sequence[models.OperatingExpense]:
    """Các khoản có phần phân bổ rơi vào [tu, den].

    So chuỗi ISO là đủ và đúng: 'YYYY-MM-DD' sắp xếp theo thứ tự từ điển trùng
    với thứ tự thời gian.
    """
    query = _expense_query(db, shop_id).filter(
        models.OperatingExpense.amortize_start_date <= den.isoformat()
    )
    if tu:
        query = query.filter(
            models.OperatingExpense.amortize_end_date >= tu.isoformat()
        )
    return query.all()


def tong_hop_chi_phi(
    db: Session,
    shop_id: int,
    tu_ngay: Optional[str],
    den_ngay: Optional[str],
) -> Dict[str, Any]:
    """Chi phí được TÍNH VÀO LÃI trong kỳ, cộng số trả trước còn lại.

    Mốc cuối bị chặn ở HÔM NAY. Xem "tháng 8" vào ngày 8/8 mà tính đủ tiền nhà
    cả tháng trong khi doanh thu mới có 8 ngày là ra một con số lỗ không có
    thật - và chủ shop nhìn con số đó thường quyết định sai (cắt người, ngừng
    nhập hàng). Cả hai vế phải cùng dừng ở một mốc thì so sánh mới có nghĩa.
    """
    bat_dau = parse_ngay(tu_ngay, "tu_ngay")
    ket_thuc = parse_ngay(den_ngay, "den_ngay")
    hom_nay = today_vn()
    moc_cuoi = min(ket_thuc, hom_nay) if ket_thuc else hom_nay

    if bat_dau and bat_dau > moc_cuoi:
        # Kỳ nằm hoàn toàn trong tương lai: chưa có gì để tính.
        return {
            "operating_expense_total": 0,
            "expense_by_category": [],
            "prepaid_remaining": 0,
            "prepaid_details": [],
            "expense_through_date": moc_cuoi.isoformat(),
        }

    khoan = _khoan_giao_voi_ky(db, shop_id, bat_dau, moc_cuoi)
    ten_loai = _ten_loai_theo_id(db, shop_id)

    theo_loai: Dict[int, int] = {}
    tra_truoc_theo_loai: Dict[int, int] = {}
    tong = 0
    for e in khoan:
        phan = phan_bo_trong_ky(e, bat_dau, moc_cuoi)
        if phan:
            tong += phan
            theo_loai[e.category_id] = theo_loai.get(e.category_id, 0) + phan
        # Tiền đã ra khỏi túi nhưng chưa được tính hết vào chi phí. Chỉ đếm
        # khoản đã trả rồi (`expense_date` <= mốc), vì đó mới là tiền đã đi.
        if e.expense_date <= moc_cuoi.isoformat():
            con = con_tra_truoc(e, moc_cuoi)
            if con > 0:
                tra_truoc_theo_loai[e.category_id] = (
                    tra_truoc_theo_loai.get(e.category_id, 0) + con
                )

    return {
        "operating_expense_total": tong,
        "expense_by_category": sorted(
            (
                {
                    "category_id": cid,
                    "category_name": ten_loai.get(cid),
                    "amount": tien,
                }
                for cid, tien in theo_loai.items()
            ),
            key=lambda r: r["amount"],
            reverse=True,
        ),
        "prepaid_remaining": sum(tra_truoc_theo_loai.values()),
        "prepaid_details": sorted(
            (
                {
                    "category_id": cid,
                    "category_name": ten_loai.get(cid),
                    "amount": tien,
                }
                for cid, tien in tra_truoc_theo_loai.items()
            ),
            key=lambda r: r["amount"],
            reverse=True,
        ),
        "expense_through_date": moc_cuoi.isoformat(),
    }


def tien_chi_theo_ngay(
    db: Session,
    shop_id: int,
    tu_ngay: Optional[str],
    den_ngay: Optional[str],
) -> Dict[str, Any]:
    """Tiền chi phí THỰC RA KHỎI TÚI trong kỳ, gom theo ngày chi.

    Khác hẳn `tong_hop_chi_phi`: ở đây lấy nguyên số đã trả vào đúng ngày trả,
    không phân bổ gì cả. Trả trước 30 triệu tiền nhà thì dòng tiền phải thấy đủ
    30 triệu ra khỏi két hôm đó.
    """
    query = _expense_query(db, shop_id)
    bat_dau = parse_ngay(tu_ngay, "tu_ngay")
    ket_thuc = parse_ngay(den_ngay, "den_ngay")
    if bat_dau:
        query = query.filter(
            models.OperatingExpense.expense_date >= bat_dau.isoformat()
        )
    if ket_thuc:
        query = query.filter(
            models.OperatingExpense.expense_date <= ket_thuc.isoformat()
        )
    rows = (
        query.with_entities(
            models.OperatingExpense.expense_date,
            models.OperatingExpense.amount,
        )
        .all()
    )
    theo_ngay: Dict[str, int] = {}
    for ngay, tien in rows:
        theo_ngay[ngay] = theo_ngay.get(ngay, 0) + int(tien or 0)
    return {"total": sum(theo_ngay.values()), "by_date": theo_ngay}


__all__ = [
    "DEFAULT_CATEGORIES",
    "METHOD_CASH_SHIFT",
    "METHOD_OUTSIDE",
    "METHOD_TRANSFER",
    "cong_thang",
    "con_tra_truoc",
    "create_category",
    "create_expense",
    "create_template",
    "ensure_categories",
    "list_categories",
    "list_expenses",
    "list_templates",
    "moc_ket_thuc_phan_bo",
    "phan_bo_trong_ky",
    "reminders",
    "tien_chi_theo_ngay",
    "today_vn",
    "tong_hop_chi_phi",
    "update_category",
    "update_template",
    "void_expense",
]
