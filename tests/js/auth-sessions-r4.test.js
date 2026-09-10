'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

async function main() {
    const source = fs.readFileSync('static/js/session-device-r4.js', 'utf8');
    const authSource = fs.readFileSync('static/js/auth.js', 'utf8');
    const apiSource = fs.readFileSync('static/js/api.js', 'utf8');
    const storage = new Map();
    const listeners = new Map();
    let uuidCalls = 0;

    global.window = global;
    global.localStorage = {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, String(value)),
        removeItem: key => storage.delete(key)
    };
    Object.defineProperty(global, 'crypto', {
        value: { randomUUID: () => `device-${++uuidCalls}` }, configurable: true
    });
    Object.defineProperty(global, 'navigator', {
        value: { userAgent: 'Desktop Browser', platform: 'Test OS' }, configurable: true
    });
    global.innerWidth = 1400;
    global.location = { pathname: '/', href: '', replace: () => {} };
    global.document = {
        readyState: 'loading',
        addEventListener: (name, fn) => listeners.set(name, fn)
    };
    global.t = key => key;

    vm.runInThisContext(source, { filename: 'static/js/session-device-r4.js' });
    assert.equal(getOrCreateAuthDeviceId(), 'device-1');
    assert.equal(getOrCreateAuthDeviceId(), 'device-1');
    assert.equal(uuidCalls, 1);
    assert.deepEqual(buildAuthDeviceMetadata(), {
        device_id: 'device-1',
        device_name: 'DESKTOP · Test OS',
        device_type: 'DESKTOP'
    });
    assert.match(authSource, /buildAuthDeviceMetadata\(\)/);
    assert.match(authSource, /cacheAuthSessionSummary\(data\.session\)/);
    assert.doesNotMatch(source, /\.innerHTML\s*=/);
    assert.doesNotMatch(source, /global\.prompt/);
    assert.doesNotMatch(source, /global\.confirm/);
    assert.match(source, /session-device-confirm/);
    assert.match(source, /textContent\s*=\s*session\.device_name/);

    storage.set('token', 'token-a');
    storage.set('username', 'alice');
    storage.set('role', 'SELLER');
    const requestState = [];
    global.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    global.FormData = class FormData {};
    global.fetch = async (_url, options) => {
        requestState.push({ method: options.method, tokenPresent: storage.has('token') });
        throw new Error('offline');
    };
    global.addEventListener = () => {};
    global.setInterval = () => 1;
    global.performance = { now: () => 1 };
    global.OfflineBan = { sealIdentityV1: async () => {} };
    global.document.getElementById = () => null;

    vm.runInThisContext(apiSource, { filename: 'static/js/api.js' });
    const draft = { operation_id: 'op-1', amount: 1000 };
    await assert.rejects(
        apiCall('/orders/1/pay', 'POST', draft),
        error => error.mutationOutcomeUnknown === true
            && error.operationId === 'op-1'
            && error.draft === draft
    );
    await assert.rejects(
        apiCall('/auth/login', 'POST', { username: 'alice', password: 'secret' }),
        error => error.mutationOutcomeUnknown === true
            && error.operationId === null
            && error.draft === null
    );
    await logout();
    assert.deepEqual(requestState.at(-1), { method: 'POST', tokenPresent: true });
    assert.equal(storage.has('token'), false);

    console.log('auth sessions r4 harness: 1 passed');
}

main().catch(error => {
    console.error(error && error.stack || error);
    process.exit(1);
});
