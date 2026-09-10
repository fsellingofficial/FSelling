"""Lifecycle credential và attribution primitive cho offline contract v1.

Lease là claim của một JWT session + device binding. Nó không chứng minh con
người thật nào cầm máy và thực hiện từng lần bán; financial ingest tương lai
phải giữ riêng claimed seller và sync actor như context bất biến bên dưới.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..core import config
from ..core.i18n import tr
from ..core.money import checked_vnd
from ..core.security import compare_secret
from ..dependencies import (
    PERMISSION_SALE,
    has_staff_permission,
    has_shop_operator_access,
    require_shop_access,
    require_staff_permission,
)
from . import order_service, subscription_service
from .offline_fingerprint import canonical_time_text, normalize_text

LEASE_TTL = timedelta(hours=12)
LEASE_GRACE = timedelta(hours=72)
CATALOG_VERSION = 0
CONTRACT_VERSION = 1

STATUS_ACTIVE = "ACTIVE"
STATUS_SYNC_ONLY = "SYNC_ONLY"
STATUS_REVOKED = "REVOKED"

ERROR_ISSUANCE_DISABLED = "OFFLINE_LEASE_ISSUANCE_DISABLED"
ERROR_NOT_YOURS = "OFFLINE_LEASE_NOT_YOURS"
ERROR_BINDING_MISMATCH = "OFFLINE_LEASE_BINDING_MISMATCH"
ERROR_REVOKED = "OFFLINE_LEASE_REVOKED"
ERROR_EXPIRED = "OFFLINE_LEASE_EXPIRED"
ERROR_RECLAIM_DENIED = "OFFLINE_LEASE_RECLAIM_DENIED"
ERROR_RECOVERY_REQUIRED = "OFFLINE_LEASE_RECOVERY_REQUIRED"
ERROR_STATE_CONFLICT = "OFFLINE_LEASE_STATE_CONFLICT"
ERROR_ISSUE_CONFLICT = "OFFLINE_LEASE_ISSUE_CONFLICT"
ERROR_REVOKE_FORBIDDEN = "OFFLINE_LEASE_REVOKE_FORBIDDEN"
ERROR_NOT_FOUND = "OFFLINE_LEASE_NOT_FOUND"
ERROR_CATALOG_INVALID = "OFFLINE_CATALOG_INVALID"
ERROR_DEVICE_INVALID = "OFFLINE_LEASE_DEVICE_INVALID"
ERROR_REASON_INVALID = "OFFLINE_LEASE_REVOKE_REASON_INVALID"

AUDIT_ISSUE = "OFFLINE_LEASE_ISSUE"
AUDIT_RECLAIM = "OFFLINE_LEASE_RECLAIM"
AUDIT_REVOKE = "OFFLINE_LEASE_REVOKE"

CATALOG_PREFIX_V1 = b"FS-OFFLINE-CATALOG-v1\n"
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LEASE_ID_RE = re.compile(r"^lse_[0-9A-HJKMNP-TV-Z]{22}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DUMMY_SECRET_DIGEST = "0" * 64


@dataclass(frozen=True)
class CatalogSnapshotV1:
    canonical_json: str
    digest: str


@dataclass(frozen=True)
class OfflineAttributionContext:
    lease_id: str
    shop_id: int
    device_id: str
    offline_session_id: str
    contract_version: int
    catalog_version: int
    catalog_snapshot_digest: str
    server_anchor_id: str
    anchor_server_time_utc: datetime
    lease_status: str
    sold_by_claimed_user_id: int
    synced_by_user_id: int
    attribution_kind: str
    created_by_user_id: int
    payment_actor_user_id: int
    shift_owner_user_id: int
    # Expose issued_at and expires_at for time boundary checks in v1 ingest
    issued_at: datetime
    expires_at: datetime


def _utcnow() -> datetime:
    return datetime.utcnow()


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": tr(message)},
    )


def _not_yours() -> HTTPException:
    # Cùng response cho ID sai, token sai, lease shop khác và membership sai.
    return _error(403, ERROR_NOT_YOURS, "Credential offline không thuộc phiên này")


def _state_conflict(current_state_version: int) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": ERROR_STATE_CONFLICT,
            "message": tr("Credential offline vừa thay đổi; vui lòng tải lại"),
            "current_state_version": int(current_state_version),
        },
    )


def is_valid_lease_id(value: Any) -> bool:
    return isinstance(value, str) and _LEASE_ID_RE.fullmatch(value) is not None


def generate_lease_id() -> str:
    """Sinh 110 bit Crockford từ nguồn ngẫu nhiên 14 byte, fixed 22 chars."""
    value = int.from_bytes(secrets.token_bytes(14), "big") >> 2
    encoded = [CROCKFORD_ALPHABET[0]] * 22
    for index in range(21, -1, -1):
        encoded[index] = CROCKFORD_ALPHABET[value & 31]
        value >>= 5
    return "lse_" + "".join(encoded)


def generate_lease_token() -> str:
    return secrets.token_urlsafe(32)


def digest_lease_token(candidate: Any) -> str:
    """Luôn trả lowercase SHA-256; input không-ASCII băm sentinel, không ném."""
    try:
        raw = candidate.encode("ascii") if isinstance(candidate, str) else b""
    except UnicodeEncodeError:
        raw = b""
    return hashlib.sha256(raw).hexdigest()


def _token_matches(candidate: Any, stored_secret_sha256: str | None) -> bool:
    candidate_digest = digest_lease_token(candidate)
    expected = stored_secret_sha256 or _DUMMY_SECRET_DIGEST
    format_ok = isinstance(candidate, str) and _TOKEN_RE.fullmatch(candidate) is not None
    # Chính xác digest-vs-digest; không bao giờ đưa raw token vào compare_secret.
    matched = compare_secret(candidate_digest, expected)
    return bool(format_ok and matched)


def _strict_product_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def catalog_snapshot_v1(products: Iterable[Any]) -> CatalogSnapshotV1:
    rows = []
    for product in products:
        product_id = _strict_product_int(product.id, "product_id")
        price = checked_vnd(_strict_product_int(product.price, "price_vnd"))
        name = normalize_text(product.name)
        if not name:
            raise ValueError("product name is empty after normalization")
        rows.append(
            {
                "id": product_id,
                "name": name,
                "price_vnd": price,
                "is_active": bool(product.is_active),
                "track_batches": bool(product.track_batches),
            }
        )
    rows.sort(key=lambda row: row["id"])
    canonical_json = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(
        CATALOG_PREFIX_V1 + canonical_json.encode("utf-8")
    ).hexdigest()
    return CatalogSnapshotV1(canonical_json=canonical_json, digest=digest)


def _catalog_for_shop(db: Session, shop_id: int) -> CatalogSnapshotV1:
    products = (
        db.query(models.Product)
        .filter(models.Product.shop_id == shop_id)
        .all()
    )
    try:
        return catalog_snapshot_v1(products)
    except ValueError as exc:
        raise _error(
            409,
            ERROR_CATALOG_INVALID,
            "Danh mục không thể tạo snapshot offline an toàn",
        ) from exc


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    except (TypeError, ValueError) as exc:
        raise _not_yours() from exc
    if canonical_time_text(parsed) != value:
        raise _not_yours()
    return parsed


def _lease_status(lease: models.OfflineLease, now: datetime) -> str:
    if lease.revoked_at is not None:
        return STATUS_REVOKED
    expires_at = _parse_time(lease.expires_at)
    if now < expires_at:
        return STATUS_ACTIVE
    if now <= expires_at + LEASE_GRACE:
        return STATUS_SYNC_ONLY
    return ERROR_EXPIRED


def _public_response(
    lease: models.OfflineLease,
    *,
    status: str,
    now: datetime,
    lease_token: str | None = None,
) -> dict[str, Any]:
    response = {
        "lease_id": lease.lease_id,
        "shop_id": int(lease.shop_id),
        "user_id": int(lease.user_id),
        "device_id": lease.device_id,
        "contract_version": int(lease.contract_version),
        "status": status,
        "server_time_utc": canonical_time_text(now),
        "server_anchor_id": lease.server_anchor_id,
        "anchor_server_time_utc": lease.anchor_server_time_utc,
        "issued_at": lease.issued_at,
        "expires_at": lease.expires_at,
        "catalog_version": int(lease.catalog_version),
        "catalog_snapshot_digest": lease.catalog_snapshot_digest,
        "state_version": int(lease.state_version),
        "revoked_at": lease.revoked_at,
    }
    if lease_token is not None:
        response["lease_token"] = lease_token
    return response


def _add_audit(
    db: Session,
    *,
    actor_id: int,
    shop_id: int,
    action: str,
    lease_id: str,
    lease_user_id: int,
    old_status: str,
    new_status: str,
    old_version: int | None,
    new_version: int,
) -> None:
    db.add(
        models.SystemLog(
            user_id=actor_id,
            shop_id=shop_id,
            action=action,
            details=(
                f"Lease {lease_id} shop #{shop_id} user #{lease_user_id}: "
                f"{old_status}->{new_status}; version "
                f"{old_version if old_version is not None else 'none'}->{new_version}"
            ),
        )
    )
    db.flush()


def _refresh_principal(db: Session, current_user: models.User) -> models.User:
    user = db.get(models.User, int(current_user.id))
    if user is None or user.is_active is False:
        raise _not_yours()
    return user


def _has_exact_membership(
    db: Session, lease: models.OfflineLease, current_user: models.User
) -> bool:
    if int(lease.user_id) != int(current_user.id):
        return False
    shop = db.get(models.Shop, int(lease.shop_id))
    user = db.get(models.User, int(current_user.id))
    if shop is None or user is None or user.is_active is False:
        return False
    return has_shop_operator_access(shop, user)


def _current_operation_principal(
    db: Session, lease: models.OfflineLease, current_user: models.User
) -> models.User | None:
    """Re-fetch principal/membership for mutating or financial capability checks.

    Heartbeat intentionally keeps the membership-only helper above: an existing
    credential may still report its server state after SALE/Pro is lost.
    """
    if int(lease.user_id) != int(current_user.id):
        return None
    user = (
        db.query(models.User)
        .populate_existing()
        .filter(models.User.id == int(current_user.id))
        .first()
    )
    shop = (
        db.query(models.Shop)
        .populate_existing()
        .filter(models.Shop.id == int(lease.shop_id))
        .first()
    )
    if shop is None or user is None or user.is_active is False:
        return None
    return user if has_shop_operator_access(shop, user) else None


def _has_current_normal_policy(
    db: Session,
    lease: models.OfflineLease,
    principal: models.User,
    *,
    now: datetime,
) -> bool:
    return bool(
        has_staff_permission(principal, PERMISSION_SALE)
        and subscription_service.get_subscription_state(
            db, int(lease.shop_id), now=now
        )["can_use_pro"]
    )


def issue_lease(
    db: Session,
    current_user: models.User,
    *,
    shop_id: int,
    device_id: str,
) -> dict[str, Any]:
    if not config.OFFLINE_LEASE_ISSUANCE_ENABLED:
        raise _error(
            503,
            ERROR_ISSUANCE_DISABLED,
            "Cấp credential offline đang tắt an toàn",
        )
    try:
        normalized_device = normalize_text(device_id)
    except ValueError as exc:
        raise _error(
            400,
            ERROR_DEVICE_INVALID,
            "Mã thiết bị offline không hợp lệ",
        ) from exc
    if not normalized_device or len(normalized_device) > 128:
        raise _error(400, ERROR_DEVICE_INVALID, "Mã thiết bị offline không hợp lệ")

    # Policy hiện hữu được kiểm trước và lặp lại dưới shop write lock.
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_SALE)
    subscription_service.require_pro(db, shop_id)
    actor_id = int(current_user.id)
    db.rollback()

    token = generate_lease_token()
    now = _utcnow()
    issued_at = canonical_time_text(now)
    try:
        order_service._lock_shop_for_order(db, shop_id)
        principal = _refresh_principal(db, current_user)
        require_shop_access(db, shop_id, principal)
        require_staff_permission(principal, PERMISSION_SALE)
        subscription_service.require_pro(db, shop_id, now=now)
        catalog = _catalog_for_shop(db, shop_id)
        lease = models.OfflineLease(
            lease_id=generate_lease_id(),
            shop_id=shop_id,
            user_id=actor_id,
            device_id=normalized_device,
            contract_version=CONTRACT_VERSION,
            catalog_version=CATALOG_VERSION,
            catalog_snapshot_digest=catalog.digest,
            secret_sha256=digest_lease_token(token),
            server_anchor_id="anc_" + secrets.token_urlsafe(18),
            anchor_server_time_utc=issued_at,
            issued_at=issued_at,
            expires_at=canonical_time_text(now + LEASE_TTL),
            state_version=0,
            issued_by_auth_session_id=db.info.get("auth_session_id"),
        )
        db.add(lease)
        db.flush()
        _add_audit(
            db,
            actor_id=actor_id,
            shop_id=shop_id,
            action=AUDIT_ISSUE,
            lease_id=lease.lease_id,
            lease_user_id=actor_id,
            old_status="NONE",
            new_status=STATUS_ACTIVE,
            old_version=None,
            new_version=0,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _error(
            409,
            ERROR_ISSUE_CONFLICT,
            "Không thể cấp credential offline; vui lòng thử lại",
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(lease)
    return _public_response(
        lease, status=STATUS_ACTIVE, now=now, lease_token=token
    )


def _authenticated_lease(
    db: Session,
    current_user: models.User,
    *,
    lease_id: str,
    lease_token: Any,
) -> models.OfflineLease:
    valid_id = is_valid_lease_id(lease_id)
    lease = db.get(models.OfflineLease, lease_id) if valid_id else None
    stored = lease.secret_sha256 if lease is not None else _DUMMY_SECRET_DIGEST
    token_ok = _token_matches(lease_token, stored)
    if lease is None or not token_ok or not _has_exact_membership(
        db, lease, current_user
    ):
        raise _not_yours()
    return lease


def heartbeat(
    db: Session,
    current_user: models.User,
    *,
    lease_id: str,
    lease_token: str,
) -> dict[str, Any]:
    lease = _authenticated_lease(
        db,
        current_user,
        lease_id=lease_id,
        lease_token=lease_token,
    )
    now = _utcnow()
    status = _lease_status(lease, now)
    if status == STATUS_REVOKED:
        raise _error(403, ERROR_REVOKED, "Credential offline đã bị thu hồi")
    if status == ERROR_EXPIRED:
        raise _error(403, ERROR_EXPIRED, "Credential offline đã hết thời gian cứu")
    return _public_response(lease, status=status, now=now)


def reclaim(
    db: Session,
    current_user: models.User,
    *,
    lease_id: str,
    expected_state_version: int,
) -> dict[str, Any]:
    if not is_valid_lease_id(lease_id):
        raise _not_yours()
    first = db.get(models.OfflineLease, lease_id)
    if first is None or not _has_exact_membership(db, first, current_user):
        raise _not_yours()
    principal = _current_operation_principal(db, first, current_user)
    if principal is None:
        raise _not_yours()
    if not _has_current_normal_policy(db, first, principal, now=_utcnow()):
        raise _error(
            403,
            ERROR_RECLAIM_DENIED,
            "Credential offline không còn được reclaim",
        )
    shop_id = int(first.shop_id)
    actor_id = int(current_user.id)
    db.rollback()

    try:
        order_service._lock_shop_for_order(db, shop_id)
        now = _utcnow()
        lease = db.get(models.OfflineLease, lease_id)
        if lease is None or not _has_exact_membership(db, lease, current_user):
            raise _not_yours()
        principal = _current_operation_principal(db, lease, current_user)
        if principal is None:
            raise _not_yours()
        if not _has_current_normal_policy(db, lease, principal, now=now):
            raise _error(
                403,
                ERROR_RECLAIM_DENIED,
                "Credential offline không còn được reclaim",
            )
        status = _lease_status(lease, now)
        if status in (STATUS_REVOKED, ERROR_EXPIRED):
            raise _error(
                403,
                ERROR_RECLAIM_DENIED,
                "Credential offline không còn được reclaim",
            )
        current_version = int(lease.state_version)
        if current_version != expected_state_version:
            raise _state_conflict(current_version)
        token = generate_lease_token()
        result = db.execute(
            text(
                """UPDATE offline_leases
                      SET secret_sha256 = :digest,
                          state_version = state_version + 1
                    WHERE lease_id = :lease_id
                      AND state_version = :expected_version
                      AND revoked_at IS NULL"""
            ),
            {
                "digest": digest_lease_token(token),
                "lease_id": lease_id,
                "expected_version": expected_state_version,
            },
        )
        if result.rowcount != 1:
            db.expire(lease, ["state_version"])
            raise _state_conflict(int(lease.state_version))
        _add_audit(
            db,
            actor_id=actor_id,
            shop_id=shop_id,
            action=AUDIT_RECLAIM,
            lease_id=lease_id,
            lease_user_id=int(lease.user_id),
            old_status=status,
            new_status=status,
            old_version=expected_state_version,
            new_version=expected_state_version + 1,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _error(
            409,
            ERROR_STATE_CONFLICT,
            "Credential offline vừa thay đổi; vui lòng tải lại",
        ) from exc
    except Exception:
        db.rollback()
        raise
    lease = db.get(models.OfflineLease, lease_id)
    return _public_response(lease, status=status, now=now, lease_token=token)


def _revoke_authorized(
    db: Session, lease: models.OfflineLease, current_user: models.User
) -> bool:
    if current_user.role == "ADMIN":
        return True
    shop = db.get(models.Shop, int(lease.shop_id))
    return bool(shop is not None and int(shop.owner_id) == int(current_user.id))


def revoke(
    db: Session,
    current_user: models.User,
    *,
    lease_id: str,
    reason: str,
) -> dict[str, Any]:
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 500:
        raise _error(
            400,
            ERROR_REASON_INVALID,
            "Lý do thu hồi phải dài từ 10 đến 500 ký tự",
        )
    if not is_valid_lease_id(lease_id):
        raise _error(404, ERROR_NOT_FOUND, "Không tìm thấy credential offline")
    first = db.get(models.OfflineLease, lease_id)
    if first is None:
        raise _error(404, ERROR_NOT_FOUND, "Không tìm thấy credential offline")
    if not _revoke_authorized(db, first, current_user):
        raise _error(
            403,
            ERROR_REVOKE_FORBIDDEN,
            "Chỉ chủ cửa hàng hoặc ADMIN được thu hồi credential offline",
        )
    shop_id = int(first.shop_id)
    expected_version = int(first.state_version)
    actor_id = int(current_user.id)
    if first.revoked_at is not None:
        now = _utcnow()
        return _public_response(first, status=STATUS_REVOKED, now=now)
    db.rollback()

    now = _utcnow()
    revoked_at = canonical_time_text(now)
    try:
        order_service._lock_shop_for_order(db, shop_id)
        lease = db.get(models.OfflineLease, lease_id)
        if lease is None:
            raise _error(404, ERROR_NOT_FOUND, "Không tìm thấy credential offline")
        if not _revoke_authorized(db, lease, current_user):
            raise _error(
                403,
                ERROR_REVOKE_FORBIDDEN,
                "Chỉ chủ cửa hàng hoặc ADMIN được thu hồi credential offline",
            )
        if lease.revoked_at is not None:
            db.rollback()
            return _public_response(lease, status=STATUS_REVOKED, now=now)
        old_status = _lease_status(lease, now)
        result = db.execute(
            text(
                """UPDATE offline_leases
                      SET revoked_at = :revoked_at,
                          revoke_reason = :reason,
                          revoked_by_user_id = :actor,
                          state_version = state_version + 1
                    WHERE lease_id = :lease_id
                      AND state_version = :expected_version
                      AND revoked_at IS NULL"""
            ),
            {
                "revoked_at": revoked_at,
                "reason": reason,
                "actor": actor_id,
                "lease_id": lease_id,
                "expected_version": expected_version,
            },
        )
        if result.rowcount != 1:
            # Một revoke song song đã thắng: trả state bền, không ghi đè audit.
            db.rollback()
            winner = db.get(models.OfflineLease, lease_id)
            if winner is not None and winner.revoked_at is not None:
                return _public_response(winner, status=STATUS_REVOKED, now=now)
            raise _error(
                409,
                ERROR_STATE_CONFLICT,
                "Credential offline vừa thay đổi; vui lòng tải lại",
            )
        _add_audit(
            db,
            actor_id=actor_id,
            shop_id=shop_id,
            action=AUDIT_REVOKE,
            lease_id=lease_id,
            lease_user_id=int(lease.user_id),
            old_status=old_status,
            new_status=STATUS_REVOKED,
            old_version=expected_version,
            new_version=expected_version + 1,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _error(
            409,
            ERROR_STATE_CONFLICT,
            "Credential offline vừa thay đổi; vui lòng tải lại",
        ) from exc
    except Exception:
        db.rollback()
        raise
    lease = db.get(models.OfflineLease, lease_id)
    return _public_response(lease, status=STATUS_REVOKED, now=now)


def authorize_normal_v1_capability(
    db: Session,
    current_user: models.User,
    *,
    shop_id: int,
    lease_id: str,
    lease_token: str,
    device_id: str,
    offline_session_id: str,
    request_received_at: datetime | None = None,
) -> OfflineAttributionContext:
    """Một seam duy nhất cho normal-v1; không bao giờ dùng cho endpoint v0.

    Revocation được xét theo state server hiện tại, không nhận ``sold_at`` từ
    client nên không có timestamp giả nào lách được. SYNC_ONLY chỉ là capability
    cứu receipt đã đông cứng; I09-E+B2 còn phải kiểm receipt nằm trong cửa sổ
    lease trước mọi financial write.
    """
    lease = _authenticated_lease(
        db,
        current_user,
        lease_id=lease_id,
        lease_token=lease_token,
    )
    if int(lease.shop_id) != int(shop_id):
        raise _not_yours()
    try:
        canonical_device = normalize_text(device_id)
    except ValueError as exc:
        raise _error(
            403,
            ERROR_BINDING_MISMATCH,
            "Binding credential offline không khớp",
        ) from exc
    if canonical_device != lease.device_id or offline_session_id != lease.lease_id:
        raise _error(
            403,
            ERROR_BINDING_MISMATCH,
            "Binding credential offline không khớp",
        )
    now = request_received_at if request_received_at is not None else _utcnow()
    status = _lease_status(lease, now)
    if status == STATUS_REVOKED:
        raise _error(409, ERROR_REVOKED, "Credential offline đã bị thu hồi")
    if status == ERROR_EXPIRED:
        raise _error(409, ERROR_EXPIRED, "Credential offline đã hết thời gian cứu")

    principal = _current_operation_principal(db, lease, current_user)
    if principal is None:
        raise _not_yours()
    if not _has_current_normal_policy(db, lease, principal, now=now):
        raise _error(
            409,
            ERROR_RECOVERY_REQUIRED,
            "Credential offline cần owner recovery",
        )

    actor = int(lease.user_id)
    issued_dt = _parse_time(lease.issued_at)
    expires_dt = _parse_time(lease.expires_at)
    anchor_dt = _parse_time(lease.anchor_server_time_utc)
    return OfflineAttributionContext(
        lease_id=lease.lease_id,
        shop_id=int(lease.shop_id),
        device_id=lease.device_id,
        offline_session_id=lease.lease_id,
        contract_version=int(lease.contract_version),
        catalog_version=int(lease.catalog_version),
        catalog_snapshot_digest=lease.catalog_snapshot_digest,
        server_anchor_id=lease.server_anchor_id,
        anchor_server_time_utc=anchor_dt,
        lease_status=status,
        sold_by_claimed_user_id=actor,
        synced_by_user_id=int(current_user.id),
        attribution_kind="LEASE_CLAIM",
        created_by_user_id=actor,
        payment_actor_user_id=actor,
        shift_owner_user_id=actor,
        issued_at=issued_dt,
        expires_at=expires_dt,
    )


__all__ = [
    "AUDIT_ISSUE",
    "AUDIT_RECLAIM",
    "AUDIT_REVOKE",
    "CATALOG_PREFIX_V1",
    "CatalogSnapshotV1",
    "ERROR_BINDING_MISMATCH",
    "ERROR_EXPIRED",
    "ERROR_DEVICE_INVALID",
    "ERROR_ISSUANCE_DISABLED",
    "ERROR_NOT_YOURS",
    "ERROR_RECLAIM_DENIED",
    "ERROR_RECOVERY_REQUIRED",
    "ERROR_REVOKED",
    "ERROR_REASON_INVALID",
    "ERROR_STATE_CONFLICT",
    "OfflineAttributionContext",
    "STATUS_ACTIVE",
    "STATUS_REVOKED",
    "STATUS_SYNC_ONLY",
    "authorize_normal_v1_capability",
    "catalog_snapshot_v1",
    "digest_lease_token",
    "generate_lease_id",
    "heartbeat",
    "is_valid_lease_id",
    "issue_lease",
    "reclaim",
    "revoke",
]
