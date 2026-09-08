"""F2: trả hàng - khách mang hàng đã mua quay lại, shop hoàn tiền.

Ba việc rất dễ nhầm với nhau, bộ test này phải giữ được ranh giới:

- Hủy đơn: đơn CHƯA thanh toán, hàng chưa ra khỏi cửa.
- Hoàn khoản chuyển thừa (`refund-complete`): trả lại tiền dư, hàng vẫn của khách.
- Trả hàng: hàng quay về, tiền đi ra, xảy ra được NHIỀU LẦN trên một đơn.
"""
import uuid

import pytest

from conftest import _unique, auth, new_seller, new_staff, seller_with_shop

from fselling import models
from fselling.core.database import SessionLocal


def _op() -> str:
    return uuid.uuid4().hex


def _sp(product_id):
    session = SessionLocal()
    try:
        return (
            session.query(models.Product)
            .filter(models.Product.id == product_id)
            .first()
        )
    finally:
        session.close()


def _tao_sp(client, ctx, gia_ban, ton, gia_von=None, ten=None):
    form = {
        "name": ten or _unique("SP"),
        "price": gia_ban,
        "stock": ton,
        "category_id": ctx["category_id"],
    }
    if gia_von is not None:
        form["cost_price"] = gia_von
    res = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data=form,
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _ban(client, ctx, dong, method="cash", voucher=None, token=None):
    """`dong` là list (san_pham, so_luong). Trả về order_id của đơn ĐÃ thanh toán."""
    tok = token or ctx["token"]
    body = {
        "items": [
            {"product_id": sp["id"], "price": sp["price"], "quantity": qty}
            for sp, qty in dong
        ],
        "payment_method": method,
    }
    if voucher:
        body["voucher_code"] = voucher
    res = client.post(
        f"/api/orders/{ctx['shop_id']}", json=body, headers=auth(tok)
    )
    assert res.status_code == 200, res.text
    order_id = res.json()["order_id"]
    tra = client.post(f"/api/orders/{order_id}/pay", headers=auth(tok))
    assert tra.status_code == 200, tra.text
    return order_id


def _chi_tiet(client, ctx, order_id, token=None):
    res = client.get(
        f"/api/orders/{order_id}/detail", headers=auth(token or ctx["token"])
    )
    assert res.status_code == 200, res.text
    return res.json()


def _dong_don(client, ctx, order_id, product_id):
    for d in _chi_tiet(client, ctx, order_id)["items"]:
        if d["product_id"] == product_id:
            return d
    raise AssertionError("Không tìm thấy dòng đơn")


def _mo_ca(client, ctx, token=None, tien_dau_ca=500000):
    res = client.post(
        f"/api/shifts/{ctx['shop_id']}/open",
        json={"opening_cash_amount": tien_dau_ca},
        headers=auth(token or ctx["token"]),
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _tra(client, ctx, order_id, items, method="transfer", token=None, **kw):
    """Mặc định hoàn bằng CHUYỂN KHOẢN: hoàn tiền mặt bắt buộc phải có ca đang
    mở (tiền ra khỏi két phải thuộc về một ca), nên các test không nhắm vào
    chuyện két thì dùng chuyển khoản cho khỏi lệ thuộc."""
    explicit_items = [{**item, "restock": item.get("restock", True)} for item in items]
    body = {
        "items": explicit_items,
        "method": method,
        "reason": "Khách trả hàng",
        "operation_id": _op(),
    }
    if method == "transfer":
        body["reference"] = "TEST-RETURN"
    body.update(kw)
    return client.post(
        f"/api/orders/{order_id}/returns",
        json=body,
        headers=auth(token or ctx["token"]),
    )


# ---------- Luồng cơ bản ----------
def test_tra_mot_mon_hoan_dung_tien_va_nhap_lai_kho(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 3)])
    assert _sp(sp["id"]).stock == 7

    dong = _dong_don(client, ctx, order_id, sp["id"])
    res = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["return"]["refund_amount"] == 50000
    assert body["returned_total"] == 50000
    assert _sp(sp["id"]).stock == 8, "Hàng trả về phải cộng lại tồn kho"


def test_product_id_shop_khac_409_truoc_tien_diem_provenance_va_ton_kho(client):
    ctx_a = seller_with_shop(client)
    ctx_b = seller_with_shop(client)
    sp_a = _tao_sp(client, ctx_a, gia_ban=50_000, ton=10, gia_von=30_000)
    order_id = _ban(client, ctx_a, [(sp_a, 1)])
    dong = _dong_don(client, ctx_a, order_id, sp_a["id"])
    stock_a_before = _sp(sp_a["id"]).stock
    stock_b_before = _sp(ctx_b["product"]["id"]).stock

    with SessionLocal() as session:
        item = session.get(models.OrderItem, dong["id"])
        original_product_id = item.product_id
        item.product_id = ctx_b["product"]["id"]
        session.commit()
        before = {
            "returns": session.query(models.OrderReturn).filter_by(order_id=order_id).count(),
            "payments": session.query(models.OrderPayment).filter_by(order_id=order_id).count(),
            "loyalty": session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count(),
            "logs": session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count(),
            "return_items": session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
                models.OrderReturn.order_id == order_id
            ).count(),
        }

    try:
        response = _tra(
            client,
            ctx_a,
            order_id,
            [{"order_item_id": dong["id"], "quantity": 1, "restock": True}],
        )
        assert response.status_code == 409, response.text
        assert _sp(sp_a["id"]).stock == stock_a_before
        assert _sp(ctx_b["product"]["id"]).stock == stock_b_before
        with SessionLocal() as session:
            item = session.get(models.OrderItem, dong["id"])
            assert (item.returned_total_qty, item.cost_return_version) == (0, 0)
            assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"]
            assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"]
            assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
            assert session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
                models.OrderReturn.order_id == order_id
            ).count() == before["return_items"]

        operation_id = _op()
        body = {
            "operation_id": operation_id,
            "items": [{"order_item_id": dong["id"], "quantity": 1, "restock": False}],
            "method": "transfer",
            "reason": "Hàng hỏng",
            "reference": "TEST-RETURN",
        }
        accepted = client.post(
            f"/api/orders/{order_id}/returns",
            headers=auth(ctx_a["token"]),
            json=body,
        )
        retry = client.post(
            f"/api/orders/{order_id}/returns",
            headers=auth(ctx_a["token"]),
            json=body,
        )
        assert accepted.status_code == retry.status_code == 200
        assert accepted.json()["return"]["id"] == retry.json()["return"]["id"]
        assert accepted.json()["return"]["refund_amount"] == 50_000
        assert _sp(sp_a["id"]).stock == stock_a_before
        assert _sp(ctx_b["product"]["id"]).stock == stock_b_before
        with SessionLocal() as session:
            item = session.get(models.OrderItem, dong["id"])
            returned = session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
                models.OrderReturn.order_id == order_id
            ).one()
            assert returned.product_id is None
            assert returned.restocked == 0
            assert (item.returned_total_qty, item.cost_return_version) == (1, 1)
            assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"] + 1
            assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"] + 1
            assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
            assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"] + 1
            assert session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
                models.OrderReturn.order_id == order_id
            ).count() == before["return_items"] + 1
    finally:
        with SessionLocal() as session:
            item = session.get(models.OrderItem, dong["id"])
            item.product_id = original_product_id
            session.commit()


def test_product_bi_xoa_chi_chan_restock_con_hang_hong_van_hoan_tien(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=41_000, ton=5, gia_von=23_000)
    order_id = _ban(client, ctx, [(sp, 1)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    with SessionLocal() as session:
        session.query(models.Product).filter(models.Product.id == sp["id"]).delete(
            synchronize_session=False
        )
        session.commit()
        assert session.get(models.Product, sp["id"]) is None
        before = {
            "returns": session.query(models.OrderReturn).filter_by(order_id=order_id).count(),
            "payments": session.query(models.OrderPayment).filter_by(order_id=order_id).count(),
            "logs": session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count(),
        }

    blocked = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1, "restock": True}],
    )
    assert blocked.status_code == 409, blocked.text

    operation_id = _op()
    body = {
        "operation_id": operation_id,
        "items": [{"order_item_id": dong["id"], "quantity": 1, "restock": False}],
        "method": "transfer",
        "reason": "Hàng hỏng",
        "reference": "TEST-RETURN",
    }
    accepted = client.post(
        f"/api/orders/{order_id}/returns", headers=auth(ctx["token"]), json=body
    )
    retry = client.post(
        f"/api/orders/{order_id}/returns", headers=auth(ctx["token"]), json=body
    )
    assert accepted.status_code == retry.status_code == 200
    assert accepted.json()["return"]["id"] == retry.json()["return"]["id"]
    assert accepted.json()["return"]["refund_amount"] == 41_000

    with SessionLocal() as session:
        item = session.get(models.OrderItem, dong["id"])
        returned = session.query(models.OrderReturnItem).join(models.OrderReturn).filter(
            models.OrderReturn.order_id == order_id
        ).one()
        payment = session.query(models.OrderPayment).filter_by(
            order_id=order_id, entry_type="RETURN_TRANSFER"
        ).one()
        assert session.get(models.Product, sp["id"]) is None
        assert returned.product_id is None and returned.restocked == 0
        assert returned.refund_amount == payment.amount == 41_000
        assert (item.returned_total_qty, item.cost_return_version) == (1, 1)
        assert item.returned_refund_vnd == 41_000
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
        assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"] + 1
        # Giữ DB test liên-file không có FK treo sau khi đã chứng minh luồng
        # hard-delete; sự kiện trả đã lưu product_id=NULL độc lập với cleanup này.
        item.product_id = None
        session.commit()


def test_preflight_nhieu_dong_mot_restock_cross_shop_rollback_toan_bo(client):
    ctx_a = seller_with_shop(client)
    ctx_b = seller_with_shop(client)
    sp_no_restock = _tao_sp(client, ctx_a, gia_ban=31_000, ton=5, gia_von=17_000)
    sp_restock = _tao_sp(client, ctx_a, gia_ban=29_000, ton=5, gia_von=13_000)
    order_id = _ban(client, ctx_a, [(sp_no_restock, 1), (sp_restock, 1)])
    line_no_restock = _dong_don(client, ctx_a, order_id, sp_no_restock["id"])
    line_restock = _dong_don(client, ctx_a, order_id, sp_restock["id"])
    stocks_before = {
        sp_no_restock["id"]: _sp(sp_no_restock["id"]).stock,
        sp_restock["id"]: _sp(sp_restock["id"]).stock,
        ctx_b["product"]["id"]: _sp(ctx_b["product"]["id"]).stock,
    }

    with SessionLocal() as session:
        corrupted = session.get(models.OrderItem, line_restock["id"])
        original_product_id = corrupted.product_id
        corrupted.product_id = ctx_b["product"]["id"]
        session.commit()
        before = {
            "returns": session.query(models.OrderReturn).filter_by(order_id=order_id).count(),
            "payments": session.query(models.OrderPayment).filter_by(order_id=order_id).count(),
            "loyalty": session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count(),
            "logs": session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count(),
        }

    try:
        response = _tra(
            client,
            ctx_a,
            order_id,
            [
                {"order_item_id": line_no_restock["id"], "quantity": 1, "restock": False},
                {"order_item_id": line_restock["id"], "quantity": 1, "restock": True},
            ],
        )
        assert response.status_code == 409, response.text
        assert _sp(sp_no_restock["id"]).stock == stocks_before[sp_no_restock["id"]]
        assert _sp(sp_restock["id"]).stock == stocks_before[sp_restock["id"]]
        assert _sp(ctx_b["product"]["id"]).stock == stocks_before[ctx_b["product"]["id"]]
        with SessionLocal() as session:
            first = session.get(models.OrderItem, line_no_restock["id"])
            second = session.get(models.OrderItem, line_restock["id"])
            assert (first.returned_total_qty, first.cost_return_version) == (0, 0)
            assert (second.returned_total_qty, second.cost_return_version) == (0, 0)
            assert session.query(models.OrderReturn).filter_by(order_id=order_id).count() == before["returns"]
            assert session.query(models.OrderPayment).filter_by(order_id=order_id).count() == before["payments"]
            assert session.query(models.LoyaltyPointEntry).filter_by(order_id=order_id).count() == before["loyalty"]
            assert session.query(models.SystemLog).filter_by(action="ORDER_RETURN").count() == before["logs"]
    finally:
        with SessionLocal() as session:
            session.get(models.OrderItem, line_restock["id"]).product_id = original_product_id
            session.commit()


def test_don_van_giu_trang_thai_paid(client):
    """Hóa đơn đã xuất là sự thật lịch sử. Việc trả nằm ở bảng riêng."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 2}])
    assert _chi_tiet(client, ctx, order_id)["status"] == "PAID"


def test_tra_nhieu_lan_tren_cung_mot_don(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=20000, ton=10, gia_von=12000)
    order_id = _ban(client, ctx, [(sp, 5)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    assert _tra(
        client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 2}]
    ).status_code == 200
    lan_hai = _tra(
        client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}]
    )
    assert lan_hai.status_code == 200, lan_hai.text
    assert lan_hai.json()["returned_total"] == 60000

    chi_tiet = _dong_don(client, ctx, order_id, sp["id"])
    assert chi_tiet["returned_quantity"] == 3
    assert chi_tiet["returnable_quantity"] == 2
    assert _sp(sp["id"]).stock == 8, "5 bán đi, 3 trả về"


def test_khong_cho_tra_qua_so_da_ban(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=20000, ton=10, gia_von=12000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 3}])
    assert res.status_code == 400
    assert _sp(sp["id"]).stock == 8, "Phiếu bị từ chối thì tồn kho không đổi"


def test_tong_cac_lan_tra_khong_vuot_so_da_ban(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=20000, ton=10, gia_von=12000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 2}])
    res = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    assert res.status_code == 400, "Đã trả hết rồi thì không trả thêm được nữa"


def test_dong_hang_cua_don_khac_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=20000, ton=20, gia_von=12000)
    don_a = _ban(client, ctx, [(sp, 1)])
    don_b = _ban(client, ctx, [(sp, 1)])
    dong_b = _dong_don(client, ctx, don_b, sp["id"])

    res = _tra(client, ctx, don_a, [{"order_item_id": dong_b["id"], "quantity": 1}])
    assert res.status_code == 400


# ---------- Nhập lại kho hay không ----------
def test_hang_hong_khong_nhap_lai_kho_nhung_van_hoan_tien(client):
    """Sữa hết hạn, áo bẩn: vẫn trả tiền khách nhưng KHÔNG cho lên kệ lại."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])
    assert _sp(sp["id"]).stock == 8

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1, "restock": False}],
        reason="Hàng hỏng",
    )
    assert res.status_code == 200, res.text
    assert res.json()["return"]["refund_amount"] == 50000
    assert _sp(sp["id"]).stock == 8, "Hàng hỏng không được quay lại tồn bán được"
    assert res.json()["return"]["items"][0]["restocked"] is False


def test_moi_dong_tu_quyet_dinh_nhap_lai_kho(client):
    ctx = seller_with_shop(client)
    tot = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    hong = _tao_sp(client, ctx, gia_ban=20000, ton=10, gia_von=12000)
    order_id = _ban(client, ctx, [(tot, 2), (hong, 2)])
    dong_tot = _dong_don(client, ctx, order_id, tot["id"])
    dong_hong = _dong_don(client, ctx, order_id, hong["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [
            {"order_item_id": dong_tot["id"], "quantity": 1, "restock": True},
            {"order_item_id": dong_hong["id"], "quantity": 1, "restock": False},
        ],
    )
    assert res.status_code == 200, res.text
    assert _sp(tot["id"]).stock == 9
    assert _sp(hong["id"]).stock == 8


def test_nhap_lai_kho_khong_lam_lech_gia_von_binh_quan(client):
    """Hàng ra đi với đúng giá vốn đã chốt nên khi quay về, bình quân tự khớp."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 4)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 4}])
    assert _sp(sp["id"]).stock == 10
    assert _sp(sp["id"]).cost_price == 30000


# ---------- Giảm giá voucher ----------
def test_tra_mot_phan_don_co_voucher_hoan_theo_ty_trong(client):
    """Đơn 100k giảm 10% còn 90k, trả 1 trong 2 món thì hoàn 45k chứ không 50k -
    hoàn theo giá niêm yết là shop chịu trọn phần đã giảm cho món khách giữ."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    res = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={"code": "GIAM10", "discount_type": "percentage", "discount_value": 10},
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 200, res.text
    order_id = _ban(client, ctx, [(sp, 2)], voucher="GIAM10")
    assert _chi_tiet(client, ctx, order_id)["total_amount"] == 90000

    dong = _dong_don(client, ctx, order_id, sp["id"])
    tra = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    assert tra.status_code == 200, tra.text
    assert tra.json()["return"]["refund_amount"] == 45000


def test_tra_het_don_co_voucher_hoan_dung_so_khach_da_tra(client):
    """Trả hết thì khách phải nhận lại đúng số đã trả, không thiếu một đồng vì
    làm tròn từng dòng."""
    ctx = seller_with_shop(client)
    a = _tao_sp(client, ctx, gia_ban=33333, ton=10, gia_von=20000)
    b = _tao_sp(client, ctx, gia_ban=16667, ton=10, gia_von=10000)
    client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={"code": "GIAM7", "discount_type": "percentage", "discount_value": 7},
        headers=auth(ctx["token"]),
    )
    order_id = _ban(client, ctx, [(a, 3), (b, 3)], voucher="GIAM7")
    tong = _chi_tiet(client, ctx, order_id)["total_amount"]

    dong_a = _dong_don(client, ctx, order_id, a["id"])
    dong_b = _dong_don(client, ctx, order_id, b["id"])
    tra = _tra(
        client,
        ctx,
        order_id,
        [
            {"order_item_id": dong_a["id"], "quantity": 3},
            {"order_item_id": dong_b["id"], "quantity": 3},
        ],
    )
    assert tra.status_code == 200, tra.text
    assert tra.json()["return"]["refund_amount"] == tong


def test_khong_bao_gio_hoan_qua_so_khach_da_tra(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={"code": "GIAM50", "discount_type": "percentage", "discount_value": 50},
        headers=auth(ctx["token"]),
    )
    order_id = _ban(client, ctx, [(sp, 4)], voucher="GIAM50")
    tong = _chi_tiet(client, ctx, order_id)["total_amount"]
    dong = _dong_don(client, ctx, order_id, sp["id"])

    da_hoan = 0
    for _ in range(4):
        res = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
        assert res.status_code == 200, res.text
        da_hoan = res.json()["returned_total"]
    assert da_hoan == tong


def test_hoan_tien_theo_phan_bo_dong_bat_bien_khong_am(client):
    """Các lần trước có thể đã dùng hết tiền hoàn dù vẫn còn hàng để trả."""
    ctx = seller_with_shop(client)
    products = [
        _tao_sp(client, ctx, gia_ban=1, ton=2, ten=_unique(f"SP le {i}"))
        for i in range(5)
    ]
    voucher = client.post(
        "/api/vouchers",
        params={"shop_id": ctx["shop_id"]},
        json={
            "code": "GIAM2D",
            "discount_type": "flat",
            "discount_value": 2,
            "min_order_value": 0,
        },
        headers=auth(ctx["token"]),
    )
    assert voucher.status_code == 200, voucher.text
    order_id = _ban(
        client,
        ctx,
        [(product, 1) for product in products],
        voucher="GIAM2D",
    )
    detail = _chi_tiet(client, ctx, order_id)
    assert detail["subtotal"] == 5
    assert detail["total_amount"] == 3
    line_ids = [row["id"] for row in detail["items"]]

    # Voucher 2đ chia cho năm dòng bằng largest remainder: hai immutable id
    # đầu nhận mỗi dòng 1đ giảm, nên net/refund lần lượt là 0, 0, 1, 1, 1.
    for line_id, expected in zip(line_ids[:3], (0, 0, 1)):
        returned = _tra(
            client,
            ctx,
            order_id,
            [{"order_item_id": line_id, "quantity": 1}],
        )
        assert returned.status_code == 200, returned.text
        assert returned.json()["return"]["refund_amount"] == expected

    final = _tra(
        client,
        ctx,
        order_id,
        [
            {"order_item_id": line_ids[3], "quantity": 1},
            {"order_item_id": line_ids[4], "quantity": 1},
        ],
    )
    assert final.status_code == 200, final.text
    assert final.json()["return"]["refund_amount"] == 2

    session = SessionLocal()
    try:
        rows = (
            session.query(models.OrderReturnItem)
            .filter(
                models.OrderReturnItem.return_id
                == final.json()["return"]["id"]
            )
            .all()
        )
        assert len(rows) == 2
        assert all(int(row.refund_amount) >= 0 for row in rows)
        assert sum(int(row.refund_amount) for row in rows) == 2
    finally:
        session.close()


# ---------- Chỉ đơn đã thanh toán ----------
def test_don_chua_thanh_toan_khong_tra_hang_duoc(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    res = client.post(
        f"/api/orders/{ctx['shop_id']}",
        json={
            "items": [{"product_id": sp["id"], "price": sp["price"], "quantity": 1}],
            "payment_method": "transfer",
        },
        headers=auth(ctx["token"]),
    )
    order_id = res.json()["order_id"]
    dong = _dong_don(client, ctx, order_id, sp["id"])

    tra = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    assert tra.status_code == 409, "Đơn chưa thanh toán thì phải HỦY, không phải trả"


def test_don_da_huy_khong_tra_hang_duoc(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    res = client.post(
        f"/api/orders/{ctx['shop_id']}",
        json={
            "items": [{"product_id": sp["id"], "price": sp["price"], "quantity": 1}],
            "payment_method": "transfer",
        },
        headers=auth(ctx["token"]),
    )
    order_id = res.json()["order_id"]
    dong = _dong_don(client, ctx, order_id, sp["id"])
    client.post(f"/api/orders/{order_id}/cancel", headers=auth(ctx["token"]))

    tra = _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    assert tra.status_code == 409


# ---------- Chống bấm lặp ----------
def test_bam_hai_lan_cung_ma_thao_tac_chi_tao_mot_phieu(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 3)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    ma = _op()
    body = {
        "items": [{"order_item_id": dong["id"], "quantity": 1, "restock": True}],
        "method": "transfer",
        "reason": "Khách trả hàng",
        "reference": "TEST-RETURN",
        "operation_id": ma,
    }
    lan_mot = client.post(
        f"/api/orders/{order_id}/returns", json=body, headers=auth(ctx["token"])
    )
    lan_hai = client.post(
        f"/api/orders/{order_id}/returns", json=body, headers=auth(ctx["token"])
    )
    assert lan_mot.status_code == 200, lan_mot.text
    assert lan_hai.status_code == 200, lan_hai.text
    assert lan_hai.json()["return"]["id"] == lan_mot.json()["return"]["id"]
    assert lan_hai.json()["returned_total"] == 50000
    assert _sp(sp["id"]).stock == 8, "Retry KHÔNG được cộng kho lần thứ hai"


def test_cung_ma_nhung_doi_noi_dung_tra_hang_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 3)])
    dong = _dong_don(client, ctx, order_id, sp["id"])
    ma = _op()

    first = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [
                {
                    "order_item_id": dong["id"],
                    "quantity": 1,
                    "restock": True,
                }
            ],
            "method": "transfer",
            "reason": "Khách đổi ý",
            "reference": "TEST-RETURN",
            "operation_id": ma,
        },
        headers=auth(ctx["token"]),
    )
    assert first.status_code == 200, first.text

    changed = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [
                {
                    "order_item_id": dong["id"],
                    "quantity": 2,
                    "restock": False,
                }
            ],
            "method": "cash",
            "reason": "Lý do khác",
            "operation_id": ma,
        },
        headers=auth(ctx["token"]),
    )
    assert changed.status_code == 409, changed.text
    assert _dong_don(client, ctx, order_id, sp["id"])["returned_quantity"] == 1


def test_cung_ma_thao_tac_cho_don_khac_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=20, gia_von=30000)
    don_a = _ban(client, ctx, [(sp, 1)])
    don_b = _ban(client, ctx, [(sp, 1)])
    dong_a = _dong_don(client, ctx, don_a, sp["id"])
    dong_b = _dong_don(client, ctx, don_b, sp["id"])

    ma = _op()
    client.post(
        f"/api/orders/{don_a}/returns",
        json={
            "items": [{"order_item_id": dong_a["id"], "quantity": 1, "restock": True}],
            "method": "transfer",
            "reason": "Khách trả hàng",
            "reference": "TEST-RETURN",
            "operation_id": ma,
        },
        headers=auth(ctx["token"]),
    )
    res = client.post(
        f"/api/orders/{don_b}/returns",
        json={
            "items": [{"order_item_id": dong_b["id"], "quantity": 1, "restock": True}],
            "method": "transfer",
            "reason": "Khách trả hàng",
            "reference": "TEST-RETURN",
            "operation_id": ma,
        },
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 409


def test_ma_thao_tac_qua_ngan_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 1)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [{"order_item_id": dong["id"], "quantity": 1}],
            "method": "cash",
            "operation_id": "abc",
        },
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 422


# ---------- Ca thu ngân ----------
def test_hoan_tien_mat_tru_dung_ket_cua_ca(client):
    """Tiền ra khỏi két phải hiện ra ở phần tiền mặt kỳ vọng của đúng ca đó."""
    ctx = seller_with_shop(client)
    _, thu_ngan = new_staff(client, ctx, staff_role="CASHIER")
    shift_id = _mo_ca(client, ctx, token=thu_ngan)

    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)], token=thu_ngan)
    ky_vong_truoc = client.get(
        f"/api/shifts/{shift_id}", headers=auth(thu_ngan)
    ).json()["expected_cash_amount"]

    dong = _dong_don(client, ctx, order_id, sp["id"])
    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="cash",
        token=thu_ngan,
    )
    assert res.status_code == 200, res.text

    ky_vong_sau = client.get(
        f"/api/shifts/{shift_id}", headers=auth(thu_ngan)
    ).json()["expected_cash_amount"]
    assert ky_vong_sau == ky_vong_truoc - 50000
    assert res.json()["return"]["shift_id"] == shift_id


def test_hoan_chuyen_khoan_khong_dung_toi_ket(client):
    ctx = seller_with_shop(client)
    _, thu_ngan = new_staff(client, ctx, staff_role="CASHIER")
    shift_id = _mo_ca(client, ctx, token=thu_ngan)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)], token=thu_ngan)
    truoc = client.get(f"/api/shifts/{shift_id}", headers=auth(thu_ngan)).json()[
        "expected_cash_amount"
    ]

    dong = _dong_don(client, ctx, order_id, sp["id"])
    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="transfer",
        reference="FT123456",
        token=ctx["token"],
    )
    assert res.status_code == 200, res.text

    sau = client.get(f"/api/shifts/{shift_id}", headers=auth(thu_ngan)).json()[
        "expected_cash_amount"
    ]
    assert sau == truoc, "Hoàn bằng chuyển khoản không rút tiền khỏi két"


def test_chu_shop_cung_phai_mo_ca_moi_hoan_tien_mat_duoc(client):
    """Không có ngoại lệ cho chủ shop: tiền ra khỏi két mà không thuộc ca nào
    thì cuối ngày đếm thiếu và không lần ra được vì sao."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="cash",
    )
    assert res.status_code == 409
    assert _sp(sp["id"]).stock == 8

    _mo_ca(client, ctx)
    lai = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="cash",
    )
    assert lai.status_code == 200, lai.text
    assert _sp(sp["id"]).stock == 9


def test_thu_ngan_chua_mo_ca_khong_hoan_tien_mat_duoc(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    _, thu_ngan = new_staff(client, ctx, staff_role="CASHIER")
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="cash",
        token=thu_ngan,
    )
    assert res.status_code == 409
    assert _sp(sp["id"]).stock == 8, "Không mở ca thì không có gì được ghi"


# ---------- Phân quyền ----------
def test_thu_ngan_nhan_tra_hang_duoc(client):
    ctx = seller_with_shop(client)
    _, thu_ngan = new_staff(client, ctx, staff_role="CASHIER")
    _mo_ca(client, ctx, token=thu_ngan)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)], token=thu_ngan)
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        method="cash",
        token=thu_ngan,
    )
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("staff_role", ["SERVICE", "WAREHOUSE", "KITCHEN", "BAR"])
def test_role_khong_co_permission_nhan_tra_hang_bi_tu_choi(client, staff_role):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    _, token = new_staff(client, ctx, staff_role=staff_role)
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        token=token,
    )
    assert res.status_code == 403


def test_shop_khac_khong_tra_hang_ho_duoc(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])
    _, nguoi_khac = new_seller(client)

    res = _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        token=nguoi_khac,
    )
    assert res.status_code == 403


# ---------- Báo cáo ----------
def _stats(client, ctx):
    res = client.get(
        f"/api/shops/{ctx['shop_id']}/stats", headers=auth(ctx["token"])
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_bao_cao_tru_tien_hoan_va_lai_bi_giam(client):
    """Bán 2 món giá 50k vốn 30k: lãi 40k. Trả 1 món có nhập lại kho: lãi còn 20k."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    assert _stats(client, ctx)["gross_profit"] == 40000

    dong = _dong_don(client, ctx, order_id, sp["id"])
    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])

    stats = _stats(client, ctx)
    assert stats["total_revenue"] == 100000, "Doanh thu bán ra giữ nguyên nghĩa cũ"
    assert stats["returned_amount"] == 50000
    assert stats["net_revenue"] == 50000
    assert stats["gross_profit"] == 20000, "Hoàn 50k nhưng thu lại vốn 30k"


def test_hang_khong_nhap_lai_kho_mat_trang_ca_von(client):
    """Cùng một lần trả nhưng hàng bỏ đi: lãi giảm bằng TOÀN BỘ tiền hoàn."""
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1, "restock": False}],
    )
    stats = _stats(client, ctx)
    assert stats["gross_profit"] == -10000, (
        "Lãi 40k trừ trọn 50k tiền hoàn: hàng bỏ đi thì vốn không thu lại được"
    )


def test_phieu_tra_thieu_gia_von_bi_dem_rieng(client):
    ctx = seller_with_shop(client)
    order_id = _ban(client, ctx, [(ctx["product"], 2)])  # SP chưa khai giá vốn
    dong = _dong_don(client, ctx, order_id, ctx["product"]["id"])

    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])
    stats = _stats(client, ctx)
    assert stats["returns_missing_cost"] == 1
    assert stats["returned_amount"] == 100000, "Tiền hoàn vẫn phải hiện ra"


def test_nhan_vien_manager_khong_thay_lai_nhung_van_thay_tien_hoan(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])
    _tra(client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 1}])

    _, quan_ly = new_staff(client, ctx, staff_role="MANAGER")
    body = client.get(
        f"/api/shops/{ctx['shop_id']}/stats", headers=auth(quan_ly)
    ).json()
    assert body["returned_amount"] == 50000
    assert "gross_profit" not in body


# ---------- Chi tiết đơn ----------
def test_chi_tiet_don_hien_lich_su_tra(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 3)])
    dong = _dong_don(client, ctx, order_id, sp["id"])
    _tra(
        client,
        ctx,
        order_id,
        [{"order_item_id": dong["id"], "quantity": 1}],
        reason="Không vừa",
    )

    chi_tiet = _chi_tiet(client, ctx, order_id)
    assert chi_tiet["returned_total"] == 50000
    assert len(chi_tiet["returns"]) == 1
    phieu = chi_tiet["returns"][0]
    assert phieu["reason"] == "Không vừa"
    assert phieu["items"][0]["quantity"] == 1
    assert chi_tiet["items"][0]["returnable_quantity"] == 2


def test_don_chua_tra_thi_lich_su_rong(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])

    chi_tiet = _chi_tiet(client, ctx, order_id)
    assert chi_tiet["returns"] == []
    assert chi_tiet["returned_total"] == 0
    assert chi_tiet["items"][0]["returned_quantity"] == 0


# ---------- Kiểm tra đầu vào ----------
def test_phieu_rong_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 1)])

    res = _tra(client, ctx, order_id, [])
    assert res.status_code == 400


def test_so_luong_tra_khong_duong_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 2)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    assert _tra(
        client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": 0}]
    ).status_code == 400
    assert _tra(
        client, ctx, order_id, [{"order_item_id": dong["id"], "quantity": -1}]
    ).status_code == 400


def test_mot_dong_khai_hai_lan_trong_cung_phieu_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 3)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = _tra(
        client,
        ctx,
        order_id,
        [
            {"order_item_id": dong["id"], "quantity": 1},
            {"order_item_id": dong["id"], "quantity": 1},
        ],
    )
    assert res.status_code == 400


def test_thieu_cach_hoan_tien_bi_tu_choi(client):
    ctx = seller_with_shop(client)
    sp = _tao_sp(client, ctx, gia_ban=50000, ton=10, gia_von=30000)
    order_id = _ban(client, ctx, [(sp, 1)])
    dong = _dong_don(client, ctx, order_id, sp["id"])

    res = client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [{"order_item_id": dong["id"], "quantity": 1, "restock": True}],
            "reason": "Khách trả hàng",
            "operation_id": _op(),
        },
        headers=auth(ctx["token"]),
    )
    assert res.status_code == 400
