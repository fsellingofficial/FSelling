import uuid

from conftest import (
    auth,
    create_fnb_area,
    create_fnb_table,
    enable_fnb,
    seller_with_shop,
)


def op(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def sent_session(client, quantity: int = 3, *, station: str | None = None):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx)
    table = create_fnb_table(client, ctx, area["id"])
    headers = auth(ctx["token"])
    floor = client.get(
        "/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers
    ).json()
    if station is not None:
        response = client.patch(
            f"/api/fnb/menu-items/{ctx['product']['id']}/station",
            json={
                "station": station,
                "expected_revision": floor["fnb_revision"],
                "operation_id": op("station"),
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        floor = client.get(
            "/api/fnb/floor", params={"shop_id": ctx["shop_id"]}, headers=headers
        ).json()
    session = client.post(
        "/api/fnb/sessions",
        json={
            "shop_id": ctx["shop_id"],
            "table_id": table["id"],
            "expected_revision": floor["fnb_revision"],
            "expected_table_version": floor["areas"][0]["tables"][0]["state_version"],
            "operation_id": op("open"),
        },
        headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/lines",
        json={
            "product_id": ctx["product"]["id"],
            "quantity": quantity,
            "expected_revision": session["revision"],
            "operation_id": op("line"),
        },
        headers=headers,
    ).json()
    session = client.post(
        f"/api/fnb/sessions/{session['id']}/send",
        json={"expected_revision": session["revision"], "operation_id": op("send")},
        headers=headers,
    ).json()
    return ctx, headers, session


def test_split_preview_confirm_and_adjustment_are_conservative(client):
    ctx, headers, session = sent_session(client)
    response = client.get(f"/api/fnb/sessions/{session['id']}/checks", headers=headers)
    assert response.status_code == 200, response.text
    checks = response.json()
    assert len(checks["checks"]) == 1
    primary = checks["checks"][0]
    assert primary["is_primary"] is True
    assert primary["subtotal_vnd"] == primary["total_vnd"] == 300000
    assert primary["lines"][0]["quantity"] == 3

    selection = {"lines": [{"line_id": session["lines"][0]["id"], "quantity": 1}]}
    preview = client.post(
        f"/api/fnb/checks/{primary['id']}/split-preview",
        json=selection,
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source"]["subtotal_vnd"] == 200000
    assert preview.json()["new_check"]["subtotal_vnd"] == 100000

    split_payload = {
        **selection,
        "label": "Khách 2",
        "expected_revision": primary["revision"],
        "expected_session_revision": session["revision"],
        "operation_id": op("split"),
    }
    split = client.post(
        f"/api/fnb/checks/{primary['id']}/split", json=split_payload, headers=headers
    )
    assert split.status_code == 200, split.text
    result = split.json()
    assert [row["subtotal_vnd"] for row in result["checks"]] == [200000, 100000]
    retry = client.post(
        f"/api/fnb/checks/{primary['id']}/split", json=split_payload, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json() == result

    new_check = result["checks"][1]
    adjusted = client.patch(
        f"/api/fnb/checks/{new_check['id']}/adjustments",
        json={
            "discount_kind": "FLAT",
            "discount_value": 10000,
            "service_charge_kind": "PERCENT",
            "service_charge_value": 1000,
            "expected_revision": new_check["revision"],
            "expected_session_revision": result["session_revision"],
            "operation_id": op("adjust"),
        },
        headers=headers,
    )
    assert adjusted.status_code == 200, adjusted.text
    changed = next(row for row in adjusted.json()["checks"] if row["id"] == new_check["id"])
    assert changed["discount_vnd"] == 10000
    assert changed["service_charge_vnd"] == 10000
    assert changed["total_vnd"] == 100000

    receipt = client.get(
        f"/api/fnb/checks/{new_check['id']}/provisional-receipt", headers=headers
    )
    assert receipt.status_code == 200
    assert receipt.json()["document_label"] == "TẠM TÍNH — CHƯA THANH TOÁN"


def test_split_rejects_stale_revision_and_cross_shop_access(client):
    _, headers, session = sent_session(client, 2)
    primary = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=headers
    ).json()["checks"][0]
    payload = {
        "lines": [{"line_id": session["lines"][0]["id"], "quantity": 1}],
        "label": "Bill phụ",
        "expected_revision": primary["revision"] + 1,
        "expected_session_revision": session["revision"],
        "operation_id": op("stale"),
    }
    stale = client.post(
        f"/api/fnb/checks/{primary['id']}/split", json=payload, headers=headers
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "FNB_CHECK_CHANGED"

    outsider = seller_with_shop(client)
    hidden = client.get(
        f"/api/fnb/sessions/{session['id']}/checks", headers=auth(outsider["token"])
    )
    assert hidden.status_code == 404
