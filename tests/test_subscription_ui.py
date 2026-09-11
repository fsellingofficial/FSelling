from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_overpaid_la_checkout_da_dong_va_khong_khoa_mua_vinh_vien():
    source = _read("static/js/subscriptions.js")
    terminal_set = source.split(
        "const TERMINAL_CHECKOUT_STATUSES = new Set([", 1
    )[1].split("]);", 1)[0]

    assert "'OVERPAID'" in terminal_set
    assert "'OVERPAID'" not in source.split(
        "const POLLED_CHECKOUT_STATUSES = new Set([", 1
    )[1].split("]);", 1)[0]


def test_checkout_da_dong_khong_bao_chu_shop_tai_lai_qr():
    source = _read("static/js/subscriptions.js")

    assert "qrIsIntentionallyClosed" in source
    assert "? info.message" in source
    assert ": translate('subscription.checkout.no_qr')" in source


def test_retry_tao_checkout_chi_nhan_dung_operation_id_cua_lan_bam():
    source = _read("static/js/subscriptions.js")

    assert "function checkoutMatchesAttempt(checkout, attempt)" in source
    assert "String(checkout.operation_id || '') === String(attempt.operation_id)" in source
    assert "if (checkoutMatchesAttempt(sellerState.data?.current_checkout, attempt))" in source
    assert "const pendingAttempt = sellerState.checkoutAttempt;" in source
    assert "sellerState.checkoutAttempt.cycle !== cycle" in source
    assert "if (sellerState.data?.current_checkout) sellerState.checkoutAttempt = null" not in source


def test_toast_kich_hoat_chi_hien_khi_dung_checkout_da_nhan_tien():
    source = _read("static/js/subscriptions.js")

    assert "function checkoutJustActivated(previousCheckout, currentCheckout)" in source
    assert "Number(previousCheckout.id) !== Number(currentCheckout.id)" in source
    assert "!!currentCheckout.activated_at" in source
    assert "currentInfo.plan === 'PRO' && currentInfo.status === 'PAID'" not in source


def test_ngay_qua_tang_hien_dung_ngay_admin_da_chon():
    source = _read("static/js/subscriptions.js")
    locale = _read("static/js/locales/subscriptions.js")

    assert "data?.active_grant_expires_on" in source
    assert "dateOnly(activeGrantExpiresOn(data))" in source
    assert "subscription.status.gift_until_end" in source
    assert "subscription.admin.current_gift_expiry" in source
    assert "'subscription.status.gift_until_end':" in locale
    assert "'subscription.admin.current_gift_expiry':" in locale


def test_admin_thay_dung_so_tien_can_hoan_khi_chuyen_thua():
    source = _read("static/js/subscriptions.js")
    locale = _read("static/js/locales/subscriptions.js")

    assert "payment.checkout_refund_due_vnd" in source
    assert "subscription.admin.problem_overpaid_refund" in source
    assert (
        "'subscription.admin.problem_overpaid_refund': "
        "'Chuyển thừa — cần hoàn {{amount}}'"
    ) in locale


def test_asset_goi_cuoc_duoc_bump_o_ca_hai_trang():
    seller_html = _read("static/seller.html")
    admin_html = _read("static/admin.html")

    for html in (seller_html, admin_html):
        assert "/css/subscriptions.css?v=20260807-goi-cuoc-f3" in html
        assert "/js/locales/subscriptions.js?v=20260807-goi-cuoc-f3" in html
        assert "/js/subscriptions.js?v=20260807-goi-cuoc-f3" in html

    # seller.js sang mốc R1 Forecast prefill; ba asset gói cước ở trên KHÔNG đổi nên
    # vẫn giữ nguyên mốc cũ - bump bừa cả cụm là bắt người dùng tải lại vô ích.
    assert "/js/seller.js?v=20260911-r5-5" in seller_html
