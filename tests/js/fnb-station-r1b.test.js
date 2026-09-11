const assert = require('node:assert/strict');
const {
    createStationController, ticketAgeMinutes, ticketAgeClass,
} = require('../../static/js/fnb-station-r1b.js');

function queue(revision = 3) {
    return { changed: true, station: 'KITCHEN', revision, tickets: [
        { id: 7, status: 'NEW', state_version: 0, session_revision: 5, tables: ['Bàn 1'], items: [] },
    ] };
}

async function run() {
    let mode = 'ok'; const events = [];
    const freshness = createStationController({
        request: async () => { if (mode === 'down') throw Error('offline'); return mode === 'same' ? {changed:false} : queue(); },
        render: e => events.push(e), setTimeoutFn:()=>1, clearTimeoutFn:()=>{}
    });
    await freshness.start(1,'KITCHEN');
    mode='down'; await assert.rejects(freshness.load());
    mode='same'; await freshness.load();
    assert.equal(events.at(-1).type,'synced');
    assert.ok(freshness.getState().lastReadAt > 0);
    for (const [status, unknown] of [[403,false],[409,false],[401,true],[200,true],[503,true]]) {
        const sent=[];
        const c=createStationController({
            request: async (_,method,body)=>{ if(method!=='POST') return queue(); sent.push(body); throw Object.assign(Error('rejected'),{status,mutationOutcomeUnknown:unknown}); },
            render:()=>{}, uuid:()=> 'stable', setTimeoutFn:()=>1,clearTimeoutFn:()=>{}
        });
        await c.start(1,'KITCHEN');
        await assert.rejects(c.transition(7,'start'));
        await c.load();
        assert.equal(Boolean(c.getState().pending),unknown);
        if(unknown) { await assert.rejects(c.retryPending()); assert.deepEqual(sent[0],sent[1]); }
    }
    assert.equal(ticketAgeMinutes('2026-09-03T10:00:00Z', Date.parse('2026-09-03T10:05:59Z')), 5);
    assert.equal(ticketAgeClass(4), '');
    assert.equal(ticketAgeClass(5), 'is-warn');
    assert.equal(ticketAgeClass(10), 'is-late');
    const calls = [];
    const renders = [];
    const clearedTimers = [];
    let operation = 0;
    const controller = createStationController({
        request: async (endpoint, method, body) => {
            calls.push({ endpoint, method, body });
            if (method === 'POST') return { ...queue(4).tickets[0], status: 'IN_PROGRESS', state_version: 1, session_revision: 6, revision: 4 };
            return queue();
        },
        render: event => renders.push(event),
        uuid: () => `queue-op-${++operation}`,
        setTimeoutFn: () => 1,
        clearTimeoutFn: timer => clearedTimers.push(timer),
        isHidden: () => false,
    });
    await controller.start(1, 'KITCHEN');
    assert.equal(renders.find(e => e.type === 'queue').value.tickets[0].id, 7);
    await controller.transition(7, 'start');
    assert.equal(calls[1].endpoint, '/fnb/tickets/7/start');
    assert.equal(calls[1].body.expected_state_version, 0);
    assert.equal(calls[1].body.expected_session_revision, 5);
    assert.match(calls[1].body.operation_id, /^queue-op-/);
    await controller.transition(7, 'resume');
    assert.equal(calls[2].endpoint, '/fnb/tickets/7/resume');
    assert.equal(calls[2].body.expected_session_revision, 6);
    await controller.load(false);
    assert.match(calls.at(-1).endpoint, /after_revision=4/);
    assert.deepEqual(clearedTimers, [1], 'manual reload must replace the scheduled poll');

    let attempts = 0;
    const bodies = [];
    const retry = createStationController({
        request: async (endpoint, method, body) => {
            if (method !== 'POST') return queue();
            bodies.push(body);
            attempts += 1;
            if (attempts === 1) throw new Error('offline');
            return { ...queue().tickets[0], status: 'IN_PROGRESS', state_version: 1, session_revision: 6, revision: 4 };
        },
        render: () => {}, uuid: () => 'same-operation',
        setTimeoutFn: () => 1, clearTimeoutFn: () => {}, isHidden: () => false,
    });
    await retry.start(1, 'KITCHEN');
    await assert.rejects(retry.transition(7, 'start'));
    await assert.rejects(
        retry.transition(7, 'start'),
        error => error.code === 'FNB_MUTATION_PENDING',
    );
    assert.equal(bodies.length, 1);
    await retry.retryPending();
    assert.equal(bodies[0].operation_id, bodies[1].operation_id);

    const pendingLoads = [];
    const late = createStationController({
        request: endpoint => new Promise(resolve => pendingLoads.push({ endpoint, resolve })),
        render: () => {}, setTimeoutFn: () => 1, clearTimeoutFn: () => {}, isHidden: () => false,
    });
    const first = late.start(1, 'KITCHEN');
    const second = late.start(2, 'BAR');
    pendingLoads.find(row => row.endpoint.includes('shop_id=2')).resolve({
        ...queue(8), station: 'BAR', tickets: [{ id: 9, status: 'NEW', state_version: 0, session_revision: 2 }],
    });
    await second;
    pendingLoads.find(row => row.endpoint.includes('shop_id=1')).resolve(queue(99));
    await first;
    assert.equal(late.getState().shopId, 2);
    assert.equal(late.getState().station, 'BAR');
    assert.deepEqual(late.getState().tickets.map(ticket => ticket.id), [9]);
}

run().then(() => process.stdout.write('fnb station controller ok\n'));
