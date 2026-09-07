import hashlib
import uuid

from conftest import auth, create_fnb_area, create_fnb_table, enable_fnb, new_staff, seller_with_shop
from fselling import models


def op(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def sent_session(client, db, station="KITCHEN"):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx)
    table = create_fnb_table(client, ctx, area["id"])
    headers = auth(ctx["token"])
    product = db.get(models.Product, ctx["product"]["id"])
    product.fnb_station = station
    db.commit()
    floor = client.get("/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers).json()
    session = client.post(
        "/api/fnb/sessions",
        json={"shop_id": ctx["shop_id"], "table_id": table["id"],
              "expected_revision": floor["fnb_revision"],
              "expected_table_version": floor["areas"][0]["tables"][0]["state_version"],
              "operation_id": op("open")}, headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={"product_id": product.id, "quantity": 2, "note": None,
              "expected_revision": session["revision"], "operation_id": op("line")},
        headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json={"expected_revision": session["revision"], "operation_id": op("send")},
        headers=headers,
    ).json()
    return ctx, session


def test_cancel_new_sent_item_restores_exact_stock_once(client, db):
    ctx, session = sent_session(client, db)
    line = session["lines"][0]
    payload = {"line_id": line["id"], "quantity": 1,
               "expected_line_version": line["state_version"],
               "expected_revision": session["revision"], "operation_id": op("cancel")}
    response = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json=payload, headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    cancelled = response.json()
    assert cancelled["lines"][0]["sent_cancelled_quantity"] == 1
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 9
    queue = client.get(
        "/api/fnb/stations/KITCHEN/tickets",
        params={"shop_id": ctx["shop_id"]}, headers=auth(ctx["token"]),
    ).json()
    assert queue["tickets"][0]["items"][0]["quantity"] == 1
    assert client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json=payload, headers=auth(ctx["token"]),
    ).json() == cancelled
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 9


def test_in_progress_cancel_requires_bound_one_use_manager_approval(client, db):
    ctx, session = sent_session(client, db)
    ticket = session["tickets"][0]
    started = client.post(
        f"/api/fnb/tickets/{ticket['id']}/start",
        json={
            "expected_state_version": 0,
            "expected_session_revision": session["revision"],
            "operation_id": op("start"),
        },
        headers=auth(ctx["token"]),
    )
    assert started.status_code == 200
    session["revision"] = started.json()["session_revision"]
    pin_set = client.patch(
        f"/api/fnb/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"}, headers=auth(ctx["token"]),
    )
    assert pin_set.status_code == 200, pin_set.text

    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    line = session["lines"][0]
    decision_required = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={
            "line_id": line["id"],
            "quantity": 1,
            "expected_line_version": line["state_version"],
            "expected_revision": session["revision"],
            "operation_id": op("cancel-needs-decision"),
        },
        headers=auth(cashier_token),
    )
    assert decision_required.status_code == 400
    assert decision_required.json()["detail"]["code"] == (
        "FNB_CANCELLATION_DECISION_REQUIRED"
    )
    db.expire_all()
    unchanged = db.get(models.FnbSessionLine, line["id"])
    assert unchanged.sent_cancelled_quantity == 0
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8

    base_cancel = {"line_id": line["id"], "quantity": 1,
                   "expected_line_version": line["state_version"],
                   "expected_revision": session["revision"], "operation_id": op("cancel"),
                   "resolution": "WASTE", "reason": "Món đã chế biến"}
    denied = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json=base_cancel, headers=auth(cashier_token),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "FNB_APPROVAL_REQUIRED"

    approval = client.post(
        "/api/fnb/manager-approvals",
        json={"shop_id": ctx["shop_id"], "approver_username": ctx["username"],
              "pin": "2468", "action": "CANCEL_SENT_LINE",
              "entity_type": "SESSION", "entity_id": session["id"],
              "revision": session["revision"]},
        headers=auth(cashier_token),
    )
    assert approval.status_code == 200, approval.text
    token = approval.json()["approval_token"]
    approved_payload = {**base_cancel, "approval_token": token}
    stale = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={**approved_payload, "expected_revision": session["revision"] - 1},
        headers=auth(cashier_token),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "FNB_SESSION_CHANGED"
    db.expire_all()
    assert db.query(models.FnbManagerApproval).filter_by(
        shop_id=ctx["shop_id"], action="CANCEL_SENT_LINE",
        entity_id=session["id"],
    ).one().used_at is None
    unchanged_line = db.get(models.FnbSessionLine, line["id"])
    assert (unchanged_line.sent_cancelled_quantity, unchanged_line.state_version) == (
        0, line["state_version"]
    )
    unchanged_ticket = db.get(models.FnbKitchenTicket, ticket["id"])
    assert (unchanged_ticket.status, unchanged_ticket.state_version) == (
        "IN_PROGRESS", 1
    )
    unchanged_item = db.query(models.FnbKitchenTicketItem).filter_by(
        session_line_id=line["id"]
    ).one()
    assert (unchanged_item.cancelled_quantity, unchanged_item.quantity) == (0, 2)
    allocation = db.query(models.FnbStockAllocation).filter_by(
        session_line_id=line["id"]
    ).one()
    assert (
        allocation.state,
        allocation.quantity,
        allocation.resolved_at,
        allocation.resolution_reason,
    ) == ("CONSUMED", 2, None, None)
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8

    cancelled = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json=approved_payload, headers=auth(cashier_token),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json=approved_payload, headers=auth(cashier_token),
    ).json() == cancelled.json()
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8
    assert db.query(models.FnbStockAllocation).filter_by(
        shop_id=ctx["shop_id"], state="WASTE"
    ).count() == 1

    reused = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={**approved_payload, "operation_id": op("cancel"),
              "expected_revision": cancelled.json()["revision"],
              "expected_line_version": cancelled.json()["lines"][0]["state_version"]},
        headers=auth(cashier_token),
    )
    assert reused.status_code == 403
    assert reused.json()["detail"]["code"] == "FNB_APPROVAL_INVALID"


def test_served_item_cannot_be_restocked(client, db):
    ctx, session = sent_session(client, db)
    headers = auth(ctx["token"])
    ticket = session["tickets"][0]
    started = client.post(
        f"/api/fnb/tickets/{ticket['id']}/start",
        json={
            "expected_state_version": ticket["state_version"],
            "expected_session_revision": session["revision"],
            "operation_id": op("served-start"),
        },
        headers=headers,
    ).json()
    done = client.post(
        f"/api/fnb/tickets/{ticket['id']}/done",
        json={
            "expected_state_version": started["state_version"],
            "expected_session_revision": started["session_revision"],
            "operation_id": op("served-done"),
        },
        headers=headers,
    ).json()
    served = client.post(
        f"/api/fnb/tickets/{ticket['id']}/serve",
        json={
            "expected_state_version": done["state_version"],
            "expected_session_revision": done["session_revision"],
            "operation_id": op("served-serve"),
        },
        headers=headers,
    ).json()
    assert client.patch(
        f"/api/fnb/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"},
        headers=headers,
    ).status_code == 200
    approval = client.post(
        "/api/fnb/manager-approvals",
        json={
            "shop_id": ctx["shop_id"],
            "approver_username": ctx["username"],
            "pin": "2468",
            "action": "CANCEL_SENT_LINE",
            "entity_type": "SESSION",
            "entity_id": session["id"],
            "revision": served["session_revision"],
        },
        headers=headers,
    ).json()
    line = session["lines"][0]

    rejected = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={
            "line_id": line["id"],
            "quantity": 1,
            "expected_line_version": line["state_version"],
            "expected_revision": served["session_revision"],
            "operation_id": op("served-restock"),
            "resolution": "RESTOCK",
            "reason": "Khách trả món đã nhận",
            "approval_token": approval["approval_token"],
        },
        headers=headers,
    )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "FNB_SERVED_RESTOCK_FORBIDDEN"
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8
    assert db.query(models.FnbStockAllocation).filter_by(
        session_id=session["id"], state="CONSUMED"
    ).count() == 1


def test_manager_pin_failures_are_rate_limited_without_storing_pin(client, db):
    ctx, session = sent_session(client, db)
    assert client.patch(
        f"/api/fnb/shops/{ctx['shop_id']}/manager-pin",
        json={"pin": "2468"}, headers=auth(ctx["token"]),
    ).status_code == 200
    cashier_username, cashier_token = new_staff(client, ctx, "CASHIER")
    payload = {"shop_id": ctx["shop_id"], "approver_username": ctx["username"],
               "pin": "0000", "action": "CANCEL_SENT_LINE",
               "entity_type": "SESSION", "entity_id": session["id"],
               "revision": session["revision"]}
    for _ in range(5):
        assert client.post(
            "/api/fnb/manager-approvals", json=payload, headers=auth(cashier_token)
        ).status_code == 403
    blocked = client.post(
        "/api/fnb/manager-approvals", json=payload, headers=auth(cashier_token)
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "FNB_PIN_RATE_LIMITED"
    audit = db.query(models.FnbManagerApproval).filter_by(
        shop_id=ctx["shop_id"], actor_user_id=db.query(models.User).filter_by(
            username=cashier_username
        ).one().id, action="PIN_FAILED"
    ).all()
    assert len(audit) == 5
    assert all(row.token_hash != hashlib.sha256(b"0000").hexdigest() for row in audit)
