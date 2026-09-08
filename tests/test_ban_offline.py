"""Kiểm bán hàng khi mất mạng.

Mỗi test ở đây tương ứng một cách mất tiền thật, không phải kiểm cho đủ:

- Giá tính lại theo hôm nay  -> ghi sai số tiền đang nằm trong két
- Sync hai lần               -> doanh thu và tồn kho cùng nhân đôi
- Hết hàng nên từ chối đơn   -> mất luôn dấu vết giao dịch, két thừa so với sổ
- Ca gắn theo giờ sync       -> doanh thu rơi sang ca đã chốt quỹ xong
"""
from __future__ import annotations

import copy
import threading
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from conftest import (
    _TEST_MIGRATIONS,
    _unique,
    auth,
    create_product,
    new_staff,
    seller_with_shop,
)

from fselling import models
from fselling.core import thoi_gian
from fselling.core.database import SessionLocal
from fselling.schemas.order import OfflineOrderCreate
from fselling.services import offline_service


def _uid() -> str:
    return "off-" + _uuid.uuid4().hex


def _phieu(product, so_luong=1, gia=None, tien_dua=None, luc_ban=None, uuid=None, may="POS-01"):
    """Mặc định `sold_at` = BÂY GIỜ, không phải quá khứ.

    Từng để mặc định 30 phút trước và bốn test đỏ oan: ca trong test được mở
    ngay lúc chạy, nên mọi phiếu "bán 30 phút trước" đều rơi vào khoảng chưa ai
    mở ca và bị gắn cờ KHONG_CO_CA. Đó là hành vi ĐÚNG của service — chỉ có
    kịch bản test là sai.
    """
    gia = product["price"] if gia is None else gia
    tong = gia * so_luong
    return {
        "offline_uuid": uuid or _uid(),
        "sold_at": (luc_ban or datetime.utcnow()).isoformat(),
        "items": [
            {
                "product_id": product["id"],
                "product_name": product["name"],
                "unit_price": gia,
                "quantity": so_luong,
            }
        ],
        "cash_tendered": tong if tien_dua is None else tien_dua,
        "device_label": may,
    }


def _gui(client, ctx, phieu):
    return client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        json=phieu,
        headers=auth(ctx["token"]),
    )


def _mo_ca(client, ctx, tien_dau=0):
    res = client.post(
        f"/api/shifts/{ctx['shop_id']}/open",
        json={"opening_cash_amount": tien_dau},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _doi_gia(client, ctx, sp, gia_moi):
    """PUT sản phẩm là form đầy đủ: thiếu `name` hay `category_id` là 422."""
    res = client.put(
        f"/api/products/{sp['id']}",
        data={"name": sp["name"], "price": gia_moi, "category_id": ctx["category_id"]},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text


def _ton_kho(product_id: int) -> int:
    s = SessionLocal()
    try:
        return s.query(models.Product).filter(models.Product.id == product_id).first().stock
    finally:
        s.close()


# ---------- Giá phải lấy từ phiếu ----------
def test_giu_nguyen_gia_tren_phieu_du_gia_hien_tai_da_doi(client):
    """Bán 100k lúc 9h, chủ shop đổi giá 120k, sync lúc 14h.

    Ghi 120k là ghi sai số tiền đang nằm trong két — lệch 20k và không tra ra
    được vì sao. Đây là lý do tồn tại của cả endpoint này.
    """
    ctx = seller_with_shop(client)
    sp = ctx["product"]  # giá 100000
    _mo_ca(client, ctx)

    _doi_gia(client, ctx, sp, 120000)  # chủ shop đổi giá SAU khi đã bán

    res = _gui(client, ctx, _phieu(sp, so_luong=1, gia=100000))
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 100000, "phải ghi giá khách đã trả, không phải giá hôm nay"


def test_gia_doi_thi_gan_co_cho_chu_shop_biet(client):
    ctx = seller_with_shop(client)
    sp = ctx["product"]
    _mo_ca(client, ctx)
    _doi_gia(client, ctx, sp, 120000)

    res = _gui(client, ctx, _phieu(sp, gia=100000))
    assert "GIA_DOI" in res.json()["issues"]


def test_gia_khong_doi_thi_khong_gan_co_gi(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    res = _gui(client, ctx, _phieu(ctx["product"]))
    assert res.status_code == 200, res.text
    assert res.json()["issues"] == []


# ---------- Chống ghi hai lần ----------
def test_gui_lai_cung_uuid_khong_tao_don_thu_hai(client):
    """Máy bán mất sóng giữa lúc gửi rồi gửi lại là chuyện BÌNH THƯỜNG."""
    ctx = seller_with_shop(client)
    sp = ctx["product"]
    _mo_ca(client, ctx)
    phieu = _phieu(sp, so_luong=2)

    r1 = _gui(client, ctx, phieu)
    assert r1.status_code == 200, r1.text
    assert r1.json()["created"] is True

    r2 = _gui(client, ctx, phieu)
    assert r2.status_code == 200, r2.text
    assert r2.json()["created"] is False, "lần hai phải là no-op"
    assert r2.json()["order_id"] == r1.json()["order_id"]


def test_gui_lai_khong_tru_kho_lan_hai(client):
    ctx = seller_with_shop(client)
    sp = ctx["product"]  # tồn 10
    _mo_ca(client, ctx)
    phieu = _phieu(sp, so_luong=3)

    _gui(client, ctx, phieu)
    con_sau_lan_dau = _ton_kho(sp["id"])
    _gui(client, ctx, phieu)
    assert _ton_kho(sp["id"]) == con_sau_lan_dau == 7


def test_gui_lai_khong_cong_tien_lan_hai(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    phieu = _phieu(ctx["product"], so_luong=2)
    _gui(client, ctx, phieu)
    _gui(client, ctx, phieu)

    s = SessionLocal()
    try:
        don_id = s.query(models.Order).filter(models.Order.offline_uuid == phieu["offline_uuid"]).first().id
        so_but_toan = (
            s.query(models.OrderPayment).filter(models.OrderPayment.order_id == don_id).count()
        )
    finally:
        s.close()
    assert so_but_toan == 1, "một phiếu chỉ được sinh đúng một bút toán tiền mặt"


def test_phieu_moi_tao_registry_receipt_v0_atomic_va_verifier_pass(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    sold_utc = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(seconds=1)
    sold_plus_seven = sold_utc.astimezone(timezone(timedelta(hours=7)))
    marker = "MÁY-I09-B1-" + _uuid.uuid4().hex
    payload = _phieu(
        ctx["product"],
        luc_ban=sold_plus_seven,
        may=f"  {marker}\u00a0 A  ",
    )

    response = _gui(client, ctx, payload)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True

    session = SessionLocal()
    try:
        order = session.get(models.Order, response.json()["order_id"])
        registry = session.get(models.OfflineReceiptRegistry, payload["offline_uuid"])
        receipt = (
            session.query(models.OfflineReceipt)
            .filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"])
            .one()
        )
        expected_sold = sold_utc.replace(tzinfo=None)
        assert order.created_at == order.sold_offline_at == expected_sold
        assert order.offline_device == f"{marker} A"
        assert registry.order_id == order.id
        assert registry.shop_id == ctx["shop_id"]
        assert registry.state == "INGESTED"
        assert registry.contract_version == 0
        assert registry.state_version == 0
        assert registry.server_fingerprint.startswith("fsofr0:")
        assert len(registry.server_fingerprint) == 71
        assert receipt.order_id == order.id
        assert receipt.server_fingerprint == registry.server_fingerprint
        assert receipt.contract_version == 0
        assert receipt.sold_by_claimed_user_id == receipt.synced_by_user_id == order.created_by_user_id
        assert receipt.attribution_kind == "LEGACY_UNKNOWN"
        assert receipt.time_confidence == "LEGACY"
        assert receipt.sold_at_effective == receipt.sold_at_client_utc
        assert receipt.sold_at_effective == expected_sold.strftime("%Y-%m-%d %H:%M:%S.%f")
        assert len(receipt.ingested_at) == 26
        assert (
            receipt.lease_id,
            receipt.device_id,
            receipt.offline_session_id,
            receipt.sequence,
            receipt.client_fingerprint,
            receipt.sold_at_upper_bound,
            receipt.client_monotonic_ms,
            receipt.server_anchor_id,
        ) == (None,) * 8
        assert receipt.client_fingerprint_mismatch == 0
        assert session.execute(
            text("SELECT typeof(total_vnd) FROM orders WHERE id=:id"),
            {"id": order.id},
        ).scalar_one() == "integer"
        audit = (
            session.query(models.SystemLog)
            .filter(models.SystemLog.action == "OFFLINE_SALE", models.SystemLog.details.contains(f"Đơn #{order.id}"))
            .one()
        )
        assert audit.shop_id == ctx["shop_id"]
        assert marker not in audit.details
        assert payload["offline_uuid"] not in audit.details
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


@pytest.mark.parametrize(
    "field",
    ["quantity", "price", "product_name", "sold_at", "cash_tendered", "device_label"],
)
def test_cung_uuid_doi_field_vat_chat_tra_fingerprint_conflict_atomic(client, field):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(
        ctx["product"],
        luc_ban=datetime.utcnow() - timedelta(seconds=2),
    )
    created = _gui(client, ctx, payload)
    assert created.status_code == 200, created.text
    before_stock = _ton_kho(ctx["product"]["id"])

    changed = copy.deepcopy(payload)
    if field == "quantity":
        changed["items"][0]["quantity"] += 1
    elif field == "price":
        changed["items"][0]["unit_price"] += 1
    elif field == "product_name":
        changed["items"][0]["product_name"] += " khác"
    elif field == "sold_at":
        changed["sold_at"] = (
            datetime.fromisoformat(changed["sold_at"]) - timedelta(seconds=1)
        ).isoformat()
    elif field == "cash_tendered":
        changed["cash_tendered"] += 1
    else:
        changed["device_label"] = "POS-02"

    conflict = _gui(client, ctx, changed)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "OFFLINE_RECEIPT_FINGERPRINT_CONFLICT"
    assert "order_id" not in conflict.text
    assert _ton_kho(ctx["product"]["id"]) == before_stock

    session = SessionLocal()
    try:
        order_id = created.json()["order_id"]
        assert session.query(models.Order).filter(models.Order.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.OrderPayment).filter(models.OrderPayment.order_id == order_id).count() == 1
        assert session.query(models.OfflineReceiptRegistry).filter(models.OfflineReceiptRegistry.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.SystemLog).filter(models.SystemLog.action == "OFFLINE_SALE", models.SystemLog.details.contains(f"Đơn #{order_id}")).count() == 1
    finally:
        session.close()


def test_reorder_items_va_timezone_tuong_duong_la_retry_hop_le(client):
    ctx = seller_with_shop(client)
    other = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        _unique("SP reorder"),
        50_000,
        10,
        ctx["category_id"],
    )
    _mo_ca(client, ctx)
    sold_utc = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(seconds=1)
    payload = _phieu(ctx["product"], luc_ban=sold_utc.astimezone(timezone(timedelta(hours=7))))
    payload["items"].append(
        {
            "product_id": other["id"],
            "product_name": other["name"],
            "unit_price": other["price"],
            "quantity": 1,
        }
    )
    payload["cash_tendered"] += other["price"]
    first = _gui(client, ctx, payload)
    assert first.status_code == 200, first.text

    retry = copy.deepcopy(payload)
    retry["items"].reverse()
    retry["sold_at"] = sold_utc.isoformat().replace("+00:00", "Z")
    second = _gui(client, ctx, retry)
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["order_id"] == first.json()["order_id"]
    assert _ton_kho(ctx["product"]["id"]) == 9
    assert _ton_kho(other["id"]) == 9


def test_duplicate_line_multiplicity_khong_bi_group_thanh_retry(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"], so_luong=1)
    payload["items"].append(copy.deepcopy(payload["items"][0]))
    payload["cash_tendered"] *= 2
    first = _gui(client, ctx, payload)
    assert first.status_code == 200, first.text

    grouped = copy.deepcopy(payload)
    grouped["items"] = [copy.deepcopy(payload["items"][0])]
    grouped["items"][0]["quantity"] = 2
    conflict = _gui(client, ctx, grouped)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "OFFLINE_RECEIPT_FINGERPRINT_CONFLICT"
    assert _ton_kho(ctx["product"]["id"]) == 8


# ---------- Tác động nghiệp vụ phải theo canonical, không theo thứ tự payload ----------
def _pool_mixed_known_unknown(client, ctx, ten: str) -> dict:
    """Sản phẩm không theo lô với pool giá vốn 1 unknown + 1 known basis 100."""
    sp = create_product(
        client, ctx["token"], ctx["shop_id"], _unique(ten), 100, 1, ctx["category_id"]
    )
    res = client.post(
        f"/api/products/{sp['id']}/stock",
        json={"delta": 1, "unit_cost": 100, "reason": "mixed cost pool"},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return sp


def _phieu_hai_dong_cung_san_pham(sp, uuid_value, *, dao_thu_tu: bool) -> dict:
    """Hai dòng CÙNG product, khác đơn giá. Canonical sắp 100 trước 200."""
    dong = [
        {
            "product_id": sp["id"],
            "product_name": sp["name"],
            "unit_price": 100,
            "quantity": 1,
        },
        {
            "product_id": sp["id"],
            "product_name": sp["name"],
            "unit_price": 200,
            "quantity": 1,
        },
    ]
    if dao_thu_tu:
        dong.reverse()
    return {
        "offline_uuid": uuid_value,
        "sold_at": datetime.utcnow().isoformat(),
        "items": dong,
        "cash_tendered": 300,
        "device_label": "POS-01",
    }


def _provenance_dong_hang(order_id: int):
    session = SessionLocal()
    try:
        return [
            (
                int(row.price),
                row.quantity,
                row.cost_known_qty,
                row.cost_unknown_qty,
                row.cost_basis_vnd,
            )
            for row in session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == order_id)
            .order_by(models.OrderItem.id)
            .all()
        ]
    finally:
        session.close()


def test_dao_thu_tu_payload_giu_nguyen_fingerprint_va_cost_provenance_canonical(client):
    """Hai dòng cùng product, pool mixed known/unknown, đảo thứ tự payload.

    Fingerprint coi `items` là tập không thứ tự. Nếu tác động nghiệp vụ vẫn chạy
    theo thứ tự client gửi thì cùng một phiếu — thậm chí hai request song song
    của chính nó — lại trừ pool giá vốn khác nhau: dòng nào nhận unknown và dòng
    nào nhận known/basis phụ thuộc request nào thắng. Lãi và trả hàng từng phần
    của cùng một phiếu sẽ khác nhau.
    """
    ctx_a = seller_with_shop(client)
    _mo_ca(client, ctx_a)
    sp_a = _pool_mixed_known_unknown(client, ctx_a, "SP canonical A")
    payload_a = _phieu_hai_dong_cung_san_pham(sp_a, _uid(), dao_thu_tu=True)
    dau = _gui(client, ctx_a, payload_a)
    assert dau.status_code == 200, dau.text
    assert dau.json()["created"] is True

    # Cùng canonical document dù thứ tự dòng ngược lại: phải là retry no-op.
    gui_lai = copy.deepcopy(payload_a)
    gui_lai["items"].reverse()
    lai = _gui(client, ctx_a, gui_lai)
    assert lai.status_code == 200, lai.text
    assert lai.json()["created"] is False
    assert lai.json()["order_id"] == dau.json()["order_id"]

    # Shop độc lập, cùng nội dung nhưng gửi theo thứ tự kia: provenance durable
    # phải trùng khít, không phụ thuộc request nào thắng.
    ctx_b = seller_with_shop(client)
    _mo_ca(client, ctx_b)
    sp_b = _pool_mixed_known_unknown(client, ctx_b, "SP canonical B")
    payload_b = _phieu_hai_dong_cung_san_pham(sp_b, _uid(), dao_thu_tu=False)
    sau = _gui(client, ctx_b, payload_b)
    assert sau.status_code == 200, sau.text

    prov_a = _provenance_dong_hang(dau.json()["order_id"])
    prov_b = _provenance_dong_hang(sau.json()["order_id"])
    assert prov_a == prov_b == [
        # Dòng 100 đứng trước theo canonical nên nhận phần unknown trước.
        (100, 1, 0, 1, 0),
        (200, 1, 1, 0, 100),
    ]
    _TEST_MIGRATIONS.verify()


def test_dao_thu_tu_payload_khong_doi_ket_qua_tra_hang_tung_phan(client):
    """Trả đúng dòng 200 ở hai shop gửi ngược thứ tự: tiền và giá vốn hoàn phải
    bằng nhau. Nếu dòng nào nhận known phụ thuộc payload thì hai lần trả cùng
    một mặt hàng lại trừ lãi khác nhau."""
    ket_qua = []
    for dao in (True, False):
        ctx = seller_with_shop(client)
        _mo_ca(client, ctx)
        sp = _pool_mixed_known_unknown(client, ctx, "SP canonical return")
        payload = _phieu_hai_dong_cung_san_pham(sp, _uid(), dao_thu_tu=dao)
        ban = _gui(client, ctx, payload)
        assert ban.status_code == 200, ban.text
        order_id = ban.json()["order_id"]

        session = SessionLocal()
        try:
            dong_200 = (
                session.query(models.OrderItem)
                .filter(
                    models.OrderItem.order_id == order_id,
                    models.OrderItem.price == 200,
                )
                .one()
            )
            dong_200_id = dong_200.id
        finally:
            session.close()

        tra = client.post(
            f"/api/orders/{order_id}/returns",
            json={
                "items": [
                    {"order_item_id": dong_200_id, "quantity": 1, "restock": True}
                ],
                "method": "transfer",
                "reason": "Kiểm thử hoàn offline",
                "reference": "TEST-RETURN",
                "operation_id": "off-return-" + _uuid.uuid4().hex,
            },
            headers=auth(ctx["token"]),
        )
        assert tra.status_code == 200, tra.text

        session = SessionLocal()
        try:
            dong = session.get(models.OrderItem, dong_200_id)
            sp_sau = session.get(models.Product, sp["id"])
            ket_qua.append(
                (
                    (
                        dong.returned_total_qty,
                        dong.returned_known_qty,
                        dong.returned_unknown_qty,
                        dong.returned_cost_basis_vnd,
                        dong.returned_refund_vnd,
                    ),
                    (
                        sp_sau.stock,
                        sp_sau.cost_known_qty,
                        sp_sau.cost_unknown_qty,
                        sp_sau.cost_basis_vnd,
                        sp_sau.cost_deficit_qty,
                    ),
                )
            )
        finally:
            session.close()

    assert ket_qua[0] == ket_qua[1] == ((1, 1, 0, 100, 200), (1, 1, 0, 100, 0))
    _TEST_MIGRATIONS.verify()


# ---------- Chuẩn hóa text và giờ biên không được thành 500 ----------
def test_ten_hang_chi_toan_khoang_trang_unicode_bi_tu_choi_400_khong_side_effect(client):
    """`product_name` là bằng chứng bắt buộc: sản phẩm bị xóa giữa bán và sync
    thì đó là thứ duy nhất nối khoản tiền trong két về mặt hàng đã bán. Chuỗi
    chỉ gồm khoảng trắng Unicode lọt qua `min_length=1` nhưng chuẩn hóa thành
    rỗng, nên phải bị chặn TRƯỚC mọi side effect."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    ton_truoc = _ton_kho(ctx["product"]["id"])
    phieu = _phieu(ctx["product"])
    phieu["items"][0]["product_name"] = "\u3000 \u00a0 "

    res = _gui(client, ctx, phieu)
    assert res.status_code == 400, res.text

    session = SessionLocal()
    try:
        assert (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == phieu["offline_uuid"])
            .count()
            == 0
        )
        assert (
            session.query(models.OrderPayment)
            .filter(
                models.OrderPayment.idempotency_key
                == f"offline:{phieu['offline_uuid']}"
            )
            .count()
            == 0
        )
        assert session.get(models.OfflineReceiptRegistry, phieu["offline_uuid"]) is None
        assert (
            session.query(models.OfflineReceipt)
            .filter(models.OfflineReceipt.offline_uuid == phieu["offline_uuid"])
            .count()
            == 0
        )
        assert (
            session.query(models.SystemLog)
            .filter(
                models.SystemLog.action == "OFFLINE_SALE",
                models.SystemLog.shop_id == ctx["shop_id"],
            )
            .count()
            == 0
        )
    finally:
        session.close()
    assert _ton_kho(ctx["product"]["id"]) == ton_truoc


def test_nhan_may_chi_toan_khoang_trang_van_thanh_null(client):
    """Nullable khác required: `device_label` rỗng vẫn canonical thành NULL."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    phieu = _phieu(ctx["product"], may="\u3000  \u00a0")

    res = _gui(client, ctx, phieu)
    assert res.status_code == 200, res.text
    assert res.json()["created"] is True

    session = SessionLocal()
    try:
        don = session.get(models.Order, res.json()["order_id"])
        assert don.offline_device is None
    finally:
        session.close()


def test_gio_ban_bien_co_offset_tra_400_an_toan_khong_500(client):
    """Năm 0001 kèm `+14:00` đẩy instant ra ngoài `datetime.min` khi đổi sang
    UTC. Input không hợp lệ không được làm endpoint 500."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    ton_truoc = _ton_kho(ctx["product"]["id"])
    phieu = _phieu(ctx["product"])
    phieu["sold_at"] = "0001-01-01T00:00:00+14:00"

    res = _gui(client, ctx, phieu)
    assert res.status_code == 400, res.text

    session = SessionLocal()
    try:
        assert (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == phieu["offline_uuid"])
            .count()
            == 0
        )
        assert session.get(models.OfflineReceiptRegistry, phieu["offline_uuid"]) is None
    finally:
        session.close()
    assert _ton_kho(ctx["product"]["id"]) == ton_truoc


def test_retry_order_legacy_pre_registry_khong_fabricate_bang_chung(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"])
    first = _gui(client, ctx, payload)
    assert first.status_code == 200, first.text

    session = SessionLocal()
    try:
        session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).delete()
        session.query(models.OfflineReceiptRegistry).filter(models.OfflineReceiptRegistry.offline_uuid == payload["offline_uuid"]).delete()
        session.commit()
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    changed = copy.deepcopy(payload)
    changed["items"][0]["quantity"] = 9
    retry = _gui(client, ctx, changed)
    assert retry.status_code == 200, retry.text
    assert retry.json()["created"] is False
    assert retry.json()["order_id"] == first.json()["order_id"]
    session = SessionLocal()
    try:
        assert session.get(models.OfflineReceiptRegistry, payload["offline_uuid"]) is None
        assert session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 0
    finally:
        session.close()


def test_registry_receipt_corruption_fail_closed_khong_tu_va(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"])
    first = _gui(client, ctx, payload)
    assert first.status_code == 200, first.text
    stock = _ton_kho(ctx["product"]["id"])

    session = SessionLocal()
    try:
        session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).delete()
        session.commit()
    finally:
        session.close()

    failed = _gui(client, ctx, payload)
    assert failed.status_code == 409, failed.text
    assert failed.json()["detail"]["code"] == "OFFLINE_RECEIPT_REGISTRY_INCONSISTENT"
    assert _ton_kho(ctx["product"]["id"]) == stock
    session = SessionLocal()
    try:
        assert session.get(models.OfflineReceiptRegistry, payload["offline_uuid"]) is not None
        assert session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 0
        # The test suite shares one temporary database across TestClient
        # lifespans. Restore a verifier-valid legacy shape after proving the
        # endpoint did not self-heal the corruption.
        session.query(models.OfflineReceiptRegistry).filter(
            models.OfflineReceiptRegistry.offline_uuid == payload["offline_uuid"]
        ).delete()
        session.commit()
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_hai_retry_dong_thoi_chi_mot_order_payment_registry_receipt(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"], so_luong=2)
    request = OfflineOrderCreate(**payload)

    ready = threading.Barrier(2)
    real_lock = offline_service.order_service._lock_shop_for_order

    def synchronized_shop_lock(db, shop_id):
        ready.wait(timeout=5)
        return real_lock(db, shop_id)

    monkeypatch.setattr(
        offline_service.order_service,
        "_lock_shop_for_order",
        synchronized_shop_lock,
    )
    outcomes = []
    outcomes_lock = threading.Lock()

    def submit():
        session = SessionLocal()
        try:
            user = session.query(models.User).filter(models.User.username == ctx["username"]).one()
            outcome = offline_service.dong_bo_phieu(
                session, user, ctx["shop_id"], request
            )
        except HTTPException as exc:  # pragma: no cover - hiện rõ lỗi thread
            outcome = {"error": exc.status_code, "detail": exc.detail}
        except Exception as exc:  # pragma: no cover - hiện rõ lỗi thread
            outcome = {"error": type(exc).__name__, "detail": str(exc)}
        finally:
            session.close()
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert all("error" not in outcome for outcome in outcomes), outcomes
    assert {outcome["created"] for outcome in outcomes} == {True, False}
    assert len({outcome["order_id"] for outcome in outcomes}) == 1
    assert _ton_kho(ctx["product"]["id"]) == 8

    session = SessionLocal()
    try:
        order_id = outcomes[0]["order_id"]
        assert session.query(models.Order).filter(models.Order.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.OrderPayment).filter(models.OrderPayment.order_id == order_id).count() == 1
        assert session.query(models.OfflineReceiptRegistry).filter(models.OfflineReceiptRegistry.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 1
        assert session.query(models.SystemLog).filter(models.SystemLog.action == "OFFLINE_SALE", models.SystemLog.details.contains(f"Đơn #{order_id}")).count() == 1
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_commit_failure_rolls_back_order_ledger_inventory_receipt_audit(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"], so_luong=3)
    request = OfflineOrderCreate(**payload)

    session = SessionLocal()
    try:
        user = session.query(models.User).filter(models.User.username == ctx["username"]).one()
        before_audit = session.query(models.SystemLog).filter(
            models.SystemLog.action == "OFFLINE_SALE",
            models.SystemLog.shop_id == ctx["shop_id"],
        ).count()

        def fail_commit():
            raise RuntimeError("injected offline commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected offline commit failure"):
            offline_service.dong_bo_phieu(
                session, user, ctx["shop_id"], request
            )
    finally:
        session.close()

    fresh = SessionLocal()
    try:
        assert fresh.query(models.Order).filter(models.Order.offline_uuid == payload["offline_uuid"]).count() == 0
        assert fresh.get(models.OfflineReceiptRegistry, payload["offline_uuid"]) is None
        assert fresh.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 0
        assert fresh.query(models.OrderPayment).filter(models.OrderPayment.idempotency_key == f"offline:{payload['offline_uuid']}").count() == 0
        assert fresh.query(models.SystemLog).filter(
            models.SystemLog.action == "OFFLINE_SALE",
            models.SystemLog.shop_id == ctx["shop_id"],
        ).count() == before_audit
        assert fresh.get(models.Product, ctx["product"]["id"]).stock == 10
    finally:
        fresh.close()
    _TEST_MIGRATIONS.verify()


def test_lost_response_retry_reads_durable_winner_without_double_mutation(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    payload = _phieu(ctx["product"], so_luong=2)
    request = OfflineOrderCreate(**payload)

    session = SessionLocal()
    try:
        user = session.query(models.User).filter(models.User.username == ctx["username"]).one()
        real_commit = session.commit

        def commit_then_lose_response():
            real_commit()
            raise RuntimeError("simulated lost response")

        monkeypatch.setattr(session, "commit", commit_then_lose_response)
        with pytest.raises(RuntimeError, match="simulated lost response"):
            offline_service.dong_bo_phieu(
                session, user, ctx["shop_id"], request
            )
    finally:
        session.close()

    retry = _gui(client, ctx, payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["created"] is False
    assert _ton_kho(ctx["product"]["id"]) == 8
    fresh = SessionLocal()
    try:
        order_id = retry.json()["order_id"]
        assert fresh.query(models.Order).filter(models.Order.offline_uuid == payload["offline_uuid"]).count() == 1
        assert fresh.query(models.OrderPayment).filter(models.OrderPayment.order_id == order_id).count() == 1
        assert fresh.query(models.OfflineReceiptRegistry).filter(models.OfflineReceiptRegistry.offline_uuid == payload["offline_uuid"]).count() == 1
        assert fresh.query(models.OfflineReceipt).filter(models.OfflineReceipt.offline_uuid == payload["offline_uuid"]).count() == 1
        assert fresh.query(models.SystemLog).filter(models.SystemLog.action == "OFFLINE_SALE", models.SystemLog.details.contains(f"Đơn #{order_id}")).count() == 1
    finally:
        fresh.close()
    _TEST_MIGRATIONS.verify()


# ---------- Hết hàng vẫn phải ghi ----------
def test_khong_du_ton_van_ghi_don_va_cho_ton_am(client):
    """Hàng đã ra khỏi cửa thật. Từ chối đơn là mất dấu vết giao dịch, và tiền
    trong két sẽ thừa so với sổ."""
    ctx = seller_with_shop(client)
    sp = ctx["product"]  # tồn 10
    _mo_ca(client, ctx)

    res = _gui(client, ctx, _phieu(sp, so_luong=13))
    assert res.status_code == 200, res.text
    assert "TON_AM" in res.json()["issues"]
    assert _ton_kho(sp["id"]) == -3


def test_stocktake_reconcile_tracked_ton_am_reports_product_delta_not_batch_delta(client):
    """Contract cho preview UI: batch 5 không đổi nhưng Product 3 -> 5.

    Sau một TON_AM source-less tail, nhập thêm đúng một lô làm Product và tổng
    batch cùng tăng 5: chênh durable 2 vẫn còn. Kiểm kê batch vẫn là 5 phải trả
    riêng synthetic reconciliation +2; đây là giá trị modal cần báo trước.
    """
    ctx = seller_with_shop(client)
    created = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": _unique("Offline preview reconciliation"),
            "price": 100,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert created.status_code == 200, created.text
    product = created.json()
    expiry = (thoi_gian.hom_nay_vn() + timedelta(days=30)).isoformat()
    seeded = client.post(
        f"/api/products/{product['id']}/stock",
        json={"delta": 5, "expiry_date": expiry, "reason": "preview seed"},
        headers=auth(ctx["token"]),
    )
    assert seeded.status_code == 200, seeded.text
    _mo_ca(client, ctx)
    sold = _gui(client, ctx, _phieu(product, so_luong=7))
    assert sold.status_code == 200, sold.text
    assert "TON_AM" in sold.json()["issues"]
    replenished = client.post(
        f"/api/products/{product['id']}/stock",
        json={"delta": 5, "expiry_date": expiry, "reason": "preview replenishment"},
        headers=auth(ctx["token"]),
    )
    assert replenished.status_code == 200, replenished.text

    snapshot = client.get(
        f"/api/products/{ctx['shop_id']}/stocktake/batches",
        headers=auth(ctx["token"]),
    )
    assert snapshot.status_code == 200, snapshot.text
    row = next(item for item in snapshot.json()["products"] if item["product_id"] == product["id"])
    assert row["stock"] == 3
    assert row["offline_deficit_qty"] == 2
    assert [batch["quantity"] for batch in row["batches"]] == [5]

    applied = client.post(
        f"/api/products/{ctx['shop_id']}/stocktake",
        json={"items": [{
            "product_id": product["id"],
            "offline_deficit_snapshot": row["offline_deficit_snapshot"],
            "batches": [{
                "batch_id": row["batches"][0]["batch_id"],
                "quantity_snapshot": 5,
                "counted": 5,
            }],
        }]},
        headers=auth(ctx["token"]),
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["tong_lech"] == 2
    assert body["da_dieu_chinh"] == [{
        "product_id": product["id"],
        "batch_id": None,
        "name": product["name"],
        "expiry_date": None,
        "truoc": 3,
        "sau": 5,
        "lech": 2,
        "offline_deficit_reconciled": True,
    }]
    assert _ton_kho(product["id"]) == 5

    # Đóng một TON_AM tracked cũng phải để lại dấu vết riêng, không chỉ nằm gộp
    # trong dòng log kiểm kê chung.
    session = SessionLocal()
    try:
        audit = session.execute(
            text(
                "SELECT shop_id, user_id, details FROM system_logs"
                " WHERE action = 'OFFLINE_ISSUE_RESOLVED' ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
    finally:
        session.close()
    assert audit is not None, "thiếu audit cho transition RESOLVED"
    assert audit[0] == ctx["shop_id"]
    assert audit[1] is not None
    assert "TON_AM" in audit[2] and "OPEN -> RESOLVED" in audit[2]
    _TEST_MIGRATIONS.verify()


def test_tracked_ton_am_evidence_restart_stocktake_aba_return_cancel(client):
    ctx = seller_with_shop(client)
    response = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": _unique("Offline tracked evidence"),
            "price": 100,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    product = response.json()
    expiry = (thoi_gian.hom_nay_vn() + timedelta(days=30)).isoformat()
    response = client.post(
        f"/api/products/{product['id']}/stock",
        json={
            "delta": 2,
            "expiry_date": expiry,
            "reason": "offline deficit regression",
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    _mo_ca(client, ctx)

    first_payload = _phieu(product, so_luong=3)
    first = _gui(client, ctx, first_payload)
    assert first.status_code == 200, first.text
    assert "TON_AM" in first.json()["issues"]

    session = SessionLocal()
    try:
        first_order = (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == first_payload["offline_uuid"])
            .one()
        )
        first_line = (
            session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == first_order.id)
            .one()
        )
        evidence = (
            session.query(models.OfflineBatchStockDeficit)
            .filter(
                models.OfflineBatchStockDeficit.order_item_id == first_line.id
            )
            .one()
        )
        assert (
            evidence.product_id,
            evidence.deficit_quantity,
            evidence.remaining_quantity,
            evidence.resolution_kind,
            evidence.state_version,
        ) == (product["id"], 1, 1, None, 0)
        assert sum(
            row.quantity
            for row in session.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == first_line.id)
            .all()
        ) == 2
        assert session.get(models.Product, product["id"]).stock == -1
        assert sum(
            row.quantity
            for row in session.query(models.ProductBatch)
            .filter(models.ProductBatch.product_id == product["id"])
            .all()
        ) == 0
        first_order_id = first_order.id
        first_line_id = first_line.id
        open_before = {
            "returns": session.query(models.OrderReturn)
            .filter(models.OrderReturn.order_id == first_order.id)
            .count(),
            "payments": session.query(models.OrderPayment)
            .filter(models.OrderPayment.order_id == first_order.id)
            .count(),
            "loyalty": session.query(models.LoyaltyPointEntry)
            .filter(models.LoyaltyPointEntry.order_id == first_order.id)
            .count(),
            "logs": session.query(models.SystemLog)
            .filter(models.SystemLog.action == "ORDER_RETURN")
            .count(),
        }
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    open_return = client.post(
        f"/api/orders/{first_order_id}/returns",
        json={
            "items": [
                {
                    "order_item_id": first_line_id,
                    "quantity": 1,
                    "restock": False,
                }
            ],
            "method": "transfer",
            "reason": "Kiểm thử hoàn offline",
            "reference": "TEST-RETURN",
            "operation_id": "offline-open-missing-source-" + _uuid.uuid4().hex,
        },
        headers=auth(ctx["token"]),
    )
    assert open_return.status_code == 409, open_return.text
    with SessionLocal() as session:
        line = session.get(models.OrderItem, first_line_id)
        evidence = session.query(models.OfflineBatchStockDeficit).filter_by(
            order_item_id=first_line_id
        ).one()
        assert (line.returned_total_qty, line.cost_return_version) == (0, 0)
        assert (
            evidence.deficit_quantity,
            evidence.remaining_quantity,
            evidence.resolution_kind,
            evidence.state_version,
        ) == (1, 1, None, 0)
        assert session.get(models.Product, product["id"]).stock == -1
        assert session.query(models.OrderReturn).filter_by(
            order_id=first_order_id
        ).count() == open_before["returns"]
        assert session.query(models.OrderPayment).filter_by(
            order_id=first_order_id
        ).count() == open_before["payments"]
        assert session.query(models.LoyaltyPointEntry).filter_by(
            order_id=first_order_id
        ).count() == open_before["loyalty"]
        assert session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count() == open_before["logs"]
    _TEST_MIGRATIONS.verify()

    snapshot_response = client.get(
        f"/api/products/{ctx['shop_id']}/stocktake/batches",
        headers=auth(ctx["token"]),
    )
    assert snapshot_response.status_code == 200, snapshot_response.text
    snapshot_row = next(
        row
        for row in snapshot_response.json()["products"]
        if row["product_id"] == product["id"]
    )
    assert snapshot_row["batches"] == []
    assert snapshot_row["offline_deficit_qty"] == 1
    stale_token = snapshot_row["offline_deficit_snapshot"]

    normal_product = ctx["product"]
    second_payload = _phieu(product, so_luong=1)
    second_payload["items"].append(
        {
            "product_id": normal_product["id"],
            "product_name": normal_product["name"],
            "unit_price": normal_product["price"],
            "quantity": 1,
        }
    )
    second_payload["cash_tendered"] += normal_product["price"]
    second = _gui(client, ctx, second_payload)
    assert second.status_code == 200, second.text
    assert "TON_AM" in second.json()["issues"]

    stale_apply = client.post(
        f"/api/products/{ctx['shop_id']}/stocktake",
        json={
            "items": [
                {
                    "product_id": product["id"],
                    "batches": [],
                    "offline_deficit_snapshot": stale_token,
                }
            ]
        },
        headers=auth(ctx["token"]),
    )
    assert stale_apply.status_code == 409, stale_apply.text

    session = SessionLocal()
    try:
        assert session.get(models.Product, product["id"]).stock == -2
        assert sum(
            row.remaining_quantity
            for row in session.query(models.OfflineBatchStockDeficit)
            .filter(models.OfflineBatchStockDeficit.product_id == product["id"])
            .all()
        ) == 2
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    fresh_response = client.get(
        f"/api/products/{ctx['shop_id']}/stocktake/batches",
        headers=auth(ctx["token"]),
    )
    fresh_row = next(
        row
        for row in fresh_response.json()["products"]
        if row["product_id"] == product["id"]
    )
    assert fresh_row["offline_deficit_qty"] == 2
    reconciled = client.post(
        f"/api/products/{ctx['shop_id']}/stocktake",
        json={
            "items": [
                {
                    "product_id": product["id"],
                    "batches": [],
                    "offline_deficit_snapshot": fresh_row[
                        "offline_deficit_snapshot"
                    ],
                }
            ]
        },
        headers=auth(ctx["token"]),
    )
    assert reconciled.status_code == 200, reconciled.text

    session = SessionLocal()
    try:
        second_order = (
            session.query(models.Order)
            .filter(models.Order.offline_uuid == second_payload["offline_uuid"])
            .one()
        )
        second_line = (
            session.query(models.OrderItem)
            .filter(
                models.OrderItem.order_id == second_order.id,
                models.OrderItem.product_id == product["id"],
            )
            .one()
        )
        normal_line = (
            session.query(models.OrderItem)
            .filter(
                models.OrderItem.order_id == second_order.id,
                models.OrderItem.product_id == normal_product["id"],
            )
            .one()
        )
        assert (
            session.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == second_line.id)
            .count()
            == 0
        )
        closed = (
            session.query(models.OfflineBatchStockDeficit)
            .filter(models.OfflineBatchStockDeficit.product_id == product["id"])
            .order_by(models.OfflineBatchStockDeficit.id)
            .all()
        )
        assert all(
            row.remaining_quantity == 0
            and row.resolution_kind == "STOCKTAKE"
            and row.state_version == 1
            for row in closed
        )
        assert session.get(models.Product, product["id"]).stock == 0
        before_payments = (
            session.query(models.OrderPayment)
            .filter(models.OrderPayment.order_id == second_order.id)
            .count()
        )
        before_returns = session.query(models.OrderReturn).filter_by(
            order_id=second_order.id
        ).count()
        before_loyalty = session.query(models.LoyaltyPointEntry).filter_by(
            order_id=second_order.id
        ).count()
        before_return_logs = session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count()
        normal_stock_after_sale = session.get(
            models.Product, normal_product["id"]
        ).stock
        second_order_id = second_order.id
        second_line_id = second_line.id
        normal_line_id = normal_line.id
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    for restock in (False, True):
        failed_return = client.post(
            f"/api/orders/{second_order_id}/returns",
            json={
                "items": [
                    {
                        "order_item_id": second_line_id,
                        "quantity": 1,
                        "restock": restock,
                    }
                ],
                "method": "transfer",
                "reason": "Kiểm thử hoàn offline",
                "reference": "TEST-RETURN",
                "operation_id": "offline-missing-source-" + _uuid.uuid4().hex,
            },
            headers=auth(ctx["token"]),
        )
        assert failed_return.status_code == 409, failed_return.text

    failed_multiline_return = client.post(
        f"/api/orders/{second_order_id}/returns",
        json={
            "items": [
                {
                    "order_item_id": normal_line_id,
                    "quantity": 1,
                    "restock": False,
                },
                {
                    "order_item_id": second_line_id,
                    "quantity": 1,
                    "restock": False,
                },
            ],
            "method": "transfer",
            "reason": "Kiểm thử hoàn offline",
            "reference": "TEST-RETURN",
            "operation_id": "offline-multiline-missing-source-"
            + _uuid.uuid4().hex,
        },
        headers=auth(ctx["token"]),
    )
    assert failed_multiline_return.status_code == 409, failed_multiline_return.text

    session = SessionLocal()
    try:
        order = session.get(models.Order, second_order_id)
        order.status = "PENDING_PAYMENT"
        session.commit()
    finally:
        session.close()
    failed_cancel = client.post(
        f"/api/orders/{second_order_id}/cancel",
        headers=auth(ctx["token"]),
    )
    assert failed_cancel.status_code == 409, failed_cancel.text

    session = SessionLocal()
    try:
        order = session.get(models.Order, second_order_id)
        line = session.get(models.OrderItem, second_line_id)
        normal_line = session.get(models.OrderItem, normal_line_id)
        assert (
            order.status,
            order.inventory_reversed,
            line.inventory_reversed,
            line.returned_total_qty,
            line.cost_return_version,
        ) == ("PENDING_PAYMENT", 0, 0, 0, 0)
        assert (
            normal_line.returned_total_qty,
            normal_line.cost_return_version,
            normal_line.inventory_reversed,
        ) == (0, 0, 0)
        assert (
            session.query(models.OrderReturn)
            .filter(models.OrderReturn.order_id == second_order_id)
            .count()
            == 0
        )
        assert (
            session.query(models.OrderPayment)
            .filter(models.OrderPayment.order_id == second_order_id)
            .count()
            == before_payments
        )
        assert session.query(models.LoyaltyPointEntry).filter_by(
            order_id=second_order_id
        ).count() == before_loyalty
        assert session.query(models.SystemLog).filter_by(
            action="ORDER_RETURN"
        ).count() == before_return_logs
        assert session.get(models.Product, product["id"]).stock == 0
        assert session.get(
            models.Product, normal_product["id"]
        ).stock == normal_stock_after_sale
        assert session.query(models.OrderReturn).filter_by(
            order_id=second_order_id
        ).count() == before_returns
        closed = session.query(models.OfflineBatchStockDeficit).filter_by(
            order_item_id=second_line_id
        ).one()
        assert (
            closed.deficit_quantity,
            closed.remaining_quantity,
            closed.resolution_kind,
            closed.state_version,
        ) == (1, 0, "STOCKTAKE", 1)
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_du_ton_thi_khong_gan_co_ton_am(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    res = _gui(client, ctx, _phieu(ctx["product"], so_luong=4))
    assert "TON_AM" not in res.json()["issues"]
    assert _ton_kho(ctx["product"]["id"]) == 6


def test_don_ton_am_hien_o_danh_sach_can_xu_ly(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    _gui(client, ctx, _phieu(ctx["product"], so_luong=99, may="POS-KIOT-2"))

    res = client.get(
        f"/api/orders/{ctx['shop_id']}/offline-issues", headers=auth(ctx["token"])
    )
    assert res.status_code == 200, res.text
    ds = res.json()
    assert len(ds) == 1
    assert "TON_AM" in ds[0]["issues"]
    assert ds[0]["device"] == "POS-KIOT-2", "chủ shop cần biết máy nào bán"


def test_don_khong_van_de_khong_lam_ban_danh_sach_xu_ly(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    _gui(client, ctx, _phieu(ctx["product"], so_luong=1))
    res = client.get(
        f"/api/orders/{ctx['shop_id']}/offline-issues", headers=auth(ctx["token"])
    )
    assert res.json() == []


# ---------- Ca thu ngân theo giờ bán ----------
def test_don_gan_vao_ca_dang_mo_luc_ban(client):
    ctx = seller_with_shop(client)
    ca = _mo_ca(client, ctx)
    res = _gui(client, ctx, _phieu(ctx["product"], luc_ban=datetime.utcnow()))
    assert res.json()["shift_id"] == ca["id"]
    assert res.json()["issues"] == []


def test_ban_truoc_khi_mo_ca_thi_bao_khong_co_ca(client):
    """Bán lúc chưa ai mở ca: không được im lặng gán bừa vào ca hiện tại."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    hom_qua = datetime.utcnow() - timedelta(days=1)

    res = _gui(client, ctx, _phieu(ctx["product"], luc_ban=hom_qua))
    assert res.status_code == 200, res.text
    assert "KHONG_CO_CA" in res.json()["issues"]
    assert res.json()["shift_id"] is None


def test_tien_mat_vao_dung_ca_luc_ban(client):
    """Bút toán phải mang shift_id của ca lúc bán, vì `_expected_cash` của ca
    cộng theo cột đó."""
    ctx = seller_with_shop(client)
    ca = _mo_ca(client, ctx)
    _gui(client, ctx, _phieu(ctx["product"], so_luong=2, luc_ban=datetime.utcnow()))

    s = SessionLocal()
    try:
        bt = (
            s.query(models.OrderPayment)
            .filter(models.OrderPayment.entry_type == "SALE_CASH")
            .order_by(models.OrderPayment.id.desc())
            .first()
        )
    finally:
        s.close()
    assert bt.shift_id == ca["id"]
    assert bt.amount == 200000


def test_don_offline_lam_tang_tien_mat_du_kien_cua_ca(client):
    ctx = seller_with_shop(client)
    ca = _mo_ca(client, ctx, tien_dau=500000)
    _gui(client, ctx, _phieu(ctx["product"], so_luong=3, luc_ban=datetime.utcnow()))

    res = client.get(f"/api/shifts/{ca['id']}", headers=auth(ctx["token"]))
    assert res.status_code == 200, res.text
    assert res.json()["cash_payment_in_amount"] == 300000


# ---------- Chỉ tiền mặt, và phiếu phải hợp lệ ----------
def test_tien_khach_dua_it_hon_tong_don_thi_tu_choi(client):
    """Đây là phiếu SAI, không phải xung đột dữ liệu. Nhận vào là ghi một khoản
    thu không có thật."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    res = _gui(client, ctx, _phieu(ctx["product"], so_luong=2, tien_dua=150000))
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "OFFLINE_TENDER_TOO_LOW"
    assert "nhỏ hơn tổng đơn" in res.json()["detail"]["message"]


def test_gio_ban_o_tuong_lai_thi_tu_choi(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    mai = datetime.utcnow() + timedelta(days=1)
    res = _gui(client, ctx, _phieu(ctx["product"], luc_ban=mai))
    assert res.status_code == 400


def test_phieu_khong_co_mat_hang_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    phieu = _phieu(ctx["product"])
    phieu["items"] = []
    assert _gui(client, ctx, phieu).status_code == 422


def test_tien_thua_duoc_tinh_dung(client):
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    phieu = _phieu(ctx["product"], so_luong=1, tien_dua=200000)
    res = _gui(client, ctx, phieu)
    assert res.status_code == 200, res.text

    s = SessionLocal()
    try:
        don = s.query(models.Order).filter(models.Order.offline_uuid == phieu["offline_uuid"]).first()
        assert don.cash_change_amount == 100000
        assert don.cash_paid_amount == 100000
        assert don.status == "PAID"
        assert don.payment_method == "cash"
    finally:
        s.close()


# ---------- Cách ly giữa các shop ----------
def test_khong_ban_duoc_hang_cua_shop_khac(client):
    """Đoán product_id của shop khác phải trượt. Thiếu điều kiện shop_id trong
    câu truy vấn là bán được hàng của người ta (bẫy 22)."""
    a = seller_with_shop(client)
    b = seller_with_shop(client)
    _mo_ca(client, a)

    phieu = _phieu(b["product"])  # sản phẩm của shop B
    res = _gui(client, a, phieu)  # gửi vào shop A
    assert res.status_code == 200, res.text
    # Không tìm thấy trong shop A -> ghi nhận tiền nhưng gắn cờ, KHÔNG trừ kho shop B
    assert "SP_KHONG_CON" in res.json()["issues"]
    assert _ton_kho(b["product"]["id"]) == 10, "tồn kho shop B không được đụng tới"


def test_uuid_cua_shop_khac_thi_bao_xung_dot(client):
    a = seller_with_shop(client)
    b = seller_with_shop(client)
    _mo_ca(client, a)
    _mo_ca(client, b)

    phieu = _phieu(a["product"])
    assert _gui(client, a, phieu).status_code == 200
    conflict = _gui(client, b, phieu)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "OFFLINE_UUID_OTHER_SHOP"
    assert "order_id" not in conflict.text


def test_nguoi_ngoai_khong_gui_duoc_phieu(client):
    a = seller_with_shop(client)
    b = seller_with_shop(client)
    res = client.post(
        f"/api/orders/{a['shop_id']}/offline",
        json=_phieu(a["product"]),
        headers=auth(b["token"]),
    )
    assert res.status_code in (403, 404)


def test_chua_dang_nhap_thi_bi_chan(client):
    a = seller_with_shop(client)
    res = client.post(f"/api/orders/{a['shop_id']}/offline", json=_phieu(a["product"]))
    assert res.status_code == 401


# ---------- Sản phẩm bị xóa giữa lúc bán và lúc sync ----------
def test_san_pham_da_xoa_van_ghi_duoc_dong_tien(client):
    """Mất tên sản phẩm là khoản tiền trong két không còn tra được về đâu."""
    ctx = seller_with_shop(client)
    _mo_ca(client, ctx)
    sp = create_product(
        client, ctx["token"], ctx["shop_id"], "Hang sap xoa", 50000, 5, ctx["category_id"]
    )
    phieu = _phieu(sp, so_luong=2)

    res = client.delete(f"/api/products/{sp['id']}", headers=auth(ctx["token"]))
    assert res.status_code == 200, res.text

    res = _gui(client, ctx, phieu)
    assert res.status_code == 200, res.text
    assert "SP_KHONG_CON" in res.json()["issues"]
    assert res.json()["total"] == 100000

    s = SessionLocal()
    try:
        don = s.query(models.Order).filter(models.Order.offline_uuid == phieu["offline_uuid"]).first()
        dong = s.query(models.OrderItem).filter(models.OrderItem.order_id == don.id).all()
        assert len(dong) == 1
        assert dong[0].product_name == "Hang sap xoa", "tên đã chụp phải được giữ"
    finally:
        s.close()


# ---------- Nhân viên ----------
def test_nhan_vien_ban_hang_gui_duoc_phieu(client):
    ctx = seller_with_shop(client)
    _, token_nv = new_staff(client, ctx, staff_role="CASHIER")
    res = client.post(
        f"/api/orders/{ctx['shop_id']}/offline",
        json=_phieu(ctx["product"]),
        headers=auth(token_nv),
    )
    assert res.status_code == 200, res.text
