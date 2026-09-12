from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_checkout_retry_state_survives_reload_and_keeps_exact_create_payload():
    js = _read("static/js/pos.js")

    assert "POS_CHECKOUT_STORAGE_PREFIX" in js
    assert "sessionStorage.setItem(key, JSON.stringify(value))" in js
    assert "function taoTrangThaiCheckout(body, phase = 'creating')" in js
    assert "taoTrangThaiCheckout(body, 'offline_pending')" in js
    assert "state.create_payload" in js
    assert "checkoutOperationId = state.operation_id" in js
    assert "state.phase = 'cash_pending'" in js
    assert "state.phase = 'transfer_pending'" in js
    assert "phucHoiCheckoutDangDo()" in js
    assert "if (!activeShift)" in js[js.index(
        "if (checkoutOperationId)"
    ):js.index("if (currentOrderId)")]
    assert "pendingCheckoutState && !coQuyenShopDangCho" in js


def test_cash_payment_requires_confirmation_when_server_total_changes():
    js = _read("static/js/pos.js")

    assert "async function xacNhanTongTienServer(state)" in js
    assert "'Tổng tiền trên server đã thay đổi'" in js
    assert "state.server_total_confirmed = false" in js
    assert "if (!await xacNhanTongTienServer(state)) return false;" in js
    assert "await apiCall(`/orders/${idDon}/pay`" in js


def test_pending_order_locks_payment_method_without_hiding_qr():
    js = _read("static/js/pos.js")
    set_method = js[js.index("function setMethod(m)"):js.index(
        "function apDungPhuongThucThanhToan"
    )]

    assert "if (currentOrderId || pendingCashOrderId)" in set_method
    assert "apDungPhuongThucThanhToan" not in set_method.split("return showToast", 1)[0]
    assert "qrSection" not in set_method


def test_pending_transfer_locks_cart_voucher_and_customer_mutations():
    js = _read("static/js/pos.js")
    lock_helper = js[js.index("function dangKhoaChinhSuaDon()"):js.index(
        "function addToCart"
    )]

    assert "currentOrderId || pendingCashOrderId" in lock_helper
    assert "checkoutOperationId" in lock_helper

    mutating_handlers = [
        "function addToCart",
        "function updateQty",
        "function removeItem",
        "async function applyVoucher",
        "function boChonKhach",
        "function chonKhach",
        "async function timKhachPOS",
        "function hienFormKhachMoi",
        "async function taoKhachPOS",
    ]
    for index, handler in enumerate(mutating_handlers):
        start = js.index(handler)
        end = (
            js.index(mutating_handlers[index + 1], start)
            if index + 1 < len(mutating_handlers)
            else start + 500
        )
        assert "dangKhoaChinhSuaDon()" in js[start:end], handler


def test_cash_movement_retry_keeps_operation_and_payload_until_known_result():
    js = _read("static/js/pos.js")

    assert "POS_MOVEMENT_STORAGE_PREFIX" in js
    assert "submitted: true" in js
    assert "payload," in js
    assert "apiCall(`/shifts/${state.shift_id}/movements`, 'POST', payload)" in js
    assert "luuMovementDangDo(state)" in js
    assert "if (laLoi4xx(e))" in js
    assert "xoaMovementDangDo(state)" in js
    assert "['movementAmountInput', capNhatMovementDraft]" in js
    assert "addEventListener('input', capNhatMovementDraft)" in js
