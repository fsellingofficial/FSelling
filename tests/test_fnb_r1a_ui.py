import re
import subprocess
import uuid
from pathlib import Path

from conftest import (
    auth,
    create_fnb_area,
    create_fnb_table,
    enable_fnb,
    new_staff,
    seller_with_shop,
)


ROOT = Path(__file__).resolve().parents[1]


def _catalog_keys(path: Path, locale: str) -> set[str]:
    content = path.read_text(encoding="utf-8")
    marker = f"Object.assign(resources.{locale}.translation, {{"
    start = content.index(marker) + len(marker)
    end_marker = (
        "Object.assign(resources.en.translation, {"
        if locale == "vi"
        else "})(window);"
    )
    end = content.index(end_marker, start)
    return set(re.findall(r"^\s*'([^']+)'\s*:", content[start:end], re.MULTILINE))


def test_fnb_page_and_assets_are_wired(client):
    page = client.get("/fnb")
    assert page.status_code == 200
    html = page.text
    for element_id in (
        "fnbFloor",
        "fnbFloorSummary",
        "fnbSessionPanel",
        "fnbLiveStatus",
        "fnbSetupOpen",
        "fnbSetupDialog",
        "fnbCheckoutOpen",
        "fnbCheckoutDialog",
        "fnbCheckList",
        "fnbVoucherCode",
        "fnbLoyaltyPoints",
        "fnbPayButton",
        "fnbCashTendered",
        "fnbCashExact",
        "fnbCashTenderedHelp",
        "fnbCashTenderedError",
        "fnbClosePaidSession",
        "fnbVariantDialog",
        "fnbVariantList",
        "fnbCategoryTabs",
        "fnbCheckoutHint",
        "fnbServiceTickets",
    ):
        assert f'id="{element_id}"' in html
    assert "/css/fnb-r1a.css?v=20260907-plan2-correction1" in html
    assert 'aria-describedby="fnbCashTenderedHelp fnbCashTenderedError"' in html
    assert "/js/locales/fnb.js?v=20260907-plan2" in html
    assert "/js/fnb-r1a.js?v=20260907-plan2-correction1" in html
    source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    assert "values.voucher_code = voucherCode" in source
    assert "values.loyalty_points_to_use = loyaltyPoints" in source
    assert "groupMenuProducts(categoryProducts, query)" in source
    assert 'data-action="choose-variant"' in source
    assert 'data-action="add-variant"' in source
    assert "type: 'cancel-action-required'" in source
    assert "type: 'cancel-conflict'" in source
    assert "FNB_CANCELLATION_DECISION_REQUIRED" in source
    assert "FNB_APPROVAL_REQUIRED" in source
    assert "function resetApprovalDialog()" in source
    assert 'data-action="serve-ticket"' in source
    assert "expected_session_revision: Number(state.session.revision)" in source
    assert "controller.retryPending()" in source
    assert client.get("/fnb.html", follow_redirects=False).headers["location"] == "/fnb"
    api_source = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    assert "error.detail =" in api_source


def test_fnb_station_page_and_role_routing_are_wired(client):
    page = client.get("/fnb/station/kitchen")
    assert page.status_code == 200
    assert 'id="fnbStationTickets"' in page.text
    assert 'id="fnbStationConnection"' in page.text
    assert "/css/fnb-station-r1b.css?" in page.text
    assert "/js/fnb-station-r1b.js?v=20260907-plan2" in page.text
    assert "/js/i18n.js?" in page.text

    source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    assert "['KITCHEN', 'BAR'].includes(staffRole)" in source
    assert "document.querySelectorAll('.fnb-queue-link')" in source
    station_source = (ROOT / "static/js/fnb-station-r1b.js").read_text(encoding="utf-8")
    assert "expected_session_revision" in station_source
    assert "controller.retryPending()" in station_source
    assert 'data-action="resume"' in station_source
    assert "Sẵn sàng giao" in station_source


def test_fnb_role_translations_use_one_fresh_common_catalog_url():
    expected = "/js/locales/common.js?v=20260901-fnb-r1b"
    for name in ("index.html", "seller.html", "fnb.html", "pos.html"):
        assert expected in (ROOT / "static" / name).read_text(encoding="utf-8")


def test_table_setup_forms_come_before_the_potentially_long_station_list():
    html = (ROOT / "static/fnb.html").read_text(encoding="utf-8")
    assert html.index('id="fnbAreaForm"') < html.index('id="fnbStationList"')


def test_fnb_r3_operational_layout_is_wired():
    html = (ROOT / "static/fnb.html").read_text(encoding="utf-8")
    source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    station = (ROOT / "static/js/fnb-station-r1b.js").read_text(encoding="utf-8")
    assert 'class="fnb-order-layout"' in html
    assert 'class="fnb-menu-pane"' in html
    assert 'class="fnb-bill-pane"' in html
    assert 'data-action="quantity-minus"' in source
    assert "fnb.checkout.paid_label" in source
    assert "ticketAgeMinutes" in station
    assert "fnb-ticket-lane" in station


def test_fnb_back_links_are_touch_sized():
    css = (ROOT / "static/css/fnb-r1a.css").read_text(encoding="utf-8")
    rule = css.split(".fnb-back {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex" in rule
    assert "min-height: 44px" in rule


def test_pos_entry_is_hidden_until_shop_capability_is_known():
    html = (ROOT / "static/pos.html").read_text(encoding="utf-8")
    assert 'id="btnTableService"' in html
    assert "hidden" in html.split('id="btnTableService"', 1)[1].split(">", 1)[0]
    source = (ROOT / "static/js/pos.js").read_text(encoding="utf-8")
    assert "function updateFnbCapability()" in source
    assert "localStorage.setItem('currentShopId'" in source
    assert "navigateToPage('/fnb')" in source


def test_final_fnb_receipt_reuses_pos_receipt_route():
    fnb_source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    pos_source = (ROOT / "static/js/pos.js").read_text(encoding="utf-8")
    assert "['PAID', 'DEBT'].includes(check.status)" in fnb_source
    assert "`/pos?receipt=${Number(check.order_id)}`" in fnb_source
    assert "query.get('receipt')" in pos_source
    assert "hienHoaDon(receiptId, null, true)" in pos_source


def test_owner_edit_switch_and_bilingual_contracts():
    seller_html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    seller_js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    assert seller_html.count('id="shopFnbEnabled"') == 1
    assert 'id="shopFnbSetting"' in seller_html
    assert "const fnbSettingOperations = new Map();" in seller_js
    assert "MY_ROLE === 'SELLER'" in seller_js
    assert "shopFnbEnabled.disabled = true" in seller_js

    fnb_path = ROOT / "static/js/locales/fnb.js"
    vi_keys = _catalog_keys(fnb_path, "vi")
    en_keys = _catalog_keys(fnb_path, "en")
    assert vi_keys == en_keys
    assert {
        "fnb.title",
        "fnb.state.loading",
        "fnb.state.no_tables",
        "fnb.state.poll_error",
        "fnb.state.offline",
        "fnb.state.conflict",
        "fnb.action.reapply",
        "fnb.setup.open",
        "fnb.setup.inactive",
        "fnb.auth.feature_disabled",
        "fnb.checkout.open",
        "fnb.checkout.split",
        "fnb.checkout.pay",
        "fnb.checkout.close_table",
        "fnb.checkout.offline",
        "fnb.checkout.cash_exact",
        "fnb.checkout.cash_help",
        "fnb.checkout.cash_promotion_help",
        "fnb.checkout.cash_required",
        "fnb.checkout.confirm_cash",
        "fnb.cancel.action_required",
        "fnb.cancel.changed",
        "fnb.cancel.reason_required",
        "fnb.service.waiting",
        "fnb.service.ready",
        "fnb.service.served",
        "fnb.service.mark_served",
        "fnb.checkout.service_block",
    } <= vi_keys

    for path, keys in (
        (ROOT / "static/js/locales/pos.js", {"pos.mode.table_service"}),
        (
            ROOT / "static/js/locales/seller.js",
            {
                "seller.shops.fnb_label",
                "seller.shops.fnb_hint",
                "seller.shops.fnb_enabled",
                "seller.shops.fnb_disabled",
            },
        ),
    ):
        assert keys <= _catalog_keys(path, "vi")
        assert keys <= _catalog_keys(path, "en")


def test_dynamic_fnb_content_uses_delegation_not_inline_handlers():
    source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    assert "onclick" not in source.lower()
    assert "data-action" in source
    assert "addEventListener('click'" in source or 'addEventListener("click"' in source
    assert "event.key === 'Escape'" in source


def test_setup_button_stays_hidden_without_an_enabled_shop():
    source = (ROOT / "static/js/fnb-r1a.js").read_text(encoding="utf-8")
    assert "elements.fnbSetupOpen.hidden = !(setupAllowed && shops.length);" in source


def test_manager_floor_snapshot_can_include_hidden_setup_rows(client):
    ctx = seller_with_shop(client)
    enable_fnb(client, ctx)
    area = create_fnb_area(client, ctx, "Sân sau")
    table = create_fnb_table(client, ctx, area["id"], "Bàn khuất")

    hidden_table = client.patch(
        f"/api/fnb/tables/{table['id']}",
        json={
            "active": False,
            "expected_revision": table["fnb_revision"],
            "expected_state_version": table["state_version"],
            "operation_id": f"hide-table-{uuid.uuid4().hex}",
        },
        headers=auth(ctx["token"]),
    )
    assert hidden_table.status_code == 200
    hidden_area = client.patch(
        f"/api/fnb/areas/{area['id']}",
        json={
            "active": False,
            "expected_revision": hidden_table.json()["fnb_revision"],
            "operation_id": f"hide-area-{uuid.uuid4().hex}",
        },
        headers=auth(ctx["token"]),
    )
    assert hidden_area.status_code == 200

    default_floor = client.get(
        "/api/fnb/floor",
        params={"shop_id": ctx["shop_id"]},
        headers=auth(ctx["token"]),
    )
    assert default_floor.status_code == 200
    assert default_floor.json()["areas"] == []

    owner_floor = client.get(
        "/api/fnb/floor",
        params={"shop_id": ctx["shop_id"], "include_inactive": True},
        headers=auth(ctx["token"]),
    )
    assert owner_floor.status_code == 200
    assert owner_floor.json()["areas"][0]["active"] is False
    assert owner_floor.json()["areas"][0]["tables"][0]["active"] is False

    for staff_role, expected in (
        ("MANAGER", 200),
        ("CASHIER", 403),
        ("WAREHOUSE", 403),
    ):
        _, token = new_staff(client, ctx, staff_role)
        response = client.get(
            "/api/fnb/floor",
            params={"shop_id": ctx["shop_id"], "include_inactive": True},
            headers=auth(token),
        )
        assert response.status_code == expected


def test_fnb_controller_node_harness():
    result = subprocess.run(
        ["node", "tests/js/fnb-r1a.test.js"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
