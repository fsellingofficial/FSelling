"""Shared one-use manager PIN approvals for F&B and Retail."""

import datetime
import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..core.i18n import tr
from ..core.security import burn_password_time, hash_password, verify_password
from ..dependencies import (
    PERMISSION_RETURN_APPROVE,
    has_staff_permission,
    require_shop_access,
)
from . import auth_session_service


def approval_error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": tr(message), **extra},
    )


def _is_manager(shop: models.Shop, user: models.User | None) -> bool:
    return bool(
        user
        and user.is_active
        and (
            user.id == shop.owner_id
            or (
                user.role == "STAFF"
                and user.staff_shop_id == shop.id
                and has_staff_permission(user, PERMISSION_RETURN_APPROVE)
            )
        )
    )


def set_manager_pin(
    db: Session, current_user: models.User, shop_id: int, pin: str
) -> dict:
    """Authorize owner/manager, hash the PIN, and commit no other state."""
    shop = require_shop_access(db, shop_id, current_user)
    if not _is_manager(shop, current_user):
        raise approval_error(
            403, "MANAGER_REQUIRED", "Chỉ chủ cửa hàng hoặc quản lý được đặt PIN"
        )
    auth_session_service.fence_live_auth_session(db)
    current_user.fnb_manager_pin_hash = hash_password(pin)
    invalidated = invalidate_approvals_for_approver(
        db, current_user.id, "PIN_CHANGE"
    )
    db.add(
        models.SystemLog(
            user_id=current_user.id,
            shop_id=shop_id,
            auth_session_id=db.info.get("auth_session_id"),
            action="APPROVALS_INVALIDATE_PIN_CHANGE",
            details=f"approvals={invalidated}",
        )
    )
    db.commit()
    return {"shop_id": shop_id, "manager_pin_configured": True}


def issue_pin_approval(
    db: Session,
    *,
    shop: models.Shop,
    actor: models.User,
    approver_username: str,
    pin: str,
    action: str,
    entity_type: str,
    entity_id: int,
    revision: int,
    context_fingerprint: str | None = None,
) -> tuple[str, models.FnbManagerApproval]:
    """Verify/rate-limit the approver and add one five-minute hashed token."""
    now = datetime.datetime.utcnow()
    actor_auth_session_id = db.info.get("auth_session_id")
    failed_attempts = (
        db.query(models.FnbManagerApproval)
        .filter(
            models.FnbManagerApproval.shop_id == shop.id,
            models.FnbManagerApproval.actor_user_id == actor.id,
            models.FnbManagerApproval.action == "PIN_FAILED",
            models.FnbManagerApproval.created_at
            >= now - datetime.timedelta(minutes=15),
        )
        .count()
    )
    if failed_attempts >= 5:
        raise approval_error(
            429,
            "APPROVAL_RATE_LIMITED",
            "Đã nhập sai PIN quá nhiều lần; vui lòng thử lại sau",
            retry_after_seconds=900,
        )

    approver = (
        db.query(models.User)
        .filter(
            models.User.username == approver_username,
            models.User.is_active == True,  # noqa: E712
        )
        .first()
    )
    valid_approver = _is_manager(shop, approver)
    pin_hash = approver.fnb_manager_pin_hash if valid_approver else None
    pin_ok = False
    if pin_hash is None:
        burn_password_time()
    else:
        pin_ok = verify_password(pin, pin_hash)
    if not pin_ok:
        db.add(
            models.FnbManagerApproval(
                shop_id=shop.id,
                approver_user_id=approver.id if valid_approver else actor.id,
                actor_user_id=actor.id,
                action="PIN_FAILED",
                entity_type=entity_type,
                entity_id=entity_id,
                revision=revision,
                context_fingerprint=context_fingerprint,
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                expires_at=now,
                used_at=now,
                created_at=now,
            )
        )
        db.commit()
        raise approval_error(403, "APPROVAL_PIN_INVALID", "PIN quản lý không đúng")

    token = secrets.token_urlsafe(32)
    if not actor_auth_session_id:
        raise approval_error(
            401, "AUTH_SESSION_INVALID", "Phiên đăng nhập không hợp lệ"
        )
    approval = models.FnbManagerApproval(
        shop_id=shop.id,
        approver_user_id=approver.id,
        actor_user_id=actor.id,
        actor_auth_session_id=actor_auth_session_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        revision=revision,
        context_fingerprint=context_fingerprint,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=now + datetime.timedelta(minutes=5),
        created_at=now,
    )
    db.add(approval)
    return token, approval


def consume_approval(
    db: Session,
    *,
    token: str,
    shop_id: int,
    actor_user_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    revision: int,
    context_fingerprint: str | None = None,
) -> models.FnbManagerApproval:
    """Lock and mark one exactly-bound approval used without committing."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    actor_auth_session_id = db.info.get("auth_session_id")
    if not actor_auth_session_id:
        raise approval_error(
            401, "AUTH_SESSION_INVALID", "Phiên đăng nhập không hợp lệ"
        )
    approval = (
        db.query(models.FnbManagerApproval)
        .filter(
            models.FnbManagerApproval.token_hash == token_hash,
            models.FnbManagerApproval.shop_id == shop_id,
            models.FnbManagerApproval.actor_user_id == actor_user_id,
            models.FnbManagerApproval.actor_auth_session_id
            == actor_auth_session_id,
            models.FnbManagerApproval.action == action,
            models.FnbManagerApproval.entity_type == entity_type,
            models.FnbManagerApproval.entity_id == entity_id,
            models.FnbManagerApproval.revision == revision,
            models.FnbManagerApproval.used_at.is_(None),
            models.FnbManagerApproval.expires_at > datetime.datetime.utcnow(),
        )
        .first()
    )
    if approval is None:
        raise approval_error(403, "APPROVAL_INVALID", "Lượt duyệt không còn hợp lệ")
    shop = db.get(models.Shop, approval.shop_id)
    approver = db.get(models.User, approval.approver_user_id)
    if shop is None or not _is_manager(shop, approver):
        raise approval_error(403, "APPROVAL_INVALID", "Lượt duyệt không còn hợp lệ")
    if approval.context_fingerprint != context_fingerprint:
        raise approval_error(
            409,
            "APPROVAL_CONTEXT_CHANGED",
            "Thông tin cần duyệt đã thay đổi",
        )
    approval.used_at = datetime.datetime.utcnow()
    db.flush()
    return approval


def invalidate_approvals_for_approver(
    db: Session, approver_user_id: int, reason: str
) -> int:
    del reason
    now = datetime.datetime.utcnow()
    return db.query(models.FnbManagerApproval).filter(
        models.FnbManagerApproval.approver_user_id == approver_user_id,
        models.FnbManagerApproval.used_at.is_(None),
        models.FnbManagerApproval.expires_at > now,
    ).update(
        {models.FnbManagerApproval.used_at: now},
        synchronize_session=False,
    )
