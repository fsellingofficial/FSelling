'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

function clone(value) {
    return value === undefined ? undefined : structuredClone(value);
}

function list(names) {
    const values = Array.from(names);
    values.contains = value => values.includes(value);
    return values;
}

function keyString(value) { return JSON.stringify(value); }

class FakeRequest {
    constructor() {
        this.result = undefined;
        this.error = null;
        this.onsuccess = null;
        this.onerror = null;
        this.onupgradeneeded = null;
        this.onblocked = null;
    }
}

class FakeStore {
    constructor(tx, definition) {
        this.tx = tx;
        this._name = definition.name;
        this._initial = definition;
    }
    get definition() {
        return this.tx._snapshot?.get(this._name)
            || this.tx.database?.stores.get(this._name)
            || this._initial;
    }
    get indexNames() { return list(this.definition.indexes.keys()); }
    createIndex(name, keyPath, options = {}) {
        this.definition.indexes.set(name, { keyPath, unique: Boolean(options.unique) });
        return {};
    }
    _key(value) { return value[this.definition.keyPath]; }
    _request(operation) { return this.tx.request(operation); }
    get(key) {
        return this._request(() => clone(this.definition.data.get(keyString(key))));
    }
    getAll() {
        return this._request(() => Array.from(this.definition.data.values(), clone));
    }
    add(value) { return this._write(value, true); }
    put(value) { return this._write(value, false); }
    _write(value, addOnly) {
        return this._request(() => {
            if (this.tx.mode !== 'readwrite' && this.tx.mode !== 'versionchange') {
                throw new Error('ReadOnlyError');
            }
            if (this.tx.backend.failNextWrite === this.definition.name) {
                this.tx.backend.failNextWrite = null;
                const error = new Error('QuotaExceededError');
                error.name = 'QuotaExceededError';
                throw error;
            }
            const row = clone(value);
            const key = this._key(row);
            const encoded = keyString(key);
            if (addOnly && this.definition.data.has(encoded)) throw new Error('ConstraintError');
            for (const index of this.definition.indexes.values()) {
                if (!index.unique) continue;
                const indexValue = Array.isArray(index.keyPath)
                    ? index.keyPath.map(part => row[part]) : row[index.keyPath];
                if (indexValue === undefined || (Array.isArray(indexValue) && indexValue.some(v => v === undefined))) continue;
                for (const [otherKey, other] of this.definition.data) {
                    if (otherKey === encoded) continue;
                    const otherValue = Array.isArray(index.keyPath)
                        ? index.keyPath.map(part => other[part]) : other[index.keyPath];
                    if (keyString(indexValue) === keyString(otherValue)) throw new Error('ConstraintError');
                }
            }
            this.definition.data.set(encoded, row);
            return key;
        });
    }
    delete(key) {
        return this._request(() => this.definition.data.delete(keyString(key)));
    }
}

class FakeTransaction {
    constructor(backend, database, names, mode, startAfter = Promise.resolve()) {
        this.backend = backend;
        this.database = database;
        this.names = names;
        this.mode = mode;
        this.error = null;
        this.oncomplete = null;
        this.onabort = null;
        this.onerror = null;
        this._pending = 0;
        this._aborted = false;
        this._finished = false;
        this._started = false;
        this._snapshot = null;
        this.done = new Promise(resolve => { this._resolveDone = resolve; });
        startAfter.then(() => {
            if (this._aborted) return;
            if (mode === 'readwrite') {
                this._snapshot = new Map();
                names.forEach(name => {
                    const original = database.stores.get(name);
                    this._snapshot.set(name, {
                        name: original.name,
                        keyPath: original.keyPath,
                        indexes: new Map(original.indexes),
                        data: new Map(Array.from(original.data, ([k, v]) => [k, clone(v)]))
                    });
                });
            }
            this._started = true;
            this._drain();
        });
    }
    objectStore(name) {
        if (!this.names.includes(name)) throw new Error('NotFoundError');
        const definition = this._snapshot?.get(name) || this.database.stores.get(name);
        return new FakeStore(this, definition);
    }
    request(operation) {
        const request = new FakeRequest();
        this._pending += 1;
        (this._queue || (this._queue = [])).push({ request, operation });
        this._drain();
        return request;
    }
    _drain() {
        if (!this._started || this._aborted || this._finished) return;
        const next = this._queue?.shift();
        if (!next) return this._maybeComplete();
        setImmediate(() => {
            if (this._aborted) return;
            try {
                next.request.result = next.operation();
                if (next.request.onsuccess) next.request.onsuccess({ target: next.request });
                this._pending -= 1;
                this._drain();
            } catch (error) {
                next.request.error = error;
                this.error = error;
                if (next.request.onerror) next.request.onerror({ target: next.request });
                if (this.onerror) this.onerror({ target: this });
                this.abort();
            }
        });
    }
    _maybeComplete() {
        if (this._pending !== 0 || this._finished || this._aborted) return;
        setImmediate(() => {
            if (this._pending !== 0 || this._finished || this._aborted || (this._queue && this._queue.length)) return;
            if (this.mode === 'readwrite') {
                this._snapshot.forEach((value, name) => this.database.stores.set(name, value));
            }
            this._finished = true;
            if (this.oncomplete) this.oncomplete({ target: this });
            this._resolveDone();
        });
    }
    abort() {
        if (this._finished || this._aborted) return;
        this._aborted = true;
        this._finished = true;
        setImmediate(() => {
            if (this.onabort) this.onabort({ target: this });
            this._resolveDone();
        });
    }
}

class FakeConnection {
    constructor(backend, database) {
        this.backend = backend;
        this.database = database;
        this.closed = false;
        this.onversionchange = null;
        database.connections.add(this);
    }
    get objectStoreNames() { return list(this.database.stores.keys()); }
    createObjectStore(name, options) {
        const definition = { name, keyPath: options.keyPath, indexes: new Map(), data: new Map() };
        this.database.stores.set(name, definition);
        return new FakeStore(this._upgradeTransaction, definition);
    }
    transaction(names, mode) {
        if (this.closed) throw new Error('InvalidStateError');
        const stores = Array.isArray(names) ? names : [names];
        stores.forEach(name => { if (!this.database.stores.has(name)) throw new Error('NotFoundError'); });
        const wait = this.database.rwTail;
        const tx = new FakeTransaction(this.backend, this.database, stores, mode, wait);
        if (mode === 'readwrite') this.database.rwTail = tx.done;
        return tx;
    }
    close() {
        this.closed = true;
        this.database.connections.delete(this);
        this.backend.closedConnections += 1;
    }
}

class FakeIndexedDB {
    constructor() {
        this.databases = new Map();
        this.closedConnections = 0;
        this.failNextWrite = null;
    }
    seed(name, version, stores) {
        const database = { version, stores: new Map(), connections: new Set(), rwTail: Promise.resolve() };
        Object.entries(stores).forEach(([storeName, spec]) => {
            const definition = { name: storeName, keyPath: spec.keyPath, indexes: new Map(), data: new Map() };
            (spec.indexes || []).forEach(index => definition.indexes.set(index.name, index));
            (spec.rows || []).forEach(row => definition.data.set(keyString(row[spec.keyPath]), clone(row)));
            database.stores.set(storeName, definition);
        });
        this.databases.set(name, database);
    }
    inspect(name, store) {
        return Array.from(this.databases.get(name).stores.get(store).data.values(), clone);
    }
    open(name, version) {
        const request = new FakeRequest();
        setImmediate(() => {
            let database = this.databases.get(name);
            if (!database) {
                database = { version: 0, stores: new Map(), connections: new Set(), rwTail: Promise.resolve() };
                this.databases.set(name, database);
            }
            if (version < database.version) {
                request.error = new Error('VersionError');
                if (request.onerror) request.onerror({ target: request });
                return;
            }
            if (version > database.version) {
                for (const connection of Array.from(database.connections)) {
                    if (connection.onversionchange) connection.onversionchange({ oldVersion: database.version, newVersion: version });
                }
                if (database.connections.size) {
                    if (request.onblocked) request.onblocked({ target: request });
                    return;
                }
            }
            const connection = new FakeConnection(this, database);
            request.result = connection;
            if (version > database.version) {
                const oldVersion = database.version;
                const upgradeTx = {
                    mode: 'versionchange', backend: this,
                    objectStore: storeName => new FakeStore(upgradeTx, database.stores.get(storeName))
                };
                connection._upgradeTransaction = upgradeTx;
                request.transaction = upgradeTx;
                database.version = version;
                if (request.onupgradeneeded) request.onupgradeneeded({ oldVersion, newVersion: version });
            }
            if (request.onsuccess) request.onsuccess({ target: request });
        });
        return request;
    }
}

function catalogDigest(products) {
    const rows = products.map(p => ({
        id: p.id, is_active: Boolean(p.is_active), name: p.name.normalize('NFC').trim().replace(/\s+/gu, ' '),
        price_vnd: p.price, track_batches: Boolean(p.track_batches)
    })).sort((a, b) => a.id - b.id);
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(
        'FS-OFFLINE-CATALOG-v1\n' + JSON.stringify(rows)
    )).then(buffer => Buffer.from(buffer).toString('hex'));
}

function loadProduction() {
    vm.runInThisContext(fs.readFileSync('static/js/offline-ban.js', 'utf8'), {
        filename: 'static/js/offline-ban.js'
    });
    return global.OfflineBan;
}

async function main() {
    const fake = new FakeIndexedDB();
    fake.seed('fselling-offline', 1, {
        phieu: {
            keyPath: 'offline_uuid', indexes: [{ name: 'shop_id', keyPath: 'shop_id', unique: false }],
            rows: [{ offline_uuid: 'legacy-v0', shop_id: 1, luc_luu: 1, loi: null }]
        },
        anhchup: {
            keyPath: 'khoa', rows: [{ khoa: 'sp:1', luc: 1, du_lieu: [{ id: 99 }] }]
        }
    });

    global.window = global;
    global.indexedDB = fake;
    Object.defineProperty(global, 'navigator', {
        configurable: true,
        value: { onLine: true }
    });
    const storage = new Map([['username', 'alice']]);
    global.localStorage = {
        getItem: key => storage.has(key) ? storage.get(key) : null,
        setItem: (key, value) => storage.set(key, String(value)),
        removeItem: key => storage.delete(key),
        key: index => Array.from(storage.keys())[index] ?? null,
        get length() { return storage.size; }
    };
    global.addEventListener = () => {};
    let perfNow = 100;
    let perfEpoch = 1000;
    Object.defineProperty(global, 'performance', {
        configurable: true,
        get: () => ({ timeOrigin: perfEpoch, now: () => perfNow })
    });

    const catalog1 = [
        { id: 7, name: '  Sữa   tươi ', price: 31000, is_active: false, track_batches: true },
        { id: 2, name: 'Cà phê sữa', price: 25000, is_active: true, track_batches: false }
    ];
    const digest1 = await catalogDigest(catalog1);
    let issueCount = 0;
    let issueNumber = 1;
    let nextDigest = digest1;
    let issueFailure = false;
    let issueMalformed = false;
    global.apiCall = async (endpoint) => {
        if (endpoint === '/offline/leases') {
            issueCount += 1;
            if (issueFailure) throw new Error('mock issuance unavailable');
            const number = issueNumber++;
            return {
                lease_id: `lse_${String(number).padStart(22, '0')}`,
                shop_id: 1, user_id: 11, device_id: fake.inspect('fselling-offline', 'meta_v1')[0].value,
                contract_version: 1, status: 'ACTIVE', server_time_utc: '2026-08-12 00:00:00.123456',
                server_anchor_id: `anc_${number}`,
                anchor_server_time_utc: issueMalformed
                    ? '2026-08-12 00:00:01.123456'
                    : '2026-08-12 00:00:00.123456',
                issued_at: '2026-08-12 00:00:00.123456', expires_at: '2099-08-12 12:00:00.123456',
                catalog_version: 0, catalog_snapshot_digest: nextDigest, state_version: 0,
                revoked_at: null, lease_token: `${'T'.repeat(42)}${number}`
            };
        }
        return { created: true };
    };

    let api = loadProduction();
    const first = await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog1 });
    assert.equal(first.user_id, 11);
    assert.equal('lease_token' in first, false);
    assert.equal(issueCount, 1);
    assert.equal(fake.databases.get('fselling-offline').version, 3);
    assert(fake.databases.get('fselling-offline').stores.has('sync_lock_v1'));
    assert.equal(fake.inspect('fselling-offline', 'phieu')[0].offline_uuid, 'legacy-v0');
    assert.deepEqual(fake.inspect('fselling-offline', 'anhchup')[0].du_lieu, [{ id: 99 }]);

    // Re-evaluating simulates reload: device/lease persist and no duplicate issue.
    api = loadProduction();
    const reused = await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog1 });
    assert.equal(reused.lease_id, first.lease_id);
    assert.equal(issueCount, 1);

    // Catalog change creates a new lease without overwriting the old token.
    const catalog2 = catalog1.map(row => ({ ...row }));
    catalog2[1].price = 26000;
    nextDigest = await catalogDigest(catalog2);
    const second = await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog2 });
    assert.equal(issueCount, 2);
    assert.notEqual(second.lease_id, first.lease_id);
    assert.equal(fake.inspect('fselling-offline', 'credential_v1').length, 2);
    assert(fake.inspect('fselling-offline', 'credential_v1').some(row => row.lease_token === `${'T'.repeat(42)}1`));

    navigator.onLine = false;
    const receiptOptions = {
        shop_id: 1, username: 'alice', payment_method: 'cash', voucher_code: null,
        loyalty_points_to_use: 0, cash_tendered: 60000, creation_key: 'operation-receipt-1',
        items: [{ product_id: 2, product_name: 'Cà phê sữa', price: 26000, quantity: 2 }]
    };
    perfNow = 500;
    const [r1, r2] = await Promise.all([
        api.createReceiptV1(receiptOptions),
        api.createReceiptV1({ ...receiptOptions, creation_key: 'operation-receipt-2' })
    ]);
    assert.deepEqual([r1.sequence, r2.sequence].sort((a, b) => a - b), [1, 2]);
    assert.notEqual(r1.offline_uuid, r2.offline_uuid);
    assert.equal(r1.state, 'READY');
    assert.equal(r1.items[0].unit_price_vnd, 26000);
    assert.equal(r1.monotonic_valid, true);
    assert.equal(r2.monotonic_valid, true);
    assert(r2.client_monotonic_ms >= r1.client_monotonic_ms);

    // Same operation is idempotent only for the exact immutable canonical intent.
    const exactRetry = await api.createReceiptV1(receiptOptions);
    assert.equal(exactRetry.offline_uuid, r1.offline_uuid);
    assert.equal(exactRetry.sequence, r1.sequence);
    const stableRow = JSON.stringify(fake.inspect('fselling-offline', 'receipt_v1')
        .find(row => row.offline_uuid === r1.offline_uuid));
    const sequenceBeforeMismatch = fake.inspect('fselling-offline', 'meta_v1')
        .find(row => row.key === 'sequence:' + second.lease_id).value;
    const mismatches = [
        { ...receiptOptions, items: [{ ...receiptOptions.items[0], product_name: 'Tên khác' }] },
        { ...receiptOptions, items: [{ ...receiptOptions.items[0], price: 25000 }] },
        { ...receiptOptions, items: [{ ...receiptOptions.items[0], quantity: 1 }] },
        { ...receiptOptions, items: [...receiptOptions.items, { ...receiptOptions.items[0], quantity: 1 }] },
        { ...receiptOptions, cash_tendered: 61000 }
    ];
    for (const mismatch of mismatches) {
        await assert.rejects(api.createReceiptV1(mismatch));
        assert.equal(JSON.stringify(fake.inspect('fselling-offline', 'receipt_v1')
            .find(row => row.offline_uuid === r1.offline_uuid)), stableRow);
        assert.equal(fake.inspect('fselling-offline', 'meta_v1')
            .find(row => row.key === 'sequence:' + second.lease_id).value, sequenceBeforeMismatch);
    }

    const exactAlice = { shop_id: 1, user_id: 11, username: 'alice', lease_id: second.lease_id };
    const readyA = await api.listReadyV1(exactAlice);
    assert.equal(readyA.length, 2);
    assert.equal(JSON.stringify(readyA).includes('T'.repeat(42)), false);
    assert.equal(readyA[0].client_fingerprint.includes('T'.repeat(42)), false);
    const internalCredential = await api.getCredentialV1(second.lease_id, exactAlice);
    assert.equal(internalCredential.lease_token, `${'T'.repeat(42)}2`);
    const statusView = await api.getOfflineStatusV1({ shop_id: 1, username: 'alice' });
    assert.equal(statusView.state_counts.READY, 2);
    assert.match(statusView.catalog_saved_at, /^\d{4}-\d{2}-\d{2}T/);
    assert.equal(JSON.stringify(statusView).includes('T'.repeat(42)), false);
    assert.equal(JSON.stringify(statusView).includes('lease_id'), false);

    // Quota/abort does not leave a DRAFT or advance sequence.
    fake.failNextWrite = 'receipt_v1';
    await assert.rejects(api.createReceiptV1({
        ...receiptOptions,
        creation_key: 'operation-receipt-3'
    }), /QuotaExceededError|transaction/i);
    perfNow = 700;
    const afterAbort = await api.createReceiptV1({ ...receiptOptions, creation_key: 'operation-receipt-3' });
    assert.equal(afterAbort.sequence, 3);
    assert(afterAbort.client_monotonic_ms >= Math.max(r1.client_monotonic_ms, r2.client_monotonic_ms));

    // Simulate a crash after durable DRAFT and before async digest.
    const readyStore = fake.databases.get('fselling-offline').stores.get('receipt_v1');
    const crash = clone(afterAbort);
    crash.offline_uuid = 'off-crash-draft';
    crash.sequence = 4;
    crash.local_creation_key = 'operation-crash-4';
    crash.state = 'DRAFT';
    crash.client_fingerprint = null;
    delete crash.ready_at;
    readyStore.data.set(keyString(crash.offline_uuid), crash);
    const metaStore = fake.databases.get('fselling-offline').stores.get('meta_v1');
    metaStore.data.set(keyString('sequence:' + second.lease_id), {
        key: 'sequence:' + second.lease_id, lease_id: second.lease_id, value: 4
    });
    const finalized = await api.finalizeDraftsV1({ shop_id: 1, user_id: 11, username: 'alice' });
    assert.equal(finalized.length, 1);
    assert.equal(finalized[0].offline_uuid, 'off-crash-draft');
    assert.equal(finalized[0].sequence, 4);
    assert.equal((await api.listReadyV1(exactAlice)).length, 4);
    const immutableRetry = await api.createReceiptV1({
        ...receiptOptions,
        creation_key: 'operation-crash-4'
    });
    assert.equal(immutableRetry.offline_uuid, 'off-crash-draft');
    assert.equal(immutableRetry.sequence, 4);
    assert.equal((await api.listReadyV1(exactAlice)).length, 4);

    // Reload/performance epoch change keeps cumulative non-decreasing but marks false.
    perfEpoch = 2000;
    perfNow = 50;
    api = loadProduction();
    navigator.onLine = true;
    await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog2 });
    navigator.onLine = false;
    const afterReload = await api.createReceiptV1({ ...receiptOptions, creation_key: 'operation-reload-5' });
    assert.equal(afterReload.monotonic_valid, false);
    assert(afterReload.client_monotonic_ms >= afterAbort.client_monotonic_ms);

    // Expiry/catalog/user/shop scope mismatch cannot create v1.
    const credentialDefinition = fake.databases.get('fselling-offline').stores.get('credential_v1');
    const credentialKey = keyString(second.lease_id);
    const savedCredential = clone(credentialDefinition.data.get(credentialKey));
    credentialDefinition.data.set(credentialKey, { ...savedCredential, expires_at: '2000-01-01 00:00:00.000000' });
    assert.equal(await api.createReceiptV1({
        ...receiptOptions, creation_key: 'operation-expired-6'
    }), null);
    credentialDefinition.data.set(credentialKey, savedCredential);
    const catalogDefinition = fake.databases.get('fselling-offline').stores.get('catalog_v1');
    const savedCatalog = clone(catalogDefinition.data.get(credentialKey));
    catalogDefinition.data.set(credentialKey, { ...savedCatalog, catalog_snapshot_digest: '0'.repeat(64) });
    assert.equal(await api.createReceiptV1({
        ...receiptOptions, creation_key: 'operation-catalog-6'
    }), null);
    catalogDefinition.data.set(credentialKey, savedCatalog);
    assert.equal(await api.createReceiptV1({
        ...receiptOptions, user_id: 12, creation_key: 'operation-user-6'
    }), null);
    assert.equal(await api.createReceiptV1({
        ...receiptOptions, shop_id: 2, creation_key: 'operation-shop-6'
    }), null);

    // Identity switch seals but never deletes A secret/receipts; B cannot list/use.
    await api.sealIdentityV1({ username: 'alice' });
    assert.deepEqual(await api.listReadyV1(exactAlice), []);
    assert.equal(await api.getCredentialV1(second.lease_id, exactAlice), null);
    const recoveryCredential = await api.getCredentialForRecoveryV1(second.lease_id, exactAlice);
    assert.equal(recoveryCredential.lease_token, `${'T'.repeat(42)}2`);
    assert.equal(await api.getCredentialForRecoveryV1(second.lease_id, {
        ...exactAlice, shop_id: 2
    }), null);
    assert.equal(await api.getCredentialForRecoveryV1(second.lease_id, {
        ...exactAlice, user_id: 12
    }), null);
    assert.equal(await api.getCredentialForRecoveryV1(first.lease_id, {
        ...exactAlice, lease_id: first.lease_id
    }), null, 'recovery seam requires a pending receipt on the exact lease');
    assert.equal(await api.createReceiptV1({
        ...receiptOptions, creation_key: 'operation-sealed-denied'
    }), null);
    storage.set('username', 'bob');
    assert.deepEqual(await api.listReadyV1({ ...exactAlice, username: 'bob' }), []);
    assert.equal(await api.getCredentialV1(second.lease_id, { ...exactAlice, username: 'bob' }), null);
    assert.equal(await api.getCredentialForRecoveryV1(
        second.lease_id, { ...exactAlice, username: 'bob' }
    ), null);
    assert(fake.inspect('fselling-offline', 'credential_v1').some(row => row.lease_token === `${'T'.repeat(42)}2`));
    assert(fake.inspect('fselling-offline', 'receipt_v1').some(row => row.username === 'alice'));
    storage.set('username', 'alice');
    navigator.onLine = true;
    const afterSeal = await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog2 });
    assert.notEqual(afterSeal.lease_id, second.lease_id);
    assert.equal(issueCount, 3);
    assert(fake.inspect('fselling-offline', 'credential_v1').some(row =>
        row.lease_id === second.lease_id && row.sealed === true
    ));
    navigator.onLine = false;
    await assert.rejects(api.createReceiptV1(receiptOptions), /đã khóa/,
        'same operation key from a sealed lease must reject');
    navigator.onLine = true;

    // Logout on a page without OfflineBan leaves this non-secret durable marker.
    // On the next module load it seals the old active lease before normal reuse.
    const markerKey = 'fselling.offline-seal.v1:' + encodeURIComponent('alice');
    storage.set(markerKey, JSON.stringify({ username: 'alice', generation: 'logout-without-module-1' }));
    api = loadProduction();
    const afterDeferredSeal = await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog2 });
    assert.notEqual(afterDeferredSeal.lease_id, afterSeal.lease_id);
    assert.equal(storage.has(markerKey), false);
    assert(fake.inspect('fselling-offline', 'credential_v1').some(row =>
        row.lease_id === afterSeal.lease_id && row.sealed === true
    ));

    // Exact canonical vector shared with server.
    const vector = await api.fingerprintV1({
        shop_id: 1, sold_at_client_utc: '2025-07-15 09:30:00.123456',
        client_monotonic_ms: 5000, monotonic_valid: true, server_anchor_id: 'anc_abc123',
        lease_id: 'lease-001', device_id: 'dev-001', offline_session_id: 'session-001',
        sequence: 1, offline_uuid: 'uuid-test-001', catalog_version: 1,
        catalog_snapshot_digest: 'sha256:deadbeef', cash_tendered: 55000,
        items: [
            { product_id: 10, product_name: 'Su\u0301a  ', unit_price_vnd: 15000, quantity: 2 },
            { product_id: 5, product_name: 'Ba\u0301nh', unit_price_vnd: 25000, quantity: 1 }
        ]
    });
    assert.equal(vector.digest, 'fsofr1:8fdaa734e0088f5b23c40ad1d411f56e07836fb4114613400230762f54320e22');
    const vectorReordered = await api.fingerprintV1({
        shop_id: 1, sold_at_client_utc: '2025-07-15T09:30:00.123456Z',
        client_monotonic_ms: 5000, monotonic_valid: true, server_anchor_id: 'anc_abc123',
        lease_id: 'lease-001', device_id: 'dev-001', offline_session_id: 'session-001',
        sequence: 1, offline_uuid: 'uuid-test-001', catalog_version: 1,
        catalog_snapshot_digest: 'sha256:deadbeef', cash_tendered: 55000,
        items: [
            { product_id: 5, product_name: 'Bánh', unit_price_vnd: 25000, quantity: 1 },
            { product_id: 10, product_name: 'Súa', unit_price_vnd: 15000, quantity: 2 }
        ]
    });
    assert.equal(vectorReordered.digest, vector.digest);
    const duplicate = await api.fingerprintV1({
        shop_id: 1, sold_at_client_utc: '2025-07-15 09:30:00.123456',
        client_monotonic_ms: 5000, monotonic_valid: true, server_anchor_id: 'anc_abc123',
        lease_id: 'lease-001', device_id: 'dev-001', offline_session_id: 'session-001',
        sequence: 1, offline_uuid: 'uuid-test-001', catalog_version: 1,
        catalog_snapshot_digest: 'sha256:deadbeef', cash_tendered: 80000,
        items: [
            { product_id: 10, product_name: 'Súa', unit_price_vnd: 15000, quantity: 2 },
            { product_id: 5, product_name: 'Bánh', unit_price_vnd: 25000, quantity: 1 },
            { product_id: 5, product_name: 'Bánh', unit_price_vnd: 25000, quantity: 1 }
        ]
    });
    assert.notEqual(duplicate.digest, vector.digest);
    for (const changed of [
        { ...vectorReordered, sequence: 2 },
        { ...vectorReordered, sold_at_client_utc: '2025-07-15 09:30:00.123457' }
    ]) {
        const result = await api.fingerprintV1({
            shop_id: 1, sold_at_client_utc: changed.sold_at_client_utc,
            client_monotonic_ms: 5000, monotonic_valid: true, server_anchor_id: 'anc_abc123',
            lease_id: 'lease-001', device_id: 'dev-001', offline_session_id: 'session-001',
            sequence: changed.sequence || 1, offline_uuid: 'uuid-test-001', catalog_version: 1,
            catalog_snapshot_digest: 'sha256:deadbeef', cash_tendered: 55000,
            items: vectorReordered.items
        });
        assert.notEqual(result.digest, vector.digest);
    }
    await assert.rejects(api.fingerprintV1({
        shop_id: 1, sold_at_client_utc: '2025-02-30 09:30:00.000000',
        client_monotonic_ms: 0, monotonic_valid: true, server_anchor_id: 'anc',
        lease_id: 'lease', device_id: 'device', offline_session_id: 'session',
        sequence: 1, offline_uuid: 'uuid-test', catalog_version: 0,
        catalog_snapshot_digest: 'digest', cash_tendered: 1,
        items: [{ product_id: 1, product_name: 'A', unit_price_vnd: 1, quantity: 1 }]
    }), /ISO-8601/);

    // Eligibility and exact integer gates.
    navigator.onLine = true;
    await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog2 });
    await assert.rejects(api.createReceiptV1({ ...receiptOptions, creation_key: 'fresh-online-denied' }), /navigator\.onLine/);
    navigator.onLine = false;
    await assert.rejects(api.createReceiptV1({ ...receiptOptions, payment_method: 'transfer' }), /tiền mặt/);
    await assert.rejects(api.createReceiptV1({ ...receiptOptions, cash_tendered: 1.5 }), /số nguyên/);
    await assert.rejects(api.createReceiptV1({ ...receiptOptions, items: [{ ...receiptOptions.items[0], quantity: true }] }), /số nguyên/);

    // A newly loaded catalog whose issuance fails must clear the active pointer;
    // it cannot silently reuse an old lease with a different snapshot.
    const catalog3 = catalog2.map(row => ({ ...row }));
    catalog3[1].price = 27000;
    nextDigest = await catalogDigest(catalog3);
    issueFailure = true;
    navigator.onLine = true;
    assert.equal(await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog3 }), null);
    navigator.onLine = false;
    assert.equal(await api.createReceiptV1({
        ...receiptOptions,
        creation_key: 'fresh-no-usable-catalog',
        items: [{ ...receiptOptions.items[0], price: 27000 }]
    }), null);
    issueFailure = false;
    const catalog4 = catalog3.map(row => ({ ...row }));
    catalog4[1].price = 28000;
    nextDigest = await catalogDigest(catalog4);
    issueMalformed = true;
    navigator.onLine = true;
    assert.equal(await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog4 }), null);
    issueMalformed = false;

    // v0 sync reads/deletes only `phieu`; READY v1 rows remain untouched.
    navigator.onLine = true;
    const readyBeforeV0Sync = fake.inspect('fselling-offline', 'receipt_v1').length;
    await api.dongBo(1);
    assert.equal(fake.inspect('fselling-offline', 'phieu').length, 0);
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, readyBeforeV0Sync);

    // Real POS checkout -> luuBanOffline -> v1 persistence (only DOM is stubbed).
    await api.prepareV1({ shop_id: 1, username: 'alice', products: catalog4 });
    navigator.onLine = false;
    const posSource = fs.readFileSync('static/js/pos.js', 'utf8');
    const section = (a, b) => posSource.slice(posSource.indexOf(a), posSource.indexOf(b, posSource.indexOf(a)));
    let onlineCreates = 0, notices = [], nextPosKey = 0;
    const posStorage = new Map();
    const pos = {
        window: { OfflineBan: api }, OfflineBan: api, localStorage,
        checkoutBusy: false, pendingCashOrderId: null, checkoutOperationId: null,
        currentOrderId: null, currentShopId: 1, activeShift: { id: 1 }, voucherBusy: false,
        paymentMethod: 'cash', currentVoucher: null, loyaltyPointsApplied: 0, selectedCustomerId: null,
        cashTenderedAmount: 60000, total: 56000,
        subtotal: 56000, discount: 0, loyaltyPointsRequested: 0, loyaltyDiscount: 0,
        selectedCustomerPointsBalance: 0, loyaltyProgram: null, selectedCustomerActive: false,
        datThongBaoDiem() {}, apDungPhuongThucThanhToan() {}, updateUI() {},
        docCheckoutDangDo: () => JSON.parse(posStorage.get('pos-state')),
        cart: [{ product_id: 2, product_name: catalog4[1].name, price: 28000, quantity: 2 }],
        capNhatTienKhachDua() {}, capNhatNutCheckout() {}, calcCart() {}, boChonKhach() {},
        capNhatHuyHieuOffline: async () => {}, pendingCheckoutState: null,
        sessionStorage: { setItem: (k,v) => posStorage.set(k,v) },
        checkoutStorageKey: () => 'pos-state', ghiSessionJson: (k,v) => posStorage.set(k,JSON.stringify(v)),
        xoaSessionKey: k => posStorage.delete(k),
        document: { getElementById: () => null }, showToast: msg => notices.push(msg),
        dich: key => key, dinhDangTien: v => v, dinhDangSoPOS: v => v,
        xacNhan: async () => true, taoOperationId: () => `pos-v1-integration-${++nextPosKey}`,
        thuTaoDonDangDo: async () => { onlineCreates++; }
    };
    vm.createContext(pos);
    vm.runInContext(section('function luuCheckoutDangDo(', 'function movementStorageKey(') + section('async function luuBanOffline(', 'async function thuTaoDonDangDo(')
        + section('function taoTrangThaiCheckout(', '// ===== Ca làm việc và sổ tiền mặt =====')
        + section('async function checkout(', 'async function thuTienMatDonDangCho('), pos);
    const v1Before = fake.inspect('fselling-offline', 'receipt_v1').length;
    await pos.checkout();
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, v1Before + 1,
        `fresh POS must persist exactly one v1 receipt: ${notices}`);
    assert.equal(fake.inspect('fselling-offline', 'phieu').length, 0, 'no v0 fallback');
    assert.equal(pos.cart.length, 0);
    assert.equal(pos.checkoutBusy, false);
    assert.equal(onlineCreates, 0);
    // Fail only the READY write after DRAFT was committed, then reload/retry.
    pos.cart = [{product_id:2,product_name:catalog4[1].name,price:28000,quantity:2}];
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    let failReady = true;
    crypto.subtle.digest = async function(algorithm, data) {
        const result = await originalDigest(algorithm, data);
        if (failReady && new TextDecoder().decode(data).startsWith('FS-OFFLINE-RECEIPT-v1')) {
            failReady = false; fake.failNextWrite = 'receipt_v1';
        }
        return result;
    };
    try { await pos.checkout(); } finally { crypto.subtle.digest = originalDigest; }
    assert(notices.some(x => /QuotaExceededError/.test(x)));
    const afterFault = fake.inspect('fselling-offline', 'receipt_v1');
    assert.equal(afterFault.length, v1Before + 2, 'DRAFT must already be durable');
    assert.equal(pos.pendingCheckoutState?.phase, 'offline_pending', 'retain offline recovery state');
    const failedKey = pos.pendingCheckoutState.operation_id;
    const recoveryState = posStorage.get('pos-state');
    pos.pendingCheckoutState = null; pos.checkoutOperationId = null; pos.cart = [];
    assert.equal(pos.phucHoiCheckoutDangDo(), true);
    assert.equal(pos.checkoutOperationId, failedKey);
    assert.equal(pos.cart[0].quantity, 2);
    assert.equal(pos.cashTenderedAmount, 60000);
    pos.cart[0].quantity = 1; pos.cashTenderedAmount = 100000; // mutable UI must not change saved intent
    navigator.onLine = true;
    await pos.checkout();
    await api.finalizeDraftsV1({shop_id:1,user_id:11,username:'alice'});
    assert.equal(fake.inspect('fselling-offline','receipt_v1').length,v1Before+2,'retry must not allocate a second receipt');
    assert.equal(nextPosKey,2,'reload retry must reuse creation key');
    assert.equal(onlineCreates,0);
    assert.equal(pos.pendingCheckoutState,null);
    // Failure to persist the retry key must stop before any IndexedDB allocation.
    navigator.onLine = false;
    pos.cart = [{product_id:2,product_name:catalog4[1].name,price:28000,quantity:2}];
    const saveState = pos.sessionStorage.setItem;
    pos.sessionStorage.setItem = () => { throw Error('session quota'); };
    await pos.checkout();
    pos.sessionStorage.setItem = saveState;
    assert.equal(fake.inspect('fselling-offline','receipt_v1').length,v1Before+2);
    assert.equal(pos.pendingCheckoutState,null);
    assert.equal(pos.checkoutBusy,false);
    assert.equal(pos.cart.length,1,'unwritten receipt must retain cart');
    // A restored, previously sent operation must bypass offline persistence.
    pos.checkoutOperationId = 'previously-sent';
    pos.pendingCheckoutState = { phase: 'creating', operation_id: 'previously-sent' };
    await pos.checkout();
    assert.equal(onlineCreates, 1);
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, v1Before + 2);

    // Sync can finish while POS still has the persisted retry state.
    const receiptStore = fake.databases.get('fselling-offline').stores.get('receipt_v1');
    for (const [key, value] of receiptStore.data) {
        if (value.local_creation_key !== failedKey) receiptStore.data.delete(key);
    }
    const savedReceipt = clone(fake.inspect('fselling-offline', 'receipt_v1')[0]);
    const replayOptions = { shop_id: 1, username: 'alice', user_id: 11, creation_key: failedKey,
        payment_method: 'cash', loyalty_points_to_use: 0, cash_tendered: 60000,
        items: JSON.parse(recoveryState).cart, allow_online_recovery: true };
    navigator.onLine = true;
    for (const state of ['SYNCING', 'RETRYABLE']) {
        receiptStore.data.set(keyString(savedReceipt.offline_uuid), { ...savedReceipt, state });
        assert.equal((await api.createReceiptV1(replayOptions)).state, state);
        assert.equal(fake.inspect('fselling-offline', 'receipt_v1')[0].state, state);
    }
    receiptStore.data.set(keyString(savedReceipt.offline_uuid), savedReceipt);
    global.getToken = () => 'demo-token';
    global.__FSellingOfflineSyncTestHooks = { fetch: async (path, options) => {
        const body = JSON.parse(options.body);
        assert(path.endsWith('/offline'));
        return { status: 200, headers: { get: () => null }, text: async () => JSON.stringify({
            contract_version: 1, offline_uuid: body.offline_uuid, order_id: 901, created: true,
            total: body.items.reduce((sum, item) => sum + item.unit_price_vnd * item.quantity, 0),
            sold_by_user_id: 11, synced_by_user_id: 11,
            sold_at_effective: '2026-09-12 00:00:00.000000',
            server_time_utc: '2026-09-12 00:00:00.000000', time_confidence: 'ANCHORED_CLIENT'
        }) };
    } };
    navigator.onLine = true;
    assert.equal((await api.triggerSyncV1({shop_id:1,user_id:11,username:'alice'})).acked, 1);
    posStorage.set('pos-state', recoveryState);
    pos.pendingCheckoutState = null; pos.checkoutOperationId = null;
    assert.equal(pos.phucHoiCheckoutDangDo(), true);
    navigator.onLine = true;
    await pos.checkout();
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, 1, 'ACK replay must never allocate another receipt');
    assert.equal(pos.pendingCheckoutState, null);
    // Catalog/lease replacement must not re-price or reallocate an already saved intent.
    navigator.onLine = true;
    const renewedCatalog = catalog4.map(row => ({ ...row, price: row.price + 1000 }));
    nextDigest = await catalogDigest(renewedCatalog);
    await api.prepareV1({shop_id:1,username:'alice',products:renewedCatalog});
    assert.equal((await api.createReceiptV1(replayOptions)).order_id, 901);
    await assert.rejects(api.createReceiptV1({...replayOptions,cash_tendered:61000}), /immutable intent/);
    await assert.rejects(api.createReceiptV1({...replayOptions,user_id:12}), /identity/);
    // Normal ACK cleanup must leave a binding that fails closed, never a fresh allocation.
    const tombstone = fake.inspect('fselling-offline', 'receipt_v1')[0];
    fake.databases.get('fselling-offline').stores.get('receipt_v1').data.set(
        keyString(tombstone.offline_uuid), {...tombstone,acked_at:'2000-01-01T00:00:00.000Z'});
    await api.triggerSyncV1({shop_id:1,user_id:11,username:'alice'});
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, 0);
    navigator.onLine = false;
    await assert.rejects(api.createReceiptV1(replayOptions), /không tạo lại/);
    assert.equal(fake.inspect('fselling-offline', 'receipt_v1').length, 0);

    // Phase A legacy fallback must also keep one receipt across a POS crash/retry.
    navigator.onLine = true;
    const oldApiCall = global.apiCall;
    global.apiCall = async path => path === '/offline/capability'
        ? {supported_versions:[0,1],minimum_accepted_version:0,phase:'PHASE_A',cutoff_at_utc:null,policy_code:'OFFLINE_CONTRACT_PHASE_A_V0_V1'}
        : oldApiCall(path);
    await api.refreshContractPolicy({shop_id:1,username:'alice'}, true);
    issueFailure = true;
    await api.prepareV1({shop_id:1,username:'alice',products:catalog4.map(row=>({...row,price:row.price+5000}))});
    issueFailure = false;
    navigator.onLine = false;
    const legacyOptions = {...replayOptions,creation_key:'legacy-pending-crash'};
    const legacySaved = await api.luuPhieuTuPOS(legacyOptions);
    assert.equal((await api.luuPhieuTuPOS(legacyOptions)).offline_uuid, legacySaved.offline_uuid);
    assert.equal(fake.inspect('fselling-offline','phieu').length,1);
    await api.xoaPhieu(legacySaved.offline_uuid);
    await assert.rejects(api.luuPhieuTuPOS(legacyOptions), /không tạo lại/);
    assert.equal(fake.inspect('fselling-offline','phieu').length,0);

    // Old v0 receipts have no creation key. Without a binding we cannot prove
    // whether this pending POS intent already produced one of those receipts.
    const unboundV0 = await api.luuPhieu(1, legacyOptions.items, 60000, 'alice');
    navigator.onLine = true;
    await assert.rejects(api.createReceiptV1({...legacyOptions,creation_key:'unbound-v0-unknown',
        allow_online_recovery:true}), error => error.offlineAllocationState !== 'definitely_not_allocated');
    assert.equal(fake.inspect('fselling-offline','phieu').length, 1);
    await api.xoaPhieu(unboundV0.offline_uuid);

    // Actual POS create/pay controllers + production IDB, with only HTTP/DOM fake.
    // Allocation abort has no durable receipt/binding. Retry and reload online
    // must create exactly one order, preserving the originally confirmed intent.
    navigator.onLine = true;
    nextDigest = await catalogDigest(catalog4);
    await api.prepareV1({shop_id:1,username:'alice',products:catalog4});
    const httpCreates = [], httpPays = [];
    Object.assign(pos, {
        posRequestUi: () => ({}), apDungKetQuaDiemServer() {},
        renderCashQuickAmounts() {}, xacNhanTongTienServer: async () => true,
        laLoi4xx: error => error.status >= 400 && error.status < 500 && !error.mutationOutcomeUnknown,
        document: {getElementById: id => id === 'txtTotal' ? {innerText:''} : null},
        hienHoaDon: async () => { pos.cart = []; },
        apiCall: async (path, method, body) => {
            assert.equal(method, 'POST');
            if (path === '/orders/1') {
                httpCreates.push(clone(body));
                return {order_id: 1000 + httpCreates.length, status:'PENDING', total:56000};
            }
            assert.match(path, /^\/orders\/\d+\/pay$/);
            httpPays.push(clone(body));
            return {msg:'Paid successfully'};
        }
    });
    vm.runInContext(section('async function thuTaoDonDangDo(', 'async function checkout(')
        + section('async function guiYeuCauTaoDonDangDo(', 'function identityDongBoPOS(')
        + section('async function hoanTatTienMatDangCho(', 'function apDungKetQuaDiemServer('), pos);
    for (const reload of [false, true]) {
        navigator.onLine = false;
        pos.currentOrderId = null; pos.pendingCashOrderId = null; pos.checkoutOperationId = null;
        pos.pendingCheckoutState = null; posStorage.clear();
        pos.cart = [{product_id:2,product_name:catalog4[1].name,price:28000,quantity:2}];
        pos.cashTenderedAmount = 60000;
        const createsBefore = httpCreates.length;
        const keyCount = nextPosKey;
        fake.failNextWrite = 'receipt_v1';
        await pos.checkout();
        const failed = clone(pos.pendingCheckoutState);
        assert.equal(failed.phase, 'offline_pending');
        assert.equal(fake.inspect('fselling-offline','receipt_v1').length, 0);
        assert.equal(fake.inspect('fselling-offline','meta_v1').filter(row => row.key === 'creation:' + failed.operation_id).length, 0);
        navigator.onLine = true;
        // Failed/unknown IDB reads or failure to commit the online binding
        // cannot authorize an HTTP request, even when the network is back.
        const transactions = new Map(Array.from(fake.databases.get('fselling-offline').connections,
            connection => [connection, connection.transaction]));
        for (const connection of transactions.keys()) connection.transaction = () => { throw new Error('IDB unavailable'); };
        try { await pos.checkout(); } finally {
            for (const [connection, transaction] of transactions) connection.transaction = transaction;
        }
        assert.equal(httpCreates.length, createsBefore);
        assert.equal(pos.pendingCheckoutState.phase, 'offline_pending');
        assert.equal(pos.pendingCheckoutState.operation_id, failed.operation_id);
        fake.failNextWrite = 'meta_v1';
        await pos.checkout();
        assert.equal(httpCreates.length, createsBefore);
        assert.equal(pos.pendingCheckoutState.phase, 'offline_pending');
        assert.equal(fake.inspect('fselling-offline','meta_v1').filter(row => row.key === 'creation:' + failed.operation_id).length, 0);
        if (reload) {
            pos.pendingCheckoutState = null; pos.checkoutOperationId = null; pos.cart = [];
            assert.equal(pos.phucHoiCheckoutDangDo(), true);
        }
        await pos.checkout();
        assert.equal(httpCreates.length, createsBefore + 1, 'allocation abort must recover to one online create');
        assert.deepEqual(httpCreates.at(-1), failed.create_payload);
        assert.deepEqual(httpPays.at(-1), {tendered_amount:60000});
        assert.equal(nextPosKey, keyCount + 1, 'online recovery keeps the exact creation key');
        assert.equal(pos.pendingCheckoutState, null);
        assert.equal(fake.inspect('fselling-offline','receipt_v1').length, 0);
        assert.equal(fake.inspect('fselling-offline','phieu').length, 0);
        const retryOptions = {shop_id:1,username:'alice',creation_key:failed.operation_id,
            payment_method:'cash',loyalty_points_to_use:0,items:failed.cart,
            cash_tendered:60000,allow_online_recovery:true};
        navigator.onLine = false;
        await assert.rejects(api.luuPhieuTuPOS(retryOptions), error =>
            error.offlineAllocationState === 'definitely_not_allocated');
        await assert.rejects(api.luuPhieuTuPOS({...retryOptions,cash_tendered:70000}), /immutable intent/);
        assert.equal(fake.inspect('fselling-offline','receipt_v1').length, 0, 'late offline retry cannot allocate after online dispatch');
        assert.equal(fake.inspect('fselling-offline','phieu').length, 0);
    }

    // A pre-binding v1 receipt is durable even without its newer meta mapping.
    const orphanOptions = {shop_id:1,username:'alice',creation_key:'pre-binding-draft-rc',
        payment_method:'cash',loyalty_points_to_use:0,items:replayOptions.items,
        cash_tendered:60000,allow_online_recovery:true};
    navigator.onLine = false;
    const orphan = await api.createReceiptV1(orphanOptions);
    fake.databases.get('fselling-offline').stores.get('meta_v1').data.delete(keyString('creation:' + orphanOptions.creation_key));
    navigator.onLine = true;
    await assert.rejects(api.createReceiptV1(orphanOptions), error =>
        error.offlineAllocationState !== 'definitely_not_allocated');
    assert.equal(fake.inspect('fselling-offline','receipt_v1')[0].offline_uuid, orphan.offline_uuid);

    // Opening a forward version triggers production onversionchange and closes cache.
    const closedBefore = fake.closedConnections;
    const upgrade = indexedDB.open('fselling-offline', 4);
    upgrade.onupgradeneeded = () => {};
    await new Promise((resolve, reject) => {
        upgrade.onsuccess = resolve;
        upgrade.onerror = () => reject(upgrade.error);
        upgrade.onblocked = () => reject(new Error('versionchange connection was not closed'));
    });
    assert(fake.closedConnections > closedBefore);

    console.log('offline-ban-v1 harness: 2 passed');
}

module.exports = { FakeIndexedDB, clone, keyString };

if (require.main === module) {
    main().catch(error => {
        console.error(error && error.stack || error);
        process.exit(1);
    });
}
