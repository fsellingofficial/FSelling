'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('static/js/pos.js', 'utf8');
const start = source.indexOf('// RECEIPT_ACTIONS_R1_START');
const end = source.indexOf('// RECEIPT_ACTIONS_R1_END');
assert(start >= 0 && end > start, 'missing Receipt R1 action block');
const posHtml = fs.readFileSync('static/pos.html', 'utf8');

assert.match(posHtml, /id="btnSalesHistory"[^>]+onclick="moLichSuDon\(\)"/);
assert.match(posHtml, /id="salesHistoryModal"[^>]+role="dialog"[^>]+aria-labelledby="salesHistoryTitle"/);
assert.match(posHtml, /id="hoaDonSection"[^>]+role="region"[^>]+tabindex="-1"[^>]+data-i18n-aria-label="pos\.receipt\.region_label"/);

{
    const localeContext = { window: {} };
    vm.runInNewContext(
        fs.readFileSync('static/js/locales/pos.js', 'utf8'),
        localeContext
    );
    const resources = localeContext.window.FSELLING_I18N_RESOURCES;
    assert.equal(resources.vi.translation['pos.receipt.print'], 'In hóa đơn');
    assert.equal(resources.vi.translation['pos.receipt.share'], 'Chia sẻ ảnh');
    assert.equal(resources.vi.translation['pos.receipt.copy'], 'Sao chép');
    assert.equal(resources.vi.translation['pos.receipt.image_copied'], 'Đã sao chép ảnh hóa đơn. Mở Zalo và dán để gửi.');
    assert.equal(resources.vi.translation['pos.receipt.image_downloaded'], 'Đã tải ảnh hóa đơn xuống máy.');
    assert.equal(resources.vi.translation['pos.receipt.copy_not_allowed'], 'Chỉ chủ cửa hàng được sao chép nội dung hóa đơn.');
    assert.equal(resources.vi.translation['pos.sales_history.open'], 'Lịch sử đơn');
    assert.equal(resources.vi.translation['pos.sales_history.title'], 'Lịch sử đơn');
    assert.equal(resources.en.translation['pos.receipt.print'], 'Print receipt');
    assert.equal(resources.en.translation['pos.receipt.share'], 'Share image');
    assert.equal(resources.en.translation['pos.receipt.copy'], 'Copy');
    assert.equal(resources.en.translation['pos.receipt.image_copied'], 'Receipt image copied. Open Zalo and paste it to send.');
    assert.equal(resources.en.translation['pos.receipt.image_downloaded'], 'Receipt image downloaded.');
    assert.equal(resources.en.translation['pos.receipt.copy_not_allowed'], 'Only the store owner can copy receipt text.');
    assert.equal(resources.en.translation['pos.sales_history.open'], 'Order history');
    assert.equal(resources.en.translation['pos.sales_history.title'], 'Order history');
}

function createContext(overrides = {}) {
    const toMoney = value => `${Math.round(Number(value) || 0).toLocaleString('vi-VN')} ₫`;
    const drawnReceipt = [];
    const canvasContext = {
        fillStyle: '',
        font: '',
        textAlign: 'left',
        textBaseline: 'top',
        fillRect() {},
        fillText: text => { drawnReceipt.push(String(text)); }
    };
    const context = {
        console,
        atob,
        Blob,
        File,
        navigator: {},
        window: { print() {} },
        document: {
            createElement: tag => {
                assert.equal(tag, 'canvas');
                return {
                    width: 0,
                    height: 0,
                    getContext: () => canvasContext,
                    toDataURL: () => 'data:image/png;base64,cG5n',
                    toBlob: () => { throw new Error('async canvas export forfeits share activation'); }
                };
            }
        },
        dich: key => key,
        showToast() {},
        dinhDangSoHoaDon: value => Math.round(Number(value) || 0).toLocaleString('vi-VN'),
        dinhDangTienHoaDon: toMoney,
        dinhDangNgayGioHoaDon: () => '29/08/2026, 10:30:00',
        localStorage: { getItem: () => 'thu-ngan-a' },
        ...overrides
    };
    context.drawnReceipt = drawnReceipt;
    vm.createContext(context);
    vm.runInContext(`${source.slice(start, end)};
        this.buildReceipt = taoNoiDungHoaDonChiaSe;
        this.setReceipt = value => { duLieuHoaDonHienTai = value; };
        this.shareReceipt = chiaSeHoaDon;
        this.copyReceipt = saoChepHoaDon;
        this.printReceipt = inHoaDon;
    `, context);
    return context;
}

function order(overrides = {}) {
    return {
        id: 42,
        shop_name: 'Tạp hóa An Nhiên',
        created_at: '2026-08-29T03:30:00Z',
        cashier_username: 'thu-ngan-a',
        bank_paid_amount: 0,
        cash_paid_amount: 24000,
        cash_tendered_amount: 30000,
        cash_change_amount: 6000,
        subtotal: 24000,
        discount_amount: 0,
        total_amount: 24000,
        refund_pending: false,
        items: [{
            product_name: 'Nước mắm',
            price: 12000,
            quantity: 2,
            line_total: 24000
        }],
        customer: { name: 'Cô Lan', phone: '0774867057' },
        ...overrides
    };
}

{
    const context = createContext();
    const text = context.buildReceipt(order());
    assert.match(text, /Tạp hóa An Nhiên/);
    assert.match(text, /Nước mắm/);
    assert.match(text, /TỔNG CỘNG: 24\.000 ₫/);
    assert.match(text, /Khách hàng: Cô Lan/);
    assert.doesNotMatch(text, /0774867057/);
    assert.match(
        context.buildReceipt(order({
            fnb_table_names: ['Bàn 1', 'Bàn 2'],
            fnb_check_label: 'Khách 2'
        })),
        /Bàn: Bàn 1 \+ Bàn 2\nBill: Khách 2/
    );
    assert.match(
        context.buildReceipt(order({
            payment_method: 'debt',
            cash_paid_amount: 0,
            cash_tendered_amount: null
        })),
        /Thanh toán: Ghi nợ/
    );
    const returnedCopy = context.buildReceipt(order({
        receipt_copy: true,
        returned_total: 12000,
        items: [{
            product_name: 'Nước mắm',
            price: 12000,
            quantity: 2,
            line_total: 24000,
            returned_quantity: 1
        }]
    }));
    assert.match(returnedCopy, /HÓA ĐƠN BÁN HÀNG · BẢN SAO/);
    assert.match(returnedCopy, /Đã trả: 1/);
    assert.match(returnedCopy, /ĐÃ HOÀN KHÁCH: - 12\.000 ₫/);
    assert.match(returnedCopy, /GIÁ TRỊ CÒN LẠI: 12\.000 ₫/);
}

{
    const receipt = { innerHTML: '' };
    const warning = { style: {}, innerText: '' };
    const copyButton = { hidden: false };
    const renderContext = {
        document: {
            getElementById: id => ({
                hoaDonNoiDung: receipt,
                hoaDonCanhBao: warning,
                btnCopyReceipt: copyButton
            })[id] || null
        },
        navigator: {},
        window: {},
        dich: key => key,
        showToast() {},
        localStorage: {
            getItem: key => key === 'role' ? 'STAFF' : 'thu-ngan-a'
        },
        escapeHtml: value => String(value ?? ''),
        dinhDangSoHoaDon: value => Math.round(Number(value) || 0).toLocaleString('vi-VN'),
        dinhDangTienHoaDon: value => `${Math.round(Number(value) || 0).toLocaleString('vi-VN')} ₫`,
        dinhDangNgayGioHoaDon: () => '29/08/2026, 10:30:00'
    };
    const renderStart = source.indexOf('function veHoaDon(');
    const renderEnd = source.indexOf('function dongHoaDon(', renderStart);
    assert(renderStart >= 0 && renderEnd > renderStart, 'missing receipt renderer');
    vm.createContext(renderContext);
    vm.runInContext(`${source.slice(start, end)}\n${source.slice(renderStart, renderEnd)}; this.renderReceipt = veHoaDon;`, renderContext);
    renderContext.renderReceipt(order());
    assert.match(receipt.innerHTML, /Khách hàng:<\/b> Cô Lan/);
    assert.doesNotMatch(receipt.innerHTML, /0774867057/);
    assert.equal(copyButton.hidden, true);

    renderContext.renderReceipt(order({
        receipt_copy: true,
        returned_total: 12000,
        items: [{
            product_name: 'Nước mắm',
            price: 12000,
            quantity: 2,
            line_total: 24000,
            returned_quantity: 1
        }]
    }));
    assert.match(receipt.innerHTML, /HÓA ĐƠN BÁN HÀNG · BẢN SAO/);
    assert.match(receipt.innerHTML, /Đã trả: 1/);
    assert.match(receipt.innerHTML, /ĐÃ HOÀN KHÁCH/);
    assert.match(receipt.innerHTML, /GIÁ TRỊ CÒN LẠI/);
}

(async () => {
    let payload = null;
    const context = createContext({
        navigator: {
            canShare: value => value.files?.[0]?.type === 'image/png',
            share: async value => { payload = value; }
        }
    });
    context.setReceipt(order());
    await context.shareReceipt();
    assert.equal(payload.title, 'Hóa đơn #42 · Tạp hóa An Nhiên');
    assert.equal(payload.files.length, 1);
    assert.equal(payload.files[0].name, 'hoa-don-42.png');
    assert.equal(payload.files[0].type, 'image/png');
    assert.equal(Object.hasOwn(payload, 'text'), false);
    assert.match(context.drawnReceipt.join('\n'), /Nước mắm/);
    assert.match(context.drawnReceipt.join('\n'), /Tạo từ F-Selling · Đơn #42/);
    assert.doesNotMatch(context.drawnReceipt.join('\n'), /0774867057/);

    let copied = null;
    let toast = null;
    const copyContext = createContext({
        navigator: {
            clipboard: {
                writeText: async text => { copied = text; }
            }
        },
        showToast: message => { toast = message; }
    });
    copyContext.setReceipt(order());
    await copyContext.copyReceipt();
    assert.match(copied, /HÓA ĐƠN BÁN HÀNG/);
    assert.doesNotMatch(copied, /0774867057/);
    assert.equal(toast, 'pos.receipt.copied');

    let staffCopied = null;
    let staffToast = null;
    const staffCopyContext = createContext({
        navigator: {
            clipboard: {
                writeText: async text => { staffCopied = text; }
            }
        },
        localStorage: {
            getItem: key => key === 'role' ? 'STAFF' : 'nhan-vien-a'
        },
        showToast: message => { staffToast = message; }
    });
    staffCopyContext.setReceipt(order());
    await staffCopyContext.copyReceipt();
    assert.equal(staffCopied, null);
    assert.equal(staffToast, 'pos.receipt.copy_not_allowed');

    let legacyField = null;
    let legacySelected = false;
    let legacyRemoved = false;
    let legacyToast = null;
    const legacyContext = createContext({
        document: {
            body: { appendChild: field => { legacyField = field; } },
            createElement: () => ({
                value: '',
                style: {},
                setAttribute() {},
                focus() {},
                select() { legacySelected = true; },
                remove() { legacyRemoved = true; }
            }),
            execCommand: command => command === 'copy'
        },
        showToast: message => { legacyToast = message; }
    });
    legacyContext.setReceipt(order());
    await legacyContext.copyReceipt();
    assert.match(legacyField.value, /Nước mắm/);
    assert.equal(legacySelected, true);
    assert.equal(legacyRemoved, true);
    assert.equal(legacyToast, 'pos.receipt.copied');

    let rejectedFallbackUsed = false;
    const rejectedClipboardContext = createContext({
        navigator: {
            clipboard: {
                writeText: async () => { throw new Error('permission denied'); }
            }
        },
        document: {
            body: { appendChild() {} },
            createElement: () => ({
                value: '',
                style: {},
                setAttribute() {},
                focus() {},
                select() {},
                remove() {}
            }),
            execCommand: command => {
                rejectedFallbackUsed = command === 'copy';
                return rejectedFallbackUsed;
            }
        }
    });
    rejectedClipboardContext.setReceipt(order());
    await rejectedClipboardContext.copyReceipt();
    assert.equal(rejectedFallbackUsed, true);

    let fallbackShareItems = null;
    let fallbackShareToast = null;
    class TestClipboardItem {
        constructor(items) { this.items = items; }
    }
    const fallbackShareContext = createContext({
        ClipboardItem: TestClipboardItem,
        navigator: {
            clipboard: {
                write: async items => { fallbackShareItems = items; }
            }
        },
        showToast: message => { fallbackShareToast = message; }
    });
    fallbackShareContext.setReceipt(order());
    await fallbackShareContext.shareReceipt();
    assert.equal(fallbackShareItems.length, 1);
    assert.equal(fallbackShareItems[0].items['image/png'].type, 'image/png');
    assert.equal(fallbackShareToast, 'pos.receipt.image_copied');

    let failedShareItems = null;
    let failedShareToast = null;
    const failedShareContext = createContext({
        ClipboardItem: TestClipboardItem,
        navigator: {
            canShare: () => true,
            share: async () => { throw new Error('share failed'); },
            clipboard: {
                write: async items => { failedShareItems = items; }
            }
        },
        showToast: message => { failedShareToast = message; }
    });
    failedShareContext.setReceipt(order());
    await failedShareContext.shareReceipt();
    assert.equal(failedShareItems.length, 1);
    assert.equal(failedShareItems[0].items['image/png'].type, 'image/png');
    assert.equal(failedShareToast, 'pos.receipt.image_copied');

    let downloadAnchor = null;
    let revokedUrl = null;
    let downloadToast = null;
    const downloadContext = createContext({
        navigator: {},
        URL: {
            createObjectURL: file => {
                assert.equal(file.type, 'image/png');
                return 'blob:receipt-42';
            },
            revokeObjectURL: url => { revokedUrl = url; }
        },
        setTimeout: callback => callback(),
        showToast: message => { downloadToast = message; }
    });
    const createElement = downloadContext.document.createElement;
    downloadContext.document.body = { appendChild() {} };
    downloadContext.document.createElement = tag => {
        if (tag !== 'a') return createElement(tag);
        downloadAnchor = {
            href: '',
            download: '',
            clicked: false,
            removed: false,
            click() { this.clicked = true; },
            remove() { this.removed = true; }
        };
        return downloadAnchor;
    };
    downloadContext.setReceipt(order());
    await downloadContext.shareReceipt();
    assert.equal(downloadAnchor.download, 'hoa-don-42.png');
    assert.equal(downloadAnchor.clicked, true);
    assert.equal(downloadAnchor.removed, true);
    assert.equal(revokedUrl, 'blob:receipt-42');
    assert.equal(downloadToast, 'pos.receipt.image_downloaded');

    let printCount = 0;
    const printContext = createContext({
        window: { print: () => { printCount += 1; } }
    });
    printContext.setReceipt(order());
    printContext.printReceipt();
    assert.equal(printCount, 1);

    let openedPayload = null;
    const openedContext = createContext({
        navigator: {
            canShare: value => value.files?.[0]?.type === 'image/png',
            share: async value => { openedPayload = value; }
        }
    });
    let scrolledReceipt = false;
    let focusedReceiptAction = false;
    openedContext.document.getElementById = id => ({
        style: {},
        scrollIntoView() { if (id === 'hoaDonSection') scrolledReceipt = true; }
    });
    openedContext.document.querySelector = selector => selector === '#hoaDonActions button'
        ? { focus() { focusedReceiptAction = true; } } : null;
    openedContext.apiCall = async () => order();
    openedContext.resetPOS = () => {};
    openedContext.veHoaDon = () => {};
    openedContext.showFirstRunSaleSuccess = () => {};
    openedContext.capNhatGioHangResponsivePOS = () => {};
    openedContext.posMobileCartMedia = { matches: true };
    openedContext.moGioHangMobile = () => {};
    const openStart = source.indexOf('async function hienHoaDon(');
    const openEnd = source.indexOf('function showFirstRunSaleSuccess(', openStart);
    assert(openStart >= 0 && openEnd > openStart, 'missing receipt opening flow');
    vm.runInContext(`${source.slice(openStart, openEnd)}; this.openReceipt = hienHoaDon;`, openedContext);
    await openedContext.openReceipt(42);
    assert.equal(scrolledReceipt, true);
    assert.equal(focusedReceiptAction, true);
    await openedContext.shareReceipt();
    assert.equal(openedPayload.title, 'Hóa đơn #42 · Tạp hóa An Nhiên');

    openedPayload = null;
    openedContext.dismissFirstRunSaleSuccess = () => {};
    const closeStart = source.indexOf('function dongHoaDon(');
    const closeEnd = source.indexOf('function resetPOS(', closeStart);
    assert(closeStart >= 0 && closeEnd > closeStart, 'missing receipt close flow');
    vm.runInContext(`${source.slice(closeStart, closeEnd)}; this.closeReceipt = dongHoaDon;`, openedContext);
    openedContext.closeReceipt();
    await openedContext.shareReceipt();
    assert.equal(openedPayload, null);

    console.log('receipt-actions-r1 harness: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
