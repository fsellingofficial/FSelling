"""Nhà cung cấp, phiếu nhập và sổ công nợ phải trả.

Mọi đường ghi tiền/kho trong file này giữ một transaction duy nhất. Không gọi
``log_system_action`` hoặc ``catalog_service.adjust_stock`` ở giữa vì hai hàm
đó tự commit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..core import thoi_gian
from ..core.i18n import tr
from ..core.numeric_limits import MAX_SAFE_QUANTITY, MAX_SAFE_VND
from ..dependencies import require_cost_visibility, require_shop_access
from ..schemas.supplier import (
    PurchaseOrderCancel,
    PurchaseOrderCreate,
    PurchaseOrderItemInput,
    PurchaseOrderPlace,
    PurchaseOrderUpdate,
    PurchaseReceiptConfirm,
    PurchaseReceiptCreate,
    PurchaseReceiptItemInput,
    PurchaseReceiptUpdate,
    SupplierCreate,
    SupplierPaymentCreate,
    SupplierStatusUpdate,
    SupplierUpdate,
)
from . import auth_session_service, catalog_service, shift_service, subscription_service


STATUS_DRAFT = "DRAFT"
STATUS_POSTED = "POSTED"
ORDER_DRAFT = "DRAFT"
ORDER_ORDERED = "ORDERED"
ORDER_RECEIVED = "RECEIVED"
ORDER_CANCELLED = "CANCELLED"
ENTRY_PURCHASE = "PURCHASE"
ENTRY_OPENING = "OPENING"
METHOD_CASH_SHIFT = "CASH_SHIFT"
METHOD_TRANSFER = "TRANSFER"
METHOD_OUTSIDE = "OUTSIDE"
METHODS = frozenset({METHOD_CASH_SHIFT, METHOD_TRANSFER, METHOD_OUTSIDE})


def _today_vn() -> str:
    """Ngày nghiệp vụ Việt Nam, không phụ thuộc timezone của máy deploy."""
    return thoi_gian.hom_nay_vn_str()


def _clean(value: Optional[str], maximum: int) -> Optional[str]:
    return (value or "").strip()[:maximum] or None


def _name(value: str) -> str:
    result = (value or "").strip()
    if not result:
        raise HTTPException(
            status_code=400, detail=tr("Tên nhà cung cấp không được để trống")
        )
    return result[:255]


def _date(value: Optional[str], field: str, *, default_today: bool = False) -> Optional[str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return _today_vn() if default_today else None
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=tr("{field} phải theo định dạng YYYY-MM-DD", field=field),
        )
    return cleaned


def _operation(value: str, label: str) -> str:
    result = (value or "").strip()
    if len(result) < 8 or len(result) > 128:
        raise HTTPException(status_code=400, detail=tr("{label} không hợp lệ", label=label))
    return result


def _hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key(prefix: str, operation_id: str) -> str:
    return f"{prefix}:{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()}"


def _audit(db: Session, user_id: Optional[int], action: str, details: str) -> None:
    db.add(
        models.SystemLog(user_id=user_id, action=action, details=details[:2000])
    )


def _authorize_shop(
    db: Session, current_user: models.User, shop_id: int
) -> models.Shop:
    shop = require_shop_access(db, shop_id, current_user)
    require_cost_visibility(shop, current_user)
    return shop


def _lock_shop(db: Session, shop_id: int) -> None:
    """Lấy SQLite write lock chung với tạo đơn trước khi đọc kho/số dư."""
    result = db.execute(
        text("UPDATE shops SET id = id WHERE id = :shop_id"),
        {"shop_id": shop_id},
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    auth_session_service.fence_live_auth_session(db)


def _lock_supplier(db: Session, supplier_id: int, shop_id: int) -> None:
    result = db.execute(
        text(
            "UPDATE suppliers SET id = id "
            "WHERE id = :supplier_id AND shop_id = :shop_id"
        ),
        {"supplier_id": supplier_id, "shop_id": shop_id},
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=404, detail=tr("Không tìm thấy nhà cung cấp")
        )
    auth_session_service.fence_live_auth_session(db)


def _get_supplier(
    db: Session, current_user: models.User, supplier_id: int
) -> Tuple[models.Supplier, models.Shop]:
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy nhà cung cấp"))
    shop = _authorize_shop(db, current_user, supplier.shop_id)
    return supplier, shop


def _allocated_map(db: Session, entry_ids: Sequence[int]) -> Dict[int, int]:
    if not entry_ids:
        return {}
    rows = (
        db.query(
            models.SupplierPaymentAllocation.payable_entry_id,
            models.SupplierPaymentAllocation.amount,
        )
        .filter(models.SupplierPaymentAllocation.payable_entry_id.in_(entry_ids))
        .all()
    )
    result: Dict[int, int] = {}
    for entry_id, amount in rows:
        result[int(entry_id)] = result.get(int(entry_id), 0) + int(amount or 0)
    return result


def _supplier_entries(db: Session, supplier_id: int) -> List[models.SupplierPayableEntry]:
    return (
        db.query(models.SupplierPayableEntry)
        .filter(models.SupplierPayableEntry.supplier_id == supplier_id)
        .order_by(
            models.SupplierPayableEntry.entry_date,
            models.SupplierPayableEntry.id,
        )
        .all()
    )


def _entry_out(
    entry: models.SupplierPayableEntry, allocated: int, today: Optional[str] = None
) -> Dict[str, Any]:
    remaining = max(int(entry.amount or 0) - int(allocated or 0), 0)
    now_date = today or _today_vn()
    overdue = bool(
        remaining > 0 and entry.due_date is not None and entry.due_date < now_date
    )
    return {
        "id": entry.id,
        "shop_id": entry.shop_id,
        "supplier_id": entry.supplier_id,
        "receipt_id": entry.receipt_id,
        "entry_type": entry.entry_type,
        "amount": int(entry.amount or 0),
        "allocated_amount": int(allocated or 0),
        "remaining_amount": remaining,
        "entry_date": entry.entry_date,
        "due_date": entry.due_date,
        "is_overdue": overdue,
        "note": entry.note,
        "created_at": entry.created_at,
    }


def _supplier_amounts(db: Session, supplier_id: int) -> Tuple[int, int]:
    entries = _supplier_entries(db, supplier_id)
    allocations = _allocated_map(db, [entry.id for entry in entries])
    today = _today_vn()
    balance = 0
    overdue = 0
    for entry in entries:
        row = _entry_out(entry, allocations.get(entry.id, 0), today=today)
        balance += row["remaining_amount"]
        if row["is_overdue"]:
            overdue += row["remaining_amount"]
    return balance, overdue


def _supplier_out(db: Session, supplier: models.Supplier) -> Dict[str, Any]:
    balance, overdue = _supplier_amounts(db, supplier.id)
    return {
        "id": supplier.id,
        "shop_id": supplier.shop_id,
        "name": supplier.name,
        "phone": supplier.phone,
        "tax_code": supplier.tax_code,
        "address": supplier.address,
        "note": supplier.note,
        "is_active": bool(supplier.is_active),
        "payable_balance": balance,
        "overdue_amount": overdue,
        "created_at": supplier.created_at,
        "updated_at": supplier.updated_at,
    }


def _same_supplier_create(
    supplier: models.Supplier, shop_id: int, user_id: int, fingerprint: str
) -> bool:
    return (
        supplier.shop_id == shop_id
        and supplier.created_by_user_id == user_id
        and supplier.create_fingerprint == fingerprint
    )


def create_supplier(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: SupplierCreate,
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    subscription_service.require_pro(db, shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác tạo nhà cung cấp")
    # Fingerprint idempotency phải dựa trên payload GỐC. Nếu request bỏ ngày
    # lúc 23:59, retry nguyên payload sau 00:00 vẫn phải nhận lại cùng kết quả,
    # không được hash theo "hôm nay" mới rồi báo xung đột.
    opening_date_input = _date(
        request.opening_date,
        "Ngày ghi nhận nợ đầu kỳ",
    )
    opening_date = opening_date_input
    if opening_date is None and request.opening_balance > 0:
        opening_date = _today_vn()
    opening_due_date = _date(request.opening_due_date, "Hạn nợ đầu kỳ")
    values = {
        "name": _name(request.name),
        "phone": _clean(request.phone, 64),
        "tax_code": _clean(request.tax_code, 64),
        "address": _clean(request.address, 500),
        "note": _clean(request.note, 500),
        "opening_balance": int(request.opening_balance),
        "opening_date": opening_date,
        "opening_due_date": opening_due_date,
        "opening_note": _clean(request.opening_note, 500),
    }
    fingerprint_values = {**values, "opening_date": opening_date_input}
    fingerprint = _hash({"shop_id": shop_id, **fingerprint_values})

    _lock_shop(db, shop_id)
    existing = db.query(models.Supplier).filter(
        models.Supplier.create_operation_id == operation_id
    ).first()
    if existing is not None:
        if not _same_supplier_create(existing, shop_id, current_user.id, fingerprint):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr("Mã thao tác đã được dùng để tạo nhà cung cấp khác"),
            )
        result = _supplier_out(db, existing)
        result["repeated"] = True
        db.rollback()
        return result

    supplier = models.Supplier(
        shop_id=shop_id,
        name=values["name"],
        phone=values["phone"],
        tax_code=values["tax_code"],
        address=values["address"],
        note=values["note"],
        create_operation_id=operation_id,
        create_fingerprint=fingerprint,
        created_by_user_id=current_user.id,
    )
    db.add(supplier)
    try:
        db.flush()
        if values["opening_balance"] > 0:
            db.add(
                models.SupplierPayableEntry(
                    shop_id=shop_id,
                    supplier_id=supplier.id,
                    entry_type=ENTRY_OPENING,
                    amount=values["opening_balance"],
                    entry_date=opening_date,
                    due_date=opening_due_date,
                    idempotency_key=f"supplier-opening:{supplier.id}",
                    note=values["opening_note"],
                    created_by_user_id=current_user.id,
                )
            )
        _audit(
            db,
            current_user.id,
            "CREATE_SUPPLIER",
            f"Shop #{shop_id}: tạo NCC '{supplier.name}'",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(models.Supplier).filter(
            models.Supplier.create_operation_id == operation_id
        ).first()
        if duplicate is not None and _same_supplier_create(
            duplicate, shop_id, current_user.id, fingerprint
        ):
            result = _supplier_out(db, duplicate)
            result["repeated"] = True
            return result
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác đã được dùng để tạo nhà cung cấp khác"),
        )
    db.refresh(supplier)
    result = _supplier_out(db, supplier)
    result["repeated"] = False
    return result


def list_suppliers(
    db: Session,
    current_user: models.User,
    shop_id: int,
    *,
    include_inactive: bool = False,
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    query = db.query(models.Supplier).filter(models.Supplier.shop_id == shop_id)
    if not include_inactive:
        query = query.filter(models.Supplier.is_active.is_(True))
    suppliers = query.order_by(models.Supplier.name, models.Supplier.id).all()
    return {"suppliers": [_supplier_out(db, supplier) for supplier in suppliers]}


def get_supplier_detail(
    db: Session, current_user: models.User, supplier_id: int
) -> Dict[str, Any]:
    supplier, _ = _get_supplier(db, current_user, supplier_id)
    entries = _supplier_entries(db, supplier.id)
    allocation_map = _allocated_map(db, [entry.id for entry in entries])
    payments = (
        db.query(models.SupplierPayment)
        .filter(models.SupplierPayment.supplier_id == supplier.id)
        .order_by(models.SupplierPayment.created_at.desc(), models.SupplierPayment.id.desc())
        .all()
    )
    return {
        "supplier": _supplier_out(db, supplier),
        "payables": [
            _entry_out(entry, allocation_map.get(entry.id, 0)) for entry in entries
        ],
        "payments": [_payment_out(db, payment) for payment in payments],
    }


def update_supplier(
    db: Session,
    current_user: models.User,
    supplier_id: int,
    request: SupplierUpdate,
) -> Dict[str, Any]:
    supplier, _ = _get_supplier(db, current_user, supplier_id)
    subscription_service.require_pro(db, supplier.shop_id)
    supplier.name = _name(request.name)
    supplier.phone = _clean(request.phone, 64)
    supplier.tax_code = _clean(request.tax_code, 64)
    supplier.address = _clean(request.address, 500)
    supplier.note = _clean(request.note, 500)
    _audit(
        db,
        current_user.id,
        "UPDATE_SUPPLIER",
        f"Shop #{supplier.shop_id}: cập nhật NCC #{supplier.id} '{supplier.name}'",
    )
    db.commit()
    db.refresh(supplier)
    return _supplier_out(db, supplier)


def update_supplier_status(
    db: Session,
    current_user: models.User,
    supplier_id: int,
    request: SupplierStatusUpdate,
) -> Dict[str, Any]:
    supplier, _ = _get_supplier(db, current_user, supplier_id)
    subscription_service.require_pro(db, supplier.shop_id)
    _lock_supplier(db, supplier.id, supplier.shop_id)
    db.refresh(supplier)
    supplier.is_active = bool(request.is_active)
    _audit(
        db,
        current_user.id,
        "UPDATE_SUPPLIER_STATUS",
        f"Shop #{supplier.shop_id}: {'dùng lại' if supplier.is_active else 'ngừng'} "
        f"NCC #{supplier.id} '{supplier.name}'",
    )
    db.commit()
    db.refresh(supplier)
    return _supplier_out(db, supplier)


def delete_supplier(
    db: Session, current_user: models.User, supplier_id: int
) -> Dict[str, str]:
    supplier, _ = _get_supplier(db, current_user, supplier_id)
    subscription_service.require_pro(db, supplier.shop_id)
    _lock_supplier(db, supplier.id, supplier.shop_id)
    db.refresh(supplier)
    has_history = any(
        query.first() is not None
        for query in (
            db.query(models.PurchaseOrder.id).filter(
                models.PurchaseOrder.supplier_id == supplier.id
            ),
            db.query(models.PurchaseReceipt.id).filter(
                models.PurchaseReceipt.supplier_id == supplier.id
            ),
            db.query(models.SupplierPayableEntry.id).filter(
                models.SupplierPayableEntry.supplier_id == supplier.id
            ),
            db.query(models.SupplierPayment.id).filter(
                models.SupplierPayment.supplier_id == supplier.id
            ),
        )
    )
    name = supplier.name
    if has_history:
        supplier.is_active = False
        action = "DEACTIVATE_SUPPLIER"
        message = "Deactivated"
    else:
        db.delete(supplier)
        action = "DELETE_SUPPLIER"
        message = "Deleted"
    _audit(
        db,
        current_user.id,
        action,
        f"Shop #{supplier.shop_id}: {action} NCC #{supplier.id} '{name}'",
    )
    db.commit()
    return {"msg": message}


def _purchase_order_values(
    request: PurchaseOrderCreate | PurchaseOrderUpdate,
) -> Dict[str, Any]:
    return {
        "supplier_id": int(request.supplier_id),
        "expected_date": _date(request.expected_date, "Ngày dự kiến nhận"),
        "note": _clean(request.note, 500),
    }


def _prepare_purchase_order_items(
    db: Session,
    shop_id: int,
    inputs: Iterable[PurchaseOrderItemInput | models.PurchaseOrderItem],
) -> List[Dict[str, Any]]:
    items = list(inputs)
    if not items:
        raise HTTPException(status_code=400, detail=tr("Đơn đặt hàng chưa có sản phẩm"))
    product_ids = [int(item.product_id) for item in items]
    if len(product_ids) != len(set(product_ids)):
        raise HTTPException(
            status_code=400,
            detail=tr("Mỗi sản phẩm chỉ được xuất hiện một lần trong đơn đặt hàng"),
        )
    products = {
        product.id: product
        for product in db.query(models.Product)
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.id.in_(product_ids),
            models.Product.is_active.is_(True),
        )
        .all()
    }
    prepared: List[Dict[str, Any]] = []
    for item in items:
        product = products.get(int(item.product_id))
        if product is None:
            raise HTTPException(
                status_code=404,
                detail=tr(
                    "Sản phẩm #{id} không hoạt động trong cửa hàng này",
                    id=str(item.product_id),
                ),
            )
        quantity = int(item.quantity)
        if quantity <= 0 or quantity > MAX_SAFE_QUANTITY:
            raise HTTPException(status_code=400, detail=tr("Số lượng đặt không hợp lệ"))
        prepared.append(
            {"source": item, "product": product, "quantity": quantity}
        )
    return prepared


def _purchase_order_fingerprint(
    shop_id: int,
    values: Dict[str, Any],
    items: Iterable[PurchaseOrderItemInput | models.PurchaseOrderItem],
) -> str:
    canonical_items = sorted(
        (
            {"product_id": int(item.product_id), "quantity": int(item.quantity)}
            for item in items
        ),
        key=lambda item: item["product_id"],
    )
    return _hash(
        {
            "shop_id": int(shop_id),
            "supplier_id": int(values["supplier_id"]),
            "expected_date": values.get("expected_date"),
            "note": values.get("note"),
            "items": canonical_items,
        }
    )


def _purchase_order_items(db: Session, order_id: int) -> List[models.PurchaseOrderItem]:
    return (
        db.query(models.PurchaseOrderItem)
        .filter(models.PurchaseOrderItem.purchase_order_id == order_id)
        .order_by(models.PurchaseOrderItem.id)
        .all()
    )


def _stored_purchase_order_fingerprint(
    order: models.PurchaseOrder,
    items: Iterable[models.PurchaseOrderItem],
) -> str:
    return _purchase_order_fingerprint(
        order.shop_id,
        {
            "supplier_id": order.supplier_id,
            "expected_date": order.expected_date,
            "note": order.note,
        },
        items,
    )


def _purchase_order_out(
    db: Session, order: models.PurchaseOrder, *, repeated: bool = False
) -> Dict[str, Any]:
    items = _purchase_order_items(db, order.id)
    supplier_name = db.query(models.Supplier.name).filter(
        models.Supplier.id == order.supplier_id
    ).scalar()
    receipt_id = db.query(models.PurchaseReceipt.id).filter(
        models.PurchaseReceipt.purchase_order_id == order.id
    ).scalar()
    return {
        "id": order.id,
        "shop_id": order.shop_id,
        "supplier_id": order.supplier_id,
        "supplier_name": supplier_name,
        "status": order.status,
        "expected_date": order.expected_date,
        "note": order.note,
        "receipt_id": receipt_id,
        "created_by_user_id": order.created_by_user_id,
        "placed_by_user_id": order.placed_by_user_id,
        "cancelled_by_user_id": order.cancelled_by_user_id,
        "received_by_user_id": order.received_by_user_id,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "placed_at": order.placed_at,
        "cancelled_at": order.cancelled_at,
        "received_at": order.received_at,
        "draft_fingerprint": _stored_purchase_order_fingerprint(order, items),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "quantity": int(item.quantity),
            }
            for item in items
        ],
        "repeated": repeated,
    }


def _get_purchase_order(
    db: Session, current_user: models.User, order_id: int
) -> Tuple[models.PurchaseOrder, models.Shop]:
    order = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.id == order_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn đặt hàng"))
    return order, _authorize_shop(db, current_user, order.shop_id)


def create_purchase_order(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: PurchaseOrderCreate,
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    subscription_service.require_pro(db, shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác tạo đơn đặt hàng")
    values = _purchase_order_values(request)
    fingerprint = _purchase_order_fingerprint(shop_id, values, request.items)
    _lock_shop(db, shop_id)
    db.expire_all()
    existing = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.create_operation_id == operation_id
    ).first()
    if existing is not None:
        if (
            existing.shop_id != shop_id
            or existing.created_by_user_id != current_user.id
            or existing.create_fingerprint != fingerprint
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr("Mã thao tác đã được dùng cho đơn đặt hàng khác"),
            )
        result = _purchase_order_out(db, existing, repeated=True)
        db.rollback()
        return result

    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == values["supplier_id"],
        models.Supplier.shop_id == shop_id,
        models.Supplier.is_active.is_(True),
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Nhà cung cấp không hoạt động"))
    prepared = _prepare_purchase_order_items(db, shop_id, request.items)
    order = models.PurchaseOrder(
        shop_id=shop_id,
        supplier_id=supplier.id,
        status=ORDER_DRAFT,
        expected_date=values["expected_date"],
        note=values["note"],
        create_operation_id=operation_id,
        create_fingerprint=fingerprint,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(order)
    try:
        db.flush()
        for row in prepared:
            db.add(
                models.PurchaseOrderItem(
                    purchase_order_id=order.id,
                    product_id=row["product"].id,
                    product_name=row["product"].name,
                    quantity=row["quantity"],
                )
            )
        _audit(
            db,
            current_user.id,
            "CREATE_PURCHASE_ORDER_DRAFT",
            f"Shop #{shop_id}: tạo nháp đơn đặt hàng #{order.id} từ NCC '{supplier.name}'",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(models.PurchaseOrder).filter(
            models.PurchaseOrder.create_operation_id == operation_id
        ).first()
        if duplicate is not None and (
            duplicate.shop_id == shop_id
            and duplicate.created_by_user_id == current_user.id
            and duplicate.create_fingerprint == fingerprint
        ):
            return _purchase_order_out(db, duplicate, repeated=True)
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác đã được dùng cho đơn đặt hàng khác"),
        )
    db.refresh(order)
    return _purchase_order_out(db, order)


def list_purchase_orders(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    orders = (
        db.query(models.PurchaseOrder)
        .filter(models.PurchaseOrder.shop_id == shop_id)
        .order_by(models.PurchaseOrder.created_at.desc(), models.PurchaseOrder.id.desc())
        .all()
    )
    return {"orders": [_purchase_order_out(db, order) for order in orders]}


def get_purchase_order_detail(
    db: Session, current_user: models.User, order_id: int
) -> Dict[str, Any]:
    order, _ = _get_purchase_order(db, current_user, order_id)
    return _purchase_order_out(db, order)


def update_purchase_order_draft(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: PurchaseOrderUpdate,
) -> Dict[str, Any]:
    order, _ = _get_purchase_order(db, current_user, order_id)
    subscription_service.require_pro(db, order.shop_id)
    _lock_shop(db, order.shop_id)
    db.expire_all()
    order = db.query(models.PurchaseOrder).filter(models.PurchaseOrder.id == order_id).first()
    if order is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn đặt hàng"))
    if order.status != ORDER_DRAFT:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Chỉ được sửa đơn đặt hàng nháp"))
    values = _purchase_order_values(request)
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == values["supplier_id"],
        models.Supplier.shop_id == order.shop_id,
        models.Supplier.is_active.is_(True),
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Nhà cung cấp không hoạt động"))
    prepared = _prepare_purchase_order_items(db, order.shop_id, request.items)
    db.query(models.PurchaseOrderItem).filter(
        models.PurchaseOrderItem.purchase_order_id == order.id
    ).delete(synchronize_session=False)
    order.supplier_id = supplier.id
    order.expected_date = values["expected_date"]
    order.note = values["note"]
    order.updated_by_user_id = current_user.id
    for row in prepared:
        db.add(
            models.PurchaseOrderItem(
                purchase_order_id=order.id,
                product_id=row["product"].id,
                product_name=row["product"].name,
                quantity=row["quantity"],
            )
        )
    _audit(db, current_user.id, "UPDATE_PURCHASE_ORDER_DRAFT", f"Shop #{order.shop_id}: sửa nháp đơn đặt hàng #{order.id}")
    db.commit()
    db.refresh(order)
    return _purchase_order_out(db, order)


def delete_purchase_order_draft(
    db: Session, current_user: models.User, order_id: int
) -> Dict[str, str]:
    order, _ = _get_purchase_order(db, current_user, order_id)
    subscription_service.require_pro(db, order.shop_id)
    _lock_shop(db, order.shop_id)
    db.expire_all()
    order = db.query(models.PurchaseOrder).filter(models.PurchaseOrder.id == order_id).first()
    if order is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn đặt hàng"))
    if order.status != ORDER_DRAFT:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Chỉ được xóa đơn đặt hàng nháp"))
    shop_id = order.shop_id
    db.query(models.PurchaseOrderItem).filter(
        models.PurchaseOrderItem.purchase_order_id == order.id
    ).delete(synchronize_session=False)
    db.delete(order)
    _audit(db, current_user.id, "DELETE_PURCHASE_ORDER_DRAFT", f"Shop #{shop_id}: xóa nháp đơn đặt hàng #{order_id}")
    db.commit()
    return {"msg": "Deleted"}


def place_purchase_order(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: PurchaseOrderPlace,
) -> Dict[str, Any]:
    order, _ = _get_purchase_order(db, current_user, order_id)
    subscription_service.require_pro(db, order.shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác đặt hàng")
    fingerprint = _hash(
        {"order_id": order_id, "draft_fingerprint": request.draft_fingerprint}
    )
    if order.status == ORDER_ORDERED:
        if order.place_operation_id == operation_id and order.place_fingerprint == fingerprint:
            return _purchase_order_out(db, order, repeated=True)
        raise HTTPException(status_code=409, detail=tr("Đơn đã được đặt"))
    _lock_shop(db, order.shop_id)
    db.expire_all()
    order = db.query(models.PurchaseOrder).filter(models.PurchaseOrder.id == order_id).first()
    if order is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn đặt hàng"))
    if order.status == ORDER_ORDERED:
        if order.place_operation_id == operation_id and order.place_fingerprint == fingerprint:
            result = _purchase_order_out(db, order, repeated=True)
            db.rollback()
            return result
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Đơn đã được đặt"))
    if order.status != ORDER_DRAFT:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Đơn không còn ở trạng thái nháp"))
    items = _purchase_order_items(db, order.id)
    if _stored_purchase_order_fingerprint(order, items) != request.draft_fingerprint:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Đơn nháp đã thay đổi; hãy mở lại và kiểm tra"))
    collision = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.place_operation_id == operation_id,
        models.PurchaseOrder.id != order_id,
    ).first()
    if collision is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Mã thao tác đã dùng để đặt đơn khác"))
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == order.supplier_id,
        models.Supplier.shop_id == order.shop_id,
        models.Supplier.is_active.is_(True),
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Nhà cung cấp đã ngừng sử dụng"))
    _prepare_purchase_order_items(db, order.shop_id, items)
    order.status = ORDER_ORDERED
    order.place_operation_id = operation_id
    order.place_fingerprint = fingerprint
    order.placed_by_user_id = current_user.id
    order.placed_at = datetime.utcnow()
    order.updated_by_user_id = current_user.id
    _audit(db, current_user.id, "PLACE_PURCHASE_ORDER", f"Shop #{order.shop_id}: đặt đơn NCC #{order.id}")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Mã thao tác đã dùng để đặt đơn khác"))
    db.refresh(order)
    return _purchase_order_out(db, order)


def cancel_purchase_order(
    db: Session,
    current_user: models.User,
    order_id: int,
    request: PurchaseOrderCancel,
) -> Dict[str, Any]:
    order, _ = _get_purchase_order(db, current_user, order_id)
    subscription_service.require_pro(db, order.shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác hủy đơn")
    if order.status == ORDER_CANCELLED:
        if order.cancel_operation_id == operation_id:
            return _purchase_order_out(db, order, repeated=True)
        raise HTTPException(status_code=409, detail=tr("Đơn đã bị hủy"))
    _lock_shop(db, order.shop_id)
    db.expire_all()
    order = db.query(models.PurchaseOrder).filter(models.PurchaseOrder.id == order_id).first()
    if order is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn đặt hàng"))
    if order.status == ORDER_CANCELLED and order.cancel_operation_id == operation_id:
        result = _purchase_order_out(db, order, repeated=True)
        db.rollback()
        return result
    if order.status != ORDER_ORDERED:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Chỉ được hủy đơn đang đặt"))
    collision = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.cancel_operation_id == operation_id,
        models.PurchaseOrder.id != order_id,
    ).first()
    if collision is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Mã thao tác đã dùng để hủy đơn khác"))
    linked_receipt = db.query(models.PurchaseReceipt.id).filter(
        models.PurchaseReceipt.purchase_order_id == order.id
    ).scalar()
    if linked_receipt is not None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Đơn đã có phiếu nhập nháp; hãy xóa phiếu nhập trước khi hủy"),
        )
    order.status = ORDER_CANCELLED
    order.cancel_operation_id = operation_id
    order.cancelled_by_user_id = current_user.id
    order.cancelled_at = datetime.utcnow()
    order.updated_by_user_id = current_user.id
    _audit(db, current_user.id, "CANCEL_PURCHASE_ORDER", f"Shop #{order.shop_id}: hủy đơn NCC #{order.id}")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Mã thao tác đã dùng để hủy đơn khác"))
    db.refresh(order)
    return _purchase_order_out(db, order)


def _receipt_values(
    request: PurchaseReceiptCreate | PurchaseReceiptUpdate,
) -> Dict[str, Any]:
    received_date = _date(request.received_date, "Ngày nhập", default_today=True)
    due_date = _date(request.due_date, "Hạn thanh toán")
    return {
        "purchase_order_id": (
            int(request.purchase_order_id)
            if request.purchase_order_id is not None
            else None
        ),
        "supplier_id": int(request.supplier_id),
        "supplier_invoice_number": _clean(request.supplier_invoice_number, 128),
        "received_date": received_date,
        "due_date": due_date,
        "note": _clean(request.note, 500),
    }


def _prepare_items(
    db: Session,
    shop_id: int,
    inputs: Iterable[PurchaseReceiptItemInput | models.PurchaseReceiptItem],
) -> Tuple[List[Dict[str, Any]], int]:
    items = list(inputs)
    if not items:
        raise HTTPException(status_code=400, detail=tr("Phiếu nhập chưa có sản phẩm"))
    product_ids = [int(item.product_id) for item in items]
    products = {
        product.id: product
        for product in db.query(models.Product)
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.id.in_(product_ids),
        )
        .all()
    }
    prepared: List[Dict[str, Any]] = []
    quantity_by_product: Dict[int, int] = {}
    total = 0
    for item in items:
        product = products.get(int(item.product_id))
        if product is None:
            raise HTTPException(
                status_code=404,
                detail=tr(
                    "Sản phẩm #{id} không thuộc cửa hàng này", id=str(item.product_id)
                ),
            )
        quantity = int(item.quantity)
        unit_cost = int(item.unit_cost)
        if quantity <= 0 or unit_cost < 0:
            raise HTTPException(
                status_code=400, detail=tr("Số lượng hoặc đơn giá nhập không hợp lệ")
            )
        expiry = _date(item.expiry_date, "Hạn sử dụng")
        if product.track_batches and expiry is None:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' theo dõi lô; phải nhập hạn sử dụng",
                    name=product.name,
                ),
            )
        if not product.track_batches and expiry is not None:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' không theo dõi lô nên không nhận hạn sử dụng",
                    name=product.name,
                ),
            )
        product_id = int(product.id)
        accumulated_quantity = quantity_by_product.get(product_id, 0) + quantity
        if accumulated_quantity > MAX_SAFE_QUANTITY:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Tổng số lượng nhập của '{name}' vượt giới hạn {maximum}",
                    name=product.name,
                    maximum=f"{MAX_SAFE_QUANTITY:,}",
                ),
            )
        current_stock = int(product.stock or 0)
        if current_stock + accumulated_quantity > MAX_SAFE_QUANTITY:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Tồn kho của '{name}' sau khi nhập vượt giới hạn {maximum}",
                    name=product.name,
                    maximum=f"{MAX_SAFE_QUANTITY:,}",
                ),
            )
        quantity_by_product[product_id] = accumulated_quantity

        if unit_cost > 0 and quantity > MAX_SAFE_VND // unit_cost:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Thành tiền của '{name}' vượt giới hạn {maximum}đ",
                    name=product.name,
                    maximum=f"{MAX_SAFE_VND:,}",
                ),
            )
        line_total = quantity * unit_cost
        if line_total > MAX_SAFE_VND - total:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Tổng phiếu nhập vượt giới hạn {maximum}đ",
                    maximum=f"{MAX_SAFE_VND:,}",
                ),
            )
        total += line_total
        prepared.append(
            {
                "source": item,
                "product": product,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "expiry_date": expiry,
                "line_total": line_total,
            }
        )
    return prepared, total


def _validate_receipt_purchase_order(
    db: Session,
    *,
    shop_id: int,
    supplier_id: int,
    purchase_order_id: Optional[int],
    items: Iterable[PurchaseReceiptItemInput | models.PurchaseReceiptItem],
    receipt_id: Optional[int] = None,
) -> Optional[models.PurchaseOrder]:
    if purchase_order_id is None:
        return None
    order = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.id == purchase_order_id
    ).first()
    if order is None or order.shop_id != shop_id:
        raise HTTPException(status_code=404, detail=tr("Đơn đặt hàng không thuộc cửa hàng"))
    if order.supplier_id != supplier_id:
        raise HTTPException(
            status_code=409,
            detail=tr("Nhà cung cấp của phiếu nhập không khớp đơn đặt hàng"),
        )
    if order.status != ORDER_ORDERED:
        raise HTTPException(
            status_code=409,
            detail=tr("Chỉ đơn đang đặt mới được lập phiếu nhận hàng"),
        )
    linked = db.query(models.PurchaseReceipt.id).filter(
        models.PurchaseReceipt.purchase_order_id == order.id
    ).scalar()
    if linked is not None and linked != receipt_id:
        raise HTTPException(
            status_code=409, detail=tr("Đơn đặt hàng đã có phiếu nhập")
        )
    expected = {
        int(row.product_id): int(row.quantity)
        for row in _purchase_order_items(db, order.id)
    }
    actual: Dict[int, int] = {}
    for row in items:
        product_id = int(row.product_id)
        actual[product_id] = actual.get(product_id, 0) + int(row.quantity)
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail=tr("Số lượng phiếu nhập phải khớp đúng đơn đặt hàng"),
        )
    return order


def _draft_fingerprint(
    shop_id: int,
    values: Dict[str, Any],
    items: Iterable[PurchaseReceiptItemInput | models.PurchaseReceiptItem],
) -> str:
    # Thứ tự dòng không mang ý nghĩa nghiệp vụ. Sắp xếp theo toàn bộ nội dung
    # từng dòng để cùng một bản nháp luôn có cùng dấu vân tay, kể cả sau khi
    # UPDATE xóa/tạo lại item với id hoặc thứ tự vật lý khác. Không gộp dòng:
    # hai dòng giống hệt vẫn là hai phần tử và vì vậy vẫn đổi fingerprint.
    canonical_items = [
        {
            "product_id": int(item.product_id),
            "quantity": int(item.quantity),
            "unit_cost": int(item.unit_cost),
            "expiry_date": (item.expiry_date or "").strip() or None,
        }
        for item in items
    ]
    canonical_items.sort(
        key=lambda item: (
            item["product_id"],
            item["expiry_date"] or "",
            item["unit_cost"],
            item["quantity"],
        )
    )
    return _hash(
        {
            "shop_id": int(shop_id),
            "purchase_order_id": values.get("purchase_order_id"),
            "supplier_id": int(values["supplier_id"]),
            "supplier_invoice_number": values.get("supplier_invoice_number"),
            "received_date": values.get("received_date"),
            "due_date": values.get("due_date"),
            "note": values.get("note"),
            "items": canonical_items,
        }
    )


def _stored_draft_fingerprint(
    receipt: models.PurchaseReceipt,
    items: Iterable[models.PurchaseReceiptItem],
) -> str:
    """Fingerprint nội dung nghiệp vụ hiện đang lưu, không dùng metadata."""
    return _draft_fingerprint(
        receipt.shop_id,
        {
            "purchase_order_id": receipt.purchase_order_id,
            "supplier_id": receipt.supplier_id,
            "supplier_invoice_number": receipt.supplier_invoice_number,
            "received_date": receipt.received_date,
            "due_date": receipt.due_date,
            "note": receipt.note,
        },
        items,
    )


def _receipt_out(
    db: Session, receipt: models.PurchaseReceipt, *, repeated: bool = False
) -> Dict[str, Any]:
    items = (
        db.query(models.PurchaseReceiptItem)
        .filter(models.PurchaseReceiptItem.receipt_id == receipt.id)
        .order_by(models.PurchaseReceiptItem.id)
        .all()
    )
    product_meta = {
        row.id: row
        for row in db.query(models.Product).filter(
            models.Product.id.in_([item.product_id for item in items])
        ).all()
    } if items else {}
    payable = db.query(models.SupplierPayableEntry).filter(
        models.SupplierPayableEntry.receipt_id == receipt.id
    ).first()
    paid = 0
    if payable is not None:
        paid_rows = (
            db.query(models.SupplierPaymentAllocation.amount)
            .filter(models.SupplierPaymentAllocation.payable_entry_id == payable.id)
            .all()
        )
        paid = sum(int(amount or 0) for (amount,) in paid_rows)
    supplier_name = db.query(models.Supplier.name).filter(
        models.Supplier.id == receipt.supplier_id
    ).scalar()
    total = int(receipt.total_amount or 0)
    return {
        "id": receipt.id,
        "shop_id": receipt.shop_id,
        "supplier_id": receipt.supplier_id,
        "purchase_order_id": receipt.purchase_order_id,
        "supplier_name": supplier_name,
        "status": receipt.status,
        "supplier_invoice_number": receipt.supplier_invoice_number,
        "received_date": receipt.received_date,
        "due_date": receipt.due_date,
        "note": receipt.note,
        "total_amount": total,
        "paid_amount": paid,
        "remaining_amount": max(total - paid, 0),
        "created_by_user_id": receipt.created_by_user_id,
        "confirmed_by_user_id": receipt.confirmed_by_user_id,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
        "confirmed_at": receipt.confirmed_at,
        # Client phải gửi lại đúng giá trị này khi xác nhận. Nó được tính từ
        # nội dung hiện tại thay vì updated_at để không phụ thuộc độ phân giải
        # đồng hồ hay id item được tạo lại lúc sửa nháp.
        "draft_fingerprint": _stored_draft_fingerprint(receipt, items),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "product_code": (
                    product_meta[item.product_id].code
                    if item.product_id in product_meta
                    else None
                ),
                "track_batches": bool(
                    product_meta[item.product_id].track_batches
                    if item.product_id in product_meta
                    else False
                ),
                "quantity": int(item.quantity),
                "unit_cost": int(item.unit_cost),
                "line_total": int(item.quantity) * int(item.unit_cost),
                "expiry_date": item.expiry_date,
                "batch_id": item.batch_id,
            }
            for item in items
        ],
        "repeated": repeated,
    }


def create_receipt_draft(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: PurchaseReceiptCreate,
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    subscription_service.require_pro(db, shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác tạo phiếu nhập")
    values = _receipt_values(request)
    # Tương tự tạo NCC: create fingerprint giữ None nếu client bỏ ngày, còn
    # bản nháp lưu ngày Việt Nam đã chốt ở lần đầu. Draft fingerprint trả cho
    # modal confirm vẫn dùng ngày ĐÃ LƯU qua _stored_draft_fingerprint.
    create_values = {
        **values,
        "received_date": _date(request.received_date, "Ngày nhập"),
    }
    fingerprint = _draft_fingerprint(shop_id, create_values, request.items)
    # DRAFT chưa đổi kho/nợ, nhưng vẫn là chứng từ phải luôn trỏ tới một NCC
    # còn tồn tại. SQLite production không bật FK: nếu đọc NCC trước rồi một
    # request khác xóa cứng NCC, INSERT phía dưới vẫn có thể tạo phiếu mồ côi.
    # Khóa shop trước, rồi đọc operation/NCC/sản phẩm dưới cùng write lock.
    # Cả đường retry cũng phải khóa: nếu đọc receipt rồi request khác xóa nháp,
    # trả object cũ từ ORM sẽ báo thành công cho một phiếu thực ra không còn.
    # Delete/deactivate NCC và sửa sản phẩm cũng là write nên sẽ phải xếp hàng.
    _lock_shop(db, shop_id)
    db.expire_all()
    existing = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.create_operation_id == operation_id
    ).first()
    if existing is not None:
        if (
            existing.shop_id != shop_id
            or existing.created_by_user_id != current_user.id
            or existing.create_fingerprint != fingerprint
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=tr("Mã thao tác đã được dùng cho phiếu nhập khác"),
            )
        result = _receipt_out(db, existing, repeated=True)
        db.rollback()
        return result

    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == values["supplier_id"],
        models.Supplier.shop_id == shop_id,
    ).first()
    if supplier is None:
        raise HTTPException(
            status_code=404,
            detail=tr("Nhà cung cấp không thuộc cửa hàng"),
        )
    if not supplier.is_active:
        raise HTTPException(
            status_code=404,
            detail=tr("Nhà cung cấp đã ngừng sử dụng"),
        )
    prepared, total = _prepare_items(db, shop_id, request.items)
    _validate_receipt_purchase_order(
        db,
        shop_id=shop_id,
        supplier_id=supplier.id,
        purchase_order_id=values["purchase_order_id"],
        items=request.items,
    )
    receipt = models.PurchaseReceipt(
        shop_id=shop_id,
        supplier_id=supplier.id,
        purchase_order_id=values["purchase_order_id"],
        status=STATUS_DRAFT,
        supplier_invoice_number=values["supplier_invoice_number"],
        received_date=values["received_date"],
        due_date=values["due_date"],
        note=values["note"],
        total_amount=total,
        create_operation_id=operation_id,
        create_fingerprint=fingerprint,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(receipt)
    try:
        db.flush()
        for row in prepared:
            db.add(
                models.PurchaseReceiptItem(
                    receipt_id=receipt.id,
                    product_id=row["product"].id,
                    product_name=row["product"].name,
                    quantity=row["quantity"],
                    unit_cost=row["unit_cost"],
                    expiry_date=row["expiry_date"],
                )
            )
        _audit(
            db,
            current_user.id,
            "CREATE_PURCHASE_RECEIPT_DRAFT",
            f"Shop #{shop_id}: tạo nháp phiếu nhập #{receipt.id} từ NCC "
            f"'{supplier.name}'",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(models.PurchaseReceipt).filter(
            models.PurchaseReceipt.create_operation_id == operation_id
        ).first()
        if duplicate is not None and (
            duplicate.shop_id == shop_id
            and duplicate.created_by_user_id == current_user.id
            and duplicate.create_fingerprint == fingerprint
        ):
            return _receipt_out(db, duplicate, repeated=True)
        raise HTTPException(
            status_code=409, detail=tr("Mã thao tác đã được dùng cho phiếu nhập khác")
        )
    db.refresh(receipt)
    return _receipt_out(db, receipt)


def _get_receipt(
    db: Session, current_user: models.User, receipt_id: int
) -> Tuple[models.PurchaseReceipt, models.Shop]:
    receipt = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.id == receipt_id
    ).first()
    if receipt is None:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy phiếu nhập"))
    shop = _authorize_shop(db, current_user, receipt.shop_id)
    return receipt, shop


def list_receipts(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    _authorize_shop(db, current_user, shop_id)
    receipts = (
        db.query(models.PurchaseReceipt)
        .filter(models.PurchaseReceipt.shop_id == shop_id)
        .order_by(models.PurchaseReceipt.created_at.desc(), models.PurchaseReceipt.id.desc())
        .all()
    )
    return {"receipts": [_receipt_out(db, receipt) for receipt in receipts]}


def get_receipt_detail(
    db: Session, current_user: models.User, receipt_id: int
) -> Dict[str, Any]:
    receipt, _ = _get_receipt(db, current_user, receipt_id)
    return _receipt_out(db, receipt)


def update_receipt_draft(
    db: Session,
    current_user: models.User,
    receipt_id: int,
    request: PurchaseReceiptUpdate,
) -> Dict[str, Any]:
    receipt, _ = _get_receipt(db, current_user, receipt_id)
    subscription_service.require_pro(db, receipt.shop_id)
    _lock_shop(db, receipt.shop_id)
    db.expire_all()
    receipt = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.id == receipt_id
    ).first()
    if receipt is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy phiếu nhập"))
    if receipt.status != STATUS_DRAFT:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=tr("Phiếu đã xác nhận nên không thể sửa")
        )
    values = _receipt_values(request)
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == values["supplier_id"],
        models.Supplier.shop_id == receipt.shop_id,
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=tr("Nhà cung cấp không thuộc cửa hàng"),
        )
    if not supplier.is_active:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=tr("Nhà cung cấp đã ngừng sử dụng"),
        )
    prepared, total = _prepare_items(db, receipt.shop_id, request.items)
    if receipt.purchase_order_id != values["purchase_order_id"]:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Không được đổi liên kết đơn đặt hàng của phiếu nhập"),
        )
    _validate_receipt_purchase_order(
        db,
        shop_id=receipt.shop_id,
        supplier_id=supplier.id,
        purchase_order_id=values["purchase_order_id"],
        items=request.items,
        receipt_id=receipt.id,
    )
    db.query(models.PurchaseReceiptItem).filter(
        models.PurchaseReceiptItem.receipt_id == receipt.id
    ).delete(synchronize_session=False)
    receipt.supplier_id = supplier.id
    receipt.purchase_order_id = values["purchase_order_id"]
    receipt.supplier_invoice_number = values["supplier_invoice_number"]
    receipt.received_date = values["received_date"]
    receipt.due_date = values["due_date"]
    receipt.note = values["note"]
    receipt.total_amount = total
    receipt.updated_by_user_id = current_user.id
    for row in prepared:
        db.add(
            models.PurchaseReceiptItem(
                receipt_id=receipt.id,
                product_id=row["product"].id,
                product_name=row["product"].name,
                quantity=row["quantity"],
                unit_cost=row["unit_cost"],
                expiry_date=row["expiry_date"],
            )
        )
    _audit(
        db,
        current_user.id,
        "UPDATE_PURCHASE_RECEIPT_DRAFT",
        f"Shop #{receipt.shop_id}: sửa nháp phiếu nhập #{receipt.id}",
    )
    db.commit()
    db.refresh(receipt)
    return _receipt_out(db, receipt)


def delete_receipt_draft(
    db: Session, current_user: models.User, receipt_id: int
) -> Dict[str, str]:
    receipt, _ = _get_receipt(db, current_user, receipt_id)
    subscription_service.require_pro(db, receipt.shop_id)
    _lock_shop(db, receipt.shop_id)
    db.expire_all()
    receipt = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.id == receipt_id
    ).first()
    if receipt is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy phiếu nhập"))
    if receipt.status != STATUS_DRAFT:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=tr("Phiếu đã xác nhận nên không thể xóa")
        )
    shop_id = receipt.shop_id
    db.query(models.PurchaseReceiptItem).filter(
        models.PurchaseReceiptItem.receipt_id == receipt.id
    ).delete(synchronize_session=False)
    db.delete(receipt)
    _audit(
        db,
        current_user.id,
        "DELETE_PURCHASE_RECEIPT_DRAFT",
        f"Shop #{shop_id}: xóa nháp phiếu nhập #{receipt_id}",
    )
    db.commit()
    return {"msg": "Deleted"}


def _payment_fingerprint(
    supplier_id: int,
    amount: int,
    method: str,
    note: Optional[str],
    reference: Optional[str],
    *,
    receipt_id: Optional[int] = None,
) -> str:
    return _hash(
        {
            "supplier_id": supplier_id,
            "receipt_id": receipt_id,
            "amount": amount,
            "method": method,
            "note": note,
            "reference": reference,
        }
    )


def _payment_out(db: Session, payment: models.SupplierPayment) -> Dict[str, Any]:
    allocations = (
        db.query(models.SupplierPaymentAllocation)
        .filter(models.SupplierPaymentAllocation.payment_id == payment.id)
        .order_by(models.SupplierPaymentAllocation.id)
        .all()
    )
    entry_ids = [row.payable_entry_id for row in allocations]
    receipt_by_entry = {
        entry.id: entry.receipt_id
        for entry in db.query(models.SupplierPayableEntry)
        .filter(models.SupplierPayableEntry.id.in_(entry_ids))
        .all()
    } if entry_ids else {}
    return {
        "id": payment.id,
        "shop_id": payment.shop_id,
        "supplier_id": payment.supplier_id,
        "amount": int(payment.amount),
        "method": payment.method,
        "shift_id": payment.shift_id,
        "cash_movement_id": payment.cash_movement_id,
        "note": payment.note,
        "reference": payment.reference,
        "created_by_user_id": payment.created_by_user_id,
        "created_at": payment.created_at,
        "allocations": [
            {
                "id": row.id,
                "payable_entry_id": row.payable_entry_id,
                "receipt_id": receipt_by_entry.get(row.payable_entry_id),
                "amount": int(row.amount),
            }
            for row in allocations
        ],
    }


def _same_payment(
    payment: models.SupplierPayment,
    supplier: models.Supplier,
    amount: int,
    method: str,
    fingerprint: str,
) -> bool:
    return (
        payment.shop_id == supplier.shop_id
        and payment.supplier_id == supplier.id
        and int(payment.amount) == amount
        and payment.method == method
        and payment.operation_fingerprint == fingerprint
    )


def _record_payment(
    db: Session,
    current_user: models.User,
    supplier: models.Supplier,
    *,
    amount: int,
    method: str,
    note: Optional[str],
    reference: Optional[str],
    idempotency_key: str,
    fingerprint: str,
    entries: Sequence[Tuple[models.SupplierPayableEntry, int]],
) -> Tuple[models.SupplierPayment, bool]:
    existing = db.query(models.SupplierPayment).filter(
        models.SupplierPayment.idempotency_key == idempotency_key
    ).first()
    if existing is not None:
        if not _same_payment(existing, supplier, amount, method, fingerprint):
            raise HTTPException(
                status_code=409,
                detail=tr("Mã thao tác đã được dùng cho lần trả khác"),
            )
        return existing, False

    payment = models.SupplierPayment(
        shop_id=supplier.shop_id,
        supplier_id=supplier.id,
        amount=amount,
        method=method,
        idempotency_key=idempotency_key,
        operation_fingerprint=fingerprint,
        note=note,
        reference=reference,
        created_by_user_id=current_user.id,
    )
    db.add(payment)
    db.flush()

    if method == METHOD_CASH_SHIFT:
        movement, _ = shift_service.add_external_cash_out(
            db,
            current_user,
            supplier.shop_id,
            amount=amount,
            # Mã chuyển động két cũng không được tiết lộ đây là tiền NCC: STAFF
            # xem được chi tiết ca và có thể đọc cả operation_id qua API.
            operation_id=_key("external-cash", idempotency_key),
            # Chi tiết NCC/công nợ nằm ở SupplierPayment (chỉ chủ shop/Admin
            # đọc được). MANAGER vẫn được xem chi tiết ca để đối chiếu két, nên
            # CashMovement chỉ giữ mô tả chung cùng direction + amount.
            note="Khoản chi",
        )
        payment.shift_id = movement.shift_id
        payment.cash_movement_id = movement.id

    remaining_to_allocate = amount
    for entry, available in entries:
        if remaining_to_allocate <= 0:
            break
        allocated = min(int(available), remaining_to_allocate)
        if allocated <= 0:
            continue
        db.add(
            models.SupplierPaymentAllocation(
                payment_id=payment.id,
                payable_entry_id=entry.id,
                amount=allocated,
            )
        )
        remaining_to_allocate -= allocated
    if remaining_to_allocate != 0:
        raise HTTPException(
            status_code=409,
            detail=tr("Số công nợ vừa thay đổi; vui lòng tải lại rồi trả lại"),
        )
    db.flush()
    return payment, True


def _normalize_payment(
    amount: int,
    method: Optional[str],
    note: Optional[str],
    reference: Optional[str],
) -> Tuple[int, Optional[str], Optional[str], Optional[str]]:
    amount = int(amount)
    clean_note = _clean(note, 500)
    clean_reference = _clean(reference, 128)
    if amount < 0:
        raise HTTPException(status_code=400, detail=tr("Số tiền trả không được âm"))
    if amount == 0:
        return 0, None, None, None
    if method not in METHODS:
        raise HTTPException(status_code=400, detail=tr("Phương thức trả tiền không hợp lệ"))
    if method == METHOD_OUTSIDE and clean_note is None:
        raise HTTPException(
            status_code=400,
            detail=tr("Trả bằng tiền ngoài két phải nhập ghi chú"),
        )
    return amount, method, clean_note, clean_reference


def confirm_receipt(
    db: Session,
    current_user: models.User,
    receipt_id: int,
    request: PurchaseReceiptConfirm,
) -> Dict[str, Any]:
    receipt, _ = _get_receipt(db, current_user, receipt_id)
    subscription_service.require_pro(db, receipt.shop_id)
    operation_id = _operation(request.operation_id, "Mã thao tác xác nhận phiếu")
    paid, method, note, reference = _normalize_payment(
        request.paid_amount, request.method, request.note, request.reference
    )
    confirm_fingerprint = _hash(
        {
            "receipt_id": receipt_id,
            "draft_fingerprint": request.draft_fingerprint,
            "paid_amount": paid,
            "method": method,
            "note": note,
            "reference": reference,
        }
    )
    if receipt.status == STATUS_POSTED:
        if (
            receipt.confirm_operation_id == operation_id
            and receipt.confirm_fingerprint == confirm_fingerprint
        ):
            return _receipt_out(db, receipt, repeated=True)
        raise HTTPException(status_code=409, detail=tr("Phiếu nhập đã được xác nhận"))

    _lock_shop(db, receipt.shop_id)
    db.expire_all()
    receipt = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.id == receipt_id
    ).first()
    if receipt is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy phiếu nhập"))
    if receipt.status == STATUS_POSTED:
        if (
            receipt.confirm_operation_id == operation_id
            and receipt.confirm_fingerprint == confirm_fingerprint
        ):
            db.rollback()
            fresh = db.query(models.PurchaseReceipt).filter(
                models.PurchaseReceipt.id == receipt_id
            ).first()
            return _receipt_out(db, fresh, repeated=True)
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("Phiếu nhập đã được xác nhận"))

    collision = db.query(models.PurchaseReceipt).filter(
        models.PurchaseReceipt.confirm_operation_id == operation_id,
        models.PurchaseReceipt.id != receipt_id,
    ).first()
    if collision is not None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác đã được dùng để xác nhận phiếu nhập khác"),
        )

    draft_items = db.query(models.PurchaseReceiptItem).filter(
        models.PurchaseReceiptItem.receipt_id == receipt.id
    ).order_by(models.PurchaseReceiptItem.id).all()
    if _stored_draft_fingerprint(receipt, draft_items) != request.draft_fingerprint:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Phiếu nháp đã được người khác sửa; hãy mở lại và kiểm tra trước khi xác nhận"
            ),
        )

    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == receipt.supplier_id,
        models.Supplier.shop_id == receipt.shop_id,
        models.Supplier.is_active.is_(True),
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr("Nhà cung cấp đã ngừng sử dụng; hãy dùng lại trước khi xác nhận"),
        )
    prepared, total = _prepare_items(db, receipt.shop_id, draft_items)
    linked_order = _validate_receipt_purchase_order(
        db,
        shop_id=receipt.shop_id,
        supplier_id=receipt.supplier_id,
        purchase_order_id=receipt.purchase_order_id,
        items=draft_items,
        receipt_id=receipt.id,
    )
    if paid > total:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Số tiền trả ({paid}đ) vượt tổng phiếu ({total}đ)",
                paid=f"{paid:,}",
                total=f"{total:,}",
            ),
        )

    current_balance, _ = _supplier_amounts(db, supplier.id)
    outstanding = total - paid
    if current_balance > MAX_SAFE_VND - outstanding:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Công nợ nhà cung cấp sau khi nhập vượt giới hạn {maximum}đ",
                maximum=f"{MAX_SAFE_VND:,}",
            ),
        )

    for row in prepared:
        source = row["source"]
        product = row["product"]
        source.product_name = product.name
        batch = catalog_service.add_purchase_stock(
            db,
            product,
            row["quantity"],
            row["unit_cost"],
            row["expiry_date"],
            batch_note=f"Phiếu nhập #{receipt.id}",
        )
        source.batch_id = batch.id if batch is not None else None

    payable = None
    if total > 0:
        payable = models.SupplierPayableEntry(
            shop_id=receipt.shop_id,
            supplier_id=supplier.id,
            receipt_id=receipt.id,
            entry_type=ENTRY_PURCHASE,
            amount=total,
            entry_date=receipt.received_date,
            due_date=receipt.due_date,
            idempotency_key=f"purchase-receipt:{receipt.id}",
            note=(
                f"Phiếu NCC {receipt.supplier_invoice_number}"
                if receipt.supplier_invoice_number
                else f"Phiếu nhập #{receipt.id}"
            ),
            created_by_user_id=current_user.id,
        )
        db.add(payable)
        db.flush()

    if paid > 0:
        assert method is not None and payable is not None
        payment_key = _key("supplier-confirm", operation_id)
        payment_fingerprint = _payment_fingerprint(
            supplier.id,
            paid,
            method,
            note,
            reference,
            receipt_id=receipt.id,
        )
        _record_payment(
            db,
            current_user,
            supplier,
            amount=paid,
            method=method,
            note=note,
            reference=reference,
            idempotency_key=payment_key,
            fingerprint=payment_fingerprint,
            entries=[(payable, total)],
        )

    receipt.total_amount = total
    receipt.status = STATUS_POSTED
    receipt.confirm_operation_id = operation_id
    receipt.confirm_fingerprint = confirm_fingerprint
    receipt.confirmed_by_user_id = current_user.id
    receipt.confirmed_at = datetime.utcnow()
    receipt.updated_by_user_id = current_user.id
    if linked_order is not None:
        linked_order.status = ORDER_RECEIVED
        linked_order.received_by_user_id = current_user.id
        linked_order.received_at = datetime.utcnow()
        linked_order.updated_by_user_id = current_user.id
    _audit(
        db,
        current_user.id,
        "CONFIRM_PURCHASE_RECEIPT",
        f"Shop #{receipt.shop_id}: xác nhận phiếu nhập #{receipt.id} từ NCC "
        f"'{supplier.name}'",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(models.PurchaseReceipt).filter(
            models.PurchaseReceipt.confirm_operation_id == operation_id
        ).first()
        if duplicate is not None and (
            duplicate.id == receipt_id
            and duplicate.confirm_fingerprint == confirm_fingerprint
        ):
            return _receipt_out(db, duplicate, repeated=True)
        raise HTTPException(
            status_code=409,
            detail=tr("Mã thao tác đã được dùng để xác nhận phiếu nhập khác"),
        )
    db.refresh(receipt)
    return _receipt_out(db, receipt)


def create_supplier_payment(
    db: Session,
    current_user: models.User,
    supplier_id: int,
    request: SupplierPaymentCreate,
) -> Dict[str, Any]:
    supplier, _ = _get_supplier(db, current_user, supplier_id)
    operation_id = _operation(request.operation_id, "Mã thao tác trả nhà cung cấp")
    amount, method, note, reference = _normalize_payment(
        request.amount, request.method, request.note, request.reference
    )
    assert amount > 0 and method is not None
    payment_key = _key("supplier-payment", operation_id)
    fingerprint = _payment_fingerprint(
        supplier.id, amount, method, note, reference
    )

    _lock_supplier(db, supplier.id, supplier.shop_id)
    db.expire_all()
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).first()
    if supplier is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy nhà cung cấp"))
    existing = db.query(models.SupplierPayment).filter(
        models.SupplierPayment.idempotency_key == payment_key
    ).first()
    if existing is not None:
        if not _same_payment(existing, supplier, amount, method, fingerprint):
            db.rollback()
            raise HTTPException(
                status_code=409, detail=tr("Mã thao tác đã được dùng cho lần trả khác")
            )
        db.rollback()
        fresh = db.query(models.SupplierPayment).filter(
            models.SupplierPayment.id == existing.id
        ).first()
        return {
            "payment": _payment_out(db, fresh),
            "supplier": _supplier_out(db, supplier),
            "repeated": True,
        }
    entries = _supplier_entries(db, supplier.id)
    allocations = _allocated_map(db, [entry.id for entry in entries])
    open_entries: List[Tuple[models.SupplierPayableEntry, int]] = []
    for entry in entries:
        remaining = max(int(entry.amount) - allocations.get(entry.id, 0), 0)
        if remaining:
            open_entries.append((entry, remaining))
    # Nợ đầu kỳ trước, rồi hạn/ ngày chứng từ cũ nhất trước.
    open_entries.sort(
        key=lambda pair: (
            pair[0].entry_type != ENTRY_OPENING,
            pair[0].due_date or pair[0].entry_date,
            pair[0].entry_date,
            pair[0].id,
        )
    )
    balance = sum(remaining for _, remaining in open_entries)
    if amount > balance:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Chỉ còn nợ nhà cung cấp {balance}đ; không được trả quá số đó",
                balance=f"{balance:,}",
            ),
        )

    payment, _ = _record_payment(
        db,
        current_user,
        supplier,
        amount=amount,
        method=method,
        note=note,
        reference=reference,
        idempotency_key=payment_key,
        fingerprint=fingerprint,
        entries=open_entries,
    )
    _audit(
        db,
        current_user.id,
        "SUPPLIER_PAYMENT",
        f"Shop #{supplier.shop_id}: trả công nợ NCC #{supplier.id} '{supplier.name}'",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(models.SupplierPayment).filter(
            models.SupplierPayment.idempotency_key == payment_key
        ).first()
        if duplicate is not None and _same_payment(
            duplicate, supplier, amount, method, fingerprint
        ):
            return {
                "payment": _payment_out(db, duplicate),
                "supplier": _supplier_out(db, supplier),
                "repeated": True,
            }
        raise HTTPException(
            status_code=409, detail=tr("Mã thao tác đã được dùng cho lần trả khác")
        )
    db.refresh(payment)
    db.refresh(supplier)
    return {
        "payment": _payment_out(db, payment),
        "supplier": _supplier_out(db, supplier),
        "repeated": False,
    }


__all__ = [
    "create_supplier",
    "list_suppliers",
    "get_supplier_detail",
    "update_supplier",
    "update_supplier_status",
    "delete_supplier",
    "create_supplier_payment",
    "create_receipt_draft",
    "list_receipts",
    "get_receipt_detail",
    "update_receipt_draft",
    "delete_receipt_draft",
    "confirm_receipt",
]
