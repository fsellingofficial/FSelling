"""Khóa cấu trúc điều hướng trái của trang quản trị người bán."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_sidebar_nhom_dung_nghiep_vu_va_giu_nguyen_hook_tab():
    html = _read("static/seller.html")
    tabs = re.findall(r'data-main-tab="([^"]+)"', html)

    assert tabs == [
        "dashboard", "action-center", "reconciliation",
        "warehouse", "purchasing", "kiemke",
        "customers", "voucher", "loyalty",
        "cashflow", "nhatky",
        "assistant", "subscription", "settings",
    ]
    assert html.index('class="seller-sidebar"') < html.index('seller-main">')
    assert html.count('id="btnOpenPos"') == 1
    assert html.count('class="seller-nav-group"') == 4


def test_sidebar_co_drawer_mobile_trang_thai_hien_tai_va_nhom_rong():
    css = _read("static/css/seller.css")
    js = _read("static/js/seller.js")

    assert "@media (max-width: 899px)" in css
    assert "body.seller-nav-open .seller-sidebar" in css
    assert "function setSellerNavigation(open)" in js
    assert "function capNhatNhomDieuHuong()" in js
    assert "aria-current" in js
    assert "sellerPageTitle" in js


def test_nhan_sidebar_song_ngu_va_asset_duoc_bump_cache():
    html = _read("static/seller.html")
    locale = _read("static/js/locales/seller.js")
    css_version = "20260825-assistant-guided-tasks-r1-1"
    version = "20260825-assistant-guided-tasks-r1-1"

    for key in (
        "seller.nav.navigation",
        "seller.nav.open_menu",
        "seller.nav.close_menu",
        "seller.nav.group.overview",
        "seller.nav.group.operations",
        "seller.nav.group.sales_customers",
        "seller.nav.group.management",
    ):
        assert locale.count(f"'{key}'") == 2

    assert f"/css/seller.css?v=20260911-r5-5" in html
    assert f"/js/locales/seller.js?v=20260911-r5-5" in html
    assert f"/js/seller.js?v=20260911-r5-5" in html


def test_service_role_and_shared_return_pin_are_wired_without_seller_admin_access():
    html = _read("static/seller.html")
    js = _read("static/js/seller.js")
    locale = _read("static/js/locales/seller.js")
    fnb_js = _read("static/js/fnb-r1a.js")

    assert html.count('value="SERVICE"') >= 1
    assert "selectedRole === 'SERVICE'" in js
    assert "SERVICE: new Set()" in js
    assert html.count("roles-return=20260908-r3") == 3
    assert 'id="returnApprovalSettings"' in html
    assert 'id="returnManagerPin"' in html
    assert "`/shops/${shopId}/manager-pin`" in js
    assert "`/fnb/shops/${shopId}/manager-pin`" in fnb_js
    for key in (
        "seller.staff.role_service_help",
        "seller.staff.role_cashier_help",
        "seller.staff.role_warehouse_help",
        "seller.staff.role_kitchen_help",
        "seller.staff.role_bar_help",
        "seller.staff.role_manager_help",
        "seller.return_pin.title",
    ):
        assert locale.count(f"'{key}'") == 2


def test_auth_asset_cache_key_includes_plan3_role_routing():
    assert "roles-return=20260908-r3" in _read("static/index.html")
