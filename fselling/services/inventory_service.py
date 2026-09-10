"""Kiểm tra và trừ tồn kho. Giá LUÔN lấy từ database, không tin client."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models
from ..core import thoi_gian
from ..core.i18n import tr
from ..core.money import checked_add, checked_multiply, checked_quantity, checked_vnd, cumulative_basis
from ..core.numeric_limits import MAX_SAFE_QUANTITY
from ..schemas.order import OrderItemCreate
from . import auth_session_service

# Cách định danh một dòng hàng: ("id", 7) hoặc ("name", "Sữa tươi").
# Gom theo khóa này thay vì theo tên trần để hai sản phẩm trùng tên không bị
# cộng dồn vào cùng một dòng.
KhoaSanPham = Tuple[str, Any]


@dataclass(frozen=True)
class CostAllocation:
    """Exact cost provenance consumed from one Product/ProductBatch pool."""

    quantity: int
    known_qty: int
    unknown_qty: int
    cost_basis_vnd: int
    batch: Optional[models.ProductBatch] = None


def _assert_pool(source: Any, physical_qty: int, *, tracked_product: bool = False) -> None:
    known = int(source.cost_known_qty or 0)
    unknown = int(source.cost_unknown_qty or 0)
    basis = int(source.cost_basis_vnd or 0)
    deficit = int(source.cost_deficit_qty or 0)
    if min(known, unknown, basis, deficit) < 0:
        raise HTTPException(status_code=409, detail=tr("Dữ liệu giá vốn không hợp lệ"))
    if tracked_product:
        if known or unknown or basis or deficit:
            raise HTTPException(status_code=409, detail=tr("Giá vốn sản phẩm theo lô bị lệch"))
    elif known + unknown - deficit != int(physical_qty):
        raise HTTPException(status_code=409, detail=tr("Số lượng và giá vốn tồn kho không khớp"))


def consume_cost_pool(source: Any, quantity: int, *, allow_deficit: bool = False) -> CostAllocation:
    """Consume unknown-first, then known; final known consume gets remainder."""
    quantity = checked_quantity(int(quantity), positive=True)
    physical = int(getattr(source, "quantity", getattr(source, "stock", 0)) or 0)
    _assert_pool(source, physical)
    unknown_before = int(source.cost_unknown_qty or 0)
    known_before = int(source.cost_known_qty or 0)
    basis_before = int(source.cost_basis_vnd or 0)
    unknown_taken = min(quantity, unknown_before)
    remaining = quantity - unknown_taken
    known_taken = min(remaining, known_before)
    remaining -= known_taken
    if remaining and not allow_deficit:
        raise HTTPException(status_code=409, detail=tr("Nguồn giá vốn không đủ số lượng"))
    allocated_basis = (
        cumulative_basis(basis_before, known_before, known_taken)
        if known_taken
        else 0
    )
    source.cost_unknown_qty = unknown_before - unknown_taken
    source.cost_known_qty = known_before - known_taken
    source.cost_basis_vnd = basis_before - allocated_basis
    if remaining:
        source.cost_deficit_qty = int(source.cost_deficit_qty or 0) + remaining
    source.cost_state_version = int(source.cost_state_version or 0) + 1
    return CostAllocation(
        quantity=quantity,
        known_qty=known_taken,
        unknown_qty=unknown_taken + remaining,
        cost_basis_vnd=allocated_basis,
        batch=source if isinstance(source, models.ProductBatch) else None,
    )


def add_cost_pool(source: Any, quantity: int, unit_cost_vnd: Optional[int]) -> CostAllocation:
    """Add inbound quantity, covering any deficit before creating a new pool."""
    quantity = checked_quantity(int(quantity), positive=True)
    physical = int(getattr(source, "quantity", getattr(source, "stock", 0)) or 0)
    _assert_pool(source, physical)
    if unit_cost_vnd is not None:
        unit_cost_vnd = checked_vnd(int(unit_cost_vnd))
    deficit_before = int(source.cost_deficit_qty or 0)
    covers_deficit = min(deficit_before, quantity)
    source.cost_deficit_qty = deficit_before - covers_deficit
    surplus = quantity - covers_deficit
    known = surplus if unit_cost_vnd is not None else 0
    unknown = surplus - known
    if (
        int(source.cost_known_qty or 0) + known > MAX_SAFE_QUANTITY
        or int(source.cost_unknown_qty or 0) + unknown > MAX_SAFE_QUANTITY
    ):
        raise HTTPException(status_code=409, detail=tr("Số lượng giá vốn vượt giới hạn"))
    basis = checked_multiply(known, unit_cost_vnd or 0) if known else 0
    source.cost_known_qty = int(source.cost_known_qty or 0) + known
    source.cost_unknown_qty = int(source.cost_unknown_qty or 0) + unknown
    source.cost_basis_vnd = checked_add(int(source.cost_basis_vnd or 0), basis)
    source.cost_state_version = int(source.cost_state_version or 0) + 1
    return CostAllocation(surplus, known, unknown, basis, source if isinstance(source, models.ProductBatch) else None)


def restore_cost_pool(source: Any, known_qty: int, unknown_qty: int, basis_vnd: int) -> None:
    checked_quantity(int(known_qty))
    checked_quantity(int(unknown_qty))
    checked_vnd(int(basis_vnd))
    physical = int(getattr(source, "quantity", getattr(source, "stock", 0)) or 0)
    _assert_pool(source, physical)
    if (
        known_qty + unknown_qty > MAX_SAFE_QUANTITY
        or int(source.cost_known_qty or 0) + int(known_qty) > MAX_SAFE_QUANTITY
        or int(source.cost_unknown_qty or 0) + int(unknown_qty) > MAX_SAFE_QUANTITY
    ):
        raise HTTPException(status_code=409, detail=tr("Số lượng hoàn vượt giới hạn"))
    source.cost_known_qty = int(source.cost_known_qty or 0) + int(known_qty)
    source.cost_unknown_qty = int(source.cost_unknown_qty or 0) + int(unknown_qty)
    source.cost_basis_vnd = checked_add(int(source.cost_basis_vnd or 0), int(basis_vnd))
    source.cost_state_version = int(source.cost_state_version or 0) + 1


def lock_shop_for_inventory(db: Session, shop_id: int) -> None:
    """Lấy cùng shop write-lock trước mọi nghiệp vụ đọc-rồi-ghi tồn/lô.

    SQLite không có SELECT FOR UPDATE. No-op UPDATE này khiến bán hàng, nhập
    hàng, điều chỉnh lô, kiểm kê và hủy hàng phải xếp hàng trước khi đọc tồn.
    Nếu một đường đọc trước khóa rồi gán theo số cũ, request chen giữa có thể
    bị nuốt mất dù cả hai transaction đều báo thành công.
    """
    result = db.execute(
        text("UPDATE shops SET id = id WHERE id = :shop_id"),
        {"shop_id": int(shop_id)},
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    auth_session_service.fence_live_auth_session(db)


def _khoa_cua(item: OrderItemCreate) -> KhoaSanPham:
    if item.product_id is not None:
        return ("id", item.product_id)
    ten = (item.product_name or "").strip()
    if not ten:
        raise HTTPException(
            status_code=400,
            detail=tr("Dòng hàng phải có product_id hoặc product_name"),
        )
    return ("name", ten)


def collect_quantities(items: Iterable[OrderItemCreate]) -> Dict[KhoaSanPham, int]:
    """Gom số lượng theo từng sản phẩm; từ chối số lượng <= 0."""
    wanted: Dict[KhoaSanPham, int] = {}
    for item in items:
        if item.quantity is None or item.quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail=tr("Số lượng sản phẩm không hợp lệ"),
            )
        khoa = _khoa_cua(item)
        combined = wanted.get(khoa, 0) + int(item.quantity)
        if combined > MAX_SAFE_QUANTITY:
            raise HTTPException(status_code=400, detail=tr("Số lượng sản phẩm vượt giới hạn"))
        wanted[khoa] = combined
    return wanted


def resolve_items(
    db: Session, shop_id: int, wanted: Dict[KhoaSanPham, int]
) -> Tuple[List[Tuple[models.Product, int]], int]:
    """Tra sản phẩm trong DB, kiểm tra tồn kho, tính subtotal theo giá DB.

    Mọi truy vấn đều bị chặn trong `shop_id` của đơn, kể cả khi client gửi
    `product_id`: thiếu điều kiện đó thì đoán id là đặt được hàng của shop khác.
    """
    resolved: List[Tuple[models.Product, int]] = []
    subtotal = 0
    for (loai, gia_tri), qty in wanted.items():
        query = db.query(models.Product).filter(
            models.Product.shop_id == shop_id,
            models.Product.is_active == True,  # noqa: E712 - SQLAlchemy cần so sánh ==
        )
        if loai == "id":
            query = query.filter(models.Product.id == gia_tri)
            nhan = f"id={gia_tri}"
        else:
            query = query.filter(models.Product.name == gia_tri)
            nhan = f"'{gia_tri}'"

        prod = query.first()
        if not prod:
            raise HTTPException(
                status_code=404,
                detail=tr(
                    "Sản phẩm {label} không tồn tại hoặc đã ẩn",
                    label=nhan,
                ),
            )
        kha_dung = ton_kha_dung(db, prod)
        if kha_dung < qty:
            # Nói rõ vì sao thiếu khi nguyên nhân là hết hạn: "còn 40 hộp mà
            # báo không đủ" là câu thu ngân sẽ hỏi ngay, và câu trả lời "12 hộp
            # trong đó đã quá hạn" phải nằm sẵn trong thông báo.
            if prod.track_batches and (prod.stock or 0) > kha_dung:
                raise HTTPException(
                    status_code=400,
                    detail=tr(
                        "Sản phẩm '{name}' chỉ còn {available} chưa hết hạn "
                        "(tổng tồn {total}); phần quá hạn không bán được",
                        name=prod.name,
                        available=kha_dung,
                        total=prod.stock or 0,
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' không đủ tồn kho",
                    name=prod.name,
                ),
            )
        line_total = checked_multiply(qty, int(prod.price))
        try:
            subtotal = checked_add(subtotal, line_total)
        except ValueError:
            raise HTTPException(status_code=400, detail=tr("Tổng tiền đơn vượt giới hạn"))
        resolved.append((prod, qty))
    return resolved, subtotal


def _hom_nay() -> str:
    """Ngày nghiệp vụ Việt Nam.

    TRƯỚC ĐÂY dùng `datetime.utcnow()`: từ 0h đến 7h sáng giờ Việt Nam, máy vẫn
    tưởng còn là hôm qua nên hàng đã quá hạn vẫn bán được thêm 7 tiếng. Phải
    dùng chung nguồn với `write_off_service` và `catalog_service`, nếu không sẽ
    có khoảng thời gian một lô vừa không bán được vừa chưa được phép hủy.
    """
    return thoi_gian.hom_nay_vn_str()


def lo_con_ban_duoc(db: Session, product_id: int):
    """Các lô còn hàng và CHƯA hết hạn, xếp theo hạn gần nhất trước (FEFO).

    Lô không có hạn (`expiry_date` NULL) xếp sau cùng: hàng có hạn phải được đẩy
    đi trước, còn hàng không hạn thì để lâu bao nhiêu cũng được.
    """
    hom_nay = _hom_nay()
    lo = (
        db.query(models.ProductBatch)
        .filter(
            models.ProductBatch.product_id == product_id,
            models.ProductBatch.quantity > 0,
        )
        .all()
    )
    con_han = [
        b for b in lo if b.expiry_date is None or b.expiry_date >= hom_nay
    ]
    # None xếp cuối; chuỗi 'YYYY-MM-DD' so sánh trực tiếp là đúng thứ tự ngày.
    con_han.sort(key=lambda b: (b.expiry_date is None, b.expiry_date or "", b.id))
    return con_han


def ton_kha_dung(db: Session, prod: models.Product) -> int:
    """Số lượng THỰC SỰ bán được: đã loại phần quá hạn.

    Sản phẩm không bật `track_batches` thì đây chính là `prod.stock` như cũ.
    """
    if not prod.track_batches:
        return int(prod.stock or 0)
    return sum(b.quantity for b in lo_con_ban_duoc(db, prod.id))


def deduct_stock(
    db: Session,
    resolved_items: Iterable[Tuple[models.Product, int]],
) -> Dict[int, List[CostAllocation]]:
    """Trừ tồn kho (đã kiểm ở resolve_items). Không commit - caller giữ một
    transaction duy nhất.

    Sản phẩm theo lô bị trừ theo FEFO: lấy hết lô hạn gần nhất rồi mới sang lô
    sau, nên hàng cũ ra khỏi kệ trước. Trả về chi tiết lô đã lấy của từng sản
    phẩm để caller ghi `order_item_batches` — không có vết đó thì lúc trả hàng
    không biết nhập lại vào lô nào.
    """
    chi_tiet: Dict[int, List[CostAllocation]] = {}
    for prod, qty in resolved_items:
        if not prod.track_batches:
            allocation = consume_cost_pool(prod, qty)
            prod.stock = int(prod.stock or 0) - qty
            chi_tiet[prod.id] = [allocation]
            continue

        con_lai = qty
        da_lay: List[CostAllocation] = []
        for lo in lo_con_ban_duoc(db, prod.id):
            if con_lai <= 0:
                break
            lay = min(lo.quantity, con_lai)
            allocation = consume_cost_pool(lo, lay)
            lo.quantity = int(lo.quantity or 0) - lay
            con_lai -= lay
            da_lay.append(allocation)
        if con_lai > 0:
            # resolve_items đã kiểm nên tới đây là có ai đó vừa bán chen vào.
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' vừa hết hàng còn hạn; vui lòng thử lại",
                    name=prod.name,
                ),
            )
        prod.stock -= qty      # bản sao tổng, cập nhật cùng transaction với lô
        chi_tiet[prod.id] = da_lay
    return chi_tiet


def allocation_totals(allocations: Iterable[CostAllocation]) -> Tuple[int, int, int]:
    known = sum(allocation.known_qty for allocation in allocations)
    unknown = sum(allocation.unknown_qty for allocation in allocations)
    basis = sum(allocation.cost_basis_vnd for allocation in allocations)
    checked_quantity(known)
    checked_quantity(unknown)
    checked_vnd(basis)
    return known, unknown, basis


def restore_stock(db: Session, order_id: int) -> Tuple[int, int]:
    """Exactly reverse original outbound allocations once, outside returns."""
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy đơn để hoàn kho"))
    if int(order.inventory_reversed or 0):
        return 0, 0
    items = (
        db.query(models.OrderItem).filter(models.OrderItem.order_id == order_id).all()
    )
    restored = 0
    for item in items:
        if int(item.returned_total_qty or 0) or int(item.inventory_reversed or 0):
            raise HTTPException(
                status_code=409,
                detail=tr("Đơn đã có provenance trả/hoàn kho; không thể hủy"),
            )
        if item.product_id is None:
            raise HTTPException(status_code=409, detail=tr("Dòng đơn thiếu product_id; không thể hoàn kho an toàn"))
        prod = (
            db.query(models.Product)
            .filter(
                models.Product.id == item.product_id,
                models.Product.shop_id == order.shop_id,
            )
            .first()
        )
        if prod is None:
            raise HTTPException(status_code=409, detail=tr("Sản phẩm nguồn không thuộc cửa hàng; không thể hoàn kho"))
        quantity = int(item.quantity or 0)
        if quantity <= 0 or int(prod.stock or 0) > MAX_SAFE_QUANTITY - quantity:
            raise HTTPException(status_code=409, detail=tr("Số lượng hoàn hủy không hợp lệ"))
        allocations = (
            db.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == item.id)
            .order_by(models.OrderItemBatch.id)
            .all()
        )
        if allocations:
            if sum(int(row.quantity or 0) for row in allocations) != quantity:
                raise HTTPException(status_code=409, detail=tr("Phân bổ lô hủy không khớp dòng đơn"))
            batch_ids = [int(row.batch_id) for row in allocations]
            batches = {
                int(batch.id): batch
                for batch in db.query(models.ProductBatch)
                .filter(
                    models.ProductBatch.id.in_(batch_ids),
                    models.ProductBatch.product_id == prod.id,
                )
                .all()
            }
            if len(batches) != len(set(batch_ids)):
                raise HTTPException(status_code=409, detail=tr("Lô nguồn không còn tồn tại; không thể hoàn kho"))
            for row in allocations:
                if int(row.inventory_reversed or 0) or int(row.returned_total_qty or 0):
                    raise HTTPException(status_code=409, detail=tr("Phân bổ lô đã được hoàn trước đó"))
                batch = batches[int(row.batch_id)]
                row_quantity = int(row.quantity or 0)
                if int(batch.quantity or 0) > MAX_SAFE_QUANTITY - row_quantity:
                    raise HTTPException(status_code=409, detail=tr("Tồn lô sau hủy vượt giới hạn"))
                restore_cost_pool(
                    batch,
                    int(row.cost_known_qty or 0),
                    int(row.cost_unknown_qty or 0),
                    int(row.cost_basis_vnd or 0),
                )
                batch.quantity = int(batch.quantity or 0) + row_quantity
                row.inventory_reversed = 1
                row.inventory_reversal_version = int(row.inventory_reversal_version or 0) + 1
        else:
            if prod.track_batches:
                raise HTTPException(status_code=409, detail=tr("Dòng theo lô thiếu provenance nguồn"))
            restore_cost_pool(
                prod,
                int(item.cost_known_qty or 0),
                int(item.cost_unknown_qty or 0),
                int(item.cost_basis_vnd or 0),
            )
        prod.stock = int(prod.stock or 0) + quantity
        item.inventory_reversed = 1
        item.inventory_reversal_version = int(item.inventory_reversal_version or 0) + 1
        restored += 1
    order.inventory_reversed = 1
    order.inventory_reversal_key = f"cancel:order:{order_id}"
    order.inventory_reversal_version = int(order.inventory_reversal_version or 0) + 1
    return restored, 0


def offline_batch_deficit_state(db: Session, product_id: int) -> Dict[str, Any]:
    """Return an ABA-safe snapshot and exact open quantity for one product."""
    rows = (
        db.query(models.OfflineBatchStockDeficit)
        .filter(models.OfflineBatchStockDeficit.product_id == int(product_id))
        .order_by(models.OfflineBatchStockDeficit.id)
        .all()
    )
    total = 0
    token_parts = ["i05", str(int(product_id))]
    for row in rows:
        raw_values = (
            row.id,
            row.order_item_id,
            row.product_id,
            row.deficit_quantity,
            row.remaining_quantity,
            row.state_version,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in raw_values
        ):
            raise HTTPException(
                status_code=409,
                detail=tr("Evidence tồn âm offline không hợp lệ"),
            )
        remaining = row.remaining_quantity
        original = row.deficit_quantity
        version = row.state_version
        resolution = row.resolution_kind
        if (
            original <= 0
            or original > MAX_SAFE_QUANTITY
            or remaining < 0
            or remaining > original
            or (
                remaining > 0
                and (resolution is not None or version != 0)
            )
            or (
                remaining == 0
                and not (
                    (resolution == "STOCKTAKE" and version == 1)
                    or (resolution == "MIGRATION_RECONCILED" and version == 0)
                )
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=tr("Evidence tồn âm offline không hợp lệ"),
            )
        if total > MAX_SAFE_QUANTITY - remaining:
            raise HTTPException(
                status_code=409,
                detail=tr("Tổng evidence tồn âm offline vượt giới hạn"),
            )
        total += remaining
        token_parts.append(
            ":".join(
                (
                    str(row.id),
                    str(row.order_item_id),
                    str(row.product_id),
                    str(original),
                    str(remaining),
                    str(resolution or "OPEN"),
                    str(version),
                )
            )
        )
    snapshot = "i05:" + hashlib.sha256(
        "|".join(token_parts).encode("ascii")
    ).hexdigest()
    return {"snapshot": snapshot, "open_quantity": total}


def open_offline_batch_deficit_qty(db: Session, product_id: int) -> int:
    """Return exact unresolved tracked-offline evidence for one product."""
    try:
        return int(offline_batch_deficit_state(db, product_id)["open_quantity"])
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail=tr("Tổng evidence tồn âm offline vượt giới hạn"),
        )


def close_offline_batch_deficits_after_stocktake(
    db: Session, product_id: int, *, actor_user_id: int, resolved_at: str
) -> int:
    """Close exact open evidence after stocktake rebuilds Product.stock."""
    rows = (
        db.query(models.OfflineBatchStockDeficit)
        .filter(
            models.OfflineBatchStockDeficit.product_id == int(product_id),
            models.OfflineBatchStockDeficit.remaining_quantity > 0,
        )
        .order_by(models.OfflineBatchStockDeficit.id)
        .all()
    )
    closed = 0
    for row in rows:
        closed += int(row.remaining_quantity or 0)
        row.remaining_quantity = 0
        row.resolution_kind = "STOCKTAKE"
        row.state_version = int(row.state_version or 0) + 1
        resolve_issue_for_evidence(
            db,
            evidence_kind="OFFLINE_BATCH_DEFICIT",
            evidence_id=int(row.id),
            actor_user_id=actor_user_id,
            resolved_at=resolved_at,
        )
    return closed


# ------------------------------------------------- exact non-batch evidence
#
# The tracked twin above lives in migration 0003 and is unchanged.  Everything
# below belongs to `offline_stock_deficits` (0004 schema, 0005 guards), which
# covers products WITHOUT `track_batches`.  The two never share a validator:
# a tracked row is only ever open-or-closed, while a non-batch row may sit at a
# partial remainder, so the tracked partial rules would wave real corruption
# through.
#
# `products.cost_deficit_qty` is deliberately never read here.  It is an
# aggregate that cannot be attributed back to an order line, so it can never
# say which piece of evidence a stocktake just closed.


def _deficit_snapshot_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "OFFLINE_DEFICIT_EVIDENCE_INVALID",
            "message": tr("Evidence tồn âm offline không hợp lệ"),
        },
    )


def offline_stock_deficit_state(db: Session, product_id: int) -> Dict[str, Any]:
    """Return an ABA-safe snapshot and exact open quantity for one product.

    The token covers identity, provenance, remaining, resolution and version of
    every row, so a deficit that was closed and a new one opened between reading
    and applying a stocktake produces a different token even when the open total
    is unchanged.
    """
    rows = (
        db.query(models.OfflineStockDeficit)
        .filter(models.OfflineStockDeficit.product_id == int(product_id))
        .order_by(models.OfflineStockDeficit.id)
        .all()
    )
    total = 0
    token_parts = ["i09c", str(int(product_id))]
    for row in rows:
        raw_values = (
            row.id,
            row.order_item_id,
            row.product_id,
            row.deficit_quantity,
            row.remaining_quantity,
            row.state_version,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in raw_values
        ):
            raise _deficit_snapshot_error()
        original = row.deficit_quantity
        remaining = row.remaining_quantity
        version = row.state_version
        resolution = row.resolution_kind
        actor = row.resolved_by_user_id
        resolved_at = row.resolved_at
        if (
            original <= 0
            or original > MAX_SAFE_QUANTITY
            or remaining < 0
            or remaining > original
            or version < 0
            # Each legal update drops remaining by at least one and raises the
            # version by exactly one, so the version can never outrun the total
            # reduction, and any reduction must have bumped it at least once.
            or version > original - remaining
            or (remaining < original and version < 1)
            or (remaining == original and version != 0)
            or (
                remaining > 0
                and (
                    resolution is not None
                    or actor is not None
                    or resolved_at is not None
                    or row.resolution_reason is not None
                )
            )
            or (
                remaining == 0
                and (
                    resolution != "STOCKTAKE"
                    or actor is None
                    or not isinstance(resolved_at, str)
                    or len(resolved_at) != 26
                )
            )
        ):
            raise _deficit_snapshot_error()
        if total > MAX_SAFE_QUANTITY - remaining:
            raise HTTPException(
                status_code=409,
                detail=tr("Tổng evidence tồn âm offline vượt giới hạn"),
            )
        total += remaining
        token_parts.append(
            ":".join(
                (
                    str(row.id),
                    str(row.order_item_id),
                    str(row.product_id),
                    str(original),
                    str(remaining),
                    str(resolution or "OPEN"),
                    str(actor if actor is not None else "-"),
                    str(resolved_at or "-"),
                    str(version),
                )
            )
        )
    snapshot = "i09c:" + hashlib.sha256(
        "|".join(token_parts).encode("ascii")
    ).hexdigest()
    return {"snapshot": snapshot, "open_quantity": total}


def open_offline_stock_deficit_qty(db: Session, product_id: int) -> int:
    """Return exact unresolved non-batch offline evidence for one product."""
    return int(offline_stock_deficit_state(db, product_id)["open_quantity"])


def _cas_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "OFFLINE_DEFICIT_CAS_CONFLICT",
            "message": tr(
                "Evidence tồn âm offline đã đổi trong lúc kiểm kê; "
                "vui lòng bắt đầu lại dòng này"
            ),
        },
    )


def _issue_link_invalid() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "OFFLINE_ISSUE_EVIDENCE_LINK_INVALID",
            "message": tr("Bằng chứng tồn âm offline thiếu vướng mắc đi kèm"),
        },
    )


def _issue_state_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "OFFLINE_ISSUE_STATE_CONFLICT",
            "message": tr("Vướng mắc đã đổi trạng thái; vui lòng tải lại"),
        },
    )


def resolve_issue_for_evidence(
    db: Session,
    *,
    evidence_kind: str,
    evidence_id: int,
    actor_user_id: int,
    resolved_at: str,
) -> None:
    """Move the TON_AM issue owning this evidence to RESOLVED, or refuse.

    There is exactly one issue per piece of exact evidence - migration 0005
    verifies it and the ingest path creates it.  So anything else here is
    corruption, not a legacy shape: returning quietly would close a shortfall
    while the job of investigating it disappears from the Đối Soát screen, or
    while somebody else's decision (an acknowledgement) is silently overwritten.

    The CAS is pinned to the exact issue code and evidence identity as well as
    the observed version, so a concurrent transition loses instead of winning
    by accident.
    """
    issues = (
        db.query(models.OfflineReceiptIssue)
        .filter(
            models.OfflineReceiptIssue.evidence_kind == evidence_kind,
            models.OfflineReceiptIssue.evidence_id == int(evidence_id),
            models.OfflineReceiptIssue.issue_code == "TON_AM",
        )
        .all()
    )
    if len(issues) != 1:
        # Missing, duplicated, or wearing a different code: in every case the
        # evidence has no single owner to resolve.
        raise _issue_link_invalid()
    issue = issues[0]
    if issue.state != "OPEN":
        raise _issue_state_conflict()

    order_id = int(issue.order_id)
    issue_code = str(issue.issue_code)
    new_version = int(issue.state_version or 0) + 1
    result = db.execute(
        text(
            """UPDATE offline_receipt_issues
                  SET state = 'RESOLVED',
                      resolution_kind = 'STOCKTAKE',
                      resolved_by_user_id = :actor,
                      resolved_at = :resolved_at,
                      state_version = state_version + 1
                WHERE id = :id AND state = 'OPEN'
                  AND state_version = :version
                  AND issue_code = :issue_code
                  AND evidence_kind = :kind AND evidence_id = :evidence_id"""
        ),
        {
            "actor": int(actor_user_id),
            "resolved_at": resolved_at,
            "id": int(issue.id),
            "version": new_version - 1,
            "issue_code": issue_code,
            "kind": evidence_kind,
            "evidence_id": int(evidence_id),
        },
    )
    if result.rowcount != 1:
        raise _cas_conflict()

    # One audit row per transition, transaction-local.  A stocktake line in the
    # generic log cannot say WHICH problem stopped being somebody's job, and
    # `log_system_action()` would commit by itself and swallow its own failure.
    shop_id = db.execute(
        text("SELECT shop_id FROM orders WHERE id = :id"), {"id": order_id}
    ).scalar()
    db.add(
        models.SystemLog(
            user_id=int(actor_user_id),
            shop_id=int(shop_id) if shop_id is not None else None,
            action="OFFLINE_ISSUE_RESOLVED",
            details=(
                f"Đơn #{order_id} - vướng {issue_code}: OPEN -> RESOLVED "
                f"(v{new_version}) do kiểm kê thực tế"
            ),
        )
    )
    db.expire(issue)


def reconcile_offline_stock_deficits(
    db: Session,
    product_id: int,
    quantity: int,
    *,
    actor_user_id: int,
    resolved_at: str,
) -> int:
    """Consume `quantity` of open non-batch evidence, oldest evidence first.

    FIFO by `id ASC` so the reduction is deterministic when one product carries
    several deficits.  Every row is written with a conditional UPDATE over its
    whole immutable/state tuple plus the version: a row that moved since the
    snapshot cannot be reduced twice, and two deficits of one product can never
    close each other's quantity.
    """
    remaining_to_apply = int(quantity)
    if remaining_to_apply <= 0:
        return 0
    rows = (
        db.query(models.OfflineStockDeficit)
        .filter(
            models.OfflineStockDeficit.product_id == int(product_id),
            models.OfflineStockDeficit.remaining_quantity > 0,
        )
        .order_by(models.OfflineStockDeficit.id)
        .all()
    )
    applied = 0
    for row in rows:
        if remaining_to_apply <= 0:
            break
        old_remaining = int(row.remaining_quantity or 0)
        take = min(old_remaining, remaining_to_apply)
        new_remaining = old_remaining - take
        closing = new_remaining == 0
        result = db.execute(
            text(
                """UPDATE offline_stock_deficits
                      SET remaining_quantity = :new_remaining,
                          resolution_kind = :kind,
                          resolved_by_user_id = :actor,
                          resolved_at = :resolved_at,
                          state_version = state_version + 1
                    WHERE id = :id
                      AND order_item_id = :order_item_id
                      AND product_id = :product_id
                      AND deficit_quantity = :deficit
                      AND remaining_quantity = :old_remaining
                      AND state_version = :version
                      AND resolution_kind IS NULL
                      AND resolved_by_user_id IS NULL
                      AND resolved_at IS NULL
                      AND resolution_reason IS NULL"""
            ),
            {
                "new_remaining": new_remaining,
                # A partial reduction keeps every resolution field NULL: the
                # line is still missing goods, so nothing has been resolved.
                "kind": "STOCKTAKE" if closing else None,
                "actor": int(actor_user_id) if closing else None,
                "resolved_at": resolved_at if closing else None,
                "id": int(row.id),
                "order_item_id": int(row.order_item_id),
                "product_id": int(row.product_id),
                "deficit": int(row.deficit_quantity),
                "old_remaining": old_remaining,
                "version": int(row.state_version or 0),
            },
        )
        if result.rowcount != 1:
            raise _cas_conflict()
        db.expire(row)
        applied += take
        remaining_to_apply -= take
        if closing:
            resolve_issue_for_evidence(
                db,
                evidence_kind="OFFLINE_STOCK_DEFICIT",
                evidence_id=int(row.id),
                actor_user_id=actor_user_id,
                resolved_at=resolved_at,
            )
    if applied != int(quantity):
        # The caller already clamped to the open total under the snapshot, so a
        # shortfall here means the evidence moved underneath this transaction.
        raise _cas_conflict()
    return applied


def doi_chieu_ton_kho(db: Session, shop_id: int) -> List[Dict[str, Any]]:
    """Tìm sản phẩm mà `Product.stock` lệch với tổng số lượng các lô.

    `Product.stock` là BẢN SAO của tổng lô, được ghi cùng transaction ở mọi
    đường. Nhưng "cùng transaction" là lời hứa của code, không phải ràng buộc
    của DB - nên phải có chỗ kiểm lại, cùng nguyên tắc fail-closed với I04
    schema fingerprint kiểm chính xác các financial unique index lúc startup.
    """
    lech: List[Dict[str, Any]] = []
    products = (
        db.query(models.Product)
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.track_batches == True,  # noqa: E712
        )
        .all()
    )
    for prod in products:
        tong_lo = sum(
            b.quantity
            for b in db.query(models.ProductBatch)
            .filter(models.ProductBatch.product_id == prod.id)
            .all()
        )
        if tong_lo != int(prod.stock or 0):
            evidence = open_offline_batch_deficit_qty(db, prod.id)
            lech.append({
                "product_id": prod.id,
                "name": prod.name,
                "stock": int(prod.stock or 0),
                "batch_total": tong_lo,
                "offline_deficit_qty": evidence,
                "has_exact_offline_evidence": (
                    tong_lo - int(prod.stock or 0) == evidence and evidence > 0
                ),
            })
    return lech
