"""Nghiệp vụ cửa hàng."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, text
from sqlalchemy.orm import Session, aliased

from .. import models
from ..core.config import MAX_SHOPS_PER_USER, log_to_file
from ..core.i18n import tr
from ..dependencies import require_own_shop
from ..schemas.shop import ManagerPinSet, ShopCreate
from . import approval_service, auth_session_service, subscription_service
from .log_service import log_system_action

# (thuộc tính trên model, giá trị từ request, thông báo lỗi khi rỗng)
_REQUIRED_FIELDS = [
    ("name", "Tên cửa hàng không được để trống"),
    ("phone", "Số điện thoại không được để trống"),
]

_BANK_FIELDS = frozenset({"bank_code", "bank_account_no", "bank_account_name"})
ERROR_QR_BANK_ACCOUNT_CHANGE_BLOCKED = "QR_BANK_ACCOUNT_CHANGE_BLOCKED"
_PROVIDER_COLLISION_REASON = "PROVIDER_EVENT_COLLISION"


def set_manager_pin(
    db: Session, current_user: models.User, shop_id: int, request: ManagerPinSet
) -> dict:
    return approval_service.set_manager_pin(db, current_user, shop_id, request.pin)


def _assert_qr_account_change_allowed(db: Session, shop_id: int) -> None:
    """Fail closed while any immutable v1 account snapshot is unresolved.

    A v1 intent needs at least one terminal non-collision evidence row. Every
    directly linked row and every collision in a mapped non-collision root's
    provider-identity lineage must be terminal. This check runs only after the
    shared shop write fence has been acquired.
    """
    terminal = ("APPLIED", "REJECTED_NOT_OURS", "REFUNDED")
    terminal_event = exists().where(
        models.BankWebhookEvent.intent_id == models.QrPaymentIntent.id,
        models.BankWebhookEvent.reason_code != _PROVIDER_COLLISION_REASON,
        models.BankWebhookEvent.disposition.in_(terminal),
    )
    intent_without_terminal = (
        db.query(models.QrPaymentIntent.id)
        .filter(
            models.QrPaymentIntent.shop_id == shop_id,
            models.QrPaymentIntent.contract_version == 1,
            ~terminal_event,
        )
        .first()
        is not None
    )
    unresolved_related_event = (
        db.query(models.BankWebhookEvent.id)
        .filter(
            models.BankWebhookEvent.shop_id == shop_id,
            models.BankWebhookEvent.reason_code != _PROVIDER_COLLISION_REASON,
            ~models.BankWebhookEvent.disposition.in_(terminal),
        )
        .first()
        is not None
    )

    # A collision always stays unscoped. Its durable provider identity can
    # still prove lineage only when a mapped non-collision root supplies the
    # seed. This provenance is account-fence-only: it never grants visibility
    # or mutates the collision's nullable shop/intent columns.
    root = aliased(models.BankWebhookEvent)
    intent = aliased(models.QrPaymentIntent)
    lineage = aliased(models.BankWebhookEvent)
    shop_provider_identities = (
        db.query(
            root.provider.label("provider"),
            root.provider_event_id.label("provider_event_id"),
        )
        .join(
            intent,
            or_(
                root.intent_id == intent.id,
                and_(
                    root.order_id == intent.order_id,
                    root.shop_id == intent.shop_id,
                ),
            ),
        )
        .filter(
            intent.shop_id == shop_id,
            intent.contract_version == 1,
            root.reason_code != _PROVIDER_COLLISION_REASON,
            root.provider_event_id.is_not(None),
        )
        .distinct()
        .subquery()
    )
    unresolved_identity_lineage = (
        db.query(lineage.id)
        .join(
            shop_provider_identities,
            and_(
                lineage.provider == shop_provider_identities.c.provider,
                lineage.provider_event_id
                == shop_provider_identities.c.provider_event_id,
            ),
        )
        .filter(~lineage.disposition.in_(terminal))
        .first()
        is not None
    )
    if (
        intent_without_terminal
        or unresolved_related_event
        or unresolved_identity_lineage
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": ERROR_QR_BANK_ACCOUNT_CHANGE_BLOCKED,
                "message": "Bank account has unresolved QR payment evidence",
            },
        )


def _clean_and_validate(shop: ShopCreate) -> Dict[str, str]:
    """Trim toàn bộ field và validate theo đúng thứ tự thông báo lỗi như code cũ."""
    data = {
        "name": (shop.name or "").strip(),
        "business_address": (shop.business_address or "").strip(),
        "tax_code": (shop.tax_code or "").strip(),
        "phone": (shop.phone or "").strip(),
        "email": (shop.email or "").strip(),
        "bank_account_no": (shop.bank_account_no or "").strip(),
        "bank_account_name": (shop.bank_account_name or "").strip(),
        "bank_code": (shop.bank_code or "").strip(),
    }
    for field, message in _REQUIRED_FIELDS:
        if not data[field]:
            raise HTTPException(status_code=400, detail=tr(message))
    bank_values = [data[field] for field in _BANK_FIELDS]
    if any(bank_values) and not all(bank_values):
        raise HTTPException(
            status_code=400,
            detail=tr("Vui lòng nhập đủ ngân hàng, số tài khoản và tên chủ tài khoản"),
        )
    return data


def create_shop(db: Session, current_user: models.User, shop: ShopCreate) -> models.Shop:
    # STAFF chỉ vận hành shop được gán; nếu tự tạo shop họ sẽ trở thành owner
    # và đi vòng qua ranh giới quản trị đang được require_own_shop bảo vệ.
    if current_user.role == "STAFF":
        raise HTTPException(
            status_code=403,
            detail=tr("Nhân viên không được tạo cửa hàng"),
        )
    count = db.query(models.Shop).filter(models.Shop.owner_id == current_user.id).count()
    if count >= MAX_SHOPS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Bạn chỉ được tạo tối đa {count} cửa hàng",
                count=MAX_SHOPS_PER_USER,
            ),
        )

    data = _clean_and_validate(shop)
    new_shop = models.Shop(owner_id=current_user.id, **data)
    db.add(new_shop)
    # Trial bắt đầu đúng lúc TẠO shop, không phải lúc người dùng mở tab gói cước
    # lần đầu. flush lấy id nhưng vẫn nằm trong cùng transaction với Shop.
    db.flush()
    subscription_service.create_trial_for_shop(db, new_shop.id)
    db.commit()
    db.refresh(new_shop)
    log_system_action(
        db,
        current_user.id,
        "CREATE_SHOP",
        f"Tạo cửa hàng: '{new_shop.name}' (SĐT: {new_shop.phone}, Bank: {new_shop.bank_code})",
    )
    db.refresh(new_shop)
    return new_shop


def update_shop(
    db: Session, current_user: models.User, shop_id: int, shop: ShopCreate
) -> models.Shop:
    data = _clean_and_validate(shop)
    # Webhook ORDER đọc account dưới đúng lock hàng Shop này. Lấy lock trước
    # mọi read quyết định để update account và account-mismatch có một thứ tự
    # durable duy nhất, kể cả khi chuyển sang DB hỗ trợ row lock thực sự.
    owner_id = current_user.id
    # Dependency xác thực đã có thể mở read snapshot. Đóng snapshot chỉ-đọc
    # trước no-op UPDATE để SQLite không gặp BUSY_SNAPSHOT khi webhook vừa thắng;
    # ownership và Shop đều được đọc lại sau khi đã lấy write lock.
    db.rollback()
    _lock_shop_for_write(db, shop_id, owner_id)
    db_shop = require_own_shop(db, shop_id, current_user)
    db.refresh(db_shop)
    bank_changed = any(
        getattr(db_shop, field) != data[field] for field in _BANK_FIELDS
    )
    if bank_changed:
        try:
            _assert_qr_account_change_allowed(db, shop_id)
        except HTTPException:
            db.rollback()
            raise
    for field, value in data.items():
        setattr(db_shop, field, value)
    db.commit()
    db.refresh(db_shop)
    log_system_action(
        db,
        current_user.id,
        "UPDATE_SHOP",
        f"Cập nhật cửa hàng: '{db_shop.name}' (SĐT: {db_shop.phone})",
    )
    db.refresh(db_shop)
    return db_shop


def toggle_shop_status(db: Session, current_user: models.User, shop_id: int) -> Dict[str, bool]:
    db_shop = require_own_shop(db, shop_id, current_user)
    db_shop.is_active = not db_shop.is_active
    db.commit()
    log_system_action(
        db,
        current_user.id,
        "TOGGLE_SHOP_STATUS",
        f"Đổi trạng thái cửa hàng '{db_shop.name}': "
        f"{'Hoạt động' if db_shop.is_active else 'Khóa'}",
    )
    return {"is_active": db_shop.is_active}


def list_shops(db: Session, current_user: models.User) -> List[models.Shop]:
    log_to_file(f"get_shops requested by user='{current_user.username}' (ID={current_user.id})")
    if current_user.role == "STAFF":
        # Nhân viên chỉ thấy đúng shop được gán, không thấy shop nào khác.
        shops = (
            db.query(models.Shop)
            .filter(models.Shop.id == current_user.staff_shop_id)
            .all()
        )
    elif current_user.role == "ADMIN":
        # ADMIN cần chọn được cửa hàng trước khi mở các màn giám sát như
        # công nợ nhà cung cấp. Quyền sửa/xóa vẫn do require_own_shop bảo vệ.
        shops = db.query(models.Shop).all()
    else:
        shops = db.query(models.Shop).filter(models.Shop.owner_id == current_user.id).all()
    log_to_file(f"get_shops DB query returned: {[s.id for s in shops]}")
    return shops


def _lock_shop_for_write(db: Session, shop_id: int, owner_id: int) -> None:
    """Tuần tự hóa update/xóa shop với mọi luồng mutation cùng shop.

    SQLite không có ``SELECT FOR UPDATE``. Cùng no-op UPDATE trên hàng ``shops``
    mà luồng đơn hàng dùng sẽ giữ write lock tới commit/rollback, nhờ vậy lần
    kiểm lịch sử bên dưới không thể vừa thấy trống thì một bút toán điểm chen
    vào trước lúc xóa shop.
    """
    locked = db.execute(
        text(
            "UPDATE shops SET id = id "
            "WHERE id = :shop_id AND owner_id = :owner_id"
        ),
        {"shop_id": shop_id, "owner_id": owner_id},
    )
    if locked.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    auth_session_service.fence_live_auth_session(db)


def _has_loyalty_data(db: Session, shop_id: int) -> bool:
    """Có cấu hình hoặc một dòng sổ điểm là đã có chứng từ cần giữ."""
    has_program = (
        db.query(models.LoyaltyProgram.id)
        .filter(models.LoyaltyProgram.shop_id == shop_id)
        .first()
        is not None
    )
    if has_program:
        return True
    return (
        db.query(models.LoyaltyPointEntry.id)
        .filter(models.LoyaltyPointEntry.shop_id == shop_id)
        .first()
        is not None
    )


def _has_supplier_data(db: Session, shop_id: int) -> bool:
    """Hồ sơ NCC hoặc chứng từ nhập/nợ đã có thì không xóa cứng shop."""
    return any(
        query.first() is not None
        for query in (
            db.query(models.Supplier.id).filter(models.Supplier.shop_id == shop_id),
            db.query(models.PurchaseReceipt.id).filter(
                models.PurchaseReceipt.shop_id == shop_id
            ),
            db.query(models.SupplierPayableEntry.id).filter(
                models.SupplierPayableEntry.shop_id == shop_id
            ),
            db.query(models.SupplierPayment.id).filter(
                models.SupplierPayment.shop_id == shop_id
            ),
        )
    )


def _has_subscription_history(db: Session, shop_id: int) -> bool:
    """Đã có checkout/quà/tiền gói thì giữ shop để không mất dấu sổ tiền."""
    return any(
        query.first() is not None
        for query in (
            db.query(models.SubscriptionGrant.id).filter(
                models.SubscriptionGrant.shop_id == shop_id
            ),
            db.query(models.SubscriptionCheckout.id).filter(
                models.SubscriptionCheckout.shop_id == shop_id
            ),
            db.query(models.SubscriptionPayment.id).filter(
                models.SubscriptionPayment.shop_id == shop_id
            ),
        )
    )


def _has_fnb_data(db: Session, shop_id: int) -> bool:
    return any(
        query.first() is not None
        for query in (
            db.query(models.FnbArea.id).filter(models.FnbArea.shop_id == shop_id),
            db.query(models.FnbServiceSession.id).filter(
                models.FnbServiceSession.shop_id == shop_id
            ),
            db.query(models.FnbActionLog.id).filter(
                models.FnbActionLog.shop_id == shop_id
            ),
        )
    )


def delete_shop(db: Session, current_user: models.User, shop_id: int) -> Dict[str, str]:
    # Lấy lock trước lần đọc quyết định. Điều kiện owner_id giữ nguyên hành vi
    # 404 cho người không phải chủ mà không cần mở một read transaction trước.
    _lock_shop_for_write(db, shop_id, current_user.id)
    db_shop = require_own_shop(db, shop_id, current_user)
    if _has_loyalty_data(db, shop_id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Cửa hàng đã có chương trình hoặc lịch sử tích điểm nên không "
                "thể xóa. Hãy bấm nút Khóa để ngừng sử dụng cửa hàng và giữ "
                "nguyên sổ điểm."
            ),
        )
    if _has_supplier_data(db, shop_id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Cửa hàng đã có nhà cung cấp hoặc lịch sử phiếu nhập/công nợ "
                "nên không thể xóa. Hãy bấm nút Khóa để giữ nguyên chứng từ."
            ),
        )
    if _has_subscription_history(db, shop_id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Cửa hàng đã có lịch sử gói cước hoặc thanh toán nên không thể "
                "xóa. Hãy bấm nút Khóa để giữ nguyên chứng từ."
            ),
        )
    if _has_fnb_data(db, shop_id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Cửa hàng đã có cấu hình hoặc lịch sử bán tại bàn nên không "
                "thể xóa. Hãy bấm nút Khóa để ngừng sử dụng và giữ lịch sử."
            ),
        )
    shop_name = db_shop.name

    order_ids = [
        row[0] for row in db.query(models.Order.id).filter(models.Order.shop_id == shop_id).all()
    ]
    shift_ids = [
        row[0]
        for row in db.query(models.CashShift.id)
        .filter(models.CashShift.shop_id == shop_id)
        .all()
    ]
    if shift_ids:
        db.query(models.CashMovement).filter(
            models.CashMovement.shift_id.in_(shift_ids)
        ).delete(synchronize_session=False)
    if order_ids:
        db.query(models.OrderPayment).filter(
            models.OrderPayment.order_id.in_(order_ids)
        ).delete(synchronize_session=False)
        db.query(models.OrderItem).filter(
            models.OrderItem.order_id.in_(order_ids)
        ).delete(synchronize_session=False)
    db.query(models.Order).filter(models.Order.shop_id == shop_id).delete(
        synchronize_session=False
    )
    if shift_ids:
        db.query(models.CashShift).filter(
            models.CashShift.id.in_(shift_ids)
        ).delete(synchronize_session=False)
    db.query(models.Customer).filter(models.Customer.shop_id == shop_id).delete(
        synchronize_session=False
    )
    db.query(models.Product).filter(models.Product.shop_id == shop_id).delete(
        synchronize_session=False
    )
    db.query(models.Category).filter(models.Category.shop_id == shop_id).delete(
        synchronize_session=False
    )
    db.query(models.Voucher).filter(models.Voucher.shop_id == shop_id).delete(
        synchronize_session=False
    )
    # Shop chỉ mới có trial mặc định, chưa phát sinh checkout/quà/tiền thì được
    # xóa cứng; SQLite production không bật FK nên phải dọn aggregate tường minh.
    db.query(models.ShopSubscription).filter(
        models.ShopSubscription.shop_id == shop_id
    ).delete(synchronize_session=False)
    # Shop không còn tồn tại thì mọi tài khoản nhân viên gán vào đó phải bị
    # vô hiệu ngay; giữ User để audit cũ vẫn truy ra đúng tên.
    staff_ids = [
        staff_id
        for (staff_id,) in db.query(models.User.id).filter(
            models.User.role == "STAFF",
            models.User.staff_shop_id == shop_id,
        ).all()
    ]
    if staff_ids:
        db.query(models.AuthSession).filter(
            models.AuthSession.user_id.in_(staff_ids),
            models.AuthSession.revoked_at.is_(None),
        ).update(
            {
                models.AuthSession.revoked_at: datetime.utcnow(),
                models.AuthSession.revoked_by_user_id: current_user.id,
                models.AuthSession.revoke_reason: "SHOP_DELETE",
            },
            synchronize_session=False,
        )
    db.query(models.User).filter(
        models.User.role == "STAFF",
        models.User.staff_shop_id == shop_id,
    ).update(
        {
            models.User.is_active: False,
            models.User.staff_shop_id: None,
        },
        synchronize_session=False,
    )

    db.delete(db_shop)
    db.commit()
    log_system_action(db, current_user.id, "DELETE_SHOP", f"Xóa cửa hàng '{shop_name}'")
    return {"msg": "Deleted"}
