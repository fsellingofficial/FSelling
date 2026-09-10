"""FastAPI dependencies dùng chung: DB session, xác thực, phân quyền."""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import models
from .core.config import log_to_file
from .core.database import SessionLocal
from .core.i18n import tr
from .core.security import decode_access_token, extract_bearer_token
from .services import auth_session_service

STAFF_ROLE_CASHIER = "CASHIER"
STAFF_ROLE_SERVICE = "SERVICE"
STAFF_ROLE_WAREHOUSE = "WAREHOUSE"
STAFF_ROLE_MANAGER = "MANAGER"
STAFF_ROLE_KITCHEN = "KITCHEN"
STAFF_ROLE_BAR = "BAR"
STAFF_ROLES = frozenset(
    {
        STAFF_ROLE_CASHIER,
        STAFF_ROLE_SERVICE,
        STAFF_ROLE_WAREHOUSE,
        STAFF_ROLE_MANAGER,
        STAFF_ROLE_KITCHEN,
        STAFF_ROLE_BAR,
    }
)

PERMISSION_SALE = "SALE"
PERMISSION_INVENTORY = "INVENTORY"
PERMISSION_CUSTOMER = "CUSTOMER"
PERMISSION_REPORT = "REPORT"
PERMISSION_VOUCHER = "VOUCHER"
PERMISSION_RECONCILIATION = "RECONCILIATION"
PERMISSION_CATALOG_READ = "CATALOG_READ"
PERMISSION_FNB_SERVICE = "FNB_SERVICE"
PERMISSION_FNB_MANAGE = "FNB_MANAGE"
PERMISSION_FNB_KITCHEN = "FNB_KITCHEN"
PERMISSION_FNB_BAR = "FNB_BAR"
PERMISSION_FNB_CHECKOUT = "FNB_CHECKOUT"
PERMISSION_ORDER_RETURN = "ORDER_RETURN"
PERMISSION_RETURN_APPROVE = "RETURN_APPROVE"

_STAFF_ROLE_PERMISSIONS = {
    STAFF_ROLE_CASHIER: frozenset(
        {
            PERMISSION_SALE,
            PERMISSION_CUSTOMER,
            PERMISSION_CATALOG_READ,
            PERMISSION_FNB_SERVICE,
            PERMISSION_FNB_CHECKOUT,
            PERMISSION_ORDER_RETURN,
        }
    ),
    STAFF_ROLE_SERVICE: frozenset(
        {PERMISSION_CATALOG_READ, PERMISSION_FNB_SERVICE}
    ),
    STAFF_ROLE_WAREHOUSE: frozenset(
        {PERMISSION_INVENTORY, PERMISSION_CATALOG_READ}
    ),
    STAFF_ROLE_MANAGER: frozenset(
        {
            PERMISSION_SALE,
            PERMISSION_INVENTORY,
            PERMISSION_CUSTOMER,
            PERMISSION_REPORT,
            PERMISSION_VOUCHER,
            PERMISSION_RECONCILIATION,
            PERMISSION_CATALOG_READ,
            PERMISSION_FNB_SERVICE,
            PERMISSION_FNB_MANAGE,
            PERMISSION_FNB_KITCHEN,
            PERMISSION_FNB_BAR,
            PERMISSION_FNB_CHECKOUT,
            PERMISSION_ORDER_RETURN,
            PERMISSION_RETURN_APPROVE,
        }
    ),
    STAFF_ROLE_KITCHEN: frozenset(
        {PERMISSION_CATALOG_READ, PERMISSION_FNB_KITCHEN}
    ),
    STAFF_ROLE_BAR: frozenset({PERMISSION_CATALOG_READ, PERMISSION_FNB_BAR}),
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> models.User:
    # Chỉ chấp nhận token qua Authorization header, KHÔNG nhận qua query string
    # để tránh lộ token qua browser history / server log / screenshot.
    token = extract_bearer_token(authorization)
    if not token:
        log_to_file("Auth failed: Token missing")
        raise HTTPException(status_code=401, detail=tr("Phiên đăng nhập không hợp lệ"))
    try:
        payload = decode_access_token(token)
        username = payload.get("sub")
        sid = payload.get("sid")
        if username is None or sid is None:
            log_to_file("Auth failed: sub is None")
            raise HTTPException(status_code=401, detail=tr("Phiên đăng nhập không hợp lệ"))
    except jwt.PyJWTError as e:
        log_to_file(f"Auth failed: PyJWTError: {e}")
        raise HTTPException(status_code=401, detail=tr("Phiên đăng nhập không hợp lệ"))

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        log_to_file(f"Auth failed: User not found: '{username}'")
        raise HTTPException(status_code=401, detail=tr("Không tìm thấy người dùng"))
    if user.is_active is False:
        log_to_file(f"Auth failed: User disabled: '{username}'")
        raise HTTPException(status_code=401, detail=tr("Tài khoản đã ngừng hoạt động"))

    auth_session = auth_session_service.require_live_session(db, user.id, sid)
    auth_session_service.bind_request_session(db, auth_session)

    log_to_file(f"Auth success: user='{username}' (ID={user.id})")
    return user


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=403,
            detail=tr("Chỉ quản trị viên được thực hiện thao tác này"),
        )
    return current_user


def effective_staff_role(current_user: models.User) -> Optional[str]:
    """Vai trò vận hành thực tế của STAFF.

    Dữ liệu STAFF tạo trước khi có cột ``staff_role`` được coi là MANAGER để
    giữ nguyên toàn bộ quyền vận hành cũ. Giá trị lạ không được tự nâng lên
    MANAGER; bảng quyền phía dưới sẽ từ chối mọi thao tác có phân quyền.
    """
    if current_user.role != "STAFF":
        return None
    return (getattr(current_user, "staff_role", None) or STAFF_ROLE_MANAGER).upper()


def has_staff_permission(current_user: models.User, permission: str) -> bool:
    """SELLER/ADMIN không bị giới hạn bởi preset của STAFF."""
    if current_user.role != "STAFF":
        return True
    permissions = _STAFF_ROLE_PERMISSIONS.get(
        effective_staff_role(current_user), frozenset()
    )
    return permission in permissions


def require_staff_permission(
    current_user: models.User, permission: str
) -> models.User:
    if not has_staff_permission(current_user, permission):
        raise HTTPException(
            status_code=403,
            detail=tr("Vai trò nhân viên không có quyền thực hiện thao tác này"),
        )
    return current_user


def require_any_staff_permission(
    current_user: models.User, *permissions: str
) -> models.User:
    if not any(has_staff_permission(current_user, p) for p in permissions):
        raise HTTPException(
            status_code=403,
            detail=tr("Vai trò nhân viên không có quyền thực hiện thao tác này"),
        )
    return current_user


def has_shop_operator_access(shop: models.Shop, current_user: models.User) -> bool:
    """Ai được VẬN HÀNH shop này (bán hàng, quản lý SP/danh mục/voucher/kho,
    xem báo cáo): ADMIN, chủ shop, hoặc nhân viên (STAFF) được gán vào shop đó.

    KHÔNG bao gồm thao tác quản trị (sửa/xóa shop, quản lý nhân viên) - những
    thao tác đó vẫn dùng require_own_shop (chỉ đúng chủ shop)."""
    if current_user.role == "ADMIN":
        return True
    if shop.owner_id == current_user.id:
        return True
    if current_user.role == "STAFF" and current_user.staff_shop_id == shop.id:
        return True
    return False


def has_cost_visibility(shop: models.Shop, current_user: models.User) -> bool:
    """Ai được XEM và SỬA giá vốn: chỉ ADMIN và chủ shop.

    Hẹp hơn hẳn `has_shop_operator_access` và CỐ Ý không đi theo
    PERMISSION_REPORT: MANAGER đang xem được doanh thu, nhưng giá vốn là con số
    nhạy cảm nhất của cửa hàng (biết giá vốn là biết chỗ để rút ruột, và là thứ
    một nhân viên nghỉ việc mang thẳng sang đối thủ được). Nới ra sau này dễ,
    thu hẹp lại thì dữ liệu đã lộ rồi.

    Thu ngân vẫn bán hàng bình thường: giá vốn được đọc và chốt ở phía server
    khi tạo đơn, không bao giờ đi ra client.
    """
    if current_user.role == "ADMIN":
        return True
    return shop.owner_id == current_user.id


def require_cost_visibility(shop: models.Shop, current_user: models.User) -> None:
    if not has_cost_visibility(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Chỉ chủ cửa hàng mới xem được giá vốn và lãi"),
        )


def require_shop_access(
    db: Session, shop_id: int, current_user: models.User
) -> models.Shop:
    """Đảm bảo current_user được vận hành shop (chủ shop, ADMIN, hoặc nhân viên
    được gán vào shop). Nếu không -> 403/404."""
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    if not has_shop_operator_access(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Bạn không có quyền truy cập cửa hàng này"),
        )
    return shop


def require_own_shop(
    db: Session, shop_id: int, current_user: models.User
) -> models.Shop:
    """Một số endpoint cũ chỉ cho phép ĐÚNG chủ shop (ADMIN cũng không được),
    trả 404 khi không khớp. Giữ nguyên hành vi đó."""
    shop = (
        db.query(models.Shop)
        .filter(models.Shop.id == shop_id, models.Shop.owner_id == current_user.id)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail=tr("Không tìm thấy cửa hàng"))
    return shop
