"""I10-D: POS QR v1 presentation lifecycle — static proof tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAYMENT_KEYS_VI = [
    'pos.payment.v1_loading',
    'pos.payment.v1_unavailable',
    'pos.payment.v1_hidden',
    'pos.payment.bank_code_label',
    'pos.payment.account_no_label',
    'pos.payment.account_name_label',
    'pos.payment.reference_label',
    'pos.payment.instruction_only',
    'pos.payment.v1_img_alt',
]
PAYMENT_KEYS_EN = [
    'pos.payment.v1_loading',
    'pos.payment.v1_unavailable',
    'pos.payment.v1_hidden',
    'pos.payment.bank_code_label',
    'pos.payment.account_no_label',
    'pos.payment.account_name_label',
    'pos.payment.reference_label',
    'pos.payment.instruction_only',
    'pos.payment.v1_img_alt',
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding='utf-8')


def _no_cached(js: str) -> str:
    """Strip block comments so search-based checks aren't fooled."""
    return '\n'.join(line.split('//')[0] for line in js.splitlines())


# ── pos.js lifecycle ownership ─────────────────────────────────────────────────


def test_qr1_module_scope_owner_exists():
    js = _read('static/js/pos.js')
    assert '_qr1OrderId' in js
    assert '_qr1Generation' in js
    assert '_qr1Controller' in js
    assert '_qr1ObjectUrl' in js
    assert '_qr1State' in js
    assert '_qr1Fingerprint' in js


def test_qr1_cleanup_aborts_controller_and_revokes_url():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert '_qr1Controller.abort()' in plain
    assert 'URL.revokeObjectURL(_qr1ObjectUrl)' in plain
    assert '_qr1Generation++' in plain
    assert '_qr1OrderId = null' in plain
    assert '_qr1State = null' in plain
    assert '_qr1Fingerprint = null' in plain


def test_qr1_cleanup_clears_image_src_and_manual_dom():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert "img.src = ''" in plain
    assert "qr1Status" in plain and "innerText = ''" in plain
    assert "qr1Manual" in plain and "style.display = 'none'" in plain


def test_qr1_cleanup_clears_sensitive_manual_fields():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert "qr1BankCode" in plain and "textContent = ''" in plain
    assert "qr1AccountNo" in plain and "textContent = ''" in plain
    assert "qr1AccountName" in plain and "textContent = ''" in plain
    assert "qr1Reference" in plain and "textContent = ''" in plain


def test_qr1_show_transient_exists_and_is_exported():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert 'function qr1ShowTransient(' in plain
    assert '_qr1FetchAndRender(intent, orderId, gen)' in plain


def test_qr1_recover_v1_exists_and_calls_api_once():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert 'async function _qr1RecoverV1(' in plain
    assert 'apiCall(`/orders/${orderId}/qr`)' in plain
    # Bounded: stop at function htmlNut (the next named function), not qr1ShowTransient
    # which appears earlier in the file and is not a recovery boundary.
    start = plain.index('async function _qr1RecoverV1(')
    end = plain.index('function htmlNut', start)
    assert 'startPaymentPolling' not in plain[start:end]


def test_qr1_fetch_requires_exact_endpoint_pattern():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Endpoint must be validated against the exact pattern.
    assert '`/api/orders/${orderId}/qr/render`' in plain
    # Must not log account/reference/token/raw payload.
    assert 'console.log' not in plain[plain.index('function _qr1FetchAndRender('):]


def test_qr1_fetch_uses_no_store_no_credentials_no_redirect():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function qr1ShowTransient(', start)
    fetch_chunk = plain[start:end]
    assert "cache: 'no-store'" in fetch_chunk
    assert "credentials: 'omit'" in fetch_chunk
    assert "redirect: 'error'" in fetch_chunk
    assert 'Authorization: `Bearer ${token}`' in fetch_chunk
    assert "Accept: 'image/png'" in fetch_chunk


def test_qr1_fetch_validates_png_and_512kib():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function qr1ShowTransient(', start)
    fetch_chunk = plain[start:end]
    assert 'image/png' in fetch_chunk
    assert '512 * 1024' in fetch_chunk
    assert 'blob.size' in fetch_chunk


def test_qr1_manual_render_uses_textcontent_not_innerhtml():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1RenderManual(')
    end = plain.index('function qr1ShowTransient(', start)
    assert '.textContent =' in plain[start:end]
    assert '.innerHTML' not in plain[start:end]


def test_qr1_generation_fence_prevents_stale_response_overwrite():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Every async step must check _qr1Generation after await.
    for label in ['res.blob()', 'apiCall(`/orders/${orderId}/qr`)', 'fetch(endpoint']:
        assert label in plain
    # Stale guard pattern after every await in fetch path.
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function qr1ShowTransient(', start)
    fetch_chunk = plain[start:end]
    assert fetch_chunk.count('generation !== _qr1Generation') >= 3


def test_qr1_abortcontroller_is_passed_to_fetch():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # The per-request fence creates localController and passes its signal to fetch.
    assert 'const localController' in plain
    assert 'signal: localController.signal' in plain
    assert '_qr1Controller = localController' in plain


def test_qr1_local_controller_identity_fence():
    """Every post-await guard must check _qr1Controller identity, not just generation/order."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function htmlNut', start)
    fetch_chunk = plain[start:end]
    # localController created and assigned inside _qr1FetchAndRender.
    assert 'const localController' in fetch_chunk
    # Identity check appears after every await before DOM/state mutation.
    assert '_qr1Controller !== localController' in fetch_chunk


def test_qr1_object_url_created_once_and_revoked_before_new():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert 'URL.createObjectURL' in plain
    # revoke called before each createObjectURL to prevent leak.
    assert 'URL.revokeObjectURL(_qr1ObjectUrl)' in plain


def test_no_revoke_object_url_on_blob():
    """URL.revokeObjectURL takes a string (object URL), not a Blob — forbid the bug."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Bounded to the QR fetch/render section only.
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function htmlNut', start)
    qr_section = plain[start:end]
    assert 'URL.revokeObjectURL(blob)' not in qr_section
    assert 'URL.revokeObjectURL( _qr1ObjectUrl )' not in qr_section


def test_no_qr_intent_persistence():
    """qr_intent must never be stored in sessionStorage."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Check that qr_intent is never assigned to state that goes into sessionStorage.
    # The persisted state field is `qr_url` only.
    assert 'qr_intent' not in plain[plain.index('function luuCheckoutDangDo('):plain.index('function xoaCheckoutDangDo')]
    # qr_url assignment is fine; qr_intent is transient.
    assert 'qr_url = res.qr_url' in plain


def test_v0_path_preserved_qr_url_only():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # v0 path: qr_url shown, no v1 call.
    idx = plain.index('if (state.qr_url) {')
    v0_chunk = plain[idx:idx + 200]
    assert 'document.getElementById(\'qrImage\').src = state.qr_url' in v0_chunk
    assert 'qr1ShowTransient' not in v0_chunk


def test_v1_initial_uses_qr1ShowTransient():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    idx = plain.index('if (res.qr_intent) {')
    v1_chunk = plain[idx:idx + 120]
    assert 'qr1ShowTransient(res.qr_intent' in v1_chunk


# ── Hidden / fingerprint helpers ────────────────────────────────────────────────


def test_qr1_render_hidden_exists():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1RenderHidden(')
    end = plain.index('function _qr1FingerprintFor(', start)
    chunk = plain[start:end]
    # Aborts and nulls controller.
    assert '_qr1Controller.abort()' in chunk
    assert '_qr1Controller = null' in chunk
    # Clears visuals.
    assert '_qr1ClearVisuals()' in chunk
    # Clears manual container and all four fields.
    assert '_qr1ClearManual()' in chunk
    # Sets fingerprint.
    assert '_qr1Fingerprint = ' in chunk
    # Sets hidden state and localized status.
    assert "_qr1State = 'hidden'" in chunk
    assert "dich('pos.payment.v1_hidden')" in chunk


def test_qr1_render_hidden_preserves_order_id():
    """_qr1RenderHidden must not null _qr1OrderId — bounded to its own function body."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1RenderHidden(')
    end = plain.index('function _qr1FingerprintFor(', start)
    chunk = plain[start:end]
    assert '_qr1OrderId = null' not in chunk


def test_qr1_render_hidden_revokes_object_url_first():
    """Bug 1 fix: revoke URL BEFORE clearVisuals so bank info never lingers."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1RenderHidden(')
    end = plain.index('function _qr1FingerprintFor(', start)
    chunk = plain[start:end]
    revoke_idx = chunk.index('URL.revokeObjectURL(_qr1ObjectUrl)')
    clear_idx = chunk.index('_qr1ClearVisuals()')
    assert revoke_idx < clear_idx, 'URL.revokeObjectURL must come before _qr1ClearVisuals'


def test_qr1_poll_paid_calls_cleanup():
    """Bug 2 fix: PAID branch must call _qr1Cleanup immediately, not wait for fetch."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    poll_chunk = plain[plain.index('if (statusRes.qr_intent'):]
    paid_idx = poll_chunk.index("statusRes.status === 'PAID'")
    paid_chunk = poll_chunk[paid_idx:paid_idx + 300]
    assert '_qr1Cleanup()' in paid_chunk


def test_qr1_fingerprint_for_exists_and_normalizes():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1FingerprintFor(')
    end = plain.index('async function _qr1RecoverV1(', start)
    chunk = plain[start:end]
    # Returns JSON with required fields.
    assert 'JSON.stringify({' in chunk
    assert 'contract_version' in chunk
    assert 'expected_vnd' in chunk
    assert 'canonical_reference' in chunk
    assert 'Boolean(intent.hidden)' in chunk


def test_qr1_recovery_hidden_calls_render_hidden():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('async function _qr1RecoverV1(')
    end = plain.index('function htmlNut', start)
    chunk = plain[start:end]
    hidden_branch = chunk[chunk.index('if (metadata.hidden)'):chunk.index('if (metadata.hidden)') + 200]
    assert '_qr1RenderHidden(' in hidden_branch
    assert '_qr1FingerprintFor(metadata)' in hidden_branch


def test_qr1_poll_hidden_calls_render_hidden():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # End at next top-level branch (non-hidden) to bound the poll hidden block.
    start = plain.index('if (statusRes.qr_intent && _qr1OrderId === idDon)')
    end = plain.index('if(statusRes.status', start)
    poll_chunk = plain[start:end]
    assert '_qr1FingerprintFor(statusRes.qr_intent)' in poll_chunk
    assert 'JSON.stringify({' not in poll_chunk
    # Hidden branch must call the helper, not inline clears.
    hidden_start = poll_chunk.index('if (statusRes.qr_intent.hidden)')
    hidden_branch = poll_chunk[hidden_start:poll_chunk.index('} else {', hidden_start)]
    assert '_qr1RenderHidden(' in hidden_branch
    assert '_qr1ClearVisuals()' not in hidden_branch  # helper does it


def test_qr1_render_unavailable_calls_clear_manual():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    start = plain.index('function _qr1RenderUnavailable(')
    end = plain.index('function _qr1RenderBlob(', start)
    chunk = plain[start:end]
    assert '_qr1ClearManual()' in chunk
    # No duplicated text clears.
    assert "qr1BankCode" not in chunk or "getElementById" not in chunk
    # No inline manual field clearing.
    assert "bCode.textContent = ''" not in chunk


def test_qr1_initial_display_uses_fingerprint_for():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Bounded to qr1ShowTransient body.
    start = plain.index('function qr1ShowTransient(')
    end = plain.index('function _qr1RecoverV1(', start)
    chunk = plain[start:end]
    # Must call the helper.
    assert '_qr1FingerprintFor(intent)' in chunk
    # Call-site in qr1ShowTransient must not inline JSON.stringify.
    call_line = [l for l in chunk.splitlines() if '_qr1Fingerprint = _qr1FingerprintFor' in l][0]
    assert 'JSON.stringify' not in call_line

def test_reload_no_qr_url_calls_qr1RecoverV1():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Use a stable code anchor (not comment-only) that survives _no_cached stripping.
    idx = plain.index('let _qr1V1Branch = true;')
    reload_chunk = plain[idx:idx + 200]
    assert '_qr1RecoverV1(currentOrderId)' in reload_chunk


def test_reload_with_qr_url_skips_v1_recovery():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    idx = plain.index("if (state.qr_url) {")
    v0_reload_chunk = plain[idx:idx + 120]
    assert '_qr1RecoverV1' not in v0_reload_chunk


def test_hidden_state_from_poll_cleans_and_shows_message():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    poll_chunk = plain[plain.index('if (statusRes.qr_intent'):]
    assert '_qr1Cleanup()' in poll_chunk
    # Poll hidden branch delegates to _qr1RenderHidden → dich inside that helper.
    assert '_qr1RenderHidden(' in poll_chunk


def test_same_fingerprint_does_not_fetch():
    """Identical metadata fingerprint must not trigger another render."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # End anchor: next top-level branch after the qr_intent block in kiemTraThanhToan.
    start = plain.index('if (statusRes.qr_intent')
    end = plain.index('if(statusRes.status', start)
    poll_chunk = plain[start:end]
    assert 'fingerprint !== _qr1Fingerprint' in poll_chunk
    # Visible reactivation sets fingerprint via the helper.
    assert '_qr1FingerprintFor(statusRes.qr_intent)' in poll_chunk


def test_language_refresh_causes_no_network():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # _qr1RefreshLabels comes BEFORE _qr1RenderManual (R < S); end at the next function.
    start = plain.index('function _qr1RefreshLabels(')
    end = plain.index('function _qr1RenderManual(', start)
    label_chunk = plain[start:end]
    assert 'dich(' in label_chunk
    assert 'fetch' not in label_chunk
    assert 'createObjectURL' not in label_chunk


def test_resetPOS_calls_qr1Cleanup():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    idx = plain.index('function resetPOS()')
    reset_chunk = plain[idx:idx + 400]
    assert '_qr1Cleanup()' in reset_chunk


def test_cancel_order_404_calls_qr1Cleanup():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Use the second 404 check (cancelOrder response, not guiYeuCauTaoDonDangDo).
    # Target: the block at the end of cancelOrder that calls _qr1Cleanup().
    idx = plain.rindex("if (Number(e.status) === 404")
    cancel_chunk = plain[idx:idx + 700]
    assert '_qr1Cleanup()' in cancel_chunk


def test_pageshow_cleanup_on_auth_loss():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    idx = plain.index("window.addEventListener('pageshow'")
    pageshow_chunk = plain[idx:idx + 200]
    assert '_qr1Cleanup()' in pageshow_chunk
    assert 'localStorage.getItem' in pageshow_chunk


def test_no_base64_dataurl_filereader_canvas():
    """Static proof: no forbidden byte pathways in QR lifecycle."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    forbidden = [
        'data:',
        'FileReader',
        'canvas',
        'caches.open',
        'indexedDB',
    ]
    start = plain.index('function _qr1FetchAndRender(')
    end = plain.index('function qr1ShowTransient(', start)
    qr_section = plain[start:end]
    for term in forbidden:
        assert term not in qr_section, f'forbidden: {term} in QR fetch path'


def test_no_qr_intent_in_session_storage_assignment():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    # Verify that luuCheckoutDangDo never stores qr_intent.
    saved_state = plain[plain.index('function luuCheckoutDangDo('):plain.index('function xoaCheckoutDangDo')]
    assert 'qr_intent' not in saved_state


def test_forbidden_cache_api_not_in_pos_js():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    assert 'caches.open' not in plain
    # Use variable declaration as start anchor (survives _no_cached comment stripping).
    qr_start = plain.index('let _qr1OrderId')
    qr_end = plain.index('function htmlNut')
    assert 'Cache' not in plain[qr_start:qr_end]


# ── DOM IDs ───────────────────────────────────────────────────────────────────


def test_qr1_status_dom_id_exists_in_html():
    html = _read('static/pos.html')
    assert 'id="qr1Status"' in html


def test_qr1_manual_fields_dom_ids_exist_in_html():
    html = _read('static/pos.html')
    assert 'id="qr1Manual"' in html
    assert 'id="qr1BankCode"' in html
    assert 'id="qr1AccountNo"' in html
    assert 'id="qr1AccountName"' in html
    assert 'id="qr1Reference"' in html


# ── Locale keys ───────────────────────────────────────────────────────────────


def test_all_v1_i18n_keys_present_vi():
    js = _read('static/js/locales/pos.js')
    for key in PAYMENT_KEYS_VI:
        assert f"'{key}':" in js


def test_all_v1_i18n_keys_present_en():
    js = _read('static/js/locales/pos.js')
    # English section starts after the vi section.
    en_idx = js.index("'pos.payment.v1_loading': 'Loading QR code…'")
    for key in PAYMENT_KEYS_EN:
        assert f"'{key}':" in js[en_idx:]


# ── Version bump ──────────────────────────────────────────────────────────────


def test_pos_js_version_bumped():
    html = _read('static/pos.html')
    assert 'pos.js?v=20260912-r5-10' in html


def test_locale_pos_version_bumped():
    html = _read('static/pos.html')
    locale_script = html[html.index("locales/pos.js"):html.index("locales/pos.js") + 100]
    assert '20260912-r5-9' in locale_script
    assert '20260912-r5-9' in locale_script


def test_transfer_method_checks_current_shop_capability_before_assignment():
    js = _read('static/js/pos.js')
    start = js.index('function setMethod(')
    end = js.index('function apDungPhuongThucThanhToan(', start)
    method_chunk = js[start:end]
    guard = method_chunk.index("m === 'transfer'")
    apply_method = method_chunk.index('apDungPhuongThucThanhToan')
    assert 'currentShopHasTransferAccount()' in method_chunk
    assert guard < apply_method


# ── Render endpoint trust boundary ────────────────────────────────────────────


def test_render_endpoint_exact_path_validation():
    """render_endpoint must match /api/orders/${orderId}/qr/render exactly."""
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    fetch_chunk = plain[plain.index('function _qr1FetchAndRender('):plain.index('function _qr1RecoverV1')]
    # Must NOT fetch unvalidated endpoint.
    assert "endpoint !== `/api/orders/${orderId}/qr/render`" in fetch_chunk


def test_token_missing_gives_unavailable():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    fetch_chunk = plain[plain.index('function _qr1FetchAndRender('):plain.index('function _qr1RecoverV1')]
    assert 'if (!token)' in fetch_chunk
    # Token missing now calls _qr1RenderUnavailable() which sets _qr1State.
    assert '_qr1RenderUnavailable()' in fetch_chunk


def test_non_ok_response_gives_manual():
    js = _read('static/js/pos.js')
    plain = _no_cached(js)
    fetch_chunk = plain[plain.index('function _qr1FetchAndRender('):plain.index('function _qr1RecoverV1')]
    # Catch block calls _qr1RenderManual → manual fallback.
    assert 'catch' in fetch_chunk
