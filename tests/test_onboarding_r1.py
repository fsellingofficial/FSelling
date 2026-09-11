"""Onboarding R1: durable first-value facts and navigation-only UI."""
from pathlib import Path
from datetime import datetime

from conftest import auth, create_shop, new_seller, new_staff

from fselling import models
from fselling.core.database import SessionLocal

ROOT = Path(__file__).resolve().parent.parent


def _state(client, shop_id, token):
    return client.get(f"/api/onboarding/{shop_id}", headers=auth(token))


def _owner_shop(client):
    username, token = new_seller(client)
    return {"username": username, "token": token, "shop_id": create_shop(client, token)}


def _add_order(session, shop_id, status, *, user_id=None, shift_id=None, product_id=None):
    order = models.Order(
        shop_id=shop_id,
        total_amount=10_000,
        legacy_total_amount=10_000,
        status=status,
        created_by_user_id=user_id,
        shift_id=shift_id,
    )
    session.add(order)
    session.flush()
    session.add(models.OrderItem(
        order_id=order.id,
        product_id=product_id,
        product_name="Sản phẩm đầu tiên",
        quantity=1,
        price=10_000,
        legacy_price=10_000,
        discount_vnd=0,
        loyalty_discount_vnd=0,
        net_amount_vnd=10_000,
        cost_known_qty=0,
        cost_unknown_qty=1,
        cost_basis_vnd=0,
    ))
    return order


def test_onboarding_progress_comes_only_from_durable_business_state(client):
    owner = _owner_shop(client)
    response = _state(client, owner["shop_id"], owner["token"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "shop_created": True,
        "product_created": False,
        "shift_opened": False,
        "sale_completed": False,
        "shift_closed": False,
        "completed_steps": 1,
        "total_steps": 5,
        "complete": False,
    }

    session = SessionLocal()
    try:
        user = session.query(models.User).filter(models.User.username == owner["username"]).one()
        category = models.Category(name="Khởi đầu", shop_id=owner["shop_id"])
        session.add(category)
        session.flush()
        product = models.Product(
            name="Sản phẩm đầu tiên",
            code=f"ONBOARD-{owner['shop_id']}",
            price=10_000,
            legacy_price=10_000,
            stock=1,
            cost_unknown_qty=1,
            category_id=category.id,
            shop_id=owner["shop_id"],
        )
        session.add(product)
        shift = models.CashShift(
            shop_id=owner["shop_id"],
            status="OPEN",
            opening_cash_amount=0,
            legacy_opening_cash_amount=0,
            opened_by_user_id=user.id,
        )
        session.add(shift)
        session.flush()
        _add_order(
            session,
            owner["shop_id"],
            "PAID",
            user_id=user.id,
            shift_id=shift.id,
            product_id=product.id,
        )
        session.commit()
    finally:
        session.close()

    middle = _state(client, owner["shop_id"], owner["token"]).json()
    assert middle["completed_steps"] == 4
    assert middle["product_created"] is True
    assert middle["shift_opened"] is True
    assert middle["sale_completed"] is True
    assert middle["shift_closed"] is False
    assert middle["complete"] is False

    session = SessionLocal()
    try:
        shift = session.query(models.CashShift).filter(
            models.CashShift.shop_id == owner["shop_id"]
        ).one()
        shift.status = "CLOSED"
        shift.counted_cash_amount = 0
        shift.legacy_counted_cash_amount = 0
        shift.expected_cash_amount = 0
        shift.legacy_expected_cash_amount = 0
        shift.variance_amount = 0
        shift.legacy_variance_amount = 0
        shift.closed_by_user_id = shift.opened_by_user_id
        shift.closed_at = datetime.utcnow()
        session.commit()
    finally:
        session.close()

    complete = _state(client, owner["shop_id"], owner["token"]).json()
    assert complete["completed_steps"] == 5
    assert complete["shift_closed"] is True
    assert complete["complete"] is True


def test_pending_and_cancelled_orders_do_not_complete_first_sale(client):
    owner = _owner_shop(client)
    session = SessionLocal()
    try:
        for status in ("PENDING", "CANCELLED"):
            _add_order(session, owner["shop_id"], status)
        session.commit()
    finally:
        session.close()

    assert _state(client, owner["shop_id"], owner["token"]).json()["sale_completed"] is False


def test_onboarding_is_owner_only_and_shop_scoped(client):
    owner = _owner_shop(client)
    other = _owner_shop(client)
    _, staff_token = new_staff(client, owner)

    assert _state(client, owner["shop_id"], staff_token).status_code == 403
    assert _state(client, owner["shop_id"], other["token"]).status_code == 403
    assert _state(client, 999_999, owner["token"]).status_code == 404


def test_onboarding_get_is_read_only(client):
    owner = _owner_shop(client)
    session = SessionLocal()
    try:
        before = {
            "products": session.query(models.Product).count(),
            "shifts": session.query(models.CashShift).count(),
            "orders": session.query(models.Order).count(),
            "logs": session.query(models.SystemLog).count(),
        }
    finally:
        session.close()

    assert _state(client, owner["shop_id"], owner["token"]).status_code == 200

    session = SessionLocal()
    try:
        after = {
            "products": session.query(models.Product).count(),
            "shifts": session.query(models.CashShift).count(),
            "orders": session.query(models.Order).count(),
            "logs": session.query(models.SystemLog).count(),
        }
    finally:
        session.close()
    assert after == before


def test_onboarding_board_and_its_hidden_frontend_fetch_are_removed():
    html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    locale = (ROOT / "static/js/locales/seller.js").read_text(encoding="utf-8")

    assert 'id="onboardingPanel"' not in html
    assert 'id="onboardingSteps"' not in html
    assert "ONBOARDING_STEPS" not in js
    assert "loadOnboarding" not in js
    assert "openOnboardingStep" not in js
    assert "seller.onboarding." not in locale
    version = "20260911-r5-5"
    assert f"/js/locales/seller.js?v={version}" in html
    assert f"/js/seller.js?v={version}" in html
