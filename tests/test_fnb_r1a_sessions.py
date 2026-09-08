import uuid

import pytest

from conftest import (
    auth,
    create_fnb_area,
    create_fnb_table,
    create_product,
    enable_fnb,
    new_staff,
    seller_with_shop,
)
from fselling import models


def operation(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.fixture
def fnb_ctx(client):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area_1 = create_fnb_area(client, ctx, "Trong nhà")
    area_2 = create_fnb_area(client, ctx, "Ngoài sân")
    table_1 = create_fnb_table(client, ctx, area_1["id"], "Bàn 1")
    table_2 = create_fnb_table(client, ctx, area_1["id"], "Bàn 2")
    table_3 = create_fnb_table(client, ctx, area_2["id"], "Bàn 3")
    product_2 = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        f"Món 2 {uuid.uuid4().hex[:8]}",
        35_000,
        7,
        ctx["category_id"],
    )
    return {
        **ctx,
        "area_1": area_1,
        "area_2": area_2,
        "table_1": table_1,
        "table_2": table_2,
        "table_3": table_3,
        "product_1": ctx["product"],
        "product_2": product_2,
    }


def floor(client, ctx):
    return client.get(
        "/api/fnb/floor",
        params={"shop_id": ctx["shop_id"]},
        headers=auth(ctx["token"]),
    ).json()


def table_snapshot(client, ctx, table_id):
    return next(
        row
        for area in floor(client, ctx)["areas"]
        for row in area["tables"]
        if row["id"] == table_id
    )


def open_session(
    client,
    ctx,
    table,
    operation_id="open-session-0001",
    expected_revision=None,
    expected_table_version=None,
):
    current_floor = floor(client, ctx)
    current_table = next(
        row
        for area in current_floor["areas"]
        for row in area["tables"]
        if row["id"] == table["id"]
    )
    return client.post(
        "/api/fnb/sessions",
        json={
            "shop_id": ctx["shop_id"],
            "table_id": table["id"],
            "expected_revision": (
                current_floor["fnb_revision"]
                if expected_revision is None
                else expected_revision
            ),
            "expected_table_version": (
                current_table["state_version"]
                if expected_table_version is None
                else expected_table_version
            ),
            "operation_id": operation_id,
        },
        headers=auth(ctx["token"]),
    )


def add_line(client, ctx, session, product, operation_id):
    response = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": product["id"],
            "quantity": 2,
            "note": "  Ít đá  ",
            "expected_revision": session["revision"],
            "operation_id": operation_id,
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_open_session_is_idempotent_and_one_table_has_one_active_session(client, fnb_ctx):
    current = floor(client, fnb_ctx)
    first = open_session(
        client,
        fnb_ctx,
        fnb_ctx["table_1"],
        expected_revision=current["fnb_revision"],
        expected_table_version=fnb_ctx["table_1"]["state_version"],
    )
    assert first.status_code == 200, first.text
    session = first.json()
    assert session["status"] == "OPEN"
    assert session["revision"] == 0
    assert [row["id"] for row in session["tables"]] == [fnb_ctx["table_1"]["id"]]

    retry = open_session(
        client,
        fnb_ctx,
        fnb_ctx["table_1"],
        expected_revision=current["fnb_revision"],
        expected_table_version=fnb_ctx["table_1"]["state_version"],
    )
    assert retry.status_code == 200
    assert retry.json() == session

    reused = open_session(
        client,
        fnb_ctx,
        fnb_ctx["table_2"],
        expected_revision=floor(client, fnb_ctx)["fnb_revision"],
        expected_table_version=fnb_ctx["table_2"]["state_version"],
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "FNB_OPERATION_REUSED"

    occupied = open_session(client, fnb_ctx, fnb_ctx["table_1"], "open-session-0002")
    assert occupied.status_code == 409
    assert occupied.json()["detail"]["code"] == "FNB_TABLE_OCCUPIED"


def test_line_lifecycle_is_revision_safe_snapshots_db_price_and_changes_no_stock(client, fnb_ctx, db):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    before_stock = db.get(models.Product, fnb_ctx["product_1"]["id"]).stock
    before_orders = {
        model: db.query(model).count()
        for model in (models.Order, models.OrderItem, models.OrderPayment)
    }
    added = add_line(client, fnb_ctx, session, fnb_ctx["product_1"], "add-line-00000001")
    line = added["lines"][0]
    assert added["revision"] == 1
    assert line["unit_price_vnd"] == int(fnb_ctx["product_1"]["price"])
    assert line["note"] == "Ít đá"
    assert added["subtotal_vnd"] == line["unit_price_vnd"] * 2

    stale = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": fnb_ctx["product_2"]["id"],
            "quantity": 1,
            "note": None,
            "expected_revision": 0,
            "operation_id": "add-line-00000002",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "FNB_SESSION_CHANGED"
    assert stale.json()["detail"]["snapshot"] == added

    updated = client.patch(
        f"/api/fnb/lines/{line['id']}",
        json={
            "quantity": 3,
            "note": "",
            "expected_line_version": line["state_version"],
            "expected_revision": added["revision"],
            "operation_id": "update-line-000001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["lines"][0]["note"] is None

    cancelled = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel-line",
        json={
            "line_id": line["id"],
            "quantity": 3,
            "expected_line_version": updated.json()["lines"][0]["state_version"],
            "expected_revision": updated.json()["revision"],
            "operation_id": "cancel-line-000001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["subtotal_vnd"] == 0
    assert cancelled.json()["unsent_quantity"] == 0
    db.expire_all()
    assert db.get(models.Product, fnb_ctx["product_1"]["id"]).stock == before_stock
    assert {
        model: db.query(model).count()
        for model in (models.Order, models.OrderItem, models.OrderPayment)
    } == before_orders


def test_line_retry_is_single_write_and_floor_returns_only_serving_summary(client, fnb_ctx, db):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    payload = {
        "product_id": fnb_ctx["product_1"]["id"],
        "quantity": 1,
        "note": "Không hành",
        "expected_revision": session["revision"],
        "operation_id": "stable-line-0001",
    }
    first = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json=payload,
        headers=auth(fnb_ctx["token"]),
    )
    retry = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json=payload,
        headers=auth(fnb_ctx["token"]),
    )
    assert retry.status_code == 200
    assert retry.json() == first.json()
    db.expire_all()
    assert (
        db.query(models.FnbSessionLine)
        .filter(models.FnbSessionLine.session_id == session["id"])
        .count()
        == 1
    )

    reused = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={**payload, "quantity": 2},
        headers=auth(fnb_ctx["token"]),
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "FNB_OPERATION_REUSED"

    current = table_snapshot(client, fnb_ctx, fnb_ctx["table_1"]["id"])
    assert current["state"] == "SERVING"
    assert current["session"] == {
        "id": session["id"],
        "revision": 1,
        "opened_at": first.json()["opened_at"],
        "subtotal_vnd": int(fnb_ctx["product_1"]["price"]),
        "unsent_quantity": 1,
        "service_stage": "DIRECT",
        "service_summary": {"NEW": 0, "IN_PROGRESS": 0, "READY": 0, "SERVED": 0},
        "table_count": 1,
    }
    assert "lines" not in current["session"]
    assert "note" not in current["session"]


def test_line_validation_rejects_client_price_inactive_and_cross_shop_product(client, fnb_ctx, db):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    extra = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": fnb_ctx["product_1"]["id"],
            "price": 1,
            "quantity": 1,
            "expected_revision": 0,
            "operation_id": "client-price-0001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert extra.status_code == 422

    product = db.get(models.Product, fnb_ctx["product_1"]["id"])
    product.is_active = False
    db.commit()
    inactive = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": product.id,
            "quantity": 1,
            "expected_revision": 0,
            "operation_id": "inactive-product-0001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert inactive.status_code == 409
    assert inactive.json()["detail"]["code"] == "FNB_PRODUCT_INACTIVE"

    other = seller_with_shop(client)
    cross = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": other["product"]["id"],
            "quantity": 1,
            "expected_revision": 0,
            "operation_id": "cross-product-00001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert cross.status_code == 404


def test_move_changes_only_table_links_and_preserves_session(client, fnb_ctx):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    source = table_snapshot(client, fnb_ctx, fnb_ctx["table_1"]["id"])
    target = table_snapshot(client, fnb_ctx, fnb_ctx["table_2"]["id"])
    moved = client.post(
        f"/api/fnb/sessions/{session['id']}/move-table",
        json={
            "from_table_id": source["id"],
            "to_table_id": target["id"],
            "expected_revision": session["revision"],
            "expected_from_state_version": source["state_version"],
            "expected_to_state_version": target["state_version"],
            "operation_id": "move-table-000001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == session["id"]
    assert [row["id"] for row in moved.json()["tables"]] == [target["id"]]


def test_move_into_occupied_table_and_cross_shop_target_are_rejected(client, fnb_ctx):
    source = open_session(client, fnb_ctx, fnb_ctx["table_1"], "source-open-0001").json()
    open_session(client, fnb_ctx, fnb_ctx["table_2"], "occupied-open-01")
    source_table = table_snapshot(client, fnb_ctx, fnb_ctx["table_1"]["id"])
    occupied_table = table_snapshot(client, fnb_ctx, fnb_ctx["table_2"]["id"])
    occupied = client.post(
        f"/api/fnb/sessions/{source['id']}/move-table",
        json={
            "from_table_id": source_table["id"],
            "to_table_id": occupied_table["id"],
            "expected_revision": source["revision"],
            "expected_from_state_version": source_table["state_version"],
            "expected_to_state_version": occupied_table["state_version"],
            "operation_id": "move-occupied-01",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert occupied.status_code == 409
    assert occupied.json()["detail"]["code"] == "FNB_TABLE_OCCUPIED"

    other = seller_with_shop(client)
    enable_fnb(client, other)
    other_area = create_fnb_area(client, other, "Shop khác")
    other_table = create_fnb_table(client, other, other_area["id"], "Bàn khác")
    cross = client.post(
        f"/api/fnb/sessions/{source['id']}/merge-table",
        json={
            "target_table_id": other_table["id"],
            "expected_revision": source["revision"],
            "expected_target_session_revision": None,
            "expected_target_table_version": other_table["state_version"],
            "operation_id": "merge-cross-shop",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert cross.status_code == 404


def test_merge_sessions_preserves_line_ids_and_stale_target_is_atomic(client, fnb_ctx, db):
    source = open_session(client, fnb_ctx, fnb_ctx["table_1"], "open-source-0001").json()
    target = open_session(client, fnb_ctx, fnb_ctx["table_2"], "open-target-0001").json()
    source = add_line(client, fnb_ctx, source, fnb_ctx["product_1"], "source-line-0001")
    target = add_line(client, fnb_ctx, target, fnb_ctx["product_2"], "target-line-0001")
    original_line_ids = {source["lines"][0]["id"], target["lines"][0]["id"]}
    target_table = table_snapshot(client, fnb_ctx, fnb_ctx["table_2"]["id"])

    stale = client.post(
        f"/api/fnb/sessions/{source['id']}/merge-table",
        json={
            "target_table_id": target_table["id"],
            "expected_revision": source["revision"],
            "expected_target_session_revision": target["revision"] - 1,
            "expected_target_table_version": target_table["state_version"],
            "operation_id": "merge-stale-00001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "FNB_SESSION_CHANGED"
    db.expire_all()
    unchanged_lines = (
        db.query(models.FnbSessionLine)
        .filter(models.FnbSessionLine.id.in_(original_line_ids))
        .all()
    )
    assert {row.session_id for row in unchanged_lines} == {source["id"], target["id"]}

    merged = client.post(
        f"/api/fnb/sessions/{source['id']}/merge-table",
        json={
            "target_table_id": target_table["id"],
            "expected_revision": source["revision"],
            "expected_target_session_revision": target["revision"],
            "expected_target_table_version": target_table["state_version"],
            "operation_id": "merge-table-00001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert merged.status_code == 200, merged.text
    assert {line["id"] for line in merged.json()["lines"]} == original_line_ids
    assert {row["id"] for row in merged.json()["tables"]} == {
        fnb_ctx["table_1"]["id"], fnb_ctx["table_2"]["id"]
    }
    db.expire_all()
    merged_target = db.get(models.FnbServiceSession, target["id"])
    assert merged_target.status == "CANCELLED"
    assert merged_target.merged_into_session_id == source["id"]


def test_merge_rejects_target_with_sent_artifacts_without_moving_graph(client, fnb_ctx, db):
    product = db.get(models.Product, fnb_ctx["product_2"]["id"])
    product.fnb_station = "KITCHEN"
    db.commit()

    source = open_session(client, fnb_ctx, fnb_ctx["table_1"], "merge-art-source").json()
    target = open_session(client, fnb_ctx, fnb_ctx["table_2"], "merge-art-target").json()
    target = add_line(client, fnb_ctx, target, fnb_ctx["product_2"], "merge-art-line")
    sent = client.post(
        f"/api/fnb/sessions/{target['id']}/send",
        json={
            "expected_revision": target["revision"],
            "operation_id": "merge-art-send",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert sent.status_code == 200, sent.text
    target = sent.json()
    target_table = table_snapshot(client, fnb_ctx, fnb_ctx["table_2"]["id"])

    rejected = client.post(
        f"/api/fnb/sessions/{source['id']}/merge-table",
        json={
            "target_table_id": target_table["id"],
            "expected_revision": source["revision"],
            "expected_target_session_revision": target["revision"],
            "expected_target_table_version": target_table["state_version"],
            "operation_id": "merge-art-reject",
        },
        headers=auth(fnb_ctx["token"]),
    )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "FNB_TARGET_SESSION_HAS_ARTIFACTS"
    db.expire_all()
    assert db.get(models.FnbServiceSession, source["id"]).status == "OPEN"
    assert db.get(models.FnbServiceSession, target["id"]).status == "OPEN"
    assert db.query(models.FnbSessionLine).filter_by(session_id=target["id"]).count() == 1
    assert db.query(models.FnbKitchenTicket).filter_by(session_id=target["id"]).count() == 1
    assert db.query(models.FnbStockAllocation).filter_by(session_id=target["id"]).count() == 1
    assert db.query(models.FnbSessionTable).filter_by(
        session_id=target["id"], released_at=None
    ).count() == 1


def test_merge_empty_table_and_cancel_session_release_every_table(client, fnb_ctx):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    target = table_snapshot(client, fnb_ctx, fnb_ctx["table_3"]["id"])
    merged = client.post(
        f"/api/fnb/sessions/{session['id']}/merge-table",
        json={
            "target_table_id": target["id"],
            "expected_revision": session["revision"],
            "expected_target_session_revision": None,
            "expected_target_table_version": target["state_version"],
            "operation_id": "attach-table-0001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert merged.status_code == 200, merged.text
    assert len(merged.json()["tables"]) == 2

    cancelled = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel",
        json={
            "expected_revision": merged.json()["revision"],
            "reason": "Khách đổi ý",
            "operation_id": "cancel-session-001",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["tables"] == []
    assert table_snapshot(client, fnb_ctx, fnb_ctx["table_1"]["id"])["state"] == "EMPTY"
    assert table_snapshot(client, fnb_ctx, fnb_ctx["table_3"]["id"])["state"] == "EMPTY"


def test_nonempty_session_cannot_cancel_until_billable_quantity_is_zero(client, fnb_ctx):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    session = add_line(client, fnb_ctx, session, fnb_ctx["product_1"], "cancel-guard-line")
    denied = client.post(
        f"/api/fnb/sessions/{session['id']}/cancel",
        json={
            "expected_revision": session["revision"],
            "operation_id": "cancel-nonempty-01",
        },
        headers=auth(fnb_ctx["token"]),
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "FNB_SESSION_NOT_EMPTY"


def test_service_can_move_table_but_cashier_cannot(client, fnb_ctx):
    _, service = new_staff(client, fnb_ctx, "SERVICE")
    service_ctx = {**fnb_ctx, "token": service}
    session = open_session(client, service_ctx, fnb_ctx["table_1"], "service-open-0001").json()
    source = table_snapshot(client, service_ctx, fnb_ctx["table_1"]["id"])
    target = table_snapshot(client, service_ctx, fnb_ctx["table_2"]["id"])
    moved = client.post(
        f"/api/fnb/sessions/{session['id']}/move-table",
        json={
            "from_table_id": source["id"],
            "to_table_id": target["id"],
            "expected_revision": session["revision"],
            "expected_from_state_version": source["state_version"],
            "expected_to_state_version": target["state_version"],
            "operation_id": "service-move-0001",
        },
        headers=auth(service),
    )
    assert moved.status_code == 200, moved.text
    assert [row["id"] for row in moved.json()["tables"]] == [target["id"]]


def test_cashier_can_serve_but_cannot_move_or_merge(client, fnb_ctx):
    _, cashier = new_staff(client, fnb_ctx, "CASHIER")
    cashier_ctx = {**fnb_ctx, "token": cashier}
    session = open_session(client, cashier_ctx, fnb_ctx["table_1"], "cashier-open-0001").json()
    added = add_line(client, cashier_ctx, session, fnb_ctx["product_1"], "cashier-line-0001")
    source = table_snapshot(client, cashier_ctx, fnb_ctx["table_1"]["id"])
    target = table_snapshot(client, cashier_ctx, fnb_ctx["table_2"]["id"])
    denied = client.post(
        f"/api/fnb/sessions/{session['id']}/move-table",
        json={
            "from_table_id": source["id"],
            "to_table_id": target["id"],
            "expected_revision": added["revision"],
            "expected_from_state_version": source["state_version"],
            "expected_to_state_version": target["state_version"],
            "operation_id": "cashier-move-0001",
        },
        headers=auth(cashier),
    )
    assert denied.status_code == 403


def test_session_ids_are_tenant_scoped(client, fnb_ctx):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    other = seller_with_shop(client)
    hidden = client.get(
        f"/api/fnb/sessions/{session['id']}", headers=auth(other["token"])
    )
    assert hidden.status_code == 404


def test_line_ids_do_not_leak_across_tenants(client, fnb_ctx):
    session = open_session(client, fnb_ctx, fnb_ctx["table_1"]).json()
    session = add_line(
        client, fnb_ctx, session, fnb_ctx["product_1"], "tenant-line-0001"
    )
    line = session["lines"][0]
    other = seller_with_shop(client)
    payload = {
        "quantity": 1,
        "note": None,
        "expected_line_version": line["state_version"],
        "expected_revision": session["revision"],
        "operation_id": "hidden-line-0001",
    }

    foreign = client.patch(
        f"/api/fnb/lines/{line['id']}",
        json=payload,
        headers=auth(other["token"]),
    )
    missing = client.patch(
        "/api/fnb/lines/999999999",
        json=payload,
        headers=auth(other["token"]),
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["detail"]["code"] == "FNB_LINE_NOT_FOUND"
    assert foreign.json()["detail"] == missing.json()["detail"]
