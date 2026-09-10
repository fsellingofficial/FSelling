"""Shared manager PIN and approval primitives for Retail and F&B."""

import hashlib

import pytest
from fastapi import HTTPException

from conftest import auth, new_seller, new_staff, seller_with_shop
from fselling import models
from fselling.core.database import SessionLocal
from fselling.core.security import hash_password
from fselling.services import approval_service


def _user(username):
    session = SessionLocal()
    try:
        return session.query(models.User).filter_by(username=username).one()
    finally:
        session.close()


@pytest.mark.parametrize("actor_role", ["CASHIER", "SERVICE"])
def test_generic_manager_pin_denies_non_manager_staff(client, actor_role):
    ctx = seller_with_shop(client)
    _, token = new_staff(client, ctx, actor_role)

    response = client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "1234"},
        headers=auth(token),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "MANAGER_REQUIRED"


def test_generic_manager_pin_owner_and_manager_set_only_their_own_hash(client):
    ctx = seller_with_shop(client)
    manager_username, manager_token = new_staff(client, ctx, "MANAGER")

    owner_response = client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "1234"},
        headers=auth(ctx["token"]),
    )
    manager_response = client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "5678"},
        headers=auth(manager_token),
    )

    expected = {"shop_id": ctx["shop_id"], "manager_pin_configured": True}
    assert owner_response.status_code == manager_response.status_code == 200
    assert owner_response.json() == manager_response.json() == expected
    assert "hash" not in owner_response.text.lower()
    session = SessionLocal()
    try:
        owner = session.query(models.User).filter_by(username=ctx["username"]).one()
        manager = session.query(models.User).filter_by(username=manager_username).one()
        assert owner.fnb_manager_pin_hash != manager.fnb_manager_pin_hash
        assert owner.fnb_manager_pin_hash != "1234"
        assert manager.fnb_manager_pin_hash != "5678"
    finally:
        session.close()


def test_generic_manager_pin_cross_shop_is_non_enumerating(client):
    ctx = seller_with_shop(client)
    _, other_token = new_seller(client)

    response = client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "1234"},
        headers=auth(other_token),
    )

    assert response.status_code == 403
    assert "pin" not in response.text.lower()


@pytest.mark.parametrize("username,pin", [("missing-user", "1234"), (None, "0000")])
def test_issue_pin_approval_wrong_identity_performs_one_hash_path(
    client, monkeypatch, username, pin
):
    ctx = seller_with_shop(client)
    cashier_username, _ = new_staff(client, ctx, "CASHIER")
    session = SessionLocal()
    calls = {"verify": 0, "burn": 0}
    try:
        owner = session.query(models.User).filter_by(username=ctx["username"]).one()
        actor = session.query(models.User).filter_by(username=cashier_username).one()
        shop = session.get(models.Shop, ctx["shop_id"])
        session.info["auth_session_id"] = (
            session.query(models.AuthSession.session_id)
            .filter(models.AuthSession.user_id == actor.id)
            .order_by(models.AuthSession.created_at.desc())
            .scalar()
        )
        owner.fnb_manager_pin_hash = hash_password("1234")
        session.commit()
        real_verify = approval_service.verify_password
        real_burn = approval_service.burn_password_time
        monkeypatch.setattr(
            approval_service,
            "verify_password",
            lambda candidate, hashed: (
                calls.__setitem__("verify", calls["verify"] + 1)
                or real_verify(candidate, hashed)
            ),
        )
        monkeypatch.setattr(
            approval_service,
            "burn_password_time",
            lambda: calls.__setitem__("burn", calls["burn"] + 1) or real_burn(),
        )

        with pytest.raises(HTTPException) as error:
            approval_service.issue_pin_approval(
                session,
                shop=shop,
                actor=actor,
                approver_username=username or ctx["username"],
                pin=pin,
                action="ORDER_RETURN_EXCEPTION",
                entity_type="ORDER",
                entity_id=1,
                revision=0,
                context_fingerprint="a" * 64,
            )

        assert error.value.detail["code"] == "APPROVAL_PIN_INVALID"
        assert calls["verify"] + calls["burn"] == 1
    finally:
        session.close()


def test_issue_and_consume_approval_hashes_token_and_binds_every_field(client):
    ctx = seller_with_shop(client)
    cashier_username, _ = new_staff(client, ctx, "CASHIER")
    session = SessionLocal()
    try:
        owner = session.query(models.User).filter_by(username=ctx["username"]).one()
        actor = session.query(models.User).filter_by(username=cashier_username).one()
        shop = session.get(models.Shop, ctx["shop_id"])
        session.info["auth_session_id"] = (
            session.query(models.AuthSession.session_id)
            .filter(models.AuthSession.user_id == actor.id)
            .order_by(models.AuthSession.created_at.desc())
            .scalar()
        )
        owner.fnb_manager_pin_hash = hash_password("1234")
        token, approval = approval_service.issue_pin_approval(
            session,
            shop=shop,
            actor=actor,
            approver_username=ctx["username"],
            pin="1234",
            action="ORDER_RETURN_EXCEPTION",
            entity_type="ORDER",
            entity_id=7,
            revision=0,
            context_fingerprint="a" * 64,
        )
        session.flush()
        assert approval.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in approval.token_hash
        assert 299 <= (approval.expires_at - approval.created_at).total_seconds() <= 301

        consumed = approval_service.consume_approval(
            session,
            token=token,
            shop_id=ctx["shop_id"],
            actor_user_id=actor.id,
            action="ORDER_RETURN_EXCEPTION",
            entity_type="ORDER",
            entity_id=7,
            revision=0,
            context_fingerprint="a" * 64,
        )
        assert consumed.id == approval.id
        assert consumed.used_at is not None
        with pytest.raises(HTTPException) as reused:
            approval_service.consume_approval(
                session,
                token=token,
                shop_id=ctx["shop_id"],
                actor_user_id=actor.id,
                action="ORDER_RETURN_EXCEPTION",
                entity_type="ORDER",
                entity_id=7,
                revision=0,
                context_fingerprint="a" * 64,
            )
        assert reused.value.detail["code"] == "APPROVAL_INVALID"
    finally:
        session.rollback()
        session.close()
