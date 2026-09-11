"""UI contracts for safe, local assistant navigation commands."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "20260911-r5-5"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_navigation_r1_has_nine_existing_destinations_and_plain_commands():
    js = _read("static/js/seller.js")

    assert "const ASSISTANT_NAVIGATION_TARGETS = Object.freeze" in js
    for destination in (
        "POS", "DASHBOARD", "WAREHOUSE", "PURCHASING", "CUSTOMERS",
        "CASHFLOW", "RECONCILIATION", "ACTION_CENTER", "SETTINGS",
    ):
        assert f"{destination}:" in js
    assert "function nhanLenhDieuHuongTroLy(cau)" in js
    assert ".normalize('NFD')" in js
    assert "seller.assistant.navigation_ready" in js


def test_navigation_r1_stays_local_and_requires_one_confirming_click():
    js = _read("static/js/seller.js")
    submit = js[js.index("async function guiCauHoi"):js.index(
        "document.getElementById('assistantInput')?.addEventListener"
    )]

    assert submit.index("nhanLenhDieuHuongTroLy(cau)") < submit.index("apiCall(")
    assert "ganDieuHuongTroLy" in submit
    assert "return;" in submit
    assert "function ganDieuHuongTroLy(bong, target)" in js
    assert "nut.onclick = () => moDichDenTroLy(target)" in js


def test_navigation_r1_reuses_current_permission_and_navigation_paths():
    js = _read("static/js/seller.js")

    assert "function mucDieuHuongTroLy(target)" in js
    assert "button.style.display === 'none'" in js
    assert "goToPOS(currentShopId)" in js
    assert "switchTab(target.tab" in js
    assert "switchWarehouseSubTab(target.subTab)" in js
    assert "if (target.tab === 'customers') loadCustomers()" in js


def test_navigation_r1_copy_is_bilingual_and_assets_are_fresh():
    html = _read("static/seller.html")
    locale = _read("static/js/locales/seller.js")

    for key in ("navigation_ready", "open_destination", "destination_pos"):
        assert locale.count(f"'seller.assistant.{key}'") == 2
    assert f"/css/seller.css?v={VERSION}" in html
    assert f"/js/locales/seller.js?v={VERSION}" in html
    assert f"/js/seller.js?v={VERSION}" in html
