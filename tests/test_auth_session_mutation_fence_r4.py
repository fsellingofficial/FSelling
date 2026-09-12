"""R4: phiên bị thu hồi không được lọt qua hàng rào ghi giao dịch."""

from contextlib import contextmanager

import pytest
from conftest import admin_token, auth, new_staff, seller_with_shop
from fastapi import HTTPException
from sqlalchemy import select, text

from fselling import models
from fselling.core.database import SessionLocal
from fselling.schemas.expense import ExpenseCreate
from fselling.schemas.shift import ShiftOpen
from fselling.schemas.supplier import SupplierPaymentCreate
from fselling.services import (
    auth_session_service,
    catalog_service,
    expense_service,
    order_service,
    qr_reconciliation_service,
    shift_service,
    supplier_service,
    voucher_service,
)


@contextmanager
def _revoked_request_db(ctx):
    request_db = SessionLocal()
    revoke_db = SessionLocal()
    try:
        user = request_db.query(models.User).filter_by(username=ctx["username"]).one()
        session = request_db.query(models.AuthSession).filter_by(user_id=user.id).one()
        auth_session_service.bind_request_session(request_db, session)
        auth_session_service.revoke_all_user_sessions(
            revoke_db,
            target_user_id=user.id,
            actor_user_id=user.id,
            reason="TEST_REVOKE",
        )
        revoke_db.commit()
        yield request_db, user
    finally:
        request_db.rollback()
        request_db.close()
        revoke_db.close()


def test_revoked_session_cannot_create_order_after_shop_lock(client, monkeypatch):
    ctx = seller_with_shop(client)
    real_lock = order_service._lock_shop_for_order

    def revoke_then_lock(db, shop_id):
        db.execute(
            text("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE session_id = :sid"),
            {"sid": db.info["auth_session_id"]},
        )
        real_lock(db, shop_id)

    monkeypatch.setattr(order_service, "_lock_shop_for_order", revoke_then_lock)
    response = client.post(
        f"/api/orders/{ctx['shop_id']}",
        json={
            "items": [{
                "product_name": ctx["product"]["name"],
                "price": 100000,
                "quantity": 1,
            }],
            "payment_method": "transfer",
        },
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"
    with SessionLocal() as db:
        assert db.query(models.Order).filter_by(shop_id=ctx["shop_id"]).count() == 0
        assert db.get(models.Product, ctx["product"]["id"]).stock == 10


def test_authenticated_system_log_keeps_auth_session_attribution(client):
    ctx = seller_with_shop(client)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username=ctx["username"]).one()
        auth_session = db.query(models.AuthSession).filter_by(user_id=user.id).one()
        log = (
            db.query(models.SystemLog)
            .filter_by(user_id=user.id, action="CREATE_PRODUCT")
            .order_by(models.SystemLog.id.desc())
            .first()
        )
        assert log is not None
        assert log.auth_session_id == auth_session.session_id


def test_replacement_session_retry_keeps_business_idempotency(client):
    ctx = seller_with_shop(client)
    payload = {
        "items": [{
            "product_name": ctx["product"]["name"],
            "price": 100000,
            "quantity": 1,
        }],
        "payment_method": "transfer",
        "operation_id": "same-business-operation-r4",
    }
    first = client.post(
        f"/api/orders/{ctx['shop_id']}", json=payload, headers=auth(ctx["token"])
    )
    replacement = client.post(
        "/api/auth/login",
        json={
            "username": ctx["username"],
            "password": "Seller@2026",
            "device_id": "replacement-device-r4",
            "device_name": "Replacement",
            "device_type": "DESKTOP",
        },
    ).json()["access_token"]

    retry = client.post(
        f"/api/orders/{ctx['shop_id']}", json=payload, headers=auth(replacement)
    )
    changed = client.post(
        f"/api/orders/{ctx['shop_id']}",
        json={**payload, "items": [{**payload["items"][0], "quantity": 2}]},
        headers=auth(replacement),
    )

    assert retry.status_code == 200
    assert retry.json()["order_id"] == first.json()["order_id"]
    assert changed.status_code == 409
    with SessionLocal() as db:
        assert db.query(models.Order).filter_by(shop_id=ctx["shop_id"]).count() == 1
        assert db.get(models.Product, ctx["product"]["id"]).stock == 9


def test_revoked_session_cannot_open_cash_shift_after_authentication(client):
    ctx = seller_with_shop(client)
    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            shift_service.open_shift(
                request_db,
                user,
                ctx["shop_id"],
                ShiftOpen(opening_cash_amount=123456),
            )
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.query(models.CashShift).filter_by(shop_id=ctx["shop_id"]).count() == 0


def test_revoked_session_cannot_pay_supplier(client):
    ctx = seller_with_shop(client)
    created = client.post(
        f"/api/suppliers/{ctx['shop_id']}",
        json={
            "name": "NCC fence R4",
            "opening_balance": 100_000,
            "operation_id": "supplier-fence-create-r4",
        },
        headers=auth(ctx["token"]),
    )
    assert created.status_code == 200, created.text
    supplier_id = created.json().get("supplier", created.json())["id"]

    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            supplier_service.create_supplier_payment(
                request_db,
                user,
                supplier_id,
                SupplierPaymentCreate(
                    amount=50_000,
                    method="TRANSFER",
                    operation_id="supplier-fence-payment-r4",
                ),
            )
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.query(models.SupplierPayment).filter_by(supplier_id=supplier_id).count() == 0


def test_revoked_session_cannot_create_transfer_expense(client):
    ctx = seller_with_shop(client)
    categories = client.get(
        f"/api/expense-categories/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()["categories"]
    category_id = categories[0]["id"]

    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            expense_service.create_expense(
                request_db,
                user,
                ctx["shop_id"],
                ExpenseCreate(
                    category_id=category_id,
                    amount=75_000,
                    method="TRANSFER",
                    operation_id="expense-create-fence-r4",
                ),
            )
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.query(models.OperatingExpense).filter_by(shop_id=ctx["shop_id"]).count() == 0


def test_revoked_session_cannot_void_expense(client):
    ctx = seller_with_shop(client)
    categories = client.get(
        f"/api/expense-categories/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()["categories"]
    created = client.post(
        f"/api/expenses/{ctx['shop_id']}",
        json={
            "category_id": categories[0]["id"],
            "amount": 80_000,
            "method": "TRANSFER",
            "operation_id": "expense-void-create-r4",
        },
        headers=auth(ctx["token"]),
    )
    assert created.status_code == 200, created.text
    expense_id = created.json().get("expense", created.json())["id"]

    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            expense_service.void_expense(request_db, user, ctx["shop_id"], expense_id)
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.get(models.OperatingExpense, expense_id).voided_at is None


def test_revoked_session_cannot_create_product(client):
    ctx = seller_with_shop(client)
    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            catalog_service.create_product(
                request_db,
                user,
                ctx["shop_id"],
                "Blocked product R4",
                12_000,
                3,
                ctx["category_id"],
            )
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.query(models.Product).filter_by(name="Blocked product R4").count() == 0


def test_revoked_session_cannot_update_product(client):
    ctx = seller_with_shop(client)
    old_name = ctx["product"]["name"]
    with _revoked_request_db(ctx) as (request_db, user):
        with pytest.raises(HTTPException) as error:
            catalog_service.update_product(
                request_db,
                user,
                ctx["product"]["id"],
                "Blocked rename R4",
                123_000,
                ctx["category_id"],
            )
        assert error.value.status_code == 401

    with SessionLocal() as db:
        assert db.get(models.Product, ctx["product"]["id"]).name == old_name


def _business_snapshot():
    """Compare every money/stock/voucher/loyalty/audit field, not just counts."""
    tables = (
        models.Order, models.OrderItem, models.OrderItemBatch, models.OrderPayment,
        models.CashShift, models.CashMovement, models.Voucher, models.SystemLog,
        models.Customer, models.LoyaltyPointEntry, models.Product, models.ProductBatch,
        models.BankWebhookEvent, models.BankReconciliationAction, models.QrPaymentIntent,
    )
    with SessionLocal() as db:
        return {
            model.__tablename__: db.execute(
                select(model.__table__).order_by(model.id)
            ).all()
            for model in tables
        }


def _revoke_at_service_entry(monkeypatch, service, name):
    real = getattr(service, name)
    called = []

    def revoked(db, *args, **kwargs):
        # The actual API authentication has already bound a live session.
        sid = db.info["auth_session_id"]
        assert db.info["auth_session_required"]
        with SessionLocal() as other:
            result = other.execute(
                text("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP "
                     "WHERE session_id = :sid AND revoked_at IS NULL"),
                {"sid": sid},
            )
            assert result.rowcount == 1
            other.commit()
        called.append(sid)
        return real(db, *args, **kwargs)

    monkeypatch.setattr(service, name, revoked)
    return called


@pytest.mark.parametrize("action,role,shift", [
    ("pay", "SELLER", False), ("pay", "SELLER", True),
    ("pay", "MANAGER", False), ("pay", "CASHIER", True),
    ("cash-topup", "SELLER", True),
    ("refund-cash", "SELLER", True), ("refund-transfer", "SELLER", False),
    ("debt-payment", "SELLER", False), ("cancel", "SELLER", False),
    ("cancel", "ADMIN", False),
])
def test_revoke_after_api_auth_rolls_back_money_and_stock(client, monkeypatch, action, role, shift):
    from test_loyalty_shop_policy import PROGRAM
    from test_vouchers import _tao_voucher

    ctx = seller_with_shop(client)
    headers = auth(ctx["token"])
    _tao_voucher(client, ctx, code="FENCE", discount_value=10000)
    saved = client.put(f"/api/loyalty/{ctx['shop_id']}", json=PROGRAM, headers=headers)
    assert saved.status_code == 200, saved.text
    with SessionLocal() as db:
        customer = models.Customer(shop_id=ctx["shop_id"], name="Fence customer", phone="0901234567")
        db.add(customer)
        db.commit()
        customer_id = customer.id
    created = client.post(f"/api/orders/{ctx['shop_id']}", headers=headers, json={
        "items": [{"product_id": ctx["product"]["id"], "product_name": ctx["product"]["name"],
                   "price": 1, "quantity": 1}],
        "payment_method": "cash" if action == "pay" else "transfer",
        "customer_id": customer_id, "voucher_code": "FENCE",
    })
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]
    if role == "ADMIN":
        headers = auth(admin_token(client))
    elif role != "SELLER":
        _, token = new_staff(client, ctx, staff_role=role)
        headers = auth(token)
    if shift:
        opened = client.post(f"/api/shifts/{ctx['shop_id']}/open", headers=headers,
                             json={"opening_cash_amount": 200000})
        assert opened.status_code == 200, opened.text
    # Seed reconciliation states without any bank/provider call.
    with SessionLocal() as db:
        order = db.get(models.Order, order_id)
        if action == "cash-topup":
            order.status, order.reconciliation_reason, order.paid_amount = "UNRECONCILED", "UNDERPAID", 40000
        elif action.startswith("refund-"):
            order.status, order.reconciliation_reason = "PAID", "OVERPAID"
            order.paid_amount, order.refund_due_amount = 100000, 10000
        elif action == "debt-payment":
            order.status, order.payment_method = "DEBT", "debt"
        if role == "ADMIN":
            db.execute(text("DELETE FROM shops WHERE id = :id"), {"id": ctx["shop_id"]})
        db.commit()
    endpoint = "refund-complete" if action.startswith("refund-") else action
    service_name = {"pay": "pay_order", "cash-topup": "cash_topup", "cancel": "cancel_order",
                    "debt-payment": "debt_payment", "refund-complete": "complete_refund"}[endpoint]
    payload = {
        "pay": {"tendered_amount": 150000},
        "cash-topup": {"amount": 50000},
        "cancel": {},
        "debt-payment": {"amount": 90000, "method": "transfer", "operation_id": f"fence-debt-rc-{order_id}"},
        "refund-complete": {"method": action.removeprefix("refund-"), "operation_id": f"fence-refund-rc-{order_id}"},
    }[endpoint]
    before = _business_snapshot()
    called = _revoke_at_service_entry(monkeypatch, order_service, service_name)
    response = client.post(f"/api/orders/{order_id}/{endpoint}", headers=headers, json=payload)
    assert len(called) == 1, response.text
    assert _business_snapshot() == before
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_revoke_after_api_auth_rolls_back_exposed_voucher_mutations(client, monkeypatch, method):
    from test_vouchers import _tao_voucher

    ctx = seller_with_shop(client)
    voucher = _tao_voucher(client, ctx, code="BEFORE")
    endpoint = f"/api/vouchers/{voucher['id']}" if method != "post" else "/api/vouchers"
    before = _business_snapshot()
    name = {"post": "create_voucher", "put": "update_voucher", "delete": "delete_voucher"}[method]
    called = _revoke_at_service_entry(monkeypatch, voucher_service, name)
    kwargs = {"headers": auth(ctx["token"])}
    if method != "delete":
        kwargs["json"] = {"code": "AFTER", "discount_type": "flat", "discount_value": 20000}
    if method == "post":
        kwargs["params"] = {"shop_id": ctx["shop_id"]}
    response = getattr(client, method)(endpoint, **kwargs)
    assert len(called) == 1, response.text
    assert _business_snapshot() == before
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"


@pytest.mark.parametrize("action", ["KEEP_OPEN", "REJECT_NOT_OURS", "MARK_REFUNDED_EXTERNALLY"])
def test_revoke_after_api_auth_blocks_unscoped_admin_reconciliation(client, monkeypatch, action):
    from i10c_helpers import create_intent_context
    from test_qr_reconciliation import _action, _collision

    ctx = create_intent_context(client, monkeypatch)
    event = _collision(client, monkeypatch, ctx, f"revoked-admin-{ctx['order_id']}")
    token = admin_token(client)
    before = _business_snapshot()
    called = _revoke_at_service_entry(monkeypatch, qr_reconciliation_service, "reconcile_event")
    response = _action(client, token, event["id"], action, note="Verified review decision")
    assert len(called) == 1, response.text
    assert _business_snapshot() == before
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"
