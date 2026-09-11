"""I09-F3: POS presents sanitized v0/v1 sync state without mutating the queue."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_node_offline_ui_state_mapping_harness():
    completed = subprocess.run(
        ["node", "tests/js/pos-offline-ui.test.js"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "pos-offline-ui harness: 1 passed" in completed.stdout


def test_badge_is_accessible_control_with_sanitized_modal_and_explicit_retry_only():
    html = _read("static/pos.html")
    pos = _read("static/js/pos.js")
    assert 'id="offlineBadge" type="button"' in html
    assert 'aria-controls="offlineStatusModal"' in html
    assert 'role="dialog"' in html and 'aria-modal="true"' in html
    assert "getOfflineStatusV1" in pos
    assert "OfflineBan.resumeSyncV1(identityDongBoPOS())" in pos
    assert "if (model.transient && !OfflineBan.dangOffline())" in pos
    assert "model.hard" not in pos[pos.index("function capNhatNoiDungTrangThaiOffline"):pos.index("async function moModalTrangThaiOffline")]
    assert "textContent" in pos
    assert "innerHTML" not in pos[pos.index("function capNhatNoiDungTrangThaiOffline"):pos.index("async function moModalTrangThaiOffline")]


def test_v1_status_view_never_exports_secret_or_receipt_payload():
    js = _read("static/js/offline-ban.js")
    start = js.index("async function getOfflineStatusV1(")
    end = js.index("async function resumeSyncV1(", start)
    view = js[start:end]
    for forbidden in ("lease_token", "offline_uuid", "client_fingerprint", "catalog_snapshot_digest", "device_label", "items"):
        assert forbidden not in view


def test_i09_f3_cache_busters_cover_every_changed_pos_asset():
    html = _read("static/pos.html")
    for asset, expected_v in [
        ("pos.css", "20260911-r5-5"),
        ("locales/pos.js", "20260911-r5-7"),
        ("offline-ban.js", "20260813-i09-f3&g=20260813-i09-g2h6"),
        ("pos.js", "20260911-r5-7"),
    ]:
        assert f"/{'css/' if asset == 'pos.css' else 'js/'}{asset}?v={expected_v}" in html
