"""Durable authentication-session primitives."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models
from ..core.config import ACCESS_TOKEN_EXPIRE_MINUTES
from ..core.security import create_access_token, new_session_id


def _auth_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": code, "message": message})


def normalize_device_metadata(
    device_id: str | None,
    device_name: str | None,
    device_type: str | None,
) -> tuple[str, str, str]:
    normalized_id = (device_id or f"legacy-{secrets.token_urlsafe(18)}").strip()
    normalized_name = (device_name or "Thiết bị chưa đặt tên").strip()
    normalized_type = (device_type or "UNKNOWN").upper()
    if not 1 <= len(normalized_id) <= 128:
        raise HTTPException(
            status_code=400,
            detail={"code": "AUTH_DEVICE_ID_INVALID", "message": "Thiết bị không hợp lệ"},
        )
    if not 1 <= len(normalized_name) <= 80:
        raise HTTPException(
            status_code=400,
            detail={"code": "AUTH_DEVICE_NAME_INVALID", "message": "Tên thiết bị không hợp lệ"},
        )
    if normalized_type not in models.AUTH_DEVICE_TYPES:
        normalized_type = "UNKNOWN"
    return normalized_id, normalized_name, normalized_type


def session_view(row: models.AuthSession, *, current_session_id: str | None = None) -> dict:
    return {
        "session_id": row.session_id,
        "device_id": row.device_id,
        "device_name": row.device_name,
        "device_type": row.device_type,
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "current": row.session_id == current_session_id,
    }


def create_session(
    db: Session,
    user: models.User,
    device_id: str | None,
    device_name: str | None,
    device_type: str | None,
    now: datetime | None = None,
) -> tuple[models.AuthSession, str]:
    device_id, device_name, device_type = normalize_device_metadata(
        device_id, device_name, device_type
    )
    now = now or datetime.utcnow()
    db.query(models.AuthSession).filter(
        models.AuthSession.user_id == user.id,
        models.AuthSession.device_id == device_id,
        models.AuthSession.revoked_at.is_(None),
    ).update(
        {
            models.AuthSession.revoked_at: now,
            models.AuthSession.revoked_by_user_id: user.id,
            models.AuthSession.revoke_reason: "SAME_DEVICE_LOGIN",
        },
        synchronize_session=False,
    )
    row = models.AuthSession(
        session_id=new_session_id(),
        user_id=user.id,
        device_id=device_id,
        device_name=device_name,
        device_type=device_type,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(row)
    db.flush()
    return row, create_access_token(
        user.username, row.session_id, expires_at=row.expires_at
    )


def require_live_session(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    touch: bool = False,
) -> models.AuthSession:
    row = db.get(models.AuthSession, session_id)
    if row is None or row.user_id != user_id:
        raise _auth_error("AUTH_SESSION_INVALID", "Phiên đăng nhập không hợp lệ")
    if row.revoked_at is not None:
        raise _auth_error("AUTH_SESSION_REVOKED", "Phiên đăng nhập đã bị thu hồi")
    now = datetime.utcnow()
    if row.expires_at <= now:
        raise _auth_error("AUTH_SESSION_EXPIRED", "Phiên đăng nhập đã hết hạn")
    if touch and row.last_seen_at <= now - timedelta(minutes=5):
        row.last_seen_at = now
    return row


def bind_request_session(db: Session, auth_session: models.AuthSession) -> None:
    db.info["auth_session_id"] = auth_session.session_id
    db.info["auth_user_id"] = auth_session.user_id
    db.info["auth_session_required"] = True


def fence_live_auth_session(db: Session) -> None:
    """Recheck the bound session inside the transaction's write boundary."""
    if not db.info.get("auth_session_required"):
        return
    session_id = db.info.get("auth_session_id")
    user_id = db.info.get("auth_user_id")
    if not session_id or not user_id:
        raise _auth_error("AUTH_SESSION_INVALID", "Phiên đăng nhập không hợp lệ")
    now = datetime.utcnow()
    matched = db.execute(
        text(
            "UPDATE auth_sessions SET last_seen_at = last_seen_at "
            "WHERE session_id = :session_id AND user_id = :user_id "
            "AND revoked_at IS NULL AND expires_at > :now"
        ),
        {"session_id": session_id, "user_id": user_id, "now": now},
    )
    if matched.rowcount == 1:
        return
    row = (
        db.query(models.AuthSession)
        .populate_existing()
        .filter(
            models.AuthSession.session_id == session_id,
            models.AuthSession.user_id == user_id,
        )
        .first()
    )
    if row is not None and row.revoked_at is not None:
        raise _auth_error("AUTH_SESSION_REVOKED", "Phiên đăng nhập đã bị thu hồi")
    if row is not None and row.expires_at <= now:
        raise _auth_error("AUTH_SESSION_EXPIRED", "Phiên đăng nhập đã hết hạn")
    raise _auth_error("AUTH_SESSION_INVALID", "Phiên đăng nhập không hợp lệ")


def list_user_sessions(
    db: Session, target_user_id: int, current_session_id: str
) -> list[dict]:
    now = datetime.utcnow()
    rows = (
        db.query(models.AuthSession)
        .filter(
            models.AuthSession.user_id == target_user_id,
            models.AuthSession.expires_at > now,
        )
        .order_by(
            models.AuthSession.last_seen_at.desc(),
            models.AuthSession.created_at.desc(),
        )
        .all()
    )
    rows.sort(key=lambda row: row.session_id != current_session_id)
    return [session_view(row, current_session_id=current_session_id) for row in rows]


def _owned_session(
    db: Session, target_user_id: int, session_id: str
) -> models.AuthSession:
    row = db.query(models.AuthSession).filter(
        models.AuthSession.session_id == session_id,
        models.AuthSession.user_id == target_user_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên đăng nhập")
    return row


def rename_session(
    db: Session,
    actor: models.User,
    target_user_id: int,
    session_id: str,
    device_name: str,
) -> dict:
    del actor
    fence_live_auth_session(db)
    normalized = (device_name or "").strip()
    if not 1 <= len(normalized) <= 80:
        raise HTTPException(
            status_code=400,
            detail={"code": "AUTH_DEVICE_NAME_INVALID", "message": "Tên thiết bị không hợp lệ"},
        )
    row = _owned_session(db, target_user_id, session_id)
    row.device_name = normalized
    db.commit()
    db.refresh(row)
    return session_view(row, current_session_id=db.info.get("auth_session_id"))


def revoke_session(
    db: Session,
    actor: models.User,
    target_user_id: int,
    session_id: str,
    reason: str,
) -> dict:
    fence_live_auth_session(db)
    row = _owned_session(db, target_user_id, session_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        row.revoked_by_user_id = actor.id
        row.revoke_reason = reason
        db.add(
            models.SystemLog(
                user_id=actor.id,
                shop_id=actor.staff_shop_id if actor.role == "STAFF" else None,
                auth_session_id=db.info.get("auth_session_id"),
                action=(
                    "AUTH_SESSION_LOGOUT" if reason == "LOGOUT" else "AUTH_SESSION_REVOKE"
                ),
                details=f"target_user_id={target_user_id}; device_type={row.device_type}; reason={reason}",
            )
        )
        db.commit()
        db.refresh(row)
    return session_view(row, current_session_id=db.info.get("auth_session_id"))


def revoke_all_user_sessions(
    db: Session,
    *,
    target_user_id: int,
    actor_user_id: int | None,
    reason: str,
    except_session_id: str | None = None,
) -> int:
    now = datetime.utcnow()
    query = db.query(models.AuthSession).filter(
        models.AuthSession.user_id == target_user_id,
        models.AuthSession.revoked_at.is_(None),
    )
    if except_session_id is not None:
        query = query.filter(models.AuthSession.session_id != except_session_id)
    return query.update(
        {
            models.AuthSession.revoked_at: now,
            models.AuthSession.revoked_by_user_id: actor_user_id,
            models.AuthSession.revoke_reason: reason,
        },
        synchronize_session=False,
    )


@dataclass(frozen=True)
class DeviceRevokeResult:
    sessions_revoked: int
    offline_leases_revoked: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def revoke_device(
    db: Session,
    *,
    actor: models.User,
    target_user_id: int,
    device_id: str,
    reason: str = "DEVICE_REVOKED",
) -> DeviceRevokeResult:
    normalized = (device_id or "").strip()
    if not 1 <= len(normalized) <= 128:
        raise HTTPException(
            status_code=400,
            detail={"code": "AUTH_DEVICE_ID_INVALID", "message": "Thiết bị không hợp lệ"},
        )
    rows = db.query(models.AuthSession.session_id).filter(
        models.AuthSession.user_id == target_user_id,
        models.AuthSession.device_id == normalized,
    ).all()
    session_ids = [row[0] for row in rows]
    shop_ids = []
    if session_ids:
        shop_ids = sorted(
            shop_id
            for (shop_id,) in db.query(models.OfflineLease.shop_id)
            .filter(models.OfflineLease.issued_by_auth_session_id.in_(session_ids))
            .distinct()
            .all()
        )
    if shop_ids:
        from . import inventory_service

        for shop_id in shop_ids:
            inventory_service.lock_shop_for_inventory(db, shop_id)
    else:
        fence_live_auth_session(db)
    now = datetime.utcnow()
    sessions_revoked = 0
    leases_revoked = 0
    if session_ids:
        sessions_revoked = db.query(models.AuthSession).filter(
            models.AuthSession.session_id.in_(session_ids),
            models.AuthSession.revoked_at.is_(None),
        ).update(
            {
                models.AuthSession.revoked_at: now,
                models.AuthSession.revoked_by_user_id: actor.id,
                models.AuthSession.revoke_reason: reason,
            },
            synchronize_session=False,
        )
        leases_revoked = db.query(models.OfflineLease).filter(
            models.OfflineLease.issued_by_auth_session_id.in_(session_ids),
            models.OfflineLease.revoked_at.is_(None),
        ).update(
            {
                models.OfflineLease.revoked_at: now.strftime("%Y-%m-%d %H:%M:%S.%f"),
                models.OfflineLease.revoked_by_user_id: actor.id,
                models.OfflineLease.revoke_reason: reason,
                models.OfflineLease.state_version: models.OfflineLease.state_version + 1,
            },
            synchronize_session=False,
        )
        db.add(
            models.SystemLog(
                user_id=actor.id,
                shop_id=actor.staff_shop_id if actor.role == "STAFF" else None,
                auth_session_id=db.info.get("auth_session_id"),
                action="AUTH_DEVICE_REVOKE",
                details=(
                    f"target_user_id={target_user_id}; sessions={sessions_revoked}; "
                    f"offline_leases={leases_revoked}; reason={reason}"
                ),
            )
        )
    db.commit()
    return DeviceRevokeResult(sessions_revoked, leases_revoked)
