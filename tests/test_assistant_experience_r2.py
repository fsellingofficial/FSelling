"""UI contracts for the existing assistant's R2 experience upgrade."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "20260911-r5-5"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_assistant_r2_explains_trust_boundary_and_is_accessible():
    html = _read("static/seller.html")

    assert 'class="assistant-intro"' in html
    assert 'id="assistantSafety"' in html
    assert 'data-i18n="seller.assistant.safety"' in html
    assert 'id="assistantChat"' in html
    assert 'role="log"' in html
    assert 'aria-live="polite"' in html
    assert 'id="assistantStatus"' in html
    assert 'role="status"' in html
    assert 'id="assistantSend"' in html and "disabled" in html


def test_assistant_r2_routes_answers_to_existing_reports_only():
    js = _read("static/js/seller.js")

    assert "const ASSISTANT_INTENT_TARGETS = Object.freeze" in js
    for intent in (
        "DOANH_THU", "SO_DON", "SO_SANH_TUAN", "BAN_CHAY", "SAP_HET_HAN",
        "CAN_NHAP", "HANG_E", "CONG_NO", "LAI", "TONG_QUAN", "GIA_TON",
        "CHI_PHI", "SHOP", "CA_TIEN",
    ):
        assert f"{intent}:" in js
    assert "function moBaoCaoTroLy(yDinh)" in js
    assert "switchTab(target.tab" in js
    assert "switchWarehouseSubTab(target.subTab)" in js
    assert "ganHanhDongTroLy(bongTraLoi, d.y_dinh, d.nguon)" in js


def test_assistant_r2_has_offline_retry_and_safe_submit_states():
    js = _read("static/js/seller.js")

    assert "function capNhatNutHoiTroLy()" in js
    assert "function thuLaiCauHoiTroLy()" in js
    assert "window.scrollTo({ top: 0, behavior: 'auto' })" in js
    assert "cauHoiTroLyGanNhat" in js
    assert "navigator.onLine" in js
    assert "aria-busy" in js
    assert "seller.assistant.offline" in js
    assert "seller.assistant.retry" in js
    assert "e?.message === t('common.network_error')" in js
    assert "delete khungChat.dataset.daChao" in js
    assert "if (window.innerWidth > 640) o?.focus()" in js


def test_assistant_r2_copy_styles_and_assets_are_bilingual_and_fresh():
    html = _read("static/seller.html")
    css = _read("static/css/seller.css")
    locale = _read("static/js/locales/seller.js")

    for key in (
        "safety", "starter_title", "sample_overview", "sample_debt",
        "sample_cash", "open_report", "retry", "offline",
    ):
        assert locale.count(f"'seller.assistant.{key}'") == 2

    for selector in (
        ".assistant-intro", ".assistant-chat", ".assistant-bubble",
        ".assistant-suggestions button", ".assistant-answer-action",
        ".assistant-feedback-action",
    ):
        assert selector in css
    voice_checkbox = css[css.index(".assistant-voice-options input {"):]
    voice_checkbox = voice_checkbox[:voice_checkbox.index("}")]
    assert "width: auto" in voice_checkbox
    assert "flex: 0 0 auto" in voice_checkbox
    assert "@media (max-width: 640px)" in css
    assert f"/css/seller.css?v={VERSION}" in html
    assert f"/js/locales/seller.js?v={VERSION}" in html
    assert f"/js/seller.js?v={VERSION}" in html
