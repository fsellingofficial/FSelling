"""Dây an toàn mã nguồn cho tab Nhập hàng và công nợ nhà cung cấp.

Các test này không thay cho bước mở trình duyệt thật. Chúng khóa những lỗi khó
nhìn nhưng có thể làm lệch tiền/kho: gọi helper không tồn tại, trộn dữ liệu hai
cửa hàng, hoặc tạo operation_id mới khi người dùng bấm thử lại sau lỗi mạng.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _function(js: str, start: str, end: str) -> str:
    begin = js.index(start)
    return js[begin:js.index(end, begin)]


def _without_line_comments(code: str) -> str:
    return "\n".join(line.split("//")[0] for line in code.splitlines())


def _declared_functions(*relative_paths: str) -> set[str]:
    names: set[str] = set()
    for relative_path in relative_paths:
        source = _read(relative_path)
        names |= set(
            re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", source)
        )
        names |= set(
            re.findall(
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
                source,
            )
        )
        names |= set(re.findall(r"global\.([A-Za-z_$][\w$]*)\s*=", source))
    return names


def test_tab_nhap_hang_co_du_ba_man_va_cac_hop_thoai_tien_quan_trong():
    html = _read("static/seller.html")

    for element_id in (
        "tabPurchasing",
        "purchasing",
        "purchaseSubTabReceipts",
        "purchaseSubTabOrders",
        "purchaseSubTabSuppliers",
        "purchaseOrdersPanel",
        "purchaseOrderEditor",
        "purchaseOrderLines",
        "purchaseOrdersList",
        "purchaseReceiptEditor",
        "purchaseReceiptLines",
        "purchaseReceiptsList",
        "purchaseSuppliersList",
        "supplierFormModal",
        "supplierHistoryModal",
        "purchaseConfirmModal",
        "supplierPaymentModal",
        "purchaseReceiptDetailModal",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert 'data-main-tab="purchasing"' in html
    assert "switchPurchasingSubTab('receipts')" in html
    assert "switchPurchasingSubTab('orders')" in html
    assert "switchPurchasingSubTab('suppliers')" in html


def test_cac_nut_icon_don_dat_hang_co_ten_cho_cong_cu_ho_tro():
    source = _read("static/js/purchasing.js")
    actions = (
        "editPurchaseOrder",
        "placePurchaseOrder",
        "deletePurchaseOrder",
        "receivePurchaseOrder",
        "cancelPurchaseOrder",
        "openPurchaseReceiptDetail",
        "removePurchaseOrderLine",
    )

    for action in actions:
        button = re.search(
            rf'<button[^>]+onclick="{action}\([^>]+>',
            source,
        )
        assert button, f"missing {action} button"
        assert "aria-label=" in button.group(0), f"{action} has no accessible name"


def test_du_bao_chi_prefill_don_dat_hang_va_khong_tu_tao_don():
    html = _read("static/seller.html")
    seller_js = _read("static/js/seller.js")
    purchasing_js = _read("static/js/purchasing.js")

    assert 'data-i18n="seller.forecast.purchase_action"' in html
    render = _function(seller_js, "function veDuBaoNhapHang(d)", "// ===== F5:")
    bridge = _function(
        seller_js,
        "async function lapPhieuNhapTuDuBao(",
        "// ===== F5:",
    )
    assert "MY_ROLE === 'SELLER'" in render
    assert "r.can_nhap > 0" in render
    assert "r.nha_cung_cap" in render
    assert "duBaoCache.shopId !== currentShopId" in bridge
    assert "Number(r.nha_cung_cap?.id) === idNcc" in bridge
    assert "Number(r.can_nhap) > 0" in bridge
    assert "prefillPurchaseOrderFromForecast" in bridge

    prefill = _function(
        purchasing_js,
        "async function prefillPurchaseOrderFromForecast(",
        "function openPurchaseOrderForm()",
    )
    assert "stillCurrent(shopId, generation)" in prefill
    assert "supplier.is_active !== false" in prefill
    assert "productOptions()" in prefill
    assert "$('purchaseOrderSupplierSelect').value = String(supplierId)" in prefill
    assert "state.orderLines" in prefill
    assert "state.nextOrderLineKey = lines.length + 1" in prefill
    assert "apiCall(" not in prefill
    assert "operationId(" not in prefill
    assert "savePurchaseOrderDraft(" not in prefill


def test_forecast_prefill_khong_de_du_lieu_cu_ghi_de_editor_dang_mo():
    js = _read("static/js/purchasing.js")
    prefill = _function(
        js,
        "async function prefillPurchaseOrderFromForecast(",
        "function openPurchaseOrderForm()",
    )

    assert "purchaseOrderEditor" in prefill
    assert "style.display === 'block'" in prefill
    assert "pendingOrderCreate" in prefill
    assert "blockForOtherPendingOperation('order_create')" in prefill
    assert "MAX_PURCHASE_QUANTITY" in prefill


def test_nhan_hang_tu_don_chi_prefill_receipt_editor_khong_tu_ghi_kho():
    js = _read("static/js/purchasing.js")
    receive = _function(
        js,
        "async function receivePurchaseOrder(",
        "function openPurchaseReceiptForm()",
    )
    assert "order.status !== 'ORDERED'" in receive
    assert "state.receiptPurchaseOrderId = Number(order.id)" in receive
    assert "state.receiptLines" in receive
    assert "unit_cost: ''" in receive
    assert "expiry_date: ''" in receive
    assert "savePurchaseReceiptDraft(" not in receive
    assert "/confirm" not in receive
    build = _function(js, "function buildReceiptPayload(", "async function sendNewReceipt(")
    assert "purchase_order_id: state.receiptPurchaseOrderId" in build


def test_tab_chi_hien_cho_chu_shop_admin_va_nap_lai_khi_mo_tab():
    html = _read("static/seller.html")
    seller_js = _read("static/js/seller.js")
    purchasing_js = _read("static/js/purchasing.js")

    assert 'id="tabPurchasing"' in html and "display:none" in html[
        html.index('id="tabPurchasing"'):html.index('id="tabPurchasing"') + 240
    ]
    assert "new Set(['SELLER', 'ADMIN'])" in purchasing_js
    assert "return PURCHASING_UI_ROLES.has(MY_ROLE)" in purchasing_js
    assert "if (tabId === 'purchasing' && !['SELLER', 'ADMIN'].includes(MY_ROLE))" in seller_js
    assert "tabPurchasing && ['SELLER', 'ADMIN'].includes(MY_ROLE)" in seller_js
    assert "if (tabId === 'purchasing') window.FSellingPurchasing?.load?.()" in seller_js

    # ADMIN chỉ có lát cắt Nhập hàng, không vô tình được cả trang vận hành/POS.
    admin_slice = _function(seller_js, "if (MY_ROLE === 'ADMIN') {", "if (MY_ROLE !== 'STAFF')")
    assert "button.dataset.mainTab === 'purchasing'" in admin_slice
    assert "section.id === 'purchasing'" in admin_slice
    assert "btnOpenPos.style.display = 'none'" in admin_slice
    assert "switchTab('purchasing', tabPurchasing)" in admin_slice
    assert 'id="btnBackAdmin"' in html
    assert "btnBackAdmin && MY_ROLE === 'ADMIN'" in seller_js


def test_module_nap_sau_seller_va_moi_file_dung_dung_phien_ban():
    """Bẫy 16: sửa file trong static/ mà quên bump ?v= là người dùng chạy code
    cũ trong im lặng, không lỗi gì cả.

    Phiên bản được ghim CỨNG ở đây có chủ ý: sửa file thì phải sửa cả dòng này,
    tức là phải nghĩ về chuyện cache một lần nữa. Mỗi file mang mốc của lần cuối
    NÓ bị sửa, không phải mốc của lần phát hành gần nhất: L5 (Giọng nói) đụng
    vào cả seller.js lẫn locales/seller.js nên hai file cùng đi tiếp, còn
    seller.css, expenses.js và purchasing.js không đổi nên đứng yên. Bump bừa cả
    cụm là bắt người dùng tải lại những file không hề đổi.
    """
    html = _read("static/seller.html")
    purchasing_version = "20260822-order-actions-a11y-r2"
    seller_css_version = "20260911-r5-5"
    cashflow_version = "20260808-dong-tien-k1"
    locale_version = "20260911-r5-5"
    seller_version = "20260911-r5-5"

    expected = (
        f"/css/seller.css?v={seller_css_version}",
        f"/js/locales/seller.js?v={locale_version}",
        f"/js/seller.js?v={seller_version}",
        f"/js/purchasing.js?v={purchasing_version}",
        f"/js/expenses.js?v={cashflow_version}",
    )
    for path in expected:
        assert path in html, path

    # Hai module đều dùng apiCall/showToast/định dạng tiền của seller.js nên
    # phải nạp SAU nó, nếu không sẽ gọi hàm chưa tồn tại.
    vi_tri_seller = html.index(f"/js/seller.js?v={seller_version}")
    assert vi_tri_seller < html.index(f"/js/purchasing.js?v={purchasing_version}")
    assert vi_tri_seller < html.index(f"/js/expenses.js?v={cashflow_version}")


def test_moi_helper_dinh_dang_cua_module_deu_co_that_trong_trang_seller():
    """Bắt lại lỗi từng xảy ra: gọi dinhDangTien() vốn chỉ có ở pos.js."""
    source = _without_line_comments(_read("static/js/purchasing.js"))
    declared = _declared_functions(
        "static/js/purchasing.js",
        "static/js/seller.js",
        "static/js/api.js",
        "static/js/i18n.js",
    )
    called = set(re.findall(r"\b((?:escapeHtml|dinhDang)[A-Za-z]*)\s*\(", source))
    missing = sorted(called - declared)

    assert not missing, f"purchasing.js gọi helper không có trong trang seller: {missing}"
    assert "dinhDangTien(" not in source


def test_doi_cua_hang_huy_response_cu_va_xoa_cache_module():
    purchasing_js = _read("static/js/purchasing.js")
    seller_js = _read("static/js/seller.js")
    suppliers = _function(
        purchasing_js, "async function loadSuppliers()", "async function loadPurchaseReceipts()"
    )
    receipts = _function(
        purchasing_js, "async function loadPurchaseReceipts()", "function load()"
    )

    for loader in (suppliers, receipts):
        assert "const generation = selectedGeneration()" in loader
        assert "stillCurrent(shopId, generation)" in loader
        assert "requestId" in loader
    assert "window.FSellingPurchasing?.resetForShopChange?.()" in seller_js
    assert "state.supplierRequestId += 1" in purchasing_js
    assert "state.receiptRequestId += 1" in purchasing_js


def test_danh_sach_quan_ly_co_ncc_ngung_nhung_phieu_moi_chi_chon_ncc_hoat_dong():
    js = _read("static/js/purchasing.js")
    load = _function(js, "async function loadSuppliers()", "async function loadPurchaseReceipts()")
    select = _function(js, "function fillSupplierSelect()", "function clearSupplierForm()")

    assert "include_inactive=true" in load
    assert "supplier.is_active !== false" in select


def test_nut_them_nha_cung_cap_khong_bi_hieu_nham_la_sua_id_0():
    html = _read("static/seller.html")
    js = _read("static/js/purchasing.js")
    open_form = _function(js, "function openSupplierForm(", "function closeSupplierForm(")

    assert 'onclick="openSupplierForm()"' in html
    assert "id === null || id === undefined || id === ''" in open_form
    assert "Number.isInteger(parsedId) && parsedId > 0" in open_form
    assert "Number.isInteger(Number(id)) ? Number(id) : null" not in open_form
    assert "state.editingSupplierId =" in open_form


def test_phieu_nhap_chan_so_le_va_san_pham_theo_lo_thieu_han_dung():
    js = _read("static/js/purchasing.js")
    build = _function(js, "function buildReceiptPayload(", "async function sendNewReceipt(")

    assert "Number.isInteger(quantity)" in build
    assert "Number.isInteger(unitCost)" in build
    assert "quantity <= 0" in build
    assert "unitCost < 0" in build
    assert "line.track_batches" in build
    assert "expiry_required" in build
    for field in (
        "supplier_id",
        "supplier_invoice_number",
        "received_date",
        "due_date",
        "note",
        "items",
        "operation_id",
    ):
        assert field in build


def test_cho_phep_nhap_no_dau_ky_va_phieu_lich_su_da_qua_han():
    """Hạn cũ có thể trước ngày ghi nhận; đây không phải dữ liệu sai."""
    js = _read("static/js/purchasing.js")
    supplier = _function(js, "function newSupplierPayload()", "async function sendNewSupplier(")
    receipt = _function(js, "function buildReceiptPayload(", "async function sendNewReceipt(")

    # Vẫn bắt ngày ghi nhận/ngày nhận hàng đúng định dạng trước khi gửi.
    assert r"/^\d{4}-\d{2}-\d{2}$/" in supplier
    assert r"/^\d{4}-\d{2}-\d{2}$/" in receipt

    # Không tự cấm chứng từ cũ chỉ vì hạn thanh toán đã nằm trong quá khứ.
    assert "dueDate < openingDate" not in supplier
    assert "dueDate < receivedDate" not in receipt
    assert "opening_due_date:" in supplier
    assert "due_date:" in receipt


def test_tao_ncc_va_phieu_moi_thu_lai_dung_payload_cu_khi_chua_ro_ket_qua():
    js = _read("static/js/purchasing.js")

    assert "persistPendingOperation('supplier_create', pending)" in js
    assert "persistPendingOperation('receipt_create', pending)" in js
    assert "lines: exactCopy(state.receiptLines)" in js
    assert "if (state.pendingSupplierCreate)" in js
    assert "sendNewSupplier(state.pendingSupplierCreate)" in js
    assert "if (state.pendingReceiptCreate) return sendNewReceipt(state.pendingReceiptCreate)" in js
    assert "state.pendingSupplierCreate = pending" in js
    assert "state.pendingReceiptCreate = pending" in js
    assert "error.status >= 500" in js
    assert "retry_before_close" in js
    assert "setGlobalShopLocked(" in js


def test_bon_thao_tac_luu_session_truoc_khi_goi_api_va_chi_xoa_khi_da_ro():
    js = _read("static/js/purchasing.js")
    sends = (
        ("supplier_create", "async function sendNewSupplier(", "async function saveSupplier()"),
        ("receipt_create", "async function sendNewReceipt(", "async function savePurchaseReceiptDraft()"),
        ("receipt_confirm", "async function sendReceiptConfirm(", "async function confirmPurchaseReceipt()"),
        ("supplier_payment", "async function sendSupplierPayment(", "async function submitSupplierPayment()"),
    )

    for kind, start, end in sends:
        send = _function(js, start, end)
        persist = f"persistPendingOperation('{kind}', pending)"
        clear = f"clearPendingOperation('{kind}', pending)"
        assert persist in send, kind
        assert send.index(persist) < send.index("await apiCall"), kind
        # Một lần cho 2xx và ít nhất một lần cho nhánh 4xx rõ ràng.
        assert send.count(clear) >= 2, kind
        unknown_start = send.index("if (unknownOutcome(error))")
        unknown_end = send.index("} else", unknown_start)
        assert clear not in send[unknown_start:unknown_end], kind


def test_reload_phuc_hoi_dung_actor_shop_entity_payload_va_operation_id():
    js = _read("static/js/purchasing.js")

    assert "fselling.purchasing.pending.v1" in js
    assert "localStorage.getItem('username')" in js
    assert "saved.username !== username" in js
    assert "saved.pending?.actor_username !== username" in js
    assert "pending.actor_username !== username" in js
    assert "sessionStorage.setItem(" in js
    assert "sessionStorage.getItem(" in js
    assert "sessionStorage.removeItem(" in js
    assert "currentShopId = restoredPendingShopId" in js
    assert "localStorage.setItem('currentShopId', String(restoredPendingShopId))" in js
    assert "switchTab('purchasing', $('tabPurchasing'))" in js
    assert "restorePendingUi(false)" in js

    # Snapshot chỉ để vẽ lại; request retry vẫn lấy nguyên pending.payload.
    assert "lines: exactCopy(state.receiptLines)" in js
    assert "receipt: exactCopy(receipt)" in js
    assert "supplier: exactCopy(supplier)" in js
    assert "/purchase-receipts/receipt/${pending.receiptId}/confirm" in js
    assert "/suppliers/member/${pending.supplierId}/payments" in js
    assert js.count("pending.payload") >= 4


def test_reload_hien_canh_bao_khoa_form_va_nut_thu_lai_ro_rang():
    html = _read("static/seller.html")
    js = _read("static/js/purchasing.js")

    assert 'id="supplierCreateRetryNotice"' in html
    for restore in (
        "restoreSupplierCreateUi",
        "restoreReceiptCreateUi",
        "restoreReceiptConfirmUi",
        "restoreSupplierPaymentUi",
    ):
        assert f"function {restore}(" in js
    assert "lockSupplierForm(true, true)" in js
    assert "lockReceiptEditor(true, true)" in js
    assert "lockConfirmModal(true, true)" in js
    assert "lockPaymentModal(true, true)" in js
    assert js.count("'common.retry'") >= 4
    assert "blockForOtherPendingOperation" in js
    assert "mainTabButton.dataset.mainTab === 'purchasing'" in js
    assert "event.stopImmediatePropagation()" in js


def test_xac_nhan_phieu_gui_dung_contract_va_khong_cho_tra_qua_tong():
    js = _read("static/js/purchasing.js")
    build = _function(js, "function buildConfirmPayload()", "async function sendReceiptConfirm(")
    submit = _function(
        js, "async function confirmPurchaseReceipt()", "function updateSupplierPaymentPreview()"
    )

    assert "paid > total" in build
    assert "Number.isInteger(paid)" in build
    assert "availablePaymentMethods().includes(method)" in build
    assert "method === 'OUTSIDE' && !note" in build
    for field in (
        "operation_id",
        "draft_fingerprint",
        "paid_amount",
        "method",
        "note",
        "reference",
    ):
        assert field in build
    assert "receipt.draft_fingerprint" in build
    for old_field in (
        "payment_method",
        "payment_note",
        "payment_reference",
    ):
        # Không bắt nhầm khóa bản dịch như `seller.purchasing.payment_method_required`.
        assert f"{old_field}:" not in build
    assert "sendReceiptConfirm(state.pendingReceiptConfirm)" in submit
    assert "payload: exactCopy(payload)" in submit
    send = _function(js, "async function sendReceiptConfirm(", "async function confirmPurchaseReceipt()")
    assert "error?.status === 409" in send
    assert "closePurchaseConfirmModal(true)" in send
    assert "await loadPurchaseReceipts()" in send


def test_phieu_nhap_noi_ro_don_gia_la_gia_cuoi_cung_da_gom_chi_phi():
    html = _read("static/seller.html")
    locales = _read("static/js/locales/seller.js")

    assert 'data-i18n="seller.purchasing.landed_unit_cost"' in html
    assert "Đơn giá nhập cuối cùng (đã gồm mọi phí/giảm giá)" in html
    assert (
        "'seller.purchasing.landed_unit_cost': "
        "'Đơn giá nhập cuối cùng (đã gồm mọi phí/giảm giá)'"
    ) in locales
    assert (
        "'seller.purchasing.landed_unit_cost': "
        "'Final landed unit cost (includes all fees/discounts)'"
    ) in locales
    assert "phải gồm mọi phí, thuế và giảm giá" in locales
    assert "must include all fees, taxes, and discounts" in locales


def test_tra_no_chan_vuot_no_va_retry_dung_operation_cu():
    js = _read("static/js/purchasing.js")
    build = _function(
        js, "function buildSupplierPaymentPayload()", "async function sendSupplierPayment("
    )
    submit = _function(js, "async function submitSupplierPayment()", "function renderReceiptDetail(")

    assert "amount > balance" in build
    assert "Number.isInteger(amount)" in build
    assert "availablePaymentMethods().includes(method)" in build
    assert "method === 'OUTSIDE' && !note" in build
    for field in ("amount", "method", "note", "reference", "operation_id"):
        assert field in build
    assert "sendSupplierPayment(state.pendingSupplierPayment)" in submit
    assert "payload: exactCopy(payload)" in submit
    assert "/suppliers/member/${pending.supplierId}/payments" in js


def test_khi_chua_ro_ket_qua_thi_khoa_form_khoa_doi_shop_va_khoa_dong_modal():
    js = _read("static/js/purchasing.js")

    for pending in (
        "pendingSupplierCreate",
        "pendingReceiptCreate",
        "pendingReceiptConfirm",
        "pendingSupplierPayment",
    ):
        assert f"state.{pending}" in js
    assert "hasUnknownOperation()" in js
    assert "setGlobalShopLocked(" in js
    assert "lockReceiptEditor(true, true)" in js
    assert "lockConfirmModal(true, true)" in js
    assert "lockPaymentModal(true, true)" in js
    assert js.count("retry_before_close") >= 4


def test_admin_khong_thay_tien_mat_trong_ket_nhung_chu_shop_van_thay():
    html = _read("static/seller.html")
    js = _read("static/js/purchasing.js")
    visibility = _function(
        js,
        "function applyPaymentMethodRoleVisibility()",
        "function selectedShopId()",
    )
    available = _function(
        js,
        "function availablePaymentMethods()",
        "const state =",
    )

    # Ba chỗ cho chọn "tiền mặt trong két": trả nợ NCC, chốt phiếu nhập, và
    # (K1) ghi chi phí vận hành. Mỗi chỗ mới phải MANG dấu này VÀ tự thực thi
    # luật ẩn, chứ không dựa vào module khác đã chạy hay chưa - phần dưới kiểm
    # đúng điều đó cho expenses.js.
    assert html.count("data-owner-cash") == 3
    assert "MY_ROLE === 'ADMIN'" in available
    assert "ADMIN_PURCHASE_PAYMENT_METHODS" in available
    assert "ALL_PURCHASE_PAYMENT_METHODS" in available
    assert "option.hidden = hideCash" in visibility
    assert "option.disabled = hideCash" in visibility
    assert js.count("applyPaymentMethodRoleVisibility()") >= 3

    # K1: màn chi phí vận hành cũng lấy được tiền từ két nên phải theo cùng luật.
    expenses = _read("static/js/expenses.js")
    expense_visibility = _function(
        expenses,
        "function applyPaymentMethodRoleVisibility()",
        "function selectedShopId()",
    )
    assert "MY_ROLE === 'ADMIN'" in expense_visibility
    assert "option.hidden = hideCash" in expense_visibility
    assert "option.disabled = hideCash" in expense_visibility
    # Ẩn ở giao diện chưa đủ: người dùng vẫn sửa được DOM, nên lúc gửi phải
    # kiểm lại phương thức có nằm trong danh sách được phép không.
    assert "availableMethods().includes(method)" in expenses


def test_giao_dien_chan_tran_so_luong_tien_va_tong_nhieu_dong():
    html = _read("static/seller.html")
    js = _read("static/js/purchasing.js")
    locales = _read("static/js/locales/seller.js")
    supplier = _function(js, "function newSupplierPayload()", "async function sendNewSupplier(")
    receipt = _function(js, "function buildReceiptPayload(", "async function sendNewReceipt(")
    confirm = _function(js, "function buildConfirmPayload()", "async function sendReceiptConfirm(")
    payment = _function(
        js, "function buildSupplierPaymentPayload()", "async function sendSupplierPayment("
    )
    safe_total = _function(js, "function safePurchaseLineTotal(", "function operationId(")

    assert "const MAX_PURCHASE_QUANTITY = 1_000_000_000" in js
    assert "const MAX_PURCHASE_VND = 9_000_000_000_000_000" in js
    assert "Math.floor(MAX_PURCHASE_VND / unitCost)" in safe_total
    assert 'max="${MAX_PURCHASE_QUANTITY}"' in js
    assert "opening > MAX_PURCHASE_VND" in supplier
    assert "quantity > MAX_PURCHASE_QUANTITY" in receipt
    assert "unitCost > MAX_PURCHASE_VND" in receipt
    assert "lineTotal > MAX_PURCHASE_VND - receiptTotal" in receipt
    assert "const quantityByProduct = new Map()" in receipt
    assert "currentStock > MAX_PURCHASE_QUANTITY - accumulated" in receipt
    assert "paid > MAX_PURCHASE_VND" in confirm
    assert "amount > MAX_PURCHASE_VND" in payment
    assert "20260822-order-actions-a11y-r2" in html
    for key in (
        "seller.purchasing.quantity_limit",
        "seller.purchasing.stock_after_limit",
        "seller.purchasing.money_limit",
        "seller.purchasing.line_total_limit",
        "seller.purchasing.receipt_total_limit",
    ):
        assert locales.count(f"'{key}'") == 2, key


def test_du_ban_dich_quan_trong_cho_ca_tieng_viet_va_tieng_anh():
    locale = _read("static/js/locales/seller.js")

    for key in (
        "seller.tabs.purchasing",
        "seller.purchasing.owner_only",
        "seller.purchasing.receipts_hint",
        "seller.purchasing.confirm_warning",
        "seller.purchasing.confirm_retry_notice",
        "seller.purchasing.payment_retry_notice",
        "seller.purchasing.outside_note_required",
        "seller.purchasing.supplier_deactivated_history",
        "seller.purchasing.supplier_retry_notice",
        "seller.purchasing.storage_unavailable",
        "seller.purchasing.pending_actor_changed",
        "seller.forecast.purchase_action",
        "seller.forecast.purchase_button",
        "seller.purchasing.forecast_prefilled",
        "seller.purchasing.forecast_unavailable",
        "seller.purchasing.editor_already_open",
    ):
        assert locale.count(f"'{key}'") == 2, key
