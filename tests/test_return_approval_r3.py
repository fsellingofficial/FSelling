"""Server-authoritative return policy and bound approval context."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import uuid

import pytest
from fastapi import HTTPException

from conftest import auth, new_staff, seller_with_shop
from fselling import models
from fselling.core import thoi_gian
from fselling.core.database import SessionLocal
from fselling.schemas.order import OrderReturnDraft
from fselling.services import loyalty_service, order_service, return_service
from test_i05_cost_provenance import _adjust, _return_atomic_state
from test_tich_diem import _create_customer, _create_order, _pay_cash, _save_program
from test_tra_hang import _ban, _dong_don, _mo_ca, _tao_sp


def _op():
    return uuid.uuid4().hex


def _draft(line_id, *, restock=True, method="cash", reason="Khách trả hàng", **kw):
    return OrderReturnDraft(
        items=[{"order_item_id": line_id, "quantity": 1, "restock": restock}],
        method=method,
        reason=reason,
        operation_id=kw.pop("operation_id", _op()),
        **kw,
    )


def _prepared(ctx, order_id, draft):
    session = SessionLocal()
    try:
        actor = session.query(models.User).filter_by(username=ctx["username"]).one()
        order = session.get(models.Order, order_id)
        order_service._lock_shop_for_order(session, order.shop_id)
        session.refresh(order)
        return return_service._prepare_return_context(session, actor, order, draft)
    finally:
        session.rollback()
        session.close()


def _set_payment_method(order_id, method):
    session = SessionLocal()
    try:
        session.get(models.Order, order_id).payment_method = method
        if method != "cash":
            session.query(models.OrderPayment).filter_by(order_id=order_id).delete()
        session.commit()
    finally:
        session.close()


def test_return_schema_requires_explicit_condition_and_service_requires_reason(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])

    missing_condition = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [{"order_item_id": line["id"], "quantity": 1}],
            "method": "cash",
            "reason": "Khách trả hàng",
            "operation_id": _op(),
        },
        headers=auth(ctx["token"]),
    )
    missing_reason = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [
                {"order_item_id": line["id"], "quantity": 1, "restock": True}
            ],
            "method": "cash",
            "reason": "   ",
            "operation_id": _op(),
        },
        headers=auth(ctx["token"]),
    )

    assert missing_condition.status_code == 422
    assert missing_reason.status_code == 400
    assert missing_reason.json()["detail"]["code"] == "RETURN_REASON_REQUIRED"


def test_return_approval_validation_never_echoes_pin(client):
    ctx = seller_with_shop(client)
    response = client.post(
        "/api/orders/1/returns/approval",
        json={
            "items": [{"order_item_id": 1, "quantity": 1, "restock": False}],
            "method": "cash",
            "reason": "Hàng hỏng",
            "operation_id": _op(),
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 422
    assert "2468" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_return_validation_never_echoes_approval_token(client):
    ctx = seller_with_shop(client)
    token = "sensitive-approval-token-1234567890"
    response = client.post(
        "/api/orders/1/returns",
        json={
            "method": "cash",
            "reason": "Hàng hỏng",
            "operation_id": _op(),
            "approval_token": token,
        },
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 422
    assert token not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize(
    "sale_method,ledger_type,refund_method,expected_source,reason_code",
    [
        ("cash", None, "cash", "cash-only", None),
        ("transfer", "BANK_IN", "transfer", "transfer-only", None),
        ("cash", "BANK_IN", "cash", "ambiguous", "AMBIGUOUS_SOURCE"),
        ("transfer", "CASH_TOPUP", "transfer", "ambiguous", "AMBIGUOUS_SOURCE"),
        ("debt", "DEBT_CASH", "cash", "debt", "DEBT_SOURCE"),
        ("cash", "RETURN_CASH", "cash", "cash-only", None),
        ("cash", "UNKNOWN_IN", "cash", "ambiguous", "AMBIGUOUS_SOURCE"),
    ],
)
def test_source_classification_is_conservative(
    client, sale_method, ledger_type, refund_method, expected_source, reason_code
):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    _set_payment_method(order_id, sale_method)
    line = _dong_don(client, ctx, order_id, product["id"])
    if ledger_type:
        session = SessionLocal()
        try:
            session.add(
                models.OrderPayment(
                    order_id=order_id,
                    entry_type=ledger_type,
                    amount=1,
                )
            )
            session.commit()
        finally:
            session.close()

    context = _prepared(
        ctx,
        order_id,
        _draft(
            line["id"],
            method=refund_method,
            reference="BANK-REF" if refund_method == "transfer" else None,
        ),
    )

    assert context["source_class"] == expected_source
    assert (reason_code in context["reason_codes"]) is bool(reason_code)


@pytest.mark.parametrize(
    "restock,refund_method,reason_code",
    [(False, "cash", "NON_RESTOCK"), (True, "transfer", "METHOD_CHANGE")],
)
def test_return_exception_reason_codes(client, restock, refund_method, reason_code):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])

    context = _prepared(
        ctx,
        order_id,
        _draft(
            line["id"],
            restock=restock,
            method=refund_method,
            reference="BANK-REF" if refund_method == "transfer" else None,
        ),
    )

    assert reason_code in context["reason_codes"]


def test_zero_refund_restock_is_ordinary(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    voucher = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={"code": "FREE100", "discount_type": "percentage", "discount_value": 100},
        headers=auth(ctx["token"]),
    )
    assert voucher.status_code == 200, voucher.text
    order_id = _ban(client, ctx, [(product, 1)], method="cash", voucher="FREE100")
    line = _dong_don(client, ctx, order_id, product["id"])

    context = _prepared(ctx, order_id, _draft(line["id"], method=None))

    assert context["tien_hoan"] == 0
    assert context["reason_codes"] == ()


def test_positive_transfer_refund_requires_reference_without_mutation(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    _set_payment_method(order_id, "transfer")
    line = _dong_don(client, ctx, order_id, product["id"])

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json=_draft(line["id"], method="transfer").model_dump(),
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "RETURN_REFERENCE_REQUIRED"
    session = SessionLocal()
    try:
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == 0
        assert session.get(models.Product, product["id"]).stock == 1
    finally:
        session.close()


def test_context_fingerprint_is_order_independent_and_changes_with_material_state(client):
    ctx = seller_with_shop(client)
    first = _tao_sp(client, ctx, gia_ban=40_000, ton=2, gia_von=20_000)
    second = _tao_sp(client, ctx, gia_ban=60_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(first, 1), (second, 1)], method="cash")
    line_a = _dong_don(client, ctx, order_id, first["id"])
    line_b = _dong_don(client, ctx, order_id, second["id"])
    operation_id = _op()
    common = {
        "method": "cash",
        "reason": " Khách trả hàng ",
        "operation_id": operation_id,
    }
    draft_ab = OrderReturnDraft(
        items=[
            {"order_item_id": line_a["id"], "quantity": 1, "restock": True},
            {"order_item_id": line_b["id"], "quantity": 1, "restock": False},
        ],
        **common,
    )
    draft_ba = OrderReturnDraft(items=list(reversed(draft_ab.items)), **common)
    base = _prepared(ctx, order_id, draft_ab)["context_fingerprint"]
    assert _prepared(ctx, order_id, draft_ba)["context_fingerprint"] == base

    changed = _draft(
        line_a["id"],
        operation_id=operation_id,
        reason="Lý do khác",
    )
    assert _prepared(ctx, order_id, changed)["context_fingerprint"] != base

    session = SessionLocal()
    try:
        row = session.get(models.OrderItem, line_a["id"])
        row.cost_return_version = 1
        session.commit()
    finally:
        session.close()
    assert _prepared(ctx, order_id, draft_ab)["context_fingerprint"] != base


def test_return_approval_endpoint_recomputes_context_and_only_adds_approval(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    assert client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    ).status_code == 200
    cashier_ctx = {**ctx, "username": cashier_username}
    draft = _draft(line["id"], restock=False)
    fingerprint = _prepared(cashier_ctx, order_id, draft)["context_fingerprint"]

    response = client.post(
        f"/api/orders/{order_id}/returns/approval",
        json={
            **draft.model_dump(),
            "context_fingerprint": fingerprint,
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(cashier_token),
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "approval_token",
        "expires_in_seconds",
        "approval_context",
    }
    assert response.json()["approval_context"]["reason_codes"] == ["NON_RESTOCK"]
    assert "2468" not in response.text
    session = SessionLocal()
    try:
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == 0
        assert session.query(models.OrderPayment).filter_by(
            order_id=order_id, entry_type="RETURN_CASH"
        ).count() == 0
        assert session.get(models.Product, product["id"]).stock == 1
        assert session.query(models.FnbManagerApproval).filter_by(
            action="ORDER_RETURN_EXCEPTION", entity_id=order_id
        ).count() == 1
    finally:
        session.close()


def test_return_approval_endpoint_rejects_changed_context(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    _, cashier_token = new_staff(client, ctx, "CASHIER")
    draft = _draft(line["id"], restock=False)

    response = client.post(
        f"/api/orders/{order_id}/returns/approval",
        json={
            **draft.model_dump(),
            "context_fingerprint": "f" * 64,
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(cashier_token),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RETURN_CONTEXT_CHANGED"


def _approve(client, ctx, actor_token, actor_username, order_id, draft):
    fingerprint = _prepared(
        {**ctx, "username": actor_username}, order_id, draft
    )["context_fingerprint"]
    return client.post(
        f"/api/orders/{order_id}/returns/approval",
        json={
            **draft.model_dump(),
            "context_fingerprint": fingerprint,
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(actor_token),
    )


def test_exceptional_return_requires_approval_for_cashier(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    _, cashier_token = new_staff(client, ctx, "CASHIER")
    draft = _draft(
        line["id"], method="transfer", reference="BANK-REF"
    )

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json=draft.model_dump(),
        headers=auth(cashier_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "RETURN_APPROVAL_REQUIRED"
    assert response.json()["detail"]["approval_context"]["reason_codes"] == [
        "METHOD_CHANGE"
    ]


def test_owner_exception_creates_bound_used_evidence(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    draft = _draft(line["id"], restock=False, method="transfer", reference="BANK-REF")

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json=draft.model_dump(),
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 200, response.text
    assert "approval_token" not in response.text
    approval_id = response.json()["return"]["manager_approval_id"]
    session = SessionLocal()
    try:
        approval = session.get(models.FnbManagerApproval, approval_id)
        actor = session.query(models.User).filter_by(username=ctx["username"]).one()
        assert approval.used_at is not None
        assert approval.approver_user_id == approval.actor_user_id == actor.id
        assert approval.context_fingerprint
    finally:
        session.close()


def test_approval_token_is_consumed_atomically_and_retry_needs_no_second_token(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    assert client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    ).status_code == 200
    draft = _draft(line["id"], method="transfer", reference="BANK-REF")
    approved = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    )
    assert approved.status_code == 200, approved.text
    token = approved.json()["approval_token"]

    accepted = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": token},
        headers=auth(cashier_token),
    )
    retry = client.post(
        f"/api/orders/{order_id}/returns",
        json=draft.model_dump(),
        headers=auth(cashier_token),
    )

    assert accepted.status_code == retry.status_code == 200
    assert accepted.json()["return"]["id"] == retry.json()["return"]["id"]
    approval_id = accepted.json()["return"]["manager_approval_id"]
    session = SessionLocal()
    try:
        approval = session.get(models.FnbManagerApproval, approval_id)
        assert approval.used_at is not None
        assert session.query(models.FnbManagerApproval).filter_by(
            action="ORDER_RETURN_EXCEPTION", entity_id=order_id
        ).count() == 1
    finally:
        session.close()


def test_changed_draft_rejects_previously_issued_token(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(
        line["id"], restock=False, method="transfer", reference="BANK-REF"
    )
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]

    changed = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            **draft.model_dump(),
            "reason": "Lý do đã đổi",
            "approval_token": token,
        },
        headers=auth(cashier_token),
    )

    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "RETURN_CONTEXT_CHANGED"


def test_competing_return_exhausting_line_reports_context_changed_and_keeps_token(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(line["id"], restock=False)
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]
    competing = client.post(
        f"/api/orders/{order_id}/returns",
        json=_draft(
            line["id"], restock=False, method="transfer", reference="BANK-OTHER"
        ).model_dump(),
        headers=auth(ctx["token"]),
    )
    assert competing.status_code == 200, competing.text

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": token},
        headers=auth(cashier_token),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RETURN_CONTEXT_CHANGED"
    session = SessionLocal()
    try:
        approval = session.query(models.FnbManagerApproval).filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).one()
        assert approval.used_at is None
    finally:
        session.close()


def test_competing_return_exhausting_line_rejects_stale_approval_preview(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(line["id"], restock=False)
    fingerprint = _prepared(
        {**ctx, "username": cashier_username}, order_id, draft
    )["context_fingerprint"]
    competing = client.post(
        f"/api/orders/{order_id}/returns",
        json=_draft(
            line["id"], restock=False, method="transfer", reference="BANK-OTHER"
        ).model_dump(),
        headers=auth(ctx["token"]),
    )
    assert competing.status_code == 200, competing.text

    response = client.post(
        f"/api/orders/{order_id}/returns/approval",
        json={
            **draft.model_dump(),
            "context_fingerprint": fingerprint,
            "approver_username": ctx["username"],
            "pin": "2468",
        },
        headers=auth(cashier_token),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RETURN_CONTEXT_CHANGED"


@pytest.mark.parametrize(
    "mismatch", ["token", "actor", "shop", "order", "action", "expired"]
)
def test_return_api_rejects_wrong_approval_binding_without_side_effect(client, mismatch):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(
        line["id"], restock=False, method="transfer", reference="BANK-REF"
    )
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]
    submitted_token = "x" * 43 if mismatch == "token" else token
    other_shop_id = seller_with_shop(client)["shop_id"] if mismatch == "shop" else None
    session = SessionLocal()
    try:
        approval = session.query(models.FnbManagerApproval).filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).one()
        if mismatch == "actor":
            approval.actor_user_id = approval.approver_user_id
        elif mismatch == "shop":
            approval.shop_id = other_shop_id
        elif mismatch == "order":
            approval.entity_id = order_id + 1_000_000
        elif mismatch == "action":
            approval.action = "CANCEL_SENT_LINE"
        elif mismatch == "expired":
            approval.expires_at = approval.created_at - timedelta(seconds=1)
        session.commit()
        before_logs = session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count()
    finally:
        session.close()
    before = _return_atomic_state(order_id, line["id"], product["id"])

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": submitted_token},
        headers=auth(cashier_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "RETURN_APPROVAL_INVALID"
    assert _return_atomic_state(order_id, line["id"], product["id"]) == before
    session = SessionLocal()
    try:
        approval = session.query(models.FnbManagerApproval).filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).one()
        assert approval.used_at is None
        assert session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count() == before_logs
    finally:
        session.close()


def test_failure_before_commit_rolls_back_approval_money_inventory_and_counters(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(line["id"], method="transfer", reference="BANK-REF")
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]

    with monkeypatch.context() as patch:
        patch.setattr(
            order_service,
            "_them_nhat_ky",
            lambda *_args, **_kw: (_ for _ in ()).throw(
                HTTPException(status_code=503, detail="injected before commit")
            ),
        )
        failed = client.post(
            f"/api/orders/{order_id}/returns",
            json={**draft.model_dump(), "approval_token": token},
            headers=auth(cashier_token),
        )
    assert failed.status_code == 503

    session = SessionLocal()
    try:
        approval = session.query(models.FnbManagerApproval).filter_by(
            action="ORDER_RETURN_EXCEPTION", entity_id=order_id
        ).one()
        order_line = session.get(models.OrderItem, line["id"])
        assert approval.used_at is None
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == 0
        assert session.query(models.OrderPayment).filter_by(
            order_id=order_id, entry_type="RETURN_TRANSFER"
        ).count() == 0
        assert session.get(models.Product, product["id"]).stock == 1
        assert (order_line.returned_total_qty, order_line.cost_return_version) == (0, 0)
    finally:
        session.close()

    retry = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": token},
        headers=auth(cashier_token),
    )
    assert retry.status_code == 200, retry.text


def test_cash_loyalty_batch_return_failure_rolls_back_with_approval(client, monkeypatch):
    ctx = seller_with_shop(client)
    created = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": f"Batch return {uuid.uuid4().hex}",
            "price": 50_000,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert created.status_code == 200, created.text
    product = created.json()
    _adjust(
        client,
        ctx,
        product["id"],
        1,
        cost=30_000,
        expiry=(thoi_gian.hom_nay_vn() + timedelta(days=30)).isoformat(),
    )
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    order = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        product=product,
        method="cash",
        operation_id=_op(),
    )
    assert order.status_code == 200, order.text
    order_id = order.json()["order_id"]
    _pay_cash(client, ctx, order_id)
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    shift_id = _mo_ca(client, ctx, token=cashier_token)
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    session = SessionLocal()
    try:
        session.add(
            models.OrderPayment(
                order_id=order_id,
                entry_type="BANK_IN",
                amount=1,
                created_by_user_id=session.query(models.User).filter_by(
                    username=ctx["username"]
                ).one().id,
            )
        )
        session.commit()
    finally:
        session.close()
    draft = _draft(line["id"], restock=True, method="cash")
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]
    before = _return_atomic_state(order_id, line["id"], product["id"])
    session = SessionLocal()
    try:
        before_logs = session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count()
        shift = session.get(models.CashShift, shift_id)
        before_shift = (shift.status, shift.opening_cash_amount)
    finally:
        session.close()

    calls = {"shift": 0, "loyalty": 0, "batch": 0}
    real_shift = order_service._current_cash_shift
    real_loyalty = loyalty_service.add_entry
    real_batch = return_service._conditional_advance_batch_return

    def traced_shift(*args, **kwargs):
        calls["shift"] += 1
        return real_shift(*args, **kwargs)

    def traced_loyalty(*args, **kwargs):
        calls["loyalty"] += 1
        return real_loyalty(*args, **kwargs)

    def traced_batch(*args, **kwargs):
        calls["batch"] += 1
        return real_batch(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(order_service, "_current_cash_shift", traced_shift)
        patch.setattr(loyalty_service, "add_entry", traced_loyalty)
        patch.setattr(return_service, "_conditional_advance_batch_return", traced_batch)
        patch.setattr(
            order_service,
            "_them_nhat_ky",
            lambda *_args, **_kw: (_ for _ in ()).throw(
                HTTPException(status_code=503, detail="injected before commit")
            ),
        )
        failed = client.post(
            f"/api/orders/{order_id}/returns",
            json={**draft.model_dump(), "approval_token": token},
            headers=auth(cashier_token),
        )

    assert failed.status_code == 503
    assert calls == {"shift": 1, "loyalty": 1, "batch": 1}
    assert _return_atomic_state(order_id, line["id"], product["id"]) == before
    session = SessionLocal()
    try:
        approval = session.query(models.FnbManagerApproval).filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).one()
        shift = session.get(models.CashShift, shift_id)
        assert approval.used_at is None
        assert (shift.status, shift.opening_cash_amount) == before_shift
        assert session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count() == before_logs
    finally:
        session.close()


def test_ordinary_return_ignores_supplied_token_and_creates_no_approval(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    _, cashier_token = new_staff(client, ctx, "CASHIER")
    _mo_ca(client, ctx, token=cashier_token)
    draft = _draft(line["id"], method="cash")

    response = client.post(
        f"/api/orders/{order_id}/returns",
        json={**draft.model_dump(), "approval_token": "x" * 32},
        headers=auth(cashier_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["return"]["manager_approval_id"] is None
    session = SessionLocal()
    try:
        assert session.query(models.FnbManagerApproval).filter_by(
            entity_id=order_id, action="ORDER_RETURN_EXCEPTION"
        ).count() == 0
    finally:
        session.close()


def test_concurrent_same_approval_and_operation_has_one_durable_return(client):
    ctx = seller_with_shop(client)
    product = _tao_sp(client, ctx, gia_ban=50_000, ton=2, gia_von=30_000)
    order_id = _ban(client, ctx, [(product, 1)], method="cash")
    line = _dong_don(client, ctx, order_id, product["id"])
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    client.patch(
        f"/api/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=auth(ctx["token"]),
    )
    draft = _draft(line["id"], method="transfer", reference="BANK-REF")
    token = _approve(
        client, ctx, cashier_token, cashier_username, order_id, draft
    ).json()["approval_token"]
    payload = {**draft.model_dump(), "approval_token": token}

    def submit():
        return client.post(
            f"/api/orders/{order_id}/returns",
            json=payload,
            headers=auth(cashier_token),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _marker: submit(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert len({response.json()["return"]["id"] for response in responses}) == 1
    session = SessionLocal()
    try:
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == 1
        approval = session.query(models.FnbManagerApproval).filter_by(
            action="ORDER_RETURN_EXCEPTION", entity_id=order_id
        ).one()
        assert approval.used_at is not None
        assert session.get(models.Product, product["id"]).stock == 2
    finally:
        session.close()
