"""Plan 4 manager approvals are bound to the actor auth session."""

import pytest
from fastapi import HTTPException

from conftest import STAFF_PASSWORD, auth, new_staff, seller_with_shop

from fselling import models
from fselling.core.database import SessionLocal
from fselling.services import approval_service
from test_return_approval_r3 import _draft
from test_tra_hang import _ban, _dong_don, _mo_ca, _tao_sp


def _login_device(client, username, device_id):
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": STAFF_PASSWORD,
            "device_id": device_id,
            "device_name": device_id,
            "device_type": "MOBILE",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_approval_token_cannot_be_consumed_from_another_actor_session(client):
    ctx = seller_with_shop(client)
    staff_username, _ = new_staff(client, ctx, "CASHIER")
    actor_a = _login_device(client, staff_username, "actor-a")
    actor_b = _login_device(client, staff_username, "actor-b")
    assert client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "1234"},
        headers=auth(ctx["token"]),
    ).status_code == 200

    with SessionLocal() as db:
        owner = db.query(models.User).filter_by(username=ctx["username"]).one()
        actor = db.query(models.User).filter_by(username=staff_username).one()
        shop = db.get(models.Shop, ctx["shop_id"])
        db.info["auth_session_id"] = actor_a["session"]["session_id"]
        token, approval = approval_service.issue_pin_approval(
            db,
            shop=shop,
            actor=actor,
            approver_username=owner.username,
            pin="1234",
            action="ORDER_RETURN_EXCEPTION",
            entity_type="ORDER",
            entity_id=91,
            revision=0,
            context_fingerprint="a" * 64,
        )
        db.commit()
        assert approval.actor_auth_session_id == actor_a["session"]["session_id"]

        db.info["auth_session_id"] = actor_b["session"]["session_id"]
        with pytest.raises(HTTPException) as error:
            approval_service.consume_approval(
                db,
                token=token,
                shop_id=shop.id,
                actor_user_id=actor.id,
                action="ORDER_RETURN_EXCEPTION",
                entity_type="ORDER",
                entity_id=91,
                revision=0,
                context_fingerprint="a" * 64,
            )
        assert error.value.detail["code"] == "APPROVAL_INVALID"
        db.refresh(approval)
        assert approval.used_at is None


def test_manager_pin_change_invalidates_outstanding_approvals(client):
    ctx = seller_with_shop(client)
    staff_username, _ = new_staff(client, ctx, "CASHIER")
    actor = _login_device(client, staff_username, "pin-actor")
    assert client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "1234"},
        headers=auth(ctx["token"]),
    ).status_code == 200

    with SessionLocal() as db:
        owner = db.query(models.User).filter_by(username=ctx["username"]).one()
        staff = db.query(models.User).filter_by(username=staff_username).one()
        shop = db.get(models.Shop, ctx["shop_id"])
        db.info["auth_session_id"] = actor["session"]["session_id"]
        token, approval = approval_service.issue_pin_approval(
            db,
            shop=shop,
            actor=staff,
            approver_username=owner.username,
            pin="1234",
            action="CANCEL_SENT_LINE",
            entity_type="SESSION",
            entity_id=13,
            revision=2,
        )
        db.commit()
        approval_id = approval.id

    changed = client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "5678"},
        headers=auth(ctx["token"]),
    )
    assert changed.status_code == 200
    with SessionLocal() as db:
        approval = db.get(models.FnbManagerApproval, approval_id)
        assert approval.used_at is not None
        db.info["auth_session_id"] = actor["session"]["session_id"]
        with pytest.raises(HTTPException) as error:
            approval_service.consume_approval(
                db,
                token=token,
                shop_id=ctx["shop_id"],
                actor_user_id=approval.actor_user_id,
                action="CANCEL_SENT_LINE",
                entity_type="SESSION",
                entity_id=13,
                revision=2,
            )
        assert error.value.detail["code"] == "APPROVAL_INVALID"


def test_cash_return_approval_is_invalid_after_shift_changes(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    shift_id = _mo_ca(client, ctx, token=cashier_token)
    assert client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    ).status_code == 200
    draft = _draft(line["id"], restock=False, method="cash")

    required = client.post(
        f"/api/orders/{order_id}/returns",
        json=draft.model_dump(),
        headers=auth(cashier_token),
    )
    assert required.status_code == 403, required.text
    approval_context = required.json()["detail"]["approval_context"]
    assert approval_context["cash_shift_id"] == shift_id
    approved = client.post(
        f"/api/orders/{order_id}/returns/approval",
        json={
            **draft.model_dump(),
            "context_fingerprint": approval_context["context_fingerprint"],
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(cashier_token),
    )
    assert approved.status_code == 200, approved.text

    closed = client.post(
        f"/api/shifts/{shift_id}/close",
        json={"counted_cash_amount": 500_000},
        headers=auth(cashier_token),
    )
    assert closed.status_code == 200, closed.text
    new_shift_id = _mo_ca(client, ctx, token=cashier_token)
    assert new_shift_id != shift_id

    rejected = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": approved.json()["approval_token"]},
        headers=auth(cashier_token),
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "RETURN_CONTEXT_CHANGED"
