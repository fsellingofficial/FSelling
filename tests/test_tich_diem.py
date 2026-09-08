"""H1: tích điểm khách thân thiết.

Bộ test này cố ý đi qua HTTP cho phần lớn tình huống. Điểm đổi được thành tiền,
nên các ca retry, trả hàng, đổi cấu hình và hai loại thanh toán phải được kiểm
như sổ tiền: hoặc toàn bộ tác dụng phụ cùng thành công, hoặc không ghi gì.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from conftest import (
    _unique,
    auth,
    create_product,
    new_seller,
    new_staff,
    seller_with_shop,
)

from fselling import models
import legacy_bootstrap_support as bootstrap
from fselling.core.database import SessionLocal
from fselling.routers import webhooks
from fselling.services import loyalty_service, order_service


PROGRAM = {
    "enabled": True,
    "earn_amount": 10_000,
    "earn_points": 1,
    "redeem_points": 1,
    "redeem_amount": 1_000,
    "min_redeem_points": 1,
    "max_redeem_percent": 100,
    "expiry_days": None,
}


def _program(**overrides):
    payload = dict(PROGRAM)
    payload.update(overrides)
    return payload


def _save_program(client, ctx, token=None, **overrides):
    res = client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json=_program(**overrides),
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_customer(client, ctx, token=None, name=None):
    res = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={
            "name": name or _unique("Khach diem"),
            "phone": _unique("09")[:15],
        },
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _balance(client, ctx, customer_id, token=None):
    res = client.get(
        f"/api/customers/member/{customer_id}",
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    assert "points_balance" in res.json()
    return res.json()["points_balance"]


def _order_payload(
    ctx,
    *,
    customer_id=None,
    points=0,
    qty=1,
    method="cash",
    product=None,
    voucher=None,
    operation_id=None,
):
    product = product or ctx["product"]
    body = {
        "items": [
            {
                "product_id": product["id"],
                "product_name": product["name"],
                "price": 1,  # server phải bỏ giá client gửi
                "quantity": qty,
            }
        ],
        "payment_method": method,
        "loyalty_points_to_use": points,
    }
    if customer_id is not None:
        body["customer_id"] = customer_id
    if voucher is not None:
        body["voucher_code"] = voucher
    if operation_id is not None:
        body["operation_id"] = operation_id
    return body


def _create_order(client, ctx, *, token=None, **kwargs):
    return client.post(
        f"/api/orders/{ctx['shop_id']}",
        json=_order_payload(ctx, **kwargs),
        headers=auth(token or ctx["token"]),
    )


def _pay_cash(client, ctx, order_id, token=None):
    res = client.post(
        f"/api/orders/{order_id}/pay",
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res


def _earn_with_cash(client, ctx, customer_id, *, qty=1, product=None):
    created = _create_order(
        client,
        ctx,
        customer_id=customer_id,
        qty=qty,
        product=product,
        method="cash",
        operation_id=uuid.uuid4().hex,
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]
    _pay_cash(client, ctx, order_id)
    return order_id


def _product_stock(product_id):
    session = SessionLocal()
    try:
        return (
            session.query(models.Product)
            .filter(models.Product.id == product_id)
            .one()
            .stock
        )
    finally:
        session.close()


def _order_record(order_id):
    session = SessionLocal()
    try:
        order = session.query(models.Order).filter(models.Order.id == order_id).one()
        return {
            "status": order.status,
            "customer_id": order.customer_id,
            "points_redeemed": order.loyalty_points_redeemed,
            "loyalty_discount": order.loyalty_discount_amount,
            "points_earned": order.loyalty_points_earned,
            "earn_amount_step": order.loyalty_earn_amount_step,
            "earn_points_step": order.loyalty_earn_points_step,
        }
    finally:
        session.close()


def _entries(*, customer_id=None, order_id=None, return_id=None):
    session = SessionLocal()
    try:
        query = session.query(models.LoyaltyPointEntry)
        if customer_id is not None:
            query = query.filter(models.LoyaltyPointEntry.customer_id == customer_id)
        if order_id is not None:
            query = query.filter(models.LoyaltyPointEntry.order_id == order_id)
        if return_id is not None:
            query = query.filter(models.LoyaltyPointEntry.return_id == return_id)
        return [
            {
                "id": row.id,
                "entry_type": row.entry_type,
                "points_delta": row.points_delta,
                "expires_at": row.expires_at,
                "idempotency_key": row.idempotency_key,
            }
            for row in query.order_by(models.LoyaltyPointEntry.id).all()
        ]
    finally:
        session.close()


def _voucher(client, ctx, amount=10_000):
    code = _unique("DIEM").upper()
    res = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={
            "code": code,
            "discount_type": "flat",
            "discount_value": amount,
            "min_order_value": 0,
        },
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return code


def _order_item_id(client, ctx, order_id, token=None):
    res = client.get(
        f"/api/orders/{order_id}/detail",
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()["items"][0]["id"]


def _return_order(client, ctx, order_id, quantity, *, operation_id=None):
    item_id = _order_item_id(client, ctx, order_id)
    body = {
        "items": [
            {"order_item_id": item_id, "quantity": quantity, "restock": True}
        ],
        "method": "transfer",
        "reason": "Khách trả hàng",
        "reference": "TEST-RETURN",
        "operation_id": operation_id or uuid.uuid4().hex,
    }
    return client.post(
        f"/api/orders/{order_id}/returns",
        json=body,
        headers=auth(ctx["token"]),
    )


# ---------- Cấu hình và quyền ----------
def test_shop_moi_mac_dinh_tat_va_khong_bia_ty_le_tien(client):
    ctx = seller_with_shop(client)

    res = client.get(
        f"/api/loyalty/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is False
    assert body["earn_amount"] is None
    assert body["earn_points"] is None
    assert body["redeem_points"] is None
    assert body["redeem_amount"] is None
    assert body["expiry_days"] is None


def test_chu_shop_luu_va_doc_lai_day_du_cau_hinh(client):
    ctx = seller_with_shop(client)
    wanted = _program(
        earn_amount=25_000,
        earn_points=3,
        redeem_points=7,
        redeem_amount=5_000,
        min_redeem_points=14,
        max_redeem_percent=40,
        expiry_days=90,
    )

    saved = client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json=wanted,
        headers=auth(ctx["token"]),
    )
    assert saved.status_code == 200, saved.text

    loaded = client.get(
        f"/api/loyalty/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert loaded.status_code == 200, loaded.text
    for key, value in wanted.items():
        assert loaded.json()[key] == value, key

    session = SessionLocal()
    try:
        audit = (
            session.query(models.SystemLog)
            .filter(models.SystemLog.action == "UPDATE_LOYALTY_PROGRAM")
            .order_by(models.SystemLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.user_id is not None
        assert f"Shop #{ctx['shop_id']}" in audit.details
        assert '"earn_amount": 25000' in audit.details
        assert '"expiry_days": 90' in audit.details
    finally:
        session.close()


def test_cau_hinh_tu_choi_so_khong_hop_le(client):
    ctx = seller_with_shop(client)
    invalid_values = [
        ("earn_amount", 0),
        ("earn_points", 0),
        ("redeem_points", 0),
        ("redeem_amount", 0),
        ("min_redeem_points", -1),
        ("max_redeem_percent", 0),
        ("max_redeem_percent", 101),
        ("expiry_days", 0),
    ]
    for field, value in invalid_values:
        res = client.put(
            f"/api/loyalty/{ctx['shop_id']}",
            json=_program(**{field: value}),
            headers=auth(ctx["token"]),
        )
        assert res.status_code in (400, 422), (field, value, res.text)


def test_cau_hinh_tu_choi_ten_field_go_sai(client):
    ctx = seller_with_shop(client)
    res = client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json={**_program(), "earn_amunt": 50_000},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 422, res.text

    loaded = client.get(
        f"/api/loyalty/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert loaded.status_code == 200
    assert loaded.json()["earn_amount"] is None


def test_cau_hinh_tu_choi_true_false_o_moi_o_so(client):
    ctx = seller_with_shop(client)
    numeric_fields = [
        "earn_amount",
        "earn_points",
        "redeem_points",
        "redeem_amount",
        "min_redeem_points",
        "max_redeem_percent",
        "expiry_days",
    ]

    for field in numeric_fields:
        for value in (True, False):
            res = client.put(
                f"/api/loyalty/{ctx['shop_id']}",
                json={**_program(), field: value},
                headers=auth(ctx["token"]),
            )
            assert res.status_code == 422, (field, value, res.text)


def test_cau_hinh_hai_shop_cach_ly(client):
    a = seller_with_shop(client)
    b = seller_with_shop(client)
    _save_program(client, a, earn_amount=10_000, earn_points=1)
    _save_program(client, b, earn_amount=50_000, earn_points=9)

    got_a = client.get(
        f"/api/loyalty/{a['shop_id']}", headers=auth(a["token"])
    ).json()
    got_b = client.get(
        f"/api/loyalty/{b['shop_id']}", headers=auth(b["token"])
    ).json()
    assert (got_a["earn_amount"], got_a["earn_points"]) == (10_000, 1)
    assert (got_b["earn_amount"], got_b["earn_points"]) == (50_000, 9)


def test_nhan_vien_chi_doc_theo_quyen_pos_va_khong_duoc_sua(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    for role, get_status in (
        ("CASHIER", 200),
        ("MANAGER", 200),
        ("WAREHOUSE", 403),
    ):
        _, staff_token = new_staff(client, ctx, role)
        got = client.get(
            f"/api/loyalty/{ctx['shop_id']}", headers=auth(staff_token)
        )
        assert got.status_code == get_status, (role, got.text)

        changed = client.put(
            f"/api/loyalty/{ctx['shop_id']}",
            json=_program(earn_amount=1),
            headers=auth(staff_token),
        )
        # Endpoint chủ-shop-only đang dùng require_own_shop nên cố ý che bằng
        # 404; 403 cũng là cách từ chối hợp lệ nếu router đổi dependency.
        assert changed.status_code in (403, 404), (role, changed.text)


def test_nguoi_ngoai_va_chua_dang_nhap_khong_doc_sua_cau_hinh(client):
    ctx = seller_with_shop(client)
    _, outsider = new_seller(client)
    url = f"/api/loyalty/{ctx['shop_id']}"

    assert client.get(url).status_code == 401
    assert client.put(url, json=_program()).status_code == 401
    assert client.get(url, headers=auth(outsider)).status_code in (403, 404)
    assert client.put(url, json=_program(), headers=auth(outsider)).status_code in (403, 404)


def test_customer_api_deu_tra_so_du_diem_ban_dau_bang_0(client):
    ctx = seller_with_shop(client)
    customer = _create_customer(client, ctx)
    assert customer["points_balance"] == 0

    detail = client.get(
        f"/api/customers/member/{customer['id']}", headers=auth(ctx["token"])
    ).json()
    listed = client.get(
        f"/api/customers/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()
    history = client.get(
        f"/api/customers/member/{customer['id']}/history",
        headers=auth(ctx["token"]),
    ).json()

    assert detail["points_balance"] == 0
    assert next(x for x in listed if x["id"] == customer["id"])["points_balance"] == 0
    assert history["customer"]["points_balance"] == 0


def test_khach_co_lich_su_diem_chi_ngung_su_dung_va_co_the_dung_lai(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])

    deleted = client.delete(
        f"/api/customers/member/{customer['id']}",
        headers=auth(ctx["token"]),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["msg"] == "Deactivated"

    detail = client.get(
        f"/api/customers/member/{customer['id']}",
        headers=auth(ctx["token"]),
    ).json()
    assert detail["is_active"] is False
    assert detail["points_balance"] == 10

    active_only = client.get(
        f"/api/customers/{ctx['shop_id']}", headers=auth(ctx["token"])
    ).json()
    assert customer["id"] not in {row["id"] for row in active_only}
    all_customers = client.get(
        f"/api/customers/{ctx['shop_id']}?include_inactive=true",
        headers=auth(ctx["token"]),
    ).json()
    assert customer["id"] in {row["id"] for row in all_customers}

    stock_before = _product_stock(ctx["product"]["id"])
    rejected = _create_order(
        client, ctx, customer_id=customer["id"], method="cash"
    )
    assert rejected.status_code == 400, rejected.text
    assert _product_stock(ctx["product"]["id"]) == stock_before

    enabled_again = client.put(
        f"/api/customers/member/{customer['id']}/status",
        json={"is_active": True},
        headers=auth(ctx["token"]),
    )
    assert enabled_again.status_code == 200, enabled_again.text
    assert enabled_again.json()["is_active"] is True
    assert enabled_again.json()["points_balance"] == 10


def test_xoa_khach_kiem_lich_su_diem_sau_khi_da_lay_shop_lock(
    client, monkeypatch
):
    """Khóa phải đứng trước lần kiểm quyết định xóa cứng hay ngừng dùng.

    Cờ mô phỏng một lần cộng điểm vừa hoàn tất trong lúc nút Xóa đang chờ
    write-lock. Bản cũ không hề lấy lock nên sẽ xóa cứng và làm test này đỏ.
    """
    ctx = seller_with_shop(client)
    customer = _create_customer(client, ctx)
    lock_acquired = False

    def fake_lock(db, shop_id):
        nonlocal lock_acquired
        assert shop_id == ctx["shop_id"]
        lock_acquired = True

    def history_after_lock(db, customer_id, shop_id=None):
        assert customer_id == customer["id"]
        assert shop_id == ctx["shop_id"]
        return lock_acquired

    monkeypatch.setattr(order_service, "_lock_shop_for_order", fake_lock)
    monkeypatch.setattr(loyalty_service, "has_history", history_after_lock)

    deleted = client.delete(
        f"/api/customers/member/{customer['id']}",
        headers=auth(ctx["token"]),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["msg"] == "Deactivated"
    assert lock_acquired is True

    detail = client.get(
        f"/api/customers/member/{customer['id']}",
        headers=auth(ctx["token"]),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is False


def test_unique_index_so_diem_ton_tai_va_duoc_kiem_bat_buoc(client, db):
    indexes = {
        row[1]: row[2]
        for row in db.execute(text("PRAGMA index_list(loyalty_point_entries)"))
    }
    name = "ux_loyalty_point_entries_idempotency_key"
    assert indexes[name] == 1, "Khóa ledger điểm phải là UNIQUE"
    assert name in bootstrap._REQUIRED_INDEXES
    assert name in bootstrap._FINANCIAL_INDEXES
    assert "ux_order_returns_idempotency_key" in bootstrap._FINANCIAL_INDEXES
    assert name not in bootstrap.verify_required_indexes(db)
    assert "ix_loyalty_point_entries_customer_created" in indexes
    assert "ix_loyalty_point_entries_shop_created" in indexes
    assert "ix_loyalty_entries_customer_created" not in indexes
    assert "ix_loyalty_entries_shop_created" not in indexes


def test_verify_khong_tin_index_tai_chinh_chi_vi_trung_ten(client, db):
    name = "ux_loyalty_point_entries_idempotency_key"
    db.execute(text(f'DROP INDEX "{name}"'))
    db.execute(
        text(
            f'CREATE INDEX "{name}" '
            "ON loyalty_point_entries(entry_type)"
        )
    )
    db.commit()
    try:
        assert name in bootstrap.verify_required_indexes(db)
    finally:
        db.execute(text(f'DROP INDEX "{name}"'))
        db.execute(
            text(
                f'CREATE UNIQUE INDEX "{name}" '
                "ON loyalty_point_entries(idempotency_key)"
            )
        )
        db.commit()


def test_verify_tu_choi_partial_index_gia_cho_ledger_diem(client, db):
    """Index ledger phải che mọi loại bút toán, không chỉ EARN."""
    name = "ux_loyalty_point_entries_idempotency_key"
    db.execute(text(f'DROP INDEX "{name}"'))
    db.execute(
        text(
            f'CREATE UNIQUE INDEX "{name}" '
            "ON loyalty_point_entries(idempotency_key) "
            "WHERE entry_type = 'EARN'"
        )
    )
    db.commit()
    try:
        assert name in bootstrap.verify_required_indexes(db)
    finally:
        db.execute(text(f'DROP INDEX "{name}"'))
        db.execute(
            text(
                f'CREATE UNIQUE INDEX "{name}" '
                "ON loyalty_point_entries(idempotency_key)"
            )
        )
        db.commit()


def test_verify_tu_choi_sai_predicate_cua_index_ca_open(client, db):
    """Index ca là partial có chủ đích, nhưng WHERE phải đúng OPEN."""
    name = "ux_cash_shifts_shop_user_open"
    db.execute(text(f'DROP INDEX "{name}"'))
    db.execute(
        text(
            f'CREATE UNIQUE INDEX "{name}" '
            "ON cash_shifts(shop_id, opened_by_user_id) "
            "WHERE status = 'NEVER_MATCH'"
        )
    )
    db.commit()
    try:
        assert name in bootstrap.verify_required_indexes(db)
    finally:
        db.execute(text(f'DROP INDEX "{name}"'))
        db.execute(
            text(
                f'CREATE UNIQUE INDEX "{name}" '
                "ON cash_shifts(shop_id, opened_by_user_id) "
                "WHERE status = 'OPEN'"
            )
        )
        db.commit()


# ---------- Cộng điểm ----------
def test_chuong_trinh_tat_khong_cong_diem(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, enabled=False)
    customer = _create_customer(client, ctx)

    order_id = _earn_with_cash(client, ctx, customer["id"])
    assert _balance(client, ctx, customer["id"]) == 0
    assert _order_record(order_id)["points_earned"] == 0


def test_tien_mat_chi_cong_sau_khi_paid_va_lam_tron_xuong_theo_block(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, earn_amount=10_000, earn_points=3)
    customer = _create_customer(client, ctx)
    product = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        _unique("SP le"),
        25_999,
        10,
        ctx["category_id"],
    )

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        product=product,
        method="cash",
    )
    assert created.status_code == 200, created.text
    assert created.json()["loyalty_points_earned"] == 0
    assert _balance(client, ctx, customer["id"]) == 0

    order_id = created.json()["order_id"]
    _pay_cash(client, ctx, order_id)
    # floor(25.999 / 10.000) * 3 = 6, không phải làm tròn thành 8.
    assert _balance(client, ctx, customer["id"]) == 6
    assert _order_record(order_id)["points_earned"] == 6


def test_voucher_ap_truoc_diem_va_chi_cong_tren_tien_thuc_tra(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # 100k -> 10 điểm
    assert _balance(client, ctx, customer["id"]) == 10
    code = _voucher(client, ctx, amount=10_000)

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=10,
        voucher=code,
        method="cash",
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["subtotal"] == 100_000
    assert body["discount"] == 10_000
    assert body["loyalty_points_redeemed"] == 10
    assert body["loyalty_discount"] == 10_000
    assert body["total"] == 80_000
    assert body["loyalty_balance"] == 0

    _pay_cash(client, ctx, body["order_id"])
    assert _balance(client, ctx, customer["id"]) == 8


def test_doi_ty_le_sau_khi_tao_don_khong_lam_doi_diem_se_cong(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, earn_amount=10_000, earn_points=1)
    customer = _create_customer(client, ctx)

    created = _create_order(
        client, ctx, customer_id=customer["id"], method="cash"
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]

    _save_program(client, ctx, earn_amount=20_000, earn_points=1)
    _pay_cash(client, ctx, order_id)
    assert _balance(client, ctx, customer["id"]) == 10
    record = _order_record(order_id)
    assert record["earn_amount_step"] == 10_000
    assert record["earn_points_step"] == 1


def test_tat_chuong_trinh_truoc_khi_paid_thi_khong_cong_va_khong_hoi_to(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    created = _create_order(
        client, ctx, customer_id=customer["id"], method="cash"
    )
    order_id = created.json()["order_id"]

    _save_program(client, ctx, enabled=False)
    _pay_cash(client, ctx, order_id)
    assert _balance(client, ctx, customer["id"]) == 0

    _save_program(client, ctx, enabled=True)
    _pay_cash(client, ctx, order_id)  # retry sau khi bật lại
    assert _balance(client, ctx, customer["id"]) == 0
    assert _order_record(order_id)["points_earned"] == 0


# ---------- Dùng điểm, cap và chống retry ----------
def test_doi_diem_lam_tron_xuong_theo_block(client):
    ctx = seller_with_shop(client)
    _save_program(
        client,
        ctx,
        earn_amount=10_000,
        earn_points=1,
        redeem_points=3,
        redeem_amount=2_000,
        min_redeem_points=3,
    )
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # 10 điểm

    created = _create_order(
        client, ctx, customer_id=customer["id"], points=5, method="cash"
    )
    assert created.status_code == 200, created.text
    assert created.json()["loyalty_points_redeemed"] == 3
    assert created.json()["loyalty_discount"] == 2_000
    assert created.json()["total"] == 98_000
    assert _balance(client, ctx, customer["id"]) == 7


def test_cap_tinh_sau_voucher_va_tu_giam_so_diem_ap_dung(client):
    ctx = seller_with_shop(client)
    _save_program(
        client,
        ctx,
        earn_amount=1_000,
        earn_points=1,
        redeem_points=3,
        redeem_amount=2_000,
        min_redeem_points=3,
        max_redeem_percent=50,
    )
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # 100 điểm
    code = _voucher(client, ctx, amount=20_000)

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=100,
        voucher=code,
        method="cash",
    )
    assert created.status_code == 200, created.text
    body = created.json()
    # Sau voucher còn 80k; cap 50% = 40k = 20 block = 60 điểm.
    assert body["discount"] == 20_000
    assert body["loyalty_points_redeemed"] == 60
    assert body["loyalty_discount"] == 40_000
    assert body["total"] == 40_000
    assert _balance(client, ctx, customer["id"]) == 40


def test_diem_giam_ve_0_thi_don_paid_ngay_khong_cho_qr(client):
    ctx = seller_with_shop(client)
    _save_program(
        client,
        ctx,
        earn_amount=1_000,
        earn_points=1,
        redeem_points=1,
        redeem_amount=1_000,
        max_redeem_percent=100,
    )
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    assert _balance(client, ctx, customer["id"]) == 100

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=100,
        method="transfer",
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "PAID"
    assert created.json()["total"] == 0
    assert created.json()["loyalty_points_redeemed"] == 100
    assert created.json()["loyalty_points_earned"] == 0
    assert created.json()["loyalty_balance"] == 0


def test_xin_dung_qua_so_du_bi_tu_choi_va_khong_co_tac_dung_phu(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # 10 điểm, tồn còn 9
    stock_before = _product_stock(ctx["product"]["id"])

    res = _create_order(
        client, ctx, customer_id=customer["id"], points=11, method="cash"
    )
    assert res.status_code == 400, res.text
    assert _balance(client, ctx, customer["id"]) == 10
    assert _product_stock(ctx["product"]["id"]) == stock_before


def test_mutation_ledger_tu_choi_redeem_qua_so_du_va_loai_la(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])

    session = SessionLocal()
    try:
        with pytest.raises(HTTPException) as over:
            loyalty_service.add_entry(
                session,
                ctx["shop_id"],
                customer["id"],
                loyalty_service.ENTRY_REDEEM,
                -11,
                "test:redeem-over-balance",
            )
        assert over.value.status_code == 400
        session.rollback()

        with pytest.raises(HTTPException) as unknown:
            loyalty_service.add_entry(
                session,
                ctx["shop_id"],
                customer["id"],
                "MYSTERY_MONEY_POINTS",
                -1,
                "test:unknown-entry",
            )
        assert unknown.value.status_code == 400
        session.rollback()
    finally:
        session.close()

    assert _balance(client, ctx, customer["id"]) == 10


def test_so_du_khong_du_mot_block_thi_tu_choi_va_khong_tru(client):
    ctx = seller_with_shop(client)
    _save_program(
        client,
        ctx,
        earn_amount=50_000,
        earn_points=1,
        redeem_points=3,
        redeem_amount=2_000,
        min_redeem_points=3,
    )
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # chỉ 2 điểm

    res = _create_order(
        client, ctx, customer_id=customer["id"], points=2, method="cash"
    )
    assert res.status_code == 400, res.text
    assert _balance(client, ctx, customer["id"]) == 2


def test_khong_chon_khach_thi_khong_duoc_dung_diem(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    res = _create_order(client, ctx, points=1, method="cash")
    assert res.status_code == 400, res.text
    assert _product_stock(ctx["product"]["id"]) == 10


def test_tat_chuong_trinh_chan_dung_nhung_giu_nguyen_so_du(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    _save_program(client, ctx, enabled=False)

    res = _create_order(
        client, ctx, customer_id=customer["id"], points=5, method="cash"
    )
    assert res.status_code == 400, res.text
    assert _balance(client, ctx, customer["id"]) == 10


def test_retry_tao_don_cung_ma_khong_tru_diem_hai_lan(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    operation_id = uuid.uuid4().hex

    first = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=4,
        method="cash",
        operation_id=operation_id,
    )
    retry = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=4,
        method="cash",
        operation_id=operation_id,
    )
    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json()
    assert _balance(client, ctx, customer["id"]) == 6
    redeemed = [
        row for row in _entries(order_id=first.json()["order_id"])
        if row["entry_type"] == "REDEEM"
    ]
    assert len(redeemed) == 1
    assert redeemed[0]["points_delta"] == -4


def test_kiem_so_du_va_ghi_dung_diem_dung_chung_mot_moc_thoi_gian(
    client, monkeypatch
):
    """Không để lô điểm hết hạn ở khe giữa kiểm số dư và ghi REDEEM."""
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=1)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])

    captured = {}
    real_balance = loyalty_service.balance_for_customer
    real_add_entry = loyalty_service.add_entry

    def capture_balance(*args, **kwargs):
        if kwargs.get("as_of") is not None:
            captured["balance_as_of"] = kwargs["as_of"]
        return real_balance(*args, **kwargs)

    def capture_entry(*args, **kwargs):
        entry_type = args[3] if len(args) > 3 else kwargs.get("entry_type")
        if entry_type == loyalty_service.ENTRY_REDEEM:
            captured["redeem_created_at"] = kwargs.get("created_at")
        return real_add_entry(*args, **kwargs)

    monkeypatch.setattr(loyalty_service, "balance_for_customer", capture_balance)
    monkeypatch.setattr(loyalty_service, "add_entry", capture_entry)

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=1,
        method="cash",
    )
    assert created.status_code == 200, created.text
    assert captured["balance_as_of"] is not None
    assert captured["redeem_created_at"] == captured["balance_as_of"]


def test_cung_ma_retry_nhung_doi_so_diem_thi_xung_dot(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    operation_id = uuid.uuid4().hex

    first = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=4,
        method="cash",
        operation_id=operation_id,
    )
    assert first.status_code == 200, first.text
    changed = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=3,
        method="cash",
        operation_id=operation_id,
    )
    assert changed.status_code == 409, changed.text
    assert _balance(client, ctx, customer["id"]) == 6


def test_huy_don_hoan_diem_da_dung_dung_mot_lan(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    created = _create_order(
        client, ctx, customer_id=customer["id"], points=10, method="cash"
    )
    order_id = created.json()["order_id"]
    assert _balance(client, ctx, customer["id"]) == 0

    first = client.post(
        f"/api/orders/{order_id}/cancel", headers=auth(ctx["token"])
    )
    retry = client.post(
        f"/api/orders/{order_id}/cancel", headers=auth(ctx["token"])
    )
    assert first.status_code == retry.status_code == 200
    assert _balance(client, ctx, customer["id"]) == 10
    deltas = [row["points_delta"] for row in _entries(order_id=order_id)]
    assert deltas.count(-10) == 1
    assert deltas.count(10) == 1


# ---------- Đơn nợ và webhook ----------
def test_don_no_chi_cong_diem_khi_tra_du_va_retry_khong_cong_lai(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    created = _create_order(
        client, ctx, customer_id=customer["id"], method="debt"
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]
    assert _balance(client, ctx, customer["id"]) == 0

    partial = client.post(
        f"/api/orders/{order_id}/debt-payment",
        json={
            "amount": 40_000,
            "method": "transfer",
            "operation_id": uuid.uuid4().hex,
        },
        headers=auth(ctx["token"]),
    )
    assert partial.status_code == 200, partial.text
    assert _balance(client, ctx, customer["id"]) == 0

    final_operation = uuid.uuid4().hex
    final_body = {
        "amount": 60_000,
        "method": "transfer",
        "operation_id": final_operation,
    }
    final = client.post(
        f"/api/orders/{order_id}/debt-payment",
        json=final_body,
        headers=auth(ctx["token"]),
    )
    retry = client.post(
        f"/api/orders/{order_id}/debt-payment",
        json=final_body,
        headers=auth(ctx["token"]),
    )
    assert final.status_code == retry.status_code == 200
    assert final.json()["loyalty_points_earned"] == 10
    assert final.json()["loyalty_balance"] == 10
    assert retry.json()["loyalty_points_earned"] == 10
    assert retry.json()["loyalty_balance"] == 10
    changed_retry = client.post(
        f"/api/orders/{order_id}/debt-payment",
        json={
            "amount": 50_000,
            "method": "cash",
            "operation_id": final_operation,
        },
        headers=auth(ctx["token"]),
    )
    assert changed_retry.status_code == 409, changed_retry.text
    assert _balance(client, ctx, customer["id"]) == 10
    earned = [x for x in _entries(order_id=order_id) if x["entry_type"] == "EARN"]
    assert len(earned) == 1
    assert earned[0]["points_delta"] == 10


def test_webhook_gui_lai_chi_cong_diem_mot_lan(client, monkeypatch):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    created = _create_order(
        client, ctx, customer_id=customer["id"], method="transfer"
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]
    secret = "loyalty-test-webhook"
    monkeypatch.setattr(webhooks, "get_webhook_secret", lambda: secret)
    payload = {
        "content": f"ORDER{order_id}",
        "transferAmount": 100_000,
        "transferType": "in",
        "referenceCode": f"LOYALTY-{order_id}",
    }

    first = client.post(
        "/api/orders/webhook", json=payload, headers={"X-Webhook-Secret": secret}
    )
    retry = client.post(
        "/api/orders/webhook", json=payload, headers={"X-Webhook-Secret": secret}
    )
    assert first.status_code == retry.status_code == 200
    assert _balance(client, ctx, customer["id"]) == 10
    assert len([x for x in _entries(order_id=order_id) if x["entry_type"] == "EARN"]) == 1

    status = client.get(
        f"/api/orders/{order_id}", headers=auth(ctx["token"])
    )
    detail = client.get(
        f"/api/orders/{order_id}/detail", headers=auth(ctx["token"])
    )
    assert status.status_code == detail.status_code == 200
    assert status.json()["loyalty_points_earned"] == 10
    assert status.json()["loyalty_balance"] == 10
    assert detail.json()["loyalty_points_earned"] == 10
    assert detail.json()["loyalty_balance"] == 10


def test_webhook_sai_tai_khoan_khong_cong_diem(client, monkeypatch):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    created = _create_order(
        client, ctx, customer_id=customer["id"], method="transfer"
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order_id"]
    before_order = _order_record(order_id)
    before_entries = _entries(order_id=order_id)
    before_balance = _balance(client, ctx, customer["id"])

    secret = "loyalty-account-mismatch"
    monkeypatch.setattr(webhooks, "get_webhook_secret", lambda: secret)
    response = client.post(
        "/api/orders/webhook",
        json={
            "content": f"ORDER{order_id}",
            "transferAmount": 100_000,
            "transferType": "in",
            "accountNumber": "9999999999",
            "referenceCode": f"LOYALTY-ACCOUNT-MISMATCH-{order_id}",
        },
        headers={"X-Webhook-Secret": secret},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rejected_order_ids"] == [order_id]
    assert _order_record(order_id) == before_order
    assert _entries(order_id=order_id) == before_entries
    assert _balance(client, ctx, customer["id"]) == before_balance == 0


# ---------- Trả hàng ----------
def test_tra_het_hoan_diem_da_dung_tru_diem_da_cong_va_retry_idempotent(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])  # số dư 10

    created = _create_order(
        client, ctx, customer_id=customer["id"], points=10, method="cash"
    )
    order_id = created.json()["order_id"]
    _pay_cash(client, ctx, order_id)  # thực trả 90k -> cộng 9; số dư 9
    assert _balance(client, ctx, customer["id"]) == 9

    operation_id = uuid.uuid4().hex
    first = _return_order(client, ctx, order_id, 1, operation_id=operation_id)
    retry = _return_order(client, ctx, order_id, 1, operation_id=operation_id)
    assert first.status_code == retry.status_code == 200
    assert retry.json()["return"]["id"] == first.json()["return"]["id"]
    returned = first.json()["return"]
    assert returned["loyalty_points_restored"] == 10
    assert returned["loyalty_points_reversed"] == 9
    assert _balance(client, ctx, customer["id"]) == 10

    return_entries = _entries(return_id=returned["id"])
    assert sorted(x["points_delta"] for x in return_entries) == [-9, 10]


def test_tra_tung_phan_tinh_luy_ke_de_lan_cuoi_khop_tuyet_doi(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"], qty=2)  # số dư 20

    created = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=10,
        qty=2,
        method="cash",
    )
    order_id = created.json()["order_id"]
    _pay_cash(client, ctx, order_id)  # 200k - 10k = 190k -> 19 điểm
    assert _balance(client, ctx, customer["id"]) == 29

    first_operation = uuid.uuid4().hex
    first = _return_order(client, ctx, order_id, 1, operation_id=first_operation)
    retry = _return_order(client, ctx, order_id, 1, operation_id=first_operation)
    assert first.status_code == retry.status_code == 200
    assert first.json()["return"]["loyalty_points_restored"] == 5
    # Khách giữ lại 95.000đ nên chỉ còn xứng đáng 9/19 điểm: lần đầu thu 10.
    assert first.json()["return"]["loyalty_points_reversed"] == 10
    assert _balance(client, ctx, customer["id"]) == 24

    second = _return_order(client, ctx, order_id, 1)
    assert second.status_code == 200, second.text
    assert second.json()["return"]["loyalty_points_restored"] == 5
    assert second.json()["return"]["loyalty_points_reversed"] == 9
    assert _balance(client, ctx, customer["id"]) == 20


def test_tra_don_cu_sau_khi_da_tieu_diem_cho_phep_so_du_am(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    old_order = _earn_with_cash(client, ctx, customer["id"])  # +10

    newer = _create_order(
        client, ctx, customer_id=customer["id"], points=10, method="cash"
    )
    _pay_cash(client, ctx, newer.json()["order_id"])  # -10 rồi +9 => 9
    assert _balance(client, ctx, customer["id"]) == 9

    returned = _return_order(client, ctx, old_order, 1)
    assert returned.status_code == 200, returned.text
    assert returned.json()["return"]["loyalty_points_reversed"] == 10
    assert _balance(client, ctx, customer["id"]) == -1


def test_tra_don_chi_thu_diem_do_chinh_don_do_sinh_ra(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=1)
    customer = _create_customer(client, ctx)
    expiring_order = _earn_with_cash(client, ctx, customer["id"])

    _save_program(client, ctx, expiry_days=None)
    source_order = _earn_with_cash(client, ctx, customer["id"])
    assert _balance(client, ctx, customer["id"]) == 20

    returned = _return_order(client, ctx, source_order, 1)
    assert returned.status_code == 200, returned.text
    assert returned.json()["return"]["loyalty_points_reversed"] == 10
    assert _balance(client, ctx, customer["id"]) == 10

    # Nếu RETURN_REVERSE ăn FEFO bừa, nó đã lấy lô sắp hết hạn của đơn đầu và
    # lô không hạn của đơn bị trả vẫn còn 10. Đẩy riêng lô đầu qua hạn sẽ lộ.
    session = SessionLocal()
    try:
        first_earn = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == expiring_order,
                models.LoyaltyPointEntry.entry_type == "EARN",
            )
            .one()
        )
        reverse_entry = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == source_order,
                models.LoyaltyPointEntry.entry_type == "RETURN_REVERSE",
            )
            .one()
        )
        # Còn hạn đúng lúc RETURN_REVERSE chạy, nhưng hết hạn ngay sau đó.
        first_earn.expires_at = reverse_entry.created_at + timedelta(
            microseconds=1
        )
        session.commit()
    finally:
        session.close()
    assert _balance(client, ctx, customer["id"]) == 0


def test_tra_don_khong_tao_no_tu_diem_da_het_han_ma_chua_dung(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=1)
    customer = _create_customer(client, ctx)
    source_order = _earn_with_cash(client, ctx, customer["id"])

    session = SessionLocal()
    try:
        earned = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == source_order,
                models.LoyaltyPointEntry.entry_type == "EARN",
            )
            .one()
        )
        earned.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    assert _balance(client, ctx, customer["id"]) == 0

    returned = _return_order(client, ctx, source_order, 1)
    assert returned.status_code == 200, returned.text
    assert returned.json()["return"]["loyalty_points_reversed"] == 0
    assert _balance(client, ctx, customer["id"]) == 0


def test_diem_hoan_khi_tra_hang_bu_no_am_truoc_khi_mang_han_moi(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=30)
    customer = _create_customer(client, ctx)
    source_order = _earn_with_cash(client, ctx, customer["id"])  # +10

    redeemed = _create_order(
        client,
        ctx,
        customer_id=customer["id"],
        points=10,
        method="cash",
    )
    redeemed_order = redeemed.json()["order_id"]
    _pay_cash(client, ctx, redeemed_order)  # dùng 10 rồi đơn mới kiếm 9

    returned_source = _return_order(client, ctx, source_order, 1)
    assert returned_source.status_code == 200, returned_source.text
    assert _balance(client, ctx, customer["id"]) == -1

    returned_redeemed = _return_order(client, ctx, redeemed_order, 1)
    assert returned_redeemed.status_code == 200, returned_redeemed.text
    assert returned_redeemed.json()["return"]["loyalty_points_reversed"] == 9
    assert returned_redeemed.json()["return"]["loyalty_points_restored"] == 10
    assert _balance(client, ctx, customer["id"]) == 0

    # Dù entry hoàn có hạn mới, cả 10 điểm đã dùng để bù nợ nên qua hạn không
    # được làm số dư rơi trở lại -10.
    session = SessionLocal()
    try:
        restored = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == redeemed_order,
                models.LoyaltyPointEntry.entry_type == "RETURN_RESTORE",
            )
            .one()
        )
        restored.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    assert _balance(client, ctx, customer["id"]) == 0


def test_huy_don_giu_han_diem_goc_va_tu_huy_cung_hoan_dung_mot_lan(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=30)

    manual_customer = _create_customer(client, ctx)
    source = _earn_with_cash(client, ctx, manual_customer["id"])
    original_expiry = next(
        entry for entry in _entries(order_id=source) if entry["entry_type"] == "EARN"
    )["expires_at"]
    pending = _create_order(
        client, ctx, customer_id=manual_customer["id"], points=10, method="cash"
    )
    pending_id = pending.json()["order_id"]
    first = client.post(
        f"/api/orders/{pending_id}/cancel", headers=auth(ctx["token"])
    )
    retry = client.post(
        f"/api/orders/{pending_id}/cancel", headers=auth(ctx["token"])
    )
    assert first.status_code == retry.status_code == 200
    assert first.json()["loyalty_points_restored"] == 10
    assert retry.json()["loyalty_points_restored"] == 10
    cancel_entries = [
        entry
        for entry in _entries(order_id=pending_id)
        if entry["entry_type"] == "CANCEL_RESTORE"
    ]
    assert len(cancel_entries) == 1
    assert cancel_entries[0]["expires_at"] == original_expiry
    assert _balance(client, ctx, manual_customer["id"]) == 10

    auto_customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, auto_customer["id"])
    auto_pending = _create_order(
        client, ctx, customer_id=auto_customer["id"], points=10, method="cash"
    )
    auto_id = auto_pending.json()["order_id"]
    session = SessionLocal()
    try:
        order = session.query(models.Order).filter(models.Order.id == auto_id).one()
        assert order_service.cancel_expired_order(session, order) is True
    finally:
        session.close()
    assert _balance(client, ctx, auto_customer["id"]) == 10
    assert len([
        entry for entry in _entries(order_id=auto_id)
        if entry["entry_type"] == "CANCEL_RESTORE"
    ]) == 1


def test_huy_sau_khi_han_goc_da_qua_khong_hoi_sinh_diem(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=1)
    customer = _create_customer(client, ctx)
    source = _earn_with_cash(client, ctx, customer["id"])
    pending = _create_order(
        client, ctx, customer_id=customer["id"], points=10, method="cash"
    )
    pending_id = pending.json()["order_id"]

    session = SessionLocal()
    try:
        earned = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == source,
                models.LoyaltyPointEntry.entry_type == "EARN",
            )
            .one()
        )
        redeemed = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.order_id == pending_id,
                models.LoyaltyPointEntry.entry_type == "REDEEM",
            )
            .one()
        )
        # Điểm hợp lệ lúc bấm dùng, rồi hết hạn trước lúc hủy.
        earned.expires_at = redeemed.created_at + timedelta(microseconds=1)
        session.commit()
    finally:
        session.close()

    cancelled = client.post(
        f"/api/orders/{pending_id}/cancel", headers=auth(ctx["token"])
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["loyalty_points_restored"] == 0
    assert _balance(client, ctx, customer["id"]) == 0


# ---------- Hết hạn và offline ----------
def test_diem_het_han_khong_con_trong_so_du_va_khong_dung_duoc(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=1)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    assert _balance(client, ctx, customer["id"]) == 10

    session = SessionLocal()
    try:
        earned = (
            session.query(models.LoyaltyPointEntry)
            .filter(
                models.LoyaltyPointEntry.customer_id == customer["id"],
                models.LoyaltyPointEntry.entry_type == "EARN",
            )
            .one()
        )
        earned.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()

    assert _balance(client, ctx, customer["id"]) == 0
    res = _create_order(
        client, ctx, customer_id=customer["id"], points=1, method="cash"
    )
    assert res.status_code == 400, res.text


def test_doi_expiry_days_khong_viet_lai_han_cua_diem_cu(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx, expiry_days=7)
    customer = _create_customer(client, ctx)
    order_id = _earn_with_cash(client, ctx, customer["id"])
    before = next(
        x for x in _entries(order_id=order_id) if x["entry_type"] == "EARN"
    )["expires_at"]
    assert before is not None

    _save_program(client, ctx, expiry_days=365)
    after = next(
        x for x in _entries(order_id=order_id) if x["entry_type"] == "EARN"
    )["expires_at"]
    assert after == before


def test_don_offline_khong_cong_khong_dung_diem_du_payload_co_gui_them(client):
    ctx = seller_with_shop(client)
    _save_program(client, ctx)
    customer = _create_customer(client, ctx)
    _earn_with_cash(client, ctx, customer["id"])
    assert _balance(client, ctx, customer["id"]) == 10

    sold_at = datetime.utcnow().isoformat()
    payload = {
        "offline_uuid": "off-" + uuid.uuid4().hex,
        "sold_at": sold_at,
        "items": [
            {
                "product_id": ctx["product"]["id"],
                "product_name": ctx["product"]["name"],
                "unit_price": ctx["product"]["price"],
                "quantity": 1,
            }
        ],
        "cash_tendered": ctx["product"]["price"],
        "device_label": "POS-LOYALTY-TEST",
        # OfflineOrderCreate cố ý không có hai field này. Kể cả client lỗi gửi
        # thêm, backend không được phép âm thầm đụng vào sổ điểm.
        "customer_id": customer["id"],
        "loyalty_points_to_use": 10,
    }
    res = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        json=payload,
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    assert _balance(client, ctx, customer["id"]) == 10

    session = SessionLocal()
    try:
        offline = (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == payload["offline_uuid"])
            .one()
        )
        assert offline.customer_id is None
        assert offline.loyalty_points_redeemed == 0
        assert offline.loyalty_points_earned == 0
        assert (
            session.query(models.LoyaltyPointEntry)
            .filter(models.LoyaltyPointEntry.order_id == offline.id)
            .count()
            == 0
        )
    finally:
        session.close()
