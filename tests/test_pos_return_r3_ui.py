from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_return_form_requires_explicit_condition_reason_and_transfer_reference():
    html = _read("static/pos.html")
    js = _read("static/js/pos.js")

    assert 'type="checkbox" data-return-restock' not in js
    assert 'class="return-condition"' in js
    assert 'value="restock"' in js and 'value="discard"' in js
    assert 'name="return-condition-${i.id}"' in js
    assert 'value="restock" checked' not in js
    assert 'id="returnReason"' in html and 'required' in html[html.index('id="returnReason"'):html.index('id="returnReason"') + 250]
    assert 'id="returnReference"' in html
    assert 'aria-describedby="returnReasonError"' in html
    assert 'aria-describedby="returnReferenceError"' in html
    assert "pos.return.condition_required" in js
    assert "pos.return.reference_required" in js


def test_return_approval_dialog_is_accessible_and_keeps_pin_ephemeral():
    html = _read("static/pos.html")
    for element_id in (
        "returnApprovalModal",
        "returnApprovalOrder",
        "returnApprovalActor",
        "returnApprovalAmount",
        "returnApprovalMethod",
        "returnApprovalNonRestock",
        "returnApprovalReason",
        "returnApprovalCodes",
        "returnApproverUsername",
        "returnApproverPin",
        "returnApprovalStatus",
    ):
        assert f'id="{element_id}"' in html
    pin = html[html.index('id="returnApproverPin"'):html.index('id="returnApproverPin"') + 300]
    assert 'type="password"' in pin
    assert 'autocomplete="new-password"' in pin
    assert 'inputmode="numeric"' in pin
    for status_id in ("returnMsg", "returnFormStatus", "returnApprovalStatus"):
        fragment = html[html.index(f'id="{status_id}"'):html.index(f'id="{status_id}"') + 220]
        assert 'role="status"' in fragment
        assert 'aria-live="polite"' in fragment

    js = _read("static/js/pos.js")
    block = js[js.index("// RETURN_R3_CONTROLLER_START"):js.index("// RETURN_R3_CONTROLLER_END")]
    assert "localStorage" not in block
    assert "URLSearchParams" not in block


def test_return_copy_is_bilingual_and_assets_share_one_version():
    locale = _read("static/js/locales/pos.js")
    for key in (
        "pos.return.condition_restock",
        "pos.return.condition_discard",
        "pos.return.condition_required",
        "pos.return.reason_required",
        "pos.return.reference_required",
        "pos.return.approval_title",
        "pos.return.approval_required",
        "pos.return.context_changed",
        "pos.return.unknown_result",
        "pos.return.retry_exact",
    ):
        assert locale.count(f"'{key}'") == 2

    html = _read("static/pos.html")
    assert html.count("return-r3=20260909-r5") == 3
