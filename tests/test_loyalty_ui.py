"""Chốt các dây an toàn giao diện của chương trình tích điểm.

Đây là test hồi quy mã nguồn; quy trình vẫn bắt buộc mở trình duyệt thật vì
JavaScript có thể đúng chuỗi nhưng hỏng lúc chạy.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _function(js: str, start: str, end: str) -> str:
    return js[js.index(start):js.index(end, js.index(start))]


def test_tab_tich_diem_co_day_du_form_va_chi_hien_cho_chu_shop():
    html = _read("static/seller.html")
    js = _read("static/js/seller.js")

    for element_id in (
        "tabLoyalty",
        "loyaltyEnabled",
        "loyaltyEarnAmount",
        "loyaltyEarnPoints",
        "loyaltyRedeemPoints",
        "loyaltyRedeemAmount",
        "loyaltyMinRedeem",
        "loyaltyMaxPercent",
        "loyaltyExpiryDays",
        "btnSaveLoyalty",
    ):
        assert f'id="{element_id}"' in html
    assert "tabLoyalty && MY_ROLE === 'SELLER'" in js
    assert "if (MY_ROLE !== 'SELLER')" in js


def test_luu_cau_hinh_khoa_form_va_chan_response_cu_ghi_de():
    js = _read("static/js/seller.js")
    save = _function(js, "async function saveLoyaltyProgram()", "[\n    'loyaltyEnabled'")

    assert "const requestId = ++loyaltyRequestId" in save
    assert "loyaltySaveBusy = true" in save
    assert "khoaFormLoyalty(true)" in save
    assert "generation !== currentShopGeneration" in save
    assert "requestId !== loyaltyRequestId" in save
    assert "loyaltySaveBusy = false" in save
    assert "khoaFormLoyalty(false)" in save
    assert "if (loyaltySaveBusy) return" in js


def test_doi_ngon_ngu_khong_ghi_de_cau_hinh_dang_go():
    js = _read("static/js/seller.js")
    locale = _function(
        js,
        "function capNhatSellerTheoNgonNgu()",
        "document.addEventListener('fselling:localechange'",
    )

    assert "capNhatLoyaltyPreview()" in locale
    assert "renderLoyaltyProgram(" not in locale


def test_pos_luu_nguyen_payload_diem_de_thu_lai_dung_mot_don():
    js = _read("static/js/pos.js")
    create_state = _function(
        js, "function taoTrangThaiCheckout(body, phase = 'creating')", "function phucHoiCheckoutDangDo()"
    )
    send = _function(
        js, "async function guiYeuCauTaoDonDangDo(state)", "async function thuTaoDonDangDo(state)"
    )

    assert "create_payload: JSON.parse(JSON.stringify(body))" in create_state
    assert "state.create_payload" in send
    assert "state.create_payload =" not in send


def test_pos_diem_bi_cap_thi_dong_bo_o_nhap_va_khong_ban_offline():
    js = _read("static/js/pos.js")
    local_apply = _function(
        js, "function apDungKetQuaDiemTaiMay(result)", "function tinhLaiDiemDaApDung()"
    )
    offline = _function(js, "async function luuBanOffline(state)", "async function thuTaoDonDangDo(state)")

    assert "loyaltyPointsRequested = result.applied" in local_apply
    assert "loyaltyPointsApplied = result.applied" in local_apply
    assert "input.value = result.applied" in local_apply
    assert "payload.loyalty_points_to_use" in offline
    assert "throw new Error(dich(khoaThongBaoRetryUuDai(payload)))" in offline


def test_pos_mat_mang_cho_bo_diem_co_xac_nhan_nhung_khong_tu_ban():
    html = _read("static/pos.html")
    js = _read("static/js/pos.js")
    render = _function(
        js,
        "function capNhatLuaChonBoUuDaiOffline()",
        "async function boUuDaiVaTiepTucBanOffline()",
    )
    action = _function(
        js,
        "async function boUuDaiVaTiepTucBanOffline()",
        "function applyLoyaltyPoints()",
    )

    assert 'id="loyaltyOfflineChoice"' in html
    assert 'id="btnRemoveLoyaltyOffline"' in html
    assert 'onclick="boUuDaiVaTiepTucBanOffline()"' in html
    assert "window.OfflineBan?.dangOffline()" in render
    assert "chiTiet.coVoucher || chiTiet.coDiem" in render
    assert "requestTaoDonCoTheDaRoiMay()" in render
    assert "const dongY = await xacNhan(" in action
    assert "oldTotal: dinhDangTien(snapshot.total)" in action
    assert "newTotal: dinhDangTien(snapshot.subtotal)" in action
    assert "if (!dongY) return" in action
    assert "xoaDiemDaApDung({ clearInput: true, clearMessage: true })" in action
    assert "currentVoucher = null" in action
    assert "apDungPhuongThucThanhToan('cash', true)" in action
    assert "checkout()" not in action
    assert "luuBanOffline(" not in action
    assert "state.create_payload" not in action
    assert "capNhatLuaChonBoDiemOffline" not in js


def test_pos_khong_cho_bo_diem_sau_khi_request_co_the_da_roi_may():
    js = _read("static/js/pos.js")
    guard_helper = _function(
        js,
        "function requestTaoDonCoTheDaRoiMay()",
        "function khoaThongBaoRetryUuDai(",
    )
    action = _function(
        js,
        "async function boUuDaiVaTiepTucBanOffline()",
        "function applyLoyaltyPoints()",
    )
    guard = action[:action.index("const chiTiet")]
    second_guard = action[action.index("if (!dongY) return"):action.index("xoaDiemDaApDung")]

    for marker in (
        "checkoutBusy",
        "checkoutOperationId",
        "currentOrderId",
        "pendingCashOrderId",
        "pendingCheckoutState?.phase === 'creating'",
    ):
        assert marker in guard_helper
    assert "requestTaoDonCoTheDaRoiMay()" in guard
    assert "requestTaoDonCoTheDaRoiMay()" in second_guard
    assert "state.create_payload =" not in js


def test_voucher_cung_bi_chan_khoi_phieu_offline_va_giu_exact_payload_retry():
    js = _read("static/js/pos.js")
    offline = _function(
        js, "async function luuBanOffline(state)", "async function thuTaoDonDangDo(state)"
    )
    retry = _function(
        js, "async function thuTaoDonDangDo(state)", "async function checkout()"
    )
    checkout = _function(js, "async function checkout()", "function startPaymentPolling()")

    assert "Boolean(payload.voucher_code)" in offline
    assert "khoaThongBaoRetryUuDai(payload)" in offline
    assert "const coVoucher = Boolean(state.create_payload?.voucher_code)" in retry
    assert "const coUuDaiOnline = coVoucher || coDungDiem" in retry
    assert "!state.create_payload?.voucher_code" in retry
    assert "currentVoucher || loyaltyPointsApplied > 0" in checkout
    assert "pos.online_discount.action_required" in checkout


def test_don_giam_con_0_dong_khong_hien_qr_va_file_tinh_da_bump_cache():
    pos_js = _read("static/js/pos.js")
    zero_total = _function(
        pos_js,
        "if (res.status === 'PAID')",
        "if (state.payment_method === 'transfer')",
    )
    pos_html = _read("static/pos.html")
    seller_html = _read("static/seller.html")

    assert "zero_total_done" in zero_total
    assert "await hienHoaDon(currentOrderId)" in zero_total
    assert "qrSection" not in zero_total
    assert "/js/pos.js?v=20260912-r5-10" in pos_html
    assert "/js/locales/pos.js?v=20260912-r5-9" in pos_html
    # R1 Forecast prefill sửa cả hai file này nên chúng cùng sang mốc mới.
    assert "/js/seller.js?v=20260911-r5-5" in seller_html
    assert "/js/locales/seller.js?v=20260911-r5-5" in seller_html


def test_khoa_i18n_quan_trong_co_du_tieng_viet_va_tieng_anh():
    pos_locale = _read("static/js/locales/pos.js")
    seller_locale = _read("static/js/locales/seller.js")

    for key in (
        "pos.loyalty.online_required",
        "pos.loyalty.network_retry",
        "pos.online_discount.remove_points",
        "pos.online_discount.remove_voucher",
        "pos.online_discount.remove_both",
        "pos.online_discount.confirm_body",
        "pos.online_discount.removed",
        "pos.checkout.zero_total_done",
    ):
        assert pos_locale.count(f"'{key}'") == 2, key
    for key in (
        "seller.loyalty.title",
        "seller.loyalty.saved",
        "seller.loyalty.owner_only",
        "seller.loyalty.expiry_invalid",
    ):
        assert seller_locale.count(f"'{key}'") == 2, key
