"""Trả hàng: khách mang hàng đã mua quay lại, shop hoàn tiền.

Khác hẳn ba thứ dễ nhầm với nó:

- **Hủy đơn** (`order_service.cancel_order`) chỉ áp dụng cho đơn CHƯA thanh
  toán. Hàng chưa ra khỏi cửa, tiền chưa vào.
- **Hoàn khoản chuyển thừa** (`order_service.complete_refund`) là trả lại phần
  tiền khách chuyển dư, hàng vẫn thuộc về khách. Chỉ xảy ra một lần cho một đơn.
- **Trả hàng** là hàng quay về, tiền đi ra, và xảy ra được NHIỀU LẦN trên cùng
  một đơn (khách mua 5 món, hôm nay trả 1, tuần sau trả thêm 2).

Đơn giữ nguyên trạng thái `PAID`. Hóa đơn đã xuất là sự thật lịch sử; việc trả
là sự kiện xảy ra sau đó, nằm ở bảng `order_returns` chứ không xóa lần bán.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..core.i18n import tr
from ..core.money import checked_add, cumulative_basis
from ..core.numeric_limits import MAX_SAFE_QUANTITY
from ..dependencies import (
    PERMISSION_ORDER_RETURN,
    require_shop_access,
    require_staff_permission,
)
from ..schemas.order import (
    OrderReturnApprovalCreate,
    OrderReturnCreate,
    OrderReturnDraft,
)
from . import approval_service, inventory_service, loyalty_service, order_service

ENTRY_RETURN_CASH = "RETURN_CASH"
ENTRY_RETURN_TRANSFER = "RETURN_TRANSFER"

MONEY_EPSILON = order_service.MONEY_EPSILON

def _khoa_thao_tac(operation_id: str) -> str:
    """Khóa chống bấm lặp. Tiền tố `return:` tách hẳn khỏi `refund:` của chu kỳ
    hoàn tiền chuyển thừa, nên hai nghiệp vụ không bao giờ nhận nhầm thao tác
    của nhau kể cả khi client vô tình dùng lại cùng một operation_id."""
    ma = operation_id.strip()
    if len(ma) < 8:
        raise HTTPException(
            status_code=400,
            detail=tr("Mã thao tác trả hàng không hợp lệ"),
        )
    return "return:" + hashlib.sha256(ma.encode("utf-8")).hexdigest()


def _return_operation_fingerprint(request: OrderReturnDraft) -> str:
    """Dấu vân tay của đúng yêu cầu mà một operation_id đại diện."""
    payload = {
        "items": sorted(
            (
                {
                    "order_item_id": int(item.order_item_id),
                    "quantity": int(item.quantity),
                    "restock": bool(item.restock),
                }
                for item in request.items
            ),
            key=lambda item: item["order_item_id"],
        ),
        "method": request.method,
        "reason": (request.reason or "").strip()[:200] or None,
        "note": (request.note or "").strip()[:500] or None,
        "reference": (request.reference or "").strip()[:128] or None,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_return_fingerprint(ban_ghi: models.OrderReturn) -> str:
    """Dựng fingerprint cho phiếu legacy chưa có cột ảnh chụp."""
    payload = {
        "items": sorted(
            (
                {
                    "order_item_id": int(item.order_item_id),
                    "quantity": int(item.quantity),
                    "restock": bool(item.restocked),
                }
                for item in ban_ghi.items
            ),
            key=lambda item: item["order_item_id"],
        ),
        "method": ban_ghi.refund_method,
        "reason": (ban_ghi.reason or "").strip()[:200] or None,
        "note": (ban_ghi.note or "").strip()[:500] or None,
        "reference": (ban_ghi.reference or "").strip()[:128] or None,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_return(ban_ghi: models.OrderReturn) -> Dict[str, Any]:
    return {
        "id": ban_ghi.id,
        "order_id": ban_ghi.order_id,
        "refund_amount": ban_ghi.refund_amount,
        "refund_method": ban_ghi.refund_method,
        "reason": ban_ghi.reason,
        "note": ban_ghi.note,
        "reference": ban_ghi.reference,
        "loyalty_points_restored": int(ban_ghi.loyalty_points_restored or 0),
        "loyalty_points_reversed": int(ban_ghi.loyalty_points_reversed or 0),
        "created_by_user_id": ban_ghi.created_by_user_id,
        "shift_id": ban_ghi.shift_id,
        "manager_approval_id": ban_ghi.manager_approval_id,
        "approved_by_user_id": (
            ban_ghi.manager_approval.approver_user_id
            if ban_ghi.manager_approval is not None
            else None
        ),
        "created_at": ban_ghi.created_at,
        "items": [
            {
                "order_item_id": d.order_item_id,
                "product_id": d.product_id,
                "product_name": d.product_name,
                "quantity": d.quantity,
                "unit_price": d.unit_price,
                "refund_amount": d.refund_amount,
                "restocked": bool(d.restocked),
            }
            for d in ban_ghi.items
        ],
    }


def da_tra_theo_dong(db: Session, order_id: int) -> Dict[int, int]:
    """Persisted cumulative returned quantity for each immutable order line."""
    rows = (
        db.query(
            models.OrderItem.id,
            models.OrderItem.returned_total_qty,
        )
        .filter(models.OrderItem.order_id == order_id)
        .all()
    )
    return {int(order_item_id): int(quantity or 0) for order_item_id, quantity in rows}


def danh_sach_phieu_tra(db: Session, order_id: int) -> List[Dict[str, Any]]:
    ban_ghi = (
        db.query(models.OrderReturn)
        .filter(models.OrderReturn.order_id == order_id)
        .order_by(models.OrderReturn.id)
        .all()
    )
    return [_serialize_return(r) for r in ban_ghi]


def tong_da_hoan(db: Session, order_id: int) -> int:
    """Tổng tiền đã hoàn cho khách qua MỌI lần trả hàng của đơn."""
    values = (
        db.query(models.OrderReturn.refund_amount)
        .filter(models.OrderReturn.order_id == order_id)
        .all()
    )
    total = 0
    for (value,) in values:
        total = checked_add(total, int(value or 0))
    return total


def _la_tra_het(
    dong_don: Dict[int, models.OrderItem],
    da_tra: Dict[int, int],
    chi_tiet: List[Dict[str, Any]],
) -> bool:
    """Lần này có trả hết toàn bộ số lượng còn lại của đơn hay không."""
    return bool(dong_don) and all(
        d_qty(chi_tiet, dong.id) + da_tra.get(dong.id, 0)
        == int(dong.quantity or 0)
        for dong in dong_don.values()
    )


def _diem_theo_ty_le(tong_diem: int, tu_so: int, mau_so: int) -> int:
    """Phân bổ điểm nguyên theo tỷ lệ và luôn làm tròn xuống.

    Tử và mẫu đều là số nguyên; Python arbitrary-size giữ phép nhân chính xác.
    Trả hết được xử lý riêng ở caller để khớp tuyệt đối.
    """
    if tong_diem <= 0 or tu_so <= MONEY_EPSILON or mau_so <= MONEY_EPSILON:
        return 0
    result = (int(tong_diem) * int(tu_so)) // int(mau_so)
    return min(max(result, 0), tong_diem)


def _tinh_dieu_chinh_diem(
    db: Session,
    order: models.Order,
    dong_don: Dict[int, models.OrderItem],
    da_tra: Dict[int, int],
    chi_tiet: List[Dict[str, Any]],
    event_at: datetime,
) -> tuple[int, int]:
    """Trả ``(điểm kiếm bị trừ, điểm đã dùng được hoàn)`` của lần trả này.

    Cả hai phép tính đều dựa trên MỨC LŨY KẾ sau lần trả hiện tại rồi trừ phần
    các phiếu trước đã xử lý. Nhờ vậy tách một lần trả thành nhiều phiếu không
    thay đổi tổng điểm vì làm tròn ở từng phiếu.
    """
    earned = int(order.loyalty_points_earned or 0)
    redeemed = int(order.loyalty_points_redeemed or 0)
    if earned <= 0 and redeemed <= 0:
        return 0, 0
    if order.customer_id is None:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Đơn có phát sinh điểm nhưng không còn hồ sơ khách hàng; "
                "chưa thể tự điều chỉnh điểm"
            ),
        )

    previous_adjustments = (
        db.query(
            models.OrderReturn.loyalty_points_reversed,
            models.OrderReturn.loyalty_points_restored,
        )
        .filter(models.OrderReturn.order_id == order.id)
        .all()
    )
    # Aggregate with Python arbitrary-size integers.  SQLite SUM is int64 and
    # must not become a hidden overflow boundary for cumulative provenance.
    reversed_before = sum(int(row[0] or 0) for row in previous_adjustments)
    restored_before = sum(int(row[1] or 0) for row in previous_adjustments)
    if reversed_before > earned or restored_before > redeemed:
        raise HTTPException(
            status_code=409,
            detail=tr("Lịch sử điều chỉnh điểm của đơn không khớp; chưa thể nhận trả"),
        )

    tra_het = _la_tra_het(dong_don, da_tra, chi_tiet)

    # Điểm đã kiếm: xét lại số điểm khách đáng được giữ theo đúng số tiền shop
    # còn giữ sau TẤT CẢ các lần hoàn. Dùng tỷ lệ đã chụp trên đơn, không đọc
    # cấu hình hiện tại vì chủ shop có thể đã đổi chương trình từ sau lúc bán.
    if tra_het:
        reversed_target = earned
    elif earned > 0:
        earn_amount = order.loyalty_earn_amount_step
        earn_points = order.loyalty_earn_points_step
        if earn_amount is None or int(earn_amount) <= 0 or not earn_points:
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Đơn thiếu ảnh chụp tỷ lệ cộng điểm; chưa thể tự điều chỉnh điểm"
                ),
            )
        # `phieu` đã flush trước khi vào hàm này, nên tổng trong DB đã gồm
        # `tien_hoan` của lần hiện tại. Cộng thêm lần nữa sẽ trừ điểm quá tay.
        refunded_after = tong_da_hoan(db, order.id)
        retained_amount = max(int(order.total_amount or 0) - refunded_after, 0)
        kept = loyalty_service.calculate_earn(
            {
                "enabled": True,
                "earn_amount": int(earn_amount),
                "earn_points": int(earn_points),
            },
            retained_amount,
        )
        reversed_target = max(earned - min(kept, earned), 0)
    else:
        reversed_target = 0

    # Điểm đã dùng: hoàn theo tỷ lệ lũy kế GIÁ NIÊM YẾT của hàng đã trả trên
    # tổng giá niêm yết. Tiền mặt đã được cumulative line target giảm tương ứng nên
    # hoàn lại phần điểm này không làm khách nhận gấp đôi.
    if tra_het:
        restored_target = redeemed
    elif redeemed > 0:
        subtotal = sum(
            int(dong.price or 0) * int(dong.quantity or 0)
            for dong in dong_don.values()
        )
        nominal_returned_before = sum(
            int(dong.price or 0) * da_tra.get(dong.id, 0)
            for dong in dong_don.values()
        )
        nominal_returned_after = nominal_returned_before + sum(
            int(d["tien_hang"] or 0) for d in chi_tiet
        )
        restored_target = _diem_theo_ty_le(
            redeemed, nominal_returned_after, subtotal
        )
    else:
        restored_target = 0

    if reversed_target < reversed_before or restored_target < restored_before:
        raise HTTPException(
            status_code=409,
            detail=tr("Lịch sử trả hàng và điểm không khớp; chưa thể nhận trả"),
        )
    reversible_now, ledger_reversed_before = (
        loyalty_service.reversible_points_for_order(
            db,
            order.shop_id,
            order.customer_id,
            order.id,
            reversed_target,
            as_of=event_at,
        )
    )
    if ledger_reversed_before != reversed_before:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Sổ điểm và lịch sử trả hàng không khớp; chưa thể nhận trả"
            ),
        )
    return reversible_now, restored_target - restored_before


def bo_sung_thong_tin_tra_hang(
    db: Session, chi_tiet: Dict[str, Any]
) -> Dict[str, Any]:
    """Gắn lịch sử trả hàng vào chi tiết đơn do `order_service` dựng.

    Router gọi hai service nối tiếp thay vì để `order_service` import ngược lên
    đây - giữ đúng một chiều phụ thuộc (return_service -> order_service) và
    không sinh vòng import.
    """
    order_id = chi_tiet["id"]
    da_tra = da_tra_theo_dong(db, order_id)
    for dong in chi_tiet.get("items", []):
        so_da_tra = da_tra.get(dong["id"], 0)
        dong["returned_quantity"] = so_da_tra
        dong["returnable_quantity"] = max(int(dong["quantity"] or 0) - so_da_tra, 0)
    chi_tiet["returns"] = danh_sach_phieu_tra(db, order_id)
    chi_tiet["returned_total"] = tong_da_hoan(db, order_id)
    return chi_tiet


def _return_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": tr(message)},
    )


def _kiem_yeu_cau(request: OrderReturnDraft) -> None:
    if not (request.reason or "").strip():
        raise _return_error(
            400, "RETURN_REASON_REQUIRED", "Phải nhập lý do trả hàng"
        )
    if not request.items:
        raise HTTPException(
            status_code=400,
            detail=tr("Chưa chọn dòng hàng nào để trả"),
        )
    ids = [it.order_item_id for it in request.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(
            status_code=400,
            detail=tr("Một dòng hàng xuất hiện nhiều lần trong phiếu trả"),
        )
    for it in request.items:
        if (
            it.quantity is None
            or it.quantity <= 0
            or it.quantity > MAX_SAFE_QUANTITY
        ):
            raise HTTPException(
                status_code=400,
                detail=tr("Số lượng trả nằm ngoài giới hạn"),
            )


def _line_return_delta(dong: models.OrderItem, quantity: int) -> Dict[str, int]:
    """Compute cumulative known-first money/cost targets minus persisted counters."""
    sold = int(dong.quantity or 0)
    returned = int(dong.returned_total_qty or 0)
    returned_known = int(dong.returned_known_qty or 0)
    returned_unknown = int(dong.returned_unknown_qty or 0)
    returned_basis = int(dong.returned_cost_basis_vnd or 0)
    returned_refund = int(dong.returned_refund_vnd or 0)
    return_version = int(dong.cost_return_version or 0)
    known = int(dong.cost_known_qty or 0)
    unknown = int(dong.cost_unknown_qty or 0)
    basis = int(dong.cost_basis_vnd or 0)
    net = int(dong.net_amount_vnd or 0)
    if (
        min(sold, returned, returned_known, returned_unknown, returned_basis, returned_refund, return_version, known, unknown, basis, net) < 0
        or known + unknown != sold
        or returned_known + returned_unknown != returned
        or returned > sold
        or returned_known > known
        or returned_unknown > unknown
        or returned_basis > basis
        or returned_refund > net
    ):
        raise HTTPException(status_code=409, detail=tr("Provenance trả hàng của dòng đơn không hợp lệ"))
    target = returned + int(quantity)
    if target > sold:
        raise HTTPException(status_code=400, detail=tr("Số lượng trả vượt số đã bán"))
    known_target = min(target, known)
    unknown_target = target - known_target
    basis_target = cumulative_basis(basis, known, known_target) if known else 0
    refund_target = cumulative_basis(net, sold, target) if sold else 0
    result = {
        "quantity": target - returned,
        "known": known_target - returned_known,
        "unknown": unknown_target - returned_unknown,
        "basis": basis_target - returned_basis,
        "refund": refund_target - returned_refund,
        "target": target,
        "known_target": known_target,
        "unknown_target": unknown_target,
        "basis_target": basis_target,
        "refund_target": refund_target,
        "old_total": returned,
        "old_known": returned_known,
        "old_unknown": returned_unknown,
        "old_basis": returned_basis,
        "old_refund": returned_refund,
        "old_version": return_version,
    }
    if min(result["quantity"], result["known"], result["unknown"], result["basis"], result["refund"]) < 0:
        raise HTTPException(status_code=409, detail=tr("Cumulative target trả hàng bị lùi"))
    return result


def _batch_return_deltas(
    db: Session,
    dong: models.OrderItem,
    line_delta: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Allocate cumulative line targets back to immutable outbound batch sources."""
    sources = (
        db.query(models.OrderItemBatch)
        .filter(models.OrderItemBatch.order_item_id == dong.id)
        .order_by(models.OrderItemBatch.id)
        .all()
    )
    if not sources:
        return []
    if sum(int(source.quantity or 0) for source in sources) != int(dong.quantity or 0):
        raise HTTPException(status_code=409, detail=tr("Phân bổ lô của dòng đơn không khớp"))

    known_left = int(line_delta["known_target"])
    unknown_left = int(line_delta["unknown_target"])
    targets: Dict[int, Dict[str, int]] = {}
    for source in sources:
        source_known = int(source.cost_known_qty or 0)
        target_known = min(source_known, known_left)
        known_left -= target_known
        targets[source.id] = {"known": target_known, "unknown": 0}
    for source in sources:
        source_unknown = int(source.cost_unknown_qty or 0)
        target_unknown = min(source_unknown, unknown_left)
        unknown_left -= target_unknown
        targets[source.id]["unknown"] = target_unknown
    if known_left or unknown_left:
        raise HTTPException(status_code=409, detail=tr("Không phân bổ được provenance lô khi trả hàng"))

    deltas: List[Dict[str, Any]] = []
    for source in sources:
        known = int(source.cost_known_qty or 0)
        unknown = int(source.cost_unknown_qty or 0)
        basis = int(source.cost_basis_vnd or 0)
        returned = int(source.returned_total_qty or 0)
        returned_known = int(source.returned_known_qty or 0)
        returned_unknown = int(source.returned_unknown_qty or 0)
        returned_basis = int(source.returned_cost_basis_vnd or 0)
        return_version = int(source.cost_return_version or 0)
        if (
            min(known, unknown, basis, returned, returned_known, returned_unknown, returned_basis, return_version) < 0
            or known + unknown != int(source.quantity or 0)
            or returned_known + returned_unknown != returned
            or returned_known > known
            or returned_unknown > unknown
            or returned_basis > basis
        ):
            raise HTTPException(status_code=409, detail=tr("Provenance nguồn lô không hợp lệ"))
        target_known = targets[source.id]["known"]
        target_unknown = targets[source.id]["unknown"]
        target_basis = cumulative_basis(basis, known, target_known) if known else 0
        delta = {
            "source": source,
            "quantity": target_known + target_unknown - returned,
            "known": target_known - returned_known,
            "unknown": target_unknown - returned_unknown,
            "basis": target_basis - returned_basis,
            "target_total": target_known + target_unknown,
            "target_known": target_known,
            "target_unknown": target_unknown,
            "target_basis": target_basis,
            "old_total": returned,
            "old_known": returned_known,
            "old_unknown": returned_unknown,
            "old_basis": returned_basis,
            "old_version": return_version,
        }
        if min(delta["quantity"], delta["known"], delta["unknown"], delta["basis"]) < 0:
            raise HTTPException(status_code=409, detail=tr("Cumulative target lô bị lùi"))
        if delta["quantity"]:
            deltas.append(delta)
    if (
        sum(row["quantity"] for row in deltas) != line_delta["quantity"]
        or sum(row["known"] for row in deltas) != line_delta["known"]
        or sum(row["unknown"] for row in deltas) != line_delta["unknown"]
        or sum(row["basis"] for row in deltas) != line_delta["basis"]
    ):
        raise HTTPException(status_code=409, detail=tr("Tổng provenance lô trả hàng không khớp dòng"))
    return deltas


def _conditional_advance_line_return(
    db: Session,
    dong: models.OrderItem,
    delta: Dict[str, int],
) -> None:
    """Persist a cumulative line target only if every observed counter is current."""
    result = db.execute(
        update(models.OrderItem)
        .where(
            models.OrderItem.id == dong.id,
            models.OrderItem.returned_total_qty == delta["old_total"],
            models.OrderItem.returned_known_qty == delta["old_known"],
            models.OrderItem.returned_unknown_qty == delta["old_unknown"],
            models.OrderItem.returned_cost_basis_vnd == delta["old_basis"],
            models.OrderItem.returned_refund_vnd == delta["old_refund"],
            models.OrderItem.cost_return_version == delta["old_version"],
        )
        .values(
            returned_total_qty=delta["target"],
            returned_known_qty=delta["known_target"],
            returned_unknown_qty=delta["unknown_target"],
            returned_cost_basis_vnd=delta["basis_target"],
            returned_refund_vnd=delta["refund_target"],
            cost_return_version=models.OrderItem.cost_return_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Trạng thái trả hàng đã thay đổi, vui lòng thử lại"),
        )


def _conditional_advance_batch_return(
    db: Session,
    source: models.OrderItemBatch,
    delta: Dict[str, Any],
) -> None:
    """Persist one source-allocation target with a full stale-state predicate."""
    result = db.execute(
        update(models.OrderItemBatch)
        .where(
            models.OrderItemBatch.id == source.id,
            models.OrderItemBatch.returned_total_qty == delta["old_total"],
            models.OrderItemBatch.returned_known_qty == delta["old_known"],
            models.OrderItemBatch.returned_unknown_qty == delta["old_unknown"],
            models.OrderItemBatch.returned_cost_basis_vnd == delta["old_basis"],
            models.OrderItemBatch.cost_return_version == delta["old_version"],
        )
        .values(
            returned_total_qty=delta["target_total"],
            returned_known_qty=delta["target_known"],
            returned_unknown_qty=delta["target_unknown"],
            returned_cost_basis_vnd=delta["target_basis"],
            cost_return_version=models.OrderItemBatch.cost_return_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Trạng thái nguồn lô trả hàng đã thay đổi, vui lòng thử lại"),
        )


def _phieu_da_ghi(
    db: Session,
    operation_key: str,
    order_id: int,
    operation_fingerprint: str,
) -> Optional[models.OrderReturn]:
    """Phiếu trả đã tạo trước đó với đúng mã thao tác này.

    Cùng mã nhưng khác đơn là client đang dùng lại id cho một việc khác - đó là
    lỗi thật, không phải retry, nên phải nổ ra chứ không im lặng.
    """
    truoc = (
        db.query(models.OrderReturn)
        .filter(models.OrderReturn.idempotency_key == operation_key)
        .first()
    )
    if truoc is None:
        return None
    if truoc.order_id != order_id:
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác trả hàng đã được dùng cho một đơn khác"),
        )
    stored_fingerprint = (
        truoc.operation_fingerprint or _stored_return_fingerprint(truoc)
    )
    if stored_fingerprint != operation_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Mã thao tác trả hàng đã được dùng cho một yêu cầu khác"
            ),
        )
    return truoc


def _classify_return_source(db: Session, order: models.Order) -> str:
    if order.payment_method == order_service.PAYMENT_METHOD_DEBT:
        return "debt"

    sources = set()
    if order.payment_method == order_service.PAYMENT_METHOD_CASH:
        sources.add("cash")
    elif order.payment_method == order_service.PAYMENT_METHOD_TRANSFER:
        sources.add("transfer")
    else:
        sources.add("unknown")

    cash_entries = {order_service.ENTRY_CASH, order_service.ENTRY_DEBT_CASH}
    transfer_entries = {order_service.ENTRY_BANK, order_service.ENTRY_DEBT_TRANSFER}
    for entry_type, in db.query(models.OrderPayment.entry_type).filter(
        models.OrderPayment.order_id == order.id,
        models.OrderPayment.amount > 0,
    ):
        entry_type = str(entry_type or "").upper()
        if entry_type.startswith(("REFUND_", "RETURN_")):
            continue
        if entry_type in cash_entries:
            sources.add("cash")
        elif entry_type in transfer_entries:
            sources.add("transfer")
        else:
            sources.add("unknown")

    if sources == {"cash"}:
        return "cash-only"
    if sources == {"transfer"}:
        return "transfer-only"
    return "ambiguous"


def _prepare_return_context(
    db: Session,
    current_user: models.User,
    order: models.Order,
    request: OrderReturnDraft,
    *,
    expected_context: bool = False,
) -> Dict[str, Any]:
    """Read and validate the locked order; perform no durable mutation."""
    if order.status != order_service.STATUS_PAID:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Chỉ nhận trả hàng cho đơn đã thanh toán. Đơn chưa thanh toán "
                "thì hủy đơn, đơn đang đối soát thì xử lý đối soát trước."
            ),
        )

    dong_don = {
        it.id: it
        for it in db.query(models.OrderItem)
        .filter(models.OrderItem.order_id == order.id)
        .all()
    }
    deficit_order_item_ids = {
        int(order_item_id)
        for (order_item_id,) in db.query(
            models.OfflineBatchStockDeficit.order_item_id
        )
        .filter(
            models.OfflineBatchStockDeficit.order_item_id.in_(dong_don.keys())
        )
        .all()
    }
    da_tra = da_tra_theo_dong(db, order.id)

    chi_tiet: List[Dict[str, Any]] = []
    for item in request.items:
        dong = dong_don.get(item.order_item_id)
        if dong is None:
            raise HTTPException(status_code=400, detail=tr("Dòng hàng không thuộc đơn này"))
        if int(dong.id) in deficit_order_item_ids:
            raise HTTPException(
                status_code=409,
                detail=tr(
                    "Dòng theo lô có phần xuất không xác định nguồn; "
                    "không thể trả hàng an toàn"
                ),
            )
        con_tra_duoc = int(dong.quantity or 0) - int(dong.returned_total_qty or 0)
        if item.quantity > con_tra_duoc:
            if expected_context:
                raise _return_error(
                    409,
                    "RETURN_CONTEXT_CHANGED",
                    "Thông tin trả hàng đã thay đổi; vui lòng kiểm tra lại",
                )
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "'{name}' chỉ còn {remaining} có thể trả (đã bán "
                    "{sold}, đã trả {returned})",
                    name=dong.product_name,
                    remaining=con_tra_duoc,
                    sold=int(dong.quantity or 0),
                    returned=int(dong.returned_total_qty or 0),
                ),
            )
        line_delta = _line_return_delta(dong, int(item.quantity))
        batch_deltas = _batch_return_deltas(db, dong, line_delta)
        product = None
        verified_product_id = None
        batches: Dict[int, models.ProductBatch] = {}
        if item.restock:
            if dong.product_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=tr("Dòng đơn thiếu product_id; không thể nhập lại kho an toàn"),
                )
            product = (
                db.query(models.Product)
                .filter(
                    models.Product.id == dong.product_id,
                    models.Product.shop_id == order.shop_id,
                )
                .first()
            )
            if product is None:
                raise HTTPException(
                    status_code=409,
                    detail=tr("Sản phẩm nguồn không thuộc cửa hàng; không thể nhập lại kho"),
                )
            verified_product_id = int(product.id)
            if batch_deltas:
                batch_ids = [int(row["source"].batch_id) for row in batch_deltas]
                batches = {
                    int(batch.id): batch
                    for batch in db.query(models.ProductBatch)
                    .filter(
                        models.ProductBatch.id.in_(batch_ids),
                        models.ProductBatch.product_id == product.id,
                    )
                    .all()
                }
                if len(batches) != len(set(batch_ids)):
                    raise HTTPException(
                        status_code=409,
                        detail=tr("Lô nguồn không thuộc sản phẩm của cửa hàng"),
                    )
            elif product.track_batches:
                raise HTTPException(
                    status_code=409,
                    detail=tr("Dòng theo lô thiếu provenance nguồn"),
                )
        chi_tiet.append(
            {
                "dong": dong,
                "product": product,
                "verified_product_id": verified_product_id,
                "quantity": int(item.quantity),
                "restock": bool(item.restock),
                "tien_hang": int(dong.price or 0) * int(item.quantity),
                "tien_hoan": line_delta["refund"],
                "line_delta": line_delta,
                "batch_deltas": batch_deltas,
                "batches": batches,
            }
        )

    tien_hoan = 0
    for detail in chi_tiet:
        tien_hoan = checked_add(tien_hoan, int(detail["tien_hoan"]))
    if tien_hoan > MONEY_EPSILON and request.method is None:
        raise HTTPException(status_code=400, detail=tr("Phải chọn cách hoàn tiền cho khách"))
    if (
        tien_hoan > MONEY_EPSILON
        and request.method == order_service.PAYMENT_METHOD_TRANSFER
        and not (request.reference or "").strip()
    ):
        raise _return_error(
            400,
            "RETURN_REFERENCE_REQUIRED",
            "Phải nhập tham chiếu khi hoàn tiền chuyển khoản",
        )

    source_class = _classify_return_source(db, order)
    reason_codes = set()
    if source_class == "debt":
        reason_codes.add("DEBT_SOURCE")
    elif source_class == "ambiguous":
        reason_codes.add("AMBIGUOUS_SOURCE")
    expected_method = {
        "cash-only": order_service.PAYMENT_METHOD_CASH,
        "transfer-only": order_service.PAYMENT_METHOD_TRANSFER,
    }.get(source_class)
    if expected_method and tien_hoan > MONEY_EPSILON and request.method != expected_method:
        reason_codes.add("METHOD_CHANGE")
    if any(not detail["restock"] for detail in chi_tiet):
        reason_codes.add("NON_RESTOCK")

    normalized_items = []
    for detail in sorted(chi_tiet, key=lambda row: int(row["dong"].id)):
        dong = detail["dong"]
        normalized_items.append(
            {
                "order_item_id": int(dong.id),
                "quantity": int(detail["quantity"]),
                "restock": bool(detail["restock"]),
                "returned_total_qty": int(dong.returned_total_qty or 0),
                "returned_known_qty": int(dong.returned_known_qty or 0),
                "returned_unknown_qty": int(dong.returned_unknown_qty or 0),
                "returned_cost_basis_vnd": int(dong.returned_cost_basis_vnd or 0),
                "returned_refund_vnd": int(dong.returned_refund_vnd or 0),
                "cost_return_version": int(dong.cost_return_version or 0),
            }
        )
    canonical = json.dumps(
        {
            "actor_user_id": int(current_user.id),
            "shop_id": int(order.shop_id),
            "order_id": int(order.id),
            "operation_id": request.operation_id.strip(),
            "items": normalized_items,
            "method": request.method,
            "reason": (request.reason or "").strip(),
            "reference": (request.reference or "").strip() or None,
            "source_class": source_class,
            "order_status": order.status,
            "refund_amount_vnd": tien_hoan,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "dong_don": dong_don,
        "da_tra": da_tra,
        "chi_tiet": chi_tiet,
        "tien_hoan": tien_hoan,
        "source_class": source_class,
        "reason_codes": tuple(sorted(reason_codes)),
        "non_restock_item_count": sum(not detail["restock"] for detail in chi_tiet),
        "context_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _safe_approval_context(
    order: models.Order,
    request: OrderReturnDraft,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "order_id": order.id,
        "refund_amount_vnd": context["tien_hoan"],
        "refund_method": request.method if context["tien_hoan"] > 0 else None,
        "non_restock_item_count": context["non_restock_item_count"],
        "reason_codes": list(context["reason_codes"]),
        "cash_shift_id": context.get("cash_shift_id"),
        "context_fingerprint": context["context_fingerprint"],
    }


def _bind_cash_shift_context(
    context: Dict[str, Any], shift: models.CashShift | None
) -> Dict[str, Any]:
    if shift is None:
        return {**context, "cash_shift_id": None}
    bound = hashlib.sha256(
        f"{context['context_fingerprint']}:{int(shift.id)}".encode("ascii")
    ).hexdigest()
    return {**context, "cash_shift_id": int(shift.id), "context_fingerprint": bound}


def create_return_approval(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: OrderReturnApprovalCreate,
) -> Dict[str, Any]:
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    try:
        shop = require_shop_access(db, order.shop_id, current_user)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
        raise
    require_staff_permission(current_user, PERMISSION_ORDER_RETURN)
    _kiem_yeu_cau(request)

    order_service._lock_shop_for_order(db, order.shop_id)
    db.refresh(order)
    try:
        context = _prepare_return_context(
            db, current_user, order, request, expected_context=True
        )
        shift = None
        if context["tien_hoan"] > MONEY_EPSILON and request.method == "cash":
            shift = order_service._current_cash_shift(
                db,
                current_user,
                order.shop_id,
                required_for_everyone=True,
                lock_for_cash_write=True,
            )
        context = _bind_cash_shift_context(context, shift)
        if context["context_fingerprint"] != request.context_fingerprint:
            raise _return_error(
                409,
                "RETURN_CONTEXT_CHANGED",
                "Thông tin trả hàng đã thay đổi; vui lòng kiểm tra lại",
            )
        if not context["reason_codes"]:
            raise _return_error(
                400,
                "RETURN_APPROVAL_NOT_REQUIRED",
                "Yêu cầu này không cần quản lý phê duyệt",
            )
        token, _approval = approval_service.issue_pin_approval(
            db,
            shop=shop,
            actor=current_user,
            approver_username=request.approver_username,
            pin=request.pin,
            action="ORDER_RETURN_EXCEPTION",
            entity_type="ORDER",
            entity_id=order.id,
            revision=0,
            context_fingerprint=context["context_fingerprint"],
        )
        db.commit()
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise

    return {
        "approval_token": token,
        "expires_in_seconds": 300,
        "approval_context": _safe_approval_context(order, request, context),
    }


def create_return(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: OrderReturnCreate,
) -> Dict[str, Any]:
    """Nhận hàng khách trả, hoàn tiền và (tùy dòng) nhập lại kho."""
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn hàng"))
    shop = require_shop_access(db, order.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_ORDER_RETURN)

    _kiem_yeu_cau(request)
    operation_key = _khoa_thao_tac(request.operation_id)
    operation_fingerprint = _return_operation_fingerprint(request)

    truoc = _phieu_da_ghi(
        db, operation_key, order_id, operation_fingerprint
    )
    if truoc is not None:
        return _ket_qua(db, order, truoc, lap_lai=True)

    # Tuần tự hóa với mọi thao tác khác trên cùng shop: hai thu ngân cùng nhận
    # trả một dòng hàng phải nối đuôi nhau, nếu không cả hai đều thấy "còn trả
    # được 1" và cùng cho trả.
    order_service._lock_shop_for_order(db, order.shop_id)
    db.refresh(order)

    # Kiểm lại sau khi có lock: một request song song cùng mã có thể vừa xong.
    truoc = _phieu_da_ghi(
        db, operation_key, order_id, operation_fingerprint
    )
    if truoc is not None:
        ket_qua = _ket_qua(db, order, truoc, lap_lai=True)
        db.rollback()
        return ket_qua

    context = _prepare_return_context(
        db,
        current_user,
        order,
        request,
        expected_context=bool(request.approval_token),
    )
    dong_don = context["dong_don"]
    da_tra = context["da_tra"]
    chi_tiet = context["chi_tiet"]
    tien_hoan = context["tien_hoan"]

    shift = None
    if tien_hoan > MONEY_EPSILON and request.method == "cash":
        # Tiền mặt ra khỏi két phải trừ đúng ca của người đang đứng quầy, nếu
        # không thì cuối ca đếm thiếu mà không ai biết vì sao.
        shift = order_service._current_cash_shift(
            db,
            current_user,
            order.shop_id,
            required_for_everyone=True,
            lock_for_cash_write=True,
        )
    context = _bind_cash_shift_context(context, shift)

    approval = None
    if context["reason_codes"]:
        if approval_service._is_manager(shop, current_user):
            now = datetime.utcnow()
            approval = models.FnbManagerApproval(
                shop_id=shop.id,
                approver_user_id=current_user.id,
                actor_user_id=current_user.id,
                actor_auth_session_id=db.info.get("auth_session_id"),
                action="ORDER_RETURN_EXCEPTION",
                entity_type="ORDER",
                entity_id=order.id,
                revision=0,
                context_fingerprint=context["context_fingerprint"],
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                expires_at=now,
                used_at=now,
                created_at=now,
            )
            db.add(approval)
            db.flush()
        elif not request.approval_token:
            safe_context = _safe_approval_context(order, request, context)
            db.rollback()
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "RETURN_APPROVAL_REQUIRED",
                    "message": tr("Yêu cầu trả hàng này cần quản lý phê duyệt"),
                    "approval_context": safe_context,
                },
            )
        else:
            try:
                approval = approval_service.consume_approval(
                    db,
                    token=request.approval_token,
                    shop_id=shop.id,
                    actor_user_id=current_user.id,
                    action="ORDER_RETURN_EXCEPTION",
                    entity_type="ORDER",
                    entity_id=order.id,
                    revision=0,
                    context_fingerprint=context["context_fingerprint"],
                )
            except HTTPException as exc:
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
                db.rollback()
                if code == "APPROVAL_CONTEXT_CHANGED":
                    raise _return_error(
                        409,
                        "RETURN_CONTEXT_CHANGED",
                        "Thông tin trả hàng đã thay đổi; vui lòng kiểm tra lại",
                    )
                raise _return_error(
                    403,
                    "RETURN_APPROVAL_INVALID",
                    "Lượt duyệt trả hàng không còn hợp lệ",
                )

    phieu = models.OrderReturn(
        order_id=order_id,
        shop_id=order.shop_id,
        refund_amount=tien_hoan,
        refund_method=request.method if tien_hoan > MONEY_EPSILON else None,
        reason=(request.reason or "").strip()[:200] or None,
        note=(request.note or "").strip()[:500] or None,
        reference=(request.reference or "").strip()[:128] or None,
        created_by_user_id=current_user.id,
        manager_approval_id=approval.id if approval is not None else None,
        shift_id=shift.id if shift else None,
        idempotency_key=operation_key,
        operation_fingerprint=operation_fingerprint,
        created_at=datetime.utcnow(),
    )
    db.add(phieu)
    try:
        db.flush()
    except IntegrityError:
        # Unique idempotency_key: một request song song cùng mã đã ghi trước.
        db.rollback()
        truoc = _phieu_da_ghi(
            db, operation_key, order_id, operation_fingerprint
        )
        if truoc is not None:
            return _ket_qua(db, order, truoc, lap_lai=True)
        raise

    diem_bi_tru, diem_duoc_hoan = _tinh_dieu_chinh_diem(
        db,
        order,
        dong_don,
        da_tra,
        chi_tiet,
        phieu.created_at,
    )
    phieu.loyalty_points_reversed = diem_bi_tru
    phieu.loyalty_points_restored = diem_duoc_hoan

    if diem_bi_tru > 0:
        loyalty_service.add_entry(
            db,
            order.shop_id,
            order.customer_id,
            loyalty_service.ENTRY_RETURN_REVERSE,
            -diem_bi_tru,
            f"return-reverse:{phieu.id}",
            order_id=order_id,
            return_id=phieu.id,
            created_by_user_id=current_user.id,
            note=f"Trừ lại điểm đã cộng khi trả hàng đơn #{order_id}",
            created_at=phieu.created_at,
        )
    if diem_duoc_hoan > 0:
        loyalty_service.add_entry(
            db,
            order.shop_id,
            order.customer_id,
            loyalty_service.ENTRY_RETURN_RESTORE,
            diem_duoc_hoan,
            f"return-restore:{phieu.id}",
            order_id=order_id,
            return_id=phieu.id,
            created_by_user_id=current_user.id,
            note=f"Hoàn điểm đã dùng khi trả hàng đơn #{order_id}",
            expiry_days=order.loyalty_expiry_days_snapshot,
            created_at=phieu.created_at,
        )

    for d in chi_tiet:
        dong = d["dong"]
        line_delta = d["line_delta"]
        return_item = models.OrderReturnItem(
                return_id=phieu.id,
                order_item_id=dong.id,
                product_id=d["verified_product_id"],
                product_name=dong.product_name,
                quantity=d["quantity"],
                unit_price=int(dong.price or 0),
                refund_amount=d["tien_hoan"],
                cost_known_qty=line_delta["known"],
                cost_unknown_qty=line_delta["unknown"],
                cost_basis_vnd=line_delta["basis"],
                restocked=1 if d["restock"] else 0,
            )
        db.add(return_item)
        db.flush()

        _conditional_advance_line_return(db, dong, line_delta)

        product = d["product"]
        batch_deltas = d["batch_deltas"]
        if batch_deltas:
            batches = d["batches"]
            for row in batch_deltas:
                source = row["source"]
                db.add(
                    models.OrderReturnItemBatch(
                        return_item_id=return_item.id,
                        source_order_item_batch_id=source.id,
                        batch_id=source.batch_id,
                        quantity=row["quantity"],
                        cost_known_qty=row["known"],
                        cost_unknown_qty=row["unknown"],
                        cost_basis_vnd=row["basis"],
                        restocked=1 if d["restock"] else 0,
                    )
                )
                _conditional_advance_batch_return(db, source, row)
                if d["restock"]:
                    batch = batches[int(source.batch_id)]
                    if int(batch.quantity or 0) > MAX_SAFE_QUANTITY - row["quantity"]:
                        raise HTTPException(status_code=409, detail=tr("Tồn lô sau trả vượt giới hạn"))
                    inventory_service.restore_cost_pool(
                        batch, row["known"], row["unknown"], row["basis"]
                    )
                    batch.quantity = int(batch.quantity or 0) + row["quantity"]
            if d["restock"]:
                if int(product.stock or 0) > MAX_SAFE_QUANTITY - d["quantity"]:
                    raise HTTPException(status_code=409, detail=tr("Tồn kho sau trả vượt giới hạn"))
                product.stock = int(product.stock or 0) + d["quantity"]
        elif d["restock"]:
            if product is None:
                raise HTTPException(status_code=409, detail=tr("Sản phẩm nguồn của hàng trả không còn tồn tại"))
            if int(product.stock or 0) > MAX_SAFE_QUANTITY - d["quantity"]:
                raise HTTPException(status_code=409, detail=tr("Tồn kho sau trả vượt giới hạn"))
            inventory_service.restore_cost_pool(
                product,
                line_delta["known"],
                line_delta["unknown"],
                line_delta["basis"],
            )
            product.stock = int(product.stock or 0) + d["quantity"]

    if tien_hoan > MONEY_EPSILON:
        db.add(
            models.OrderPayment(
                order_id=order_id,
                entry_type=(
                    ENTRY_RETURN_CASH
                    if request.method == "cash"
                    else ENTRY_RETURN_TRANSFER
                ),
                amount=tien_hoan,
                idempotency_key=operation_key,
                created_by_user_id=current_user.id,
                shift_id=shift.id if shift else None,
                note=phieu.note,
                reference=phieu.reference,
            )
        )

    mo_ta = (
        f"Order {order_id}: nhận trả {sum(d['quantity'] for d in chi_tiet)} món, "
        f"hoàn {tien_hoan:,.0f}đ"
    )
    if tien_hoan > MONEY_EPSILON:
        mo_ta += " bằng " + (
            "tiền mặt" if request.method == "cash" else "chuyển khoản"
        )
    khong_nhap_lai = [d for d in chi_tiet if not d["restock"]]
    if khong_nhap_lai:
        mo_ta += f" - {len(khong_nhap_lai)} dòng KHÔNG nhập lại kho"
    if diem_bi_tru:
        mo_ta += f" - trừ lại {diem_bi_tru} điểm đã cộng"
    if diem_duoc_hoan:
        mo_ta += f" - hoàn {diem_duoc_hoan} điểm đã dùng"
    if phieu.reason:
        mo_ta += f" - lý do: {phieu.reason}"
    mo_ta += f" - người thực hiện: {current_user.id}/{current_user.username}"
    if approval is not None:
        approver = db.get(models.User, approval.approver_user_id)
        mo_ta += f" - người duyệt: {approval.approver_user_id}/{approver.username}"
    order_service._them_nhat_ky(db, current_user.id, "ORDER_RETURN", mo_ta)

    db.commit()
    db.refresh(phieu)
    return _ket_qua(db, order, phieu, lap_lai=False)


def d_qty(chi_tiet: List[Dict[str, Any]], order_item_id: int) -> int:
    """Số lượng đang trả của một dòng trong phiếu hiện tại (0 nếu không trả)."""
    for d in chi_tiet:
        if d["dong"].id == order_item_id:
            return int(d["quantity"])
    return 0


def _ket_qua(
    db: Session,
    order: models.Order,
    phieu: models.OrderReturn,
    lap_lai: bool,
) -> Dict[str, Any]:
    return {
        "msg": tr(
            "Lần trả hàng này đã được ghi nhận trước đó"
            if lap_lai
            else "Đã ghi nhận trả hàng"
        ),
        "order_id": order.id,
        "order_status": order.status,
        "total_amount": order.total_amount,
        "returned_total": tong_da_hoan(db, order.id),
        "loyalty_points_restored": int(phieu.loyalty_points_restored or 0),
        "loyalty_points_reversed": int(phieu.loyalty_points_reversed or 0),
        "return": _serialize_return(phieu),
    }
