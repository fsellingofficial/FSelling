'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

async function main() {
    const authSource = fs.readFileSync('static/js/auth.js', 'utf8');
    assert.match(
        authSource,
        /data\.role === 'STAFF' && data\.staff_role === 'SERVICE'[\s\S]*navigateToPage\('\/fnb'\)/
    );
    assert(
        authSource.indexOf("data.staff_role === 'SERVICE'")
            < authSource.indexOf("data.staff_role === 'CASHIER'")
    );

    const storage = new Map([
        ['token', 'token-a'],
        ['username', 'alice'],
        ['role', 'SELLER']
    ]);
    const listeners = new Map();
    const redirects = [];
    const seals = [];

    global.window = global;
    global.localStorage = {
        getItem: key => storage.has(key) ? storage.get(key) : null,
        setItem: (key, value) => storage.set(key, String(value)),
        removeItem: key => storage.delete(key),
        key: index => Array.from(storage.keys())[index] ?? null,
        get length() { return storage.size; }
    };
    global.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    global.document = {
        readyState: 'loading',
        addEventListener: (name, callback) => listeners.set('document:' + name, callback),
        getElementById: () => null
    };
    global.location = {
        pathname: '/',
        replace: value => redirects.push(value),
        href: ''
    };
    global.addEventListener = (name, callback) => listeners.set(name, callback);
    global.setInterval = () => 1;
    global.t = key => key;
    global.OfflineBan = {
        sealIdentityV1: async identity => {
            seals.push({ username: identity.username, usernameAtSeal: storage.get('username') });
        }
    };

    vm.runInThisContext(fs.readFileSync('static/js/api.js', 'utf8'), {
        filename: 'static/js/api.js'
    });

    // Same-tab A -> B: A is sealed before auth.js overwrites username with B.
    await prepareAuthIdentityChangeV1('bob');
    assert.deepEqual(seals.shift(), { username: 'alice', usernameAtSeal: 'alice' });
    localStorage.setItem('username', 'bob');
    localStorage.setItem('token', 'token-b');

    // Cross-tab: storage already contains B when A's document receives the event.
    localStorage.setItem('username', 'alice');
    localStorage.setItem('token', 'token-a');
    storage.set('username', 'bob');
    storage.set('token', 'token-b');
    await listeners.get('storage')({ key: 'token', newValue: 'token-b' });
    assert.deepEqual(seals.shift(), { username: 'alice', usernameAtSeal: 'bob' });
    assert.equal(redirects.length, 1);

    // A non-POS page has no OfflineBan module: logout must retain a durable marker.
    localStorage.setItem('username', 'alice');
    localStorage.setItem('token', 'token-a');
    delete global.OfflineBan;
    await logout();
    const markerKey = 'fselling.offline-seal.v1:' + encodeURIComponent('alice');
    const marker = JSON.parse(storage.get(markerKey));
    assert.equal(marker.username, 'alice');
    assert.equal(typeof marker.generation, 'string');
    assert.equal(storage.get(markerKey).includes('token-a'), false);
    assert.equal(storage.has('token'), false);
    assert.equal(storage.has('username'), false);

    console.log('auth-offline-seal harness: 1 passed');
}

main().catch(error => {
    console.error(error && error.stack || error);
    process.exit(1);
});
