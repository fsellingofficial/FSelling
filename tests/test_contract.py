"""Bảo vệ contract: danh sách route, trang HTML, static, header export."""
from conftest import auth, seller_with_shop

# Ảnh chụp toàn bộ route của app.py TRƯỚC khi refactor (47 route).
BASELINE_ROUTES = {
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/verify-email"),
    ("POST", "/api/auth/resend-code"),
    ("POST", "/api/auth/forgot-password-request"),
    ("POST", "/api/auth/forgot-password-reset"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/session-check"),
    ("POST", "/api/shops"),
    ("GET", "/api/shops"),
    ("PUT", "/api/shops/{shop_id}"),
    ("PUT", "/api/shops/{shop_id}/status"),
    ("DELETE", "/api/shops/{shop_id}"),
    ("GET", "/api/shops/{shop_id}/stats"),
    ("POST", "/api/categories"),
    ("PUT", "/api/categories/{category_id}"),
    ("GET", "/api/categories/{shop_id}"),
    ("POST", "/api/products"),
    ("GET", "/api/products/{shop_id}"),
    ("PUT", "/api/products/{product_id}/status"),
    ("DELETE", "/api/products/{product_id}"),
    ("POST", "/api/orders/webhook"),
    ("POST", "/api/orders/{shop_id}"),
    ("GET", "/api/orders/{order_id}"),
    ("POST", "/api/orders/{order_id}/pay"),
    ("POST", "/api/vouchers"),
    ("PUT", "/api/vouchers/{voucher_id}"),
    ("DELETE", "/api/vouchers/{voucher_id}"),
    ("GET", "/api/vouchers/{shop_id}"),
    ("POST", "/api/vouchers/apply/{shop_id}"),
    ("GET", "/api/dashboard/seller/{shop_id}"),
    ("GET", "/api/dashboard/admin"),
    ("GET", "/api/export/admin"),
    ("GET", "/api/logs/admin"),
    ("GET", "/api/export/seller/{shop_id}"),
    ("GET", "/admin"),
    ("GET", "/pos"),
    ("GET", "/register"),
    ("GET", "/seller"),
    ("GET", "/verify"),
    ("GET", "/admin.html"),
    ("GET", "/pos.html"),
    ("GET", "/register.html"),
    ("GET", "/seller.html"),
    ("GET", "/verify.html"),
    ("GET", "/index.html"),
    ("GET", "/index"),
}


# Route được thêm CÓ CHỦ Ý sau bản refactor. Mọi route /api không nằm trong
# BASELINE_ROUTES hoặc danh sách này đều bị coi là thêm ngoài ý muốn.
ROUTES_BO_SUNG = {
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/sessions"),
    ("PATCH", "/api/auth/sessions/{session_id}"),
    ("DELETE", "/api/auth/sessions/{session_id}"),
    ("POST", "/api/auth/devices/revoke"),
    ("GET", "/api/staff/member/{staff_id}/sessions"),
    ("DELETE", "/api/staff/member/{staff_id}/sessions/{session_id}"),
    ("POST", "/api/staff/member/{staff_id}/devices/revoke"),
    ("GET", "/api/health/ready"),  # I04: readiness chỉ GO sau schema verify
    # Lịch sử đơn R1: read model tối thiểu cho POS, vẫn khóa theo shop và SALE.
    ("GET", "/api/orders/{shop_id}/history"),
    # I10-B: metadata đã lọc và ảnh QR được render cùng origin từ intent v1
    # đã tồn tại. Hai route này không phát hành/tái tạo intent và không nhận
    # token qua query string.
    ("GET", "/api/orders/{order_id}/qr"),
    ("GET", "/api/orders/{order_id}/qr/render"),
    # I10-C: durable normalized inbox plus scoped, explicit reconciliation.
    ("POST", "/api/qr-payments/webhook"),
    ("GET", "/api/qr-reconciliation/events"),
    ("GET", "/api/qr-reconciliation/events/{event_id}"),
    ("POST", "/api/qr-reconciliation/events/{event_id}/actions"),
    ("POST", "/api/orders/{order_id}/cancel"),  # A1d: hủy đơn + hoàn tồn kho
    ("GET", "/api/orders/{order_id}/detail"),   # B3: xem chi tiết đơn kèm dòng hàng
    ("PUT", "/api/products/{product_id}"),      # behavior fix: sửa sản phẩm từ Kho hàng
    ("POST", "/api/products/{product_id}/stock"),  # nhập/xuất kho theo delta
    ("POST", "/api/staff/{shop_id}"),               # C1b: chủ shop tạo nhân viên
    ("GET", "/api/staff/{shop_id}"),                # C1b: danh sách nhân viên
    ("DELETE", "/api/staff/member/{staff_id}"),     # C1b: xóa nhân viên
    ("PUT", "/api/staff/member/{staff_id}/password"),  # C1b: đặt lại mật khẩu NV
    ("PUT", "/api/staff/member/{staff_id}/role"),      # RBAC: đổi preset quyền NV
    ("POST", "/api/customers/{shop_id}"),            # C2b: thêm khách hàng
    ("GET", "/api/customers/{shop_id}"),             # C2b: danh sách/tìm khách
    ("GET", "/api/customers/member/{customer_id}"),  # C2b: chi tiết khách
    ("PUT", "/api/customers/member/{customer_id}"),  # C2b: sửa khách
    ("PUT", "/api/customers/member/{customer_id}/status"),  # H1: dùng lại/ngừng khách
    ("DELETE", "/api/customers/member/{customer_id}"),  # C2b: xóa khách
    ("GET", "/api/customers/member/{customer_id}/history"),  # C2c: lịch sử mua
    ("GET", "/api/products/{shop_id}/barcode/{barcode}"),  # B1a: tra SP theo mã vạch
    # Product Import R1: owner-only, preview-first create flow. Commit is
    # idempotent by operation id; undo hides only products from that import.
    ("POST", "/api/products/{shop_id}/imports/preview"),
    ("POST", "/api/products/{shop_id}/imports/commit"),
    ("POST", "/api/products/{shop_id}/imports/{operation_id}/undo"),
    ("POST", "/api/products/{shop_id}/stocktake"),  # B4: áp dụng kết quả kiểm kê
    ("POST", "/api/tts"),          # D3: sinh giọng đọc khi máy thiếu giọng Việt
    ("GET", "/api/tts/status"),    # D3: frontend hỏi server có đọc hộ được không
    ("POST", "/api/orders/{order_id}/cash-topup"),  # D4: thu tiền mặt bù phần thiếu
    ("POST", "/api/orders/{order_id}/refund-complete"),  # D4: ghi nhận đã hoàn tiền
    ("GET", "/api/shifts/current/{shop_id}"),  # E1: ca OPEN của user hiện tại
    ("POST", "/api/shifts/{shop_id}/open"),  # E1: mở ca thu ngân
    ("GET", "/api/shifts/history/{shop_id}"),  # E1: lịch sử ca
    ("GET", "/api/shifts/{shift_id}"),  # E1: chi tiết ca và sổ thu/chi
    ("POST", "/api/shifts/{shift_id}/movements"),  # E1: thu/chi tiền mặt
    ("POST", "/api/shifts/{shift_id}/close"),  # E1: chốt ca
    # F1: giá vốn tách riêng khỏi GET /api/products/{shop_id}. Endpoint đó nay
    # đã có xác thực (F6) nhưng NHÂN VIÊN vẫn đọc được, còn route này chỉ chủ
    # shop/ADMIN - hai vòng người xem khác nhau nên vẫn phải tách.
    ("GET", "/api/products/{shop_id}/costs"),
    # F2: nhận hàng khách trả. Khác hủy đơn (đơn chưa thanh toán) và khác
    # refund-complete (hoàn khoản chuyển thừa, hàng vẫn của khách).
    ("POST", "/api/orders/{order_id}/returns"),
    ("POST", "/api/orders/{order_id}/returns/approval"),
    # F4: khách trả bớt nợ. Khác cash-topup (đơn chuyển thiếu, phải trả trọn
    # phần còn thiếu) vì trả nợ dần nhiều lần là chuyện bình thường.
    ("POST", "/api/orders/{order_id}/debt-payment"),
    # F5: lô hàng sắp/đã hết hạn của shop.
    ("GET", "/api/products/{shop_id}/batches"),
    # F6: phiếu hủy hàng. Khác hẳn xuất kho: xuất kho không ghi lý do và không
    # chốt giá vốn, nên số hàng đó biến mất khỏi báo cáo và lãi bị thổi lên.
    # F6: kiểm kê theo lô cần biết MỌI lô còn hàng, khác /batches (chỉ lô
    # sắp/đã hết hạn, phục vụ màn cảnh báo).
    ("GET", "/api/products/{shop_id}/stocktake/batches"),
    ("POST", "/api/products/{shop_id}/write-off"),
    ("GET", "/api/products/{shop_id}/write-off/expired"),
    ("GET", "/api/products/{shop_id}/write-offs"),
    # G1: sao lưu DB lên R2. Do dịch vụ cron NGOÀI gọi (máy Fly tự tắt khi rảnh
    # nên APScheduler trong tiến trình không chạy được job ban đêm). Xác thực
    # bằng BACKUP_CRON_SECRET, không qua JWT vì người gọi không phải người dùng.
    ("POST", "/api/cron/backup"),
    # G2: nhận phiếu đã bán khi mất mạng. KHÁC HẲN POST /api/orders/{shop_id}:
    # ở đó giao dịch đang xảy ra nên giá tính lại từ DB và hết hàng thì từ chối;
    # ở đây giao dịch đã xảy ra rồi nên giá lấy từ phiếu và hết hàng vẫn ghi.
    ("POST", "/api/orders/{shop_id}/offline"),
    ("GET", "/api/orders/{shop_id}/offline-issues"),
    # I09-C: chủ shop ghi nhận đã xem một vướng mắc offline không có bằng chứng
    # exact để đóng. Khác kiểm kê (đóng TON_AM bằng hàng thật) và khác phục hồi
    # (map lại sản phẩm/giá vốn, thuộc I09-G).
    ("POST", "/api/orders/{shop_id}/offline-issues/{issue_id}/acknowledge"),
    # I09-D: lifecycle credential; normal-v1 financial ingest chưa được mở.
    ("POST", "/api/offline/leases"),
    ("POST", "/api/offline/leases/{lease_id}/heartbeat"),
    ("POST", "/api/offline/leases/{lease_id}/reclaim"),
    ("DELETE", "/api/offline/leases/{lease_id}"),
    # I09-H: authenticated public policy; the response intentionally carries no
    # token, digest, identity or fleet counts.
    ("GET", "/api/offline/capability"),
    # I09-G1: owner/ADMIN recovery data path. The file checksum is accidental
    # corruption detection only; every operation still requires JWT shop owner.
    ("POST", "/api/offline/recovery/{shop_id}/export"),
    ("POST", "/api/offline/recovery/{shop_id}/import"),
    ("GET", "/api/offline/recovery/{shop_id}/candidates"),
    ("GET", "/api/offline/recovery/{shop_id}/candidates/{offline_uuid}"),
    ("POST", "/api/offline/recovery/{shop_id}/candidates/{offline_uuid}/resolve"),
    # G3: màn "Ai làm gì" của chủ shop. Khác /api/logs/admin: chỉ việc của người
    # thuộc shop này, và đã lọc bỏ hành động không đụng tiền hay kho.
    ("GET", "/api/logs/shop/{shop_id}"),
    # H1: chủ shop tự cài luật tích/đổi điểm; POS và nhân viên bán hàng được đọc.
    ("GET", "/api/loyalty/{shop_id}"),
    ("PUT", "/api/loyalty/{shop_id}"),
    # I1: hồ sơ NCC + công nợ phải trả. Route ``member`` đứng riêng để ID nhà
    # cung cấp không bị nhầm với shop_id của route danh sách.
    ("POST", "/api/suppliers/{shop_id}"),
    ("GET", "/api/suppliers/{shop_id}"),
    ("GET", "/api/suppliers/member/{supplier_id}"),
    ("PUT", "/api/suppliers/member/{supplier_id}"),
    ("PUT", "/api/suppliers/member/{supplier_id}/status"),
    ("DELETE", "/api/suppliers/member/{supplier_id}"),
    ("POST", "/api/suppliers/member/{supplier_id}/payments"),
    # I1: DRAFT chỉ là bản nháp; confirm mới tăng kho và ghi công nợ trong cùng
    # transaction. POSTED không sửa/xóa để giữ nguyên chứng từ.
    ("POST", "/api/purchase-receipts/{shop_id}"),
    ("GET", "/api/purchase-receipts/{shop_id}"),
    ("GET", "/api/purchase-receipts/receipt/{receipt_id}"),
    ("PUT", "/api/purchase-receipts/receipt/{receipt_id}"),
    ("DELETE", "/api/purchase-receipts/receipt/{receipt_id}"),
    ("POST", "/api/purchase-receipts/receipt/{receipt_id}/confirm"),
    # Purchase Order is a commitment only; receipt confirmation remains the
    # sole stock/payable mutation.
    ("POST", "/api/purchase-orders/{shop_id}"),
    ("GET", "/api/purchase-orders/{shop_id}"),
    ("GET", "/api/purchase-orders/order/{order_id}"),
    ("PUT", "/api/purchase-orders/order/{order_id}"),
    ("DELETE", "/api/purchase-orders/order/{order_id}"),
    ("POST", "/api/purchase-orders/order/{order_id}/place"),
    ("POST", "/api/purchase-orders/order/{order_id}/cancel"),
    # J1: gói Free/Pro theo shop. Tiền thuê bao dùng webhook/ledger riêng, không
    # đi vào OrderPayment/doanh thu bán hàng của shop.
    ("GET", "/api/subscriptions/{shop_id}"),
    ("POST", "/api/subscriptions/{shop_id}/checkouts"),
    ("POST", "/api/subscriptions/webhook"),
    ("GET", "/api/admin/subscriptions"),
    ("POST", "/api/admin/subscriptions/{shop_id}/gifts"),
    ("POST", "/api/admin/subscriptions/grants/{grant_id}/revoke"),
    ("GET", "/api/admin/subscription-payments"),
    # K1: chi phí vận hành + lãi ròng + dòng tiền. Ba tiền tố tách riêng để id
    # danh mục / id khoản chi không bao giờ rơi vào cùng khuôn với shop_id.
    # Danh mục CỐ Ý không có DELETE: SQLite production không bật khóa ngoại nên
    # xóa một danh mục đang dùng chỉ để lại báo cáo cũ trỏ vào hư không.
    ("GET", "/api/expense-categories/{shop_id}"),
    ("POST", "/api/expense-categories/{shop_id}"),
    ("PUT", "/api/expense-categories/{shop_id}/{category_id}"),
    ("GET", "/api/expense-templates/{shop_id}"),
    ("POST", "/api/expense-templates/{shop_id}"),
    ("PUT", "/api/expense-templates/{shop_id}/{template_id}"),
    ("GET", "/api/expense-reminders/{shop_id}"),
    ("GET", "/api/expenses/{shop_id}"),
    ("POST", "/api/expenses/{shop_id}"),
    # Gỡ khoản ghi nhầm. KHÔNG phải DELETE: khoản đã rút tiền từ ca bị từ chối,
    # và dòng vẫn được giữ lại để truy được ai gỡ.
    ("POST", "/api/expenses/{shop_id}/{expense_id}/void"),
    # Lợi nhuận ròng + dòng tiền thực. Khác /api/stats/{shop_id}: màn đó dừng ở
    # lãi gộp và MANAGER xem được, còn màn này chỉ chủ shop/ADMIN.
    ("GET", "/api/reports/cashflow/{shop_id}"),
    # L1: dự báo nhập hàng. Chỉ ĐỌC và tính bằng công thức, không ghi gì và
    # không gọi dịch vụ ngoài. Tách khỏi /api/stats vì trả lời câu hỏi khác:
    # stats nói chuyện đã xảy ra, còn màn này nói ngày mai phải nhập bao nhiêu.
    ("GET", "/api/forecast/{shop_id}"),
    # L2: xả hàng tồn. CHỈ ĐỌC - không tự đổi giá, không tự tạo voucher. Tách
    # khỏi /api/forecast vì hỏi ngược lại: forecast lo hàng sắp HẾT, màn này lo
    # hàng KHÔNG ĐI. Chỉ chủ shop/ADMIN vì mọi con số đều dựng từ giá vốn.
    ("GET", "/api/clearance/{shop_id}"),
    # L3: hỏi đáp báo cáo bằng tiếng Việt. POST chứ không GET vì câu hỏi là nội
    # dung người dùng gõ, mà query string thì nằm lại trong lịch sử trình duyệt
    # và log máy chủ. CHỈ ĐỌC: nó chỉ chọn một báo cáo có sẵn rồi gọi lại, không
    # tự sinh câu lệnh và không ghi gì.
    ("POST", "/api/assistant/{shop_id}"),
    # Assistant Feedback R1: POST chỉ ghi metadata cố định của một reply đã ký;
    # GET chỉ trả aggregate cho chủ shop/ADMIN, không có raw question/answer.
    ("POST", "/api/assistant/{shop_id}/feedback"),
    ("GET", "/api/assistant/{shop_id}/feedback/summary"),
    # Onboarding R1: owner-only read model over durable shop/product/shift/order
    # facts. It never records clicks or performs business mutations.
    ("GET", "/api/onboarding/{shop_id}"),
    # Action Center R1: một GET read-only tổng hợp các nguồn sự thật hiện có;
    # không notification table, background job hay mutation tự động.
    ("GET", "/api/action-center/{shop_id}"),
    # F&B R1A: setup/floor và lifecycle phiên phục vụ nháp revision-safe.
    ("PATCH", "/api/fnb/shops/{shop_id}/settings"),
    ("POST", "/api/fnb/areas"),
    ("PATCH", "/api/fnb/areas/{area_id}"),
    ("POST", "/api/fnb/tables"),
    ("PATCH", "/api/fnb/tables/{table_id}"),
    ("GET", "/api/fnb/floor"),
    ("POST", "/api/fnb/sessions"),
    ("GET", "/api/fnb/sessions/{session_id}"),
    ("POST", "/api/fnb/sessions/{session_id}/lines"),
    ("PATCH", "/api/fnb/lines/{line_id}"),
    ("POST", "/api/fnb/sessions/{session_id}/cancel-line"),
    ("POST", "/api/fnb/sessions/{session_id}/move-table"),
    ("POST", "/api/fnb/sessions/{session_id}/merge-table"),
    ("POST", "/api/fnb/sessions/{session_id}/cancel"),
    ("PATCH", "/api/fnb/menu-items/{product_id}/station"),
    ("POST", "/api/fnb/sessions/{session_id}/send"),
    ("GET", "/api/fnb/stations/{station}/tickets"),
    ("POST", "/api/fnb/tickets/{ticket_id}/start"),
    ("POST", "/api/fnb/tickets/{ticket_id}/done"),
    ("POST", "/api/fnb/tickets/{ticket_id}/out-of-stock"),
    ("POST", "/api/fnb/tickets/{ticket_id}/resume"),
    ("POST", "/api/fnb/tickets/{ticket_id}/serve"),
    ("PATCH", "/api/fnb/shops/{shop_id}/manager-pin"),
    ("POST", "/api/fnb/manager-approvals"),
    ("PATCH", "/api/shops/{shop_id}/manager-pin"),
    # F&B R1C: bill, split, provisional receipt, payment and safe table close.
    ("GET", "/api/fnb/sessions/{session_id}/checks"),
    ("POST", "/api/fnb/checks/{check_id}/split-preview"),
    ("POST", "/api/fnb/checks/{check_id}/split"),
    ("PATCH", "/api/fnb/checks/{check_id}/adjustments"),
    ("GET", "/api/fnb/checks/{check_id}/provisional-receipt"),
    ("POST", "/api/fnb/checks/{check_id}/pay"),
    ("POST", "/api/fnb/sessions/{session_id}/close"),
}


def _iter_routes(routes):
    """Yield concrete routes across eager and lazy FastAPI router layouts."""
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_routes(original_router.routes)
        else:
            yield route


def _routes(app):
    found = set()
    for route in _iter_routes(app.routes):
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or path is None:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            found.add((m, path))
    return found


def test_giu_nguyen_toan_bo_route_cu(app):
    found = _routes(app)
    thieu = BASELINE_ROUTES - found
    assert not thieu, f"Thiếu route so với bản gốc: {sorted(thieu)}"


def test_khong_them_route_api_ngoai_du_kien(app):
    duoc_phep = BASELINE_ROUTES | ROUTES_BO_SUNG
    api_moi = {r for r in _routes(app) if r[1].startswith("/api/")} - duoc_phep
    assert not api_moi, f"Route /api mới ngoài dự kiến: {sorted(api_moi)}"


def test_cac_route_bo_sung_deu_ton_tai(app):
    thieu = ROUTES_BO_SUNG - _routes(app)
    assert not thieu, f"Route bổ sung bị thiếu: {sorted(thieu)}"


def test_webhook_dang_ky_truoc_route_shop_id(app):
    paths = [
        (list(r.methods)[0] if getattr(r, "methods", None) else None, r.path)
        for r in _iter_routes(app.routes)
        if getattr(r, "path", "").startswith("/api/orders")
    ]
    order_paths = [p for _, p in paths]
    assert order_paths.index("/api/orders/webhook") < order_paths.index("/api/orders/{shop_id}")

    subscription_paths = [
        r.path
        for r in _iter_routes(app.routes)
        if getattr(r, "path", "").startswith("/api/subscriptions")
    ]
    assert subscription_paths.index("/api/subscriptions/webhook") < (
        subscription_paths.index("/api/subscriptions/{shop_id}")
    )


def test_cors_cho_phep_header_lease_nhung_khong_mo_wildcard(client):
    response = client.options(
        "/api/offline/leases/lse_0000000000000000000000/heartbeat",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Offline-Lease-Token",
        },
    )
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-offline-lease-token" in allowed
    assert response.headers["access-control-allow-origin"] == "http://testserver"


def test_trang_html_va_redirect(client):
    for page in ("/admin", "/pos", "/register", "/seller", "/verify"):
        res = client.get(page)
        assert res.status_code == 200, page
        assert "text/html" in res.headers["content-type"]

    for old, new in (
        ("/admin.html", "/admin"),
        ("/pos.html", "/pos"),
        ("/index.html", "/"),
        ("/index", "/"),
    ):
        res = client.get(old, follow_redirects=False)
        assert res.status_code == 301, old
        assert res.headers["location"] == new


def test_trang_chu_va_static_file(client):
    assert client.get("/").status_code == 200
    assert client.get("/js/api.js").status_code == 200
    assert client.get("/js/i18n.js").status_code == 200
    assert client.get("/js/vendor/i18next-26.3.6.min.js").status_code == 200
    for catalog in ("common", "auth-admin", "seller", "pos"):
        assert client.get(f"/js/locales/{catalog}.js").status_code == 200
    assert client.get("/css/style.css").status_code == 200


def test_export_seller_tra_dung_header(client):
    ctx = seller_with_shop(client)
    res = client.get(f"/api/export/seller/{ctx['shop_id']}", headers=auth(ctx["token"]))
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]
    assert "seller_transactions.xlsx" in res.headers["content-disposition"]


def test_danh_sach_san_pham_giu_nguyen_cac_truong(client):
    ctx = seller_with_shop(client)
    res = client.get(f"/api/products/{ctx['shop_id']}", headers=auth(ctx["token"]))
    assert res.status_code == 200
    item = res.json()[0]
    assert set(item.keys()) == {
        "id",
        "code",
        "barcode",  # B1a: thêm khóa là thay đổi an toàn với frontend hiện tại
        "name",
        "price",
        "stock",
        "image_url",
        "is_active",
        "category_id",
        "shop_id",
        "category_is_active",
        # F5: cờ theo dõi lô + hạn sử dụng. POS cần biết để cảnh báo khi tồn
        # khả dụng thấp hơn tổng tồn vì có hàng quá hạn.
        "track_batches",
        # F6: biến thể. Cả hai NULL với sản phẩm đơn lẻ, nên client cũ đọc
        # `name` như trước vẫn đúng - `name` đã là tên đầy đủ kèm biến thể.
        "variant_group",
        "variant_name",
        # R1B: nơi chế biến mặc định, dùng lại cùng catalog cho màn phục vụ.
        "fnb_station",
    }


def test_dashboard_seller_giu_nguyen_contract(client):
    """Nhóm B thêm khóa phân trang. Hai khóa cũ phải còn nguyên tên và ý nghĩa
    để frontend hiện tại không vỡ (thêm khóa là thay đổi an toàn)."""
    ctx = seller_with_shop(client)
    res = client.get(f"/api/dashboard/seller/{ctx['shop_id']}", headers=auth(ctx["token"]))
    body = res.json()
    assert {"total_revenue", "orders"} <= set(body.keys())
    assert isinstance(body["orders"], list)
    assert set(body.keys()) == {
        "total_revenue", "orders", "page", "per_page", "total_orders", "has_more",
        "reconciliation_count",
    }


# ---------- Cache-busting cho file tĩnh ----------
def test_moi_file_js_css_deu_co_dau_phien_ban():
    """Mọi <script src="/js/..."> và <link href="/css/..."> phải kèm `?v=`.

    Trình duyệt cache JS/CSS rất dai (xem CLAUDE.md). Sửa file mà quên đổi dấu
    phiên bản thì người dùng đang mở sẵn trang sẽ CHẠY CODE CŨ - trong im lặng,
    không có lỗi nào cả, và mọi tính năng mới coi như không tồn tại với họ.
    Test này chỉ bắt được ca "thêm file mới mà quên `?v=`"; việc BUMP dấu phiên
    bản khi sửa file thì không máy nào kiểm hộ được, phải tự nhớ.
    """
    import re
    from pathlib import Path

    static_dir = Path(__file__).resolve().parent.parent / "static"
    mau = re.compile(r'(?:src|href)="(/(?:js|css)/[^"]+)"')
    thieu = []
    for trang in sorted(static_dir.glob("*.html")):
        for duong_dan in mau.findall(trang.read_text(encoding="utf-8")):
            if "?v=" not in duong_dan:
                thieu.append(f"{trang.name}: {duong_dan}")
    assert not thieu, "File tĩnh thiếu dấu phiên bản: " + ", ".join(thieu)
