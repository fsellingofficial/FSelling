'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
async function main() {
    const timers = new Map(); let next = 0;
    const storage = new Map();
    const context = {
        console, FormData, AbortController,
        localStorage: { getItem: k => storage.get(k) || null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) },
        sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        document: { getElementById: () => null, addEventListener() {} },
        location: { pathname: '/pos', replace() {} }, addEventListener() {},
        setInterval() {}, setTimeout: (fn, ms) => { timers.set(++next, {fn, ms}); return next; },
        clearTimeout: id => timers.delete(id), t: k => k
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('static/js/api.js','utf8'), context);
    const body = { operation_id: 'r5-op', quantity: 1 };
    const reply = (status, raw) => ({ status, ok: status < 400, headers: {get:()=>null}, text: async () => raw });
    context.fetch = async () => reply(200, '{');
    await assert.rejects(context.apiCall('/orders/1','POST',body), e => e.mutationOutcomeUnknown && e.operationId === 'r5-op' && e.draft === body);
    context.fetch = async () => reply(503, '{"detail":"unavailable"}');
    await assert.rejects(context.apiCall('/orders/1','POST',body), e => e.status === 503 && e.mutationOutcomeUnknown);
    for (const status of [403,409]) {
        context.fetch = async () => reply(status, '{"detail":{"code":"REJECTED","message":"no"}}');
        await assert.rejects(context.apiCall('/orders/1','POST',body), e => e.status === status && e.code === 'REJECTED' && !e.mutationOutcomeUnknown);
    }
    for (const method of ['GET','POST']) {
        let signal; let slow = 0;
        context.fetch = async (_url, options) => { signal = options.signal; return {...reply(200,''), text: () => new Promise(()=>{})}; };
        const pending = context.apiCall('/orders/1',method,method === 'POST' ? body : null, { timeoutMs: 15000, slowAfterMs: 10000, onSlow: () => slow++ });
        const rejected = assert.rejects(pending, e => e.code === 'CLIENT_TIMEOUT' && !e.status && Boolean(e.mutationOutcomeUnknown) === (method === 'POST'));
        await Promise.resolve(); await Promise.resolve();
        assert.equal(timers.size,2);
        [...timers.values()].find(x=>x.ms===10000).fn();
        assert.equal(slow,1);
        [...timers.values()].find(x=>x.ms===15000).fn();
        await rejected;
        assert.equal(signal.aborted,true);
        assert.equal(timers.size,0);
    }
    context.fetch = async () => reply(200,'{"ok":true}');
    assert.equal((await context.apiCall('/orders','GET',null,{timeoutMs:15000})).ok,true);
    assert.equal(timers.size,0);
    context.fetch = async () => { throw Error('network'); };
    await assert.rejects(context.apiCall('/auth/login','POST',{password:'secret'}), e => e.draft === null);
    await assert.rejects(context.apiCall('/fnb/cancel','POST',{operation_id:'r5-op',approval_token:'secret'}), e => e.draft === null && e.operationId === 'r5-op');
    let cleared = 0, redirected = 0;
    context.clearAuthState = async () => cleared++;
    context.redirectToLogin = () => redirected++;
    context.fetch = async () => ({...reply(401,''), text: async () => { throw Error('body lost'); }});
    await assert.rejects(context.apiCall('/orders','POST',body), e => e.status === 401 && e.mutationOutcomeUnknown);
    assert.equal(cleared, 1);
    assert.equal(redirected, 1);
    console.log('api request r5: passed');
}
main().catch(e=>{ console.error(e); process.exitCode=1; });
