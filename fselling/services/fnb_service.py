from __future__ import annotations

import datetime
import hashlib
import json
import unicodedata

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..core.i18n import tr
from ..core.money import (
    checked_add,
    checked_multiply,
    checked_vnd,
    cumulative_basis,
    largest_remainder_allocate,
    round_percentage_vnd,
)
from ..core.numeric_limits import MAX_SAFE_QUANTITY
from ..dependencies import (
    PERMISSION_FNB_MANAGE,
    PERMISSION_FNB_BAR,
    PERMISSION_FNB_CHECKOUT,
    PERMISSION_FNB_KITCHEN,
    PERMISSION_FNB_SERVICE,
    effective_staff_role,
    has_shop_operator_access,
    require_own_shop,
    require_shop_access,
    require_staff_permission,
)
from ..schemas.fnb import (
    FnbAreaCreate,
    FnbAreaUpdate,
    FnbCheckAdjustments,
    FnbCheckPay,
    FnbCheckSplit,
    FnbCheckSplitPreview,
    FnbLineCancel,
    FnbLineCreate,
    FnbLineUpdate,
    FnbManagerApprovalCreate,
    FnbMergeTable,
    FnbMoveTable,
    FnbSessionCancel,
    FnbSessionClose,
    FnbSessionOpen,
    FnbSessionSend,
    FnbSettingsUpdate,
    FnbStationUpdate,
    FnbTableCreate,
    FnbTableUpdate,
    FnbTicketTransition,
)
from ..schemas.shop import ManagerPinSet
from . import (
    approval_service,
    inventory_service,
    loyalty_service,
    order_service,
    payment_service,
    qr_sales_service,
    voucher_service,
)

_ACTIVE_SESSION_STATUSES = ("OPEN", "PARTIALLY_SETTLED", "PAYMENT_PENDING")


def fnb_error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": tr(message), **extra},
    )


def require_fnb_access(
    db: Session, shop_id: int, current_user: models.User, permission: str
) -> models.Shop:
    shop = require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, permission)
    return shop


def require_fnb_shop(
    db: Session, shop_id: int, current_user: models.User
) -> models.Shop:
    shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
    if not bool(shop.fnb_enabled):
        raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
    return shop


def normalize_name(value: str) -> tuple[str, str]:
    display = unicodedata.normalize("NFC", " ".join((value or "").strip().split()))
    if not display:
        raise fnb_error(400, "FNB_NAME_REQUIRED", "Tên không được để trống")
    return display, unicodedata.normalize("NFKC", display).casefold()


def operation_fingerprint(action: str, payload: dict) -> str:
    canonical = json.dumps(
        {"action": action, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _existing_operation(
    db: Session, shop_id: int, operation_id: str, fingerprint: str
) -> dict | None:
    operation = (
        db.query(models.FnbActionLog)
        .filter(
            models.FnbActionLog.shop_id == shop_id,
            models.FnbActionLog.operation_id == operation_id,
        )
        .first()
    )
    if operation is None:
        return None
    if operation.operation_fingerprint != fingerprint:
        raise fnb_error(
            409,
            "FNB_OPERATION_REUSED",
            "Mã thao tác đã được dùng cho nội dung khác",
        )
    return json.loads(operation.result_json)


def _record_operation(
    db: Session,
    *,
    shop_id: int,
    session_id: int | None,
    actor_user_id: int,
    action: str,
    operation_id: str,
    fingerprint: str,
    result: dict,
    before=None,
    after=None,
    reason: str | None = None,
) -> None:
    db.add(
        models.FnbActionLog(
            shop_id=shop_id,
            session_id=session_id,
            actor_user_id=actor_user_id,
            action=action,
            operation_id=operation_id,
            operation_fingerprint=fingerprint,
            result_json=_json(result),
            before_json=_json(before) if before is not None else None,
            after_json=_json(after) if after is not None else None,
            reason=reason,
        )
    )


def require_floor_revision(shop: models.Shop, expected_revision: int) -> None:
    current = int(shop.fnb_revision or 0)
    if current != int(expected_revision):
        raise fnb_error(
            409,
            "FNB_FLOOR_CHANGED",
            "Sơ đồ bàn vừa được cập nhật",
            revision=current,
        )


def _prepare_locked_shop(db: Session, shop_id: int) -> None:
    # The auth read may have opened a SQLite snapshot. Close it before taking
    # the shared no-op UPDATE lock, then every caller rechecks auth under lock.
    db.rollback()
    inventory_service.lock_shop_for_inventory(db, shop_id)


def _payload(request, **identity) -> dict:
    return {
        **identity,
        **request.model_dump(exclude={"operation_id"}),
    }


def _area_result(area: models.FnbArea, revision: int) -> dict:
    return {
        "id": area.id,
        "shop_id": area.shop_id,
        "name": area.name,
        "sort_order": int(area.sort_order),
        "active": bool(area.active),
        "fnb_revision": int(revision),
    }


def _table_result(table: models.FnbTable, revision: int) -> dict:
    return {
        "id": table.id,
        "shop_id": table.shop_id,
        "area_id": table.area_id,
        "name": table.name,
        "sort_order": int(table.sort_order),
        "active": bool(table.active),
        "state_version": int(table.state_version),
        "fnb_revision": int(revision),
    }


def _finish(
    db: Session,
    current_user: models.User,
    action: str,
    operation_id: str,
    fingerprint: str,
    result: dict,
    *,
    session_id: int | None = None,
    before=None,
    after=None,
    reason: str | None = None,
) -> dict:
    _record_operation(
        db,
        shop_id=result["shop_id"],
        session_id=session_id,
        actor_user_id=current_user.id,
        action=action,
        operation_id=operation_id,
        fingerprint=fingerprint,
        result=result,
        before=before,
        after=after,
        reason=reason,
    )
    db.commit()
    return result


def update_fnb_settings(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: FnbSettingsUpdate,
) -> dict:
    action = "FNB_SETTINGS_UPDATE"
    fingerprint = operation_fingerprint(action, _payload(request, shop_id=shop_id))
    try:
        require_own_shop(db, shop_id, current_user)
        _prepare_locked_shop(db, shop_id)
        shop = require_own_shop(db, shop_id, current_user)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        require_floor_revision(shop, request.expected_revision)
        before = {
            "fnb_enabled": bool(shop.fnb_enabled),
            "fnb_revision": int(shop.fnb_revision or 0),
        }
        if bool(shop.fnb_enabled) and not request.enabled:
            active = (
                db.query(models.FnbServiceSession.id)
                .filter(
                    models.FnbServiceSession.shop_id == shop_id,
                    models.FnbServiceSession.status.in_(_ACTIVE_SESSION_STATUSES),
                )
                .first()
            )
            if active is not None:
                raise fnb_error(
                    409,
                    "FNB_ACTIVE_SESSION",
                    "Cửa hàng còn bàn đang phục vụ",
                )
        if bool(shop.fnb_enabled) != request.enabled:
            shop.fnb_enabled = request.enabled
            shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = {
            "shop_id": shop.id,
            "fnb_enabled": bool(shop.fnb_enabled),
            "fnb_revision": int(shop.fnb_revision or 0),
        }
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def update_product_station(
    db: Session,
    current_user: models.User,
    product_id: int,
    request: FnbStationUpdate,
) -> dict:
    action = "FNB_PRODUCT_STATION_UPDATE"
    fingerprint = operation_fingerprint(action, _payload(request, product_id=product_id))
    try:
        product = db.get(models.Product, product_id)
        if product is None:
            raise fnb_error(404, "FNB_PRODUCT_NOT_FOUND", "Không tìm thấy món")
        shop_id = int(product.shop_id)
        require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
        _prepare_locked_shop(db, shop_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        require_floor_revision(shop, request.expected_revision)
        product = (
            db.query(models.Product)
            .filter(models.Product.id == product_id, models.Product.shop_id == shop_id)
            .first()
        )
        if product is None:
            raise fnb_error(404, "FNB_PRODUCT_NOT_FOUND", "Không tìm thấy món")
        before = {"station": product.fnb_station}
        if product.fnb_station != request.station:
            product.fnb_station = request.station
            shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        result = {
            "id": product.id,
            "shop_id": shop_id,
            "station": product.fnb_station,
            "fnb_revision": int(shop.fnb_revision or 0),
        }
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def set_manager_pin(
    db: Session,
    current_user: models.User,
    shop_id: int,
    request: ManagerPinSet,
) -> dict:
    require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
    try:
        return approval_service.set_manager_pin(
            db, current_user, shop_id, request.pin
        )
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "MANAGER_REQUIRED":
            raise fnb_error(
                exc.status_code,
                "FNB_MANAGER_REQUIRED",
                "Chỉ chủ quán hoặc quản lý được đặt PIN",
            ) from exc
        raise


def create_manager_approval(
    db: Session,
    current_user: models.User,
    request: FnbManagerApprovalCreate,
) -> dict:
    shop = require_fnb_access(
        db, request.shop_id, current_user, PERMISSION_FNB_SERVICE
    )
    session = (
        db.query(models.FnbServiceSession)
        .filter(
            models.FnbServiceSession.id == request.entity_id,
            models.FnbServiceSession.shop_id == request.shop_id,
        )
        .first()
    )
    if session is None:
        raise fnb_error(404, "FNB_SESSION_NOT_FOUND", "Không tìm thấy phiên phục vụ")
    require_session_revision(db, session, request.revision)
    try:
        token, _ = approval_service.issue_pin_approval(
            db,
            shop=shop,
            actor=current_user,
            approver_username=request.approver_username,
            pin=request.pin,
            action=request.action,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            revision=request.revision,
        )
        db.commit()
        return {"approval_token": token, "expires_in_seconds": 300}
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        mapping = {
            "APPROVAL_PIN_INVALID": ("FNB_PIN_INVALID", "PIN quản lý không đúng"),
            "APPROVAL_RATE_LIMITED": (
                "FNB_PIN_RATE_LIMITED",
                "Đã nhập sai PIN quá nhiều lần; vui lòng thử lại sau",
            ),
        }
        if code in mapping:
            legacy_code, message = mapping[code]
            extra = {
                key: value
                for key, value in exc.detail.items()
                if key not in {"code", "message"}
            }
            raise fnb_error(exc.status_code, legacy_code, message, **extra) from exc
        raise


def create_area(
    db: Session, current_user: models.User, request: FnbAreaCreate
) -> dict:
    action = "FNB_AREA_CREATE"
    fingerprint = operation_fingerprint(action, _payload(request))
    try:
        require_fnb_access(db, request.shop_id, current_user, PERMISSION_FNB_MANAGE)
        _prepare_locked_shop(db, request.shop_id)
        shop = require_fnb_access(
            db, request.shop_id, current_user, PERMISSION_FNB_MANAGE
        )
        existing = _existing_operation(
            db, request.shop_id, request.operation_id, fingerprint
        )
        if existing is not None:
            db.rollback()
            return existing
        require_floor_revision(shop, request.expected_revision)
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        name, name_key = normalize_name(request.name)
        duplicate = (
            db.query(models.FnbArea.id)
            .filter(
                models.FnbArea.shop_id == request.shop_id,
                models.FnbArea.name_key == name_key,
            )
            .first()
        )
        if duplicate is not None:
            raise fnb_error(409, "FNB_NAME_EXISTS", "Tên khu vực đã tồn tại")
        area = models.FnbArea(
            shop_id=request.shop_id,
            name=name,
            name_key=name_key,
            sort_order=request.sort_order,
        )
        db.add(area)
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _area_result(area, shop.fnb_revision)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def _area_for_manage(
    db: Session, current_user: models.User, area_id: int
) -> models.FnbArea:
    area = db.query(models.FnbArea).filter(models.FnbArea.id == area_id).first()
    if area is None or not has_shop_operator_access(
        db.query(models.Shop).filter(models.Shop.id == area.shop_id).one(), current_user
    ):
        raise fnb_error(404, "FNB_AREA_NOT_FOUND", "Không tìm thấy khu vực")
    require_staff_permission(current_user, PERMISSION_FNB_MANAGE)
    return area


def update_area(
    db: Session,
    current_user: models.User,
    area_id: int,
    request: FnbAreaUpdate,
) -> dict:
    action = "FNB_AREA_UPDATE"
    fingerprint = operation_fingerprint(action, _payload(request, area_id=area_id))
    try:
        area = _area_for_manage(db, current_user, area_id)
        shop_id = int(area.shop_id)
        _prepare_locked_shop(db, shop_id)
        area = _area_for_manage(db, current_user, area_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        require_floor_revision(shop, request.expected_revision)
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        before = _area_result(area, shop.fnb_revision)
        if request.active is False and bool(area.active):
            active_table = (
                db.query(models.FnbTable.id)
                .filter(
                    models.FnbTable.area_id == area.id,
                    models.FnbTable.active.is_(True),
                )
                .first()
            )
            if active_table is not None:
                raise fnb_error(
                    409,
                    "FNB_AREA_HAS_ACTIVE_TABLES",
                    "Khu vực còn bàn đang hoạt động",
                )
        if request.name is not None:
            name, name_key = normalize_name(request.name)
            duplicate = (
                db.query(models.FnbArea.id)
                .filter(
                    models.FnbArea.shop_id == shop_id,
                    models.FnbArea.name_key == name_key,
                    models.FnbArea.id != area.id,
                )
                .first()
            )
            if duplicate is not None:
                raise fnb_error(409, "FNB_NAME_EXISTS", "Tên khu vực đã tồn tại")
            area.name, area.name_key = name, name_key
        if request.sort_order is not None:
            area.sort_order = request.sort_order
        if request.active is not None:
            area.active = request.active
        area.updated_at = datetime.datetime.utcnow()
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _area_result(area, shop.fnb_revision)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def create_table(
    db: Session, current_user: models.User, request: FnbTableCreate
) -> dict:
    action = "FNB_TABLE_CREATE"
    fingerprint = operation_fingerprint(action, _payload(request))
    try:
        require_fnb_access(db, request.shop_id, current_user, PERMISSION_FNB_MANAGE)
        _prepare_locked_shop(db, request.shop_id)
        shop = require_fnb_access(
            db, request.shop_id, current_user, PERMISSION_FNB_MANAGE
        )
        existing = _existing_operation(
            db, request.shop_id, request.operation_id, fingerprint
        )
        if existing is not None:
            db.rollback()
            return existing
        require_floor_revision(shop, request.expected_revision)
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        area = (
            db.query(models.FnbArea)
            .filter(
                models.FnbArea.id == request.area_id,
                models.FnbArea.shop_id == request.shop_id,
            )
            .first()
        )
        if area is None:
            raise fnb_error(404, "FNB_AREA_NOT_FOUND", "Không tìm thấy khu vực")
        if not bool(area.active):
            raise fnb_error(409, "FNB_AREA_INACTIVE", "Khu vực đã ngừng hoạt động")
        name, name_key = normalize_name(request.name)
        duplicate = (
            db.query(models.FnbTable.id)
            .filter(
                models.FnbTable.area_id == area.id,
                models.FnbTable.name_key == name_key,
            )
            .first()
        )
        if duplicate is not None:
            raise fnb_error(409, "FNB_NAME_EXISTS", "Tên bàn đã tồn tại")
        table = models.FnbTable(
            shop_id=request.shop_id,
            area_id=area.id,
            name=name,
            name_key=name_key,
            sort_order=request.sort_order,
        )
        db.add(table)
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _table_result(table, shop.fnb_revision)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def _table_for_manage(
    db: Session, current_user: models.User, table_id: int
) -> models.FnbTable:
    table = db.query(models.FnbTable).filter(models.FnbTable.id == table_id).first()
    if table is None or not has_shop_operator_access(
        db.query(models.Shop).filter(models.Shop.id == table.shop_id).one(), current_user
    ):
        raise fnb_error(404, "FNB_TABLE_NOT_FOUND", "Không tìm thấy bàn")
    require_staff_permission(current_user, PERMISSION_FNB_MANAGE)
    return table


def _table_has_active_session(db: Session, table_id: int) -> bool:
    return (
        db.query(models.FnbSessionTable.id)
        .join(
            models.FnbServiceSession,
            models.FnbServiceSession.id == models.FnbSessionTable.session_id,
        )
        .filter(
            models.FnbSessionTable.table_id == table_id,
            models.FnbSessionTable.released_at.is_(None),
            models.FnbServiceSession.status.in_(_ACTIVE_SESSION_STATUSES),
        )
        .first()
        is not None
    )


def update_table(
    db: Session,
    current_user: models.User,
    table_id: int,
    request: FnbTableUpdate,
) -> dict:
    action = "FNB_TABLE_UPDATE"
    fingerprint = operation_fingerprint(action, _payload(request, table_id=table_id))
    try:
        table = _table_for_manage(db, current_user, table_id)
        shop_id = int(table.shop_id)
        _prepare_locked_shop(db, shop_id)
        table = _table_for_manage(db, current_user, table_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        require_floor_revision(shop, request.expected_revision)
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        if int(table.state_version or 0) != request.expected_state_version:
            raise fnb_error(
                409,
                "FNB_TABLE_CHANGED",
                "Bàn vừa được cập nhật",
                state_version=int(table.state_version or 0),
            )
        moving = request.area_id is not None and request.area_id != table.area_id
        deactivating = request.active is False and bool(table.active)
        if (moving or deactivating) and _table_has_active_session(db, table.id):
            raise fnb_error(409, "FNB_TABLE_OCCUPIED", "Bàn đang phục vụ")
        target_area_id = request.area_id if request.area_id is not None else table.area_id
        area = (
            db.query(models.FnbArea)
            .filter(
                models.FnbArea.id == target_area_id,
                models.FnbArea.shop_id == shop_id,
            )
            .first()
        )
        if area is None:
            raise fnb_error(404, "FNB_AREA_NOT_FOUND", "Không tìm thấy khu vực")
        if not bool(area.active):
            raise fnb_error(409, "FNB_AREA_INACTIVE", "Khu vực đã ngừng hoạt động")
        before = _table_result(table, shop.fnb_revision)
        name, name_key = (
            normalize_name(request.name)
            if request.name is not None
            else (table.name, table.name_key)
        )
        duplicate = (
            db.query(models.FnbTable.id)
            .filter(
                models.FnbTable.area_id == target_area_id,
                models.FnbTable.name_key == name_key,
                models.FnbTable.id != table.id,
            )
            .first()
        )
        if duplicate is not None:
            raise fnb_error(409, "FNB_NAME_EXISTS", "Tên bàn đã tồn tại")
        table.area_id = target_area_id
        table.name, table.name_key = name, name_key
        if request.sort_order is not None:
            table.sort_order = request.sort_order
        if request.active is not None:
            table.active = request.active
        table.state_version = int(table.state_version or 0) + 1
        table.updated_at = datetime.datetime.utcnow()
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _table_result(table, shop.fnb_revision)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def _active_links(db: Session, session_id: int) -> list[models.FnbSessionTable]:
    return (
        db.query(models.FnbSessionTable)
        .filter(
            models.FnbSessionTable.session_id == session_id,
            models.FnbSessionTable.released_at.is_(None),
        )
        .order_by(models.FnbSessionTable.id)
        .all()
    )


def serialize_session(db: Session, session: models.FnbServiceSession) -> dict:
    tables = []
    for link in _active_links(db, session.id):
        table = (
            db.query(models.FnbTable)
            .filter(
                models.FnbTable.id == link.table_id,
                models.FnbTable.shop_id == session.shop_id,
            )
            .one()
        )
        tables.append(
            {
                "id": table.id,
                "area_id": table.area_id,
                "name": table.name,
                "state_version": int(table.state_version or 0),
            }
        )
    lines = (
        db.query(models.FnbSessionLine)
        .filter(models.FnbSessionLine.session_id == session.id)
        .order_by(models.FnbSessionLine.id)
        .all()
    )
    serialized_lines = [
        {
            "id": line.id,
            "product_id": line.product_id,
            "product_name": line.product_name,
            "unit_price_vnd": int(line.unit_price_vnd),
            "station": line.station,
            "note": line.note,
            "quantity": int(line.quantity),
            "cancelled_quantity": int(line.cancelled_quantity or 0),
            "sent_quantity": int(line.sent_quantity or 0),
            "sent_cancelled_quantity": int(line.sent_cancelled_quantity or 0),
            "active_sent_quantity": int(line.sent_quantity or 0)
            - int(line.sent_cancelled_quantity or 0),
            "unsent_quantity": int(line.quantity)
            - int(line.sent_quantity or 0)
            - int(line.cancelled_quantity or 0)
            + int(line.sent_cancelled_quantity or 0),
            "billable_quantity": int(line.quantity)
            - int(line.cancelled_quantity or 0),
            "state_version": int(line.state_version or 0),
        }
        for line in lines
    ]
    try:
        subtotal = checked_add(
            *(
                checked_multiply(line["billable_quantity"], line["unit_price_vnd"])
                for line in serialized_lines
            )
        )
    except ValueError as exc:
        raise fnb_error(
            400, "FNB_AMOUNT_TOO_LARGE", "Tổng tiền vượt giới hạn hỗ trợ"
        ) from exc
    tickets = (
        db.query(models.FnbKitchenTicket)
        .filter(models.FnbKitchenTicket.session_id == session.id)
        .order_by(models.FnbKitchenTicket.id)
        .all()
    )
    service_tickets = [_ticket_result(db, ticket) for ticket in tickets]
    service_summary = {stage: 0 for stage in ("NEW", "IN_PROGRESS", "READY", "SERVED")}
    for ticket in service_tickets:
        stage = ticket["service_stage"]
        if stage in service_summary:
            service_summary[stage] += 1
    service_stage = "DIRECT"
    for stage in ("READY", "IN_PROGRESS", "NEW", "SERVED"):
        if service_summary[stage]:
            service_stage = stage
            break
    return {
        "id": session.id,
        "shop_id": session.shop_id,
        "status": session.status,
        "revision": int(session.revision or 0),
        "opened_at": session.opened_at.isoformat() + "Z",
        "tables": tables,
        "lines": serialized_lines,
        "subtotal_vnd": subtotal,
        "unsent_quantity": sum(line["unsent_quantity"] for line in serialized_lines),
        "service_stage": service_stage,
        "service_summary": service_summary,
        "service_tickets": [
            row for row in service_tickets if row["service_stage"] != "CANCELLED"
        ],
    }


def _primary_check(
    db: Session, session: models.FnbServiceSession, *, create: bool = False
) -> models.FnbServiceCheck | None:
    check = (
        db.query(models.FnbServiceCheck)
        .filter(
            models.FnbServiceCheck.session_id == session.id,
            models.FnbServiceCheck.is_primary.is_(True),
        )
        .first()
    )
    if check is None and create:
        check = models.FnbServiceCheck(
            session_id=session.id,
            label="Bill chính",
            is_primary=True,
            status="OPEN",
        )
        db.add(check)
        db.flush()
    return check


def _ordering_check(
    db: Session, session: models.FnbServiceSession
) -> models.FnbServiceCheck:
    primary = _primary_check(db, session, create=True)
    if primary.status == "OPEN":
        return primary
    primary.is_primary = False
    db.flush()
    sequence = db.query(models.FnbServiceCheck).filter_by(session_id=session.id).count()
    supplemental = models.FnbServiceCheck(
        session_id=session.id,
        label=f"Bill bổ sung {sequence}",
        is_primary=True,
        status="OPEN",
    )
    db.add(supplemental)
    db.flush()
    return supplemental


def _adjustment_amount(kind: str, value: int, subtotal: int, *, discount: bool) -> int:
    if kind == "NONE":
        if value != 0:
            raise fnb_error(400, "FNB_ADJUSTMENT_INVALID", "Giá trị phải bằng 0 khi không áp dụng")
        return 0
    if kind == "PERCENT":
        if value > 10_000:
            raise fnb_error(400, "FNB_ADJUSTMENT_INVALID", "Tỷ lệ phải từ 0 đến 100%")
        amount = round_percentage_vnd(subtotal, value)
    else:
        amount = checked_vnd(value)
    if discount and amount > subtotal:
        raise fnb_error(400, "FNB_DISCOUNT_TOO_LARGE", "Giảm giá không được vượt tiền món")
    return amount


def _recalculate_check(db: Session, check: models.FnbServiceCheck) -> None:
    rows = (
        db.query(models.FnbCheckLine, models.FnbSessionLine)
        .join(
            models.FnbSessionLine,
            models.FnbSessionLine.id == models.FnbCheckLine.session_line_id,
        )
        .filter(models.FnbCheckLine.check_id == check.id)
        .order_by(models.FnbCheckLine.id)
        .all()
    )
    try:
        subtotal = checked_add(
            *(checked_multiply(int(row.quantity), int(line.unit_price_vnd)) for row, line in rows)
        )
        discount = _adjustment_amount(
            check.discount_kind, int(check.discount_value or 0), subtotal, discount=True
        )
        service_charge = _adjustment_amount(
            check.service_charge_kind,
            int(check.service_charge_value or 0),
            subtotal,
            discount=False,
        )
        total = checked_add(subtotal - discount, service_charge)
    except ValueError as exc:
        raise fnb_error(400, "FNB_AMOUNT_TOO_LARGE", "Tổng tiền vượt giới hạn hỗ trợ") from exc
    check.subtotal_vnd = subtotal
    check.discount_vnd = discount
    check.service_charge_vnd = service_charge
    check.total_vnd = total


def _serialize_check(db: Session, check: models.FnbServiceCheck) -> dict:
    _recalculate_check(db, check)
    discount_vnd = int(check.discount_vnd or 0)
    total_vnd = int(check.total_vnd or 0)
    if check.order_id is not None:
        order = db.get(models.Order, check.order_id)
        if order is not None:
            discount_vnd = int(order.discount_amount or 0) + int(
                order.loyalty_discount_amount or 0
            )
            total_vnd = int(order.total_amount or 0)
    rows = (
        db.query(models.FnbCheckLine, models.FnbSessionLine)
        .join(
            models.FnbSessionLine,
            models.FnbSessionLine.id == models.FnbCheckLine.session_line_id,
        )
        .filter(models.FnbCheckLine.check_id == check.id)
        .order_by(models.FnbCheckLine.id)
        .all()
    )
    return {
        "id": check.id,
        "label": check.label,
        "is_primary": bool(check.is_primary),
        "status": check.status,
        "revision": int(check.revision or 0),
        "order_id": check.order_id,
        "discount_kind": check.discount_kind,
        "discount_value": int(check.discount_value or 0),
        "service_charge_kind": check.service_charge_kind,
        "service_charge_value": int(check.service_charge_value or 0),
        "subtotal_vnd": int(check.subtotal_vnd or 0),
        "discount_vnd": discount_vnd,
        "service_charge_vnd": int(check.service_charge_vnd or 0),
        "total_vnd": total_vnd,
        "lines": [
            {
                "id": row.id,
                "line_id": line.id,
                "product_id": line.product_id,
                "product_name": line.product_name,
                "unit_price_vnd": int(line.unit_price_vnd),
                "quantity": int(row.quantity),
                "note": line.note,
            }
            for row, line in rows
        ],
    }


def _checks_result(db: Session, session: models.FnbServiceSession) -> dict:
    checks = (
        db.query(models.FnbServiceCheck)
        .filter(models.FnbServiceCheck.session_id == session.id)
        .order_by(models.FnbServiceCheck.is_primary.desc(), models.FnbServiceCheck.id)
        .all()
    )
    return {
        "shop_id": session.shop_id,
        "session_id": session.id,
        "session_status": session.status,
        "session_revision": int(session.revision or 0),
        "checks": [_serialize_check(db, check) for check in checks],
    }


def _check_for_access(
    db: Session, current_user: models.User, check_id: int
) -> tuple[models.FnbServiceCheck, models.FnbServiceSession]:
    check = db.get(models.FnbServiceCheck, check_id)
    session = db.get(models.FnbServiceSession, check.session_id) if check else None
    shop = db.get(models.Shop, session.shop_id) if session else None
    if check is None or session is None or shop is None or not has_shop_operator_access(shop, current_user):
        raise fnb_error(404, "FNB_CHECK_NOT_FOUND", "Không tìm thấy bill")
    require_staff_permission(current_user, PERMISSION_FNB_CHECKOUT)
    return check, session


def _require_check_revision(check: models.FnbServiceCheck, expected: int) -> None:
    if int(check.revision or 0) != int(expected):
        raise fnb_error(
            409,
            "FNB_CHECK_CHANGED",
            "Bill vừa được cập nhật trên thiết bị khác",
            revision=int(check.revision or 0),
        )


def _split_selection(
    db: Session,
    check: models.FnbServiceCheck,
    request: FnbCheckSplitPreview,
) -> tuple[dict[int, tuple[models.FnbCheckLine, models.FnbSessionLine]], dict[int, int]]:
    rows = (
        db.query(models.FnbCheckLine, models.FnbSessionLine)
        .join(
            models.FnbSessionLine,
            models.FnbSessionLine.id == models.FnbCheckLine.session_line_id,
        )
        .filter(models.FnbCheckLine.check_id == check.id)
        .all()
    )
    available = {line.id: (row, line) for row, line in rows}
    selected: dict[int, int] = {}
    for item in request.lines:
        if item.line_id in selected:
            raise fnb_error(400, "FNB_SPLIT_DUPLICATE_LINE", "Một món chỉ được chọn một lần")
        pair = available.get(item.line_id)
        if pair is None or int(item.quantity) > int(pair[0].quantity):
            raise fnb_error(409, "FNB_SPLIT_QUANTITY_CHANGED", "Số lượng món trên bill vừa thay đổi")
        selected[item.line_id] = int(item.quantity)
    remaining = sum(int(row.quantity) for row, _ in rows) - sum(selected.values())
    if remaining <= 0:
        raise fnb_error(400, "FNB_SPLIT_EMPTY_SOURCE", "Bill gốc phải còn ít nhất một món")
    return available, selected


def _split_amounts(
    db: Session,
    check: models.FnbServiceCheck,
    available: dict[int, tuple[models.FnbCheckLine, models.FnbSessionLine]],
    selected: dict[int, int],
) -> dict:
    _recalculate_check(db, check)
    new_subtotal = checked_add(
        *(
            checked_multiply(quantity, int(available[line_id][1].unit_price_vnd))
            for line_id, quantity in selected.items()
        )
    )
    source_subtotal = int(check.subtotal_vnd) - new_subtotal
    destinations = [(0, source_subtotal, "source"), (1, new_subtotal, "new")]
    discounts = dict(largest_remainder_allocate(int(check.discount_vnd), destinations))
    charges = dict(largest_remainder_allocate(int(check.service_charge_vnd), destinations))
    return {
        "source": {
            "subtotal_vnd": source_subtotal,
            "discount_vnd": discounts["source"],
            "service_charge_vnd": charges["source"],
            "total_vnd": source_subtotal - discounts["source"] + charges["source"],
        },
        "new_check": {
            "subtotal_vnd": new_subtotal,
            "discount_vnd": discounts["new"],
            "service_charge_vnd": charges["new"],
            "total_vnd": new_subtotal - discounts["new"] + charges["new"],
        },
    }


def get_checks(db: Session, current_user: models.User, session_id: int) -> dict:
    session = _session_for_access(db, current_user, session_id, PERMISSION_FNB_CHECKOUT)
    require_fnb_shop(db, session.shop_id, current_user)
    if _sync_check_payment_statuses(db, session):
        db.commit()
        db.refresh(session)
    return _checks_result(db, session)


def preview_split(
    db: Session, current_user: models.User, check_id: int, request: FnbCheckSplitPreview
) -> dict:
    check, session = _check_for_access(db, current_user, check_id)
    require_fnb_shop(db, session.shop_id, current_user)
    if check.status != "OPEN":
        raise fnb_error(409, "FNB_CHECK_NOT_OPEN", "Bill không còn mở để tách")
    available, selected = _split_selection(db, check, request)
    result = _split_amounts(db, check, available, selected)
    result["session_revision"] = int(session.revision or 0)
    result["check_revision"] = int(check.revision or 0)
    return result


def split_check(
    db: Session, current_user: models.User, check_id: int, request: FnbCheckSplit
) -> dict:
    action = "FNB_CHECK_SPLIT"
    fingerprint = operation_fingerprint(action, _payload(request, check_id=check_id))
    try:
        check, session = _check_for_access(db, current_user, check_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        check, session = _check_for_access(db, current_user, check_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_CHECKOUT)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        if check.status != "OPEN":
            raise fnb_error(409, "FNB_CHECK_NOT_OPEN", "Bill không còn mở để tách")
        require_session_revision(db, session, request.expected_session_revision)
        _require_check_revision(check, request.expected_revision)
        label, _ = normalize_name(request.label)
        available, selected = _split_selection(db, check, request)
        amounts = _split_amounts(db, check, available, selected)
        before = _checks_result(db, session)
        new_check = models.FnbServiceCheck(
            session_id=session.id,
            label=label,
            is_primary=False,
            status="OPEN",
        )
        db.add(new_check)
        db.flush()
        for line_id, quantity in selected.items():
            source_row, _ = available[line_id]
            if quantity == int(source_row.quantity):
                db.delete(source_row)
            else:
                source_row.quantity = int(source_row.quantity) - quantity
            db.add(
                models.FnbCheckLine(
                    check_id=new_check.id,
                    session_line_id=line_id,
                    quantity=quantity,
                )
            )
        for target, values in ((check, amounts["source"]), (new_check, amounts["new_check"])):
            target.discount_kind = "FLAT" if values["discount_vnd"] else "NONE"
            target.discount_value = values["discount_vnd"]
            target.service_charge_kind = "FLAT" if values["service_charge_vnd"] else "NONE"
            target.service_charge_value = values["service_charge_vnd"]
            _recalculate_check(db, target)
        check.revision = int(check.revision or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _checks_result(db, session)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def update_check_adjustments(
    db: Session,
    current_user: models.User,
    check_id: int,
    request: FnbCheckAdjustments,
) -> dict:
    action = "FNB_CHECK_ADJUST"
    fingerprint = operation_fingerprint(action, _payload(request, check_id=check_id))
    try:
        check, session = _check_for_access(db, current_user, check_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        check, session = _check_for_access(db, current_user, check_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_CHECKOUT)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if check.status != "OPEN":
            raise fnb_error(409, "FNB_CHECK_NOT_OPEN", "Bill không còn mở để điều chỉnh")
        require_session_revision(db, session, request.expected_session_revision)
        _require_check_revision(check, request.expected_revision)
        before = _checks_result(db, session)
        check.discount_kind = request.discount_kind
        check.discount_value = int(request.discount_value)
        check.service_charge_kind = request.service_charge_kind
        check.service_charge_value = int(request.service_charge_value)
        _recalculate_check(db, check)
        check.revision = int(check.revision or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _checks_result(db, session)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def get_provisional_receipt(
    db: Session, current_user: models.User, check_id: int
) -> dict:
    check, session = _check_for_access(db, current_user, check_id)
    require_fnb_shop(db, session.shop_id, current_user)
    tables = [table["name"] for table in serialize_session(db, session)["tables"]]
    return {
        "document_label": "TẠM TÍNH — CHƯA THANH TOÁN",
        "shop_id": session.shop_id,
        "session_id": session.id,
        "tables": tables,
        "check": _serialize_check(db, check),
    }


def _refresh_session_status(
    db: Session, session: models.FnbServiceSession
) -> None:
    statuses = [
        row[0]
        for row in db.query(models.FnbServiceCheck.status)
        .filter(models.FnbServiceCheck.session_id == session.id)
        .all()
    ]
    if any(status in ("PAYING", "PAYMENT_PENDING") for status in statuses):
        session.status = "PAYMENT_PENDING"
    elif any(status in ("PAID", "DEBT", "CANCELLED") for status in statuses):
        session.status = "PARTIALLY_SETTLED"
    else:
        session.status = "OPEN"


def _sync_check_payment_statuses(
    db: Session, session: models.FnbServiceSession
) -> bool:
    changed = False
    checks = (
        db.query(models.FnbServiceCheck)
        .filter(
            models.FnbServiceCheck.session_id == session.id,
            models.FnbServiceCheck.order_id.is_not(None),
        )
        .all()
    )
    for check in checks:
        order = db.get(models.Order, check.order_id)
        if order is None:
            raise fnb_error(409, "FNB_ORDER_MISSING", "Chứng từ thanh toán không còn tồn tại")
        target = {
            order_service.STATUS_PAID: "PAID",
            order_service.STATUS_DEBT: "DEBT",
            order_service.STATUS_CANCELLED: "CANCELLED",
            order_service.STATUS_PENDING: "PAYMENT_PENDING",
            order_service.STATUS_UNRECONCILED: "PAYMENT_PENDING",
        }.get(order.status, "PAYMENT_PENDING")
        if check.status != target:
            check.status = target
            check.revision = int(check.revision or 0) + 1
            check.settled_at = (
                datetime.datetime.utcnow()
                if target in ("PAID", "DEBT", "CANCELLED")
                else None
            )
            changed = True
    if changed:
        _refresh_session_status(db, session)
        session.revision = int(session.revision or 0) + 1
        shop = db.get(models.Shop, session.shop_id)
        if shop is not None:
            shop.fnb_revision = int(shop.fnb_revision or 0) + 1
    return changed


def _allocation_slice(
    allocation: models.FnbStockAllocation, used: int, quantity: int
) -> tuple[int, int, int]:
    known_total = int(allocation.cost_known_qty or 0)
    known_before = min(used, known_total)
    known_after = min(used + quantity, known_total)
    known = known_after - known_before
    unknown = quantity - known
    basis_before = cumulative_basis(
        int(allocation.cost_basis_vnd or 0), known_total, known_before
    )
    basis_after = cumulative_basis(
        int(allocation.cost_basis_vnd or 0), known_total, known_after
    )
    return known, unknown, basis_after - basis_before


def _transfer_line_provenance(
    db: Session,
    check: models.FnbServiceCheck,
    check_line: models.FnbCheckLine,
    order_item: models.OrderItem,
) -> None:
    remaining = int(check_line.quantity)
    allocations = (
        db.query(models.FnbStockAllocation)
        .filter(
            models.FnbStockAllocation.session_line_id == check_line.session_line_id,
            models.FnbStockAllocation.state.in_(("CONSUMED", "TRANSFERRED_TO_ORDER")),
        )
        .order_by(models.FnbStockAllocation.id)
        .all()
    )
    total_known = total_unknown = total_basis = 0
    for allocation in allocations:
        used = int(
            db.query(func.coalesce(func.sum(models.FnbAllocationTransfer.quantity), 0))
            .filter(models.FnbAllocationTransfer.allocation_id == allocation.id)
            .scalar()
            or 0
        )
        available = int(allocation.quantity) - used
        if available <= 0:
            continue
        take = min(available, remaining)
        known, unknown, basis = _allocation_slice(allocation, used, take)
        transfer = models.FnbAllocationTransfer(
            allocation_id=allocation.id,
            check_id=check.id,
            order_item_id=order_item.id,
            quantity=take,
            cost_known_qty=known,
            cost_unknown_qty=unknown,
            cost_basis_vnd=basis,
        )
        db.add(transfer)
        if allocation.batch_id is not None:
            db.add(
                models.OrderItemBatch(
                    order_item_id=order_item.id,
                    batch_id=allocation.batch_id,
                    quantity=take,
                    cost_known_qty=known,
                    cost_unknown_qty=unknown,
                    cost_basis_vnd=basis,
                )
            )
        total_known += known
        total_unknown += unknown
        total_basis += basis
        remaining -= take
        if used + take == int(allocation.quantity):
            allocation.state = "TRANSFERRED_TO_ORDER"
            allocation.resolved_at = datetime.datetime.utcnow()
            allocation.resolution_reason = f"FNB_CHECK:{check.id}:ORDER_ITEM:{order_item.id}"
        if remaining == 0:
            break
    if remaining:
        raise fnb_error(
            409,
            "FNB_ALLOCATION_MISSING",
            "Không đủ dấu vết tồn kho để tạo chứng từ thanh toán",
            line_id=check_line.session_line_id,
            missing_quantity=remaining,
        )
    order_item.cost_known_qty = total_known
    order_item.cost_unknown_qty = total_unknown
    order_item.cost_basis_vnd = total_basis


def _remove_cancelled_from_checks(
    db: Session, session_id: int, line_id: int, quantity: int
) -> None:
    remaining = quantity
    rows = (
        db.query(models.FnbCheckLine, models.FnbServiceCheck)
        .join(models.FnbServiceCheck, models.FnbServiceCheck.id == models.FnbCheckLine.check_id)
        .filter(
            models.FnbServiceCheck.session_id == session_id,
            models.FnbServiceCheck.status == "OPEN",
            models.FnbCheckLine.session_line_id == line_id,
        )
        .order_by(models.FnbServiceCheck.is_primary.desc(), models.FnbServiceCheck.id)
        .all()
    )
    for check_line, check in rows:
        take = min(int(check_line.quantity), remaining)
        if take == int(check_line.quantity):
            db.delete(check_line)
        else:
            check_line.quantity = int(check_line.quantity) - take
        check.revision = int(check.revision or 0) + 1
        db.flush()
        _recalculate_check(db, check)
        remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise fnb_error(409, "FNB_CHECK_QUANTITY_MISSING", "Số lượng bill không khớp món đã gửi")


def _checkout_customer(
    db: Session, shop_id: int, customer_id: int | None
) -> models.Customer | None:
    if customer_id is None:
        return None
    customer = (
        db.query(models.Customer)
        .filter(models.Customer.id == customer_id, models.Customer.shop_id == shop_id)
        .first()
    )
    if customer is None:
        raise fnb_error(404, "FNB_CUSTOMER_NOT_FOUND", "Không tìm thấy khách hàng")
    if not bool(customer.is_active):
        raise fnb_error(409, "FNB_CUSTOMER_INACTIVE", "Khách hàng đã ngừng sử dụng")
    return customer


def _order_checkout_result(
    db: Session, shop: models.Shop, order: models.Order
) -> dict:
    legacy = order_service._create_order_response(db, shop, order)
    return {
        "id": order.id,
        "status": order.status,
        "total_vnd": int(order.total_amount or 0),
        "payment_method": order.payment_method,
        "cash_tendered_vnd": order.cash_tendered_amount,
        "cash_change_vnd": order.cash_change_amount,
        "qr_url": legacy.get("qr_url"),
        **({"qr_intent": legacy["qr_intent"]} if "qr_intent" in legacy else {}),
    }


def pay_check(
    db: Session,
    current_user: models.User,
    check_id: int,
    request: FnbCheckPay,
) -> dict:
    action = "FNB_CHECK_PAY"
    fingerprint = operation_fingerprint(action, _payload(request, check_id=check_id))
    try:
        check, session = _check_for_access(db, current_user, check_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        check, session = _check_for_access(db, current_user, check_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_CHECKOUT)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        if check.status != "OPEN" or check.order_id is not None:
            raise fnb_error(409, "FNB_CHECK_NOT_OPEN", "Bill không còn mở để thanh toán")
        require_session_revision(db, session, request.expected_session_revision)
        _require_check_revision(check, request.expected_revision)
        _recalculate_check(db, check)
        check_lines = (
            db.query(models.FnbCheckLine, models.FnbSessionLine)
            .join(
                models.FnbSessionLine,
                models.FnbSessionLine.id == models.FnbCheckLine.session_line_id,
            )
            .filter(models.FnbCheckLine.check_id == check.id)
            .order_by(models.FnbCheckLine.id)
            .all()
        )
        if not check_lines:
            raise fnb_error(409, "FNB_CHECK_EMPTY", "Bill chưa có món để thanh toán")
        customer = _checkout_customer(db, shop_id, request.customer_id)
        manual_discount = int(check.discount_vnd or 0)
        service_charge = int(check.service_charge_vnd or 0)
        before_promotions = int(check.total_vnd or 0)
        voucher_code = (request.voucher_code or "").strip().upper() or None
        applied_voucher, voucher_discount = voucher_service.resolve_for_order(
            db, shop_id, voucher_code, before_promotions
        )
        amount_after_voucher = max(before_promotions - voucher_discount, 0)
        program = loyalty_service.get_program_model(db, shop_id)
        loyalty_event_at = datetime.datetime.utcnow()
        loyalty_points = 0
        loyalty_discount = 0
        if request.loyalty_points_to_use > 0:
            if customer is None:
                raise fnb_error(
                    400,
                    "FNB_LOYALTY_CUSTOMER_REQUIRED",
                    "Phải chọn khách hàng trước khi dùng điểm",
                )
            balance = loyalty_service.balance_for_customer(
                db,
                customer.id,
                as_of=loyalty_event_at,
                shop_id=shop_id,
            )
            redeemed = loyalty_service.calculate_redeem(
                program,
                balance,
                int(request.loyalty_points_to_use),
                amount_after_voucher,
            )
            loyalty_points = int(redeemed["applied_points"])
            loyalty_discount = int(redeemed["discount"])
        total = checked_vnd(max(amount_after_voucher - loyalty_discount, 0))
        if request.payment_method == order_service.PAYMENT_METHOD_TRANSFER:
            payment_service.require_transfer_account(shop)
        if request.payment_method == order_service.PAYMENT_METHOD_DEBT:
            order_service._kiem_ban_ghi_no(db, customer, total)
        if (
            request.payment_method == order_service.PAYMENT_METHOD_CASH
            and request.cash_tendered_vnd is None
        ):
            raise fnb_error(
                400,
                "FNB_CASH_TENDERED_REQUIRED",
                "Cần nhập số tiền khách đã đưa",
                required=total,
            )
        if (
            request.payment_method != order_service.PAYMENT_METHOD_CASH
            and request.cash_tendered_vnd is not None
        ):
            raise fnb_error(
                400,
                "FNB_CASH_TENDERED_INVALID",
                "Chỉ nhập tiền khách đưa khi thu tiền mặt",
            )
        tendered = (
            int(request.cash_tendered_vnd)
            if request.payment_method == order_service.PAYMENT_METHOD_CASH
            else total
        )
        if request.payment_method == order_service.PAYMENT_METHOD_CASH and tendered < total:
            raise fnb_error(
                400,
                "FNB_CASH_SHORT",
                "Tiền khách đưa chưa đủ",
                required=total,
            )
        shift = None
        if request.payment_method == order_service.PAYMENT_METHOD_CASH and total > 0:
            shift = order_service._current_cash_shift(
                db,
                current_user,
                shop_id,
                required_for_cashier=True,
                lock_for_cash_write=True,
            )
        paid_now = total == 0 or request.payment_method == order_service.PAYMENT_METHOD_CASH
        status = (
            order_service.STATUS_PAID
            if paid_now
            else (
                order_service.STATUS_DEBT
                if request.payment_method == order_service.PAYMENT_METHOD_DEBT
                else order_service.STATUS_PENDING
            )
        )
        order = models.Order(
            shop_id=shop_id,
            created_by_user_id=current_user.id,
            shift_id=shift.id if shift else None,
            operation_id=f"fnb:{hashlib.sha256(request.operation_id.encode()).hexdigest()}",
            operation_fingerprint=fingerprint,
            total_amount=total,
            discount_amount=manual_discount + voucher_discount,
            loyalty_points_redeemed=loyalty_points,
            loyalty_discount_amount=loyalty_discount,
            payment_method=request.payment_method,
            customer_id=customer.id if customer else None,
            voucher_code=voucher_code,
            status=status,
            cash_paid_amount=(total if paid_now and total > 0 else 0),
            cash_tendered_amount=(tendered if request.payment_method == "cash" else None),
            cash_change_amount=(tendered - total if request.payment_method == "cash" else None),
            loyalty_earn_amount_step=(
                program.earn_amount if customer and program and program.enabled else None
            ),
            loyalty_earn_points_step=(
                program.earn_points if customer and program and program.enabled else None
            ),
            loyalty_expiry_days_snapshot=(
                program.expiry_days if customer and program and program.enabled else None
            ),
        )
        db.add(order)
        db.flush()
        if loyalty_points > 0:
            loyalty_service.add_entry(
                db,
                shop_id,
                customer.id,
                loyalty_service.ENTRY_REDEEM,
                -loyalty_points,
                f"redeem:order:{order.id}",
                order_id=order.id,
                created_by_user_id=current_user.id,
                note=f"Giữ điểm để dùng cho đơn #{order.id}",
                created_at=loyalty_event_at,
            )
        created: list[tuple[models.OrderItem, int]] = []
        for check_line, line in check_lines:
            gross = checked_multiply(int(check_line.quantity), int(line.unit_price_vnd))
            order_item = models.OrderItem(
                order_id=order.id,
                product_id=line.product_id,
                product_name=line.product_name,
                price=int(line.unit_price_vnd),
                quantity=int(check_line.quantity),
                net_amount_vnd=gross,
            )
            db.add(order_item)
            db.flush()
            _transfer_line_provenance(db, check, check_line, order_item)
            created.append((order_item, gross))
        if service_charge > 0:
            charge_item = models.OrderItem(
                order_id=order.id,
                product_id=None,
                product_name="Phụ thu",
                price=service_charge,
                quantity=1,
                cost_known_qty=1,
                cost_unknown_qty=0,
                cost_basis_vnd=0,
            )
            db.add(charge_item)
            db.flush()
            created.append((charge_item, service_charge))
        discounts = dict(
            largest_remainder_allocate(
                manual_discount + voucher_discount,
                [(item.id, gross, item.id) for item, gross in created],
            )
        )
        after_discount = [
            (item.id, gross - discounts[item.id], item.id)
            for item, gross in created
        ]
        loyalty_discounts = dict(
            largest_remainder_allocate(loyalty_discount, after_discount)
        )
        for item, gross in created:
            item.discount_vnd = discounts[item.id]
            item.loyalty_discount_vnd = loyalty_discounts[item.id]
            item.net_amount_vnd = (
                gross - item.discount_vnd - item.loyalty_discount_vnd
            )
        if sum(int(item.net_amount_vnd or 0) for item, _ in created) != total:
            raise fnb_error(409, "FNB_ORDER_TOTAL_MISMATCH", "Tổng chứng từ không khớp bill")
        if applied_voucher is not None:
            applied_voucher.usage_count = int(applied_voucher.usage_count or 0) + 1
        if request.payment_method == order_service.PAYMENT_METHOD_CASH and total > 0:
            db.add(
                models.OrderPayment(
                    order_id=order.id,
                    entry_type=order_service.ENTRY_CASH,
                    amount=total,
                    idempotency_key=f"fnb-cash:{check.id}",
                    created_by_user_id=current_user.id,
                    shift_id=shift.id if shift else None,
                    note=f"Thu tiền mặt F&B check #{check.id}",
                )
            )
        if paid_now:
            order_service._award_loyalty_paid_order(db, order, current_user.id)
        if request.payment_method == order_service.PAYMENT_METHOD_TRANSFER and total > 0:
            qr_sales_service.issue_intent_if_enabled(db, order, shop)
        check.order_id = order.id
        check.status = (
            "PAID"
            if status == order_service.STATUS_PAID
            else ("DEBT" if status == order_service.STATUS_DEBT else "PAYMENT_PENDING")
        )
        check.settled_at = datetime.datetime.utcnow() if check.status in ("PAID", "DEBT") else None
        check.revision = int(check.revision or 0) + 1
        _refresh_session_status(db, session)
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = {
            "shop_id": shop_id,
            "session_id": session.id,
            "session_status": session.status,
            "session_revision": int(session.revision or 0),
            "check": _serialize_check(db, check),
            "order": _order_checkout_result(db, shop, order),
        }
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def close_session(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbSessionClose,
) -> dict:
    action = "FNB_SESSION_CLOSE"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        session = _session_for_access(db, current_user, session_id, PERMISSION_FNB_CHECKOUT)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(db, current_user, session_id, PERMISSION_FNB_CHECKOUT)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_CHECKOUT)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if session.status not in _ACTIVE_SESSION_STATUSES:
            raise fnb_error(409, "FNB_SESSION_NOT_ACTIVE", "Phiên phục vụ không còn hoạt động")
        _sync_check_payment_statuses(db, session)
        require_session_revision(db, session, request.expected_revision)
        checks = (
            db.query(models.FnbServiceCheck)
            .filter(models.FnbServiceCheck.session_id == session.id)
            .all()
        )
        if not checks or any(check.status not in ("PAID", "DEBT", "CANCELLED") for check in checks):
            raise fnb_error(409, "FNB_UNSETTLED_CHECKS", "Vẫn còn bill chưa thanh toán")
        lines = db.query(models.FnbSessionLine).filter_by(session_id=session.id).all()
        if any(
            int(line.quantity) - int(line.sent_quantity or 0) - int(line.cancelled_quantity or 0)
            + int(line.sent_cancelled_quantity or 0) > 0
            for line in lines
        ):
            raise fnb_error(409, "FNB_UNSENT_LINES", "Vẫn còn món chưa gửi")
        if db.query(models.FnbStockAllocation).filter_by(
            session_id=session.id, state="CONSUMED"
        ).first() is not None:
            raise fnb_error(409, "FNB_ALLOCATION_UNSETTLED", "Vẫn còn tồn kho chưa gắn vào chứng từ")
        active_tickets = (
            db.query(models.FnbKitchenTicket)
            .filter(
                models.FnbKitchenTicket.session_id == session.id,
                models.FnbKitchenTicket.status.in_(("NEW", "IN_PROGRESS")),
            )
            .order_by(models.FnbKitchenTicket.id)
            .all()
        )
        if active_tickets:
            raise fnb_error(
                409,
                "FNB_ACTIVE_TICKETS",
                "Vẫn còn món đang chờ bếp/bar",
                tickets=[
                    {"id": row.id, "station": row.station, "status": row.status}
                    for row in active_tickets
                ],
            )
        ready_tickets = (
            db.query(models.FnbKitchenTicket)
            .filter(
                models.FnbKitchenTicket.session_id == session.id,
                models.FnbKitchenTicket.status == "DONE",
                models.FnbKitchenTicket.served_at.is_(None),
            )
            .order_by(models.FnbKitchenTicket.id)
            .all()
        )
        if ready_tickets:
            raise fnb_error(
                409,
                "FNB_UNSERVED_TICKETS",
                "Vẫn còn món sẵn sàng nhưng chưa giao",
                tickets=[
                    {"id": row.id, "station": row.station, "status": "READY"}
                    for row in ready_tickets
                ],
            )
        now = datetime.datetime.utcnow()
        before = serialize_session(db, session)
        for link in _active_links(db, session.id):
            link.released_at = now
            table = db.get(models.FnbTable, link.table_id)
            if table is not None:
                table.state_version = int(table.state_version or 0) + 1
                table.updated_at = now
        session.status = "CLOSED"
        session.closed_by_user_id = current_user.id
        session.closed_at = now
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = serialize_session(db, session)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def require_session_revision(
    db: Session, session: models.FnbServiceSession, expected_revision: int
) -> None:
    if int(session.revision or 0) != int(expected_revision):
        raise fnb_error(
            409,
            "FNB_SESSION_CHANGED",
            "Bàn vừa được cập nhật trên thiết bị khác",
            revision=int(session.revision or 0),
            snapshot=serialize_session(db, session),
        )


def _session_for_access(
    db: Session,
    current_user: models.User,
    session_id: int,
    permission: str = PERMISSION_FNB_SERVICE,
) -> models.FnbServiceSession:
    session = (
        db.query(models.FnbServiceSession)
        .filter(models.FnbServiceSession.id == session_id)
        .first()
    )
    shop = db.get(models.Shop, session.shop_id) if session is not None else None
    if session is None or shop is None or not has_shop_operator_access(shop, current_user):
        raise fnb_error(404, "FNB_SESSION_NOT_FOUND", "Không tìm thấy phiên phục vụ")
    require_staff_permission(current_user, permission)
    return session


def _line_for_access(
    db: Session, current_user: models.User, line_id: int
) -> tuple[models.FnbSessionLine, models.FnbServiceSession]:
    line = db.get(models.FnbSessionLine, line_id)
    session = db.get(models.FnbServiceSession, line.session_id) if line else None
    shop = db.get(models.Shop, session.shop_id) if session else None
    if line is None or shop is None or not has_shop_operator_access(shop, current_user):
        raise fnb_error(404, "FNB_LINE_NOT_FOUND", "Không tìm thấy món")
    require_staff_permission(current_user, PERMISSION_FNB_SERVICE)
    return line, session


def _require_open(session: models.FnbServiceSession) -> None:
    if session.status != "OPEN":
        raise fnb_error(409, "FNB_SESSION_NOT_OPEN", "Phiên phục vụ không còn mở")


def _require_service_mutable(session: models.FnbServiceSession) -> None:
    if session.status not in ("OPEN", "PARTIALLY_SETTLED"):
        raise fnb_error(
            409,
            "FNB_SESSION_NOT_SERVICEABLE",
            "Phiên bàn không còn nhận thay đổi phục vụ",
        )


def _require_table_version(
    table: models.FnbTable, expected: int, code: str = "FNB_TABLE_CHANGED"
) -> None:
    if int(table.state_version or 0) != int(expected):
        raise fnb_error(
            409,
            code,
            "Bàn vừa được cập nhật",
            state_version=int(table.state_version or 0),
        )


def _normalize_note(note: str | None) -> str | None:
    normalized = " ".join(note.strip().split()) if note else ""
    return normalized or None


def _session_result_finish(
    db: Session,
    current_user: models.User,
    session: models.FnbServiceSession,
    action: str,
    operation_id: str,
    fingerprint: str,
    *,
    before=None,
    reason: str | None = None,
) -> dict:
    db.flush()
    result = serialize_session(db, session)
    return _finish(
        db,
        current_user,
        action,
        operation_id,
        fingerprint,
        result,
        session_id=session.id,
        before=before,
        after=result,
        reason=reason,
    )


def open_session(
    db: Session, current_user: models.User, request: FnbSessionOpen
) -> dict:
    action = "FNB_SESSION_OPEN"
    fingerprint = operation_fingerprint(action, _payload(request))
    try:
        require_fnb_access(db, request.shop_id, current_user, PERMISSION_FNB_SERVICE)
        _prepare_locked_shop(db, request.shop_id)
        shop = require_fnb_access(
            db, request.shop_id, current_user, PERMISSION_FNB_SERVICE
        )
        existing = _existing_operation(
            db, request.shop_id, request.operation_id, fingerprint
        )
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        require_floor_revision(shop, request.expected_revision)
        table = (
            db.query(models.FnbTable)
            .filter(
                models.FnbTable.id == request.table_id,
                models.FnbTable.shop_id == request.shop_id,
            )
            .first()
        )
        if table is None:
            raise fnb_error(404, "FNB_TABLE_NOT_FOUND", "Không tìm thấy bàn")
        area = db.get(models.FnbArea, table.area_id)
        if not bool(table.active) or area is None or not bool(area.active):
            raise fnb_error(409, "FNB_TABLE_INACTIVE", "Bàn đã ngừng hoạt động")
        _require_table_version(table, request.expected_table_version)
        if _table_has_active_session(db, table.id):
            raise fnb_error(409, "FNB_TABLE_OCCUPIED", "Bàn đang phục vụ")
        session = models.FnbServiceSession(
            shop_id=shop.id,
            status="OPEN",
            revision=0,
            opened_by_user_id=current_user.id,
        )
        db.add(session)
        db.flush()
        _primary_check(db, session, create=True)
        db.add(models.FnbSessionTable(session_id=session.id, table_id=table.id))
        table.state_version = int(table.state_version or 0) + 1
        table.updated_at = datetime.datetime.utcnow()
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db, current_user, session, action, request.operation_id, fingerprint
        )
    except Exception:
        db.rollback()
        raise


def get_session(
    db: Session, current_user: models.User, session_id: int
) -> dict:
    session = _session_for_access(db, current_user, session_id)
    require_fnb_shop(db, session.shop_id, current_user)
    return serialize_session(db, session)


def add_line(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbLineCreate,
) -> dict:
    action = "FNB_LINE_ADD"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        session = _session_for_access(db, current_user, session_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(db, current_user, session_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(session)
        require_session_revision(db, session, request.expected_revision)
        product = (
            db.query(models.Product)
            .filter(
                models.Product.id == request.product_id,
                models.Product.shop_id == shop_id,
            )
            .first()
        )
        if product is None:
            raise fnb_error(404, "FNB_PRODUCT_NOT_FOUND", "Không tìm thấy món")
        if not bool(product.is_active):
            raise fnb_error(409, "FNB_PRODUCT_INACTIVE", "Món đã ngừng bán")
        try:
            price = checked_vnd(product.price)
        except ValueError as exc:
            raise fnb_error(400, "FNB_INVALID_PRICE", "Giá món không hợp lệ") from exc
        before = serialize_session(db, session)
        db.add(
            models.FnbSessionLine(
                session_id=session.id,
                product_id=product.id,
                product_name=product.name,
                unit_price_vnd=price,
                station=product.fnb_station,
                note=_normalize_note(request.note),
                quantity=request.quantity,
                cancelled_quantity=0,
                created_by_user_id=current_user.id,
            )
        )
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db,
            current_user,
            session,
            action,
            request.operation_id,
            fingerprint,
            before=before,
        )
    except Exception:
        db.rollback()
        raise


def _ticket_result(db: Session, ticket: models.FnbKitchenTicket) -> dict:
    session = db.get(models.FnbServiceSession, ticket.session_id)
    items = (
        db.query(models.FnbKitchenTicketItem)
        .filter(models.FnbKitchenTicketItem.ticket_id == ticket.id)
        .order_by(models.FnbKitchenTicketItem.id)
        .all()
    )
    tables = [
        row[0]
        for row in db.query(models.FnbTable.name)
        .join(models.FnbSessionTable, models.FnbSessionTable.table_id == models.FnbTable.id)
        .filter(
            models.FnbSessionTable.session_id == ticket.session_id,
            models.FnbSessionTable.released_at.is_(None),
        )
        .order_by(models.FnbSessionTable.id)
        .all()
    ]
    service_stage = ticket.status
    if ticket.served_at is not None:
        service_stage = "SERVED"
    elif ticket.status == "DONE" and not ticket.out_of_stock_reason:
        service_stage = "READY"
    return {
        "id": ticket.id,
        "shop_id": ticket.shop_id,
        "session_id": ticket.session_id,
        "session_revision": int(session.revision or 0) if session else 0,
        "station": ticket.station,
        "sequence": int(ticket.sequence),
        "status": ticket.status,
        "service_stage": service_stage,
        "state_version": int(ticket.state_version or 0),
        "out_of_stock_reason": ticket.out_of_stock_reason,
        "served_by_user_id": ticket.served_by_user_id,
        "served_at": ticket.served_at.isoformat() + "Z" if ticket.served_at else None,
        "created_at": ticket.created_at.isoformat() + "Z",
        "tables": tables,
        "items": [
            {
                "id": item.id,
                "line_id": item.session_line_id,
                "product_name": item.product_name,
                "quantity": int(item.quantity) - int(item.cancelled_quantity or 0),
                "original_quantity": int(item.quantity),
                "cancelled_quantity": int(item.cancelled_quantity or 0),
                "note": item.note,
            }
            for item in items
            if int(item.quantity) > int(item.cancelled_quantity or 0)
        ],
    }


def send_session(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbSessionSend,
) -> dict:
    action = "FNB_SESSION_SEND"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        session = _session_for_access(db, current_user, session_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(db, current_user, session_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(session)
        require_session_revision(db, session, request.expected_revision)
        lines = (
            db.query(models.FnbSessionLine)
            .filter(models.FnbSessionLine.session_id == session.id)
            .order_by(models.FnbSessionLine.id)
            .all()
        )
        pending = []
        wanted: dict[int, int] = {}
        products: dict[int, models.Product] = {}
        for line in lines:
            quantity = (
                int(line.quantity)
                - int(line.sent_quantity or 0)
                - int(line.cancelled_quantity or 0)
                + int(line.sent_cancelled_quantity or 0)
            )
            if quantity <= 0:
                continue
            product = (
                db.query(models.Product)
                .filter(
                    models.Product.id == line.product_id,
                    models.Product.shop_id == shop_id,
                )
                .first()
            )
            if product is None:
                raise fnb_error(409, "FNB_PRODUCT_MISSING", "Món nguồn không còn tồn tại")
            pending.append((line, product, quantity))
            products[product.id] = product
            wanted[product.id] = wanted.get(product.id, 0) + quantity
        if not pending:
            raise fnb_error(409, "FNB_NOTHING_TO_SEND", "Không có món mới để gửi")
        for product_id, quantity in wanted.items():
            available = inventory_service.ton_kha_dung(db, products[product_id])
            if available < quantity:
                raise fnb_error(
                    409,
                    "FNB_STOCK_SHORTAGE",
                    "Món không đủ tồn kho để gửi",
                    product_id=product_id,
                    product_name=products[product_id].name,
                    requested=quantity,
                    available=available,
                )

        before = serialize_session(db, session)
        ticket_by_station: dict[str, models.FnbKitchenTicket] = {}
        for station in sorted({line.station for line, _, _ in pending} & {"KITCHEN", "BAR"}):
            sequence = int(
                db.query(func.max(models.FnbKitchenTicket.sequence))
                .filter(
                    models.FnbKitchenTicket.shop_id == shop_id,
                    models.FnbKitchenTicket.station == station,
                )
                .scalar()
                or 0
            ) + 1
            ticket = models.FnbKitchenTicket(
                shop_id=shop_id,
                session_id=session.id,
                station=station,
                sequence=sequence,
                status="NEW",
                operation_id=request.operation_id,
                created_by_user_id=current_user.id,
            )
            db.add(ticket)
            db.flush()
            ticket_by_station[station] = ticket

        for line, product, quantity in pending:
            ticket_item = None
            ticket = ticket_by_station.get(line.station)
            if ticket is not None:
                ticket_item = models.FnbKitchenTicketItem(
                    ticket_id=ticket.id,
                    session_line_id=line.id,
                    quantity=quantity,
                    product_name=line.product_name,
                    note=line.note,
                )
                db.add(ticket_item)
                db.flush()
            allocations = inventory_service.deduct_stock(db, [(product, quantity)])[product.id]
            for allocation in allocations:
                db.add(
                    models.FnbStockAllocation(
                        shop_id=shop_id,
                        session_id=session.id,
                        session_line_id=line.id,
                        ticket_item_id=ticket_item.id if ticket_item else None,
                        product_id=product.id,
                        batch_id=allocation.batch.id if allocation.batch else None,
                        quantity=allocation.quantity,
                        cost_known_qty=allocation.known_qty,
                        cost_unknown_qty=allocation.unknown_qty,
                        cost_basis_vnd=allocation.cost_basis_vnd,
                        state="CONSUMED",
                        operation_id=request.operation_id,
                    )
                )
            line.sent_quantity = int(line.sent_quantity or 0) + quantity
            line.state_version = int(line.state_version or 0) + 1

            primary = _ordering_check(db, session)
            check_line = (
                db.query(models.FnbCheckLine)
                .filter(
                    models.FnbCheckLine.check_id == primary.id,
                    models.FnbCheckLine.session_line_id == line.id,
                )
                .first()
            )
            if check_line is None:
                db.add(
                    models.FnbCheckLine(
                        check_id=primary.id,
                        session_line_id=line.id,
                        quantity=quantity,
                    )
                )
            else:
                check_line.quantity = int(check_line.quantity) + quantity

        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = serialize_session(db, session)
        result["tickets"] = [
            _ticket_result(db, ticket) for ticket in ticket_by_station.values()
        ]
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            before=before,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def get_station_tickets(
    db: Session,
    current_user: models.User,
    shop_id: int,
    station: str,
    after_revision: int | None = None,
) -> dict:
    station = station.upper()
    permission = {
        "KITCHEN": PERMISSION_FNB_KITCHEN,
        "BAR": PERMISSION_FNB_BAR,
    }.get(station)
    if permission is None:
        raise fnb_error(400, "FNB_STATION_INVALID", "Khu chế biến không hợp lệ")
    shop = require_fnb_access(db, shop_id, current_user, permission)
    if not bool(shop.fnb_enabled):
        raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
    revision = int(shop.fnb_revision or 0)
    if after_revision is not None and int(after_revision) == revision:
        return {"changed": False, "station": station, "revision": revision}
    tickets = (
        db.query(models.FnbKitchenTicket)
        .join(
            models.FnbServiceSession,
            models.FnbServiceSession.id == models.FnbKitchenTicket.session_id,
        )
        .join(
            models.FnbKitchenTicketItem,
            models.FnbKitchenTicketItem.ticket_id == models.FnbKitchenTicket.id,
        )
        .filter(
            models.FnbKitchenTicket.shop_id == shop_id,
            models.FnbKitchenTicket.station == station,
            models.FnbKitchenTicket.status.in_(("NEW", "IN_PROGRESS")),
            models.FnbServiceSession.status.in_(_ACTIVE_SESSION_STATUSES),
            models.FnbKitchenTicketItem.quantity
            > func.coalesce(models.FnbKitchenTicketItem.cancelled_quantity, 0),
        )
        .distinct()
        .order_by(models.FnbKitchenTicket.sequence)
        .all()
    )
    return {
        "changed": True,
        "shop_id": shop_id,
        "station": station,
        "revision": revision,
        "tickets": [_ticket_result(db, ticket) for ticket in tickets],
    }


def _ticket_session_for_mutation(
    db: Session, ticket: models.FnbKitchenTicket
) -> models.FnbServiceSession:
    session = db.get(models.FnbServiceSession, ticket.session_id)
    if session is None:
        raise fnb_error(404, "FNB_SESSION_NOT_FOUND", "Không tìm thấy phiên phục vụ")
    if session.status not in _ACTIVE_SESSION_STATUSES:
        raise fnb_error(409, "FNB_SESSION_NOT_ACTIVE", "Phiên phục vụ đã kết thúc")
    active_item = (
        db.query(models.FnbKitchenTicketItem.id)
        .filter(
            models.FnbKitchenTicketItem.ticket_id == ticket.id,
            models.FnbKitchenTicketItem.quantity
            > func.coalesce(models.FnbKitchenTicketItem.cancelled_quantity, 0),
        )
        .first()
    )
    if active_item is None:
        raise fnb_error(409, "FNB_TICKET_EMPTY", "Phiếu không còn món cần xử lý")
    return session


def transition_ticket(
    db: Session,
    current_user: models.User,
    ticket_id: int,
    transition: str,
    request: FnbTicketTransition,
) -> dict:
    transitions = {
        "start": ("FNB_TICKET_START", "NEW", "IN_PROGRESS"),
        "done": ("FNB_TICKET_DONE", "IN_PROGRESS", "DONE"),
        "out-of-stock": ("FNB_TICKET_OUT_OF_STOCK", None, None),
        "resume": ("FNB_TICKET_RESUME", None, None),
    }
    if transition not in transitions:
        raise fnb_error(400, "FNB_TICKET_ACTION_INVALID", "Thao tác phiếu không hợp lệ")
    action, required_status, next_status = transitions[transition]
    fingerprint = operation_fingerprint(
        action, _payload(request, ticket_id=ticket_id)
    )
    try:
        ticket = db.get(models.FnbKitchenTicket, ticket_id)
        if ticket is None:
            raise fnb_error(404, "FNB_TICKET_NOT_FOUND", "Không tìm thấy phiếu")
        shop_id = int(ticket.shop_id)
        permission = (
            PERMISSION_FNB_KITCHEN
            if ticket.station == "KITCHEN"
            else PERMISSION_FNB_BAR
        )
        require_fnb_access(db, shop_id, current_user, permission)
        _prepare_locked_shop(db, shop_id)
        shop = require_fnb_access(db, shop_id, current_user, permission)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        ticket = (
            db.query(models.FnbKitchenTicket)
            .filter(
                models.FnbKitchenTicket.id == ticket_id,
                models.FnbKitchenTicket.shop_id == shop_id,
            )
            .first()
        )
        if ticket is None:
            raise fnb_error(404, "FNB_TICKET_NOT_FOUND", "Không tìm thấy phiếu")
        session = _ticket_session_for_mutation(db, ticket)
        if int(ticket.state_version or 0) != request.expected_state_version:
            raise fnb_error(
                409,
                "FNB_TICKET_CHANGED",
                "Phiếu vừa được cập nhật",
                state_version=int(ticket.state_version or 0),
                snapshot=_ticket_result(db, ticket),
            )
        require_session_revision(db, session, request.expected_session_revision)
        if transition == "out-of-stock":
            reason = _normalize_note(request.reason)
            if not reason:
                raise fnb_error(400, "FNB_REASON_REQUIRED", "Cần nhập lý do hết món")
            if ticket.status not in ("NEW", "IN_PROGRESS"):
                raise fnb_error(409, "FNB_TICKET_CLOSED", "Phiếu đã hoàn tất")
            ticket.out_of_stock_reason = reason
        elif transition == "resume":
            if request.reason is not None:
                raise fnb_error(400, "FNB_REASON_NOT_ALLOWED", "Thao tác này không cần lý do")
            if ticket.status not in ("NEW", "IN_PROGRESS"):
                raise fnb_error(409, "FNB_TICKET_CLOSED", "Phiếu đã hoàn tất")
            if not ticket.out_of_stock_reason:
                raise fnb_error(409, "FNB_TICKET_NOT_OUT_OF_STOCK", "Phiếu không báo hết món")
            ticket.out_of_stock_reason = None
        else:
            if request.reason is not None:
                raise fnb_error(400, "FNB_REASON_NOT_ALLOWED", "Thao tác này không cần lý do")
            if ticket.status != required_status:
                raise fnb_error(409, "FNB_TICKET_STATE_INVALID", "Trạng thái phiếu không phù hợp")
            if transition == "done" and ticket.out_of_stock_reason:
                raise fnb_error(
                    409,
                    "FNB_TICKET_OUT_OF_STOCK",
                    "Cần tiếp tục chế biến trước khi báo sẵn sàng",
                    snapshot=_ticket_result(db, ticket),
                )
            ticket.status = next_status
            now = datetime.datetime.utcnow()
            if transition == "start":
                ticket.started_by_user_id = current_user.id
                ticket.started_at = now
            else:
                ticket.done_by_user_id = current_user.id
                ticket.done_at = now
        ticket.state_version = int(ticket.state_version or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _ticket_result(db, ticket)
        result["revision"] = int(shop.fnb_revision or 0)
        result["session_revision"] = int(session.revision or 0)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=ticket.session_id,
            after=result,
            reason=ticket.out_of_stock_reason if transition == "out-of-stock" else None,
        )
    except Exception:
        db.rollback()
        raise


def serve_ticket(
    db: Session,
    current_user: models.User,
    ticket_id: int,
    request: FnbTicketTransition,
) -> dict:
    action = "FNB_TICKET_SERVE"
    fingerprint = operation_fingerprint(action, _payload(request, ticket_id=ticket_id))
    try:
        ticket = db.get(models.FnbKitchenTicket, ticket_id)
        if ticket is None:
            raise fnb_error(404, "FNB_TICKET_NOT_FOUND", "Không tìm thấy phiếu")
        shop_id = int(ticket.shop_id)
        require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        _prepare_locked_shop(db, shop_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        ticket = db.query(models.FnbKitchenTicket).filter_by(
            id=ticket_id, shop_id=shop_id
        ).first()
        if ticket is None:
            raise fnb_error(404, "FNB_TICKET_NOT_FOUND", "Không tìm thấy phiếu")
        session = _ticket_session_for_mutation(db, ticket)
        if int(ticket.state_version or 0) != request.expected_state_version:
            raise fnb_error(
                409,
                "FNB_TICKET_CHANGED",
                "Phiếu vừa được cập nhật",
                state_version=int(ticket.state_version or 0),
                snapshot=_ticket_result(db, ticket),
            )
        require_session_revision(db, session, request.expected_session_revision)
        if request.reason is not None:
            raise fnb_error(400, "FNB_REASON_NOT_ALLOWED", "Thao tác này không cần lý do")
        if ticket.out_of_stock_reason:
            raise fnb_error(
                409,
                "FNB_TICKET_OUT_OF_STOCK",
                "Cần tiếp tục chế biến trước khi giao món",
                snapshot=_ticket_result(db, ticket),
            )
        if ticket.status != "DONE" or ticket.served_at is not None:
            raise fnb_error(
                409,
                "FNB_TICKET_NOT_READY",
                "Món chưa ở trạng thái sẵn sàng giao",
                snapshot=_ticket_result(db, ticket),
            )
        now = datetime.datetime.utcnow()
        ticket.served_by_user_id = current_user.id
        ticket.served_at = now
        ticket.state_version = int(ticket.state_version or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = _ticket_result(db, ticket)
        result["revision"] = int(shop.fnb_revision or 0)
        result["session_revision"] = int(session.revision or 0)
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=ticket.session_id,
            after=result,
        )
    except Exception:
        db.rollback()
        raise


def update_line(
    db: Session,
    current_user: models.User,
    line_id: int,
    request: FnbLineUpdate,
) -> dict:
    action = "FNB_LINE_UPDATE"
    fingerprint = operation_fingerprint(action, _payload(request, line_id=line_id))
    try:
        _, session = _line_for_access(db, current_user, line_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        line, session = _line_for_access(db, current_user, line_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(session)
        require_session_revision(db, session, request.expected_revision)
        if int(line.state_version or 0) != request.expected_line_version:
            raise fnb_error(
                409,
                "FNB_LINE_CHANGED",
                "Món vừa được cập nhật",
                state_version=int(line.state_version or 0),
                snapshot=serialize_session(db, session),
            )
        if int(line.sent_quantity or 0) > 0:
            raise fnb_error(409, "FNB_LINE_ALREADY_SENT", "Món đã gửi không thể sửa")
        if request.quantity < int(line.cancelled_quantity or 0):
            raise fnb_error(
                409, "FNB_QUANTITY_CANCELLED", "Số lượng thấp hơn phần đã hủy"
            )
        before = serialize_session(db, session)
        line.quantity = request.quantity
        line.note = _normalize_note(request.note)
        line.state_version = int(line.state_version or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db,
            current_user,
            session,
            action,
            request.operation_id,
            fingerprint,
            before=before,
        )
    except Exception:
        db.rollback()
        raise


def _sent_allocation_parts(
    db: Session, line_id: int, quantity: int
) -> list[tuple[models.FnbStockAllocation, int]]:
    rows = (
        db.query(models.FnbStockAllocation)
        .filter(
            models.FnbStockAllocation.session_line_id == line_id,
            models.FnbStockAllocation.state == "CONSUMED",
        )
        .order_by(models.FnbStockAllocation.id)
        .all()
    )
    parts = []
    remaining = quantity
    for row in rows:
        take = min(remaining, int(row.quantity))
        if take:
            parts.append((row, take))
            remaining -= take
        if not remaining:
            break
    if remaining:
        raise fnb_error(409, "FNB_ALLOCATION_MISMATCH", "Phân bổ tồn món bị lệch")
    return parts


def _approval_for_sent_cancel(
    db: Session,
    current_user: models.User,
    session: models.FnbServiceSession,
    token: str | None,
) -> models.FnbManagerApproval:
    if not token:
        raise fnb_error(403, "FNB_APPROVAL_REQUIRED", "Cần PIN quản lý để hủy món đang làm")
    try:
        return approval_service.consume_approval(
            db,
            token=token,
            shop_id=session.shop_id,
            actor_user_id=current_user.id,
            action="CANCEL_SENT_LINE",
            entity_type="SESSION",
            entity_id=session.id,
            revision=session.revision,
        )
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "APPROVAL_INVALID":
            raise fnb_error(
                exc.status_code,
                "FNB_APPROVAL_INVALID",
                "Lượt duyệt không còn hợp lệ",
            ) from exc
        raise


def _resolve_sent_allocations(
    db: Session,
    parts: list[tuple[models.FnbStockAllocation, int]],
    resolution: str,
    reason: str | None,
    operation_id: str,
) -> None:
    now = datetime.datetime.utcnow()
    allocation_state = "RESTOCKED" if resolution == "RESTOCK" else resolution
    for row, take in parts:
        unknown_take = min(take, int(row.cost_unknown_qty or 0))
        known_take = take - unknown_take
        basis_take = (
            cumulative_basis(
                int(row.cost_basis_vnd or 0),
                int(row.cost_known_qty or 0),
                known_take,
            )
            if known_take
            else 0
        )
        terminal = row
        if take < int(row.quantity):
            row.quantity = int(row.quantity) - take
            row.cost_known_qty = int(row.cost_known_qty or 0) - known_take
            row.cost_unknown_qty = int(row.cost_unknown_qty or 0) - unknown_take
            row.cost_basis_vnd = int(row.cost_basis_vnd or 0) - basis_take
            terminal = models.FnbStockAllocation(
                shop_id=row.shop_id,
                session_id=row.session_id,
                session_line_id=row.session_line_id,
                ticket_item_id=row.ticket_item_id,
                product_id=row.product_id,
                batch_id=row.batch_id,
                quantity=take,
                cost_known_qty=known_take,
                cost_unknown_qty=unknown_take,
                cost_basis_vnd=basis_take,
                operation_id=operation_id,
            )
            db.add(terminal)
        terminal.state = allocation_state
        terminal.resolved_at = now
        terminal.resolution_reason = reason
        if row.ticket_item_id is not None:
            item = db.get(models.FnbKitchenTicketItem, row.ticket_item_id)
            item.cancelled_quantity = int(item.cancelled_quantity or 0) + take
        if resolution != "RESTOCK":
            continue
        product = db.get(models.Product, row.product_id)
        if product is None or product.shop_id != row.shop_id:
            raise fnb_error(409, "FNB_PRODUCT_MISSING", "Món nguồn không còn tồn tại")
        if int(product.stock or 0) > MAX_SAFE_QUANTITY - take:
            raise fnb_error(409, "FNB_STOCK_OVERFLOW", "Tồn kho sau hoàn vượt giới hạn")
        source = product
        if row.batch_id is not None:
            source = db.get(models.ProductBatch, row.batch_id)
            if source is None or source.product_id != product.id:
                raise fnb_error(409, "FNB_BATCH_MISSING", "Lô nguồn không còn tồn tại")
            if int(source.quantity or 0) > MAX_SAFE_QUANTITY - take:
                raise fnb_error(409, "FNB_STOCK_OVERFLOW", "Tồn lô sau hoàn vượt giới hạn")
        inventory_service.restore_cost_pool(source, known_take, unknown_take, basis_take)
        if row.batch_id is not None:
            source.quantity = int(source.quantity or 0) + take
        product.stock = int(product.stock or 0) + take


def _cancel_empty_tickets(db: Session, ticket_ids: set[int]) -> None:
    if not ticket_ids:
        return
    db.flush()
    active_item = (
        db.query(models.FnbKitchenTicketItem.id)
        .filter(
            models.FnbKitchenTicketItem.ticket_id == models.FnbKitchenTicket.id,
            models.FnbKitchenTicketItem.quantity
            > func.coalesce(models.FnbKitchenTicketItem.cancelled_quantity, 0),
        )
        .correlate(models.FnbKitchenTicket)
        .exists()
    )
    tickets = (
        db.query(models.FnbKitchenTicket)
        .filter(
            models.FnbKitchenTicket.id.in_(ticket_ids),
            models.FnbKitchenTicket.served_at.is_(None),
            models.FnbKitchenTicket.status.in_(("NEW", "IN_PROGRESS", "DONE")),
            ~active_item,
        )
        .all()
    )
    for ticket in tickets:
        ticket.status = "CANCELLED"
        ticket.out_of_stock_reason = None
        ticket.state_version = int(ticket.state_version or 0) + 1


def cancel_line(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbLineCancel,
) -> dict:
    action = "FNB_LINE_CANCEL"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        session = _session_for_access(db, current_user, session_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(db, current_user, session_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(session)
        require_session_revision(db, session, request.expected_revision)
        line = (
            db.query(models.FnbSessionLine)
            .filter(
                models.FnbSessionLine.id == request.line_id,
                models.FnbSessionLine.session_id == session.id,
            )
            .first()
        )
        if line is None:
            raise fnb_error(404, "FNB_LINE_NOT_FOUND", "Không tìm thấy món")
        if int(line.state_version or 0) != request.expected_line_version:
            raise fnb_error(
                409,
                "FNB_LINE_CHANGED",
                "Món vừa được cập nhật",
                state_version=int(line.state_version or 0),
                snapshot=serialize_session(db, session),
            )
        billable = int(line.quantity) - int(line.cancelled_quantity or 0)
        if request.quantity > billable:
            raise fnb_error(
                409, "FNB_CANCEL_EXCEEDS_QUANTITY", "Số lượng hủy vượt phần còn lại"
            )
        before = serialize_session(db, session)
        unsent = (
            int(line.quantity)
            - int(line.sent_quantity or 0)
            - int(line.cancelled_quantity or 0)
            + int(line.sent_cancelled_quantity or 0)
        )
        sent_to_cancel = max(0, int(request.quantity) - max(0, unsent))
        approval = None
        reason = _normalize_note(request.reason)
        ticket_ids: set[int] = set()
        if sent_to_cancel:
            parts = _sent_allocation_parts(db, line.id, sent_to_cancel)
            item_ids = [row.ticket_item_id for row, _ in parts if row.ticket_item_id]
            if item_ids:
                ticket_ids = {
                    row[0]
                    for row in db.query(models.FnbKitchenTicketItem.ticket_id)
                    .filter(models.FnbKitchenTicketItem.id.in_(item_ids))
                    .all()
                }
            progressed = False
            if item_ids:
                progressed = (
                    db.query(models.FnbKitchenTicket.id)
                    .join(
                        models.FnbKitchenTicketItem,
                        models.FnbKitchenTicketItem.ticket_id == models.FnbKitchenTicket.id,
                    )
                    .filter(
                        models.FnbKitchenTicketItem.id.in_(item_ids),
                        models.FnbKitchenTicket.status.in_(("IN_PROGRESS", "DONE")),
                    )
                    .first()
                    is not None
                )
            if progressed:
                if request.resolution not in ("RESTOCK", "WASTE") or not reason:
                    raise fnb_error(
                        400,
                        "FNB_CANCELLATION_DECISION_REQUIRED",
                        "Cần chọn hoàn tồn hoặc hao hụt và nhập lý do",
                    )
                served = (
                    db.query(models.FnbKitchenTicket.id)
                    .join(
                        models.FnbKitchenTicketItem,
                        models.FnbKitchenTicketItem.ticket_id == models.FnbKitchenTicket.id,
                    )
                    .filter(
                        models.FnbKitchenTicketItem.id.in_(item_ids),
                        models.FnbKitchenTicket.served_at.is_not(None),
                    )
                    .first()
                    is not None
                )
                if served and request.resolution == "RESTOCK":
                    raise fnb_error(
                        409,
                        "FNB_SERVED_RESTOCK_FORBIDDEN",
                        "Món đã giao không thể hoàn lại tồn kho",
                    )
                approval = _approval_for_sent_cancel(
                    db, current_user, session, request.approval_token
                )
                resolution = request.resolution
            else:
                resolution = "RESTOCK"
            _resolve_sent_allocations(
                db, parts, resolution, reason, request.operation_id
            )
            _cancel_empty_tickets(db, ticket_ids)
        line.cancelled_quantity = int(line.cancelled_quantity or 0) + request.quantity
        line.sent_cancelled_quantity = (
            int(line.sent_cancelled_quantity or 0) + sent_to_cancel
        )
        if sent_to_cancel:
            _remove_cancelled_from_checks(db, session.id, line.id, sent_to_cancel)
        line.state_version = int(line.state_version or 0) + 1
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        db.flush()
        result = serialize_session(db, session)
        if approval is not None:
            result["manager_approval_id"] = approval.id
        return _finish(
            db,
            current_user,
            action,
            request.operation_id,
            fingerprint,
            result,
            session_id=session.id,
            before=before,
            after=result,
            reason=reason,
        )
    except Exception:
        db.rollback()
        raise


def move_table(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbMoveTable,
) -> dict:
    action = "FNB_TABLE_MOVE"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    move_permission = (
        PERMISSION_FNB_SERVICE
        if effective_staff_role(current_user) == "SERVICE"
        else PERMISSION_FNB_MANAGE
    )
    try:
        session = _session_for_access(
            db, current_user, session_id, move_permission
        )
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(
            db, current_user, session_id, move_permission
        )
        shop = require_fnb_access(db, shop_id, current_user, move_permission)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(session)
        require_session_revision(db, session, request.expected_revision)
        source_link = (
            db.query(models.FnbSessionTable)
            .filter(
                models.FnbSessionTable.session_id == session.id,
                models.FnbSessionTable.table_id == request.from_table_id,
                models.FnbSessionTable.released_at.is_(None),
            )
            .first()
        )
        if source_link is None:
            raise fnb_error(
                409, "FNB_TABLE_NOT_ATTACHED", "Bàn nguồn không thuộc phiên phục vụ"
            )
        source = db.get(models.FnbTable, request.from_table_id)
        target = (
            db.query(models.FnbTable)
            .filter(
                models.FnbTable.id == request.to_table_id,
                models.FnbTable.shop_id == shop_id,
            )
            .first()
        )
        if target is None:
            raise fnb_error(404, "FNB_TABLE_NOT_FOUND", "Không tìm thấy bàn")
        area = db.get(models.FnbArea, target.area_id)
        if not bool(target.active) or area is None or not bool(area.active):
            raise fnb_error(409, "FNB_TABLE_INACTIVE", "Bàn đã ngừng hoạt động")
        _require_table_version(source, request.expected_from_state_version)
        _require_table_version(target, request.expected_to_state_version)
        if _table_has_active_session(db, target.id):
            raise fnb_error(409, "FNB_TABLE_OCCUPIED", "Bàn đang phục vụ")
        before = serialize_session(db, session)
        now = datetime.datetime.utcnow()
        source_link.released_at = now
        db.add(models.FnbSessionTable(session_id=session.id, table_id=target.id))
        for table in (source, target):
            table.state_version = int(table.state_version or 0) + 1
            table.updated_at = now
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db,
            current_user,
            session,
            action,
            request.operation_id,
            fingerprint,
            before=before,
        )
    except Exception:
        db.rollback()
        raise


def session_has_only_r1a_drafts(
    db: Session, session: models.FnbServiceSession
) -> bool:
    lines = db.query(models.FnbSessionLine).filter_by(session_id=session.id).all()
    if any(int(row.sent_quantity or 0) or int(row.sent_cancelled_quantity or 0) for row in lines):
        return False
    if db.query(models.FnbKitchenTicket).filter_by(session_id=session.id).first() is not None:
        return False
    if db.query(models.FnbStockAllocation).filter_by(session_id=session.id).first() is not None:
        return False
    checks = db.query(models.FnbServiceCheck).filter_by(session_id=session.id).all()
    if any(
        row.status != "OPEN"
        or row.order_id is not None
        or int(row.subtotal_vnd or 0)
        or int(row.discount_vnd or 0)
        or int(row.service_charge_vnd or 0)
        or int(row.total_vnd or 0)
        for row in checks
    ):
        return False
    check_ids = [row.id for row in checks]
    return not check_ids or db.query(models.FnbCheckLine).filter(
        models.FnbCheckLine.check_id.in_(check_ids)
    ).first() is None


def merge_table(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbMergeTable,
) -> dict:
    action = "FNB_TABLE_MERGE"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        source = _session_for_access(
            db, current_user, session_id, PERMISSION_FNB_MANAGE
        )
        shop_id = int(source.shop_id)
        _prepare_locked_shop(db, shop_id)
        source = _session_for_access(
            db, current_user, session_id, PERMISSION_FNB_MANAGE
        )
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_MANAGE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_service_mutable(source)
        require_session_revision(db, source, request.expected_revision)
        target_table = (
            db.query(models.FnbTable)
            .filter(
                models.FnbTable.id == request.target_table_id,
                models.FnbTable.shop_id == shop_id,
            )
            .first()
        )
        if target_table is None:
            raise fnb_error(404, "FNB_TABLE_NOT_FOUND", "Không tìm thấy bàn")
        area = db.get(models.FnbArea, target_table.area_id)
        if not bool(target_table.active) or area is None or not bool(area.active):
            raise fnb_error(409, "FNB_TABLE_INACTIVE", "Bàn đã ngừng hoạt động")
        _require_table_version(target_table, request.expected_target_table_version)
        target_link = (
            db.query(models.FnbSessionTable)
            .filter(
                models.FnbSessionTable.table_id == target_table.id,
                models.FnbSessionTable.released_at.is_(None),
            )
            .first()
        )
        if target_link is not None and target_link.session_id == source.id:
            raise fnb_error(
                409, "FNB_TABLE_ALREADY_ATTACHED", "Bàn đã thuộc phiên phục vụ này"
            )
        before = serialize_session(db, source)
        now = datetime.datetime.utcnow()
        if target_link is None:
            db.add(models.FnbSessionTable(session_id=source.id, table_id=target_table.id))
            target_table.state_version = int(target_table.state_version or 0) + 1
            target_table.updated_at = now
        else:
            target = db.get(models.FnbServiceSession, target_link.session_id)
            if target is None or target.shop_id != shop_id:
                raise fnb_error(404, "FNB_SESSION_NOT_FOUND", "Không tìm thấy phiên phục vụ")
            _require_open(target)
            if request.expected_target_session_revision is None:
                raise fnb_error(
                    409,
                    "FNB_TARGET_REVISION_REQUIRED",
                    "Cần phiên bản mới nhất của bàn đích",
                )
            require_session_revision(
                db, target, request.expected_target_session_revision
            )
            if not session_has_only_r1a_drafts(db, target):
                raise fnb_error(
                    409,
                    "FNB_TARGET_SESSION_HAS_ARTIFACTS",
                    "Phiên đích không thể gộp",
                )
            target_links = _active_links(db, target.id)
            for link in target_links:
                link.released_at = now
                db.add(
                    models.FnbSessionTable(
                        session_id=source.id, table_id=link.table_id
                    )
                )
                table = db.get(models.FnbTable, link.table_id)
                table.state_version = int(table.state_version or 0) + 1
                table.updated_at = now
            db.query(models.FnbSessionLine).filter(
                models.FnbSessionLine.session_id == target.id
            ).update({models.FnbSessionLine.session_id: source.id})
            target.status = "CANCELLED"
            target.merged_into_session_id = source.id
            target.closed_by_user_id = current_user.id
            target.closed_at = now
            target.revision = int(target.revision or 0) + 1
        source.revision = int(source.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db,
            current_user,
            source,
            action,
            request.operation_id,
            fingerprint,
            before=before,
        )
    except Exception:
        db.rollback()
        raise


def cancel_session(
    db: Session,
    current_user: models.User,
    session_id: int,
    request: FnbSessionCancel,
) -> dict:
    action = "FNB_SESSION_CANCEL"
    fingerprint = operation_fingerprint(action, _payload(request, session_id=session_id))
    try:
        session = _session_for_access(db, current_user, session_id)
        shop_id = int(session.shop_id)
        _prepare_locked_shop(db, shop_id)
        session = _session_for_access(db, current_user, session_id)
        shop = require_fnb_access(db, shop_id, current_user, PERMISSION_FNB_SERVICE)
        existing = _existing_operation(db, shop_id, request.operation_id, fingerprint)
        if existing is not None:
            db.rollback()
            return existing
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
        _require_open(session)
        require_session_revision(db, session, request.expected_revision)
        if (
            db.query(models.FnbSessionLine.id)
            .filter(
                models.FnbSessionLine.session_id == session.id,
                models.FnbSessionLine.quantity
                > models.FnbSessionLine.cancelled_quantity,
            )
            .first()
            is not None
        ):
            raise fnb_error(
                409,
                "FNB_SESSION_NOT_EMPTY",
                "Hãy hủy hết số lượng món nháp trước khi hủy phiên",
            )
        before = serialize_session(db, session)
        now = datetime.datetime.utcnow()
        for link in _active_links(db, session.id):
            link.released_at = now
            table = db.get(models.FnbTable, link.table_id)
            table.state_version = int(table.state_version or 0) + 1
            table.updated_at = now
        session.status = "CANCELLED"
        session.closed_by_user_id = current_user.id
        session.closed_at = now
        session.revision = int(session.revision or 0) + 1
        shop.fnb_revision = int(shop.fnb_revision or 0) + 1
        return _session_result_finish(
            db,
            current_user,
            session,
            action,
            request.operation_id,
            fingerprint,
            before=before,
            reason=_normalize_note(request.reason),
        )
    except Exception:
        db.rollback()
        raise


def get_floor(
    db: Session,
    current_user: models.User,
    shop_id: int,
    after_revision: int | None = None,
    include_inactive: bool = False,
) -> dict:
    if include_inactive:
        shop = require_fnb_access(
            db, shop_id, current_user, PERMISSION_FNB_MANAGE
        )
        if not bool(shop.fnb_enabled):
            raise fnb_error(409, "FNB_DISABLED", "Cửa hàng chưa bật bán tại bàn")
    else:
        shop = require_fnb_shop(db, shop_id, current_user)
    revision = int(shop.fnb_revision or 0)
    if after_revision == revision:
        return {"changed": False, "fnb_revision": revision}
    area_query = db.query(models.FnbArea).filter(models.FnbArea.shop_id == shop_id)
    table_query = db.query(models.FnbTable).filter(models.FnbTable.shop_id == shop_id)
    if not include_inactive:
        area_query = area_query.filter(models.FnbArea.active.is_(True))
        table_query = table_query.filter(models.FnbTable.active.is_(True))
    areas = area_query.order_by(models.FnbArea.sort_order, models.FnbArea.id).all()
    tables = table_query.order_by(models.FnbTable.sort_order, models.FnbTable.id).all()
    by_area: dict[int, list[models.FnbTable]] = {}
    for table in tables:
        by_area.setdefault(table.area_id, []).append(table)
    links = (
        db.query(models.FnbSessionTable, models.FnbServiceSession)
        .join(
            models.FnbServiceSession,
            models.FnbServiceSession.id == models.FnbSessionTable.session_id,
        )
        .filter(
            models.FnbSessionTable.table_id.in_([table.id for table in tables]),
            models.FnbSessionTable.released_at.is_(None),
            models.FnbServiceSession.status.in_(_ACTIVE_SESSION_STATUSES),
        )
        .all()
        if tables
        else []
    )
    occupied = {link.table_id: session for link, session in links}
    summaries = {}
    for session in occupied.values():
        if session.id not in summaries:
            snapshot = serialize_session(db, session)
            summaries[session.id] = {
                "id": session.id,
                "revision": int(session.revision or 0),
                "opened_at": session.opened_at.isoformat() + "Z",
                "subtotal_vnd": snapshot["subtotal_vnd"],
                "unsent_quantity": snapshot["unsent_quantity"],
                "service_stage": snapshot["service_stage"],
                "service_summary": snapshot["service_summary"],
                "table_count": len(snapshot["tables"]),
            }
    return {
        "changed": True,
        "shop_id": shop.id,
        "fnb_revision": revision,
        "areas": [
            {
                "id": area.id,
                "name": area.name,
                "sort_order": int(area.sort_order),
                **({"active": bool(area.active)} if include_inactive else {}),
                "tables": [
                    {
                        "id": table.id,
                        "name": table.name,
                        **({"active": bool(table.active)} if include_inactive else {}),
                        **(
                            {"sort_order": int(table.sort_order)}
                            if include_inactive
                            else {}
                        ),
                        "state_version": int(table.state_version or 0),
                        "state": "SERVING" if table.id in occupied else "EMPTY",
                        "session": (
                            summaries[occupied[table.id].id]
                            if table.id in occupied
                            else None
                        ),
                    }
                    for table in by_area.get(area.id, [])
                ],
            }
            for area in areas
        ],
    }
