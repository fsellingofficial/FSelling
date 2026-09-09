'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('static/js/pos.js', 'utf8');
const start = source.indexOf('// RETURN_R3_CONTROLLER_START');
const end = source.indexOf('// RETURN_R3_CONTROLLER_END');
assert(start >= 0 && end > start, 'missing return R3 controller block');

let sequence = 0;
const context = { taoOperationId: () => `operation-${++sequence}` };
vm.createContext(context);
vm.runInContext(`${source.slice(start, end)};
    this.begin = returnR3Begin;
    this.edit = returnR3Edit;
    this.payload = returnR3Payload;
    this.approvalRequired = returnR3ApprovalRequired;
    this.approved = returnR3Approved;
    this.approvalInvalid = typeof returnR3ApprovalInvalid === 'function'
        ? returnR3ApprovalInvalid : null;
    this.unknown = returnR3Unknown;
    this.succeeded = returnR3Succeeded;
    this.state = returnR3State;
`, context);

const draft = {
    items: [{ order_item_id: 42, quantity: 1, restock: false }],
    method: 'cash',
    reason: 'Hàng hỏng',
    reference: null
};

context.begin(draft);
assert.equal(context.state().state, 'submitting');
assert.equal(context.payload().operation_id, 'operation-1');

context.edit({
    ...draft,
    items: [{ order_item_id: 42, quantity: 2, restock: false }]
});
assert.equal(context.state().state, 'submitting');
assert.equal(context.payload().items[0].quantity, 1);
assert.equal(context.payload().operation_id, 'operation-1');

context.approvalRequired();
assert.equal(context.state().state, 'approval-required');
context.edit({ ...draft, reason: 'Lý do mới' });
assert.equal(context.state().approvalToken, null);
assert.equal(context.state().state, 'editing');
assert.equal(context.payload().operation_id, 'operation-1');

context.approved('a'.repeat(43));
assert.equal(context.payload().approval_token, 'a'.repeat(43));
assert.equal(context.payload().operation_id, 'operation-1');

assert.equal(typeof context.approvalInvalid, 'function');
context.approvalInvalid();
assert.equal(context.state().state, 'approval-required');
assert.equal(context.payload().approval_token, undefined);
assert.equal(context.payload().operation_id, 'operation-1');

context.begin(context.state().draft);
context.unknown();
assert.equal(context.state().state, 'unknown');
assert.equal(context.payload().operation_id, 'operation-1');

context.edit({ ...draft, reason: 'Không được ghi đè phiếu chưa rõ kết quả' });
assert.equal(context.state().state, 'unknown');
assert.equal(context.payload().reason, 'Lý do mới');

context.begin(context.state().draft);
assert.equal(context.state().state, 'submitting');
assert.equal(context.payload().reason, 'Lý do mới');
assert.equal(context.payload().operation_id, 'operation-1', 'network retry reuses id');
context.succeeded();
assert.equal(context.state(), null);

const elements = new Map();
const failedContext = {
    taoOperationId: () => 'failed-operation',
    datNutDangXuLy: () => {},
    apiCall: async () => {
        throw { status: 400, message: 'known failure' };
    },
    showToast: () => {},
    document: {
        getElementById: id => {
            if (!elements.has(id)) {
                elements.set(id, { hidden: false, innerText: '', value: '' });
            }
            return elements.get(id);
        }
    }
};
vm.createContext(failedContext);
const submitEnd = source.indexOf('function moDuyetTraHang(context)');
vm.runInContext(`${source.slice(start, submitEnd)};
    donDangTra = { id: 1 };
    this.begin = returnR3Begin;
    this.edit = returnR3Edit;
    this.payload = returnR3Payload;
    this.submit = guiPhieuTraHangDangDo;
    this.state = returnR3State;
`, failedContext);

(async () => {
    failedContext.begin(draft);
    await failedContext.submit();
    assert.equal(failedContext.state().state, 'editing');
    failedContext.edit({
        ...draft,
        items: [{ order_item_id: 42, quantity: 2, restock: false }]
    });
    assert.equal(failedContext.payload().items[0].quantity, 2);
    console.log('pos-return-r3.test.js: PASS');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
