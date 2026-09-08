"""I09-D: credential, catalog snapshot, membership và attribution seam."""

from __future__ import annotations

import datetime
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from conftest import (
    STAFF_PASSWORD,
    _TEST_MIGRATIONS,
    _unique,
    auth,
    create_shop,
    login,
    new_seller,
    new_staff,
    seller_with_shop,
)
from fselling import models
from fselling.core.database import SessionLocal
from fselling.services import offline_lease_service, subscription_service


@pytest.fixture
def lease_enabled(monkeypatch):
    monkeypatch.setattr(
        offline_lease_service.config,
        "OFFLINE_LEASE_ISSUANCE_ENABLED",
        True,
    )


def _issue(client, ctx, *, token=None, device_id="device-main"):
    response = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": device_id},
        headers=auth(token or ctx["token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _heartbeat(client, token, lease, raw_token=None):
    return client.post(
        f"/api/offline/leases/{lease['lease_id']}/heartbeat",
        headers={
            **auth(token),
            "X-Offline-Lease-Token": raw_token or lease["lease_token"],
        },
    )


def _expire_trial_paid_and_gift(shop_id: int) -> None:
    now = datetime.datetime.utcnow()
    with SessionLocal() as session:
        subscription = (
            session.query(models.ShopSubscription)
            .filter(models.ShopSubscription.shop_id == shop_id)
            .one()
        )
        subscription.trial_started_at = now - datetime.timedelta(days=100)
        subscription.trial_ends_at = now - datetime.timedelta(days=70)
        subscription.paid_until = now - datetime.timedelta(days=8)
        subscription.updated_at = now
        session.commit()
        assert not subscription_service.get_subscription_state(
            session, shop_id, now=now
        )["can_use_pro"]


def _lease_mutation_snapshot(lease_id: str) -> tuple[str, int, int]:
    with SessionLocal() as session:
        lease = session.get(models.OfflineLease, lease_id)
        reclaim_audits = (
            session.query(models.SystemLog)
            .filter(
                models.SystemLog.action == offline_lease_service.AUDIT_RECLAIM,
                models.SystemLog.details.contains(lease_id),
            )
            .count()
        )
        return lease.secret_sha256, int(lease.state_version), reclaim_audits


def test_identifier_and_catalog_known_vector_are_canonical(monkeypatch):
    monkeypatch.setattr(
        offline_lease_service.secrets,
        "token_bytes",
        lambda size: bytes(range(size)),
    )
    lease_id = offline_lease_service.generate_lease_id()
    assert lease_id == "lse_000G40R40M30E209185GR3"
    assert offline_lease_service.is_valid_lease_id(lease_id)
    assert not offline_lease_service.is_valid_lease_id(lease_id.lower())
    assert len(lease_id) == 26

    products = [
        SimpleNamespace(
            id=7,
            name="  Sữa   tươi ",
            price=31_000,
            is_active=False,
            track_batches=True,
            stock=999,
            cost_price=123,
        ),
        SimpleNamespace(
            id=2,
            name="Cà phê sữa",
            price=25_000,
            is_active=True,
            track_batches=False,
            stock=1,
            cost_price=9_999,
        ),
    ]
    snapshot = offline_lease_service.catalog_snapshot_v1(products)
    assert snapshot.digest == (
        "a2998a44b804de0f892d608206c4f6c28ef539f27cb049f1381594736d4acd26"
    )
    document = json.loads(snapshot.canonical_json)
    assert [row["id"] for row in document] == [2, 7]
    assert set(document[0]) == {
        "id", "name", "price_vnd", "is_active", "track_batches"
    }
    assert "stock" not in snapshot.canonical_json
    assert "cost" not in snapshot.canonical_json
    assert offline_lease_service.catalog_snapshot_v1(reversed(products)) == snapshot

    invalid_float = [SimpleNamespace(**vars(product)) for product in products]
    invalid_float[1].price = 25_000.0
    with pytest.raises(ValueError):
        offline_lease_service.catalog_snapshot_v1(invalid_float)

    for field, value in (
        ("name", "Tên mới"),
        ("price", 25_001),
        ("is_active", False),
        ("track_batches", True),
    ):
        changed = [SimpleNamespace(**vars(product)) for product in products]
        setattr(changed[1], field, value)
        assert offline_lease_service.catalog_snapshot_v1(changed).digest != snapshot.digest


def test_issue_owner_persists_only_digest_and_heartbeat_has_no_secret(
    client, lease_enabled, monkeypatch
):
    ctx = seller_with_shop(client)
    fixed = (
        datetime.datetime.utcnow() + datetime.timedelta(seconds=1)
    ).replace(microsecond=654321)
    monkeypatch.setattr(offline_lease_service, "_utcnow", lambda: fixed)
    lease = _issue(client, ctx, device_id="  máy   quầy 1 ")

    assert lease["status"] == "ACTIVE"
    assert lease["device_id"] == "máy quầy 1"
    assert lease["anchor_server_time_utc"] == lease["server_time_utc"]
    assert lease["issued_at"] == lease["server_time_utc"]
    assert datetime.datetime.strptime(
        lease["expires_at"], "%Y-%m-%d %H:%M:%S.%f"
    ) - fixed == datetime.timedelta(hours=12)
    assert len(lease["lease_token"]) == 43
    assert "secret_sha256" not in lease

    with SessionLocal() as session:
        stored = session.get(models.OfflineLease, lease["lease_id"])
        assert stored.secret_sha256 == hashlib.sha256(
            lease["lease_token"].encode("ascii")
        ).hexdigest()
        assert stored.secret_sha256 != lease["lease_token"]
        audit = (
            session.query(models.SystemLog)
            .filter(models.SystemLog.action == offline_lease_service.AUDIT_ISSUE)
            .order_by(models.SystemLog.id.desc())
            .first()
        )
        assert lease["lease_id"] in audit.details
        assert lease["lease_token"] not in audit.details
        assert stored.secret_sha256 not in audit.details
        assert lease["device_id"] not in audit.details

    heartbeat = _heartbeat(client, ctx["token"], lease)
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "ACTIVE"
    assert "lease_token" not in heartbeat.json()
    assert "secret_sha256" not in heartbeat.json()
    assert _TEST_MIGRATIONS.verify().current_revision == (
        "0013_roles_returns_approval_r3"
    )


def test_issue_flag_permission_membership_pro_and_v0_regression(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    disabled = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "device-disabled"},
        headers=auth(ctx["token"]),
    )
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == (
        offline_lease_service.ERROR_ISSUANCE_DISABLED
    )

    monkeypatch.setattr(
        offline_lease_service.config,
        "OFFLINE_LEASE_ISSUANCE_ENABLED",
        True,
    )
    _, cashier = new_staff(client, ctx, "CASHIER")
    assert _issue(client, ctx, token=cashier)["status"] == "ACTIVE"

    _, warehouse = new_staff(client, ctx, "WAREHOUSE")
    denied = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "warehouse-device"},
        headers=auth(warehouse),
    )
    assert denied.status_code == 403

    _, other_token = new_seller(client)
    other_shop = create_shop(client, other_token, _unique("shop-khac"))
    cross = client.post(
        "/api/offline/leases",
        json={"shop_id": other_shop, "device_id": "cross-device"},
        headers=auth(cashier),
    )
    assert cross.status_code == 403

    _expire_trial_paid_and_gift(ctx["shop_id"])
    non_pro = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "free-device"},
        headers=auth(cashier),
    )
    assert non_pro.status_code == 402

    # V0 là chứng từ tiền đã phát sinh: cờ/pro gate mới không được chặn hồi tố.
    sold_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    v0 = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        json={
            "offline_uuid": _unique("v0-regression"),
            "sold_at": sold_at.isoformat(),
            "items": [{
                "product_id": ctx["product"]["id"],
                "product_name": ctx["product"]["name"],
                "unit_price": 100_000,
                "quantity": 1,
            }],
            "cash_tendered": 100_000,
            "device_label": "legacy-v0",
        },
        headers=auth(cashier),
    )
    assert v0.status_code == 200, v0.text
    with SessionLocal() as session:
        receipt = (
            session.query(models.OfflineReceipt)
            .filter(models.OfflineReceipt.order_id == v0.json()["order_id"])
            .one()
        )
        assert receipt.attribution_kind == "LEGACY_UNKNOWN"
        assert receipt.lease_id is None


def test_wrong_malformed_unknown_other_user_and_compare_is_digest_only(
    client, lease_enabled, monkeypatch
):
    ctx = seller_with_shop(client)
    lease = _issue(client, ctx)
    _, other_token = new_seller(client)
    other_ctx = seller_with_shop(client)
    other_lease = _issue(client, other_ctx, device_id="other-shop-device")

    for lease_id, raw_token, jwt_token in (
        (lease["lease_id"], "bad", ctx["token"]),
        ("lse_" + "0" * 22, lease["lease_token"], ctx["token"]),
        (lease["lease_id"], lease["lease_token"], other_token),
        (other_lease["lease_id"], other_lease["lease_token"], ctx["token"]),
    ):
        response = client.post(
            f"/api/offline/leases/{lease_id}/heartbeat",
            headers={
                **auth(jwt_token),
                "X-Offline-Lease-Token": raw_token,
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == offline_lease_service.ERROR_NOT_YOURS
        assert lease["lease_token"] not in response.text

    seen = []
    real_compare = offline_lease_service.compare_secret

    def record_compare(candidate, expected):
        seen.append((candidate, expected))
        return real_compare(candidate, expected)

    monkeypatch.setattr(offline_lease_service, "compare_secret", record_compare)
    ok = _heartbeat(client, ctx["token"], lease)
    assert ok.status_code == 200
    assert len(seen[-1][0]) == len(seen[-1][1]) == 64
    assert seen[-1][0] != lease["lease_token"]


def test_attribution_context_locks_all_financial_actors_and_membership(
    client, lease_enabled
):
    ctx = seller_with_shop(client)
    staff_name, staff_token = new_staff(client, ctx, "CASHIER")
    lease = _issue(client, ctx, token=staff_token, device_id="device-attribution")

    with SessionLocal() as session:
        staff = session.query(models.User).filter(models.User.username == staff_name).one()
        context = offline_lease_service.authorize_normal_v1_capability(
            session,
            staff,
            shop_id=ctx["shop_id"],
            lease_id=lease["lease_id"],
            lease_token=lease["lease_token"],
            device_id="device-attribution",
            offline_session_id=lease["lease_id"],
        )
        assert context.sold_by_claimed_user_id == staff.id
        assert context.synced_by_user_id == staff.id
        assert context.attribution_kind == "LEASE_CLAIM"
        assert {
            context.created_by_user_id,
            context.payment_actor_user_id,
            context.shift_owner_user_id,
        } == {staff.id}
        assert context.offline_session_id == context.lease_id

        with pytest.raises(HTTPException) as exc:
            offline_lease_service.authorize_normal_v1_capability(
                session,
                staff,
                shop_id=ctx["shop_id"],
                lease_id=lease["lease_id"],
                lease_token="wrong-token",
                device_id="device-attribution",
                offline_session_id=lease["lease_id"],
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == offline_lease_service.ERROR_NOT_YOURS

        bad_binding = dict(
            shop_id=ctx["shop_id"],
            lease_id=lease["lease_id"],
            lease_token=lease["lease_token"],
            device_id="device-khac",
            offline_session_id=lease["lease_id"],
        )
        with pytest.raises(HTTPException) as exc:
            offline_lease_service.authorize_normal_v1_capability(
                session, staff, **bad_binding
            )
        assert exc.value.detail["code"] == offline_lease_service.ERROR_BINDING_MISMATCH

    other_ctx = seller_with_shop(client)
    with SessionLocal() as session:
        staff = session.query(models.User).filter(models.User.username == staff_name).one()
        staff.staff_shop_id = other_ctx["shop_id"]
        session.commit()
        with pytest.raises(HTTPException) as exc:
            offline_lease_service.authorize_normal_v1_capability(
                session,
                staff,
                shop_id=ctx["shop_id"],
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                device_id="device-attribution",
                offline_session_id=lease["lease_id"],
            )
        assert exc.value.detail["code"] == offline_lease_service.ERROR_NOT_YOURS


def test_role_downgrade_denies_reclaim_and_requires_normal_recovery(
    client, lease_enabled
):
    ctx = seller_with_shop(client)
    staff_name, staff_token = new_staff(client, ctx, "CASHIER")
    lease = _issue(client, ctx, token=staff_token, device_id="device-role-change")
    before = _lease_mutation_snapshot(lease["lease_id"])

    with SessionLocal() as session:
        staff_id = (
            session.query(models.User.id)
            .filter(models.User.username == staff_name)
            .scalar()
        )
    changed = client.put(
        f"/api/staff/member/{staff_id}/role",
        json={"staff_role": "WAREHOUSE"},
        headers=auth(ctx["token"]),
    )
    assert changed.status_code == 200, changed.text
    fresh_token = login(client, staff_name, STAFF_PASSWORD)
    heartbeat = _heartbeat(client, fresh_token, lease)
    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "ACTIVE"

    denied = client.post(
        f"/api/offline/leases/{lease['lease_id']}/reclaim",
        json={"expected_state_version": lease["state_version"]},
        headers=auth(fresh_token),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == (
        offline_lease_service.ERROR_RECLAIM_DENIED
    )
    assert _lease_mutation_snapshot(lease["lease_id"]) == before

    with SessionLocal() as session:
        staff = (
            session.query(models.User)
            .filter(models.User.username == staff_name)
            .one()
        )
        with pytest.raises(HTTPException) as exc:
            offline_lease_service.authorize_normal_v1_capability(
                session,
                staff,
                shop_id=ctx["shop_id"],
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                device_id="device-role-change",
                offline_session_id=lease["lease_id"],
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == (
            offline_lease_service.ERROR_RECOVERY_REQUIRED
        )


def test_pro_expiry_denies_lease_claim_but_v0_sync_still_succeeds(
    client, lease_enabled
):
    ctx = seller_with_shop(client)
    lease = _issue(client, ctx, device_id="device-pro-expiry")
    before = _lease_mutation_snapshot(lease["lease_id"])
    _expire_trial_paid_and_gift(ctx["shop_id"])
    heartbeat = _heartbeat(client, ctx["token"], lease)
    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "ACTIVE"

    denied = client.post(
        f"/api/offline/leases/{lease['lease_id']}/reclaim",
        json={"expected_state_version": lease["state_version"]},
        headers=auth(ctx["token"]),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == (
        offline_lease_service.ERROR_RECLAIM_DENIED
    )
    assert _lease_mutation_snapshot(lease["lease_id"]) == before

    with SessionLocal() as session:
        owner = (
            session.query(models.User)
            .filter(models.User.username == ctx["username"])
            .one()
        )
        with pytest.raises(HTTPException) as exc:
            offline_lease_service.authorize_normal_v1_capability(
                session,
                owner,
                shop_id=ctx["shop_id"],
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                device_id="device-pro-expiry",
                offline_session_id=lease["lease_id"],
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == (
            offline_lease_service.ERROR_RECOVERY_REQUIRED
        )

    v0 = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        json={
            "offline_uuid": _unique("v0-after-pro-expiry"),
            "sold_at": (
                datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
            ).isoformat(),
            "items": [{
                "product_id": ctx["product"]["id"],
                "product_name": ctx["product"]["name"],
                "unit_price": 100_000,
                "quantity": 1,
            }],
            "cash_tendered": 100_000,
            "device_label": "legacy-v0-after-pro-expiry",
        },
        headers=auth(ctx["token"]),
    )
    assert v0.status_code == 200, v0.text
    with SessionLocal() as session:
        receipt = (
            session.query(models.OfflineReceipt)
            .filter(models.OfflineReceipt.order_id == v0.json()["order_id"])
            .one()
        )
        assert receipt.attribution_kind == "LEGACY_UNKNOWN"
        assert receipt.lease_id is None


def test_reclaim_rechecks_current_policy_under_shop_write_lock(
    client, lease_enabled, monkeypatch
):
    ctx = seller_with_shop(client)
    lease = _issue(client, ctx, device_id="device-policy-race")
    before = _lease_mutation_snapshot(lease["lease_id"])
    real_lock = offline_lease_service.order_service._lock_shop_for_order

    def expire_pro_before_lock(db, shop_id):
        _expire_trial_paid_and_gift(shop_id)
        real_lock(db, shop_id)

    monkeypatch.setattr(
        offline_lease_service.order_service,
        "_lock_shop_for_order",
        expire_pro_before_lock,
    )
    denied = client.post(
        f"/api/offline/leases/{lease['lease_id']}/reclaim",
        json={"expected_state_version": lease["state_version"]},
        headers=auth(ctx["token"]),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == (
        offline_lease_service.ERROR_RECLAIM_DENIED
    )
    assert _lease_mutation_snapshot(lease["lease_id"]) == before
