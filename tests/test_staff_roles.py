"""RBAC preset cho STAFF: CASHIER, WAREHOUSE và MANAGER."""
from __future__ import annotations

import uuid

import pytest

from conftest import (
    SHOP_PAYLOAD,
    STAFF_PASSWORD,
    auth,
    login,
    new_seller,
    new_staff,
    seller_with_shop,
)

from fselling import dependencies, models
import legacy_bootstrap_support as bootstrap
from fselling.core.database import SessionLocal


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _staff_record(client, owner_ctx: dict, staff_role: str):
    username, token = new_staff(client, owner_ctx, staff_role)
    staff = client.get(
        f"/api/staff/{owner_ctx['shop_id']}",
        headers=auth(owner_ctx["token"]),
    ).json()
    return username, token, next(item for item in staff if item["username"] == username)


def _voucher_payload(code: str) -> dict:
    return {
        "code": code,
        "discount_type": "flat",
        "discount_value": 5000,
    }


def test_staff_mac_dinh_manager_giu_quyen_cu(client):
    ctx = seller_with_shop(client)
    username, token = new_staff(client, ctx)

    listed = client.get(
        f"/api/staff/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()
    staff = next(item for item in listed if item["username"] == username)
    assert staff["staff_role"] == "MANAGER"
    assert client.get(
        f"/api/dashboard/seller/{ctx['shop_id']}", headers=auth(token)
    ).status_code == 200


def test_service_chi_co_quyen_catalog_va_phuc_vu(client):
    ctx = seller_with_shop(client)
    username, _ = new_staff(client, ctx, "SERVICE")

    session = SessionLocal()
    try:
        user = session.query(models.User).filter_by(username=username).one()
        assert dependencies.effective_staff_role(user) == "SERVICE"
        assert dependencies.has_staff_permission(
            user, dependencies.PERMISSION_CATALOG_READ
        )
        assert dependencies.has_staff_permission(
            user, dependencies.PERMISSION_FNB_SERVICE
        )
        for denied in (
            dependencies.PERMISSION_SALE,
            dependencies.PERMISSION_ORDER_RETURN,
            dependencies.PERMISSION_RETURN_APPROVE,
            dependencies.PERMISSION_FNB_CHECKOUT,
            dependencies.PERMISSION_FNB_MANAGE,
            dependencies.PERMISSION_INVENTORY,
            dependencies.PERMISSION_RECONCILIATION,
        ):
            assert not dependencies.has_staff_permission(user, denied)
    finally:
        session.close()


def test_permission_cashier_manager_legacy_va_unknown_fail_closed(client):
    ctx = seller_with_shop(client)
    cashier_username, _ = new_staff(client, ctx, "CASHIER")
    manager_username, _ = new_staff(client, ctx, "MANAGER")

    session = SessionLocal()
    try:
        cashier = session.query(models.User).filter_by(username=cashier_username).one()
        manager = session.query(models.User).filter_by(username=manager_username).one()
        assert dependencies.has_staff_permission(
            cashier, dependencies.PERMISSION_ORDER_RETURN
        )
        assert not dependencies.has_staff_permission(
            cashier, dependencies.PERMISSION_RETURN_APPROVE
        )
        assert dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_ORDER_RETURN
        )
        assert dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_RETURN_APPROVE
        )

        manager.staff_role = None
        assert dependencies.effective_staff_role(manager) == "MANAGER"
        assert dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_ORDER_RETURN
        )
        assert dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_RETURN_APPROVE
        )

        manager.staff_role = "UNKNOWN"
        assert not dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_ORDER_RETURN
        )
        assert not dependencies.has_staff_permission(
            manager, dependencies.PERMISSION_RETURN_APPROVE
        )
    finally:
        session.rollback()
        session.close()


def test_staff_legacy_null_duoc_hieu_la_manager_va_migration_backfill(client):
    ctx = seller_with_shop(client)
    username, token = new_staff(client, ctx)

    session = SessionLocal()
    try:
        staff = session.query(models.User).filter(models.User.username == username).first()
        staff.staff_role = None
        session.commit()
    finally:
        session.close()

    # Tương thích ngay cả trước khi migration backfill chạy.
    res = client.post(
        "/api/categories",
        params={"name": _unique("legacy"), "shop_id": ctx["shop_id"]},
        headers=auth(token),
    )
    assert res.status_code == 200

    session = SessionLocal()
    try:
        bootstrap.run_migrations(session)
        staff = session.query(models.User).filter(models.User.username == username).first()
        assert staff.staff_role == "MANAGER"
    finally:
        session.close()


def test_login_staff_tra_staff_role_con_seller_khong_them_khoa(client):
    ctx = seller_with_shop(client)
    username, _ = new_staff(client, ctx, "CASHIER")

    staff_login = client.post(
        "/api/auth/login",
        json={"username": username, "password": STAFF_PASSWORD},
    )
    assert staff_login.status_code == 200
    assert set(staff_login.json()) == {
        "access_token", "token_type", "role", "staff_role"
    }
    assert staff_login.json()["staff_role"] == "CASHIER"

    seller_login = client.post(
        "/api/auth/login",
        json={"username": ctx["username"], "password": "Seller@2026"},
    )
    assert set(seller_login.json()) == {"access_token", "token_type", "role"}


def test_chu_shop_doi_service_sang_cashier_va_vo_hieu_phien_cu(client):
    ctx = seller_with_shop(client)
    username, old_token, staff = _staff_record(client, ctx, "SERVICE")

    res = client.put(
        f"/api/staff/member/{staff['id']}/role",
        json={"staff_role": "CASHIER"},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200
    assert res.json()["staff_role"] == "CASHIER"
    assert client.get("/api/shops", headers=auth(old_token)).status_code == 401

    new_token = login(client, username, STAFF_PASSWORD)
    assert client.get("/api/shops", headers=auth(new_token)).status_code == 200


def test_khong_the_gan_role_la_hoac_doi_staff_shop_khac(client):
    ctx = seller_with_shop(client)
    _, _, staff = _staff_record(client, ctx, "CASHIER")

    invalid = client.put(
        f"/api/staff/member/{staff['id']}/role",
        json={"staff_role": "OWNER"},
        headers=auth(ctx["token"]),
    )
    assert invalid.status_code == 422

    _, token_b = new_seller(client)
    forbidden = client.put(
        f"/api/staff/member/{staff['id']}/role",
        json={"staff_role": "WAREHOUSE"},
        headers=auth(token_b),
    )
    assert forbidden.status_code == 404


def test_cashier_duoc_pos_crm_nhung_khong_duoc_kho_voucher_bao_cao(client):
    ctx = seller_with_shop(client)
    _, cashier = new_staff(client, ctx, "CASHIER")
    headers = auth(cashier)

    # Hai API đọc catalog mà POS cần: danh mục và tra barcode.
    assert client.get(
        f"/api/categories/{ctx['shop_id']}", headers=headers
    ).status_code == 200
    assert client.get(
        f"/api/products/{ctx['shop_id']}/barcode/NOPE1234", headers=headers
    ).status_code == 404

    customer = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={"name": "Khách thu ngân", "phone": _unique("09")},
        headers=headers,
    )
    assert customer.status_code == 200

    stock_before = ctx["product"]["stock"]
    denied_calls = [
        client.post("/api/shops", json=SHOP_PAYLOAD, headers=headers),
        client.post(
            "/api/categories",
            params={"name": _unique("cam"), "shop_id": ctx["shop_id"]},
            headers=headers,
        ),
        client.put(
            f"/api/categories/{ctx['category_id']}",
            json={"name": "Không được sửa", "is_active": True},
            headers=headers,
        ),
        client.post(
            f"/api/products/{ctx['product']['id']}/stock",
            json={"delta": 5, "reason": "Kiểm thử nhập kho"},
            headers=headers,
        ),
        client.post(
            f"/api/products/{ctx['shop_id']}/stocktake",
            json={
                "items": [{
                    "product_id": ctx["product"]["id"],
                    "counted": 1,
                    "stock_snapshot": stock_before,
                }]
            },
            headers=headers,
        ),
        client.post(
            "/api/vouchers",
            params={"shop_id": ctx["shop_id"]},
            json=_voucher_payload(_unique("VC")),
            headers=headers,
        ),
        client.get(
            f"/api/dashboard/seller/{ctx['shop_id']}", headers=headers
        ),
        client.get(f"/api/shops/{ctx['shop_id']}/stats", headers=headers),
        client.get(f"/api/export/seller/{ctx['shop_id']}", headers=headers),
    ]
    assert {response.status_code for response in denied_calls} == {403}
    product = client.get(
        f"/api/products/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()[0]
    assert product["stock"] == stock_before


def test_warehouse_duoc_kho_nhung_khong_duoc_crm_voucher_bao_cao(client):
    ctx = seller_with_shop(client)
    _, warehouse = new_staff(client, ctx, "WAREHOUSE")
    headers = auth(warehouse)

    assert client.get(
        f"/api/categories/{ctx['shop_id']}", headers=headers
    ).status_code == 200
    assert client.get(
        f"/api/products/{ctx['shop_id']}/barcode/NOPE1234", headers=headers
    ).status_code == 404
    assert client.post(
        "/api/categories",
        params={"name": _unique("kho"), "shop_id": ctx["shop_id"]},
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/products/{ctx['product']['id']}/stock",
        json={"delta": 3, "reason": "Kiểm thử nhập kho"},
        headers=headers,
    ).status_code == 200

    denied_calls = [
        client.get(f"/api/customers/{ctx['shop_id']}", headers=headers),
        client.post(
            "/api/vouchers",
            params={"shop_id": ctx["shop_id"]},
            json=_voucher_payload(_unique("VC")),
            headers=headers,
        ),
        client.get(
            f"/api/dashboard/seller/{ctx['shop_id']}", headers=headers
        ),
        client.get(f"/api/shops/{ctx['shop_id']}/stats", headers=headers),
        client.get(f"/api/export/seller/{ctx['shop_id']}", headers=headers),
    ]
    assert {response.status_code for response in denied_calls} == {403}


def test_cashier_khong_sua_hoac_xoa_voucher_da_co(client):
    ctx = seller_with_shop(client)
    payload = _voucher_payload(_unique("OWNERVC"))
    voucher = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json=payload,
        headers=auth(ctx["token"]),
    ).json()
    _, cashier = new_staff(client, ctx, "CASHIER")

    assert client.put(
        f"/api/vouchers/{voucher['id']}",
        json=payload,
        headers=auth(cashier),
    ).status_code == 403
    assert client.delete(
        f"/api/vouchers/{voucher['id']}",
        headers=auth(cashier),
    ).status_code == 403
