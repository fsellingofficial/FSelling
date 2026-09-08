"""Acceptance tests for offline contract v1 (I09-E+B2).

Known vectors for canonical fingerprint v1.
Time boundary rules.
Sequence conflict.
Body cap.
"""
from __future__ import annotations

import json
import hashlib
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fselling import models
from fselling.core.database import SessionLocal
from fselling.core.numeric_limits import MAX_SAFE_VND
from fselling.services import offline_lease_service, offline_service, order_service
from conftest import _TEST_MIGRATIONS, auth, new_staff, seller_with_shop

BASE_NOW = datetime(2025, 7, 15, 9, 30, 0, 123456)


@pytest.fixture(autouse=True)
def lease_enabled(monkeypatch):
    """Enable offline lease issuance for tests."""
    monkeypatch.setattr(
        offline_lease_service.config,
        "OFFLINE_LEASE_ISSUANCE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        offline_lease_service.subscription_service,
        "require_pro",
        lambda *_args, **_kwargs: {"can_use_pro": True},
    )
    monkeypatch.setattr(
        offline_lease_service.subscription_service,
        "get_subscription_state",
        lambda *_args, **_kwargs: {"can_use_pro": True},
    )
    monkeypatch.setattr(offline_lease_service, "_utcnow", lambda: BASE_NOW)
    monkeypatch.setattr(offline_service, "_utcnow", lambda: BASE_NOW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _issue_lease(
    client: TestClient,
    shop_id: int,
    token: str,
    device_id: str = "dev-001",
) -> dict:
    resp = client.post(
        "/api/offline/leases",
        headers=_auth_header(token),
        json={"shop_id": shop_id, "device_id": device_id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()


def _v1_payload(
    lease: dict,
    items: list,
    sold_at: str,
    monotonic_ms: int,
    monotonic_valid: bool,
    seq: int,
    offline_uuid: str,
    client_fp: str | None = None,
) -> dict:
    payload = {
        "offline_contract_version": 1,
        "lease_id": lease["lease_id"],
        "device_id": lease["device_id"],
        "offline_session_id": lease["lease_id"],
        "sequence": seq,
        "offline_uuid": offline_uuid,
        "sold_at_client_utc": sold_at,
        "client_monotonic_ms": monotonic_ms,
        "monotonic_valid": monotonic_valid,
        "server_anchor_id": lease["server_anchor_id"],
        "catalog_version": lease["catalog_version"],
        "catalog_snapshot_digest": lease["catalog_snapshot_digest"],
        "client_fingerprint": client_fp or "fsofr1:" + "0" * 64,
        "items": items,
        "cash_tendered": sum(i["unit_price_vnd"] * i["quantity"] for i in items),
    }
    if client_fp is None:
        payload["client_fingerprint"] = _independent_v1_digest(
            shop_id=int(lease["shop_id"]), payload=payload
        )
    return payload


def _canonical_client_time(value: str) -> str:
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")


def _independent_v1_digest(*, shop_id: int, payload: dict) -> str:
    """Independent test encoder; deliberately does not call production code."""
    fs, rs = "\x1f", "\x1e"
    rows = []
    total = 0
    for item in payload["items"]:
        name = " ".join(unicodedata.normalize("NFC", item["product_name"]).split())
        row = (
            item["product_id"],
            name,
            item["unit_price_vnd"],
            item["quantity"],
        )
        rows.append(row)
        total += row[2] * row[3]
    rows.sort(key=lambda row: (row[0], row[1].encode("utf-8"), row[2], row[3]))
    document = "FS-OFFLINE-RECEIPT-v1\n"
    document += fs.join(
        map(
            str,
            (
                shop_id,
                1,
                payload["lease_id"],
                payload["device_id"],
                payload["offline_session_id"],
                payload["sequence"],
                payload["offline_uuid"],
            ),
        )
    ) + "\n"
    document += fs.join(
        (
            _canonical_client_time(payload["sold_at_client_utc"]),
            str(payload["client_monotonic_ms"]),
            "1" if payload["monotonic_valid"] else "0",
            payload["server_anchor_id"],
        )
    ) + "\n"
    document += fs.join(
        (str(payload["catalog_version"]), payload["catalog_snapshot_digest"])
    ) + "\n"
    document += fs.join(
        ("CASH", str(payload["cash_tendered"]), str(total), str(len(rows)))
    ) + "\n"
    document += rs.join(fs.join(map(str, row)) for row in rows) + "\n"
    return "fsofr1:" + hashlib.sha256(document.encode("utf-8")).hexdigest()


def _sync_v1(
    client: TestClient,
    shop_id: int,
    token: str,
    lease: dict,
    payload: dict,
) -> TestClient:
    return client.post(
        f"/api/orders/{shop_id}/offline",
        json=payload,
        headers={
            **_auth_header(token),
            "X-Offline-Lease-Token": lease["lease_token"],
        },
    )


# ---------------------------------------------------------------------------
# Fingerprint v1 — known vector
# ---------------------------------------------------------------------------

def test_v1_fingerprint_known_vector():
    """Canonical fingerprint v1 produces fsofr1: digest, server-computes total."""
    from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1

    result = fingerprint_offline_receipt_v1(
        shop_id=1,
        sold_at_client_utc="2025-07-15 09:30:00.123456",
        client_monotonic_ms=5000,
        monotonic_valid=True,
        server_anchor_id="anc_abc123",
        lease_id="lease-001",
        device_id="dev-001",
        offline_session_id="session-001",
        sequence=1,
        offline_uuid="uuid-test-001",
        catalog_version=1,
        catalog_snapshot_digest="sha256:deadbeef",
        items=[
            {"product_id": 10, "product_name": "Súa  ", "unit_price_vnd": 15000, "quantity": 2},
            {"product_id": 5, "product_name": "Bánh", "unit_price_vnd": 25000, "quantity": 1},
        ],
        cash_tendered_vnd=55000,
    )

    assert result.digest == (
        "fsofr1:8fdaa734e0088f5b23c40ad1d411f56e07836fb4114613400230762f54320e22"
    )
    assert result.total_vnd == 55000
    assert result.item_count == 2
    assert result.items[0].product_id == 5
    assert result.items[1].product_id == 10
    # NFC: COMBINING ACUTE (U+0301) normalizes to precomposed acute
    # "Bánh" → U+00E1 + "nh" = "Bánh"
    # "Súa  " → "Su" + U+00FA + "a" = "Súa"
    assert result.items[0].product_name == "Bánh"
    assert result.items[1].product_name == "Súa"
    result.canonical_bytes.decode("utf-8")


def test_v1_fingerprint_nfc_unicode():
    """NFC normalization makes equivalent Unicode forms produce same digest."""
    from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1

    result1 = fingerprint_offline_receipt_v1(
        shop_id=1,
        sold_at_client_utc="2025-07-15 09:30:00.000000",
        client_monotonic_ms=0,
        monotonic_valid=True,
        server_anchor_id="anc_test",
        lease_id="lease-test",
        device_id="dev",
        offline_session_id="session",
        sequence=1,
        offline_uuid="uuid-nfc",
        catalog_version=0,
        catalog_snapshot_digest="digest",
        items=[{"product_id": 1, "product_name": "  Cáfé\u3000", "unit_price_vnd": 10000, "quantity": 1}],
        cash_tendered_vnd=10000,
    )

    result2 = fingerprint_offline_receipt_v1(
        shop_id=1,
        sold_at_client_utc="2025-07-15 09:30:00.000000",
        client_monotonic_ms=0,
        monotonic_valid=True,
        server_anchor_id="anc_test",
        lease_id="lease-test",
        device_id="dev",
        offline_session_id="session",
        sequence=1,
        offline_uuid="uuid-nfc",
        catalog_version=0,
        catalog_snapshot_digest="digest",
        items=[{"product_id": 1, "product_name": "Cáfé", "unit_price_vnd": 10000, "quantity": 1}],
        cash_tendered_vnd=10000,
    )

    assert result1.items[0].product_name == result2.items[0].product_name == "Cáfé"
    assert result1.canonical_bytes == result2.canonical_bytes


def test_v1_fingerprint_forbidden_char_rejected():
    """Forbidden characters (U+2028 LINE SEPARATOR) are rejected."""
    from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1

    with pytest.raises(ValueError):
        fingerprint_offline_receipt_v1(
            shop_id=1,
            sold_at_client_utc="2025-07-15 09:30:00.000000",
            client_monotonic_ms=0,
            monotonic_valid=True,
            server_anchor_id="anc",
            lease_id="lease",
            device_id="dev",
            offline_session_id="session",
            sequence=1,
            offline_uuid="uuid",
            catalog_version=0,
            catalog_snapshot_digest="digest",
            # U+2028 is LINE SEPARATOR -- forbidden per spec
            items=[{"product_id": 1, "product_name": "Test Null", "unit_price_vnd": 100, "quantity": 1}],
            cash_tendered_vnd=100,
        )


def test_v1_fingerprint_300_codepoint_limit():
    """Product name > 300 codepoints is rejected."""
    from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1

    with pytest.raises(ValueError):
        fingerprint_offline_receipt_v1(
            shop_id=1,
            sold_at_client_utc="2025-07-15 09:30:00.000000",
            client_monotonic_ms=0,
            monotonic_valid=True,
            server_anchor_id="anc",
            lease_id="lease",
            device_id="dev",
            offline_session_id="session",
            sequence=1,
            offline_uuid="uuid",
            catalog_version=0,
            catalog_snapshot_digest="digest",
            items=[{"product_id": 1, "product_name": "X" * 301, "unit_price_vnd": 100, "quantity": 1}],
            cash_tendered_vnd=100,
        )


def test_v1_fingerprint_duplicate_items_preserved():
    """Duplicate rows are kept intact and sorted together."""
    from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1

    result = fingerprint_offline_receipt_v1(
        shop_id=1,
        sold_at_client_utc="2025-07-15 09:30:00.000000",
        client_monotonic_ms=0,
        monotonic_valid=True,
        server_anchor_id="anc",
        lease_id="lease",
        device_id="dev",
        offline_session_id="session",
        sequence=1,
        offline_uuid="uuid-dup",
        catalog_version=0,
        catalog_snapshot_digest="digest",
        items=[
            {"product_id": 5, "product_name": "A", "unit_price_vnd": 100, "quantity": 1},
            {"product_id": 5, "product_name": "A", "unit_price_vnd": 100, "quantity": 1},
        ],
        cash_tendered_vnd=200,
    )

    assert result.item_count == 2
    assert result.items[0].product_id == 5
    assert result.items[1].product_id == 5


# ---------------------------------------------------------------------------
# v1 ingest -- time contract
# ---------------------------------------------------------------------------

def test_v1_anchored_client_confidence(client, db, monkeypatch, lease_enabled):
    """monotonic_valid=True -> ANCHORED_CLIENT confidence."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    # Use product from seller_with_shop (product_id = 1)
    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]

    payload = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456",
        0, True, 1, "uuid-anchored", "fsofr1:" + "0" * 64
    )
    resp = _sync_v1(client, shop_id, token, lease, payload)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["contract_version"] == 1
    assert data["time_confidence"] == "ANCHORED_CLIENT"
    assert data["sold_at_effective"] == "2025-07-15 09:30:00.123456"


def test_v1_body_cap_64kib_rejected(client, db, monkeypatch, lease_enabled):
    """Body > 64 KiB -> 413 OFFLINE_BODY_TOO_LARGE."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    large_payload = {"x": "x" * (65 * 1024)}
    resp = client.post(
        f"/api/orders/{shop_id}/offline",
        content=json.dumps(large_payload).encode("utf-8"),
        headers={
            **_auth_header(token),
            "X-Offline-Lease-Token": lease["lease_token"],
        },
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "OFFLINE_BODY_TOO_LARGE"


def test_v1_200_items_max(client, db, monkeypatch, lease_enabled):
    """> 200 items -> 422 OFFLINE_RECEIPT_MALFORMED."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    payload = {
        "offline_contract_version": 1,
        "lease_id": lease["lease_id"],
        "device_id": lease["device_id"],
        "offline_session_id": lease["lease_id"],
        "sequence": 1,
        "offline_uuid": "uuid-many-items",
        "sold_at_client_utc": "2025-07-15 09:30:00.000000",
        "client_monotonic_ms": 0,
        "monotonic_valid": True,
        "server_anchor_id": lease["server_anchor_id"],
        "catalog_version": lease["catalog_version"],
        "catalog_snapshot_digest": lease["catalog_snapshot_digest"],
        "client_fingerprint": "fsofr1:" + "0" * 64,
        "items": [
            {"product_id": 1, "product_name": f"Item {i}", "unit_price_vnd": 1000, "quantity": 1}
            for i in range(201)
        ],
        "cash_tendered": 201000,
    }
    resp = _sync_v1(client, shop_id, token, lease, payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "OFFLINE_RECEIPT_MALFORMED"


def test_v1_revoked_lease_returns_409(client, db, monkeypatch, lease_enabled):
    """Revoked lease -> 409 OFFLINE_LEASE_REVOKED."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    client.request(
        "DELETE",
        f"/api/offline/leases/{lease['lease_id']}",
        headers=_auth_header(token),
        json={"reason": "Test revoke"},
    )

    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]
    payload = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-revoked", "fsofr1:" + "0" * 64
    )
    resp = _sync_v1(client, shop_id, token, lease, payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "OFFLINE_LEASE_REVOKED"
    assert _receipt_rows("uuid-revoked")[0] is None


def test_v1_sequence_conflict_returns_409(client, db):
    """Same lease+session+sequence, different UUID -> 409 SEQUENCE_CONFLICT."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]

    payload1 = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-seq-1", "fsofr1:" + "0" * 64
    )
    resp1 = _sync_v1(client, shop_id, token, lease, payload1)
    assert resp1.status_code == 200, resp1.json()

    payload2 = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-seq-2", "fsofr1:" + "0" * 64
    )
    resp2 = _sync_v1(client, shop_id, token, lease, payload2)
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["code"] == "OFFLINE_SEQUENCE_CONFLICT"


def test_v1_fingerprint_mismatch_opens_info_issue(client, db):
    """Client fingerprint != server -> FINGERPRINT_LECH INFO issue on first ingest."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]
    wrong_fp = "fsofr1:0000000000000000000000000000000000000000000000000000000000000000"

    payload = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-fp-lech", wrong_fp
    )
    resp = _sync_v1(client, shop_id, token, lease, payload)
    assert resp.status_code == 200, resp.json()
    assert "FINGERPRINT_LECH" in resp.json().get("issues", [])


def test_v1_retry_does_not_open_second_issue(client, db):
    """Retry (same UUID + same server fingerprint) -> no new FINGERPRINT_LECH issue."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]
    wrong_fp = "fsofr1:0000000000000000000000000000000000000000000000000000000000000000"

    payload = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-retry-noissue", wrong_fp
    )

    resp1 = _sync_v1(client, shop_id, token, lease, payload)
    assert resp1.status_code == 200
    assert resp1.json()["created"] is True

    resp2 = _sync_v1(client, shop_id, token, lease, payload)
    assert resp2.status_code == 200
    assert resp2.json()["created"] is False


def test_v0_v1_contract_versioning(client, db):
    """No offline_contract_version -> v0 path (unchanged)."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    from datetime import datetime
    now = datetime.utcnow()
    payload = {
        "offline_uuid": "uuid-v0-unchanged",
        "sold_at": now.isoformat(),
        "items": [{"product_id": 1, "product_name": "Test", "unit_price": 10000, "quantity": 1}],
        "cash_tendered": 10000,
    }
    resp = client.post(
        f"/api/orders/{shop_id}/offline",
        json=payload,
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "contract_version" not in data


def test_v1_response_contract(client, db):
    """v1 response has all required fields."""
    setup = seller_with_shop(client)
    shop_id = setup["shop_id"]
    token = setup["token"]

    lease = _issue_lease(client, shop_id, token)
    items = [{"product_id": 1, "product_name": "Test", "unit_price_vnd": 10000, "quantity": 1}]

    payload = _v1_payload(
        lease, items,
        "2025-07-15 09:30:00.123456", 0, True, 1, "uuid-response", "fsofr1:" + "0" * 64
    )
    resp = _sync_v1(client, shop_id, token, lease, payload)
    assert resp.status_code == 200
    data = resp.json()

    required = ["contract_version", "order_id", "offline_uuid", "sold_by_user_id",
                "synced_by_user_id", "sold_at_effective", "time_confidence", "server_time_utc"]
    for field in required:
        assert field in data, f"Missing field: {field}"
    assert data["contract_version"] == 1
    response_text = json.dumps(data)
    assert lease["lease_token"] not in response_text
    assert payload["client_fingerprint"] not in response_text
    assert "catalog_snapshot_digest" not in data


# ---------------------------------------------------------------------------
# Full I09-E+B2 regression matrix
# ---------------------------------------------------------------------------


def _items(product: dict, *, name: str | None = None, price: int | None = None):
    return [
        {
            "product_id": product["id"],
            "product_name": name or product["name"],
            "unit_price_vnd": product["price"] if price is None else price,
            "quantity": 1,
        }
    ]


def _receipt_rows(uuid: str):
    with SessionLocal() as session:
        order = (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == uuid)
            .first()
        )
        receipt = (
            session.query(models.OfflineReceipt)
            .filter(models.OfflineReceipt.offline_uuid == uuid)
            .first()
        )
        issues = [] if order is None else (
            session.query(models.OfflineReceiptIssue)
            .filter(models.OfflineReceiptIssue.order_id == order.id)
            .all()
        )
        payment_count = 0 if order is None else (
            session.query(models.OrderPayment)
            .filter(models.OrderPayment.order_id == order.id)
            .count()
        )
        item_count = 0 if order is None else (
            session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == order.id)
            .count()
        )
        return order, receipt, issues, payment_count, item_count


def test_v1_reorder_same_digest_name_change_conflicts_and_duplicate_rows_persist(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    rows = [
        {"product_id": ctx["product"]["id"], "product_name": "B", "unit_price_vnd": 40_000, "quantity": 1},
        {"product_id": ctx["product"]["id"], "product_name": "A", "unit_price_vnd": 30_000, "quantity": 1},
    ]
    payload = _v1_payload(
        lease, rows, lease["anchor_server_time_utc"], 0, True, 1,
        "uuid-v1-reorder", None,
    )
    assert _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload).status_code == 200

    reordered = dict(payload)
    reordered["items"] = list(reversed(payload["items"]))
    # Item order is intentionally absent from the canonical identity.
    assert _independent_v1_digest(shop_id=ctx["shop_id"], payload=reordered) == payload["client_fingerprint"]
    retry = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, reordered)
    assert retry.status_code == 200 and retry.json()["created"] is False

    changed = json.loads(json.dumps(payload))
    changed["items"][0]["product_name"] = "Tên khác"
    changed["client_fingerprint"] = _independent_v1_digest(shop_id=ctx["shop_id"], payload=changed)
    conflict = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "OFFLINE_RECEIPT_FINGERPRINT_CONFLICT"

    duplicate = _v1_payload(
        lease,
        [rows[0], dict(rows[0])],
        lease["anchor_server_time_utc"],
        1_000,
        True,
        2,
        "uuid-v1-duplicate-lines",
    )
    result = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, duplicate)
    assert result.status_code == 200, result.text
    assert _receipt_rows("uuid-v1-duplicate-lines")[4] == 2
    with SessionLocal() as session:
        receipt = session.query(models.OfflineReceipt).filter_by(
            offline_uuid="uuid-v1-duplicate-lines"
        ).one()
        snapshots = session.query(models.OfflineReceiptItem).filter_by(
            receipt_id=receipt.id
        ).order_by(models.OfflineReceiptItem.item_ordinal).all()
        assert [row.item_ordinal for row in snapshots] == [1, 2]
        assert len({row.order_item_id for row in snapshots}) == 2
        assert [row.claimed_product_id for row in snapshots] == [rows[0]["product_id"]] * 2


def test_v1_missing_product_nulls_business_fk_and_keeps_claimed_snapshot(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    claimed_product_id = ctx["product"]["id"] + 1_000_000
    payload = _v1_payload(
        lease,
        [{
            "product_id": claimed_product_id,
            "product_name": "Hàng đã xóa",
            "unit_price_vnd": 12_000,
            "quantity": 1,
        }],
        lease["anchor_server_time_utc"],
        0,
        True,
        1,
        "uuid-v1-deleted-product",
    )
    first = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    retry = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert first.status_code == retry.status_code == 200
    assert retry.json()["created"] is False
    order, _receipt, issues, _payments, item_count = _receipt_rows(payload["offline_uuid"])
    assert item_count == 1
    with SessionLocal() as session:
        item = session.query(models.OrderItem).filter_by(order_id=order.id).one()
        snapshot = session.query(models.OfflineReceiptItem).filter_by(
            order_item_id=item.id
        ).one()
        assert item.product_id is None
        assert snapshot.claimed_product_id == claimed_product_id
        assert (snapshot.product_name, snapshot.unit_price_vnd, snapshot.quantity) == (
            "Hàng đã xóa", 12_000, 1
        )
        order_id, order_item_id = order.id, item.id
        stock_before = session.get(models.Product, ctx["product"]["id"]).stock
        before = {
            "returns": session.query(models.OrderReturn).filter_by(order_id=order.id).count(),
            "payments": session.query(models.OrderPayment).filter_by(order_id=order.id).count(),
            "loyalty": session.query(models.LoyaltyPointEntry).filter_by(order_id=order.id).count(),
            "logs": session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count(),
        }

    blocked = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(ctx["token"]),
        json={
            "operation_id": "return-missing-product-restock",
            "items": [{"order_item_id": order_item_id, "quantity": 1, "restock": True}],
            "method": "transfer",
            "reason": "Kiểm thử hoàn offline",
            "reference": "TEST-RETURN",
        },
    )
    assert blocked.status_code == 409, blocked.text
    with SessionLocal() as session:
        item = session.get(models.OrderItem, order_item_id)
        assert session.get(models.Product, ctx["product"]["id"]).stock == stock_before
        assert (item.returned_total_qty, item.cost_return_version) == (0, 0)
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"]
        assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"]
        assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
        assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"]

    no_restock_body = {
        "operation_id": "return-missing-product-no-restock",
        "items": [{"order_item_id": order_item_id, "quantity": 1, "restock": False}],
        "method": "transfer",
        "reason": "Kiểm thử hoàn offline",
        "reference": "TEST-RETURN",
    }
    accepted = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(ctx["token"]),
        json=no_restock_body,
    )
    accepted_retry = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(ctx["token"]),
        json=no_restock_body,
    )
    assert accepted.status_code == accepted_retry.status_code == 200
    assert accepted.json()["return"]["id"] == accepted_retry.json()["return"]["id"]
    assert accepted.json()["return"]["refund_amount"] == 12_000
    with SessionLocal() as session:
        item = session.get(models.OrderItem, order_item_id)
        returned = session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
            models.OrderReturn.order_id == order_id
        ).one()
        refund = session.query(models.OrderPayment).filter_by(
            order_id=order_id, entry_type="RETURN_TRANSFER"
        ).one()
        assert returned.product_id is None and returned.restocked == 0
        assert returned.refund_amount == refund.amount == 12_000
        assert (item.returned_total_qty, item.cost_return_version) == (1, 1)
        assert item.returned_known_qty + item.returned_unknown_qty == 1
        assert item.returned_refund_vnd == 12_000
        assert (
            returned.cost_known_qty,
            returned.cost_unknown_qty,
            returned.cost_basis_vnd,
        ) == (
            item.returned_known_qty,
            item.returned_unknown_qty,
            item.returned_cost_basis_vnd,
        )
        assert session.get(models.Product, ctx["product"]["id"]).stock == stock_before
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"] + 1
        assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"] + 1
        assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
        assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"] + 1
    assert [issue.issue_code for issue in issues].count("SP_KHONG_CON") == 1
    _TEST_MIGRATIONS.verify()


def test_v1_snapshot_ordinal_must_remain_canonical_order(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    payload = _v1_payload(
        lease,
        [
            {"product_id": ctx["product"]["id"], "product_name": "B", "unit_price_vnd": 20_000, "quantity": 1},
            {"product_id": ctx["product"]["id"], "product_name": "A", "unit_price_vnd": 10_000, "quantity": 1},
        ],
        lease["anchor_server_time_utc"],
        0,
        True,
        1,
        "uuid-v1-snapshot-order-corrupt",
    )
    assert _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload).status_code == 200
    with SessionLocal() as session:
        receipt = session.query(models.OfflineReceipt).filter_by(
            offline_uuid=payload["offline_uuid"]
        ).one()
        receipt_id = receipt.id
        rows = session.query(models.OfflineReceiptItem).filter_by(
            receipt_id=receipt_id
        ).order_by(models.OfflineReceiptItem.item_ordinal).all()
        assert [row.product_name for row in rows] == ["A", "B"]
        rows[0].item_ordinal = 3
        session.flush()
        rows[1].item_ordinal = 1
        session.flush()
        rows[0].item_ordinal = 2
        session.commit()

    retry = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "OFFLINE_RECEIPT_REGISTRY_INCONSISTENT"
    revision_0006 = next(
        spec.module for spec in _TEST_MIGRATIONS._graph().revisions
        if spec.revision == "0006_i09e_offline_receipt_items"
    )
    with SessionLocal() as session, pytest.raises(
        RuntimeError, match="I09E_VERIFY_ITEM_CANONICAL_ORDER"
    ):
        revision_0006.verify(session.connection())

    with SessionLocal() as session:
        rows = session.query(models.OfflineReceiptItem).filter_by(
            receipt_id=receipt_id
        ).order_by(models.OfflineReceiptItem.item_ordinal).all()
        rows[0].item_ordinal = 3
        session.flush()
        rows[1].item_ordinal = 1
        session.flush()
        rows[0].item_ordinal = 2
        session.commit()


def test_v1_cross_shop_claim_never_becomes_inventory_fk_and_return_is_atomic(client):
    shop_a = seller_with_shop(client)
    shop_b = seller_with_shop(client)
    lease = _issue_lease(client, shop_a["shop_id"], shop_a["token"])
    product_b_id = shop_b["product"]["id"]
    stock_b_before = shop_b["product"]["stock"]
    payload = _v1_payload(
        lease,
        [{
            "product_id": product_b_id,
            "product_name": "Claim shop B",
            "unit_price_vnd": 17_000,
            "quantity": 1,
        }],
        lease["anchor_server_time_utc"],
        0,
        True,
        1,
        "uuid-v1-cross-shop-claim",
    )

    first = _sync_v1(client, shop_a["shop_id"], shop_a["token"], lease, payload)
    retry = _sync_v1(client, shop_a["shop_id"], shop_a["token"], lease, payload)
    assert first.status_code == retry.status_code == 200
    assert first.json()["created"] is True and retry.json()["created"] is False
    assert "SP_KHONG_CON" in first.json()["issues"]

    with SessionLocal() as session:
        order = session.query(models.Order).filter_by(
            offline_uuid=payload["offline_uuid"]
        ).one()
        item = session.query(models.OrderItem).filter_by(order_id=order.id).one()
        snapshot = session.query(models.OfflineReceiptItem).filter_by(
            order_item_id=item.id
        ).one()
        assert item.product_id is None
        assert snapshot.claimed_product_id == product_b_id
        assert session.get(models.Product, product_b_id).stock == stock_b_before
        before = {
            "returns": session.query(models.OrderReturn).filter_by(order_id=order.id).count(),
            "payments": session.query(models.OrderPayment).filter_by(order_id=order.id).count(),
            "loyalty": session.query(models.LoyaltyPointEntry).filter_by(order_id=order.id).count(),
            "logs": session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count(),
        }
        order_id, order_item_id = order.id, item.id

    failed = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(shop_a["token"]),
        json={
            "operation_id": "return-cross-shop-claim",
            "items": [{"order_item_id": order_item_id, "quantity": 1, "restock": True}],
            "method": "transfer",
            "reason": "Kiểm thử hoàn offline",
            "reference": "TEST-RETURN",
        },
    )
    assert failed.status_code == 409, failed.text

    with SessionLocal() as session:
        item = session.get(models.OrderItem, order_item_id)
        assert session.get(models.Product, product_b_id).stock == stock_b_before
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"]
        assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"]
        assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
        assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"]
        assert (item.returned_total_qty, item.cost_return_version) == (0, 0)

    no_restock_body = {
        "operation_id": "return-cross-shop-no-restock",
        "items": [{"order_item_id": order_item_id, "quantity": 1, "restock": False}],
        "method": "transfer",
        "reason": "Kiểm thử hoàn offline",
        "reference": "TEST-RETURN",
    }
    accepted = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(shop_a["token"]),
        json=no_restock_body,
    )
    accepted_retry = client.post(
        f"/api/orders/{order_id}/returns",
        headers=auth(shop_a["token"]),
        json=no_restock_body,
    )
    assert accepted.status_code == accepted_retry.status_code == 200
    assert accepted.json()["return"]["id"] == accepted_retry.json()["return"]["id"]
    assert accepted.json()["return"]["refund_amount"] == 17_000
    with SessionLocal() as session:
        item = session.get(models.OrderItem, order_item_id)
        returned = session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
            models.OrderReturn.order_id == order_id
        ).one()
        refund = session.query(models.OrderPayment).filter_by(
            order_id=order_id, entry_type="RETURN_TRANSFER"
        ).one()
        assert returned.product_id is None and returned.restocked == 0
        assert returned.refund_amount == refund.amount == 17_000
        assert session.get(models.Product, product_b_id).stock == stock_b_before
        assert (item.returned_total_qty, item.cost_return_version) == (1, 1)
        assert item.returned_refund_vnd == 17_000
        assert (
            returned.cost_known_qty,
            returned.cost_unknown_qty,
            returned.cost_basis_vnd,
        ) == (
            item.returned_known_qty,
            item.returned_unknown_qty,
            item.returned_cost_basis_vnd,
        )
        assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"] + 1
        assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"] + 1
        assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
        assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"] + 1
    _TEST_MIGRATIONS.verify()


def test_client_fingerprint_mismatch_is_one_resolved_info_issue_and_conflict_atomic(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    payload = _v1_payload(
        lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
        0, True, 1, "uuid-v1-mismatch", "fsofr1:" + "0" * 64,
    )
    first = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    second = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert (first.status_code, second.status_code) == (200, 200)
    assert (first.json()["created"], second.json()["created"]) == (True, False)
    order, receipt, issues, payment_count, _ = _receipt_rows("uuid-v1-mismatch")
    fp_issues = [issue for issue in issues if issue.issue_code == "FINGERPRINT_LECH"]
    assert receipt.client_fingerprint_mismatch == 1
    assert len(fp_issues) == 1
    assert (fp_issues[0].severity, fp_issues[0].state, fp_issues[0].resolution_kind) == (
        "INFO", "RESOLVED", "INFORMATIONAL_AT_INGEST"
    )
    stock_after = ctx["product"]["stock"] - 1

    changed = json.loads(json.dumps(payload))
    changed["cash_tendered"] += 1
    changed["client_fingerprint"] = _independent_v1_digest(shop_id=ctx["shop_id"], payload=changed)
    conflict = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "OFFLINE_RECEIPT_FINGERPRINT_CONFLICT"
    with SessionLocal() as session:
        assert session.get(models.Product, ctx["product"]["id"]).stock == stock_after
        assert session.query(models.OrderPayment).filter(models.OrderPayment.order_id == order.id).count() == payment_count == 1


def test_sequence_conflict_concurrent_has_one_financial_winner(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    payloads = [
        _v1_payload(
            lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
            1_000, True, 1, f"uuid-v1-race-{suffix}",
        )
        for suffix in ("a", "b")
    ]

    def send(payload):
        return _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, payloads))
    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["detail"]["code"] == "OFFLINE_SEQUENCE_CONFLICT"
    with SessionLocal() as session:
        winners = session.query(models.Order).filter(
            models.Order.offline_uuid.in_([payload["offline_uuid"] for payload in payloads])
        ).all()
        assert len(winners) == 1
        assert session.query(models.OrderPayment).filter(models.OrderPayment.order_id == winners[0].id).count() == 1
        assert session.query(models.SystemLog).filter(
            models.SystemLog.action == "OFFLINE_SALE_V1",
            models.SystemLog.shop_id == ctx["shop_id"],
        ).count() == 1
        assert session.get(models.Product, ctx["product"]["id"]).stock == ctx["product"]["stock"] - 1

    # Lost-response retry returns the same durable winner and does not mutate.
    winner_payload = next(
        payload for payload, response in zip(payloads, responses) if response.status_code == 200
    )
    retry = send(winner_payload)
    assert retry.status_code == 200 and retry.json()["created"] is False


def test_anchored_wall_clock_anomaly_and_fake_delta_remains_untrusted(client, monkeypatch):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    anchor = datetime.strptime(lease["anchor_server_time_utc"], "%Y-%m-%d %H:%M:%S.%f")
    request_now = anchor + timedelta(minutes=10)
    monkeypatch.setattr(offline_service, "_utcnow", lambda: request_now)

    wall_plus_three_hours = _v1_payload(
        lease, _items(ctx["product"]),
        (anchor + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        60_000, True, 1, "uuid-v1-wall-anomaly",
    )
    response = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, wall_plus_three_hours)
    assert response.status_code == 200, response.text
    assert response.json()["sold_at_effective"] == (anchor + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert response.json()["time_confidence"] == "ANOMALY"
    assert "DONG_HO_LECH" in response.json()["issues"]

    # An in-window client-declared delta is accepted as ANCHORED_CLIENT.  This
    # is deliberately not proof of the human sale time; KIEN_TRUC documents the
    # lease/revocation trust boundary.
    fake_delta = _v1_payload(
        lease, _items(ctx["product"]),
        (anchor + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        120_000, True, 2, "uuid-v1-fake-delta",
    )
    accepted = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, fake_delta)
    assert accepted.status_code == 200
    assert accepted.json()["time_confidence"] == "ANCHORED_CLIENT"


def test_reload_bounded_exact_lower_upper_clamp_and_offset_normalization(client, monkeypatch):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    anchor = datetime.strptime(lease["anchor_server_time_utc"], "%Y-%m-%d %H:%M:%S.%f")
    monkeypatch.setattr(offline_service, "_utcnow", lambda: anchor)
    payload = _v1_payload(
        lease,
        _items(ctx["product"]),
        (anchor + timedelta(hours=7, minutes=4)).replace(tzinfo=timezone(timedelta(hours=7))).isoformat(),
        0,
        False,
        1,
        "uuid-v1-bounded",
    )
    # The offset-aware wall instant is anchor+4m UTC; request+2m is the tighter
    # upper bound, so effective clamps there without becoming a >10m anomaly.
    response = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert response.status_code == 200, response.text
    expected_upper = (anchor + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert response.json()["sold_at_effective"] == expected_upper
    assert response.json()["time_confidence"] == "BOUNDED"
    _, receipt, _, _, _ = _receipt_rows("uuid-v1-bounded")
    assert receipt.sold_at_upper_bound == expected_upper
    assert receipt.sold_at_client_utc == (anchor + timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert len(receipt.sold_at_effective) == len(receipt.sold_at_client_utc) == 26


def test_predecessor_and_successor_non_monotonic_open_one_action_issue(client, monkeypatch):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    anchor = datetime.strptime(lease["anchor_server_time_utc"], "%Y-%m-%d %H:%M:%S.%f")
    monkeypatch.setattr(offline_service, "_utcnow", lambda: anchor + timedelta(minutes=10))

    def send(seq: int, delta: int, uuid: str):
        wall = (anchor + timedelta(milliseconds=delta)).strftime("%Y-%m-%d %H:%M:%S.%f")
        payload = _v1_payload(lease, _items(ctx["product"]), wall, delta, True, seq, uuid)
        return _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)

    assert send(1, 3_000, "uuid-v1-seq-1").json()["time_confidence"] == "ANCHORED_CLIENT"
    predecessor_bad = send(2, 2_000, "uuid-v1-seq-2")
    assert predecessor_bad.json()["time_confidence"] == "ANOMALY"
    assert send(4, 4_000, "uuid-v1-seq-4").json()["time_confidence"] == "ANCHORED_CLIENT"
    successor_bad = send(3, 5_000, "uuid-v1-seq-3")
    assert successor_bad.json()["time_confidence"] == "ANOMALY"
    for uuid in ("uuid-v1-seq-2", "uuid-v1-seq-3"):
        issues = [issue for issue in _receipt_rows(uuid)[2] if issue.issue_code == "DONG_HO_LECH"]
        assert len(issues) == 1
        assert (issues[0].severity, issues[0].state) == ("ACTION", "OPEN")


@pytest.mark.parametrize(
    ("delta", "expected_status", "expected_code"),
    [
        (timedelta(hours=12, milliseconds=1), 409, "OFFLINE_LEASE_EXPIRED"),
        (timedelta(minutes=2, milliseconds=1), 400, "OFFLINE_TIME_FUTURE"),
    ],
)
def test_after_expiry_and_future_boundaries_are_atomic(
    client, monkeypatch, delta, expected_status, expected_code
):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    anchor = datetime.strptime(lease["anchor_server_time_utc"], "%Y-%m-%d %H:%M:%S.%f")
    payload = _v1_payload(
        lease, _items(ctx["product"]),
        (anchor + delta).strftime("%Y-%m-%d %H:%M:%S.%f"),
        int(delta.total_seconds() * 1000), True, 1,
        f"uuid-v1-boundary-{expected_status}",
    )
    response = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert _receipt_rows(payload["offline_uuid"])[0] is None
    with SessionLocal() as session:
        assert session.get(models.Product, ctx["product"]["id"]).stock == ctx["product"]["stock"]


def test_future_exact_plus_two_minutes_is_accepted(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    anchor = datetime.strptime(lease["anchor_server_time_utc"], "%Y-%m-%d %H:%M:%S.%f")
    payload = _v1_payload(
        lease,
        _items(ctx["product"]),
        (anchor + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        120_000,
        True,
        1,
        "uuid-v1-future-exact-two",
    )
    response = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert response.status_code == 200, response.text
    assert response.json()["sold_at_effective"] == payload["sold_at_client_utc"]


def test_before_issued_and_grace_hour_72_73_exact(client, monkeypatch):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    issued = datetime.strptime(lease["issued_at"], "%Y-%m-%d %H:%M:%S.%f")
    expires = datetime.strptime(lease["expires_at"], "%Y-%m-%d %H:%M:%S.%f")

    # Make the durable anchor one microsecond before issued to exercise the
    # explicit lower boundary; no financial row may appear.
    with SessionLocal() as session:
        row = session.get(models.OfflineLease, lease["lease_id"])
        row.anchor_server_time_utc = (issued - timedelta(microseconds=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
        session.commit()
    lease["anchor_server_time_utc"] = (issued - timedelta(microseconds=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
    before = _v1_payload(
        lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
        0, True, 1, "uuid-v1-before-issued",
    )
    response = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, before)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OFFLINE_TIME_BEFORE_LEASE"
    assert _receipt_rows("uuid-v1-before-issued")[0] is None

    # Restore a valid anchor, then show exact hour 72 is accepted for an old
    # effective time while hour 73 is rejected before financial mutation.
    with SessionLocal() as session:
        row = session.get(models.OfflineLease, lease["lease_id"])
        row.anchor_server_time_utc = lease["issued_at"]
        session.commit()
    lease["anchor_server_time_utc"] = lease["issued_at"]
    monkeypatch.setattr(offline_service, "_utcnow", lambda: expires + timedelta(hours=72))
    within = _v1_payload(
        lease, _items(ctx["product"]), lease["issued_at"],
        0, True, 2, "uuid-v1-grace-72",
    )
    accepted = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, within)
    assert accepted.status_code == 200, accepted.text

    monkeypatch.setattr(offline_service, "_utcnow", lambda: expires + timedelta(hours=73))
    beyond = _v1_payload(
        lease, _items(ctx["product"]), lease["issued_at"],
        0, True, 3, "uuid-v1-grace-73",
    )
    rejected = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, beyond)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "OFFLINE_LEASE_EXPIRED"
    assert _receipt_rows("uuid-v1-grace-73")[0] is None


def test_cashier_attribution_shift_payment_and_binding_fail_closed(client):
    owner = seller_with_shop(client)
    cashier_name, cashier_token = new_staff(client, owner, "CASHIER")
    _other_name, other_token = new_staff(client, owner, "MANAGER")
    lease = _issue_lease(client, owner["shop_id"], cashier_token, "cashier-device")
    shift = client.post(
        f"/api/shifts/{owner['shop_id']}/open",
        json={"opening_cash_amount": 0},
        headers=auth(cashier_token),
    )
    assert shift.status_code == 200, shift.text
    with SessionLocal() as session:
        cashier = session.query(models.User).filter(models.User.username == cashier_name).one()
        shift_row = session.get(models.CashShift, shift.json()["id"])
        shift_row.opened_at = BASE_NOW - timedelta(minutes=1)
        session.commit()
        cashier_id = cashier.id

    payload = _v1_payload(
        lease, _items(owner["product"]), lease["anchor_server_time_utc"],
        0, True, 1, "uuid-v1-cashier",
    )
    ok = _sync_v1(client, owner["shop_id"], cashier_token, lease, payload)
    assert ok.status_code == 200, ok.text
    with SessionLocal() as session:
        order = session.get(models.Order, ok.json()["order_id"])
        receipt = session.query(models.OfflineReceipt).filter_by(order_id=order.id).one()
        payment = session.query(models.OrderPayment).filter_by(order_id=order.id).one()
        assert order.created_by_user_id == payment.created_by_user_id == cashier_id
        assert order.shift_id == payment.shift_id == shift.json()["id"]
        assert receipt.sold_by_claimed_user_id == receipt.synced_by_user_id == cashier_id
        assert receipt.attribution_kind == "LEASE_CLAIM"

    other_payload = _v1_payload(
        lease, _items(owner["product"]), lease["anchor_server_time_utc"],
        1_000, True, 2, "uuid-v1-other-staff",
    )
    wrong_actor = _sync_v1(client, owner["shop_id"], other_token, lease, other_payload)
    assert wrong_actor.status_code == 403
    assert _receipt_rows("uuid-v1-other-staff")[0] is None

    leaked_body = dict(other_payload, offline_uuid="uuid-v1-token-in-body")
    leaked_body["lease_token"] = lease["lease_token"]
    body_rejected = client.post(
        f"/api/orders/{owner['shop_id']}/offline",
        json=leaked_body,
        headers=auth(cashier_token),
    )
    assert body_rejected.status_code == 422
    query_rejected = client.post(
        f"/api/orders/{owner['shop_id']}/offline?X-Offline-Lease-Token={lease['lease_token']}",
        json=other_payload,
        headers=auth(cashier_token),
    )
    assert query_rejected.status_code == 403
    assert lease["lease_token"] not in query_rejected.text

    for field, bad_value in (
        ("device_id", "other-device"),
        ("offline_session_id", "lse_000G40R40M30E209185GR3"),
        ("server_anchor_id", "anc_other"),
        ("catalog_version", lease["catalog_version"] + 1),
        ("catalog_snapshot_digest", "0" * 64),
    ):
        bad = _v1_payload(
            lease, _items(owner["product"]), lease["anchor_server_time_utc"],
            2_000, True, 2, f"uuid-v1-bad-{field}",
        )
        bad[field] = bad_value
        bad["client_fingerprint"] = _independent_v1_digest(shop_id=owner["shop_id"], payload=bad)
        response = _sync_v1(client, owner["shop_id"], cashier_token, lease, bad)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "OFFLINE_LEASE_BINDING_MISMATCH"
        assert _receipt_rows(bad["offline_uuid"])[0] is None


def test_contract_dispatch_v0_compatibility_and_stable_errors(client):
    ctx = seller_with_shop(client)
    v0 = {
        "offline_contract_version": 0,
        "offline_uuid": "uuid-v0-explicit-zero",
        "sold_at": BASE_NOW.isoformat(),
        "items": [{
            "product_id": ctx["product"]["id"],
            "product_name": ctx["product"]["name"],
            "unit_price": ctx["product"]["price"],
            "quantity": 1,
        }],
        "cash_tendered": ctx["product"]["price"],
    }
    response = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=v0, headers=auth(ctx["token"])
    )
    assert response.status_code == 200
    with SessionLocal() as session:
        receipt = session.query(models.OfflineReceipt).filter_by(offline_uuid=v0["offline_uuid"]).one()
        assert receipt.contract_version == 0
        assert receipt.attribution_kind == "LEGACY_UNKNOWN"
        assert receipt.lease_id is receipt.server_anchor_id is receipt.sequence is None

    mixed = dict(v0, offline_uuid="uuid-v0-mixed", lease_id="lse_000G40R40M30E209185GR3")
    malformed = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=mixed, headers=auth(ctx["token"])
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "OFFLINE_RECEIPT_MALFORMED"

    bad_version = dict(v0, offline_contract_version=2, offline_uuid="uuid-version-two")
    assert client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=bad_version, headers=auth(ctx["token"])
    ).status_code == 422

    missing_uuid = dict(v0, offline_contract_version=0, offline_uuid="        ")
    missing = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=missing_uuid, headers=auth(ctx["token"])
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "OFFLINE_UUID_MISSING"

    tender = dict(v0, offline_uuid="uuid-v0-tender", cash_tendered=0)
    low = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=tender, headers=auth(ctx["token"])
    )
    assert low.status_code == 400
    assert low.json()["detail"]["code"] == "OFFLINE_TENDER_TOO_LOW"

    future = dict(v0, offline_uuid="uuid-v0-future", sold_at="2999-01-01T00:00:00")
    ahead = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=future, headers=auth(ctx["token"])
    )
    assert ahead.status_code == 400
    assert ahead.json()["detail"]["code"] == "OFFLINE_TIME_FUTURE"

    overflow = dict(v0, offline_uuid="uuid-v0-overflow")
    overflow["items"] = [{
        "product_id": ctx["product"]["id"],
        "product_name": ctx["product"]["name"],
        "unit_price": MAX_SAFE_VND,
        "quantity": 2,
    }]
    overflow["cash_tendered"] = MAX_SAFE_VND
    too_large = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=overflow, headers=auth(ctx["token"])
    )
    assert too_large.status_code == 400
    assert too_large.json()["detail"]["code"] == "OFFLINE_TOTAL_OVERFLOW"

    lease = _issue_lease(client, ctx["shop_id"], ctx["token"], "strict-device")
    strict_payload = _v1_payload(
        lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
        0, True, 1, "uuid-v1-strict-numeric",
    )
    strict_payload["items"][0]["quantity"] = 1.0
    strict_payload["monotonic_valid"] = 1
    strict = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, strict_payload)
    assert strict.status_code == 422
    assert strict.json()["detail"]["code"] == "OFFLINE_RECEIPT_MALFORMED"


def _v0_body_with_size(ctx: dict, size: int) -> bytes:
    payload = {
        "offline_uuid": f"uuid-body-{size}",
        "sold_at": BASE_NOW.isoformat(),
        "items": [{
            "product_id": ctx["product"]["id"],
            "product_name": ctx["product"]["name"],
            "unit_price": ctx["product"]["price"],
            "quantity": 1,
        }],
        "cash_tendered": ctx["product"]["price"],
        "padding": "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload["padding"] = "x" * (size - len(encoded))
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(encoded) == size
    return encoded


def test_body_cap_exact_64kib_lying_length_and_auth_before_read(client, monkeypatch):
    from fselling.routers import orders as orders_router

    ctx = seller_with_shop(client)
    exact = _v0_body_with_size(ctx, 64 * 1024)
    accepted = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        content=exact,
        headers={**auth(ctx["token"]), "Content-Type": "application/json"},
    )
    assert accepted.status_code == 200, accepted.text

    oversized = _v0_body_with_size(ctx, 64 * 1024 + 1)
    rejected = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        content=oversized,
        headers={
            **auth(ctx["token"]),
            "Content-Type": "application/json",
            "Content-Length": "1",
        },
    )
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "OFFLINE_BODY_TOO_LARGE"

    called = False

    async def must_not_read(_request):
        nonlocal called
        called = True
        raise AssertionError("unauthenticated body was read")

    monkeypatch.setattr(orders_router, "_read_offline_body_bounded", must_not_read)
    unauthenticated = client.post(
        f"/api/orders/{ctx['shop_id']}/offline", content=oversized
    )
    assert unauthenticated.status_code == 401
    assert called is False


def test_bounded_reader_caps_when_content_length_is_absent():
    import asyncio
    from starlette.requests import Request
    from fselling.routers.orders import _read_offline_body_bounded

    chunks = iter((b"x" * 40_000, b"y" * 25_537))

    async def receive():
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_offline_body_bounded(request))
    assert getattr(exc.value, "status_code", None) == 413
    assert exc.value.detail["code"] == "OFFLINE_BODY_TOO_LARGE"


def test_audit_and_commit_faults_rollback_entire_v1(client, monkeypatch):
    from fselling.schemas.order import OfflineOrderCreateV1

    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])

    def run_direct(uuid: str, sequence: int, fault_target: str):
        payload = _v1_payload(
            lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
            sequence * 1_000, True, sequence, uuid,
        )
        with SessionLocal() as session:
            user = session.query(models.User).filter_by(username=ctx["username"]).one()
            if fault_target == "commit":
                monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit fault")))
            with pytest.raises(RuntimeError):
                offline_service.dong_bo_phieu_v1(
                    session,
                    user,
                    ctx["shop_id"],
                    OfflineOrderCreateV1.model_validate(payload),
                    lease_token=lease["lease_token"],
                )
        assert _receipt_rows(uuid)[0] is None
        with SessionLocal() as check:
            assert check.get(models.Product, ctx["product"]["id"]).stock == ctx["product"]["stock"]

    with monkeypatch.context() as audit_patch:
        audit_patch.setattr(
            order_service,
            "_them_nhat_ky",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit fault")),
        )
        run_direct("uuid-v1-audit-fault", 1, "audit")
    run_direct("uuid-v1-commit-fault", 2, "commit")


def test_durable_corruption_fails_closed_and_verifier_passes_for_v0_plus_v1(client):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    payload = _v1_payload(
        lease, _items(ctx["product"]), lease["anchor_server_time_utc"],
        0, True, 1, "uuid-v1-verifier",
    )
    assert _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload).status_code == 200

    v0 = {
        "offline_uuid": "uuid-v0-verifier",
        "sold_at": BASE_NOW.isoformat(),
        "items": [{
            "product_id": ctx["product"]["id"],
            "product_name": ctx["product"]["name"],
            "unit_price": ctx["product"]["price"],
            "quantity": 1,
        }],
        "cash_tendered": ctx["product"]["price"],
    }
    assert client.post(
        f"/api/orders/{ctx['shop_id']}/offline", json=v0, headers=auth(ctx["token"])
    ).status_code == 200
    _TEST_MIGRATIONS.verify()

    with SessionLocal() as session:
        receipt = session.query(models.OfflineReceipt).filter_by(offline_uuid=payload["offline_uuid"]).one()
        receipt.sold_at_client_utc = "2025-07-15 09:30:00.123457"
        session.commit()
    retry = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "OFFLINE_RECEIPT_REGISTRY_INCONSISTENT"
    assert lease["lease_token"] not in retry.text
    assert payload["client_fingerprint"] not in retry.text


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_snapshot",
        "ordinal_gap",
        "crosslink",
        "name",
        "price",
        "quantity",
        "claimed_id",
        "null_valid_fk",
        "wrong_shop_fk",
    ],
)
def test_v1_snapshot_corruption_fails_service_and_startup_verifier(client, corruption):
    ctx = seller_with_shop(client)
    lease = _issue_lease(client, ctx["shop_id"], ctx["token"])
    payload = _v1_payload(
        lease,
        _items(ctx["product"]),
        lease["anchor_server_time_utc"],
        0,
        True,
        1,
        "uuid-v1-snapshot-corrupt-" + corruption,
    )
    assert _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload).status_code == 200

    other_product_id = None
    if corruption == "wrong_shop_fk":
        other_product_id = seller_with_shop(client)["product"]["id"]

    crosslink_item_id = None
    if corruption == "crosslink":
        v0 = {
            "offline_uuid": "uuid-v0-crosslink-target",
            "sold_at": BASE_NOW.isoformat(),
            "items": [{
                "product_id": ctx["product"]["id"],
                "product_name": ctx["product"]["name"],
                "unit_price": ctx["product"]["price"],
                "quantity": 1,
            }],
            "cash_tendered": ctx["product"]["price"],
        }
        response = client.post(
            f"/api/orders/{ctx['shop_id']}/offline",
            json=v0,
            headers=auth(ctx["token"]),
        )
        assert response.status_code == 200
        with SessionLocal() as session:
            crosslink_item_id = session.query(models.OrderItem.id).filter(
                models.OrderItem.order_id == response.json()["order_id"]
            ).scalar()

    with SessionLocal() as session:
        receipt = session.query(models.OfflineReceipt).filter_by(
            offline_uuid=payload["offline_uuid"]
        ).one()
        snapshot = session.query(models.OfflineReceiptItem).filter_by(
            receipt_id=receipt.id
        ).one()
        item = session.get(models.OrderItem, snapshot.order_item_id)
        receipt_id = receipt.id
        item_id = item.id
        original_product_id = item.product_id
        original_snapshot = {
            "order_item_id": snapshot.order_item_id,
            "item_ordinal": snapshot.item_ordinal,
            "claimed_product_id": snapshot.claimed_product_id,
            "product_name": snapshot.product_name,
            "unit_price_vnd": snapshot.unit_price_vnd,
            "quantity": snapshot.quantity,
        }
        if corruption == "missing_snapshot":
            session.delete(snapshot)
        elif corruption == "ordinal_gap":
            snapshot.item_ordinal = 2
        elif corruption == "crosslink":
            snapshot.order_item_id = crosslink_item_id
        elif corruption == "name":
            snapshot.product_name += " sai"
        elif corruption == "price":
            snapshot.unit_price_vnd += 1
        elif corruption == "quantity":
            snapshot.quantity += 1
        elif corruption == "claimed_id":
            snapshot.claimed_product_id += 1_000_000
        elif corruption == "null_valid_fk":
            item.product_id = None
        elif corruption == "wrong_shop_fk":
            item.product_id = other_product_id
            snapshot.claimed_product_id = other_product_id
        session.commit()

    retry = _sync_v1(client, ctx["shop_id"], ctx["token"], lease, payload)
    assert retry.status_code == 409, retry.text
    assert retry.json()["detail"]["code"] == "OFFLINE_RECEIPT_REGISTRY_INCONSISTENT"
    revision_0006 = next(
        spec.module
        for spec in _TEST_MIGRATIONS._graph().revisions
        if spec.revision == "0006_i09e_offline_receipt_items"
    )
    with SessionLocal() as session, pytest.raises(RuntimeError):
        revision_0006.verify(session.connection())
    with SessionLocal() as session:
        snapshot = session.query(models.OfflineReceiptItem).filter_by(
            receipt_id=receipt_id
        ).first()
        if snapshot is None:
            snapshot = models.OfflineReceiptItem(receipt_id=receipt_id)
            session.add(snapshot)
        for key, value in original_snapshot.items():
            setattr(snapshot, key, value)
        session.get(models.OrderItem, item_id).product_id = original_product_id
        session.commit()
