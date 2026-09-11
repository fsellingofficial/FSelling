"""UI contracts for local, safe guided task answers."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "20260911-r5-5"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_guided_tasks_r1_covers_nine_core_tasks_and_plain_help_phrases():
    js = _read("static/js/seller.js")

    assert "const ASSISTANT_GUIDED_TASKS = Object.freeze" in js
    for task in (
        "SELL", "OPEN_SHIFT", "CLOSE_SHIFT", "CREATE_PRODUCT",
        "RECEIVE_STOCK", "CUSTOMER_DEBT", "STOCKTAKE", "REORDER", "EXPIRY",
    ):
        assert f"{task}:" in js
    assert "function nhanYeuCauHuongDanTroLy(cau)" in js
    assert ".normalize('NFD')" in js
    for phrase in ("lam sao", "huong dan", "how do i", "show me how"):
        assert phrase in js


def test_guided_tasks_r1_is_local_structured_and_requires_user_action():
    js = _read("static/js/seller.js")
    submit = js[js.index("async function guiCauHoi"):js.index(
        "document.getElementById('assistantInput')?.addEventListener"
    )]

    assert submit.index("nhanYeuCauHuongDanTroLy(cau)") < submit.index("apiCall(")
    assert "ganHuongDanTroLy" in submit
    assert "return;" in submit
    assert "function ganHuongDanTroLy(bong, huongDan)" in js
    assert "document.createElement('ol')" in js
    assert "document.createElement('li')" in js
    assert "ganDieuHuongTroLy(bong, huongDan.target)" in js
    assert "apiCall" not in js[js.index("function ganHuongDanTroLy"):js.index(
        "function capNhatNutHoiTroLy"
    )]


def test_guided_tasks_r1_reuses_permission_aware_destinations_and_is_discoverable():
    js = _read("static/js/seller.js")
    css = _read("static/css/seller.css")

    assert "mucDieuHuongTroLy(huongDan.target)" in js
    assert "seller.assistant.guide_unavailable" in js
    assert "seller.assistant.sample_guide" in js
    assert ".assistant-guide-steps" in css
    assert "padding-left" in css


def test_guided_tasks_r1_copy_is_bilingual_and_assets_are_fresh():
    html = _read("static/seller.html")
    locale = _read("static/js/locales/seller.js")

    for key in ("sample_guide", "guide_safety", "guide_unavailable"):
        assert locale.count(f"'seller.assistant.{key}'") == 2
    for task in (
        "sell", "open_shift", "close_shift", "create_product",
        "receive_stock", "customer_debt", "stocktake", "reorder", "expiry",
    ):
        assert locale.count(f"'seller.assistant.guide.{task}.title'") == 2
        for step in range(1, 4):
            assert locale.count(
                f"'seller.assistant.guide.{task}.step{step}'"
            ) == 2
    assert f"/css/seller.css?v={VERSION}" in html
    assert f"/js/locales/seller.js?v={VERSION}" in html
    assert f"/js/seller.js?v={VERSION}" in html
