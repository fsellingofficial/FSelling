import uuid

import pytest
from sqlalchemy import select, text

from conftest import auth
from fselling import models
from fselling.core.database import SessionLocal
from fselling.services import fnb_service, loyalty_service

from test_fnb_r1c_checks import op, sent_session


def _closed_debt_settled_externally(client):
    ctx, headers, session = sent_session(client, 1, station="DIRECT")
    check = client.get(f"/api/fnb/sessions/{session['id']}/checks", headers=headers).json()["checks"][0]
    customer = client.post(f"/api/customers/{ctx['shop_id']}", headers=headers,
                           json={"name": "Closed debt", "phone": f"09{uuid.uuid4().int % 10**8:08d}"})
    assert customer.status_code == 200, customer.text
    paid = client.post(f"/api/fnb/checks/{check['id']}/pay", headers=headers, json={
        "payment_method": "debt", "customer_id": customer.json()["id"],
        "expected_revision": check["revision"], "expected_session_revision": session["revision"],
        "operation_id": op("closed-debt"),
    })
    assert paid.status_code == 200, paid.text
    closed = client.post(f"/api/fnb/sessions/{session['id']}/close", headers=headers, json={
        "expected_revision": paid.json()["session_revision"], "operation_id": op("close-debt"),
    })
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    order_id = paid.json()["order"]["id"]
    settled = client.post(f"/api/orders/{order_id}/debt-payment", headers=headers, json={
        "method": "transfer", "amount": 100000, "operation_id": op("external-debt"),
    })
    assert settled.status_code == 200, settled.text
    return ctx, headers, session["id"], check["id"]


def _fnb_sync_snapshot():
    with SessionLocal() as db:
        return {
            model.__tablename__: db.execute(select(model.__table__).order_by(model.id)).all()
            for model in (models.FnbServiceSession, models.FnbServiceCheck, models.FnbActionLog,
                          models.FnbSessionTable, models.FnbTable, models.Shop)
        }


@pytest.mark.parametrize("terminal", ["CLOSED", "CANCELLED"])
def test_terminal_debt_session_stays_terminal_after_external_settlement(client, terminal):
    ctx, headers, session_id, check_id = _closed_debt_settled_externally(client)
    with SessionLocal() as db:
        session = db.get(models.FnbServiceSession, session_id)
        # CLOSED is the exact public journey. CANCELLED covers the other model
        # terminal state for historical/imported checks still linked to orders.
        session.status = terminal
        db.commit()
        closed_identity = (session.closed_at, session.closed_by_user_id, session.merged_into_session_id)
        revision = session.revision
        check_revision = db.get(models.FnbServiceCheck, check_id).revision
        floor_revision = db.get(models.Shop, ctx["shop_id"]).fnb_revision
    before = _fnb_sync_snapshot()
    response = client.get(f"/api/fnb/sessions/{session_id}/checks", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["checks"][0]["status"] == "PAID"
    with SessionLocal() as db:
        session = db.get(models.FnbServiceSession, session_id)
        assert session.status == terminal
        assert (session.closed_at, session.closed_by_user_id, session.merged_into_session_id) == closed_identity
        assert session.revision == revision + 1
        assert db.get(models.FnbServiceCheck, check_id).revision == check_revision + 1
        assert db.get(models.Shop, ctx["shop_id"]).fnb_revision == floor_revision + 1
        assert db.query(models.FnbSessionTable).filter_by(session_id=session_id, released_at=None).count() == 0
    after = _fnb_sync_snapshot()
    for table in ("fnb_action_logs", "fnb_session_tables", "fnb_tables"):
        assert after[table] == before[table]
    assert client.get(f"/api/fnb/sessions/{session_id}/checks", headers=headers).json() == response.json()
    assert _fnb_sync_snapshot() == after


def test_checks_sync_rechecks_revoked_session_before_writing(client, monkeypatch):
    from test_auth_session_mutation_fence_r4 import _business_snapshot, _revoke_at_service_entry

    _, headers, session_id, _ = _closed_debt_settled_externally(client)
    before = (_business_snapshot(), _fnb_sync_snapshot())
    called = _revoke_at_service_entry(monkeypatch, fnb_service, "get_checks")
    response = client.get(f"/api/fnb/sessions/{session_id}/checks", headers=headers)
    assert len(called) == 1
    assert (_business_snapshot(), _fnb_sync_snapshot()) == before
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"


def test_checks_sync_discards_stale_read_before_write_lock(client, monkeypatch):
    _, headers, session_id, _ = _closed_debt_settled_externally(client)
    real_access = fnb_service._session_for_access
    revisions = []

    def stale_read(db, *args, **kwargs):
        session = real_access(db, *args, **kwargs)
        if not revisions:
            revisions.append(session.revision)
            with SessionLocal() as other:
                other.execute(text("UPDATE fnb_service_sessions SET status = 'CANCELLED', "
                                   "revision = revision + 1 WHERE id = :id"), {"id": session_id})
                other.commit()
        return session

    monkeypatch.setattr(fnb_service, "_session_for_access", stale_read)
    response = client.get(f"/api/fnb/sessions/{session_id}/checks", headers=headers)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        session = db.get(models.FnbServiceSession, session_id)
        assert session.status == "CANCELLED"
        assert session.revision == revisions[0] + 2


def test_close_rejects_active_kitchen_ticket_without_releasing_table(client, db):
    _, headers, session = sent_session(client, 1, station="KITCHEN")
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("active-ticket-pay"),
        },
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    result = paid.json()

    rejected = client.post(
        f"/api/fnb/sessions/{session['id']}/close",
        json={
            "expected_revision": result["session_revision"],
            "operation_id": op("active-ticket-close"),
        },
        headers=headers,
    )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "FNB_ACTIVE_TICKETS"
    db.expire_all()
    assert db.get(models.FnbServiceSession, session["id"]).status != "CLOSED"
    assert db.query(models.FnbSessionTable).filter_by(
        session_id=session["id"], released_at=None
    ).count() == 1


def test_cash_checkout_requires_explicit_tender_without_side_effects(client, db):
    ctx, headers, session = sent_session(client, 1)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    product = db.get(models.Product, ctx["product"]["id"])
    before_stock = product.stock
    before_orders = db.query(models.Order).count()
    before_logs = db.query(models.FnbActionLog).count()
    before_check_revision = primary["revision"]
    before_session_revision = session["revision"]

    response = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "expected_revision": before_check_revision,
            "expected_session_revision": before_session_revision,
            "operation_id": op("cash-missing-tender"),
        },
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == {
        "code": "FNB_CASH_TENDERED_REQUIRED",
        "message": "Cần nhập số tiền khách đã đưa",
        "required": 100_000,
    }
    db.expire_all()
    assert db.query(models.Order).count() == before_orders
    assert db.query(models.FnbActionLog).count() == before_logs
    assert db.get(models.Product, product.id).stock == before_stock
    check = db.get(models.FnbServiceCheck, primary["id"])
    assert check.status == "OPEN"
    assert check.revision == before_check_revision
    assert db.get(models.FnbServiceSession, session["id"]).revision == before_session_revision


def test_cash_checkout_rejects_short_tender_without_side_effects(client, db):
    ctx, headers, session = sent_session(client, 1)
    shift = client.post(
        f"/api/shifts/{ctx['shop_id']}/open",
        json={"opening_cash_amount": 50_000, "note": "Ca kiểm tender thiếu"},
        headers=headers,
    ).json()
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    product = db.get(models.Product, ctx["product"]["id"])
    before_stock = product.stock
    before_orders = db.query(models.Order).count()
    before_payments = db.query(models.OrderPayment).count()
    before_movements = db.query(models.CashMovement).count()
    before_logs = db.query(models.FnbActionLog).count()
    before_check_revision = primary["revision"]
    before_session_revision = session["revision"]
    before_shift = db.get(models.CashShift, shift["id"])
    before_status = before_shift.status
    before_opening_cash = before_shift.opening_cash_amount
    before_expected_cash = before_shift.expected_cash_amount

    response = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "cash_tendered_vnd": primary["total_vnd"] - 1,
            "expected_revision": before_check_revision,
            "expected_session_revision": before_session_revision,
            "operation_id": op("cash-short-tender"),
        },
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == {
        "code": "FNB_CASH_SHORT",
        "message": "Tiền khách đưa chưa đủ",
        "required": 100_000,
    }
    db.expire_all()
    assert db.query(models.Order).count() == before_orders
    assert db.query(models.OrderPayment).count() == before_payments
    assert db.query(models.CashMovement).count() == before_movements
    assert db.query(models.FnbActionLog).count() == before_logs
    assert db.get(models.Product, product.id).stock == before_stock
    check = db.get(models.FnbServiceCheck, primary["id"])
    assert check.status == "OPEN"
    assert check.revision == before_check_revision
    assert db.get(models.FnbServiceSession, session["id"]).revision == before_session_revision
    after_shift = db.get(models.CashShift, shift["id"])
    assert after_shift.status == before_status
    assert after_shift.opening_cash_amount == before_opening_cash
    assert after_shift.expected_cash_amount == before_expected_cash


def test_transfer_with_cash_tender_is_rejected_without_side_effects(client, db):
    ctx, headers, session = sent_session(client, 1)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    product = db.get(models.Product, ctx["product"]["id"])
    before_stock = product.stock
    before_orders = db.query(models.Order).count()
    before_payments = db.query(models.OrderPayment).count()
    before_logs = db.query(models.FnbActionLog).count()

    response = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "transfer",
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("transfer-cash-tender"),
        },
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "FNB_CASH_TENDERED_INVALID"
    db.expire_all()
    assert db.query(models.Order).count() == before_orders
    assert db.query(models.OrderPayment).count() == before_payments
    assert db.query(models.FnbActionLog).count() == before_logs
    assert db.get(models.Product, product.id).stock == before_stock
    assert db.get(models.FnbServiceCheck, primary["id"]).status == "OPEN"
    assert db.get(models.FnbServiceCheck, primary["id"]).revision == primary["revision"]
    assert db.get(models.FnbServiceSession, session["id"]).revision == session["revision"]


def test_debt_with_cash_tender_is_rejected_without_side_effects(client, db):
    ctx, headers, session = sent_session(client, 1)
    customer = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={"name": "Khách nợ tender", "phone": f"09{uuid.uuid4().int % 10**8:08d}"},
        headers=headers,
    ).json()
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    product = db.get(models.Product, ctx["product"]["id"])
    before_stock = product.stock
    before_orders = db.query(models.Order).count()
    before_payments = db.query(models.OrderPayment).count()
    before_logs = db.query(models.FnbActionLog).count()

    response = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "debt",
            "customer_id": customer["id"],
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("debt-cash-tender"),
        },
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "FNB_CASH_TENDERED_INVALID"
    db.expire_all()
    assert db.query(models.Order).count() == before_orders
    assert db.query(models.OrderPayment).count() == before_payments
    assert db.query(models.FnbActionLog).count() == before_logs
    assert db.get(models.Product, product.id).stock == before_stock
    assert db.get(models.FnbServiceCheck, primary["id"]).status == "OPEN"
    assert db.get(models.FnbServiceCheck, primary["id"]).revision == primary["revision"]
    assert db.get(models.FnbServiceSession, session["id"]).revision == session["revision"]


def test_cash_checkout_transfers_provenance_once_and_closes_table(client, db):
    ctx, headers, session = sent_session(client, 2, station="KITCHEN")
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    payload = {
        "payment_method": "cash",
        "cash_tendered_vnd": 250000,
        "expected_revision": primary["revision"],
        "expected_session_revision": session["revision"],
        "operation_id": op("cash-pay"),
    }
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=payload, headers=headers
    )
    assert paid.status_code == 200, paid.text
    result = paid.json()
    assert result["check"]["status"] == "PAID"
    assert result["order"]["status"] == "PAID"
    assert result["order"]["cash_change_vnd"] == 50000

    retry = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=payload, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json() == result

    db.expire_all()
    order = db.get(models.Order, result["order"]["id"])
    assert order.total_amount == 200000
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8
    assert db.query(models.OrderItem).filter_by(order_id=order.id).one().quantity == 2
    transfer = db.query(models.FnbAllocationTransfer).filter_by(
        check_id=primary["id"]
    ).one()
    assert transfer.quantity == 2
    allocation = db.get(models.FnbStockAllocation, transfer.allocation_id)
    assert allocation.state == "TRANSFERRED_TO_ORDER"

    ticket = db.query(models.FnbKitchenTicket).filter_by(session_id=session["id"]).one()
    started = client.post(
        f"/api/fnb/tickets/{ticket.id}/start",
        json={
            "expected_state_version": ticket.state_version,
            "expected_session_revision": result["session_revision"],
            "operation_id": op("start"),
        },
        headers=headers,
    )
    assert started.status_code == 200, started.text
    done = client.post(
        f"/api/fnb/tickets/{ticket.id}/done",
        json={
            "expected_state_version": started.json()["state_version"],
            "expected_session_revision": started.json()["session_revision"],
            "operation_id": op("done"),
        },
        headers=headers,
    )
    assert done.status_code == 200, done.text

    blocked = client.post(
        f"/api/fnb/sessions/{session['id']}/close",
        json={
            "expected_revision": done.json()["session_revision"],
            "operation_id": op("close-ready"),
        },
        headers=headers,
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "FNB_UNSERVED_TICKETS"

    served = client.post(
        f"/api/fnb/tickets/{ticket.id}/serve",
        json={
            "expected_state_version": done.json()["state_version"],
            "expected_session_revision": done.json()["session_revision"],
            "operation_id": op("serve"),
        },
        headers=headers,
    )
    assert served.status_code == 200, served.text

    closed = client.post(
        f"/api/fnb/sessions/{session['id']}/close",
        json={
            "expected_revision": served.json()["session_revision"],
            "operation_id": op("close"),
        },
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    floor = client.get(
        "/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers
    ).json()
    assert floor["areas"][0]["tables"][0]["state"] == "EMPTY"


def test_transfer_stays_pending_until_order_is_confirmed(client, db):
    _, headers, session = sent_session(client, 1)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    pending = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "transfer",
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("transfer-pay"),
        },
        headers=headers,
    )
    assert pending.status_code == 200, pending.text
    result = pending.json()
    assert result["check"]["status"] == "PAYMENT_PENDING"
    assert result["order"]["status"] == "PENDING"
    blocked = client.post(
        f"/api/fnb/sessions/{session['id']}/close",
        json={
            "expected_revision": result["session_revision"],
            "operation_id": op("close-pending"),
        },
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "FNB_UNSETTLED_CHECKS"

    order = db.get(models.Order, result["order"]["id"])
    order.status = "PAID"
    db.commit()
    refreshed = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()
    assert refreshed["checks"][0]["status"] == "PAID"


def test_debt_checkout_requires_customer_and_is_terminal(client):
    ctx, headers, session = sent_session(client, 1)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    base = {
        "payment_method": "debt",
        "expected_revision": primary["revision"],
        "expected_session_revision": session["revision"],
        "operation_id": op("debt-missing"),
    }
    missing = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=base, headers=headers
    )
    assert missing.status_code == 400

    customer = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={"name": "Khách ghi nợ", "phone": f"09{uuid.uuid4().int % 10**8:08d}"},
        headers=headers,
    ).json()
    base.update(customer_id=customer["id"], operation_id=op("debt-pay"))
    debt = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=base, headers=headers
    )
    assert debt.status_code == 200, debt.text
    assert debt.json()["check"]["status"] == "DEBT"
    assert debt.json()["order"]["status"] == "DEBT"


def test_cancelled_sent_quantity_is_removed_from_open_check(client, db):
    ctx, headers, session = sent_session(client, 2)
    line = session["lines"][0]
    cancelled = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={
            "line_id": line["id"],
            "quantity": 1,
            "expected_line_version": line["state_version"],
            "expected_revision": session["revision"],
            "operation_id": op("cancel-before-pay"),
        },
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    checks = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()
    primary = checks["checks"][0]
    assert primary["lines"][0]["quantity"] == 1
    assert primary["total_vnd"] == 100000
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": checks["session_revision"],
            "operation_id": op("pay-after-cancel"),
        },
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 9


def test_split_checks_pay_once_each_and_conserve_stock_provenance(client, db):
    ctx, headers, session = sent_session(client, 3)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    split = client.post(
        f"/api/fnb/checks/{primary['id']}/split",
        json={
            "lines": [{"line_id": primary["lines"][0]["line_id"], "quantity": 1}],
            "label": "Khách 2",
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("split-pay"),
        },
        headers=headers,
    ).json()
    revision = split["session_revision"]
    order_ids = []
    for check in split["checks"]:
        paid = client.post(
            f"/api/fnb/checks/{check['id']}/pay",
            json={
                "payment_method": "cash",
                "cash_tendered_vnd": check["total_vnd"],
                "expected_revision": check["revision"],
                "expected_session_revision": revision,
                "operation_id": op(f"pay-{check['id']}"),
            },
            headers=headers,
        )
        assert paid.status_code == 200, paid.text
        revision = paid.json()["session_revision"]
        order_ids.append(paid.json()["order"]["id"])

    db.expire_all()
    transfers = db.query(models.FnbAllocationTransfer).filter(
        models.FnbAllocationTransfer.check_id.in_([row["id"] for row in split["checks"]])
    ).all()
    assert sum(row.quantity for row in transfers) == 3
    assert len({row.order_item_id for row in transfers}) == 2
    assert db.get(models.Product, ctx["product"]["id"]).stock == 7
    assert db.query(models.Order).filter(models.Order.id.in_(order_ids)).count() == 2


def test_partially_settled_session_sends_new_items_to_supplemental_check(client, db):
    ctx, headers, session = sent_session(client, 2)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    split = client.post(
        f"/api/fnb/checks/{primary['id']}/split",
        json={
            "lines": [{"line_id": primary["lines"][0]["line_id"], "quantity": 1}],
            "label": "Khách 2",
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("partial-split"),
        },
        headers=headers,
    ).json()
    primary = next(row for row in split["checks"] if row["is_primary"])
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": split["session_revision"],
            "operation_id": op("partial-pay"),
        },
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    paid_result = paid.json()
    paid_order_id = paid_result["order"]["id"]
    paid_total = paid_result["order"]["total_vnd"]

    added = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": ctx["product"]["id"],
            "quantity": 1,
            "expected_revision": paid_result["session_revision"],
            "operation_id": op("partial-add"),
        },
        headers=headers,
    )
    assert added.status_code == 200, added.text
    added_session = added.json()
    new_line = added_session["lines"][-1]
    send_payload = {
        "expected_revision": added_session["revision"],
        "operation_id": op("partial-send"),
    }
    sent = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json=send_payload,
        headers=headers,
    )
    assert sent.status_code == 200, sent.text
    retry = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json=send_payload,
        headers=headers,
    )
    assert retry.json() == sent.json()

    checks = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"]
    old = next(row for row in checks if row["id"] == primary["id"])
    supplemental = next(row for row in checks if row["is_primary"])
    assert old["status"] == "PAID"
    assert old["order_id"] == paid_order_id
    assert old["total_vnd"] == paid_total
    assert supplemental["id"] != old["id"]
    assert supplemental["label"].startswith("Bill bổ sung")
    assert supplemental["status"] == "OPEN"
    assert [(row["line_id"], row["quantity"]) for row in supplemental["lines"]] == [
        (new_line["id"], 1)
    ]
    assert supplemental["total_vnd"] == new_line["unit_price_vnd"]
    assert db.get(models.Order, paid_order_id).total_amount == paid_total


def test_paid_fnb_order_reuses_receipt_history_and_cash_shift(client):
    ctx, headers, session = sent_session(client, 1)
    opened = client.post(
        f"/api/shifts/{ctx['shop_id']}/open",
        json={"opening_cash_amount": 50_000, "note": "Đầu ca F&B"},
        headers=headers,
    )
    assert opened.status_code == 200, opened.text
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={
            "payment_method": "cash",
            "cash_tendered_vnd": primary["total_vnd"],
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("receipt-shift"),
        },
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    order_id = paid.json()["order"]["id"]

    detail = client.get(f"/api/orders/{order_id}/detail", headers=headers).json()
    assert detail["fnb_table_names"] == ["Bàn 1"]
    assert detail["fnb_check_label"] == "Bill chính"

    history = client.get(
        f"/api/orders/{ctx['shop_id']}/history",
        params={"q": "Bàn 1"},
        headers=headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["orders"][0]["id"] == order_id
    assert history.json()["orders"][0]["fnb_table_names"] == ["Bàn 1"]

    shift = client.get(
        f"/api/shifts/current/{ctx['shop_id']}", headers=headers
    ).json()["shift"]
    assert shift["cash_payment_in_amount"] == 100_000
    assert shift["expected_cash_amount"] == 150_000


def test_fnb_checkout_reuses_voucher_and_loyalty_contract_once(client, db):
    ctx, headers, session = sent_session(client, 2)
    program = client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json={
            "enabled": True,
            "earn_amount": 1_000_000,
            "earn_points": 1,
            "redeem_points": 1,
            "redeem_amount": 1_000,
            "min_redeem_points": 1,
            "max_redeem_percent": 100,
            "expiry_days": None,
        },
        headers=headers,
    )
    assert program.status_code == 200, program.text
    customer = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={"name": "Khách F&B", "phone": f"09{uuid.uuid4().int % 10**8:08d}"},
        headers=headers,
    ).json()
    loyalty_service.add_entry(
        db,
        ctx["shop_id"],
        customer["id"],
        loyalty_service.ENTRY_EARN,
        10,
        op("seed-points"),
        created_by_user_id=None,
    )
    db.commit()
    voucher_code = f"FNB{uuid.uuid4().hex[:8].upper()}"
    voucher = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={
            "code": voucher_code,
            "discount_type": "flat",
            "discount_value": 10_000,
            "min_order_value": 0,
            "usage_limit": 1,
            "expires_at": None,
        },
        headers=headers,
    )
    assert voucher.status_code == 200, voucher.text

    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    adjusted = client.patch(
        f"/api/fnb/checks/{primary['id']}/adjustments",
        json={
            "discount_kind": "FLAT",
            "discount_value": 10_000,
            "service_charge_kind": "FLAT",
            "service_charge_value": 5_000,
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("manual-adjustment"),
        },
        headers=headers,
    )
    assert adjusted.status_code == 200, adjusted.text
    primary = adjusted.json()["checks"][0]
    payload = {
        "payment_method": "cash",
        "customer_id": customer["id"],
        "voucher_code": voucher_code,
        "loyalty_points_to_use": 1,
        "cash_tendered_vnd": 200_000,
        "expected_revision": primary["revision"],
        "expected_session_revision": adjusted.json()["session_revision"],
        "operation_id": op("voucher-points-pay"),
    }
    missing_tender = {
        key: value for key, value in payload.items() if key != "cash_tendered_vnd"
    }
    before_balance = loyalty_service.balance_for_customer(
        db, customer["id"], shop_id=ctx["shop_id"]
    )
    before_orders = db.query(models.Order).count()
    rejected = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json=missing_tender,
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"]["code"] == "FNB_CASH_TENDERED_REQUIRED"
    db.expire_all()
    assert db.get(models.Voucher, voucher.json()["id"]).usage_count == 0
    assert loyalty_service.balance_for_customer(
        db, customer["id"], shop_id=ctx["shop_id"]
    ) == before_balance
    assert db.query(models.Order).count() == before_orders
    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=payload, headers=headers
    )
    assert paid.status_code == 200, paid.text
    result = paid.json()
    assert result["check"]["total_vnd"] == 184_000
    assert result["order"]["total_vnd"] == 184_000
    assert result["order"]["cash_change_vnd"] == 16_000

    retry = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=payload, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json() == result
    db.expire_all()
    order = db.get(models.Order, result["order"]["id"])
    assert order.voucher_code == voucher_code
    assert order.discount_amount == 20_000
    assert order.loyalty_points_redeemed == 1
    assert order.loyalty_discount_amount == 1_000
    assert db.get(models.Voucher, voucher.json()["id"]).usage_count == 1
    assert all(
        item.cost_known_qty + item.cost_unknown_qty == item.quantity
        and item.price * item.quantity
        - item.discount_vnd
        - item.loyalty_discount_vnd
        == item.net_amount_vnd
        for item in order.items
    )
    assert sum(item.net_amount_vnd for item in order.items) == 184_000
    assert loyalty_service.balance_for_customer(
        db, customer["id"], shop_id=ctx["shop_id"]
    ) == 9


def test_zero_total_cash_still_requires_explicit_numeric_zero(client, db):
    _, headers, session = sent_session(client, 1)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    adjusted = client.patch(
        f"/api/fnb/checks/{primary['id']}/adjustments",
        json={
            "discount_kind": "FLAT",
            "discount_value": primary["total_vnd"],
            "service_charge_kind": "NONE",
            "service_charge_value": 0,
            "expected_revision": primary["revision"],
            "expected_session_revision": session["revision"],
            "operation_id": op("zero-total-adjust"),
        },
        headers=headers,
    ).json()
    primary = adjusted["checks"][0]
    base = {
        "payment_method": "cash",
        "expected_revision": primary["revision"],
        "expected_session_revision": adjusted["session_revision"],
        "operation_id": op("zero-total-pay"),
    }

    missing = client.post(
        f"/api/fnb/checks/{primary['id']}/pay", json=base, headers=headers
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["required"] == 0

    paid = client.post(
        f"/api/fnb/checks/{primary['id']}/pay",
        json={**base, "cash_tendered_vnd": 0},
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["order"]["cash_tendered_vnd"] == 0
    assert paid.json()["order"]["cash_change_vnd"] == 0
