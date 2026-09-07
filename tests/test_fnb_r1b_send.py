import datetime
import uuid

import pytest

from conftest import (
    auth,
    create_fnb_area,
    create_fnb_table,
    enable_fnb,
    new_staff,
    seller_with_shop,
)
from fselling import models

from test_fnb_r1b_cancel import sent_session


def op(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.mark.parametrize("session_status", ["CLOSED", "CANCELLED"])
@pytest.mark.parametrize(
    ("route", "extra"),
    [
        ("start", {}),
        ("done", {}),
        ("out-of-stock", {"reason": "Hết nguyên liệu"}),
        ("resume", {}),
        ("serve", {}),
    ],
)
def test_every_ticket_mutation_fails_closed_on_terminal_session(
    client, db, session_status, route, extra
):
    ctx, session = sent_session(client, db)
    ticket = session["tickets"][0]
    stored_session = db.get(models.FnbServiceSession, session["id"])
    stored_session.status = session_status
    for link in db.query(models.FnbSessionTable).filter_by(session_id=session["id"]):
        link.released_at = datetime.datetime.utcnow()
    db.commit()
    shop_revision = db.get(models.Shop, ctx["shop_id"]).fnb_revision

    response = client.post(
        f"/api/fnb/tickets/{ticket['id']}/{route}",
        json={
            "expected_state_version": ticket["state_version"],
            "expected_session_revision": session["revision"],
            "operation_id": op(f"terminal-{route}"),
            **extra,
        },
        headers=auth(ctx["token"]),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "FNB_SESSION_NOT_ACTIVE"
    db.expire_all()
    assert db.get(models.FnbKitchenTicket, ticket["id"]).state_version == 0
    assert db.get(models.FnbServiceSession, session["id"]).revision == session["revision"]
    assert db.get(models.Shop, ctx["shop_id"]).fnb_revision == shop_revision


def test_legacy_empty_ticket_is_hidden_and_cannot_mutate(client, db):
    ctx, session = sent_session(client, db)
    ticket = session["tickets"][0]
    item = db.query(models.FnbKitchenTicketItem).filter_by(ticket_id=ticket["id"]).one()
    item.cancelled_quantity = item.quantity
    db.commit()

    queue = client.get(
        "/api/fnb/stations/KITCHEN/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=auth(ctx["token"]),
    )
    assert queue.status_code == 200
    assert queue.json()["tickets"] == []
    response = client.post(
        f"/api/fnb/tickets/{ticket['id']}/start",
        json={
            "expected_state_version": 0,
            "expected_session_revision": session["revision"],
            "operation_id": op("legacy-empty"),
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FNB_TICKET_EMPTY"


def test_done_out_of_stock_ticket_is_not_ready_or_servable(client, db):
    ctx, session = sent_session(client, db)
    ticket = session["tickets"][0]
    stored = db.get(models.FnbKitchenTicket, ticket["id"])
    stored.status = "DONE"
    stored.out_of_stock_reason = "Dữ liệu cũ không hợp lệ"
    db.commit()

    snapshot = client.get(
        f"/api/fnb/sessions/{session['id']}", headers=auth(ctx["token"])
    )
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["service_summary"]["READY"] == 0
    assert body["service_tickets"][0]["service_stage"] != "READY"

    response = client.post(
        f"/api/fnb/tickets/{ticket['id']}/serve",
        json={
            "expected_state_version": 0,
            "expected_session_revision": session["revision"],
            "operation_id": op("legacy-serve"),
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FNB_TICKET_OUT_OF_STOCK"
    stored = db.get(models.FnbKitchenTicket, ticket["id"])
    stored.status = "NEW"
    stored.out_of_stock_reason = None
    db.commit()


def test_send_is_atomic_idempotent_and_station_scoped(client, db):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx)
    table = create_fnb_table(client, ctx, area["id"])
    headers = auth(ctx["token"])

    floor = client.get("/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers).json()
    station = client.patch(
        f"/api/fnb/menu-items/{ctx['product']['id']}/station",
        json={
            "station": "KITCHEN",
            "expected_revision": floor["fnb_revision"],
            "operation_id": op("station"),
        },
        headers=headers,
    )
    assert station.status_code == 200, station.text
    products = client.get(
        f"/api/products/{ctx['shop_id']}", headers=headers
    ).json()
    assert next(row for row in products if row["id"] == ctx["product"]["id"])[
        "fnb_station"
    ] == "KITCHEN"

    floor = client.get("/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers).json()
    current_table = floor["areas"][0]["tables"][0]
    session = client.post(
        "/api/fnb/sessions",
        json={
            "shop_id": ctx["shop_id"],
            "table_id": table["id"],
            "expected_revision": floor["fnb_revision"],
            "expected_table_version": current_table["state_version"],
            "operation_id": op("open"),
        },
        headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": ctx["product"]["id"],
            "quantity": 2,
            "note": "ít cay",
            "expected_revision": session["revision"],
            "operation_id": op("line"),
        },
        headers=headers,
    ).json()
    assert session["lines"][0]["station"] == "KITCHEN"
    send_operation = op("send")
    payload = {"expected_revision": session["revision"], "operation_id": send_operation}
    sent = client.post(
        f"/api/fnb/sessions/{session['id']}/send", json=payload, headers=headers
    )
    assert sent.status_code == 200, sent.text
    result = sent.json()
    assert result["unsent_quantity"] == 0
    assert result["lines"][0]["sent_quantity"] == 2
    assert len(result["tickets"]) == 1
    assert result["tickets"][0]["station"] == "KITCHEN"

    retry = client.post(
        f"/api/fnb/sessions/{session['id']}/send", json=payload, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json() == result
    db.expire_all()
    assert db.get(models.Product, ctx["product"]["id"]).stock == 8
    assert db.query(models.FnbKitchenTicket).filter_by(
        shop_id=ctx["shop_id"]
    ).count() == 1
    assert db.query(models.FnbStockAllocation).filter_by(
        shop_id=ctx["shop_id"]
    ).count() == 1

    kitchen = client.get(
        "/api/fnb/stations/KITCHEN/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=headers,
    )
    bar = client.get(
        "/api/fnb/stations/BAR/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=headers,
    )
    assert kitchen.status_code == bar.status_code == 200
    assert [row["id"] for row in kitchen.json()["tickets"]] == [result["tickets"][0]["id"]]
    assert kitchen.json()["tickets"][0]["session_revision"] == result["revision"]
    assert bar.json()["tickets"] == []

    _, kitchen_token = new_staff(client, ctx, "KITCHEN")
    kitchen_headers = auth(kitchen_token)
    assert client.get(
        "/api/fnb/stations/KITCHEN/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=kitchen_headers,
    ).status_code == 200
    assert client.get(
        "/api/fnb/stations/BAR/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=kitchen_headers,
    ).status_code == 403

    ticket_id = result["tickets"][0]["id"]
    out_payload = {
        "expected_state_version": 0,
        "expected_session_revision": result["revision"],
        "operation_id": op("out"),
        "reason": "Hết nguyên liệu",
    }
    out = client.post(
        f"/api/fnb/tickets/{ticket_id}/out-of-stock",
        json=out_payload,
        headers=kitchen_headers,
    )
    assert out.status_code == 200, out.text
    assert out.json()["out_of_stock_reason"] == "Hết nguyên liệu"
    assert out.json()["session_revision"] == result["revision"] + 1
    start_payload = {
        "expected_state_version": 1,
        "expected_session_revision": out.json()["session_revision"],
        "operation_id": op("start"),
    }
    started = client.post(
        f"/api/fnb/tickets/{ticket_id}/start",
        json=start_payload,
        headers=kitchen_headers,
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_PROGRESS"
    assert client.post(
        f"/api/fnb/tickets/{ticket_id}/start",
        json=start_payload,
        headers=kitchen_headers,
    ).json() == started.json()
    stale = client.post(
        f"/api/fnb/tickets/{ticket_id}/done",
        json={
            "expected_state_version": 0,
            "expected_session_revision": started.json()["session_revision"],
            "operation_id": op("done-stale"),
        },
        headers=kitchen_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "FNB_TICKET_CHANGED"
    blocked = client.post(
        f"/api/fnb/tickets/{ticket_id}/done",
        json={
            "expected_state_version": 2,
            "expected_session_revision": started.json()["session_revision"],
            "operation_id": op("done-blocked"),
        },
        headers=kitchen_headers,
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "FNB_TICKET_OUT_OF_STOCK"

    resume_payload = {
        "expected_state_version": 2,
        "expected_session_revision": started.json()["session_revision"],
        "operation_id": op("resume"),
    }
    resumed = client.post(
        f"/api/fnb/tickets/{ticket_id}/resume",
        json=resume_payload,
        headers=kitchen_headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["out_of_stock_reason"] is None
    assert client.post(
        f"/api/fnb/tickets/{ticket_id}/resume",
        json=resume_payload,
        headers=kitchen_headers,
    ).json() == resumed.json()

    done = client.post(
        f"/api/fnb/tickets/{ticket_id}/done",
        json={
            "expected_state_version": resumed.json()["state_version"],
            "expected_session_revision": resumed.json()["session_revision"],
            "operation_id": op("done"),
        },
        headers=kitchen_headers,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "DONE"
    assert done.json()["service_stage"] == "READY"
    floor_ready = client.get(
        "/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers
    ).json()
    floor_session = floor_ready["areas"][0]["tables"][0]["session"]
    assert floor_session["service_stage"] == "READY"
    assert floor_session["service_summary"]["READY"] == 1

    serve_payload = {
        "expected_state_version": done.json()["state_version"],
        "expected_session_revision": done.json()["session_revision"],
        "operation_id": op("serve"),
    }
    served = client.post(
        f"/api/fnb/tickets/{ticket_id}/serve",
        json=serve_payload,
        headers=headers,
    )
    assert served.status_code == 200, served.text
    assert served.json()["service_stage"] == "SERVED"
    assert served.json()["served_at"] is not None
    assert served.json()["served_by_user_id"] is not None
    assert client.post(
        f"/api/fnb/tickets/{ticket_id}/serve",
        json=serve_payload,
        headers=headers,
    ).json() == served.json()

    session_view = client.get(
        f"/api/fnb/sessions/{session['id']}", headers=headers
    ).json()
    assert session_view["service_stage"] == "SERVED"
    assert session_view["service_summary"] == {
        "NEW": 0,
        "IN_PROGRESS": 0,
        "READY": 0,
        "SERVED": 1,
    }
    assert client.get(
        "/api/fnb/stations/KITCHEN/tickets",
        params={"shop_id": ctx["shop_id"]},
        headers=kitchen_headers,
    ).json()["tickets"] == []


def test_send_rejects_aggregate_stock_shortage_without_partial_writes(client, db):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx)
    table = create_fnb_table(client, ctx, area["id"])
    headers = auth(ctx["token"])
    product = db.get(models.Product, ctx["product"]["id"])
    product.fnb_station = "BAR"
    product.stock = product.cost_unknown_qty = 1
    product.cost_known_qty = product.cost_basis_vnd = 0
    db.commit()

    floor = client.get("/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers).json()
    session = client.post(
        "/api/fnb/sessions",
        json={
            "shop_id": ctx["shop_id"], "table_id": table["id"],
            "expected_revision": floor["fnb_revision"],
            "expected_table_version": floor["areas"][0]["tables"][0]["state_version"],
            "operation_id": op("open"),
        }, headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={"product_id": product.id, "quantity": 2, "note": None,
              "expected_revision": session["revision"], "operation_id": op("line")},
        headers=headers,
    ).json()
    response = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json={"expected_revision": session["revision"], "operation_id": op("send")},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FNB_STOCK_SHORTAGE"
    db.expire_all()
    assert db.get(models.Product, product.id).stock == 1
    assert db.query(models.FnbKitchenTicket).filter(
        models.FnbKitchenTicket.shop_id == ctx["shop_id"]
    ).count() == 0
    assert db.query(models.FnbStockAllocation).filter(
        models.FnbStockAllocation.shop_id == ctx["shop_id"]
    ).count() == 0


def test_direct_item_skips_ticket_and_keeps_exact_batch_provenance(client, db):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx)
    table = create_fnb_table(client, ctx, area["id"])
    headers = auth(ctx["token"])
    product = db.get(models.Product, ctx["product"]["id"])
    product.track_batches = True
    product.fnb_station = "DIRECT"
    product.cost_known_qty = 0
    product.cost_unknown_qty = 0
    product.cost_basis_vnd = 0
    product.cost_deficit_qty = 0
    first = models.ProductBatch(
        product_id=product.id, shop_id=ctx["shop_id"], expiry_date="2099-01-01",
        quantity=1, cost_known_qty=0, cost_unknown_qty=1, cost_basis_vnd=0,
    )
    second = models.ProductBatch(
        product_id=product.id, shop_id=ctx["shop_id"], expiry_date="2099-02-01",
        quantity=9, cost_known_qty=0, cost_unknown_qty=9, cost_basis_vnd=0,
    )
    db.add_all([first, second])
    db.commit()

    floor = client.get("/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers).json()
    session = client.post(
        "/api/fnb/sessions",
        json={
            "shop_id": ctx["shop_id"], "table_id": table["id"],
            "expected_revision": floor["fnb_revision"],
            "expected_table_version": floor["areas"][0]["tables"][0]["state_version"],
            "operation_id": op("open"),
        }, headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": product.id, "quantity": 2, "note": None,
            "expected_revision": session["revision"], "operation_id": op("line"),
        }, headers=headers,
    ).json()
    sent = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json={"expected_revision": session["revision"], "operation_id": op("send")},
        headers=headers,
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["tickets"] == []
    db.expire_all()
    allocations = db.query(models.FnbStockAllocation).filter(
        models.FnbStockAllocation.session_id == session["id"]
    ).order_by(models.FnbStockAllocation.id).all()
    assert [(row.batch_id, row.quantity) for row in allocations] == [
        (first.id, 1), (second.id, 1),
    ]
    assert (db.get(models.ProductBatch, first.id).quantity,
            db.get(models.ProductBatch, second.id).quantity) == (0, 8)
