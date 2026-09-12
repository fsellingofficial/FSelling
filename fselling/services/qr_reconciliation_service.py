"""I10-C scoped reads and atomic commands for durable bank evidence."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from .. import models
from ..dependencies import effective_staff_role
from ..schemas.qr_reconciliation import (
    MAX_RECONCILIATION_ID,
    ReconciliationActionRequest,
)
from . import auth_session_service, order_service
from .qr_webhook_service import serialize_event


ACTION_KEEP_OPEN = "KEEP_OPEN"
ACTION_MAP = "MAP_AND_APPLY"
ACTION_REJECT = "REJECT_NOT_OURS"
ACTION_REFUNDED = "MARK_REFUNDED_EXTERNALLY"
TERMINAL_ACTIONS = frozenset({ACTION_MAP, ACTION_REJECT, ACTION_REFUNDED})
TERMINAL_DISPOSITIONS = frozenset(
    {"APPLIED", "REJECTED_NOT_OURS", "REFUNDED"}
)
AUDIT_ACTION = "BANK_RECONCILIATION"
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000

ERROR_FORBIDDEN = "QR_RECONCILIATION_FORBIDDEN"
ERROR_NOT_FOUND = "QR_RECONCILIATION_EVENT_NOT_FOUND"
ERROR_CONFLICT = "QR_RECONCILIATION_STATE_CONFLICT"
ERROR_INVALID = "QR_RECONCILIATION_ACTION_INVALID"
ERROR_NOTE = "QR_RECONCILIATION_NOTE_INVALID"
ERROR_PERSISTENCE = "QR_RECONCILIATION_PERSISTENCE_FAILED"

_SECRET_WORDS = re.compile(
    r"(?:authorization|bearer|signature|secret|token|raw[_ -]?body|payload)",
    re.IGNORECASE,
)
_HEX_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)
_MIN_FOLDED_IDENTIFIER = 6


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    detail.update(extra)
    return HTTPException(status_code=status, detail=detail)


def _eligible_actor(user: models.User) -> bool:
    return (
        user.role == "ADMIN"
        or user.role == "SELLER"
        or (user.role == "STAFF" and effective_staff_role(user) == "MANAGER")
    )


def require_reconciliation_actor(user: models.User) -> None:
    if not _eligible_actor(user):
        raise _error(
            403,
            ERROR_FORBIDDEN,
            "This account cannot reconcile bank evidence",
        )


def _scoped_event_query(db: Session, user: models.User):
    query = db.query(models.BankWebhookEvent)
    if user.role == "ADMIN":
        return query
    if user.role == "SELLER":
        return query.join(
            models.Shop, models.Shop.id == models.BankWebhookEvent.shop_id
        ).filter(models.Shop.owner_id == user.id)
    return query.filter(models.BankWebhookEvent.shop_id == user.staff_shop_id)


def get_event_model(
    db: Session, user: models.User, event_id: int
) -> models.BankWebhookEvent:
    require_reconciliation_actor(user)
    if (
        type(event_id) is not int
        or event_id < 1
        or event_id > MAX_RECONCILIATION_ID
    ):
        raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
    event = _scoped_event_query(db, user).filter(
        models.BankWebhookEvent.id == event_id
    ).one_or_none()
    if event is None:
        raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
    return event


def get_event(db: Session, user: models.User, event_id: int) -> dict:
    return serialize_event(get_event_model(db, user, event_id))


def list_unapplied_events(
    db: Session,
    user: models.User,
    *,
    limit: int,
    offset: int,
) -> dict:
    require_reconciliation_actor(user)
    if type(limit) is not int or limit < 1 or limit > MAX_PAGE_SIZE:
        raise _error(400, ERROR_INVALID, "Pagination limit is invalid")
    if type(offset) is not int or offset < 0 or offset > MAX_OFFSET:
        raise _error(400, ERROR_INVALID, "Pagination offset is invalid")
    rows = (
        _scoped_event_query(db, user)
        .filter(models.BankWebhookEvent.disposition == "UNAPPLIED")
        .order_by(
            models.BankWebhookEvent.received_at.asc(),
            models.BankWebhookEvent.id.asc(),
        )
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    return {
        "items": [serialize_event(row) for row in visible],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if has_more else None,
    }


def _actor_role(user: models.User, shop_id: int | None) -> str:
    if user.role == "ADMIN":
        return "ADMIN"
    if shop_id is None:
        raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
    if user.role == "SELLER":
        return "OWNER"
    return "MANAGER"


def _has_unsafe_unicode(value: str) -> bool:
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            return True
    return False


def _identifier_fold(value: str) -> str:
    """Comparison-only fold; the durable note is never transformed."""
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(char.casefold() for char in normalized if char.isalnum())


def _canonical_note(
    note: str | None,
    *,
    required: bool,
    event: models.BankWebhookEvent,
) -> str | None:
    if note is None:
        if required:
            raise _error(400, ERROR_NOTE, "A sanitized note is required")
        return None
    if type(note) is not str:
        raise _error(400, ERROR_NOTE, "Reconciliation note is invalid")
    # Reject unsafe code points before trimming so a trailing line separator or
    # format control cannot be silently transformed into an accepted note.
    if _has_unsafe_unicode(note):
        raise _error(400, ERROR_NOTE, "Reconciliation note is invalid")
    value = note.strip()
    if (
        not value
        or len(value) > 500
        or len(value.encode("utf-8")) > 1500
        or _SECRET_WORDS.search(value)
        or _HEX_DIGEST.search(value)
    ):
        raise _error(400, ERROR_NOTE, "Reconciliation note is invalid")
    sensitive = (
        event.normalized_account_no,
        event.normalized_reference,
        event.provider,
        event.provider_event_id,
        event.idempotency_key,
        event.normalized_sha256,
        event.envelope_sha256,
    )
    folded_note = _identifier_fold(value)
    folded_sensitive = (
        _identifier_fold(str(candidate)) for candidate in sensitive if candidate
    )
    if any(
        len(candidate) >= _MIN_FOLDED_IDENTIFIER and candidate in folded_note
        for candidate in folded_sensitive
    ):
        raise _error(400, ERROR_NOTE, "Reconciliation note is invalid")
    return value


def _account_key(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().upper()
    return value.lstrip("0") or "0"


def _provider_collision(db: Session, event: models.BankWebhookEvent) -> bool:
    if event.provider_event_id is None:
        return False
    rows = (
        db.query(
            models.BankWebhookEvent.normalized_sha256,
            models.BankWebhookEvent.envelope_sha256,
        )
        .filter(
            models.BankWebhookEvent.provider == event.provider,
            models.BankWebhookEvent.provider_event_id == event.provider_event_id,
        )
        .all()
    )
    evidence = {(row[0], row[1]) for row in rows}
    return len(evidence) > 1


# ponytail: correction-4 -- row-specific collision recognition for terminalized collisions.
# A collision is recognized by durable ordering: there exists an earlier row with the same
# provider+provider_event_id but different normalized/envelope digest.  This is distinct from
# _provider_collision() which tests all rows in the identity group and is used during
# _validate_map to gate MAP_AND_APPLY.  Here we need row-specific recognition that survives
# ADMIN terminal action (which changes reason_code to REJECT_NOT_OURS/MARK_REFUNDED_EXTERNALLY).
# Root/original events are NOT descendants because no earlier row in the group has a different
# digest (they are the first to define the identity).  Terminalized descendants keep the same
# 409 response as fresh collisions -- the reconciliation action must not be targetable.
def _is_collision_descendant(db: Session, event: models.BankWebhookEvent) -> bool:
    if event.provider_event_id is None:
        return False
    earlier = (
        db.query(models.BankWebhookEvent.id)
        .filter(
            models.BankWebhookEvent.provider == event.provider,
            models.BankWebhookEvent.provider_event_id == event.provider_event_id,
            models.BankWebhookEvent.id < event.id,
            or_(
                models.BankWebhookEvent.normalized_sha256 != event.normalized_sha256,
                models.BankWebhookEvent.envelope_sha256 != event.envelope_sha256,
            ),
        )
        .limit(1)
        .scalar()
    )
    return earlier is not None


def _resolve_target(
    db: Session,
    user: models.User,
    event: models.BankWebhookEvent,
    request: ReconciliationActionRequest,
) -> models.QrPaymentIntent | None:
    if request.target_intent_id is None:
        if event.intent_id is None:
            return None
        return db.query(models.QrPaymentIntent).filter_by(id=event.intent_id).one_or_none()
    if user.role != "ADMIN" and request.target_intent_id != event.intent_id:
        # Never reveal whether a supplied cross-tenant intent exists.
        raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
    # ponytail: correction-3 -- collision + target_intent_id = cross-shop scope leak.
    # A collision is permanently unscoped; ADMIN terminal action is valid only
    # via the no-target path so event/action/audit remain NULL and the lineage
    # fence (shop A) is correctly resolved by the no-target terminal transition.
    # Guard is placed before the intent lookup so it fires for any target_intent_id
    # including boundary values that would not resolve in DB (no DB query needed).
    # ponytail: correction-4 -- check durable ordering, not transient reason_code.
    # _is_collision_descendant uses durable digest evidence, so it stays valid even
    # after ADMIN terminal action has changed reason_code to REJECT_NOT_OURS or
    # MARK_REFUNDED_EXTERNALLY.  This fires before any intent lookup, returns 409
    # for any target_intent_id including boundary values, and is row-specific (root
    #/original events are not descendants because no earlier row exists).
    if _is_collision_descendant(db, event):
        raise _error(
            409,
            ERROR_INVALID,
            "A collision cannot be targeted for reconciliation",
        )
    intent = (
        db.query(models.QrPaymentIntent)
        .filter(
            models.QrPaymentIntent.id == request.target_intent_id,
            models.QrPaymentIntent.contract_version == 1,
        )
        .one_or_none()
    )
    if intent is None:
        raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
    if event.intent_id is not None and event.intent_id != intent.id:
        raise _error(409, ERROR_INVALID, "Evidence mapping is immutable for this action")
    if (
        event.reference_state != "EXACT"
        or event.normalized_reference != intent.canonical_reference
    ):
        raise _error(409, ERROR_INVALID, "Evidence does not match the target intent")
    return intent
    return intent


def _validate_map(
    db: Session,
    event: models.BankWebhookEvent,
    intent: models.QrPaymentIntent | None,
) -> models.Order:
    if intent is None:
        raise _error(409, ERROR_INVALID, "Evidence has no exact intent mapping")
    order = (
        db.query(models.Order)
        .filter(
            models.Order.id == intent.order_id,
            models.Order.shop_id == intent.shop_id,
        )
        .one_or_none()
    )
    valid = (
        order is not None
        and intent.contract_version == 1
        and event.reference_state == "EXACT"
        and event.normalized_reference == intent.canonical_reference
        and event.direction == "IN"
        and type(event.amount_vnd) is int
        and event.amount_vnd > 0
        and event.amount_vnd == intent.expected_vnd
        and _account_key(event.normalized_account_no) == _account_key(intent.account_no)
        and bool(_account_key(event.normalized_account_no))
        and order.payment_method == "transfer"
        and order.offline_uuid is None
        and order.status == order_service.STATUS_PENDING
        and event.reason_code
        not in {
            "PROVIDER_EVENT_COLLISION",
            "ORDER_FINAL_CANCELLED",
            "ORDER_NOT_PENDING",
            "ORDER_PAYMENT_CONFLICT",
            "ORDER_UNAVAILABLE",
        }
        and (
            event.intent_id is None
            or (
                event.intent_id == intent.id
                and event.order_id == intent.order_id
                and event.shop_id == intent.shop_id
            )
        )
    )
    if not valid or _provider_collision(db, event):
        raise _error(409, ERROR_INVALID, "Evidence is not ready for application")

    money_exists = (
        db.query(models.OrderPayment.id)
        .filter(models.OrderPayment.order_id == intent.order_id)
        .first()
        is not None
    )
    idempotency_used = (
        db.query(models.OrderPayment.id)
        .filter(models.OrderPayment.idempotency_key == event.idempotency_key)
        .first()
        is not None
    )
    applied_exists = (
        db.query(models.BankWebhookEvent.id)
        .filter(
            models.BankWebhookEvent.id != event.id,
            or_(
                models.BankWebhookEvent.intent_id == intent.id,
                models.BankWebhookEvent.order_id == intent.order_id,
            ),
            or_(
                models.BankWebhookEvent.disposition == "APPLIED",
                models.BankWebhookEvent.payment_id.is_not(None),
            ),
        )
        .first()
        is not None
    )
    received = int(order.paid_amount or 0) + int(order.cash_paid_amount or 0)
    if money_exists or idempotency_used or applied_exists or received != 0:
        raise _error(409, ERROR_INVALID, "Order already has payment evidence")
    return order


def _performed_at(
    db: Session,
    event: models.BankWebhookEvent,
    action_kind: str,
) -> tuple[datetime, str]:
    # 0007's verifier requires every action timestamp to be >= the event's
    # eventual updated_at.  KEEP_OPEN does not advance the event, so a later
    # terminal decision reuses that durable KEEP_OPEN timestamp.  The terminal
    # trigger accepts it because it is still strictly newer than the original
    # UNAPPLIED event timestamp.
    if action_kind in TERMINAL_ACTIONS:
        kept_at = (
            db.query(models.BankReconciliationAction.performed_at)
            .filter(
                models.BankReconciliationAction.event_id == event.id,
                models.BankReconciliationAction.action_kind == ACTION_KEEP_OPEN,
                models.BankReconciliationAction.event_state_version
                == event.state_version,
            )
            .scalar()
        )
        if kept_at is not None:
            parsed = datetime.strptime(kept_at, "%Y-%m-%d %H:%M:%S.%f")
            return parsed, kept_at
    current = datetime.utcnow()
    previous = datetime.strptime(event.updated_at, "%Y-%m-%d %H:%M:%S.%f")
    if current <= previous:
        current = previous + timedelta(microseconds=1)
    return current, current.strftime("%Y-%m-%d %H:%M:%S.%f")


def _add_order_payment(db: Session, payment: models.OrderPayment) -> None:
    db.add(payment)
    db.flush()


def _add_reconciliation_audit(db: Session, audit: models.SystemLog) -> None:
    db.add(audit)
    db.flush()


def _add_reconciliation_action(
    db: Session, action: models.BankReconciliationAction
) -> None:
    db.add(action)
    db.flush()


def _flush_reconciliation(db: Session) -> None:
    db.flush()


def _commit_reconciliation(db: Session) -> None:
    db.commit()


def _action_response(
    event: models.BankWebhookEvent,
    action: models.BankReconciliationAction,
    *,
    replay: bool,
) -> dict:
    return {
        "event": serialize_event(event),
        "action": {
            "id": int(action.id),
            "kind": action.action_kind,
            "event_state_version": int(action.event_state_version),
            "performed_at": action.performed_at,
        },
        "idempotent_replay": replay,
    }


def _durable_action(
    db: Session,
    *,
    event_id: int,
    request: ReconciliationActionRequest,
) -> models.BankReconciliationAction | None:
    version = (
        request.expected_state_version
        if request.action == ACTION_KEEP_OPEN
        else request.expected_state_version + 1
    )
    return (
        db.query(models.BankReconciliationAction)
        .filter(
            models.BankReconciliationAction.event_id == event_id,
            models.BankReconciliationAction.event_state_version == version,
        )
        .one_or_none()
    )


def _matching_retry(
    action: models.BankReconciliationAction | None,
    *,
    user_id: int,
    request: ReconciliationActionRequest,
    note: str | None,
    intended_intent_id: int | None,
) -> bool:
    return bool(
        action is not None
        and action.action_kind == request.action
        and action.performed_by_user_id == user_id
        and action.note == note
        and action.intent_id == intended_intent_id
    )


def _retry_or_conflict(
    db: Session,
    user: models.User,
    event_id: int,
    request: ReconciliationActionRequest,
    *,
    note: str | None,
    intended_intent_id: int | None,
) -> dict:
    durable = _durable_action(db, event_id=event_id, request=request)
    current = db.query(models.BankWebhookEvent).filter_by(id=event_id).one_or_none()
    if current is not None and _matching_retry(
        durable,
        user_id=user.id,
        request=request,
        note=note,
        intended_intent_id=intended_intent_id,
    ):
        return _action_response(current, durable, replay=True)
    version = int(current.state_version) if current is not None else 0
    raise _error(
        409,
        ERROR_CONFLICT,
        "Bank evidence state changed",
        current_state_version=version,
    )


def reconcile_event(
    db: Session,
    user: models.User,
    event_id: int,
    request: ReconciliationActionRequest,
) -> dict:
    """Run one reconciliation command with a shop lock and one outer commit."""
    initial = get_event_model(db, user, event_id)
    target = _resolve_target(db, user, initial, request)
    intended_intent_id = target.id if target is not None else initial.intent_id
    intended_shop_id = target.shop_id if target is not None else initial.shop_id
    note = _canonical_note(
        request.note,
        required=request.action in {ACTION_REJECT, ACTION_REFUNDED},
        event=initial,
    )
    actor_id = int(user.id)
    actor_database_role = str(user.role)

    # Close any authorization read snapshot, then acquire the common shop row
    # fence.  Truly unscoped admin actions use SQLite's global writer fence.
    db.rollback()
    try:
        if intended_shop_id is None:
            if actor_database_role != "ADMIN":
                raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
            db.execute(text("BEGIN IMMEDIATE"))
            auth_session_service.fence_live_auth_session(db)
        else:
            order_service._lock_shop_for_order(db, int(intended_shop_id))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise _error(
            503,
            ERROR_PERSISTENCE,
            "Reconciliation was not persisted",
        ) from None

    try:
        actor = db.query(models.User).filter_by(id=actor_id).one_or_none()
        if actor is None or not _eligible_actor(actor):
            raise _error(403, ERROR_FORBIDDEN, "This account cannot reconcile bank evidence")
        event = _scoped_event_query(db, actor).filter(
            models.BankWebhookEvent.id == event_id
        ).one_or_none()
        if event is None:
            raise _error(404, ERROR_NOT_FOUND, "Bank evidence was not found")
        target = _resolve_target(db, actor, event, request)
        intended_intent_id = target.id if target is not None else event.intent_id
        intended_shop_id = target.shop_id if target is not None else event.shop_id
        note = _canonical_note(
            request.note,
            required=request.action in {ACTION_REJECT, ACTION_REFUNDED},
            event=event,
        )

        if (
            event.disposition != "UNAPPLIED"
            or int(event.state_version) != request.expected_state_version
        ):
            result = _retry_or_conflict(
                db,
                actor,
                event_id,
                request,
                note=note,
                intended_intent_id=intended_intent_id,
            )
            db.rollback()
            return result
        existing = _durable_action(db, event_id=event_id, request=request)
        if existing is not None:
            result = _retry_or_conflict(
                db,
                actor,
                event_id,
                request,
                note=note,
                intended_intent_id=intended_intent_id,
            )
            db.rollback()
            return result

        if request.action == ACTION_KEEP_OPEN and (
            request.target_intent_id is not None
            and request.target_intent_id != event.intent_id
        ):
            raise _error(409, ERROR_INVALID, "KEEP_OPEN cannot change evidence mapping")

        order: models.Order | None = None
        payment: models.OrderPayment | None = None
        if request.action == ACTION_MAP:
            order = _validate_map(db, event, target)
            payment = models.OrderPayment(
                order_id=order.id,
                entry_type=order_service.ENTRY_BANK,
                amount=int(event.amount_vnd),
                idempotency_key=event.idempotency_key,
                provider=event.provider,
                bank_txn_id=event.provider_event_id,
                account_no=event.normalized_account_no,
                created_by_user_id=actor.id,
                shift_id=None,
                note="I10-C reconciled normalized bank evidence",
                reference=None,
            )
            _add_order_payment(db, payment)
            if not order_service.apply_transition(
                db,
                order.id,
                (order_service.STATUS_PENDING,),
                order_service.STATUS_PAID,
            ):
                raise _error(409, ERROR_CONFLICT, "Order state changed")
            db.expire(order)
            db.refresh(order)
            order.paid_amount = int(event.amount_vnd)
            order.legacy_paid_amount = float(event.amount_vnd)
            order.bank_txn_id = event.provider_event_id
            order.reconciliation_reason = None
            order.refund_due_amount = 0
            order.legacy_refund_due_amount = 0.0
            order_service._award_loyalty_paid_order(db, order, actor.id)

        performed_dt, performed_text = _performed_at(db, event, request.action)
        links = target
        shop_id = links.shop_id if links is not None else event.shop_id
        order_id = links.order_id if links is not None else event.order_id
        audit_details = json.dumps(
            {
                "action": request.action,
                "entity": "bank_webhook_event",
                "event_id": int(event.id),
                "event_state_version": (
                    int(event.state_version)
                    if request.action == ACTION_KEEP_OPEN
                    else int(event.state_version) + 1
                ),
                "intent_id": intended_intent_id,
                "note_present": note is not None,
                "order_id": order_id,
                "shop_id": shop_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        audit = models.SystemLog(
            user_id=actor.id,
            shop_id=shop_id,
            action=AUDIT_ACTION,
            details=audit_details,
            created_at=performed_dt,
        )
        _add_reconciliation_audit(db, audit)

        action_version = (
            int(event.state_version)
            if request.action == ACTION_KEEP_OPEN
            else int(event.state_version) + 1
        )
        action = models.BankReconciliationAction(
            event_id=event.id,
            event_state_version=action_version,
            action_kind=request.action,
            intent_id=intended_intent_id,
            order_id=order_id,
            shop_id=shop_id,
            payment_id=payment.id if payment is not None else None,
            performed_by_user_id=actor.id,
            actor_role=_actor_role(actor, shop_id),
            note=note,
            performed_at=performed_text,
            system_log_id=audit.id,
        )
        _add_reconciliation_action(db, action)
        _flush_reconciliation(db)
        _commit_reconciliation(db)
        db.refresh(event)
        db.refresh(action)
        return _action_response(event, action, replay=False)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        try:
            actor = db.query(models.User).filter_by(id=actor_id).one_or_none()
            if actor is not None:
                result = _retry_or_conflict(
                    db,
                    actor,
                    event_id,
                    request,
                    note=note,
                    intended_intent_id=intended_intent_id,
                )
                db.rollback()
                return result
        except HTTPException as conflict:
            if conflict.detail.get("code") == ERROR_CONFLICT:
                # A durable competing decision is a real CAS conflict.  If no
                # action exists this was a persistence failure, not a race.
                durable = _durable_action(db, event_id=event_id, request=request)
                current = db.query(models.BankWebhookEvent).filter_by(id=event_id).one_or_none()
                if durable is not None or (
                    current is not None
                    and int(current.state_version) != request.expected_state_version
                ):
                    raise
        raise _error(
            503,
            ERROR_PERSISTENCE,
            "Reconciliation was not persisted",
        ) from None


__all__ = [
    "ACTION_KEEP_OPEN",
    "ACTION_MAP",
    "ACTION_REFUNDED",
    "ACTION_REJECT",
    "AUDIT_ACTION",
    "get_event",
    "list_unapplied_events",
    "reconcile_event",
]
