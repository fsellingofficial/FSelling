"""I09-F2: durable offline receipt sync client contract v1."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_node_fake_indexeddb_sync_harness():
    """ACK/retry/auth/reclaim/restart/stale-lock contracts execute in Node."""

    completed = subprocess.run(
        ["node", "tests/js/offline-ban-sync-v1.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "offline-ban-sync-v1 harness: 8 passed" in completed.stdout


def test_sync_uses_dedicated_transport_and_exact_lease_header():
    js = _read("static/js/offline-ban.js")
    start = js.index("// ---------- Sync engine v1 (I09-F2) ----------")
    end = js.index("// ---------- Contract v0 giữ nguyên ----------")
    sync = js[start:end]
    assert "global.getToken" in sync
    assert "X-Offline-Lease-Token" in sync
    assert "apiCall(" not in sync
    assert "clearAuthState" not in sync
    assert "redirectToLogin" not in sync


def test_v1_sync_is_wired_without_replacing_v0_sync():
    js = _read("static/js/pos.js")
    assert "OfflineBan.batTuDongBoV1(" in js
    assert "OfflineBan.batTuDongBo(" in js
    html = _read("static/pos.html")
    assert "/js/offline-ban.js?v=20260813-i09-f3&g=20260813-i09-g2h6" in html
    assert "/js/pos.js?v=20260912-r5-9" in html


def test_node_confirmation_stays_above_recovery_and_keeps_focus():
    """Nested recovery confirmation is a real top-layer DOM interaction."""
    script = r'''const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('static/js/pos.js','utf8');
const html=fs.readFileSync('static/pos.html','utf8'),css=fs.readFileSync('static/css/pos.css','utf8');
assert.match(html,/id="xacNhanModal"[^>]*role="dialog"[^>]*aria-modal="true"/);
assert.match(html,/aria-labelledby="xnTieuDe"[^>]*aria-describedby="xnNoiDung"/);
assert.match(css,/#xacNhanModal\.pos-confirm-modal\s*\{[\s\S]*?z-index:\s*10040/);
assert.match(css,/\.pos-modal\s*\{[\s\S]*?z-index:\s*10020/);
class E { constructor(id,d){this.id=id;this.d=d;this.style={};this.attrs={};this.isConnected=true;this.disabled=false;this.innerText='';} focus(){this.d.activeElement=this;} setAttribute(k,v){this.attrs[k]=String(v);} }
const d={activeElement:null,listeners:{},getElementById(id){return this.e[id];},addEventListener(t,f,c){this.listeners[t]={f,c};},removeEventListener(t){delete this.listeners[t];}};
d.e=Object.fromEntries(['xacNhanModal','xnDongY','xnHuy','xnTieuDe','xnNoiDung','trigger'].map(id=>[id,new E(id,d)]));
const start=source.indexOf('function xacNhan('),end=source.indexOf('\nlet paymentPollingInterval',start); assert(start>=0&&end>start);
const ctx={document:d}; vm.createContext(ctx); vm.runInContext(source.slice(start,end)+';this.x=xacNhan;',ctx);
(async()=>{const {trigger,xacNhanModal:modal,xnDongY:ok,xnHuy:cancel}=d.e; trigger.focus(); const yes=ctx.x('t','b'); assert.equal(modal.style.display,'flex'); assert.equal(d.listeners.keydown.c,true); let tab=false; d.listeners.keydown.f({key:'Tab',shiftKey:false,preventDefault(){tab=true;},stopImmediatePropagation(){}}); assert(tab&&d.activeElement===cancel); ok.onclick(); assert.equal(await yes,true); assert.equal(d.activeElement,trigger); trigger.focus(); const no=ctx.x('t','b'); let esc=false; d.listeners.keydown.f({key:'Escape',preventDefault(){esc=true;},stopImmediatePropagation(){}}); assert(esc); assert.equal(await no,false); assert.equal(modal.style.display,'none'); assert.equal(d.activeElement,trigger); console.log('confirmation DOM: 1 passed');})().catch(e=>{console.error(e);process.exitCode=1;});'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "confirmation DOM: 1 passed" in completed.stdout


def test_v1_state_and_lock_contract_are_explicit():
    js = _read("static/js/offline-ban.js")
    for state in (
        "DRAFT",
        "READY",
        "SYNCING",
        "ACKED",
        "RETRYABLE",
        "BLOCKED_RECOVERABLE",
        "QUARANTINED",
    ):
        assert f"'{state}'" in js
    assert "sync_lock_v1" in js
    assert "owner_tab_id" in js
    assert "fence" in js
    assert "expires_at" in js
    assert "SYNC_LOCK_HEARTBEAT_MS = 5 * 1000" in js
    assert "SYNC_LOCK_TTL_MS = 15 * 1000" in js
    assert "SYNC_STALE_MS = 60 * 1000" in js
