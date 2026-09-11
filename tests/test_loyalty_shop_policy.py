"""Quy tắc cấu hình số nguyên và giữ shop đã có sổ điểm."""
from pathlib import Path
import uuid

from conftest import _unique, auth, seller_with_shop

from fselling import models
from fselling.core.database import SessionLocal


ROOT = Path(__file__).resolve().parent.parent

PROGRAM = {
    "enabled": True,
    "earn_amount": 10_000,
    "earn_points": 1,
    "redeem_points": 10,
    "redeem_amount": 10_000,
    "min_redeem_points": 10,
    "max_redeem_percent": 50,
    "expiry_days": 90,
}


def _save_program(client, ctx, payload=None):
    return client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json=payload or PROGRAM,
        headers=auth(ctx["token"]),
    )


def test_cau_hinh_chi_nhan_so_nguyen_json(client):
    ctx = seller_with_shop(client)
    money_fields = ("earn_amount", "redeem_amount")
    integer_fields = (
        "earn_points",
        "redeem_points",
        "min_redeem_points",
        "expiry_days",
    )

    # VND rejects fractional values but accepts compatibility representations
    # that are exactly integral. Point/day counters stay strict integers.
    for field in money_fields:
        for invalid in (1.5, "2.5"):
            payload = {**PROGRAM, field: invalid}
            response = _save_program(client, ctx, payload)
            assert response.status_code == 422, (field, invalid, response.text)
    for field in integer_fields:
        for invalid in (1.5, 2.0, "2"):
            payload = {**PROGRAM, field: invalid}
            response = _save_program(client, ctx, payload)
            assert response.status_code == 422, (field, invalid, response.text)
    for invalid_rate in (1.234, "2.345"):
        response = _save_program(
            client, ctx, {**PROGRAM, "max_redeem_percent": invalid_rate}
        )
        assert response.status_code == 422, response.text

    for invalid_enabled in (1, "true"):
        response = _save_program(
            client,
            ctx,
            {**PROGRAM, "enabled": invalid_enabled},
        )
        assert response.status_code == 422, response.text

    # Mọi request sai đều bị chặn trước service, không được tạo nửa cấu hình.
    loaded = client.get(
        f"/api/loyalty/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert loaded.status_code == 200
    assert loaded.json()["id"] is None

    for field in money_fields:
        for exact in (2.0, "2", "2.0"):
            response = _save_program(client, ctx, {**PROGRAM, field: exact})
            assert response.status_code == 200, (field, exact, response.text)
            assert response.json()[field] == 2
    for exact_rate in (12.34, "12.34"):
        response = _save_program(
            client, ctx, {**PROGRAM, "max_redeem_percent": exact_rate}
        )
        assert response.status_code == 200, response.text
        assert response.json()["max_redeem_percent"] == 12.34


def test_cau_hinh_so_nguyen_luu_va_doc_lai_du(client):
    ctx = seller_with_shop(client)
    response = _save_program(client, ctx)

    assert response.status_code == 200, response.text
    body = response.json()
    for field, value in PROGRAM.items():
        assert body[field] == value, field


def test_shop_da_luu_chuong_trinh_thi_chi_duoc_khoa(client):
    ctx = seller_with_shop(client)
    saved = _save_program(client, ctx, {"enabled": False})
    assert saved.status_code == 200, saved.text

    deleted = client.delete(
        f"/api/shops/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert deleted.status_code == 409, deleted.text
    assert "Khóa" in deleted.json()["detail"]
    assert "sổ điểm" in deleted.json()["detail"]

    # Bị chặn phải là toàn bộ-or-không-gì: shop, hàng và cấu hình đều còn.
    session = SessionLocal()
    try:
        assert session.get(models.Shop, ctx["shop_id"]) is not None
        assert session.get(models.Product, ctx["product"]["id"]) is not None
        assert (
            session.query(models.LoyaltyProgram)
            .filter(models.LoyaltyProgram.shop_id == ctx["shop_id"])
            .count()
            == 1
        )
    finally:
        session.close()

    locked = client.put(
        f"/api/shops/{ctx['shop_id']}/status", headers=auth(ctx["token"])
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["is_active"] is False


def test_shop_co_ledger_diem_du_khong_con_program_van_khong_xoa(client):
    ctx = seller_with_shop(client)
    customer = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={"name": _unique("Khach giu so"), "phone": _unique("09")[:15]},
        headers=auth(ctx["token"]),
    )
    assert customer.status_code == 200, customer.text

    session = SessionLocal()
    try:
        assert (
            session.query(models.LoyaltyProgram)
            .filter(models.LoyaltyProgram.shop_id == ctx["shop_id"])
            .count()
            == 0
        )
        session.add(
            models.LoyaltyPointEntry(
                shop_id=ctx["shop_id"],
                customer_id=customer.json()["id"],
                entry_type="EARN",
                points_delta=1,
                idempotency_key=f"test:{uuid.uuid4().hex}",
                customer_name=customer.json()["name"],
            )
        )
        session.commit()
    finally:
        session.close()

    deleted = client.delete(
        f"/api/shops/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert deleted.status_code == 409, deleted.text

    session = SessionLocal()
    try:
        assert session.get(models.Shop, ctx["shop_id"]) is not None
        assert (
            session.query(models.LoyaltyPointEntry)
            .filter(models.LoyaltyPointEntry.shop_id == ctx["shop_id"])
            .count()
            == 1
        )
    finally:
        session.close()


def test_shop_chua_co_du_lieu_diem_van_xoa_nhu_cu(client):
    ctx = seller_with_shop(client)
    deleted = client.delete(
        f"/api/shops/{ctx['shop_id']}", headers=auth(ctx["token"])
    )
    assert deleted.status_code == 200, deleted.text

    session = SessionLocal()
    try:
        assert session.get(models.Shop, ctx["shop_id"]) is None
    finally:
        session.close()


def test_tab_tich_diem_chan_so_le_truoc_khi_goi_api_va_da_bump_cache():
    js = (ROOT / "static" / "js" / "seller.js").read_text(encoding="utf-8")
    locale = (ROOT / "static" / "js" / "locales" / "seller.js").read_text(
        encoding="utf-8"
    )
    html = (ROOT / "static" / "seller.html").read_text(encoding="utf-8")

    assert "_loyaltySoNguyenHopLe(earnAmount, 1)" in js
    assert "_loyaltySoNguyenHopLe(redeemAmount, 1)" in js
    assert "_loyaltySoNguyenHopLe(maxPercent, 1)" in js
    assert "seller.loyalty.amount_integer" in js
    assert locale.count("'seller.loyalty.amount_integer':") == 2
    assert 'id="loyaltyEarnAmount" type="number" min="1" step="1"' in html
    assert 'id="loyaltyRedeemAmount" type="number" min="1" step="1"' in html
    # R1 Forecast prefill sửa cả hai file này nên chúng cùng sang mốc mới. Ghim
    # cứng là có chủ ý: đổi file thì phải sửa dòng này, tức là nghĩ lại về cache.
    assert "/js/locales/seller.js?v=20260911-r5-5" in html
    assert "/js/seller.js?v=20260911-r5-5" in html
