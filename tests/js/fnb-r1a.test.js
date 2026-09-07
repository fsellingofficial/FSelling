const assert = require('node:assert/strict');
const {
    createController, escapeHtml, partitionLines,
    adjustmentValueForApi, adjustmentValueForForm, groupMenuProducts,
    cashTenderedForPayment, cashExactAllowed,
} = require('../../static/js/fnb-r1a.js');

function floor(revision = 2) {
    return {
        changed: true,
        shop_id: 1,
        fnb_revision: revision,
        areas: [{
            id: 10,
            name: 'Trong nhà',
            active: true,
            tables: [
                { id: 20, name: 'Bàn 1', active: true, state: 'EMPTY', state_version: 4, session: null },
                { id: 21, name: 'Bàn 2', active: true, state: 'SERVING', state_version: 8,
                    session: { id: 31, revision: 6 } },
            ],
        }],
    };
}

function session(revision = 3) {
    return {
        id: 30,
        shop_id: 1,
        revision,
        tables: [{ id: 20, name: 'Bàn 1', state_version: 5 }],
        lines: [{
            id: 40, product_id: 7, product_name: 'Trà', quantity: 2,
            cancelled_quantity: 0, unsent_quantity: 0, active_sent_quantity: 2,
            station: 'KITCHEN', state_version: 9,
        }],
        unsent_quantity: 0,
        subtotal_vnd: 20000,
        service_stage: 'DIRECT',
        service_summary: { NEW: 0, IN_PROGRESS: 0, READY: 0, SERVED: 0 },
        service_tickets: [],
    };
}

function makeDeps(overrides = {}) {
    let uuidIndex = 0;
    const renders = [];
    const timers = [];
    const cleared = [];
    return {
        request: async () => floor(),
        render: event => renders.push(event),
        setTimeoutFn: (callback, delay) => {
            timers.push({ callback, delay });
            return timers.length;
        },
        clearTimeoutFn: id => cleared.push(id),
        storage: new Map(),
        now: () => 1_000,
        uuid: () => `operation-${++uuidIndex}`,
        username: 'lan',
        role: 'SELLER',
        staffRole: null,
        renders,
        timers,
        cleared,
        ...overrides,
    };
}

async function loadedController(overrides = {}) {
    const deps = makeDeps(overrides);
    const controller = createController(deps);
    await controller.selectShop(1);
    return { controller, deps };
}

async function testLateFloorResponseIsIgnoredAfterShopChange() {
    const pending = [];
    const deps = makeDeps({
        request: endpoint => new Promise(resolve => pending.push({ endpoint, resolve })),
    });
    const controller = createController(deps);
    const first = controller.selectShop(1);
    const second = controller.selectShop(2);
    pending.find(row => row.endpoint.includes('shop_id=2')).resolve({
        changed: true, shop_id: 2, fnb_revision: 2, areas: [],
    });
    await second;
    pending.find(row => row.endpoint.includes('shop_id=1')).resolve({
        changed: true, shop_id: 1, fnb_revision: 9, areas: [{ id: 9, tables: [] }],
    });
    await first;
    assert.equal(controller.getState().shopId, 2);
    assert.deepEqual(controller.getState().floor.areas, []);
}

async function testPollingAndLifecycle() {
    const replies = [floor(4), { changed: false, fnb_revision: 4 }];
    const endpoints = [];
    const deps = makeDeps({
        request: async endpoint => {
            endpoints.push(endpoint);
            return replies.shift();
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    const floorRenders = deps.renders.filter(event => event.type === 'floor').length;
    await controller.loadFloor(false);
    assert.match(endpoints[1], /after_revision=4/);
    assert.equal(deps.renders.filter(event => event.type === 'floor').length, floorRenders);
    assert.equal(deps.renders.at(-1).type, 'floor-synced');
    assert.equal(deps.timers.at(-1).delay, 2_000);
    controller.dispose();
    assert.ok(deps.cleared.length > 0);

    const hidden = makeDeps({ isHidden: () => true });
    const background = createController(hidden);
    await background.selectShop(1);
    assert.equal(hidden.timers.at(-1).delay, 10_000);
}

async function testSessionAnnouncementsOnlyFollowMutations() {
    const replies = [floor(), session()];
    const deps = makeDeps({ request: async () => replies.shift() });
    const controller = createController(deps);
    await controller.selectShop(1);
    await controller.loadSession(30);
    assert.equal(deps.renders.at(-1).saved, false);

    deps.request = async endpoint => endpoint.startsWith('/fnb/floor') ? floor() : session(4);
    await controller.openTable(20);
    const openedSession = deps.renders.findLast(event => event.type === 'session');
    assert.equal(openedSession.saved, false);

    controller.seedSession(session(4));
    await controller.addLine({ product_id: 7, quantity: 1, note: '' });
    const savedSession = deps.renders.findLast(event => event.type === 'session');
    assert.equal(savedSession.saved, true);
}

async function testConflictKeepsDraftAndUsesAuthoritativeSnapshot() {
    const draft = { product_id: 7, quantity: 2, note: 'Ít đá' };
    const latest = { ...session(3), lines: [] };
    const deps = makeDeps({
        request: async endpoint => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            const error = new Error('changed');
            error.status = 409;
            error.code = 'FNB_SESSION_CHANGED';
            error.detail = { code: 'FNB_SESSION_CHANGED', snapshot: latest };
            throw error;
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session(2));
    await assert.rejects(controller.addLine(draft));
    assert.deepEqual(controller.getState().session, latest);
    assert.deepEqual(controller.getState().recoverableDraft, draft);
    assert.equal(deps.renders.at(-1).type, 'conflict');
}

async function testConflictReapplyKeepsOriginalAction() {
    const calls = [];
    const latest = session(6);
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            calls.push({ endpoint, method, body });
            if (calls.length === 1) {
                const error = new Error('changed');
                error.status = 409;
                error.code = 'FNB_SESSION_CHANGED';
                error.detail = { code: error.code, snapshot: latest };
                throw error;
            }
            return session(7);
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session(5));

    await assert.rejects(controller.moveTable(20, 21));
    await controller.reapplyRecoverable();

    assert.equal(calls.length, 2);
    assert.equal(calls[1].endpoint, '/fnb/sessions/30/move-table');
    assert.equal(calls[1].body.expected_revision, 6);
    assert.notEqual(calls[1].body.operation_id, calls[0].body.operation_id);
}

async function testSingleFlightRetryAndDefinitiveFailure() {
    let resolveRequest;
    const calls = [];
    const deps = makeDeps({
        request: endpoint => {
            if (endpoint.startsWith('/fnb/floor')) return Promise.resolve(floor());
            calls.push({ endpoint, body: arguments[2] });
            return new Promise(resolve => { resolveRequest = resolve; });
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session());
    const first = controller.addLine({ product_id: 7, quantity: 1, note: '' });
    const second = controller.addLine({ product_id: 7, quantity: 1, note: '' });
    assert.notStrictEqual(first, second);
    await assert.rejects(second, error => error.code === 'FNB_MUTATION_PENDING');
    assert.equal(calls.length, 1);
    resolveRequest(session(4));
    await first;

    let attempt = 0;
    const bodies = [];
    const retryDeps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            bodies.push(body);
            attempt += 1;
            if (attempt === 1) throw new Error('offline');
            return session(4);
        },
    });
    const retry = createController(retryDeps);
    await retry.selectShop(1);
    retry.seedSession(session());
    await assert.rejects(retry.addLine({ product_id: 7, quantity: 1, note: 'Nóng' }));
    const key = [...retryDeps.storage.keys()][0];
    assert.match(key, /lan.*1.*30/);
    await assert.rejects(
        retry.addLine({ product_id: 7, quantity: 1, note: 'Nóng' }),
        error => error.code === 'FNB_MUTATION_PENDING',
    );
    assert.equal(bodies.length, 1);
    await retry.retryPending();
    assert.equal(bodies[0].operation_id, bodies[1].operation_id);

    const rejectedBodies = [];
    const rejectedDeps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            rejectedBodies.push(body);
            if (rejectedBodies.length === 1) {
                const error = new Error('bad');
                error.status = 400;
                throw error;
            }
            return session(4);
        },
    });
    const rejected = createController(rejectedDeps);
    await rejected.selectShop(1);
    rejected.seedSession(session());
    await assert.rejects(rejected.addLine({ product_id: 7, quantity: 1, note: '' }));
    await rejected.addLine({ product_id: 7, quantity: 1, note: '' });
    assert.notEqual(rejectedBodies[0].operation_id, rejectedBodies[1].operation_id);
}

async function testApprovalSecretsAreNeverPersistedForRecovery() {
    const deps = makeDeps({
        request: async endpoint => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            throw new Error('offline');
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session());

    await assert.rejects(controller.cancelLine(40, 1, {
        resolution: 'WASTE',
        reason: 'Món đã làm',
        approval_token: 'approval-secret-must-not-persist',
    }));

    assert.equal(
        [...deps.storage.values()].some(value => String(value).includes('approval-secret')),
        false,
    );
}

async function testCancelDecisionRequiredAfterNetworkFailureClearsPendingAndDraft() {
    const bodies = [];
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            bodies.push(body);
            if (bodies.length === 1) throw new Error('offline');
            const error = new Error('Cần chọn cách xử lý tồn');
            error.status = 400;
            error.code = 'FNB_CANCELLATION_DECISION_REQUIRED';
            error.detail = { code: error.code };
            throw error;
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session());

    await assert.rejects(controller.cancelLine(40, 1));
    assert.equal(deps.storage.size, 1);
    assert.deepEqual(bodies[0], {
        line_id: 40,
        quantity: 1,
        expected_line_version: 9,
        expected_revision: 3,
        operation_id: 'operation-1',
    });

    deps.renders.length = 0;
    await assert.rejects(controller.retryPending());

    assert.deepEqual(bodies[1], bodies[0]);
    assert.equal(controller.getState().pendingMutation, null);
    assert.equal(controller.getState().recoverableDraft, null);
    assert.equal(deps.storage.size, 0);
    assert.equal(deps.renders.at(-1).type, 'cancel-action-required');
    assert.equal(deps.renders.at(-1).attempt.line_id, 40);
    assert.equal(
        deps.renders.some(event => event.type === 'mutation-error'),
        false,
    );
}

async function testCancelConflictAfterNetworkFailureRequiresFreshUserDecision() {
    const latest = { ...session(8), lines: [{
        ...session(8).lines[0], state_version: 10,
    }] };
    const bodies = [];
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            bodies.push(body);
            if (bodies.length === 1) throw new Error('offline');
            const error = new Error('Món vừa thay đổi');
            error.status = 409;
            error.code = 'FNB_LINE_CHANGED';
            error.detail = { code: error.code, snapshot: latest };
            throw error;
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session(7));

    await assert.rejects(controller.cancelLine(40, 1, {
        resolution: 'WASTE', reason: 'Món đã chế biến',
    }));
    assert.equal(deps.storage.size, 1);
    assert.deepEqual(bodies[0], {
        line_id: 40,
        quantity: 1,
        resolution: 'WASTE',
        reason: 'Món đã chế biến',
        expected_line_version: 9,
        expected_revision: 7,
        operation_id: 'operation-1',
    });

    await assert.rejects(controller.retryPending());

    assert.deepEqual(bodies[1], bodies[0]);
    assert.deepEqual(controller.getState().session, latest);
    assert.equal(controller.getState().pendingMutation, null);
    assert.equal(controller.getState().recoverableDraft, null);
    assert.equal(deps.storage.size, 0);
    assert.equal(deps.renders.at(-1).type, 'cancel-conflict');
    assert.equal(deps.renders.some(event => event.type === 'conflict'), false);
}

async function testLatestRevisionBodies() {
    const calls = [];
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return floor(12);
            calls.push({ endpoint, method, body });
            return session(Number(body.expected_revision || 0) + 1);
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session(14));
    await controller.cancelLine(40, 1);
    assert.deepEqual(calls.at(-1).body, {
        line_id: 40,
        quantity: 1,
        expected_line_version: 9,
        expected_revision: 14,
        operation_id: calls.at(-1).body.operation_id,
    });

    controller.seedSession(session(20));
    await controller.moveTable(20, 21);
    assert.equal(calls.at(-1).body.expected_revision, 20);
    assert.equal(calls.at(-1).body.expected_from_state_version, 5);
    assert.equal(calls.at(-1).body.expected_to_state_version, 8);

    controller.seedSession(session(25));
    await controller.mergeTable(21);
    assert.equal(calls.at(-1).body.expected_revision, 25);
    assert.equal(calls.at(-1).body.expected_target_session_revision, 6);

    controller.seedSession(session(30));
    await controller.cancelSession('Khách đổi ý');
    assert.equal(calls.at(-1).body.expected_revision, 30);

    controller.seedSession(session(31));
    await controller.sendSession();
    assert.equal(calls.at(-1).endpoint, '/fnb/sessions/30/send');
    assert.equal(calls.at(-1).body.expected_revision, 31);
}

async function testServeTicketUsesCurrentTicketAndSessionRevisions() {
    const calls = [];
    const liveFloor = floor();
    liveFloor.areas[0].tables[0] = {
        ...liveFloor.areas[0].tables[0],
        state: 'SERVING',
        session: { id: 30, revision: 13 },
    };
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            if (endpoint.startsWith('/fnb/floor')) return liveFloor;
            calls.push({ endpoint, method, body });
            if (endpoint === '/fnb/sessions/30') return session(13);
            return { session_revision: 13, service_stage: 'SERVED' };
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession({
        ...session(12),
        service_stage: 'READY',
        service_summary: { NEW: 0, IN_PROGRESS: 0, READY: 1, SERVED: 0 },
        service_tickets: [{ id: 50, state_version: 4, service_stage: 'READY' }],
    });

    await controller.serveTicket(50);

    assert.deepEqual(calls[0], {
        endpoint: '/fnb/tickets/50/serve',
        method: 'POST',
        body: {
            expected_state_version: 4,
            expected_session_revision: 12,
            operation_id: 'operation-1',
        },
    });
    assert.equal(controller.getState().session.revision, 13);
}

async function testStationUpdateUsesCurrentFloorRevision() {
    const calls = [];
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            calls.push({ endpoint, method, body });
            if (endpoint.startsWith('/fnb/floor')) return floor(16);
            return { id: 7, station: 'BAR', fnb_revision: 17 };
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    await controller.updateProductStation(7, 'BAR');
    assert.equal(calls[1].endpoint, '/fnb/menu-items/7/station');
    assert.equal(calls[1].body.station, 'BAR');
    assert.equal(calls[1].body.expected_revision, 16);
}

async function testSetupMutationsAndAccess() {
    const calls = [];
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            calls.push({ endpoint, method, body });
            if (endpoint.startsWith('/fnb/floor')) return floor(7);
            return { ...body, id: 99, state_version: 5, fnb_revision: 8 };
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    await controller.createArea({ name: 'Sân', sort_order: 2 });
    assert.equal(calls[1].body.expected_revision, 7);
    assert.equal(calls.filter(call => call.endpoint.startsWith('/fnb/floor')).length, 2);

    await controller.createTable({ area_id: 10, name: 'Bàn 3', sort_order: 3 });
    assert.equal(calls.at(-2).body.operation_id.startsWith('operation-'), true);
    await controller.updateTable(20, { name: 'Bàn VIP' });
    assert.equal(calls.at(-2).body.expected_state_version, 4);

    const duplicateDeps = makeDeps({
        request: async endpoint => {
            if (endpoint.startsWith('/fnb/floor')) return floor();
            const error = new Error('exists');
            error.status = 409;
            error.code = 'FNB_NAME_EXISTS';
            throw error;
        },
    });
    const duplicate = createController(duplicateDeps);
    await duplicate.selectShop(1);
    const values = { name: 'Trùng', sort_order: 0 };
    await assert.rejects(duplicate.createArea(values));
    assert.deepEqual(duplicateDeps.renders.at(-1).value, values);

    for (const [role, staffRole, allowed] of [
        ['SELLER', null, true],
        ['STAFF', 'MANAGER', true],
        ['STAFF', 'CASHIER', false],
        ['STAFF', 'WAREHOUSE', false],
    ]) {
        const accessDeps = makeDeps({ role, staffRole });
        createController(accessDeps);
        assert.equal(accessDeps.renders[0].allowed, allowed);
    }
}

async function testCheckoutUsesLatestCheckAndSessionRevisions() {
    const calls = [];
    const checks = {
        shop_id: 1,
        session_id: 30,
        session_revision: 12,
        checks: [{
            id: 51, revision: 4, status: 'OPEN', is_primary: true,
            lines: [{ line_id: 40, quantity: 2 }], total_vnd: 20000,
        }],
    };
    const deps = makeDeps({
        request: async (endpoint, method, body) => {
            calls.push({ endpoint, method, body });
            if (endpoint.startsWith('/fnb/floor')) return floor();
            if (method === 'GET') return checks;
            if (endpoint.endsWith('/pay')) {
                return {
                    session_revision: 14,
                    session_status: 'PARTIALLY_SETTLED',
                    check: { ...checks.checks[0], revision: 5, status: 'PAID' },
                    order: { id: 90, status: 'PAID' },
                };
            }
            return { ...checks, session_revision: 13 };
        },
    });
    const controller = createController(deps);
    await controller.selectShop(1);
    controller.seedSession(session(12));
    await controller.loadChecks();
    await controller.splitCheck(51, [{ line_id: 40, quantity: 1 }], 'Khách 2');
    assert.equal(calls.at(-2).endpoint, '/fnb/checks/51/split');
    assert.equal(calls.at(-2).body.expected_revision, 4);
    assert.equal(calls.at(-2).body.expected_session_revision, 12);
    await controller.payCheck(51, { payment_method: 'cash', cash_tendered_vnd: 20000 });
    assert.equal(calls.at(-2).endpoint, '/fnb/checks/51/pay');
    assert.equal(calls.at(-2).body.expected_session_revision, 13);
}

function fakeCheckoutElement(id, paymentMethod) {
    const listeners = new Map();
    return {
        id,
        value: id === 'fnbCancelResolution' ? 'WASTE' : '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        disabled: false,
        open: false,
        dataset: {},
        classList: { add() {}, remove() {} },
        addEventListener(type, handler) {
            listeners.set(type, [...(listeners.get(type) || []), handler]);
        },
        emit(type, event = {}) {
            (listeners.get(type) || []).forEach(handler => handler({
                preventDefault() {}, target: this, ...event,
            }));
        },
        querySelector(selector) {
            return selector.includes('fnbPaymentMethod') ? paymentMethod : null;
        },
        querySelectorAll() { return this.controls || []; },
        closest() { return this; },
        matches() { return false; },
        focus() { this.focused = true; },
        showModal() { this.open = true; },
        close() { this.open = false; this.emit('close'); },
    };
}

function check(id, total) {
    return {
        id, label: `Bill ${id}`, status: 'OPEN', total_vnd: total, subtotal_vnd: total,
        revision: 1, order_id: 0, discount_vnd: 0, service_charge_vnd: 0,
        discount_kind: 'NONE', discount_value: 0,
        service_charge_kind: 'NONE', service_charge_value: 0,
        lines: [{ line_id: id, product_name: 'Tea', quantity: 1, unit_price_vnd: total }],
    };
}

async function mountedFnbHarness(options = {}) {
    const original = Object.fromEntries([
        'document', 'localStorage', 'sessionStorage', 'navigator', 'apiCall', 't',
        'showToast', 'navigateToPage', 'redirectToLogin', 'addEventListener', 'window',
        'setTimeout', 'clearTimeout',
    ].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    const globalListeners = new Map();
    const paymentMethod = { value: 'cash' };
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, fakeCheckoutElement(id, paymentMethod));
        return elements.get(id);
    };
    const documentListeners = new Map();
    const document = {
        readyState: 'complete', hidden: false,
        body: { classList: { add() {}, remove() {} } },
        getElementById: element,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener(type, handler) {
            documentListeners.set(type, [...(documentListeners.get(type) || []), handler]);
        },
        emit(type, event = {}) {
            (documentListeners.get(type) || []).forEach(handler => handler(event));
        },
    };
    const storage = values => ({
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key),
    });
    const first = check(1, 120000);
    const second = check(2, 70000);
    const checkReplies = [
        { session_revision: 3, session_status: 'OPEN', checks: [first, second] },
        { session_revision: 4, session_status: 'OPEN', checks: [first] },
    ];
    const cancelReplies = [...(options.cancelReplies || [])];
    const approvalReplies = [...(options.approvalReplies || [])];
    const payReplies = [...(options.payReplies || [])];
    const calls = [];
    let opened = false;
    let hideSession = false;
    let floorSessionRevision = 3;
    const setGlobal = (key, value) => Object.defineProperty(globalThis, key, {
        configurable: true, writable: true, value,
    });
    setGlobal('document', document);
    setGlobal('localStorage', storage(new Map([['token', 'test'], ['role', 'SELLER']])));
    setGlobal('sessionStorage', storage(new Map()));
    setGlobal('navigator', { onLine: true });
    setGlobal('t', (key, values = {}) => values.amount ? `${key}:${values.amount}` : key);
    setGlobal('showToast', () => {});
    setGlobal('navigateToPage', () => {});
    setGlobal('redirectToLogin', () => {});
    setGlobal('setTimeout', () => 0);
    setGlobal('clearTimeout', () => {});
    setGlobal('addEventListener', (type, handler) => {
        globalListeners.set(type, [...(globalListeners.get(type) || []), handler]);
    });
    let uuidIndex = 0;
    setGlobal('window', {
        document,
        addEventListener: globalThis.addEventListener,
        crypto: { randomUUID: () => `operation-${++uuidIndex}` },
    });
    setGlobal('apiCall', async (endpoint, method, body) => {
        calls.push({ endpoint, method, body });
        if (endpoint === '/shops') return [{ id: 1, name: 'Test', fnb_enabled: true }];
        if (endpoint.startsWith('/fnb/floor')) {
            const value = floor();
            if (opened && !hideSession) value.areas[0].tables[0] = {
                ...value.areas[0].tables[0], state: 'SERVING',
                session: { id: 30, revision: floorSessionRevision },
            };
            return value;
        }
        if (endpoint.startsWith('/products/') || endpoint.startsWith('/categories/')) return [];
        if (endpoint.startsWith('/customers/')) return [];
        if (endpoint === '/fnb/sessions') {
            opened = true;
            return session();
        }
        if (endpoint === '/fnb/sessions/30') return session(floorSessionRevision);
        if (endpoint === '/fnb/sessions/30/checks') return checkReplies.shift();
        if (endpoint === '/fnb/checks/1/pay') {
            const reply = payReplies.length ? payReplies.shift() : {
                session_revision: 3, session_status: 'OPEN', checks: [second], check: first,
                order: { qr_url: 'receipt-one' },
            };
            if (reply instanceof Error) throw reply;
            return reply;
        }
        if (endpoint === '/fnb/sessions/30/cancel-line') {
            if (!cancelReplies.length) throw new Error(`Unexpected endpoint ${endpoint}`);
            const reply = cancelReplies.shift();
            if (reply instanceof Error) throw reply;
            return reply;
        }
        if (endpoint === '/fnb/manager-approvals') {
            if (!approvalReplies.length) throw new Error(`Unexpected endpoint ${endpoint}`);
            const reply = approvalReplies.shift();
            if (reply instanceof Error) throw reply;
            return reply;
        }
        throw new Error(`Unexpected endpoint ${endpoint}`);
    });
    const source = require.resolve('../../static/js/fnb-r1a.js');
    element('fnbPayForm').controls = [
        element('fnbCashTendered'), element('fnbCashExact'), element('fnbPayButton'),
    ];
    delete require.cache[source];
    require(source);
    const settle = async () => {
        for (let index = 0; index < 20; index += 1) await new Promise(resolve => setImmediate(resolve));
    };
    await settle();
    const table = fakeCheckoutElement('table', paymentMethod);
    table.dataset = { action: 'open-table', id: '20' };
    document.emit('click', { target: table });
    await settle();
    if (options.openCheckout) {
        element('fnbCheckoutOpen').emit('click');
        await settle();
    }
    return {
        elements: Object.fromEntries([...elements.entries()]), calls, paymentMethod,
        document,
        clickAction(action, id) {
            const target = fakeCheckoutElement(action, paymentMethod);
            target.dataset = { action, ...(id === undefined ? {} : { id: String(id) }) };
            document.emit('click', { target });
        },
        emitOnline: () => (globalListeners.get('online') || []).forEach(handler => handler()),
        refreshSession(revision) {
            floorSessionRevision = revision;
            (globalListeners.get('online') || []).forEach(handler => handler());
        },
        emitPagehide: () => (globalListeners.get('pagehide') || []).forEach(handler => handler()),
        hideSession: () => { hideSession = true; },
        settle,
        cleanup() {
            delete require.cache[source];
            for (const [key, descriptor] of Object.entries(original)) {
                if (descriptor) Object.defineProperty(globalThis, key, descriptor);
                else delete globalThis[key];
            }
        },
    };
}

function fnbError(code, message, status, snapshot) {
    const error = new Error(message);
    error.status = status;
    error.code = code;
    error.detail = { code, ...(snapshot ? { snapshot } : {}) };
    return error;
}

function approvalState(elements) {
    return {
        open: elements.fnbApprovalDialog.open,
        pin: elements.fnbApprovalPin.value,
        reason: elements.fnbCancelReason.value,
        resolution: elements.fnbCancelResolution.value,
        status: elements.fnbApprovalStatus.textContent,
    };
}

async function withCancellation(options, action) {
    const code = options.code || 'FNB_APPROVAL_REQUIRED';
    const harness = await mountedFnbHarness({
        cancelReplies: [
            fnbError(code, 'Action required', code === 'FNB_CANCELLATION_DECISION_REQUIRED' ? 400 : 403),
            ...(options.cancelReplies || []),
        ],
        approvalReplies: options.approvalReplies || [],
    });
    try {
        harness.clickAction('cancel-line', 40);
        await harness.settle();
        await action(harness);
    } finally {
        harness.cleanup();
    }
}

async function testCancellationApprovalDialogLifecycle() {
    for (const code of ['FNB_CANCELLATION_DECISION_REQUIRED', 'FNB_APPROVAL_REQUIRED']) {
        await withCancellation({ code }, async ({ elements }) => {
            assert.equal(elements.fnbApprovalDialog.open, true);
            assert.equal(elements.fnbCancelResolution.focused, true);
            assert.equal(elements.fnbSessionStatus.textContent, 'fnb.cancel.action_required');
            assert.doesNotMatch(elements.fnbSentLines.innerHTML, /fnb-unsynced/);
            assert.match(elements.fnbSentLines.innerHTML, /data-action="cancel-line"/);
        });
    }

    await withCancellation({
        cancelReplies: [session(4)],
        approvalReplies: [{ approval_token: 'approval-token' }],
    }, async ({ elements, calls, settle }) => {
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = '   ';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.equal(calls.filter(call => call.endpoint === '/fnb/manager-approvals').length, 0);
        assert.equal(calls.filter(call => call.endpoint.endsWith('/cancel-line')).length, 1);
        assert.equal(elements.fnbCancelReason.focused, true);
        assert.equal(elements.fnbApprovalStatus.textContent, 'fnb.cancel.reason_required');
    });

    await withCancellation({
        cancelReplies: [session(4)],
        approvalReplies: [
            fnbError('FNB_APPROVAL_INVALID', 'Sai PIN', 403),
            { approval_token: 'approval-token' },
        ],
    }, async ({ elements, calls, settle }) => {
        elements.fnbApprovalPin.value = '0000';
        elements.fnbCancelReason.value = '  Món đã làm  ';
        elements.fnbCancelResolution.value = 'RESTOCK';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.deepEqual(approvalState(elements), {
            open: true, pin: '', reason: '  Món đã làm  ', resolution: 'RESTOCK', status: 'Sai PIN',
        });
        elements.fnbApprovalPin.value = '2468';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.deepEqual(approvalState(elements), {
            open: false, pin: '', reason: '', resolution: 'WASTE', status: '',
        });
        const approved = calls.filter(call => call.endpoint.endsWith('/cancel-line')).at(-1).body;
        assert.equal(approved.reason, 'Món đã làm');
        assert.equal(approved.resolution, 'RESTOCK');
        assert.equal(approved.approval_token, 'approval-token');
    });

    await withCancellation({
        cancelReplies: [fnbError('FNB_SESSION_CHANGED', 'Món vừa thay đổi', 409, session(5))],
        approvalReplies: [{ approval_token: 'approval-token' }],
    }, async ({ elements, calls, settle }) => {
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = 'Món đã làm';
        elements.fnbCancelResolution.value = 'RESTOCK';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.deepEqual(approvalState(elements), {
            open: false, pin: '', reason: '', resolution: 'WASTE', status: '',
        });
        assert.equal(elements.fnbSessionStatus.textContent, 'fnb.cancel.changed');
        elements.fnbCancelReason.value = 'Không được gửi lại';
        elements.fnbApprovalPin.value = '2468';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.equal(calls.filter(call => call.endpoint === '/fnb/manager-approvals').length, 1);
    });

    for (const [name, teardown] of [
        ['close', async harness => harness.clickAction('close-approval')],
        ['native dialog close', async ({ elements }) => elements.fnbApprovalDialog.close()],
        ['shop change', async ({ elements, settle }) => {
            elements.fnbShopSelect.value = '2';
            elements.fnbShopSelect.emit('change');
            await settle();
        }],
        ['session close', async harness => {
            harness.hideSession();
            harness.emitOnline();
            await harness.settle();
        }],
        ['page disposal', async harness => harness.emitPagehide()],
    ]) {
        await withCancellation({}, async harness => {
            const { elements, calls, settle } = harness;
            elements.fnbApprovalPin.value = '2468';
            elements.fnbCancelReason.value = 'Nhạy cảm';
            elements.fnbCancelResolution.value = 'RESTOCK';
            await teardown(harness);
            assert.deepEqual(approvalState(elements), {
                open: false, pin: '', reason: '', resolution: 'WASTE', status: '',
            }, name);
            elements.fnbApprovalPin.value = '2468';
            elements.fnbCancelReason.value = 'Không được gửi lại';
            elements.fnbApprovalForm.emit('submit');
            await settle();
            assert.equal(
                calls.filter(call => call.endpoint === '/fnb/manager-approvals').length,
                0,
                name,
            );
        });
    }
}

async function testApprovalEndpointConflict() {
    const latest = {
        ...session(9),
        tables: [{ id: 20, name: 'Bàn mới', state_version: 6 }],
    };
    await withCancellation({
        approvalReplies: [fnbError('FNB_SESSION_CHANGED', 'Phiên vừa thay đổi', 409, latest)],
    }, async ({ elements, calls, settle }) => {
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = 'Món đã làm';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.equal(calls.filter(call => call.endpoint.endsWith('/cancel-line')).length, 1);
        assert.equal(elements.fnbSessionTitle.textContent, 'Bàn mới');
        assert.equal(elements.fnbSessionStatus.textContent, 'fnb.cancel.changed');
        assert.deepEqual(approvalState(elements), {
            open: false, pin: '', reason: '', resolution: 'WASTE', status: '',
        });
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = 'Không được gửi lại';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.equal(calls.filter(call => call.endpoint === '/fnb/manager-approvals').length, 1);
    });
}

async function testApprovalClosesAfterBackgroundRevisionChange() {
    await withCancellation({}, async harness => {
        const { elements, calls, settle } = harness;
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = 'Quyết định cũ';
        elements.fnbCancelResolution.value = 'RESTOCK';

        harness.refreshSession(4);
        await settle();

        assert.ok(calls.some(call => call.endpoint === '/fnb/sessions/30'), JSON.stringify(calls));
        assert.deepEqual(approvalState(elements), {
            open: false, pin: '', reason: '', resolution: 'WASTE', status: '',
        });
        assert.equal(elements.fnbSessionStatus.textContent, 'fnb.cancel.changed');
        elements.fnbApprovalForm.emit('submit');
        await settle();
        assert.equal(calls.filter(call => call.endpoint === '/fnb/manager-approvals').length, 0);
        assert.equal(calls.filter(call => call.endpoint.endsWith('/cancel-line')).length, 1);
    });
}

async function testApprovalAsyncRace() {
    for (const outcome of ['error', 'success']) {
        let resolveApproval;
        let rejectApproval;
        const pendingApproval = new Promise((resolve, reject) => {
            resolveApproval = resolve;
            rejectApproval = reject;
        });
        await withCancellation({
            cancelReplies: [
                fnbError('FNB_APPROVAL_REQUIRED', 'Action required', 403),
                session(4),
            ],
            approvalReplies: [pendingApproval],
        }, async harness => {
            const { elements, calls, settle } = harness;
            elements.fnbApprovalPin.value = '2468';
            elements.fnbCancelReason.value = 'Yêu cầu cũ';
            elements.fnbApprovalForm.emit('submit');
            await settle();
            elements.fnbApprovalDialog.close();
            harness.clickAction('cancel-line', 41);
            await settle();
            elements.fnbApprovalPin.value = '9999';
            elements.fnbCancelReason.value = 'Yêu cầu mới';
            elements.fnbCancelResolution.value = 'RESTOCK';
            if (outcome === 'success') resolveApproval({ approval_token: 'old-token' });
            else rejectApproval(fnbError('FNB_APPROVAL_INVALID', 'PIN cũ sai', 403));
            await settle();
            assert.equal(calls.filter(call => call.endpoint.endsWith('/cancel-line')).length, 2, outcome);
            assert.deepEqual(approvalState(elements), {
                open: true, pin: '9999', reason: 'Yêu cầu mới', resolution: 'RESTOCK', status: '',
            }, outcome);
        });
    }

    let resolveCurrentApproval;
    const currentApproval = new Promise(resolve => { resolveCurrentApproval = resolve; });
    await withCancellation({
        cancelReplies: [session(4)],
        approvalReplies: [currentApproval],
    }, async ({ elements, calls, settle }) => {
        elements.fnbApprovalPin.value = '2468';
        elements.fnbCancelReason.value = '  Yêu cầu ban đầu  ';
        elements.fnbCancelResolution.value = 'WASTE';
        elements.fnbApprovalForm.emit('submit');
        await settle();
        elements.fnbCancelReason.value = 'Yêu cầu bị sửa';
        elements.fnbCancelResolution.value = 'RESTOCK';
        resolveCurrentApproval({ approval_token: 'current-token' });
        await settle();
        const approvals = calls.filter(call => call.endpoint === '/fnb/manager-approvals');
        const cancellations = calls.filter(call => call.endpoint.endsWith('/cancel-line'));
        assert.equal(approvals[0].body.entity_id, 30);
        assert.equal(approvals[0].body.revision, 3);
        assert.equal(cancellations[1].body.line_id, 40);
        assert.equal(cancellations[1].body.reason, 'Yêu cầu ban đầu');
        assert.equal(cancellations[1].body.resolution, 'WASTE');
    });
}

async function testCheckoutCashBehavior() {
    const harness = await mountedFnbHarness({ openCheckout: true });
    const { elements, calls, paymentMethod, emitOnline, settle } = harness;
    try {
        elements.fnbPayForm.emit('submit');
        assert.equal(calls.some(call => call.endpoint.endsWith('/pay')), false);
        assert.equal(elements.fnbCashTendered.focused, true);
        assert.equal(elements.fnbCashTenderedError.textContent, 'fnb.checkout.cash_required');

        assert.equal(elements.fnbCashExact.disabled, false);
        elements.fnbVoucherCode.value = 'GIAM10';
        elements.fnbVoucherCode.emit('input');
        assert.equal(elements.fnbCashExact.disabled, true);
        elements.fnbVoucherCode.value = '';
        elements.fnbVoucherCode.emit('input');
        elements.fnbCashExact.emit('click');
        assert.equal(elements.fnbCashExact.disabled, false);
        assert.equal(elements.fnbCashTendered.value, '120000');

        elements.fnbCashTendered.value = '130000';
        elements.fnbCashTenderedError.textContent = 'old error';
        paymentMethod.value = 'transfer';
        elements.fnbPayForm.emit('submit');
        await settle();
        assert.equal(elements.fnbCashTendered.value, '');
        assert.equal(elements.fnbCashTenderedError.textContent, '');
        elements.fnbCheckoutDialog.open = true;
        emitOnline();
        await settle();
        assert.doesNotMatch(elements.fnbCheckDetail.innerHTML, /receipt-one/);
    } finally {
        harness.cleanup();
    }
}

async function testCheckoutFailureRecovery() {
    const rejected = await mountedFnbHarness({
        openCheckout: true,
        payReplies: [fnbError('FNB_CASH_SHORT', 'Tiền khách đưa chưa đủ', 400)],
    });
    try {
        rejected.elements.fnbCashTendered.value = '0';
        rejected.elements.fnbPayForm.emit('submit');
        await rejected.settle();
        assert.equal(rejected.elements.fnbCheckoutStatus.textContent, 'Tiền khách đưa chưa đủ');
        rejected.elements.fnbPayForm.controls.forEach(control =>
            assert.equal(control.disabled, false));
    } finally {
        rejected.cleanup();
    }

    for (const error of [new Error('offline'), fnbError('SERVER_ERROR', 'server error', 500)]) {
        const ambiguous = await mountedFnbHarness({ openCheckout: true, payReplies: [error] });
        try {
            ambiguous.elements.fnbCashTendered.value = '130000';
            ambiguous.elements.fnbPayForm.emit('submit');
            await ambiguous.settle();
            ambiguous.elements.fnbPayForm.controls.forEach(control =>
                assert.equal(control.disabled, true));
            const originalBody = structuredClone(
                ambiguous.calls.find(call => call.endpoint === '/fnb/checks/1/pay').body,
            );

            ambiguous.elements.fnbCashTendered.value = '999999';
            ambiguous.elements.fnbPayForm.emit('submit');
            await ambiguous.settle();
            assert.equal(
                ambiguous.calls.filter(call => call.endpoint === '/fnb/checks/1/pay').length,
                1,
            );
            ambiguous.clickAction('retry-pending');
            await ambiguous.settle();
            const payments = ambiguous.calls.filter(call => call.endpoint === '/fnb/checks/1/pay');
            assert.equal(payments.length, 2);
            assert.deepEqual(payments[1].body, originalBody);
        } finally {
            ambiguous.cleanup();
        }
    }
}

assert.equal(
    escapeHtml('<img src=x onerror=alert(1)>'),
    '&lt;img src=x onerror=alert(1)&gt;',
);
assert.equal(escapeHtml(`&"'`), '&amp;&quot;&#39;');
assert.equal(adjustmentValueForApi('PERCENT', 10), 1000);
assert.equal(adjustmentValueForForm('PERCENT', 1000), 10);
const buckets = partitionLines([
    { id: 1, unsent_quantity: 2, active_sent_quantity: 0 },
    { id: 2, unsent_quantity: 0, active_sent_quantity: 3 },
    { id: 3, unsent_quantity: 1, active_sent_quantity: 2 },
]);
assert.deepEqual(buckets.draft.map(line => line.id), [1, 3]);
assert.deepEqual(buckets.sent.map(line => line.id), [2, 3]);

const menu = [
    { id: 1, name: 'Cà phê sữa - Nhỏ', variant_group: 'Cà phê sữa', variant_name: 'Nhỏ' },
    { id: 2, name: 'Bánh mì', variant_group: null, variant_name: null },
    { id: 3, name: 'Cà phê sữa - Lớn', variant_group: 'Cà phê sữa', variant_name: 'Lớn' },
];
assert.deepEqual(groupMenuProducts(menu).map(entry => ({
    group: entry.group, ids: entry.products.map(product => product.id),
})), [
    { group: 'Cà phê sữa', ids: [1, 3] },
    { group: null, ids: [2] },
]);
assert.deepEqual(groupMenuProducts(menu, 'lớn')[0].products.map(product => product.id), [3]);
assert.deepEqual(groupMenuProducts(menu, 'bánh').map(entry => entry.products[0].id), [2]);

assert.deepEqual(cashTenderedForPayment('transfer', ''), {});
assert.deepEqual(cashTenderedForPayment('debt', '50000'), {});
assert.deepEqual(cashTenderedForPayment('cash', ''), { error: 'required' });
assert.deepEqual(cashTenderedForPayment('cash', '   '), { error: 'required' });
assert.deepEqual(cashTenderedForPayment('cash', '0'), { cash_tendered_vnd: 0 });
assert.deepEqual(cashTenderedForPayment('cash', '120000'), {
    cash_tendered_vnd: 120000,
});
assert.equal(cashExactAllowed('', 0), true);
assert.equal(cashExactAllowed('GIAM10', 0), false);
assert.equal(cashExactAllowed('', 1), false);

Promise.resolve()
    .then(testLateFloorResponseIsIgnoredAfterShopChange)
    .then(testPollingAndLifecycle)
    .then(testSessionAnnouncementsOnlyFollowMutations)
    .then(testConflictKeepsDraftAndUsesAuthoritativeSnapshot)
    .then(testConflictReapplyKeepsOriginalAction)
    .then(testSingleFlightRetryAndDefinitiveFailure)
    .then(testApprovalSecretsAreNeverPersistedForRecovery)
    .then(testCancelDecisionRequiredAfterNetworkFailureClearsPendingAndDraft)
    .then(testCancelConflictAfterNetworkFailureRequiresFreshUserDecision)
    .then(testLatestRevisionBodies)
    .then(testServeTicketUsesCurrentTicketAndSessionRevisions)
    .then(testStationUpdateUsesCurrentFloorRevision)
    .then(testSetupMutationsAndAccess)
    .then(testCheckoutUsesLatestCheckAndSessionRevisions)
    .then(testCancellationApprovalDialogLifecycle)
    .then(testApprovalAsyncRace)
    .then(testApprovalClosesAfterBackgroundRevisionChange)
    .then(testApprovalEndpointConflict)
    .then(testCheckoutCashBehavior)
    .then(testCheckoutFailureRecovery)
    .then(() => process.stdout.write('fnb-r1a controller ok\n'));
