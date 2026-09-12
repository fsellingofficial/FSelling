"""I09-F1: durable client receipt/persistence contract v1."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v1


ROOT = Path(__file__).resolve().parents[1]
KNOWN_VECTOR = (
    "fsofr1:8fdaa734e0088f5b23c40ad1d411f56e07836fb4114613400230762f54320e22"
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_node_fake_indexeddb_persistence_harness():
    """Executable v1 upgrade/identity/sequence/crash/fingerprint coverage."""

    completed = subprocess.run(
        ["node", "tests/js/offline-ban-v1.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "offline-ban-v1 harness: 2 passed" in completed.stdout


def test_node_auth_identity_seal_harness():
    """Old identity is sealed across same-tab, cross-tab and unloaded module."""

    completed = subprocess.run(
        ["node", "tests/js/auth-offline-seal.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "auth-offline-seal harness: 1 passed" in completed.stdout


def test_server_known_vector_matches_executable_client_vector():
    """The Node harness asserts the same constant from independent JS bytes."""

    result = fingerprint_offline_receipt_v1(
        shop_id=1,
        sold_at_client_utc="2025-07-15 09:30:00.123456",
        client_monotonic_ms=5000,
        monotonic_valid=True,
        server_anchor_id="anc_abc123",
        lease_id="lease-001",
        device_id="dev-001",
        offline_session_id="session-001",
        sequence=1,
        offline_uuid="uuid-test-001",
        catalog_version=1,
        catalog_snapshot_digest="sha256:deadbeef",
        items=[
            {
                "product_id": 10,
                "product_name": "Su\u0301a  ",
                "unit_price_vnd": 15000,
                "quantity": 2,
            },
            {
                "product_id": 5,
                "product_name": "Ba\u0301nh",
                "unit_price_vnd": 25000,
                "quantity": 1,
            },
        ],
        cash_tendered_vnd=55000,
    )
    assert result.digest == KNOWN_VECTOR


def test_pos_awaits_durable_v1_or_v0_before_cleanup():
    pos = _read("static/js/pos.js")
    start = pos.index("async function luuBanOffline(")
    end = pos.index("async function thuTaoDonDangDo(")
    body = pos[start:end]
    write = body.index("await OfflineBan.luuPhieuTuPOS(")
    cleanup = body.index("xoaCheckoutDangDo()")
    assert write < cleanup
    assert "payment_method: state.payment_method" in body
    assert "voucher_code: payload.voucher_code" in body
    assert "loyalty_points_to_use:" in body


def test_all_touched_static_files_have_i09_g2h_cache_buster():
    html_files = sorted((ROOT / "static").glob("*.html"))
    api_consumers = [path for path in html_files if '/js/api.js?v=' in path.read_text(encoding="utf-8")]
    assert {path.name for path in api_consumers} == {
        "admin.html", "fnb-station.html", "fnb.html", "index.html", "pos.html",
        "register.html", "seller.html", "verify.html"
    }
    for path in api_consumers:
        assert 'g=20260813-i09-g2h2' in path.read_text(encoding="utf-8")

    index = _read("static/index.html")
    pos = _read("static/pos.html")
    assert '/js/auth.js?v=20260901-fnb-r1b' in index
    assert '/js/offline-ban.js?v=20260813-i09-f3&g=20260813-i09-g2h6' 
    assert '/js/pos.js?v=20260912-r5-9' in pos


def test_capability_refresh_is_independent_of_catalog_success_and_reconnects():
    pos = _read("static/js/pos.js")
    assert "async function taiChinhSachOfflinePOS(force = false)" in pos
    assert "taiChinhSachOfflinePOS();\nloadShop();" in pos
    assert "await taiChinhSachOfflinePOS();\n        await Promise.all([" in pos
    assert "await taiChinhSachOfflinePOS();\n    await Promise.all([" in pos
    assert "window.addEventListener('online', async () => {" in pos
    assert "await taiChinhSachOfflinePOS(true);\n        await capNhatTrangThaiMangPOS();" in pos
    script = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const s=fs.readFileSync('static/js/pos.js','utf8');
const a=s.indexOf('async function taiChinhSachOfflinePOS(force = false)'),b=s.indexOf('\nasync function loadLoyaltyProgram()',a);
assert(a>=0&&b>a); let calls=0; const ctx={currentShopId:7,localStorage:{getItem:k=>k==='username'?'owner':''},window:{OfflineBan:{dangOffline:()=>false,refreshContractPolicy:async value=>{calls++;assert.deepEqual(value,{shop_id:7,username:'owner'});}}}};
vm.createContext(ctx);vm.runInContext(s.slice(a,b)+';this.run=taiChinhSachOfflinePOS;',ctx);
(async()=>{await ctx.run();assert.equal(calls,1);ctx.window.OfflineBan.dangOffline=()=>true;await ctx.run();assert.equal(calls,1);console.log('pos capability refresh: 1 passed');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "pos capability refresh: 1 passed" in completed.stdout


def test_phase_b_unknown_policy_never_persists_v0_in_production_js_fake_idb():
    """Regression for the reviewed empty-cache Phase-B legacy fallback hole."""
    script = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const {FakeIndexedDB}=require('./tests/js/offline-ban-v1.test.js');
const fake=new FakeIndexedDB(); fake.seed('fselling-offline',1,{phieu:{keyPath:'offline_uuid',indexes:[{name:'shop_id',keyPath:'shop_id',unique:false}],rows:[]},anhchup:{keyPath:'khoa',rows:[]}});
global.window=global; global.indexedDB=fake; global.crypto=require('crypto').webcrypto; Object.defineProperty(global,'navigator',{configurable:true,value:{onLine:false}});
const storage=new Map([['username','owner']]); global.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k),key:i=>Array.from(storage.keys())[i]||null,get length(){return storage.size;}}; global.addEventListener=()=>{};
let calls=0,mode='PHASE_A'; global.apiCall=async endpoint=>{assert.equal(endpoint,'/offline/capability');calls++;if(mode==='FAIL')throw new Error('network');const contradictory=mode==='CONTRADICT';const phase=contradictory?'PHASE_B':mode;return {supported_versions:[0,1],minimum_accepted_version:mode==='PHASE_B'?1:0,phase,cutoff_at_utc:phase==='PHASE_B'?'2026-08-01T00:00:00Z':null,policy_code:phase==='PHASE_B'?'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM':'OFFLINE_CONTRACT_PHASE_A_V0_V1'};};
vm.runInThisContext(fs.readFileSync('static/js/offline-ban.js','utf8'),{filename:'static/js/offline-ban.js'}); const api=global.OfflineBan;
const receipt={shop_id:1,username:'owner',payment_method:'cash',voucher_code:null,loyalty_points_to_use:0,cash_tendered:100000,device_label:'test',items:[{product_id:2,product_name:'Legacy',price:100000,quantity:1}]};
(async()=>{await assert.rejects(api.luuPhieuTuPOS({...receipt,creation_key:'policy-unknown'}),e=>e.code==='OFFLINE_CONTRACT_POLICY_REQUIRED');assert.equal(calls,0);assert.equal(fake.inspect('fselling-offline','phieu').length,0);mode='CONTRADICT';navigator.onLine=true;assert.equal(await api.refreshContractPolicy({shop_id:1,username:'owner'}),null);navigator.onLine=false;await assert.rejects(api.luuPhieuTuPOS({...receipt,creation_key:'policy-contradict'}),e=>e.code==='OFFLINE_CONTRACT_POLICY_REQUIRED');assert.equal(fake.inspect('fselling-offline','phieu').length,0);mode='PHASE_A';navigator.onLine=true;await api.refreshContractPolicy({shop_id:1,username:'owner'});assert.equal(calls,2);navigator.onLine=false;await api.luuPhieuTuPOS({...receipt,creation_key:'policy-phase-a'});assert.equal(fake.inspect('fselling-offline','phieu').length,1);mode='PHASE_B';navigator.onLine=true;await api.refreshContractPolicy({shop_id:1,username:'owner'},true);navigator.onLine=false;await assert.rejects(api.luuPhieuTuPOS({...receipt,creation_key:'policy-phase-b'}),e=>e.code==='OFFLINE_CONTRACT_V1_REQUIRED');assert.equal(fake.inspect('fselling-offline','phieu').length,1);assert((await api.localReceiptsForRecovery({shop_id:1,username:'owner'})).some(x=>x.contract_version===0));mode='CONTRADICT';navigator.onLine=true;assert.equal((await api.refreshContractPolicy({shop_id:1,username:'owner'},true)).minimum_accepted_version,1);mode='PHASE_A';assert.equal((await api.refreshContractPolicy({shop_id:1,username:'owner'},true)).minimum_accepted_version,1);mode='FAIL';assert.equal((await api.refreshContractPolicy({shop_id:1,username:'owner'},true)).minimum_accepted_version,1);navigator.onLine=false;await assert.rejects(api.luuPhieuTuPOS({...receipt,shop_id:2,creation_key:'policy-foreign'}),e=>e.code==='OFFLINE_CONTRACT_POLICY_REQUIRED');console.log('phase policy fake-idb: 1 passed');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "phase policy fake-idb: 1 passed" in completed.stdout


def test_cross_tab_phase_b_policy_write_is_durable_monotonic():
    """A delayed tab cannot overwrite a Phase-B row written by another tab."""
    script = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const {FakeIndexedDB}=require('./tests/js/offline-ban-v1.test.js');
const source=fs.readFileSync('static/js/offline-ban.js','utf8');
const A={supported_versions:[0,1],minimum_accepted_version:0,phase:'PHASE_A',cutoff_at_utc:null,policy_code:'OFFLINE_CONTRACT_PHASE_A_V0_V1'};
const B={supported_versions:[0,1],minimum_accepted_version:1,phase:'PHASE_B',cutoff_at_utc:'2020-01-15T00:00:00Z',policy_code:'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM'};
const B2={...B,cutoff_at_utc:'2020-01-16T00:00:00Z'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn){for(let i=0;i<30;i++){if(fn())return;await tick();}throw new Error('mock request did not start');}
function fresh(){const fake=new FakeIndexedDB();fake.seed('fselling-offline',1,{phieu:{keyPath:'offline_uuid',indexes:[{name:'shop_id',keyPath:'shop_id',unique:false}],rows:[{offline_uuid:'existing-v0',shop_id:1,sold_at:'2026-01-01T00:00:00Z',items:[],cash_tendered:0}]},anhchup:{keyPath:'khoa',rows:[]}});return fake;}
function tab(fake,provider){const storage=new Map([['username','owner']]);const ctx={console,Date,Promise,TextEncoder,structuredClone,crypto:require('crypto').webcrypto,indexedDB:fake,navigator:{onLine:true},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k),key:i=>Array.from(storage.keys())[i]||null,get length(){return storage.size;}},addEventListener:()=>{},setTimeout,clearTimeout};ctx.window=ctx;ctx.apiCall=provider;vm.createContext(ctx);vm.runInContext(source,ctx,{filename:'static/js/offline-ban.js'});return ctx;}
async function race(resolveOrder,simultaneous=false){const fake=fresh();let callsA=0,callsB=0,resolveA,resolveB;const gateA=new Promise(r=>resolveA=r),gateB=new Promise(r=>resolveB=r);const a=tab(fake,async()=>{callsA++;return gateA;}),b=tab(fake,async()=>{callsB++;return gateB;});const ctx={shop_id:1,username:'owner'};const pa=a.OfflineBan.refreshContractPolicy(ctx,true),pb=b.OfflineBan.refreshContractPolicy(ctx,true);await until(()=>callsA===1&&callsB===1);if(simultaneous){resolveA(A);resolveB(B);await Promise.all([pa,pb]);}else if(resolveOrder==='BA'){resolveB(B);await pb;resolveA(A);await pa;}else{resolveA(A);await pa;resolveB(B);await pb;}assert.equal(callsA,1);assert.equal(callsB,1);assert.equal((await a.OfflineBan.cachedContractPolicy(ctx)).minimum_accepted_version,1);assert.equal((await b.OfflineBan.cachedContractPolicy(ctx)).minimum_accepted_version,1);b.navigator.onLine=false;const receipt={shop_id:1,username:'owner',payment_method:'cash',voucher_code:null,loyalty_points_to_use:0,cash_tendered:100000,device_label:'race',items:[{product_id:2,product_name:'Legacy',price:100000,quantity:1}]};await assert.rejects(b.OfflineBan.luuPhieuTuPOS({...receipt,creation_key:`race-${resolveOrder}-${simultaneous}`}),e=>e.code==='OFFLINE_CONTRACT_V1_REQUIRED');assert.equal(fake.inspect('fselling-offline','phieu').length,1);assert((await b.OfflineBan.localReceiptsForRecovery(ctx)).some(x=>x.contract_version===0&&x.offline_uuid==='existing-v0'));return {fake,a,b};}
(async()=>{const phaseAFake=fresh(),phaseACtx={shop_id:1,username:'owner'},phaseATabA=tab(phaseAFake,async()=>A),phaseATabB=tab(phaseAFake,async()=>A);await Promise.all([phaseATabA.OfflineBan.refreshContractPolicy(phaseACtx,true),phaseATabB.OfflineBan.refreshContractPolicy(phaseACtx,true)]);assert.equal((await phaseATabA.OfflineBan.cachedContractPolicy(phaseACtx)).minimum_accepted_version,0);assert.equal((await phaseATabB.OfflineBan.cachedContractPolicy(phaseACtx)).minimum_accepted_version,0);const exact=await race('BA');await race('AB');await race('AB',true);const ctx={shop_id:1,username:'owner'};let phase=A;const sameA=tab(exact.fake,async()=>phase),sameB=tab(exact.fake,async()=>phase);const other={shop_id:2,username:'owner'};const otherTab=tab(exact.fake,async()=>A);await otherTab.OfflineBan.refreshContractPolicy(other,true);assert.equal((await otherTab.OfflineBan.cachedContractPolicy(other)).minimum_accepted_version,0);assert.equal((await exact.a.OfflineBan.cachedContractPolicy(ctx)).minimum_accepted_version,1);phase=B2;assert.equal((await sameA.OfflineBan.refreshContractPolicy(ctx,true)).cutoff_at_utc,B2.cutoff_at_utc);assert.equal((await sameB.OfflineBan.refreshContractPolicy(ctx,true)).minimum_accepted_version,1);phase={...B,minimum_accepted_version:0};assert.equal((await sameA.OfflineBan.refreshContractPolicy(ctx,true)).minimum_accepted_version,1);phase=()=>{throw new Error('network')};sameB.apiCall=async()=>phase();assert.equal((await sameB.OfflineBan.refreshContractPolicy(ctx,true)).minimum_accepted_version,1);console.log('cross-tab policy CAS: 1 passed');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "cross-tab policy CAS: 1 passed" in completed.stdout


def test_cross_tab_request_start_fence_rejects_stale_response_but_allows_new_rollback():
    """The durable request-start generation, not cache age, owns a response."""
    script = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const {FakeIndexedDB}=require('./tests/js/offline-ban-v1.test.js');
const source=fs.readFileSync('static/js/offline-ban.js','utf8');
const A={supported_versions:[0,1],minimum_accepted_version:0,phase:'PHASE_A',cutoff_at_utc:null,policy_code:'OFFLINE_CONTRACT_PHASE_A_V0_V1'};
const B={supported_versions:[0,1],minimum_accepted_version:1,phase:'PHASE_B',cutoff_at_utc:'2020-01-15T00:00:00Z',policy_code:'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM'};
let now=Date.parse('2026-01-01T00:00:00Z');class ClockDate extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const tick=()=>new Promise(resolve=>setImmediate(resolve));async function until(fn){for(let i=0;i<30;i++){if(fn())return;await tick();}throw new Error('mock request did not start');}
function fresh(){const fake=new FakeIndexedDB();fake.seed('fselling-offline',1,{phieu:{keyPath:'offline_uuid',indexes:[{name:'shop_id',keyPath:'shop_id',unique:false}],rows:[{offline_uuid:'existing-v0',shop_id:1,sold_at:'2026-01-01T00:00:00Z',items:[],cash_tendered:0}]},anhchup:{keyPath:'khoa',rows:[]}});return fake;}
function tab(fake,provider,username='owner'){const storage=new Map([['username',username]]);const ctx={console,Date:ClockDate,Promise,TextEncoder,structuredClone,crypto:require('crypto').webcrypto,indexedDB:fake,navigator:{onLine:true},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k),key:i=>Array.from(storage.keys())[i]||null,get length(){return storage.size;}},addEventListener:()=>{},setTimeout,clearTimeout};ctx.window=ctx;ctx.apiCall=provider;vm.createContext(ctx);vm.runInContext(source,ctx,{filename:'static/js/offline-ban.js'});return ctx;}
function policyRow(fake,shop=1,user='owner'){return fake.inspect('fselling-offline','meta_v1').find(row=>row.key===`offline-contract-policy-v1:${shop}:${encodeURIComponent(user)}`);}
const receipt={shop_id:1,username:'owner',payment_method:'cash',voucher_code:null,loyalty_points_to_use:0,cash_tendered:100000,device_label:'fence',items:[{product_id:2,product_name:'Legacy',price:100000,quantity:1}]};
(async()=>{const fake=fresh(),ctx={shop_id:1,username:'owner'};let callsA=0,resolveA;const delayed=new Promise(resolve=>resolveA=resolve);const a=tab(fake,async()=>{callsA++;return delayed}),b=tab(fake,async()=>B);const old=a.OfflineBan.refreshContractPolicy(ctx,true);await until(()=>callsA===1);assert.equal((await b.OfflineBan.refreshContractPolicy(ctx,true)).minimum_accepted_version,1);now+=25*60*60*1000;resolveA(A);assert.equal(await old,null);assert.equal(policyRow(fake).policy.minimum_accepted_version,1);b.navigator.onLine=false;await assert.rejects(b.OfflineBan.luuPhieuTuPOS({...receipt,creation_key:'old-pre-b'}),e=>e.code==='OFFLINE_CONTRACT_POLICY_REQUIRED');assert.equal(fake.inspect('fselling-offline','phieu').length,1);const rollback=tab(fake,async()=>A);assert.equal((await rollback.OfflineBan.refreshContractPolicy(ctx,true)).minimum_accepted_version,0);assert.equal(policyRow(fake).policy.minimum_accepted_version,0);rollback.navigator.onLine=false;await rollback.OfflineBan.luuPhieuTuPOS({...receipt,creation_key:'new-approved-rollback'});assert.equal(fake.inspect('fselling-offline','phieu').length,2);const failed=fresh();let resolveOld,oldCalls=0;const oldGate=new Promise(resolve=>resolveOld=resolve);const oldTab=tab(failed,async()=>{oldCalls++;return oldGate}),invalidTab=tab(failed,async()=>({...B,minimum_accepted_version:0}));const oldCtx={shop_id:1,username:'owner'};const oldResponse=oldTab.OfflineBan.refreshContractPolicy(oldCtx,true);await until(()=>oldCalls===1);assert.equal(await invalidTab.OfflineBan.refreshContractPolicy(oldCtx,true),null);resolveOld(A);assert.equal(await oldResponse,null);assert.equal(policyRow(failed),undefined);const networkTab=tab(failed,async()=>{throw new Error('network')});assert.equal(await networkTab.OfflineBan.refreshContractPolicy(oldCtx,true),null);const newest=tab(failed,async()=>B);assert.equal((await newest.OfflineBan.refreshContractPolicy(oldCtx,true)).minimum_accepted_version,1);assert.equal(policyRow(failed).policy.minimum_accepted_version,1);console.log('request-start policy fence: 1 passed');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "request-start policy fence: 1 passed" in completed.stdout


def test_public_capability_coherence_matrix_is_strict():
    script = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('static/js/offline-ban.js','utf8');
const start=source.indexOf('function publicCutoffTimestamp('),end=source.indexOf('\n    function policyContext(',start);assert(start>=0&&end>start);
const ctx={Date,POLICY_CODE_PHASE_A:'OFFLINE_CONTRACT_PHASE_A_V0_V1',POLICY_CODE_PHASE_B:'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM'};vm.createContext(ctx);vm.runInContext(source.slice(start,end)+';this.parse=boundedPolicy;',ctx);
const A={supported_versions:[0,1],minimum_accepted_version:0,phase:'PHASE_A',cutoff_at_utc:null,policy_code:'OFFLINE_CONTRACT_PHASE_A_V0_V1'};
const B={supported_versions:[0,1],minimum_accepted_version:1,phase:'PHASE_B',cutoff_at_utc:'2020-01-15T00:00:00Z',policy_code:'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM'};
assert(ctx.parse(A));assert(ctx.parse({...A,cutoff_at_utc:'2030-01-15T00:00:00.123Z'}));assert(ctx.parse(B));
for(const bad of [
 {...B,minimum_accepted_version:0}, {...A,minimum_accepted_version:1},
 {...B,policy_code:A.policy_code}, {...A,policy_code:B.policy_code},
 {...A,supported_versions:[1,0]}, {...A,supported_versions:[0,1,2]}, {...A,supported_versions:['0','1']},
 {...B,cutoff_at_utc:null}, {...B,cutoff_at_utc:'not-a-time'}, {...B,cutoff_at_utc:'2020-02-31T00:00:00Z'}, {...B,cutoff_at_utc:'2999-01-01T00:00:00Z'},
 {...A,phase:'UNKNOWN'}, {...A,phase:null}, {...A,policy_code:'UNKNOWN'}, {...A,cutoff_at_utc:5},
 {...A,cutoff_at_utc:undefined},
 {minimum_accepted_version:0,phase:'PHASE_A',cutoff_at_utc:null,policy_code:A.policy_code}
]) assert.equal(ctx.parse(bad),null,JSON.stringify(bad));
console.log('capability coherence matrix: 1 passed');
'''
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "capability coherence matrix: 1 passed" in completed.stdout


def test_same_tab_login_seals_previous_identity_before_overwrite():
    auth = _read("static/js/auth.js")
    seal = auth.index("await prepareAuthIdentityChangeV1(username)")
    token_write = auth.index("localStorage.setItem('token', data.access_token)")
    username_write = auth.index("localStorage.setItem('username', username)")
    assert seal < token_write < username_write


def test_f1_does_not_add_v1_sync_or_touch_v0_payload_shape():
    js = _read("static/js/offline-ban.js")
    v0_sync = js[js.index("async function dongBo(") : js.index("function luuAnhChupSanPham(")]
    assert "receipt_v1" not in v0_sync
    assert "X-Offline-Lease-Token" not in v0_sync
    assert "offline_contract_version" not in v0_sync
    assert "client_fingerprint" not in v0_sync
