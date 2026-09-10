// Chủ shop (SELLER) và nhân viên (STAFF) đều dùng trang này. Vai trò quyết định
// những gì được hiển thị (xem applyRoleUI ở cuối file).
const MY_ROLE = localStorage.getItem('role');
if(!['ADMIN', 'SELLER', 'STAFF'].includes(MY_ROLE)) redirectToLogin();
const MY_STAFF_ROLE = MY_ROLE === 'STAFF'
    ? (localStorage.getItem('staff_role') || 'MANAGER').toUpperCase()
    : null;
if (MY_ROLE === 'STAFF' && ['KITCHEN', 'BAR'].includes(MY_STAFF_ROLE)) {
    navigateToPage(`/fnb/station/${MY_STAFF_ROLE.toLowerCase()}`);
}
if (MY_ROLE === 'STAFF' && MY_STAFF_ROLE === 'SERVICE') {
    navigateToPage('/fnb');
}
const STAFF_UI_PERMISSIONS = Object.freeze({
    CASHIER: new Set(['SALE', 'CUSTOMER']),
    SERVICE: new Set(),
    WAREHOUSE: new Set(['INVENTORY']),
    MANAGER: new Set(['SALE', 'INVENTORY', 'CUSTOMER', 'REPORT', 'VOUCHER', 'RECONCILIATION'])
});

function coQuyenNhanVien(permission) {
    if (MY_ROLE !== 'STAFF') return true;
    return STAFF_UI_PERMISSIONS[MY_STAFF_ROLE]?.has(permission) === true;
}

// Giá vốn và lãi chỉ dành cho chủ cửa hàng, kể cả nhân viên MANAGER (đang xem
// được doanh thu) cũng không thấy. Đây CHỈ là lớp giao diện cho gọn mắt - server
// mới là chỗ chặn thật (has_cost_visibility trong dependencies.py). Đừng bao giờ
// coi cờ này là biện pháp bảo mật: sửa localStorage là qua được.
const XEM_DUOC_GIA_VON = MY_ROLE === 'SELLER' || MY_ROLE === 'ADMIN';

// Giá vốn theo product_id, nạp riêng qua endpoint chỉ chủ shop gọi được. Danh
// sách sản phẩm mở cho cả nhân viên nên KHÔNG kèm giá vốn - phải ghép ở đây.
let giaVonTheoSanPham = {};
let soSanPhamChuaKhaiGiaVon = 0;

const BANKS = [
    { code: 'VCB', label: 'Vietcombank (VCB)' },
    { code: 'ACB', label: 'ACB' },
    { code: 'BIDV', label: 'BIDV' },
    { code: 'CTG', label: 'VietinBank (CTG)' },
    { code: 'MB', label: 'MBBank (MB)' },
    { code: 'TCB', label: 'Techcombank (TCB)' },
    { code: 'TPB', label: 'TPBank (TPB)' },
    { code: 'VPB', label: 'VPBank (VPB)' },
    { code: 'HDB', label: 'HDBank (HDB)' },
    { code: 'VIB', label: 'VIB' },
    { code: 'OCB', label: 'OCB' },
    { code: 'SCB', label: 'SCB' },
    { code: 'SHB', label: 'SHB' },
    { code: 'EIB', label: 'Eximbank (EIB)' },
    { code: 'MSB', label: 'MSB' },
    { code: 'NCB', label: 'NCB' },
    { code: 'ABB', label: 'ABBank (ABB)' },
    { code: 'STB', label: 'Sacombank (STB)' }
];

let allShops = [];
let currentShopId = null;
let editShopId = null; // null = create mode, id = edit mode
const fnbSettingOperations = new Map();
let dashboardShopId = null;
let chartInstance = null;
let pieChartInstance = null;
let doiSoatShopId = null;
let trangDoiSoat = 1;
const DOI_SOAT_MOI_TRANG = 50;
let doiSoatRequestId = 0;
let doiSoatBadgeCount = 0;
let refundOrderId = null;
let refundDueAmount = 0;
let refundOperationId = null;
let dangLuuHoanTien = false;
let refundReason = null;
let currentCategories = [];
let currentCategoriesShopId = null;
let currentProductsShopId = null;
let currentVouchersShopId = null;
let currentStaff = [];
let currentStaffShopId = null;
let openOrderDetailId = null;
let openCustomerHistoryId = null;
let currentCustomersShopId = null;
let currentCustomersQuery = '';
let dashboardOrdersCache = null;
let dashboardStatsCache = null;
let shiftHistoryCache = null;
let reconciliationCache = null;
let orderDetailCache = null;
let customerHistoryCache = null;
let customerHistoryShopId = null;
let currentShopGeneration = 0;
let loadedCurrentShopId = null;
let categoriesRequestId = 0;
let productsRequestId = 0;
let vouchersRequestId = 0;
let dashboardRequestId = 0;
let shiftHistoryRequestId = 0;
let doiSoatBadgeRequestId = 0;
let staffRequestId = 0;
let customersRequestId = 0;
let customerHistoryRequestId = 0;
let loyaltyProgramCache = null;
let loyaltyProgramShopId = null;
let loyaltyRequestId = 0;
let loyaltySaveBusy = false;
let actionCenterRequestId = 0;
let productImportFile = null;
let productImportPreviewData = null;
let productImportOperationId = null;
let productImportLastOperationId = null;
let productImportBusy = false;

const ACTION_CENTER_TARGETS = Object.freeze({
    ORDER_RECONCILIATION: Object.freeze({ tab: 'reconciliation', icon: 'ph-warning-diamond' }),
    UNAPPLIED_BANK_EVENTS: Object.freeze({ tab: 'reconciliation', icon: 'ph-bank' }),
    OFFLINE_ISSUES: Object.freeze({ tab: 'reconciliation', icon: 'ph-cloud-slash' }),
    STOCK_RISK: Object.freeze({ tab: 'warehouse', subTab: 'expiry', icon: 'ph-calendar-x' }),
    REORDER: Object.freeze({ tab: 'warehouse', subTab: 'forecast', icon: 'ph-trend-up' }),
    OVERDUE_PURCHASE_ORDERS: Object.freeze({ tab: 'purchasing', subTab: 'orders', icon: 'ph-truck' }),
    EXPENSE_REMINDERS: Object.freeze({ tab: 'cashflow', icon: 'ph-calendar-dots' }),
    SUPPLIER_OVERDUE: Object.freeze({ tab: 'purchasing', subTab: 'suppliers', icon: 'ph-hand-coins' }),
    CUSTOMER_DEBT: Object.freeze({ tab: 'customers', icon: 'ph-users' })
});

const DAILY_CLOSE_TARGETS = Object.freeze({
    OPEN_SHIFTS: Object.freeze({ tab: 'dashboard', icon: 'ph-cash-register' }),
    CASH_VARIANCE: Object.freeze({ tab: 'dashboard', icon: 'ph-scales' }),
    ORDER_RECONCILIATION: ACTION_CENTER_TARGETS.ORDER_RECONCILIATION,
    UNAPPLIED_BANK_EVENTS: ACTION_CENTER_TARGETS.UNAPPLIED_BANK_EVENTS,
    OFFLINE_ISSUES: ACTION_CENTER_TARGETS.OFFLINE_ISSUES,
    EXPENSE_REMINDERS: ACTION_CENTER_TARGETS.EXPENSE_REMINDERS
});

function dinhDangSoSeller(value, options = {}) {
    return window.FSellingI18n?.formatNumber(value, options)
        ?? Number(value || 0).toLocaleString('vi-VN', options);
}

function dinhDangNhanNgayBieuDo(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!match) return String(value || '');
    const date = new Date(
        Number(match[1]),
        Number(match[2]) - 1,
        Number(match[3])
    );
    return new Intl.DateTimeFormat(
        window.FSellingI18n?.getIntlLocale?.() || 'vi-VN',
        { day: '2-digit', month: 'short' }
    ).format(date);
}

function capNhatNhanNut(selector, key) {
    const label = document.querySelector(selector);
    if (label) label.textContent = t(key);
}

function cacheThuocShop(cacheShopId, shopId) {
    return Number.isInteger(Number(shopId))
        && Number(cacheShopId) === Number(shopId);
}

function damBaoCacheShopHienTai(cacheShopId) {
    if (cacheThuocShop(cacheShopId, currentShopId)) return true;
    showToast(t('common.loading'));
    return false;
}

function shopDangChon(selectorId) {
    const value = Number(document.getElementById(selectorId)?.value);
    return Number.isInteger(value) && value > 0 ? value : null;
}

function damBaoCacheShopTrongSelector(cacheShopId, selectorId) {
    const selectedShopId = shopDangChon(selectorId);
    if (selectedShopId && cacheThuocShop(cacheShopId, selectedShopId)) {
        return selectedShopId;
    }
    showToast(t('common.loading'));
    return null;
}

function xoaDuLieuShopCuKhoiGiaoDien() {
    currentCategories = [];
    currentCategoriesShopId = null;
    currentProducts = [];
    currentProductsShopId = null;
    currentVouchers = [];
    currentVouchersShopId = null;
    categoriesRequestId += 1;
    productsRequestId += 1;
    vouchersRequestId += 1;
    loyaltyRequestId += 1;
    actionCenterRequestId += 1;
    loyaltyProgramCache = null;
    loyaltyProgramShopId = null;
    window.FSellingSubscriptions?.resetSellerForShopChange?.();
    // Module Nhập hàng nằm ở file riêng để seller.js không phình thêm hàng
    // nghìn dòng. Nó vẫn dùng cùng generation/shop hiện tại của trang này.
    window.FSellingPurchasing?.resetForShopChange?.();
    window.FSellingExpenses?.resetForShopChange?.();
    resetProductImportState();

    cancelEditCategory();
    cancelEditProduct();
    cancelEditVoucher();
    kkXoaHet();

    const catSelect = document.getElementById('catSelect');
    if (catSelect) catSelect.innerHTML = '';
    const filterCatSelect = document.getElementById('filterCatSelect');
    if (filterCatSelect) {
        filterCatSelect.innerHTML = `<option value="">${escapeHtml(t('seller.filters.all'))}</option>`;
    }
    const categoryTableBody = document.getElementById('categoryTableBody');
    if (categoryTableBody) categoryTableBody.innerHTML = '';
    const productList = document.getElementById('prodList');
    if (productList) productList.innerHTML = '';
    const voucherList = document.getElementById('voucherList');
    if (voucherList) voucherList.innerHTML = '';
    const actionList = document.getElementById('actionCenterList');
    if (actionList) actionList.innerHTML = '';
    const dailyClosePanel = document.getElementById('dailyClosePanel');
    if (dailyClosePanel) dailyClosePanel.style.display = 'none';
    const dailyCloseList = document.getElementById('dailyCloseList');
    if (dailyCloseList) dailyCloseList.innerHTML = '';
    const actionBadge = document.getElementById('actionCenterBadge');
    if (actionBadge) actionBadge.style.display = 'none';
    ['actionCenterCritical', 'actionCenterAttention', 'actionCenterPlan'].forEach(id => {
        const value = document.getElementById(id);
        if (value) value.textContent = '0';
    });
}

function batDauDungShopHienTai() {
    const shopId = Number(currentShopId);
    if (!Number.isInteger(shopId) || shopId <= 0) return false;
    if (loadedCurrentShopId !== shopId) {
        loadedCurrentShopId = shopId;
        currentShopGeneration += 1;
        xoaDuLieuShopCuKhoiGiaoDien();
    }
    return true;
}

function renderBankOptions() {
    const bankSelect = document.getElementById('bankCode');
    if (!bankSelect) return;
    const selected = bankSelect.value;
    bankSelect.innerHTML = `<option value="" disabled>${escapeHtml(t('seller.shops.choose_bank'))}</option>` +
        BANKS.map(bank => `<option value="${bank.code}">${bank.label}</option>`).join('');
    bankSelect.value = BANKS.some(bank => bank.code === selected) ? selected : '';
}

function setSellerNavigation(open) {
    document.body.classList.toggle('seller-nav-open', Boolean(open));
    const toggle = document.getElementById('sellerNavToggle');
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function capNhatNhomDieuHuong() {
    document.querySelectorAll('.seller-nav-group, .seller-nav-utilities').forEach(group => {
        const coTabHien = Array.from(group.querySelectorAll('.tab-btn[data-main-tab]'))
            .some(button => button.style.display !== 'none');
        group.hidden = !coTabHien;
    });
}

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setSellerNavigation(false);
});

function switchTab(tabId, buttonEl = null) {
    if (tabId === 'action-center' && MY_ROLE !== 'SELLER') {
        showToast(t('seller.action_center.owner_only'));
        return;
    }
    if (tabId === 'loyalty' && MY_ROLE !== 'SELLER') {
        showToast(t('seller.loyalty.owner_only'));
        return;
    }
    if (tabId === 'purchasing' && !['SELLER', 'ADMIN'].includes(MY_ROLE)) {
        showToast(t('seller.purchasing.owner_only'));
        return;
    }
    // Chi phí và lãi ròng cùng vòng bí mật với giá vốn: biết lãi ròng là suy
    // ngược ra được giá vốn, và lương nhân viên thì càng không phải thứ để
    // nhân viên khác đọc. Đây chỉ là lớp giao diện — server mới chặn thật.
    if (tabId === 'cashflow' && !XEM_DUOC_GIA_VON) {
        showToast(t('seller.cashflow.owner_only'));
        return;
    }
    if (tabId === 'subscription' && MY_ROLE !== 'SELLER') {
        showToast(t('subscription.seller.owner_only'));
        return;
    }
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn[data-main-tab]').forEach(el => {
        el.classList.remove('active');
        el.removeAttribute('aria-current');
    });
    const tab = document.getElementById(tabId);
    if (!tab) return;
    tab.classList.add('active');
    const nutTab = buttonEl || document.querySelector(`.tab-btn[data-main-tab="${tabId}"]`);
    if (nutTab) {
        nutTab.classList.add('active');
        nutTab.setAttribute('aria-current', 'page');
        const nhan = nutTab.querySelector('[data-i18n]');
        const tieuDe = document.getElementById('sellerPageTitle');
        if (nhan && tieuDe) {
            tieuDe.dataset.i18n = nhan.dataset.i18n;
            tieuDe.textContent = t(nhan.dataset.i18n);
        }
    }
    setSellerNavigation(false);
    if (tabId === 'action-center') loadActionCenter();
    if (tabId === 'reconciliation') loadDoiSoat();
    if (tabId === 'assistant') moTroLy();
    if (tabId === 'nhatky') loadNhatKy();
    // Nạp lô mỗi lần MỞ tab chứ không nạp một lần lúc khởi động: hàng nhập vào
    // giữa buổi phải đếm được, và số lượng của lô là thứ thay đổi liên tục.
    if (tabId === 'kiemke') kkNapLo();
    if (tabId === 'loyalty') loadLoyaltyProgram();
    if (tabId === 'purchasing') window.FSellingPurchasing?.load?.();
    if (tabId === 'cashflow') window.FSellingExpenses?.load?.();
    if (tabId === 'subscription') {
        window.FSellingSubscriptions?.loadSeller?.(currentShopId, currentShopGeneration);
    }
    window.FSellingSubscriptions?.onSellerTabChange?.(tabId);
}

function actionCenterMetric(value) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

function actionCenterDescription(item) {
    return t(`seller.action_center.kind.${item.kind}.description`, {
        count: dinhDangSoSeller(actionCenterMetric(item.count)),
        detailCount: dinhDangSoSeller(actionCenterMetric(item.detail_count)),
        quantity: dinhDangSoSeller(actionCenterMetric(item.quantity)),
        incoming: dinhDangSoSeller(actionCenterMetric(item.incoming_quantity)),
        amount: dinhDangTienDoiSoat(actionCenterMetric(item.amount_vnd))
    });
}

function renderDailyClose(dailyClose) {
    const panel = document.getElementById('dailyClosePanel');
    const list = document.getElementById('dailyCloseList');
    if (!panel || !list || !dailyClose) return;
    const summary = dailyClose.summary || {};
    document.getElementById('dailyCloseRevenue').textContent = dinhDangTienDoiSoat(
        actionCenterMetric(summary.revenue_vnd)
    );
    document.getElementById('dailyCloseOrders').textContent = dinhDangSoSeller(
        actionCenterMetric(summary.order_count)
    );
    document.getElementById('dailyCloseDate').textContent = dailyClose.business_date || '';
    const status = document.getElementById('dailyCloseStatus');
    status.className = `daily-close-status ${dailyClose.ready ? 'clear' : 'attention'}`;
    status.textContent = t(dailyClose.ready
        ? 'seller.daily_close.ready'
        : 'seller.daily_close.not_ready');

    const checks = (Array.isArray(dailyClose.checks) ? dailyClose.checks : [])
        .filter(item => item && Object.prototype.hasOwnProperty.call(DAILY_CLOSE_TARGETS, item.kind));
    list.innerHTML = '';
    for (const item of checks) {
        const target = DAILY_CLOSE_TARGETS[item.kind];
        const state = ['BLOCKING', 'ATTENTION', 'CLEAR'].includes(item.status)
            ? item.status
            : 'ATTENTION';
        const stateKey = state.toLowerCase();
        const description = t(`seller.daily_close.kind.${item.kind}.description`, {
            count: dinhDangSoSeller(actionCenterMetric(item.count)),
            amount: dinhDangTienDoiSoat(actionCenterMetric(item.amount_vnd))
        });
        list.insertAdjacentHTML('beforeend', `<div class="daily-close-row">
            <div class="daily-close-row-main">
                <strong><i class="ph ${target.icon}"></i> ${escapeHtml(t(`seller.daily_close.kind.${item.kind}.title`))}</strong>
                <small>${escapeHtml(description)}</small>
            </div>
            <span class="daily-close-status ${stateKey}">${escapeHtml(t(`seller.daily_close.${stateKey}`))}</span>
            ${state === 'CLEAR' ? '' : `<button class="btn-outline" type="button" data-daily-close-kind="${item.kind}">${escapeHtml(t('seller.daily_close.view'))}</button>`}
        </div>`);
    }
    list.querySelectorAll('[data-daily-close-kind]').forEach(button => {
        button.addEventListener('click', () => openDailyCloseItem(button.dataset.dailyCloseKind));
    });
    panel.style.display = 'block';
}

function renderActionCenter(data) {
    const list = document.getElementById('actionCenterList');
    const empty = document.getElementById('actionCenterEmpty');
    const error = document.getElementById('actionCenterError');
    const loading = document.getElementById('actionCenterLoading');
    if (!list || !empty || !error || !loading) return;

    const items = (Array.isArray(data?.items) ? data.items : []).filter(item => (
        item && Object.prototype.hasOwnProperty.call(ACTION_CENTER_TARGETS, item.kind)
    ));
    const summary = data?.summary || {};
    const values = {
        critical: actionCenterMetric(summary.critical),
        attention: actionCenterMetric(summary.attention),
        plan: actionCenterMetric(summary.plan),
        total: actionCenterMetric(summary.total)
    };
    renderDailyClose(data?.daily_close);
    document.getElementById('actionCenterCritical').textContent = dinhDangSoSeller(values.critical);
    document.getElementById('actionCenterAttention').textContent = dinhDangSoSeller(values.attention);
    document.getElementById('actionCenterPlan').textContent = dinhDangSoSeller(values.plan);
    const badge = document.getElementById('actionCenterBadge');
    if (badge) {
        badge.textContent = dinhDangSoSeller(values.total);
        badge.style.display = values.total > 0 ? 'inline-flex' : 'none';
        badge.title = t('seller.action_center.badge_aria');
    }

    loading.style.display = 'none';
    error.style.display = 'none';
    empty.style.display = items.length ? 'none' : 'block';
    list.innerHTML = '';
    for (const item of items) {
        const target = ACTION_CENTER_TARGETS[item.kind];
        const severity = ['CRITICAL', 'ATTENTION', 'PLAN'].includes(item.severity)
            ? item.severity
            : 'PLAN';
        const severityKey = severity.toLowerCase();
        const title = t(`seller.action_center.kind.${item.kind}.title`);
        list.insertAdjacentHTML('beforeend', `<article class="action-center-card ${severityKey}">
            <div class="action-center-card-head">
                <h4><i class="ph ${target.icon}"></i> ${escapeHtml(title)}</h4>
                <span class="action-center-severity">${escapeHtml(t(`seller.action_center.${severityKey}`))}</span>
            </div>
            <p>${escapeHtml(actionCenterDescription(item))}</p>
            <button class="btn-outline" type="button" data-action-center-kind="${item.kind}">${escapeHtml(t('seller.action_center.view'))}</button>
        </article>`);
    }
    list.querySelectorAll('[data-action-center-kind]').forEach(button => {
        button.addEventListener('click', () => openActionCenterItem(button.dataset.actionCenterKind));
    });
}

async function loadActionCenter() {
    if (MY_ROLE !== 'SELLER') return;
    const shopId = Number(currentShopId);
    if (!Number.isInteger(shopId) || shopId <= 0) return;
    const generation = currentShopGeneration;
    const requestId = ++actionCenterRequestId;
    const loading = document.getElementById('actionCenterLoading');
    const error = document.getElementById('actionCenterError');
    const empty = document.getElementById('actionCenterEmpty');
    if (loading) loading.style.display = 'block';
    if (error) error.style.display = 'none';
    if (empty) empty.style.display = 'none';
    try {
        const data = await apiCall(`/action-center/${shopId}`);
        if (
            Number(currentShopId) !== shopId
            || generation !== currentShopGeneration
            || requestId !== actionCenterRequestId
        ) return;
        renderActionCenter(data);
    } catch (exception) {
        if (
            Number(currentShopId) !== shopId
            || generation !== currentShopGeneration
            || requestId !== actionCenterRequestId
        ) return;
        if (loading) loading.style.display = 'none';
        if (error) error.style.display = 'block';
        const message = document.getElementById('actionCenterErrorText');
        if (message) message.textContent = exception?.message || t('seller.action_center.error');
    }
}

function openActionCenterItem(kind) {
    const target = ACTION_CENTER_TARGETS[kind];
    if (!target) return;
    switchTab(target.tab, document.querySelector(`.tab-btn[data-main-tab="${target.tab}"]`));
    if (target.tab === 'warehouse' && target.subTab) switchWarehouseSubTab(target.subTab);
    if (target.tab === 'purchasing' && target.subTab) switchPurchasingSubTab(target.subTab);
    if (target.tab === 'customers') loadCustomers();
}

function openDailyCloseItem(kind) {
    const target = DAILY_CLOSE_TARGETS[kind];
    if (!target) return;
    switchTab(target.tab, document.querySelector(`.tab-btn[data-main-tab="${target.tab}"]`));
    if (target.tab === 'cashflow') window.FSellingExpenses?.load?.();
}

// Live Preview Logic
document.getElementById('shopName').addEventListener('input', e => {
    document.getElementById('previewCardName').innerText =
        e.target.value || t('seller.shops.preview_name');
});
document.getElementById('shopTaxCode').addEventListener('input', e => {
    document.getElementById('previewCardTaxCode').innerText =
        e.target.value || t('seller.shops.preview_empty');
});
document.getElementById('shopAddress').addEventListener('input', e => {
    document.getElementById('previewCardAddress').innerText =
        e.target.value || t('seller.shops.preview_empty');
});

function openBankSetupFromQuery() {
    // ?setup=bank is a one-shot handoff from POS when VietQR is not configured.
    const url = new URL(window.location.href);
    if (url.searchParams.get('setup') !== 'bank' || !currentShopId) return;

    switchTab('settings');
    openEditShopForm(currentShopId);
    document.getElementById('bankCode')?.focus();
    url.searchParams.delete('setup');
    history.replaceState(history.state, '', `${url.pathname}${url.search}${url.hash}`);
}

async function initializeOnboardingR2() {
    const firstRunOwnsScreen = await window.FSellingOnboardingR2.initialize({
        role: MY_ROLE,
        username: localStorage.getItem('username') || '',
        shops: allShops,
        shopId: currentShopId,
        onShopCreated(shop) {
            allShops = [shop];
            currentShopId = shop.id;
            dashboardShopId = shop.id;
            localStorage.setItem('currentShopId', shop.id);
            renderShopsList();
            renderShopSelectors();
            loadDataForCurrentShop();
        },
        onOpenPos(shopId) {
            goToPOS(shopId, true);
        },
        onOpenAssistant() {
            switchTab('assistant');
        },
        async onEditProduct(productId) {
            switchTab('warehouse', document.querySelector('.tab-btn[data-main-tab="warehouse"]'));
            switchWarehouseSubTab('products');
            await loadProducts();
            editProduct(productId);
            document.getElementById('prodName')?.focus();
        }
    });
    if (!firstRunOwnsScreen) openBankSetupFromQuery();
    return firstRunOwnsScreen;
}

async function init() {
    try {
        allShops = await apiCall('/shops');
        renderShopsList(); // Cho phần cài đặt
        if (MY_ROLE === 'SELLER' || MY_STAFF_ROLE === 'MANAGER') {
            renderReturnManagerPinShopOptions();
        }
        // Danh sách nhân viên chỉ dành cho chủ shop (nhân viên gọi sẽ bị 404).
        if (MY_ROLE === 'SELLER') renderStaffShopOptions();
        if (MY_ROLE !== 'ADMIN' && coQuyenNhanVien('CUSTOMER')) renderCustomerShopOptions();

        if(allShops.length === 0) {
            currentShopId = null;
            dashboardShopId = null;
            localStorage.removeItem('currentShopId');
            document.getElementById('dashboardContent').style.display = 'none';
            document.getElementById('noShopMsg').style.display = 'block';
            if (MY_ROLE === 'SELLER') openCreateShopForm();
        } else {
            // Mặc định nạp dữ liệu cho shop đầu tiên nếu có currentShopId
            let savedId = localStorage.getItem('currentShopId');
            if(savedId && allShops.find(s => s.id == savedId)) {
                currentShopId = parseInt(savedId);
            } else {
                currentShopId = allShops[0].id;
                localStorage.setItem('currentShopId', currentShopId);
            }
            dashboardShopId = currentShopId; // Initialize dashboardShopId
            renderShopSelectors(); // Call AFTER currentShopId and dashboardShopId are set
            loadDataForCurrentShop();
        }
        if (await initializeOnboardingR2()) return;
    } catch(e) {
        showToast(e instanceof TypeError ? t('common.network_error') : e.message);
    }

    renderBankOptions();
}

function renderShopSelectors() {
    const dashList = document.getElementById('dashShopList');
    const whList = document.getElementById('whShopList');
    const vouList = document.getElementById('vouShopList');
    const posList = document.getElementById('posShopList');
    const doiSoatSelect = document.getElementById('doiSoatShopSelect');
    
    dashList.innerHTML = '';
    if(whList) whList.innerHTML = '';
    if(vouList) vouList.innerHTML = '';
    if(posList) posList.innerHTML = '';

    if (doiSoatSelect) {
        if (!doiSoatShopId || !allShops.some(s => s.id === doiSoatShopId)) {
            doiSoatShopId = currentShopId || allShops[0]?.id || null;
        }
        doiSoatSelect.innerHTML = allShops.map(s =>
            `<option value="${s.id}">${escapeHtml(s.name)}</option>`
        ).join('');
        if (doiSoatShopId) doiSoatSelect.value = String(doiSoatShopId);
    }

    const nhatKySelect = document.getElementById('nhatKyShopSelect');
    if (nhatKySelect) {
        if (!nhatKyShopId || !allShops.some(s => s.id === nhatKyShopId)) {
            nhatKyShopId = currentShopId || allShops[0]?.id || null;
        }
        nhatKySelect.innerHTML = allShops.map(s =>
            `<option value="${s.id}">${escapeHtml(s.name)}</option>`
        ).join('');
        if (nhatKyShopId) nhatKySelect.value = String(nhatKyShopId);
    }

    renderOChonCuaHangChung();

    allShops.forEach(s => {
        // Dashboard (Thống Kê)
        const btn1 = document.createElement('button');
        btn1.className = dashboardShopId === s.id ? 'btn-primary' : 'btn-outline';
        btn1.innerText = s.name;
        btn1.onclick = () => loadDashboardShop(s.id);
        dashList.appendChild(btn1);

        // Warehouse
        if(whList) {
            const btn2 = document.createElement('button');
            btn2.className = currentShopId === s.id ? 'btn-primary' : 'btn-outline';
            btn2.innerText = s.name;
            btn2.onclick = () => changeShop(s.id);
            whList.appendChild(btn2);
        }

        // Trợ lý
            const troLyList = document.getElementById('assistantShopList');
        if (troLyList) {
            const btnTroLy = document.createElement('button');
            btnTroLy.className = currentShopId === s.id ? 'btn-primary' : 'btn-outline';
            btnTroLy.innerText = s.name;
            btnTroLy.onclick = () => { changeShop(s.id); moTroLy(); };
            troLyList.appendChild(btnTroLy);
        }

        // Kiểm kê
        const kkList = document.getElementById('kkShopList');
        if(kkList) {
            const btnKK = document.createElement('button');
            btnKK.className = currentShopId === s.id ? 'btn-primary' : 'btn-outline';
            btnKK.innerText = s.name;
            btnKK.onclick = () => changeShop(s.id);
            kkList.appendChild(btnKK);
        }

        // Voucher
        if(vouList) {
            const btn3 = document.createElement('button');
            btn3.className = currentShopId === s.id ? 'btn-primary' : 'btn-outline';
            btn3.innerText = s.name;
            btn3.onclick = () => changeShop(s.id);
            vouList.appendChild(btn3);
        }
        
        // POS
        if(posList) {
            posList.innerHTML += `<button style="width: 100%; padding: 1rem; text-align: left; display: flex; align-items: center; gap: 0.5rem;" onclick="goToPOS(${s.id})"><i class="ph ph-storefront"></i> ${escapeHtml(s.name)}</button>`;
        }
    });
}

// ---------- Một cửa hàng cho cả trang ----------
// Gốc của vấn đề không phải 9 ô chọn, mà là BỐN biến trạng thái riêng biệt:
// `currentShopId`, `dashboardShopId`, `doiSoatShopId`, `nhatKyShopId`. Chín ô
// chọn chỉ là chỗ chúng lộ ra. Hàm này đặt cả bốn về cùng một giá trị.
//
// CỐ Ý không gộp `posShopList`: ô đó không lọc dữ liệu mà là nút "mở POS cho
// cửa hàng nào" — một hành động khác hẳn, gộp vào là mất chức năng.
const CAC_O_CHON_CU = [
    'shopChungSelect', 'doiSoatShopSelect', 'nhatKyShopSelect',
    'custShopSelect', 'staffShopSelect'
];

function doiCuaHangChung(giaTri) {
    const id = Number.parseInt(giaTri, 10);
    if (!Number.isInteger(id) || !allShops.some(s => s.id === id)) return;

    dashboardShopId = id;
    doiSoatShopId = id;
    nhatKyShopId = id;
    trangDoiSoat = 1;
    trangNhatKy = 1;
    doiSoatBadgeRequestId += 1;

    // Ghi vào cả các ô cũ đang ẩn: nhiều hàm vẫn đọc `.value` của chúng để biết
    // đang xem cửa hàng nào. Bỏ bước này là các tab đó đọc ra giá trị cũ.
    CAC_O_CHON_CU.forEach(idOo => {
        const o = document.getElementById(idOo);
        if (o && [...o.options].some(x => x.value === String(id))) o.value = String(id);
    });

    // `changeShop` lo phần dùng chung: sản phẩm, danh mục, khuyến mãi, tồn kho.
    // Phải gọi kể cả khi đang ở tab khác, nếu không lát nữa mở Kho Hàng ra sẽ
    // thấy hàng của cửa hàng trước đó.
    changeShop(id);

    // Rồi nạp lại ĐÚNG tab đang mở. Nạp cả sáu tab là sáu lượt gọi mạng cho
    // năm màn hình không ai đang nhìn.
    const tab = document.querySelector('.tab-content.active')?.id;
    if (tab === 'dashboard') loadDashboardShop(id);
    else if (tab === 'action-center') loadActionCenter();
    else if (tab === 'reconciliation') loadDoiSoat();
    else if (tab === 'nhatky') loadNhatKy();
    else if (tab === 'customers') loadCustomers();
    else if (tab === 'loyalty') loadLoyaltyProgram();
    else if (tab === 'purchasing') window.FSellingPurchasing?.load?.();
    else if (tab === 'cashflow') window.FSellingExpenses?.load?.();
    else if (tab === 'subscription') {
        window.FSellingSubscriptions?.loadSeller?.(id, currentShopGeneration);
    }
    else if (tab === 'settings') loadStaff();
}

function renderOChonCuaHangChung() {
    const o = document.getElementById('shopChungSelect');
    const khung = document.getElementById('oChonCuaHangChung');
    if (!o || !khung) return;
    // Một cửa hàng thì không có gì để chọn — ẩn luôn cho đỡ rối.
    if (allShops.length <= 1) {
        khung.style.display = 'none';
    } else {
        khung.style.display = 'inline-flex';
    }
    o.innerHTML = allShops.map(s =>
        `<option value="${s.id}">${escapeHtml(s.name)}</option>`
    ).join('');
    if (currentShopId) o.value = String(currentShopId);
}

function openPosShopSelector() {
    if(allShops.length === 0) return showToast(t('seller.shops.create_first'));
    document.getElementById('posModal').style.display = 'flex';
}

function renderDashboardOrders(id, res) {
    const shop = allShops.find(s => s.id === id);
    document.getElementById('currentShopName').innerText = shop ? shop.name : '';

    const tbody = document.getElementById('orderList');
    tbody.innerHTML = '';
    (res.orders || []).forEach(o => {
        const hienThi = window.moTaTrangThaiDon(o.status);
        const dt = dinhDangNgayGio(o.date);
        tbody.innerHTML += `<tr>
            <td><strong>#${o.id}</strong></td>
            <td>${dt}</td>
            <td>${escapeHtml(o.cashier_username || '—')}${o.shift_id ? `<br><small style="color:var(--text-muted);">${escapeHtml(t('seller.dashboard.shift_number', { id: o.shift_id }))}</small>` : ''}</td>
            <td>${dinhDangTienDoiSoat(o.total)}</td>
            <td style="color: ${hienThi.color}; font-weight: 600;">${escapeHtml(hienThi.label)}</td>
            <td><button class="btn-outline" style="padding: 0.25rem 0.6rem; font-size: 0.8rem;" onclick="xemChiTietDon(${o.id})">${escapeHtml(t('seller.actions.view'))}</button></td>
        </tr>`;
    });
    capNhatDieuKhienTrang(res);

    const dashList = document.getElementById('dashShopList');
    if (dashList) {
        Array.from(dashList.children).forEach(btn => {
            btn.className = shop && btn.innerText === shop.name
                ? 'btn-primary'
                : 'btn-outline';
        });
    }
    document.getElementById('dashboardContent').style.display = 'block';
}

/** Thẻ lãi gộp trên dashboard.
 *
 *  Server BỎ HẲN nhóm field này khi người gọi không được xem, nên điều kiện là
 *  "field có mặt hay không" chứ không phải giá trị của nó - lãi bằng 0 là một
 *  con số hợp lệ (bán đúng bằng giá vốn) và vẫn phải hiện ra.
 *
 *  Phần đơn chưa đủ giá vốn LUÔN được nói ra khi có. Im lặng ở đây nghĩa là
 *  chủ shop nhìn một con số lãi thấp hơn thực tế mà tưởng đó là toàn bộ. */
function veTheLaiGop(stats) {
    const card = document.getElementById('statProfitCard');
    if (!card) return;
    if (stats.gross_profit === undefined) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';
    document.getElementById('statProfit').innerText =
        dinhDangTienDoiSoat(stats.gross_profit);

    const ghiChu = document.getElementById('statProfitNote');
    if (!ghiChu) return;
    const phan = [];
    if (stats.gross_margin !== null && stats.gross_margin !== undefined) {
        phan.push(t('seller.dashboard.gross_margin', {
            percent: dinhDangSoSeller(stats.gross_margin, {
                maximumFractionDigits: 1
            })
        }));
    }
    if (stats.orders_missing_cost) {
        phan.push(t('seller.dashboard.orders_missing_cost', {
            count: stats.orders_missing_cost,
            formattedCount: dinhDangSoSeller(stats.orders_missing_cost),
            amount: dinhDangTienDoiSoat(stats.revenue_missing_cost || 0)
        }));
    }
    // Lỗ do hủy hàng ĐÃ được trừ khỏi `gross_profit` ở server. Nói ra để chủ
    // shop biết lãi tụt vì hàng hỏng chứ không phải vì bán kém - hai chuyện đó
    // dẫn tới hai quyết định khác hẳn nhau.
    if (stats.write_off_loss) {
        phan.push(t('seller.dashboard.write_off_loss', {
            amount: dinhDangTienDoiSoat(stats.write_off_loss),
            quantity: dinhDangSoSeller(stats.written_off_quantity || 0)
        }));
    }
    // Phiếu chưa khai giá vốn KHÔNG được cộng vào số lỗ (không đoán NULL là 0),
    // nên phải nói ra, nếu không số lỗ hiện ra thấp hơn thực tế trong im lặng.
    if (stats.write_offs_missing_cost) {
        phan.push(t('seller.dashboard.write_offs_missing_cost', {
            count: stats.write_offs_missing_cost,
            formattedCount: dinhDangSoSeller(stats.write_offs_missing_cost)
        }));
    }
    ghiChu.innerText = phan.join(' · ');
    ghiChu.style.color =
        (stats.orders_missing_cost || stats.write_offs_missing_cost)
            ? '#B45309'
            : '#64748B';
}

function renderDashboardStats(stats) {
    document.getElementById('statRev').innerText = dinhDangTienDoiSoat(stats.total_revenue);
    document.getElementById('statOrders').innerText = dinhDangSoSeller(stats.total_orders);
    document.getElementById('statSold').innerText = dinhDangSoSeller(stats.total_sold);
    veTheLaiGop(stats);

    const pieCtx = document.getElementById('productPieChart').getContext('2d');
    if (pieChartInstance) pieChartInstance.destroy();

    // Nhóm biến thể ghi kèm số loại đã bán, để "Áo thun 12" không bị đọc nhầm
    // thành một mặt hàng đơn lẻ bán chạy trong khi đó là tổng của mấy size.
    const pieLabels = (stats.top_products || []).map(p => (
        Number(p.variants) > 1
            ? t('seller.dashboard.top_group', {
                name: p.name,
                count: Number(p.variants),
                formattedCount: dinhDangSoSeller(p.variants)
            })
            : p.name
    ));
    const pieData = (stats.top_products || []).map(p => p.qty);
    const pieColors = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6'];

    pieChartInstance = new Chart(pieCtx, {
        type: 'doughnut',
        data: {
            labels: pieLabels.length ? pieLabels : [t('seller.dashboard.no_chart_data')],
            datasets: [{
                data: pieData.length ? pieData : [1],
                backgroundColor: pieData.length ? pieColors : ['#E2E8F0'],
                borderWidth: 0
            }]
        },
        options: {
            locale: window.FSellingI18n?.getIntlLocale?.() || 'vi-VN',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { boxWidth: 12 } }
            }
        }
    });

    const ctx = document.getElementById('revenueChart').getContext('2d');
    if (chartInstance) chartInstance.destroy();
    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: (stats.trend_labels || []).map(dinhDangNhanNgayBieuDo),
            datasets: [{
                label: t('seller.dashboard.revenue_vnd'),
                data: stats.trend_data || [],
                borderColor: '#3B82F6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            locale: window.FSellingI18n?.getIntlLocale?.() || 'vi-VN',
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
}

function khoaDashboardOrders(shopId, query) {
    return `${shopId}|${query}`;
}

function khoaDashboardStats(shopId, query) {
    return `${shopId}|${query}`;
}

async function loadDashboardShop(id) {
    const shopId = Number(id);
    if (!Number.isInteger(shopId) || !allShops.some(shop => shop.id === shopId)) return;
    dashboardShopId = shopId;
    const ordersQuery = chuoiThamSoDon();
    const statsQuery = chuoiThamSoNgay();
    const ordersKey = khoaDashboardOrders(shopId, ordersQuery);
    const statsKey = khoaDashboardStats(shopId, statsQuery);
    const requestId = ++dashboardRequestId;
    // Hủy ngay cả request lịch sử ca cũ trước khi request dashboard mới hoàn tất.
    shiftHistoryRequestId += 1;
    try {
        const res = await apiCall(`/dashboard/seller/${shopId}${ordersQuery}`);
        if (
            requestId !== dashboardRequestId
            || shopId !== dashboardShopId
            || ordersKey !== khoaDashboardOrders(shopId, chuoiThamSoDon())
        ) return;
        if (!doiSoatShopId) doiSoatShopId = shopId;
        if (doiSoatShopId === shopId) {
            doiSoatBadgeRequestId += 1;
            capNhatBadgeDoiSoat(res.reconciliation_count || 0);
        }
        dashboardOrdersCache = { shopId, key: ordersKey, data: res };
        renderDashboardOrders(shopId, res);
        loadShiftHistory(shopId);

        const stats = await apiCall(`/shops/${shopId}/stats${statsQuery}`);
        if (
            requestId !== dashboardRequestId
            || shopId !== dashboardShopId
            || statsKey !== khoaDashboardStats(shopId, chuoiThamSoNgay())
        ) return;
        dashboardStatsCache = { shopId, key: statsKey, data: stats };
        renderDashboardStats(stats);
    } catch(e) {
        if (requestId === dashboardRequestId && shopId === dashboardShopId) {
            showToast(e.message);
        }
    }
}

function renderShiftHistory(data) {
    const tbody = document.getElementById('shiftHistoryList');
    if (!tbody) return;
    if (!data.items.length) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:var(--text-muted);">${escapeHtml(t('seller.shifts.empty'))}</td></tr>`;
        return;
    }
    tbody.innerHTML = data.items.map(shift => {
        const dangMo = shift.status === 'OPEN';
        const variance = Number(shift.variance_amount);
        const varianceText = dangMo || !Number.isFinite(variance)
            ? '—'
            : `${variance > 0 ? '+' : ''}${dinhDangTienDoiSoat(variance)}`;
        const varianceColor = variance < 0
            ? '#EF4444'
            : (variance > 0 ? '#F59E0B' : 'var(--success)');
        return `<tr>
            <td><strong>#${shift.id}</strong><br><small style="color:${dangMo ? '#10B981' : 'var(--text-muted)'};">${escapeHtml(t(dangMo ? 'seller.status.open' : 'seller.status.closed'))}</small></td>
            <td>${escapeHtml(shift.opened_by_username || '—')}</td>
            <td>${dinhDangNgayGio(shift.opened_at)}</td>
            <td>${dangMo ? '—' : dinhDangNgayGio(shift.closed_at)}</td>
            <td>${dinhDangTienDoiSoat(shift.expected_cash_amount || 0)}</td>
            <td>${dangMo || shift.counted_cash_amount == null ? '—' : dinhDangTienDoiSoat(shift.counted_cash_amount)}</td>
            <td style="font-weight:700; color:${varianceColor};">${varianceText}</td>
        </tr>`;
    }).join('');
}

async function loadShiftHistory(shopId) {
    const tbody = document.getElementById('shiftHistoryList');
    if (!tbody || !shopId) return;
    const requestId = ++shiftHistoryRequestId;
    tbody.innerHTML = `<tr><td colspan="7" style="color:var(--text-muted);">${escapeHtml(t('common.loading'))}</td></tr>`;
    try {
        const data = await apiCall(`/shifts/history/${shopId}?page=1&per_page=10`);
        // Dashboard và kho có thể đang chọn hai shop khác nhau. Chỉ bỏ response
        // cũ khi người dùng đã chuyển shop thống kê trong lúc request đang chạy.
        if (shopId !== dashboardShopId) return;
        if (requestId !== shiftHistoryRequestId) return;
        shiftHistoryCache = { shopId, data };
        renderShiftHistory(data);
    } catch (e) {
        if (requestId === shiftHistoryRequestId && shopId === dashboardShopId) {
            tbody.innerHTML = `<tr><td colspan="7" style="color:#EF4444;">${escapeHtml(e.message || t('seller.shifts.load_error'))}</td></tr>`;
        }
    }
}

// ===== D4: danh sách đơn cần đối soát =====

function dinhDangTienDoiSoat(value) {
    return window.FSellingI18n?.formatMoney(value)
        ?? `${Math.round(Number(value) || 0).toLocaleString('vi-VN')} ₫`;
}

function thongTinLoaiDoiSoat(reason) {
    const loai = {
        UNDERPAID: {
            title: t('seller.reconciliation.underpaid_title'),
            cardClass: 'underpaid',
            stateClass: 'waiting',
            stateLabel: t('seller.reconciliation.underpaid_state'),
            note: t('seller.reconciliation.underpaid_note')
        },
        OVERPAID: {
            title: t('seller.reconciliation.overpaid_title'),
            cardClass: 'overpaid',
            stateClass: 'invoice',
            stateLabel: t('seller.reconciliation.overpaid_state'),
            note: t('seller.reconciliation.overpaid_note')
        },
        LATE_PAYMENT: {
            title: t('seller.reconciliation.late_title'),
            cardClass: 'late-payment',
            stateClass: 'cancelled',
            stateLabel: t('seller.reconciliation.late_state'),
            note: t('seller.reconciliation.late_note')
        },
        LEGACY_REVIEW: {
            title: t('seller.reconciliation.legacy_title'),
            cardClass: 'legacy-review',
            stateClass: 'review',
            stateLabel: t('seller.reconciliation.review_state'),
            note: t('seller.reconciliation.legacy_note')
        },
        // F6: tiền về cho đơn ghi nợ. KHÔNG phải một lý do do server đặt vào
        // `reconciliation_reason` - đơn nợ giữ nguyên reason NULL. Khóa này do
        // `taoTheDoiSoat` tự suy ra từ `unapplied_transfer_amount`, vì nếu để
        // rơi vào nhánh mặc định thì thẻ hiện "đơn đối soát cũ không đủ dữ
        // liệu" - sai hoàn toàn và làm người bán không biết phải làm gì.
        DEBT_TRANSFER_NOTICE: {
            title: t('seller.reconciliation.debt_transfer_title'),
            cardClass: 'underpaid',
            stateClass: 'waiting',
            stateLabel: t('seller.reconciliation.debt_transfer_state'),
            note: t('seller.reconciliation.debt_transfer_note')
        }
    };
    return loai[reason] || {
        title: reason
            ? t('seller.reconciliation.unknown_title_reason', { reason })
            : t('seller.reconciliation.unknown_title'),
        cardClass: 'legacy-review',
        stateClass: 'review',
        stateLabel: t('seller.reconciliation.review_state'),
        note: t('seller.reconciliation.unknown_note')
    };
}

function capNhatBadgeDoiSoat(count) {
    const badge = document.getElementById('doiSoatBadge');
    if (!badge) return;
    const soLuong = Math.max(0, Number.parseInt(count, 10) || 0);
    doiSoatBadgeCount = soLuong;
    badge.innerText = soLuong > 99 ? '99+' : String(soLuong);
    badge.style.display = soLuong > 0 ? 'inline-flex' : 'none';
    badge.title = t('seller.reconciliation.badge_title', {
        count: soLuong,
        formattedCount: dinhDangSoSeller(soLuong)
    });
}

function chonShopDoiSoat(value) {
    const id = Number.parseInt(value, 10);
    if (!Number.isInteger(id) || !allShops.some(s => s.id === id)) return;
    doiSoatShopId = id;
    doiSoatBadgeRequestId += 1;
    trangDoiSoat = 1;
    loadDoiSoat();
}

function renderDoiSoatKhongCoShop() {
    const list = document.getElementById('doiSoatList');
    const loading = document.getElementById('doiSoatLoading');
    const empty = document.getElementById('doiSoatEmpty');
    if (!list || !loading || !empty) return;
    capNhatBadgeDoiSoat(0);
    document.getElementById('doiSoatCount').innerText =
        t('seller.reconciliation.waiting_cases', {
            count: 0,
            formattedCount: dinhDangSoSeller(0)
        });
    list.innerHTML = '';
    loading.style.display = 'none';
    empty.style.display = 'block';
    empty.innerHTML = `<i class="ph ph-storefront" style="display:block; margin-bottom:0.5rem; font-size:2.2rem;"></i>${escapeHtml(t('seller.reconciliation.no_shop'))}`;
}

function renderDoiSoatResponse(res, capNhatBadge = true) {
    const tongDangChoRaw = Number(res.reconciliation_count ?? res.total_orders ?? 0);
    const tongDangCho = Number.isFinite(tongDangChoRaw)
        ? Math.max(0, tongDangChoRaw)
        : 0;
    if (capNhatBadge) capNhatBadgeDoiSoat(tongDangCho);
    document.getElementById('doiSoatCount').innerText =
        t('seller.reconciliation.waiting_cases', {
            count: tongDangCho,
            formattedCount: dinhDangSoSeller(tongDangCho)
        });
    renderDoiSoat(res.orders || []);
    capNhatPhanTrangDoiSoat(res);
}

async function loadDoiSoat() {
    const shopId = doiSoatShopId || currentShopId || dashboardShopId;
    const list = document.getElementById('doiSoatList');
    const loading = document.getElementById('doiSoatLoading');
    const empty = document.getElementById('doiSoatEmpty');
    const reloadButton = document.getElementById('btnTaiLaiDoiSoat');
    if (!list || !loading || !empty) return;

    if (!shopId) {
        renderDoiSoatKhongCoShop();
        return;
    }

    doiSoatShopId = shopId;
    const select = document.getElementById('doiSoatShopSelect');
    if (select) select.value = String(shopId);

    // Khối phụ: tải song song, KHÔNG await. Đơn offline hỏng thì danh sách đối
    // soát ngân hàng bên dưới vẫn phải hiện bình thường.
    loadDonOffline(shopId);

    const requestId = ++doiSoatRequestId;
    const badgeRequestId = ++doiSoatBadgeRequestId;
    loading.style.display = 'block';
    empty.style.display = 'none';
    if (reloadButton) reloadButton.disabled = true;

    try {
        const params = new URLSearchParams({
            page: String(trangDoiSoat),
            per_page: String(DOI_SOAT_MOI_TRANG),
            reconciliation_only: 'true'
        });
        const res = await apiCall(`/dashboard/seller/${shopId}?${params.toString()}`);
        if (requestId !== doiSoatRequestId || shopId !== doiSoatShopId) return;

        if ((!res.orders || res.orders.length === 0) && trangDoiSoat > 1 && res.total_orders > 0) {
            trangDoiSoat -= 1;
            return loadDoiSoat();
        }

        reconciliationCache = { shopId, data: res };
        renderDoiSoatResponse(res, badgeRequestId === doiSoatBadgeRequestId);
    } catch (e) {
        if (requestId !== doiSoatRequestId || shopId !== doiSoatShopId) return;
        list.innerHTML = '';
        empty.style.display = 'block';
        empty.innerHTML = `<i class="ph ph-warning-circle" style="display:block; margin-bottom:0.5rem; color:#DC2626; font-size:2.2rem;"></i>${escapeHtml(e.message)}`;
        showToast(e.message);
    } finally {
        if (requestId === doiSoatRequestId && shopId === doiSoatShopId) {
            loading.style.display = 'none';
            if (reloadButton) reloadButton.disabled = false;
        }
    }
}

// ---------- Ai làm gì (nhật ký thao tác) ----------
let nhatKyShopId = null;
let trangNhatKy = 1;
let nhatKyRequestId = 0;
const NHAT_KY_MOI_TRANG = 25;

// Nhóm theo MỨC ĐỘ CẦN ĐỂ Ý, không theo loại nghiệp vụ. Chủ shop mở màn này ra
// là để tìm chuyện bất thường, nên tiền-ra và xóa-dữ-liệu phải bật lên trước mắt.
//
// Hành động không nằm trong nhóm nào vẫn hiện, chỉ là màu trung tính — danh
// sách này để tô màu, KHÔNG phải để lọc.
const NHOM_HANH_DONG = {
    // Đỏ: tiền rời khỏi cửa hàng, hoặc dữ liệu bị xóa
    '#DC2626': [
        'CANCEL_ORDER', 'AUTO_CANCEL_ORDER', 'REFUND_COMPLETE', 'ORDER_RETURN',
        'CASH_PAY_OUT', 'WRITE_OFF_STOCK',
        'DELETE_PRODUCT', 'DELETE_VOUCHER', 'DELETE_CUSTOMER', 'DELETE_SHOP',
        'DISABLE_STAFF'
    ],
    // Xanh lá: tiền vào
    '#059669': [
        'PAY_ORDER', 'CASH_TOPUP', 'DEBT_PAYMENT', 'OFFLINE_SALE',
        'WEBHOOK_PAYMENT'
    ],
    // Hổ phách: đụng vào kho hoặc quyền
    '#B45309': [
        'ADJUST_STOCK', 'STOCKTAKE', 'UPDATE_PRODUCT', 'TOGGLE_PRODUCT_STATUS',
        'CREATE_STAFF', 'UPDATE_STAFF_ROLE', 'RESET_STAFF_PASSWORD',
        'UPDATE_VOUCHER', 'CREATE_VOUCHER', 'CHANGE_PASSWORD',
        'SUBSCRIPTION_CHECKOUT_CREATED', 'SUBSCRIPTION_ADMIN_GIFT',
        'SUBSCRIPTION_ADMIN_GIFT_REVOKED', 'SUBSCRIPTION_PAYMENT_REVIEW',
        'SUBSCRIPTION_PAYMENT_OVERPAID', 'SUBSCRIPTION_PAYMENT_UNDERPAID',
        'SUBSCRIPTION_IDEMPOTENCY_COLLISION', 'SUBSCRIPTION_ACTIVATED'
    ]
};

function mauHanhDong(action) {
    for (const mau of Object.keys(NHOM_HANH_DONG)) {
        if (NHOM_HANH_DONG[mau].includes(action)) return mau;
    }
    return '#64748B';
}

function tenHanhDong(action) {
    const khoa = `seller.activity.action.${action}`;
    const ten = t(khoa);
    // Chưa dịch thì hiện mã trần chứ KHÔNG ẩn: server mới hơn giao diện là
    // chuyện thường, và giấu một dòng nhật ký còn tệ hơn hiện nó khó đọc.
    return ten === khoa ? action : ten;
}

function taoDongNhatKy(muc) {
    const mau = mauHanhDong(muc.action);
    const luc = muc.created_at ? dinhDangNgayGio(muc.created_at) : '—';
    return `
        <div style="display:flex; gap:0.75rem; padding:0.7rem 0.2rem; border-bottom:1px solid var(--border-color);">
            <div style="width:4px; border-radius:2px; background:${mau}; flex-shrink:0;"></div>
            <div style="flex:1; min-width:0;">
                <div style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:baseline;">
                    <span style="font-weight:600; color:${mau};">${escapeHtml(tenHanhDong(muc.action))}</span>
                    <span style="color:var(--text-muted); font-size:0.82rem;">${escapeHtml(muc.username || '—')}</span>
                    <span style="color:var(--text-muted); font-size:0.82rem; margin-left:auto;">${escapeHtml(luc)}</span>
                </div>
                <div style="color:var(--text-main); font-size:0.88rem; margin-top:0.2rem; word-break:break-word;">
                    ${escapeHtml(muc.details || '')}
                </div>
            </div>
        </div>`;
}

function renderNhatKy(kq) {
    const list = document.getElementById('nhatKyList');
    const empty = document.getElementById('nhatKyEmpty');
    const phanTrang = document.getElementById('nhatKyPagination');
    if (!list) return;

    const dong = kq.logs || [];
    list.innerHTML = dong.map(taoDongNhatKy).join('');
    if (empty) empty.style.display = dong.length ? 'none' : 'block';

    if (phanTrang) {
        const nhieuTrang = (kq.total_pages || 0) > 1;
        phanTrang.style.display = nhieuTrang ? 'flex' : 'none';
        const tt = document.getElementById('nhatKyThongTinTrang');
        if (tt) tt.innerText = t('seller.activity.page_info', {
            page: kq.page, total_pages: kq.total_pages, total: kq.total
        });
        const truoc = document.getElementById('nhatKyTrangTruoc');
        const sau = document.getElementById('nhatKyTrangSau');
        if (truoc) truoc.disabled = (kq.page || 1) <= 1;
        if (sau) sau.disabled = (kq.page || 1) >= (kq.total_pages || 1);
    }
}

async function loadNhatKy() {
    const shopId = nhatKyShopId || currentShopId || dashboardShopId;
    const list = document.getElementById('nhatKyList');
    const loading = document.getElementById('nhatKyLoading');
    const empty = document.getElementById('nhatKyEmpty');
    if (!list) return;

    if (!shopId) {
        list.innerHTML = '';
        if (empty) empty.style.display = 'block';
        return;
    }
    nhatKyShopId = shopId;
    const select = document.getElementById('nhatKyShopSelect');
    if (select) select.value = String(shopId);

    const requestId = ++nhatKyRequestId;
    if (loading) loading.style.display = 'block';
    if (empty) empty.style.display = 'none';
    try {
        const params = new URLSearchParams({
            page: String(trangNhatKy),
            per_page: String(NHAT_KY_MOI_TRANG)
        });
        const kq = await apiCall(`/logs/shop/${shopId}?${params.toString()}`);
        // Bỏ qua phản hồi của lần bấm cũ: đổi cửa hàng nhanh hai lần thì lần
        // chậm hơn về sau sẽ ghi đè lên lần mới.
        if (requestId !== nhatKyRequestId) return;
        renderNhatKy(kq);
    } catch (e) {
        if (requestId !== nhatKyRequestId) return;
        list.innerHTML = '';
        if (empty) {
            empty.style.display = 'block';
            empty.innerHTML = `<i class="ph ph-warning-circle" style="display:block; margin-bottom:0.5rem; color:#DC2626; font-size:2.2rem;"></i>${escapeHtml(e.message)}`;
        }
        showToast(e.message);
    } finally {
        if (requestId === nhatKyRequestId && loading) loading.style.display = 'none';
    }
}

function chonShopNhatKy(value) {
    const id = Number.parseInt(value, 10);
    if (!Number.isInteger(id) || !allShops.some(s => s.id === id)) return;
    nhatKyShopId = id;
    trangNhatKy = 1;
    loadNhatKy();
}

function doiTrangNhatKy(buoc) {
    const moi = trangNhatKy + buoc;
    if (moi < 1) return;
    trangNhatKy = moi;
    loadNhatKy();
}

// ---------- Đơn bán khi mất mạng có vướng mắc ----------
// Thứ tự này là thứ tự CẦN LÀM GÌ, không phải bảng chữ cái: tồn âm là thứ duy
// nhất bắt người ta phải đi đếm lại hàng, còn giá đổi thì chỉ cần biết.
const OFFLINE_ISSUE_ORDER = [
    'TON_AM',
    'SP_KHONG_CON',
    'KHONG_CO_CA',
    'CA_DA_CHOT',
    'GIA_DOI'
];

function taoTheDonOffline(don) {
    const ma = Number(don.order_id);
    // Giờ từ server là UTC KHÔNG có ký hiệu múi giờ. Phải qua dinhDangNgayGio(),
    // gọi thẳng new Date() là lệch 7 tiếng và đơn buổi tối bị ghi lùi một ngày.
    const luc = don.sold_at ? dinhDangNgayGio(don.sold_at) : '—';
    const may = (don.device || '').trim();

    const cac_van_de = (don.issues || [])
        .slice()
        .sort((a, b) => OFFLINE_ISSUE_ORDER.indexOf(a) - OFFLINE_ISSUE_ORDER.indexOf(b));

    const dong_van_de = cac_van_de.map(ma_loi => {
        // Cờ lạ (bản server mới hơn bản giao diện) vẫn phải hiện ra chứ không
        // được nuốt: một cảnh báo không đọc được vẫn hơn không có cảnh báo nào.
        const ten = t(`seller.offline.issue.${ma_loi}`);
        const cach = t(`seller.offline.fix.${ma_loi}`);
        const chua_dich = ten === `seller.offline.issue.${ma_loi}`;
        return `
            <li style="margin-bottom:0.5rem;">
                <strong>${escapeHtml(chua_dich ? ma_loi : ten)}</strong>
                ${chua_dich ? '' : `<div style="color:var(--text-muted); font-size:0.84rem; line-height:1.5;">${escapeHtml(cach)}</div>`}
            </li>`;
    }).join('');

    return `
        <div style="border:1px solid var(--border-color); border-radius:10px; padding:0.75rem 0.9rem; margin-bottom:0.6rem; background:var(--card-bg);">
            <div style="display:flex; justify-content:space-between; gap:0.75rem; flex-wrap:wrap; margin-bottom:0.45rem;">
                <strong>${escapeHtml(t('seller.offline.order', { id: ma }))}</strong>
                <span style="font-weight:600;">${escapeHtml(dinhDangTienDoiSoat(don.total))}</span>
            </div>
            <div style="color:var(--text-muted); font-size:0.84rem; margin-bottom:0.55rem;">
                ${escapeHtml(t('seller.offline.sold_at'))}: ${escapeHtml(luc)}
                ${may ? ` &middot; ${escapeHtml(t('seller.offline.device'))}: ${escapeHtml(may)}` : ''}
            </div>
            <ul style="margin:0; padding-left:1.1rem;">${dong_van_de}</ul>
        </div>`;
}

function renderDonOffline(danh_sach) {
    const hop = document.getElementById('offlineIssueBox');
    const list = document.getElementById('offlineIssueList');
    const dem = document.getElementById('offlineIssueCount');
    if (!hop || !list) return;

    const hop_le = (danh_sach || []).filter(d => Number.isInteger(Number(d.order_id)));
    if (!hop_le.length) {
        hop.style.display = 'none';
        list.innerHTML = '';
        return;
    }
    list.innerHTML = hop_le.map(taoTheDonOffline).join('');
    if (dem) dem.innerText = t('seller.offline.count', { count: hop_le.length });
    hop.style.display = 'block';
}

async function loadDonOffline(shopId) {
    if (!shopId) return renderDonOffline([]);
    try {
        renderDonOffline(await apiCall(`/orders/${shopId}/offline-issues`));
    } catch (e) {
        // KHÔNG chen toast: đây là khối phụ, hỏng thì ẩn đi chứ không được kéo
        // đổ danh sách đối soát ngân hàng đang hiện bên dưới.
        //
        // Nhưng dùng `console.warn` chứ KHÔNG phải `console.debug`: bản đầu ghi
        // debug và nó đã nuốt trọn một lỗi thật (`dinhDangTien` không tồn tại
        // trong phạm vi seller.js — hàm đó nằm ở pos.js). Khối chỉ lặng lẽ ẩn,
        // nhìn y hệt "không có đơn nào cần kiểm". Nuốt lỗi để bảo vệ màn hình
        // là đúng; nuốt luôn cả tiếng kêu là tự bịt mắt mình.
        console.warn('Chưa tải được đơn offline cần kiểm:', e);
        renderDonOffline([]);
    }
}

async function lamMoiBadgeDoiSoatNen() {
    if (document.hidden || !doiSoatShopId) return;
    const tabDangMo = document.getElementById('reconciliation')?.classList.contains('active');
    if (tabDangMo) return loadDoiSoat();
    const shopId = doiSoatShopId;
    const requestId = ++doiSoatBadgeRequestId;
    try {
        const params = new URLSearchParams({
            page: '1',
            per_page: '1',
            reconciliation_only: 'true'
        });
        const res = await apiCall(
            `/dashboard/seller/${shopId}?${params.toString()}`
        );
        if (requestId !== doiSoatBadgeRequestId || shopId !== doiSoatShopId) return;
        capNhatBadgeDoiSoat(res.reconciliation_count ?? res.total_orders ?? 0);
    } catch (e) {
        // Làm mới nền không chen toast vào công việc đang làm.
        console.debug('Chưa làm mới được badge đối soát:', e.message);
    }
}

function renderDoiSoat(orders) {
    const list = document.getElementById('doiSoatList');
    const empty = document.getElementById('doiSoatEmpty');
    const hopLe = (orders || []).filter(o => Number.isInteger(Number(o.id)));
    list.innerHTML = hopLe.map(taoTheDoiSoat).join('');
    empty.style.display = hopLe.length ? 'none' : 'block';
    if (!hopLe.length) {
        empty.innerHTML = `<i class="ph ph-check-circle" style="display:block; margin-bottom:0.5rem; color:var(--success); font-size:2.2rem;"></i>${escapeHtml(t('seller.reconciliation.empty'))}`;
    }
}

function taoTheDoiSoat(order) {
    const orderId = Number(order.id);
    const tienVeChuaGhi = Math.max(Number(order.unapplied_transfer_amount) || 0, 0);
    // Đơn nợ có tiền về giữ `reconciliation_reason` NULL, nên phải suy ra khóa
    // ở đây - để nó rơi vào nhánh mặc định là hiện nhầm thành "đối soát cũ".
    const reason = tienVeChuaGhi > 0 && !order.reconciliation_reason
        ? 'DEBT_TRANSFER_NOTICE'
        : String(order.reconciliation_reason || 'LEGACY_REVIEW');
    const meta = thongTinLoaiDoiSoat(reason);
    const tongDon = Number(order.total) || 0;
    const tienNganHang = Number(order.bank_paid_amount) || 0;
    const tienMat = Number(order.cash_paid_amount) || 0;
    const daNhan = Number(order.received_amount) || 0;
    const conThieu = Math.max(Number(order.remaining_amount) || 0, 0);
    const canHoan = Math.max(Number(order.refund_due_amount) || 0, 0);
    const ngay = dinhDangNgayGio(order.date);

    let nhanChenhLech = t('seller.reconciliation.received');
    let tienChenhLech = daNhan;
    if (reason === 'DEBT_TRANSFER_NOTICE') {
        nhanChenhLech = t('seller.reconciliation.transferred_not_recorded');
        tienChenhLech = tienVeChuaGhi;
    } else if (reason === 'UNDERPAID') {
        nhanChenhLech = t('seller.reconciliation.remaining');
        tienChenhLech = conThieu;
    } else if (reason === 'OVERPAID') {
        nhanChenhLech = t('seller.reconciliation.refund_customer');
        tienChenhLech = canHoan;
    } else if (reason === 'LATE_PAYMENT') {
        nhanChenhLech = t('seller.reconciliation.refund_all');
        tienChenhLech = canHoan;
    }

    let nutXuLy = '';
    if (reason === 'UNDERPAID' && conThieu > 0) {
        nutXuLy = `
            <button id="btnBuTienMat-${orderId}" onclick="thuBuTienMatDoiSoat(${orderId}, ${conThieu})">
                <i class="ph ph-money"></i> ${escapeHtml(t('seller.reconciliation.collect_topup', { amount: dinhDangTienDoiSoat(conThieu) }))}
            </button>`;
    } else if (
        (reason === 'OVERPAID' || reason === 'LATE_PAYMENT')
        && order.refund_pending
        && canHoan > 0
    ) {
        nutXuLy = `
            <button id="btnDaHoan-${orderId}" onclick="moModalHoanTien(${orderId}, ${canHoan}, '${reason}')">
                <i class="ph ph-arrow-u-up-left"></i> ${escapeHtml(t('seller.reconciliation.mark_refunded'))}
            </button>`;
    }

    return `
        <article class="doi-soat-card ${meta.cardClass}" id="doiSoatCard-${orderId}">
            <div class="doi-soat-card-head">
                <div>
                    <h4 class="doi-soat-card-title">${escapeHtml(t('seller.reconciliation.order_title', { id: orderId, title: meta.title }))}</h4>
                    <div class="doi-soat-card-time">${escapeHtml(ngay)}</div>
                </div>
                <span class="doi-soat-state ${meta.stateClass}">${escapeHtml(meta.stateLabel)}</span>
            </div>
            <div class="doi-soat-money-grid">
                <div class="doi-soat-money">
                    <span>${escapeHtml(t('seller.reconciliation.order_total'))}</span>
                    <strong>${dinhDangTienDoiSoat(tongDon)}</strong>
                </div>
                <div class="doi-soat-money">
                    <span>${escapeHtml(t('seller.reconciliation.bank_amount'))}</span>
                    <strong>${dinhDangTienDoiSoat(tienNganHang)}</strong>
                </div>
                <div class="doi-soat-money">
                    <span>${escapeHtml(t('seller.reconciliation.cash_topup'))}</span>
                    <strong>${dinhDangTienDoiSoat(tienMat)}</strong>
                </div>
                <div class="doi-soat-money highlight">
                    <span>${escapeHtml(nhanChenhLech)}</span>
                    <strong>${dinhDangTienDoiSoat(tienChenhLech)}</strong>
                </div>
            </div>
            <p class="doi-soat-note">${escapeHtml(meta.note)}</p>
            <div class="doi-soat-actions">
                <button class="btn-outline" onclick="xemChiTietDon(${orderId})">
                    <i class="ph ph-receipt"></i> ${escapeHtml(t('seller.actions.view_detail'))}
                </button>
                ${nutXuLy}
            </div>
        </article>`;
}

function capNhatPhanTrangDoiSoat(res) {
    const box = document.getElementById('doiSoatPagination');
    const info = document.getElementById('doiSoatThongTinTrang');
    const truoc = document.getElementById('doiSoatTrangTruoc');
    const sau = document.getElementById('doiSoatTrangSau');
    const tong = Number(res.total_orders) || 0;
    if (!box || !info || !truoc || !sau) return;

    box.style.display = tong > DOI_SOAT_MOI_TRANG ? 'flex' : 'none';
    if (tong > 0) {
        const dau = (res.page - 1) * res.per_page + 1;
        const cuoi = Math.min(res.page * res.per_page, tong);
        info.innerText = t('seller.pagination.showing_cases', {
            from: dinhDangSoSeller(dau),
            to: dinhDangSoSeller(cuoi),
            total: dinhDangSoSeller(tong)
        });
    } else {
        info.innerText = '';
    }
    truoc.disabled = res.page <= 1;
    sau.disabled = !res.has_more;
}

function doiTrangDoiSoat(buoc) {
    const trangMoi = trangDoiSoat + buoc;
    if (trangMoi < 1) return;
    trangDoiSoat = trangMoi;
    loadDoiSoat();
}

async function coCaTienMatDangMo() {
    const shopId = doiSoatShopId || dashboardShopId || currentShopId;
    if (!shopId) {
        showToast(t('seller.reconciliation.choose_shop_first'));
        return false;
    }
    try {
        const res = await apiCall(`/shifts/current/${shopId}`);
        if (res?.shift) return true;
        showToast(t('seller.shifts.open_required'));
        return false;
    } catch (e) {
        showToast(e.message || t('seller.shifts.check_error'));
        return false;
    }
}

async function thuBuTienMatDoiSoat(orderId, conThieu) {
    const id = Number(orderId);
    const soTien = Number(conThieu);
    if (!Number.isInteger(id) || !Number.isFinite(soTien) || soTien <= 0) return;
    if (!await coCaTienMatDangMo()) return;

    showCustomConfirm(
        t('seller.reconciliation.topup_title', { id }),
        t('seller.reconciliation.topup_confirm', {
            amount: dinhDangTienDoiSoat(soTien)
        }),
        async () => {
            const button = document.getElementById(`btnBuTienMat-${id}`);
            if (button) button.disabled = true;
            try {
                const res = await apiCall(`/orders/${id}/cash-topup`, 'POST', {});
                showToast(res.msg || t('seller.reconciliation.topup_done'));
                await loadDoiSoat();
            } catch (e) {
                showToast(e.message);
                await loadDoiSoat();
            } finally {
                const currentButton = document.getElementById(`btnBuTienMat-${id}`);
                if (currentButton) currentButton.disabled = false;
            }
        },
        t('seller.reconciliation.money_received')
    );
}

function taoOperationIdHoanTien() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
        return globalThis.crypto.randomUUID();
    }
    return `refund-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function moModalHoanTien(orderId, amount, reason) {
    const id = Number(orderId);
    const soTien = Number(amount);
    if (!Number.isInteger(id) || !Number.isFinite(soTien) || soTien <= 0) return;

    refundOrderId = id;
    refundDueAmount = soTien;
    refundOperationId = taoOperationIdHoanTien();
    refundReason = reason;
    document.getElementById('refundOrderLabel').innerText =
        reason === 'LATE_PAYMENT'
            ? t('seller.refund.cancelled_order', { id })
            : t('seller.refund.completed_order', { id });
    document.getElementById('refundAmount').innerText = dinhDangTienDoiSoat(soTien);
    document.getElementById('refundMethod').value = 'cash';
    document.getElementById('refundReference').value = '';
    document.getElementById('refundNote').value = '';
    capNhatFormHoanTien();
    document.getElementById('refundModal').style.display = 'flex';
    document.body.style.overflow = 'hidden';
    document.getElementById('refundMethod').focus();
}

function capNhatFormHoanTien() {
    const method = document.getElementById('refundMethod')?.value;
    const group = document.getElementById('refundReferenceGroup');
    if (group) group.style.display = method === 'transfer' ? 'block' : 'none';
}

function dongModalHoanTien() {
    if (dangLuuHoanTien) return;
    const modal = document.getElementById('refundModal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
    refundOrderId = null;
    refundDueAmount = 0;
    refundOperationId = null;
    refundReason = null;
}

async function xacNhanDaHoanTien() {
    if (
        dangLuuHoanTien
        || !refundOrderId
        || refundDueAmount <= 0
        || !refundOperationId
    ) return;
    const method = document.getElementById('refundMethod').value;
    if (method !== 'cash' && method !== 'transfer') {
        return showToast(t('seller.refund.choose_method'));
    }
    if (method === 'cash' && !await coCaTienMatDangMo()) return;

    const note = document.getElementById('refundNote').value.trim();
    const reference = method === 'transfer'
        ? document.getElementById('refundReference').value.trim()
        : '';
    const payload = { method, operation_id: refundOperationId };
    if (note) payload.note = note;
    if (reference) payload.reference = reference;

    const orderId = refundOrderId;
    const button = document.getElementById('btnSubmitRefund');
    dangLuuHoanTien = true;
    button.disabled = true;
    button.innerHTML = `<i class="ph ph-spinner-gap"></i> <span data-seller-action-label="refund-submit">${escapeHtml(t('seller.refund.saving'))}</span>`;
    try {
        const res = await apiCall(`/orders/${orderId}/refund-complete`, 'POST', payload);
        showToast(res.msg || t('seller.refund.success'));
        dangLuuHoanTien = false;
        dongModalHoanTien();
        await loadDoiSoat();
    } catch (e) {
        showToast(e.message);
        await loadDoiSoat();
    } finally {
        dangLuuHoanTien = false;
        button.disabled = false;
        button.innerHTML = `<i class="ph ph-check-circle"></i> <span data-seller-action-label="refund-submit">${escapeHtml(t('seller.reconciliation.mark_refunded'))}</span>`;
    }
}

function goToPOS(id, onboardingR2 = false) {
    localStorage.setItem('currentShopId', id);
    const resumeOnboardingR2 = onboardingR2
        || new URLSearchParams(window.location.search).get('onboarding') === 'r2';
    window.location.href = resumeOnboardingR2
        ? '/pos?tour=sale&onboarding=r2'
        : '/pos';
}

function changeShop(id) {
    const shopId = Number.parseInt(id, 10);
    if (!Number.isInteger(shopId) || !allShops.some(shop => shop.id === shopId)) return;
    currentShopId = shopId;
    localStorage.setItem('currentShopId', currentShopId);
    void window.FSellingOnboardingR2?.selectShop?.(currentShopId);
    loadDataForCurrentShop();
    const shop = allShops.find(s => s.id === currentShopId);
    showToast(t('seller.shops.loaded', { name: shop?.name || '' }));
}

function loadDataForCurrentShop() {
    if(allShops.length === 0 || !batDauDungShopHienTai()) return;
    // ADMIN vào seller.html chỉ để làm nghiệp vụ Nhập Hàng. Không tải dashboard,
    // voucher, khách hay nhân viên của từng shop; chỉ cần danh mục sản phẩm để
    // lập phiếu và module công nợ.
    if (MY_ROLE === 'ADMIN') {
        document.getElementById('dashboardContent').style.display = 'none';
        document.getElementById('warehouseContent').style.display = 'none';
        document.getElementById('voucherContent').style.display = 'none';
        document.getElementById('kkContent').style.display = 'none';
        document.getElementById('noShopMsg').style.display = 'none';
        loadProducts();
        window.FSellingPurchasing?.load?.();
        return;
    }
    if (!document.getElementById('action-center')?.classList.contains('active')) {
        loadActionCenter();
    }
    const canReport = coQuyenNhanVien('REPORT');
    const canInventory = coQuyenNhanVien('INVENTORY');
    const canVoucher = coQuyenNhanVien('VOUCHER');
    document.getElementById('dashboardContent').style.display = canReport ? 'block' : 'none';
    document.getElementById('warehouseContent').style.display = canInventory ? 'grid' : 'none';
    document.getElementById('voucherContent').style.display = canVoucher ? 'grid' : 'none';
    document.getElementById('kkContent').style.display = canInventory ? 'block' : 'none';
    document.getElementById('noShopMsg').style.display = 'none';
    
    const shop = allShops.find(s => s.id === currentShopId);
    if (canReport) {
        document.getElementById('currentShopName').innerText = shop.name;
        loadDashboardShop(currentShopId);
    }
    if (canInventory) {
        document.getElementById('whShopName').innerText = shop.name;
        document.getElementById('kkShopName').innerText = shop.name;
        kkXoaHet();   // đổi shop thì phiếu đếm của shop cũ không còn nghĩa lý gì

        // Clear inputs for new shop
        document.getElementById('prodCode').value = '';
        document.getElementById('prodBarcode').value = '';
        document.getElementById('prodName').value = '';
        document.getElementById('prodPrice').value = '';
        document.getElementById('prodImage').value = '';
        document.getElementById('catNameInput').value = '';

        loadCategories();
        loadProducts();
    }
    if (canVoucher) {
        document.getElementById('vcShopName').innerText = shop.name;
        loadVouchers();
    }
    if (
        MY_ROLE === 'SELLER'
        && document.getElementById('loyalty')?.classList.contains('active')
    ) loadLoyaltyProgram();
    if (
        MY_ROLE === 'SELLER'
        && document.getElementById('purchasing')?.classList.contains('active')
    ) window.FSellingPurchasing?.load?.();
}

// Settings List Render
function renderShopsList() {
    const listDiv = document.getElementById('myShopsList');
    listDiv.innerHTML = '';
    if(allShops.length === 0) {
        listDiv.innerHTML = `<p>${escapeHtml(t('seller.shops.no_shops'))}</p>`;
        return;
    }
    allShops.forEach(s => {
        const activeBadge = s.is_active
            ? `<span style="color:var(--success); font-size: 0.8rem; margin-left: 0.5rem; padding: 2px 6px; background: rgba(16,185,129,0.1); border-radius: 4px;">${escapeHtml(t('seller.status.active'))}</span>`
            : `<span style="color:#ef4444; font-size: 0.8rem; margin-left: 0.5rem; padding: 2px 6px; background: rgba(239,68,68,0.1); border-radius: 4px;">${escapeHtml(t('seller.status.inactive'))}</span>`;
        const toggleBtn = `<button class="btn-outline" onclick="toggleShopStatus(${s.id})" style="padding: 0.5rem 1rem; margin-right: 0.5rem;" title="${escapeHtml(t('seller.actions.change_status'))}" aria-label="${escapeHtml(t('seller.actions.change_status'))}"><i class="ph ph-power"></i></button>`;
        const deleteBtn = `<button class="btn-outline" onclick="deleteShop(${s.id})" style="padding: 0.5rem 1rem; color: #ef4444; margin-left: 0.5rem;" title="${escapeHtml(t('common.delete'))}" aria-label="${escapeHtml(t('common.delete'))}"><i class="ph ph-trash"></i></button>`;
        
        listDiv.innerHTML += `
            <div class="shop-list-card">
                <div>
                    <h4 style="display:flex; align-items:center;">${escapeHtml(s.name)} ${activeBadge}</h4>
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.3rem;"><i class="ph ph-map-pin"></i> ${escapeHtml(s.business_address || t('seller.shops.not_updated'))}</div>
                </div>
                <div style="display: flex;">
                    ${toggleBtn}
                    <button class="btn-outline" onclick="openEditShopForm(${s.id})" style="padding: 0.5rem 1rem;"><i class="ph ph-pencil"></i> ${escapeHtml(t('seller.actions.edit'))}</button>
                    ${deleteBtn}
                </div>
            </div>
        `;
    });
}

async function toggleShopStatus(id) {
    try {
        await apiCall(`/shops/${id}/status`, 'PUT');
        showToast(t('seller.shops.updated_status'));
        init();
    } catch(e) { showToast(e.message); }
}

function deleteShop(id) {
    showCustomConfirm(
        t('seller.shops.delete_title'),
        t('seller.shops.delete_confirm'),
        async () => {
            try {
                await apiCall(`/shops/${id}`, 'DELETE');
                showToast(t('seller.shops.deleted'));
                // Clear selected shop if it was the one deleted
                if (currentShopId === id) {
                    currentShopId = null;
                    localStorage.removeItem('currentShopId');
                    loadedCurrentShopId = null;
                    currentShopGeneration += 1;
                    xoaDuLieuShopCuKhoiGiaoDien();
                }
                init();
            } catch(e) { showToast(e.message); }
        }
    );
}

function openCreateShopForm() {
    if(allShops.length >= 3) return showToast(t('seller.shops.limit_reached'));
    editShopId = null;
    const shopFnbEnabled = document.getElementById('shopFnbEnabled');
    const shopFnbSetting = document.getElementById('shopFnbSetting');
    shopFnbEnabled.checked = false;
    shopFnbEnabled.disabled = true;
    shopFnbSetting.hidden = true;
    document.getElementById('formTitle').innerHTML = `<i class="ph ph-storefront" style="color: var(--primary);"></i> <span data-seller-action-label="shop-form-title">${escapeHtml(t('seller.shops.create_title'))}</span>`;
    document.getElementById('btnSaveShop').innerHTML = `<i class="ph ph-plus-circle"></i> <span data-seller-action-label="shop-save">${escapeHtml(t('seller.shops.create_confirm'))}</span>`;
    
    renderBankOptions();
    // Clear form
    document.getElementById('shopName').value = '';
    document.getElementById('shopAddress').value = '';
    document.getElementById('shopTaxCode').value = '';
    document.getElementById('shopPhone').value = '';
    document.getElementById('shopEmail').value = '';
    document.getElementById('bankCode').value = '';
    document.getElementById('bankAcc').value = '';
    document.getElementById('bankAccName').value = '';
    
    triggerPreview();
    
    document.getElementById('shopFormContainer').style.display = 'grid';
    document.getElementById('shopFormDivider').style.display = 'block';
}

function openEditShopForm(id) {
    editShopId = id;
    const shop = allShops.find(s => s.id === id);
    const shopFnbEnabled = document.getElementById('shopFnbEnabled');
    const shopFnbSetting = document.getElementById('shopFnbSetting');
    shopFnbEnabled.checked = Boolean(shop.fnb_enabled);
    shopFnbEnabled.disabled = MY_ROLE !== 'SELLER';
    shopFnbSetting.hidden = MY_ROLE !== 'SELLER';
    document.getElementById('formTitle').innerHTML = `<i class="ph ph-storefront" style="color: var(--primary);"></i> <span data-seller-action-label="shop-form-title">${escapeHtml(t('seller.shops.edit_title', { name: shop.name }))}</span>`;
    document.getElementById('btnSaveShop').innerHTML = `<i class="ph ph-check-circle"></i> <span data-seller-action-label="shop-save">${escapeHtml(t('seller.shops.save_update'))}</span>`;
    
    renderBankOptions();
    // Fill Settings Form
    document.getElementById('shopName').value = shop.name;
    document.getElementById('shopAddress').value = shop.business_address || '';
    document.getElementById('shopTaxCode').value = shop.tax_code || '';
    document.getElementById('shopPhone').value = shop.phone || '';
    document.getElementById('shopEmail').value = shop.email || '';
    document.getElementById('bankCode').value = shop.bank_code;
    document.getElementById('bankAcc').value = shop.bank_account_no;
    document.getElementById('bankAccName').value = shop.bank_account_name || '';
    
    triggerPreview();
    
    document.getElementById('shopFormContainer').style.display = 'grid';
    document.getElementById('shopFormDivider').style.display = 'block';
}

function triggerPreview() {
    document.getElementById('shopName').dispatchEvent(new Event('input'));
    document.getElementById('shopTaxCode').dispatchEvent(new Event('input'));
    document.getElementById('shopAddress').dispatchEvent(new Event('input'));
}

function closeShopForm() {
    document.getElementById('shopFormContainer').style.display = 'none';
    document.getElementById('shopFormDivider').style.display = 'none';
}

async function saveShop() {
    const name = document.getElementById('shopName').value.trim();
    const address = document.getElementById('shopAddress').value.trim();
    const taxCode = document.getElementById('shopTaxCode').value.trim();
    const phone = document.getElementById('shopPhone').value.trim();
    const email = document.getElementById('shopEmail').value.trim();
    const bankAcc = document.getElementById('bankAcc').value.trim();
    const bankAccName = document.getElementById('bankAccName').value.trim();
    const bankCode = document.getElementById('bankCode').value;

    if (!name) return showToast(t('seller.shops.name_required_error'));
    if (!phone) return showToast(t('seller.shops.phone_required_error'));
    if (bankCode || bankAcc || bankAccName) {
        if (!bankCode) return showToast(t('seller.shops.bank_required_error'));
        if (!bankAcc) return showToast(t('seller.shops.account_required_error'));
        if (!bankAccName) return showToast(t('seller.shops.account_name_required_error'));
    }

    const body = {
        name,
        business_address: address,
        tax_code: taxCode,
        phone,
        email,
        bank_account_no: bankAcc,
        bank_account_name: bankAccName,
        bank_code: bankCode
    };
    
    try {
        if(editShopId) {
            await apiCall(`/shops/${editShopId}`, 'PUT', body);
            showToast(t('seller.shops.updated'));
        } else {
            await apiCall('/shops', 'POST', body);
            showToast(t('seller.shops.created'));
        }
        setTimeout(() => location.reload(), 1000);
    } catch(e) { showToast(e.message); }
}

// --- DASHBOARD / DATA LOGIC ---

function switchWarehouseSubTab(subTab) {
    const nut = {
        products: 'whSubTabProds',
        categories: 'whSubTabCats',
        expiry: 'whSubTabExpiry',
        forecast: 'whSubTabForecast',
        clearance: 'whSubTabClearance',
        writeoff: 'whSubTabWriteOff'
    };
    const khoi = {
        products: ['warehouseProductsSection', 'grid'],
        categories: ['warehouseCategoriesSection', 'grid'],
        expiry: ['warehouseExpirySection', 'block'],
        forecast: ['warehouseForecastSection', 'block'],
        clearance: ['warehouseClearanceSection', 'block'],
        writeoff: ['warehouseWriteOffSection', 'block']
    };
    // Nhân viên gọi thẳng switchWarehouseSubTab('writeoff') từ console vẫn không
    // xem được gì: endpoint phía sau đòi require_cost_visibility. Nhánh này chỉ
    // để họ không rơi vào một tab trống không hiểu vì sao.
    if (subTab === 'writeoff' && !XEM_DUOC_GIA_VON) subTab = 'products';
    // Xả hàng cũng vậy: mọi con số dựng từ giá vốn, endpoint đòi
    // require_cost_visibility. Nhánh này chỉ để nhân viên không rơi vào một tab
    // trống không hiểu vì sao.
    if (subTab === 'clearance' && !XEM_DUOC_GIA_VON) subTab = 'products';
    if (!khoi[subTab]) subTab = 'products';

    Object.entries(nut).forEach(([ten, id]) => {
        document.getElementById(id)?.classList.toggle('active', ten === subTab);
    });
    Object.entries(khoi).forEach(([ten, [id, kieu]]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = ten === subTab ? kieu : 'none';
    });
    const importPanel = document.getElementById('productImportPanel');
    if (importPanel) importPanel.hidden = MY_ROLE !== 'SELLER' || subTab !== 'products';

    if (subTab === 'expiry') loadHanSuDung();
    if (subTab === 'forecast') loadDuBaoNhapHang();
    if (subTab === 'clearance') loadXaHangTon();
    if (subTab === 'writeoff') loadPhieuHuy();
}

// ===== L3: trợ lý hỏi đáp =====

let troLyDangHoi = false;
let cauHoiTroLyGanNhat = null;

// Một câu trả lời chỉ mở màn báo cáo ĐÃ CÓ. Không chạy mutation và không tạo
// đường điều hướng thứ hai song song với `switchTab` / Warehouse subtabs.
const ASSISTANT_INTENT_TARGETS = Object.freeze({
    DOANH_THU: { tab: 'dashboard' },
    SO_DON: { tab: 'dashboard' },
    SO_SANH_TUAN: { tab: 'dashboard' },
    BAN_CHAY: { tab: 'dashboard' },
    SAP_HET_HAN: { tab: 'warehouse', subTab: 'expiry' },
    CAN_NHAP: { tab: 'warehouse', subTab: 'forecast' },
    HANG_E: { tab: 'warehouse', subTab: 'clearance', needsCost: true },
    CONG_NO: { tab: 'customers' },
    LAI: { tab: 'dashboard' },
    TONG_QUAN: { tab: 'dashboard' },
    GIA_TON: { tab: 'warehouse', subTab: 'products' },
    CHI_PHI: { tab: 'cashflow', needsCost: true },
    SHOP: { tab: 'subscription', ownerOnly: true },
    CA_TIEN: { tab: 'dashboard' }
});

function mucBaoCaoTroLy(yDinh) {
    const target = ASSISTANT_INTENT_TARGETS[yDinh];
    if (!target) return null;
    if (target.ownerOnly && MY_ROLE !== 'SELLER') return null;
    if (target.needsCost && !XEM_DUOC_GIA_VON) return null;
    return target;
}

async function updateFnbSetting(input) {
    if (!editShopId || input.disabled) return;
    const shopId = Number(editShopId);
    const previous = !input.checked;
    input.disabled = true;
    const operationId = fnbSettingOperations.get(shopId)
        || `fnb-setting-${crypto.randomUUID()}`;
    fnbSettingOperations.set(shopId, operationId);
    try {
        const result = await apiCall(`/fnb/shops/${shopId}/settings`, 'PATCH', {
            enabled: input.checked,
            expected_revision: Number(
                allShops.find(row => row.id === shopId)?.fnb_revision || 0
            ),
            operation_id: operationId,
        });
        const shop = allShops.find(row => row.id === shopId);
        if (shop) {
            shop.fnb_enabled = result.fnb_enabled;
            shop.fnb_revision = result.fnb_revision;
        }
        fnbSettingOperations.delete(shopId);
        if (Number(editShopId) === shopId) {
            showToast(t(result.fnb_enabled ? 'seller.shops.fnb_enabled' : 'seller.shops.fnb_disabled'));
        }
    } catch (error) {
        if (Number(editShopId) === shopId) input.checked = previous;
        if (Number(error.status) >= 400 && Number(error.status) < 500) {
            fnbSettingOperations.delete(shopId);
        }
        if (Number(editShopId) === shopId) showToast(error.message);
    } finally {
        if (Number(editShopId) === shopId) input.disabled = false;
    }
}

function moBaoCaoTroLy(yDinh) {
    const target = mucBaoCaoTroLy(yDinh);
    if (!target) return;
    switchTab(target.tab, document.querySelector(`.tab-btn[data-main-tab="${target.tab}"]`));
    if (target.tab === 'warehouse' && target.subTab) switchWarehouseSubTab(target.subTab);
    if (target.tab === 'customers') loadCustomers();
}

function ganHanhDongTroLy(bong, yDinh, nguon) {
    const noiDung = bong?.firstElementChild;
    if (!noiDung || !mucBaoCaoTroLy(yDinh)) return;
    const nut = document.createElement('button');
    nut.type = 'button';
    nut.className = 'btn-outline assistant-answer-action';
    nut.innerHTML = '<i class="ph ph-arrow-square-out" aria-hidden="true"></i>';
    const nhan = document.createElement('span');
    nhan.innerText = t('seller.assistant.open_report', {
        name: nguon || t('seller.assistant.report_fallback')
    });
    nut.appendChild(nhan);
    nut.onclick = () => moBaoCaoTroLy(yDinh);
    noiDung.appendChild(nut);
}

// Lệnh điều hướng chạy ngay trong trình duyệt. Cố ý yêu cầu động từ rõ ràng
// ("mở", "đi tới", "open") để câu hỏi báo cáo như "hàng nào sắp hết" vẫn đi
// qua assistant API và nhận số liệu, thay vì bị biến thành một lệnh chuyển tab.
const ASSISTANT_NAVIGATION_TARGETS = Object.freeze({
    POS: {
        pos: true,
        labelKey: 'seller.assistant.destination_pos',
        icon: 'ph-shopping-cart',
        patterns: [
            /\b(?:mo|vao|di toi|dua toi den)\b.*\b(?:pos|ban hang|tinh tien|mo ca|dong ca)\b/,
            /\btoi muon\b.*\b(?:ban hang|tinh tien|mo ca|dong ca)\b/,
            /\b(?:open|go to|take me to)\b.*\b(?:pos|checkout|sales|open shift|close shift)\b/
        ]
    },
    WAREHOUSE: {
        tab: 'warehouse',
        subTab: 'products',
        labelKey: 'seller.tabs.warehouse',
        icon: 'ph-package',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:kho|kho hang|san pham|ton kho)\b/,
            /\btoi muon\b.*\b(?:them|tao|nhap)\b.*\bsan pham\b/,
            /\b(?:open|go to)\b.*\b(?:warehouse|products|inventory)\b/,
            /\bi want to\b.*\b(?:add|create|import)\b.*\bproducts?\b/
        ]
    },
    PURCHASING: {
        tab: 'purchasing',
        labelKey: 'seller.tabs.purchasing',
        icon: 'ph-truck',
        patterns: [
            /\b(?:mo|vao|di toi|toi muon)\b.*\b(?:nhap hang|mua hang|nha cung cap|don nhap)\b/,
            /\b(?:open|go to)\b.*\b(?:purchasing|purchase orders?|suppliers?)\b/
        ]
    },
    CUSTOMERS: {
        tab: 'customers',
        labelKey: 'seller.tabs.customers',
        icon: 'ph-users',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:khach hang|cong no)\b/,
            /\b(?:open|go to)\b.*\b(?:customers?|customer debt)\b/
        ]
    },
    CASHFLOW: {
        tab: 'cashflow',
        labelKey: 'seller.tabs.cashflow',
        icon: 'ph-wallet',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:dong tien|chi phi|lai rong)\b/,
            /\b(?:open|go to)\b.*\b(?:cash flow|expenses?|net profit)\b/
        ]
    },
    RECONCILIATION: {
        tab: 'reconciliation',
        labelKey: 'seller.tabs.reconciliation',
        icon: 'ph-arrows-left-right',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:doi soat|chuyen khoan)\b/,
            /\b(?:open|go to)\b.*\b(?:reconciliation|bank transfers?)\b/
        ]
    },
    ACTION_CENTER: {
        tab: 'action-center',
        labelKey: 'seller.tabs.action_center',
        icon: 'ph-list-checks',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:can xu ly|viec can lam|trung tam xu ly)\b/,
            /\b(?:open|go to)\b.*\b(?:action center|things to do)\b/
        ]
    },
    SETTINGS: {
        tab: 'settings',
        labelKey: 'seller.tabs.settings',
        icon: 'ph-sliders',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:cai dat|thiet lap)\b/,
            /\b(?:open|go to)\b.*\bsettings\b/
        ]
    },
    DASHBOARD: {
        tab: 'dashboard',
        labelKey: 'seller.tabs.dashboard',
        icon: 'ph-chart-bar',
        patterns: [
            /\b(?:mo|vao|di toi)\b.*\b(?:thong ke|tong quan|dashboard)\b/,
            /\b(?:open|go to)\b.*\b(?:dashboard|statistics|overview)\b/
        ]
    }
});

// Hướng dẫn cố ý chỉ mô tả đúng các nút đang có. Không tự bấm, không ghi dữ
// liệu và không gọi server: người bán luôn là người quyết định thao tác cuối.
const ASSISTANT_GUIDED_TASKS = Object.freeze({
    OPEN_SHIFT: {
        key: 'open_shift',
        target: ASSISTANT_NAVIGATION_TARGETS.POS,
        patterns: [
            /\b(?:mo|bat dau|open|start)\b.*\b(?:ca|shift)\b/
        ]
    },
    CLOSE_SHIFT: {
        key: 'close_shift',
        target: ASSISTANT_NAVIGATION_TARGETS.POS,
        patterns: [
            /\b(?:dong|ket|chot|close|end)\b.*\b(?:ca|shift)\b/
        ]
    },
    CREATE_PRODUCT: {
        key: 'create_product',
        target: ASSISTANT_NAVIGATION_TARGETS.WAREHOUSE,
        patterns: [
            /\b(?:tao|them|add|create)\b.*\b(?:san pham|products?)\b/
        ]
    },
    RECEIVE_STOCK: {
        key: 'receive_stock',
        target: ASSISTANT_NAVIGATION_TARGETS.PURCHASING,
        patterns: [
            /\b(?:nhap hang|nhan hang|tao phieu nhap|receive stock|receive goods|stock in|purchase receipt)\b/
        ]
    },
    CUSTOMER_DEBT: {
        key: 'customer_debt',
        target: ASSISTANT_NAVIGATION_TARGETS.CUSTOMERS,
        patterns: [
            /\b(?:thu no|khach.*tra no|cong no khach|collect.*debt|customer.*debt)\b/
        ]
    },
    STOCKTAKE: {
        key: 'stocktake',
        target: {
            tab: 'kiemke',
            labelKey: 'seller.tabs.stocktake',
            icon: 'ph-clipboard-text'
        },
        patterns: [
            /\b(?:kiem ke|dem kho|stocktake|stock count|count inventory)\b/
        ]
    },
    EXPIRY: {
        key: 'expiry',
        target: {
            tab: 'warehouse',
            subTab: 'expiry',
            labelKey: 'seller.warehouse.expiry',
            icon: 'ph-calendar-x'
        },
        patterns: [
            /\b(?:sap het han|het han|han su dung|expir(?:y|ing|ed))\b/
        ]
    },
    REORDER: {
        key: 'reorder',
        target: {
            tab: 'warehouse',
            subTab: 'forecast',
            labelKey: 'seller.warehouse.forecast',
            icon: 'ph-trend-up'
        },
        patterns: [
            /\b(?:sap het hang|gan het hang|hang sap het|can nhap them|nhap them hang|reorder|running out|low stock)\b/
        ]
    },
    SELL: {
        key: 'sell',
        target: ASSISTANT_NAVIGATION_TARGETS.POS,
        patterns: [
            /\b(?:ban hang|tinh tien|tao don|checkout|sell|make a sale)\b/
        ]
    }
});

function chuanHoaCauTroLy(cau) {
    return (cau || '').toLowerCase().normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd')
        .replace(/\s+/g, ' ').trim();
}

function mucDieuHuongTroLy(target) {
    if (!target) return null;
    const button = target.pos
        ? document.getElementById('btnOpenPos')
        : document.querySelector(`.tab-btn[data-main-tab="${target.tab}"]`);
    if (!button || button.hidden || button.style.display === 'none') return null;
    return target;
}

function nhanLenhDieuHuongTroLy(cau) {
    const normalized = chuanHoaCauTroLy(cau);
    return Object.values(ASSISTANT_NAVIGATION_TARGETS).find(target =>
        target.patterns.some(pattern => pattern.test(normalized))
    ) || null;
}

function nhanYeuCauHuongDanTroLy(cau) {
    const normalized = chuanHoaCauTroLy(cau);
    const dangHoiCachLam = /\b(?:lam sao|huong dan|chi toi|giup toi|toi can lam gi|cach|how do i|how to|show me how|guide me|help me)\b/
        .test(normalized);
    if (!dangHoiCachLam) return null;
    return Object.values(ASSISTANT_GUIDED_TASKS).find(huongDan =>
        huongDan.patterns.some(pattern => pattern.test(normalized))
    ) || null;
}

function moDichDenTroLy(target) {
    target = mucDieuHuongTroLy(target);
    if (!target) return;
    if (target.pos) return goToPOS(currentShopId);
    switchTab(target.tab, document.querySelector(`.tab-btn[data-main-tab="${target.tab}"]`));
    if (target.tab === 'warehouse' && target.subTab) switchWarehouseSubTab(target.subTab);
    if (target.tab === 'customers') loadCustomers();
}

function ganDieuHuongTroLy(bong, target) {
    const noiDung = bong?.firstElementChild;
    if (!noiDung || !mucDieuHuongTroLy(target)) return;
    const nut = document.createElement('button');
    nut.type = 'button';
    nut.className = 'btn-outline assistant-answer-action';
    nut.innerHTML = `<i class="ph ${target.icon}" aria-hidden="true"></i>`;
    const nhan = document.createElement('span');
    nhan.innerText = t('seller.assistant.open_destination', { name: t(target.labelKey) });
    nut.appendChild(nhan);
    nut.onclick = () => moDichDenTroLy(target);
    noiDung.appendChild(nut);
}

function ganHuongDanTroLy(bong, huongDan) {
    const noiDung = bong?.firstElementChild;
    if (!noiDung || !huongDan) return;
    const danhSach = document.createElement('ol');
    danhSach.className = 'assistant-guide-steps';
    for (let step = 1; step <= 3; step += 1) {
        const dong = document.createElement('li');
        dong.innerText = t(`seller.assistant.guide.${huongDan.key}.step${step}`);
        danhSach.appendChild(dong);
    }
    noiDung.appendChild(danhSach);

    const dichDen = mucDieuHuongTroLy(huongDan.target);
    const ghiChu = document.createElement('div');
    ghiChu.className = 'assistant-answer-source';
    ghiChu.innerText = dichDen
        ? t('seller.assistant.guide_safety')
        : t('seller.assistant.guide_unavailable', {
            name: t(huongDan.target.labelKey)
        });
    noiDung.appendChild(ghiChu);
    if (dichDen) ganDieuHuongTroLy(bong, huongDan.target);
}

function capNhatNutHoiTroLy() {
    const nut = document.getElementById('assistantSend');
    const cau = document.getElementById('assistantInput')?.value.trim();
    if (nut) nut.disabled = troLyDangHoi || !currentShopId || !cau;
}

function datTrangThaiTroLy(khoa = null, dangTai = false) {
    const trangThai = document.getElementById('assistantStatus');
    const khungChat = document.getElementById('assistantChat');
    if (khungChat) khungChat.setAttribute('aria-busy', dangTai ? 'true' : 'false');
    if (!trangThai) return;
    trangThai.hidden = !khoa;
    trangThai.dataset.state = dangTai ? 'loading' : '';
    trangThai.textContent = khoa ? t(khoa) : '';
}

// ----- Giọng nói cho trợ lý -----
//
// NGHE: Web Speech API của trình duyệt. Miễn phí, nhưng KHÔNG có ở khắp nơi -
// Firefox không hỗ trợ, và webview trong Zalo/Facebook thì có hàm nhưng gọi là
// lỗi. Người bán Việt Nam hay mở link từ Zalo nên đây không phải ca hiếm.
//
// ĐỌC: dùng lại `DocTien.noi()` đã có sẵn cho màn POS. Nó đã xử lý đúng cái
// khó nhất: máy có giọng Việt thì đọc tại chỗ, không có (Chrome trên Windows)
// thì nhờ server đọc hộ, không cấu hình server thì im lặng.
const KHOA_DOC_TRA_LOI = 'troLy.doc';
let boNghe = null;
let dangNghe = false;

function ngheDuocKhong() {
    // `isSecureContext` false = trang chạy qua http trên IP LAN. Chrome chặn
    // mic ở đó, và nút mic sẽ bấm mãi không ra gì.
    if (!window.isSecureContext) return false;
    return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function docTraLoiDangBat() {
    return localStorage.getItem(KHOA_DOC_TRA_LOI) === '1';
}

function doiDocTraLoi() {
    const bat = document.getElementById('assistantSpeak')?.checked;
    localStorage.setItem(KHOA_DOC_TRA_LOI, bat ? '1' : '0');
    if (bat) capNhatGhiChuGiong();
}

/** Nói cho người dùng biết TRƯỚC là máy này có đọc được tiếng Việt không. */
async function capNhatGhiChuGiong() {
    const o = document.getElementById('assistantVoiceNote');
    if (!o) return;
    if (!document.getElementById('assistantSpeak')?.checked) { o.innerText = ''; return; }
    if (window.DocTien?.giongTiengViet?.().length) { o.innerText = ''; return; }
    // Máy không có giọng Việt: hỏi server một lần xem có đọc hộ được không.
    const serverDoc = window.DocTien?.serverDocDuoc?.();
    const co = serverDoc === null ? await window.DocTien?.kiemTraServer?.() : serverDoc;
    o.innerText = co ? '' : t('seller.assistant.no_voice');
}

let daNgheDoiGiong = false;

function khoiTaoGiongNoiTroLy() {
    const nutMic = document.getElementById('assistantMic');
    if (nutMic) nutMic.style.display = ngheDuocKhong() ? '' : 'none';
    const oDoc = document.getElementById('assistantSpeak');
    if (oDoc) oDoc.checked = docTraLoiDangBat();

    // `getVoices()` thường trả RỖNG ở lần gọi đầu rồi mới nạp xong sau đó.
    // Không chờ sự kiện này thì dòng ghi chú sẽ khẳng định "máy chưa có giọng
    // tiếng Việt" trên đúng cái máy đang có - nói sai về máy của người dùng còn
    // tệ hơn là không nói gì.
    if (!daNgheDoiGiong && window.speechSynthesis && 'onvoiceschanged' in window.speechSynthesis) {
        daNgheDoiGiong = true;
        window.speechSynthesis.addEventListener('voiceschanged', capNhatGhiChuGiong);
    }
    capNhatGhiChuGiong();
}

function batTatNgheTroLy() {
    if (dangNghe) { boNghe?.stop(); return; }
    const BoNhanDang = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!BoNhanDang) { showToast(t('seller.assistant.mic_unsupported')); return; }

    boNghe = new BoNhanDang();
    boNghe.lang = 'vi-VN';
    boNghe.continuous = false;      // hỏi một câu rồi dừng, không nghe lén
    boNghe.interimResults = false;  // chỉ lấy kết quả cuối, khỏi nhấp nháy

    const nut = document.getElementById('assistantMic');
    boNghe.onstart = () => {
        dangNghe = true;
        if (nut) { nut.classList.add('btn-primary'); nut.classList.remove('btn-outline'); }
        document.getElementById('assistantInput').placeholder = t('seller.assistant.listening');
    };
    const ketThuc = () => {
        dangNghe = false;
        if (nut) { nut.classList.remove('btn-primary'); nut.classList.add('btn-outline'); }
        const o = document.getElementById('assistantInput');
        if (o) o.placeholder = t('seller.assistant.placeholder');
    };
    boNghe.onend = ketThuc;
    boNghe.onerror = (e) => {
        ketThuc();
        // 'aborted' là do chính người dùng bấm dừng - không phải lỗi, đừng la.
        if (e.error === 'aborted') return;
        const khoa = {
            'not-allowed': 'seller.assistant.mic_denied',
            'service-not-allowed': 'seller.assistant.mic_denied',
            'no-speech': 'seller.assistant.mic_no_speech'
        }[e.error] || 'seller.assistant.mic_failed';
        showToast(t(khoa));
    };
    boNghe.onresult = (e) => {
        const chu = e.results?.[0]?.[0]?.transcript || '';
        if (!chu.trim()) return;
        document.getElementById('assistantInput').value = chu;
        capNhatNutHoiTroLy();
        guiCauHoi();       // nói xong là hỏi luôn, khỏi bắt bấm thêm nút
    };

    try {
        boNghe.start();
    } catch (e) {
        ketThuc();
        showToast(t('seller.assistant.mic_failed'));
    }
}

/** Đổi số tiền sang CHỮ trước khi đọc.
 *
 *  Bộ đọc gặp "3.740.500đ" sẽ đọc thành "ba chấm bảy bốn không chấm năm không
 *  không" - đúng bài học đã ghi trong `doc-tien.js`. CHỈ đổi cụm có đuôi "đ":
 *  các số khác trong câu ("còn đủ bán 0.6 ngày") dùng dấu chấm làm dấu THẬP
 *  PHÂN, đổi luôn là đọc sai theo hướng ngược lại.
 */
function docDuocCauTraLoi(chu) {
    return (chu || '').replace(/(\d[\d.]*)đ/g, (nguyen, so) => {
        const giaTri = Number(so.replace(/\./g, ''));
        if (!Number.isFinite(giaTri) || !window.DocTien?.docSo) return nguyen;
        return window.DocTien.docSo(giaTri) + ' đồng';
    });
}

function moTroLy() {
    // Người dùng thường mở Trợ lý sau khi đã cuộn sâu ở một báo cáo dài. Giữ
    // nguyên scrollY làm họ rơi vào giữa khung chat và không thấy lời giới thiệu.
    window.scrollTo({ top: 0, behavior: 'auto' });
    const than = document.getElementById('assistantBody');
    const chonShop = document.getElementById('assistantShopSelector');
    if (!than) return;
    // Chưa chọn shop thì chỉ hiện danh sách shop. Hỏi "hôm nay bán bao nhiêu"
    // khi chưa biết hỏi về cửa hàng nào là câu hỏi không có câu trả lời.
    const daChon = Boolean(currentShopId);
    than.style.display = daChon ? '' : 'none';
    if (chonShop) chonShop.style.display = '';
    capNhatNutHoiTroLy();
    if (!daChon) return;

    const khungChat = document.getElementById('assistantChat');
    if (khungChat && Number(khungChat.dataset.shopId) !== Number(currentShopId)) {
        khungChat.replaceChildren();
        khungChat.dataset.shopId = String(currentShopId);
        delete khungChat.dataset.daChao;
        cauHoiTroLyGanNhat = null;
    }
    if (khungChat && !khungChat.dataset.daChao) {
        khungChat.dataset.daChao = '1';
        themBongChat('may', t('seller.assistant.greeting'));
    }
    veGoiY();
    khoiTaoGiongNoiTroLy();
    taiTongHopDanhGiaTroLy();
}

function veGoiY(danhSach = null) {
    const o = document.getElementById('assistantSuggestions');
    if (!o) return;
    const goiY = danhSach || [
        t('seller.assistant.sample_guide'),
        t('seller.assistant.sample_today'),
        t('seller.assistant.sample_overview'),
        t('seller.assistant.sample_reorder'),
        t('seller.assistant.sample_expiry'),
        t('seller.assistant.sample_debt'),
        t('seller.assistant.sample_cash')
    ];
    o.innerHTML = '';
    goiY.forEach(cau => {
        const nut = document.createElement('button');
        nut.type = 'button';
        nut.className = 'btn-outline';
        nut.innerText = cau;
        nut.onclick = () => {
            document.getElementById('assistantInput').value = cau;
            capNhatNutHoiTroLy();
            guiCauHoi();
        };
        o.appendChild(nut);
    });
}

/** Một bong bóng chat. `ben` = 'nguoi' | 'may'. */
function themBongChat(ben, chu, phuChu = '') {
    const khung = document.getElementById('assistantChat');
    if (!khung) return null;
    const cuaNguoi = ben === 'nguoi';
    const bong = document.createElement('div');
    bong.className = `assistant-message ${cuaNguoi ? 'from-user' : 'from-assistant'}`;
    const noiDung = document.createElement('div');
    noiDung.className = 'assistant-bubble';
    const cauTraLoi = document.createElement('div');
    cauTraLoi.className = 'assistant-answer-text';
    cauTraLoi.innerText = chu;
    noiDung.appendChild(cauTraLoi);
    if (phuChu) {
        const nguon = document.createElement('div');
        nguon.className = 'assistant-answer-source';
        nguon.innerText = phuChu;
        noiDung.appendChild(nguon);
    }
    bong.appendChild(noiDung);
    khung.appendChild(bong);
    khung.scrollTop = khung.scrollHeight;
    return bong;
}

function ganThuLaiTroLy(bong) {
    const noiDung = bong?.firstElementChild;
    if (!noiDung || !cauHoiTroLyGanNhat) return;
    const nut = document.createElement('button');
    nut.type = 'button';
    nut.className = 'btn-outline assistant-answer-action';
    nut.innerHTML = '<i class="ph ph-arrow-clockwise" aria-hidden="true"></i>';
    const nhan = document.createElement('span');
    nhan.innerText = t('seller.assistant.retry');
    nut.appendChild(nhan);
    nut.onclick = thuLaiCauHoiTroLy;
    noiDung.appendChild(nut);
}

function thuLaiCauHoiTroLy() {
    const lanTruoc = cauHoiTroLyGanNhat;
    if (!lanTruoc || troLyDangHoi || Number(currentShopId) !== Number(lanTruoc.shopId)) return;
    const o = document.getElementById('assistantInput');
    if (!o) return;
    o.value = lanTruoc.cau;
    capNhatNutHoiTroLy();
    guiCauHoi();
}

const LY_DO_DANH_GIA_TRO_LY = Object.freeze({
    NOT_UNDERSTOOD: 'seller.assistant.reason_not_understood',
    WRONG_REPORT: 'seller.assistant.reason_wrong_report',
    WRONG_TIME_RANGE: 'seller.assistant.reason_wrong_time_range',
    WRONG_NUMBERS: 'seller.assistant.reason_wrong_numbers',
    OTHER: 'seller.assistant.reason_other'
});

let lanTaiTongHopTroLy = 0;

function taoNutDanhGiaTroLy(label, onClick) {
    const nut = document.createElement('button');
    nut.type = 'button';
    nut.className = 'btn-outline assistant-feedback-action';
    nut.innerText = label;
    nut.setAttribute('aria-label', label);
    nut.onclick = onClick;
    return nut;
}

function ganDanhGiaTroLy(bong, token, shopId) {
    const noiDung = bong?.firstElementChild;
    if (!noiDung || !token) return;

    const khung = document.createElement('div');
    khung.style.cssText = 'margin-top:0.55rem; padding-top:0.45rem; border-top:1px solid rgba(127,127,127,0.25);';
    const cauHoi = document.createElement('div');
    cauHoi.style.cssText = 'font-size:0.72rem; opacity:0.75; margin-bottom:0.35rem;';
    cauHoi.innerText = t('seller.assistant.feedback_question');
    const cacNut = document.createElement('div');
    cacNut.style.cssText = 'display:flex; gap:0.35rem; flex-wrap:wrap;';

    const huuIch = taoNutDanhGiaTroLy(t('seller.assistant.helpful'), () => {
        guiDanhGiaTroLy(khung, token, shopId, 'HELPFUL');
    });
    const chuaDung = taoNutDanhGiaTroLy(t('seller.assistant.not_helpful'), () => {
        cauHoi.innerText = t('seller.assistant.choose_reason');
        cacNut.replaceChildren();
        Object.entries(LY_DO_DANH_GIA_TRO_LY).forEach(([reason, key]) => {
            cacNut.appendChild(taoNutDanhGiaTroLy(t(key), () => {
                guiDanhGiaTroLy(khung, token, shopId, 'NOT_HELPFUL', reason);
            }));
        });
    });
    cacNut.append(huuIch, chuaDung);
    khung.append(cauHoi, cacNut);
    noiDung.appendChild(khung);
}

async function guiDanhGiaTroLy(khung, token, shopId, rating, reason = null) {
    const cacNut = Array.from(khung.querySelectorAll('button'));
    cacNut.forEach(nut => { nut.disabled = true; });
    const payload = { feedback_token: token, rating };
    if (reason) payload.reason = reason;
    try {
        await apiCall(`/assistant/${shopId}/feedback`, 'POST', payload);
        khung.replaceChildren();
        const camOn = document.createElement('small');
        camOn.style.opacity = '0.75';
        camOn.innerText = t('seller.assistant.feedback_thanks');
        khung.appendChild(camOn);
        if (currentShopId === shopId) taiTongHopDanhGiaTroLy();
    } catch (error) {
        cacNut.forEach(nut => { nut.disabled = false; });
        showToast(error.message || t('seller.assistant.feedback_failed'));
    }
}

async function taiTongHopDanhGiaTroLy() {
    const khung = document.getElementById('assistantFeedbackSummary');
    const noiDung = document.getElementById('assistantFeedbackSummaryBody');
    if (!khung || !noiDung || !currentShopId || !['SELLER', 'ADMIN'].includes(MY_ROLE)) {
        if (khung) khung.hidden = true;
        return;
    }
    const shopId = currentShopId;
    const lanNay = ++lanTaiTongHopTroLy;
    try {
        const summary = await apiCall(`/assistant/${shopId}/feedback/summary`);
        if (lanNay !== lanTaiTongHopTroLy || currentShopId !== shopId) return;
        khung.hidden = false;
        if (!summary.total) {
            noiDung.innerText = `${t('seller.assistant.quality_empty')} ${t('seller.assistant.quality_ai_stays_off')}`;
            return;
        }
        const lines = [t('seller.assistant.quality_result', {
            helpful: summary.helpful,
            total: summary.total,
            percent: summary.helpful_percent
        })];
        if (summary.total < summary.gate.minimum_samples) {
            lines.push(t('seller.assistant.quality_need_more', {
                count: summary.gate.minimum_samples - summary.total
            }));
        } else {
            lines.push(t(summary.gate.ready_for_review
                ? 'seller.assistant.quality_ready'
                : 'seller.assistant.quality_below'));
        }
        const topReason = Object.entries(summary.reasons || {}).sort((a, b) => b[1] - a[1])[0];
        if (topReason?.[1] > 0 && LY_DO_DANH_GIA_TRO_LY[topReason[0]]) {
            lines.push(t('seller.assistant.quality_top_reason', {
                reason: t(LY_DO_DANH_GIA_TRO_LY[topReason[0]]),
                count: topReason[1]
            }));
        }
        lines.push(t('seller.assistant.quality_ai_stays_off'));
        noiDung.innerText = lines.join(' ');
    } catch (error) {
        if (lanNay === lanTaiTongHopTroLy) khung.hidden = true;
    }
}

async function guiCauHoi(bienCo) {
    if (bienCo) bienCo.preventDefault();
    const o = document.getElementById('assistantInput');
    const cau = (o?.value || '').trim();
    if (!cau || !currentShopId) return;
    // Chặn bấm hai lần: câu hỏi thứ hai gửi đè lên câu thứ nhất sẽ làm hai câu
    // trả lời về lộn xộn, và người dùng không biết cái nào trả lời cái nào.
    if (troLyDangHoi) return;

    const huongDan = nhanYeuCauHuongDanTroLy(cau);
    if (huongDan) {
        themBongChat('nguoi', cau);
        o.value = '';
        cauHoiTroLyGanNhat = null;
        const bong = themBongChat(
            'may',
            t(`seller.assistant.guide.${huongDan.key}.title`)
        );
        ganHuongDanTroLy(bong, huongDan);
        capNhatNutHoiTroLy();
        if (window.innerWidth > 640) o?.focus();
        return;
    }

    const dichDen = nhanLenhDieuHuongTroLy(cau);
    if (dichDen) {
        themBongChat('nguoi', cau);
        o.value = '';
        cauHoiTroLyGanNhat = null;
        const tenDich = t(dichDen.labelKey);
        const bong = themBongChat(
            'may',
            t('seller.assistant.navigation_ready', { name: tenDich })
        );
        ganDieuHuongTroLy(bong, dichDen);
        capNhatNutHoiTroLy();
        if (window.innerWidth > 640) o?.focus();
        return;
    }

    const shopId = currentShopId;
    troLyDangHoi = true;
    cauHoiTroLyGanNhat = { cau, shopId };
    capNhatNutHoiTroLy();
    themBongChat('nguoi', cau);
    o.value = '';
    datTrangThaiTroLy('seller.assistant.thinking', true);

    try {
        if (!navigator.onLine) {
            const offline = new Error(t('seller.assistant.offline'));
            offline.isOffline = true;
            throw offline;
        }
        const d = await apiCall(`/assistant/${shopId}`, 'POST', { cau_hoi: cau });
        // Đổi cửa hàng giữa chừng thì câu trả lời cũ không được hiện lên nữa:
        // nó là số của cửa hàng khác.
        if (currentShopId !== shopId) return;
        // Nói rõ khi câu hỏi vừa được gửi ra Google. Người dùng có quyền biết
        // câu nào rời khỏi máy chủ, và đó cũng là cách họ thấy hạn mức tiêu đi đâu.
        let phuChu = d.nguon ? t('seller.assistant.source', { name: d.nguon }) : '';
        if (d.dung_ai) {
            phuChu += (phuChu ? ' · ' : '') + t('seller.assistant.via_ai');
        }
        const bongTraLoi = themBongChat('may', d.tra_loi, phuChu);
        ganHanhDongTroLy(bongTraLoi, d.y_dinh, d.nguon);
        ganDanhGiaTroLy(bongTraLoi, d.feedback_token, shopId);
        if (docTraLoiDangBat()) {
            // Cắt bớt cho vừa giới hạn của server đọc hộ (TTS_MAX_CHARS = 300).
            window.DocTien?.noi?.(docDuocCauTraLoi(d.tra_loi).slice(0, 280));
        }
        if (d.goi_y && d.goi_y.length) veGoiY(d.goi_y);
        else veGoiY();
    } catch (e) {
        const loiMang = e?.isOffline || !navigator.onLine
            || e?.message === t('common.network_error')
            || /failed to fetch|networkerror|load failed/i.test(e?.message || '');
        const bongLoi = themBongChat(
            'may',
            loiMang ? t('seller.assistant.offline') : (e.message || t('seller.assistant.error'))
        );
        const coTheThuLai = loiMang || !Number.isInteger(e?.status) || e.status >= 500;
        if (coTheThuLai) ganThuLaiTroLy(bongLoi);
    } finally {
        troLyDangHoi = false;
        datTrangThaiTroLy();
        capNhatNutHoiTroLy();
        // Trên điện thoại, focus lại sẽ bật bàn phím và che mất câu trả lời.
        // Desktop vẫn giữ focus để chủ shop hỏi tiếp bằng bàn phím cho nhanh.
        if (window.innerWidth > 640) o?.focus();
    }
}

document.getElementById('assistantInput')?.addEventListener('input', capNhatNutHoiTroLy);

// ===== L2: xả hàng tồn =====

let xaHangRequestId = 0;
let xaHangCache = null;

const NHAN_LY_DO_XA_HANG = {
    CA_HAI:       { mau: '#B91C1C', khoa: 'seller.clearance.reason_both' },
    SAP_HET_HAN:  { mau: '#B45309', khoa: 'seller.clearance.reason_expiring' },
    NAM_E:        { mau: '#7C3AED', khoa: 'seller.clearance.reason_idle' }
};

async function loadXaHangTon() {
    const shopId = currentShopId;
    if (!shopId || !XEM_DUOC_GIA_VON) return;
    const soNgay = Number(document.getElementById('clearanceIdleDays')?.value) || 45;
    const requestId = ++xaHangRequestId;
    const generation = currentShopGeneration;
    try {
        const d = await apiCall(`/clearance/${shopId}?so_ngay_coi_la_e=${soNgay}`);
        if (requestId !== xaHangRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId) return;
        xaHangCache = { shopId, data: d };
        veXaHangTon(d);
    } catch (e) {
        if (requestId === xaHangRequestId && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function veXaHangTon(d) {
    const danhSach = d.danh_sach || [];

    document.getElementById('clearanceCapital').innerText =
        dinhDangTienDoiSoat(Number(d.tong_von_dang_dong || 0));
    const soThieu = Number(d.so_mat_hang_chua_khai_gia_von || 0);
    document.getElementById('clearanceItemCount').innerText = soThieu
        ? t('seller.clearance.count_with_missing', {
            count: dinhDangSoSeller(d.so_mat_hang), missing: dinhDangSoSeller(soThieu) })
        : t('seller.clearance.count', { count: dinhDangSoSeller(d.so_mat_hang) });

    // Hàng đã hỏng chỉ hiện khi thực sự có: một ô báo "0 phải hủy" lúc nào cũng
    // nằm đó là thứ người ta thôi không đọc nữa.
    const canHuy = Number(d.so_luong_can_huy || 0);
    const oHuy = document.getElementById('clearanceWriteOffCard');
    if (oHuy) {
        oHuy.style.display = canHuy > 0 ? '' : 'none';
        document.getElementById('clearanceToDestroy').innerText = dinhDangSoSeller(canHuy);
    }

    const badge = document.getElementById('clearanceBadge');
    if (badge) {
        badge.innerText = danhSach.length > 99 ? '99+' : String(danhSach.length);
        badge.style.display = danhSach.length > 0 ? 'inline-block' : 'none';
    }

    const tbody = document.getElementById('clearanceList');
    tbody.innerHTML = '';
    if (!danhSach.length) {
        tbody.innerHTML = `<tr><td colspan="8" style="color: var(--text-muted);">`
            + `${escapeHtml(t('seller.clearance.empty'))}</td></tr>`;
        return;
    }

    danhSach.forEach(r => {
        const kieu = NHAN_LY_DO_XA_HANG[r.ly_do];
        let viSao = kieu
            ? `<span style="color:${kieu.mau}; font-weight:700; white-space:nowrap;">`
              + `${escapeHtml(t(kieu.khoa))}</span>`
            : escapeHtml(r.ly_do || '');
        if (r.so_ngay_con_han !== null && r.so_ngay_con_han !== undefined) {
            viSao += `<br><small style="color:#64748B;">`
                + `${escapeHtml(t('seller.clearance.days_left', { days: r.so_ngay_con_han }))}</small>`;
        } else if (r.so_ngay_khong_ban !== null && r.so_ngay_khong_ban !== undefined) {
            viSao += `<br><small style="color:#64748B;">`
                + `${escapeHtml(t('seller.clearance.idle_for', { days: r.so_ngay_khong_ban }))}</small>`;
        } else {
            viSao += `<br><small style="color:#64748B;">`
                + `${escapeHtml(t('seller.clearance.never_sold'))}</small>`;
        }

        // Không tính được giá thì nói RÕ vì sao, và tuyệt đối không hiện một
        // con số nào ở cột giá - số ở đó sẽ bị đọc là lời khuyên.
        let deXuat, conLai, nut;
        if (r.gia_de_xuat === null || r.gia_de_xuat === undefined) {
            const khoa = r.khong_tinh_duoc === 'DANG_BAN_KHONG_LAI'
                ? 'seller.clearance.no_margin'
                : 'seller.clearance.no_cost';
            deXuat = `<small style="color:#B45309;">${escapeHtml(t(khoa))}</small>`;
            conLai = '--';
            nut = '';
        } else {
            deXuat = `<strong style="color:#15803D;">${escapeHtml(dinhDangTienDoiSoat(r.gia_de_xuat))}</strong>`
                + `<br><small style="color:#64748B;">`
                + `${escapeHtml(t('seller.clearance.down_by', { percent: r.giam_phan_tram }))}</small>`;
            conLai = escapeHtml(dinhDangTienDoiSoat(r.lai_moi_cai_sau_giam));
            nut = `<button class="btn-outline" style="padding:0.3rem 0.7rem; white-space:nowrap;"`
                + ` onclick="apDungGiaDeXuat(${Number(r.product_id)}, ${Number(r.gia_de_xuat)})">`
                + `<i class="ph ph-tag"></i> ${escapeHtml(t('seller.clearance.apply'))}</button>`;
        }

        const ghiChuHuy = r.so_luong_da_het_han > 0
            ? `<br><small style="color:#B91C1C;">`
              + `${escapeHtml(t('seller.clearance.expired_qty', { qty: dinhDangSoSeller(r.so_luong_da_het_han) }))}</small>`
            : '';

        tbody.innerHTML += `<tr>
            <td>${escapeHtml(r.ten || '')}</td>
            <td>${viSao}</td>
            <td>${escapeHtml(dinhDangSoSeller(r.ton_kho))}${ghiChuHuy}</td>
            <td>${r.von_dang_dong === null || r.von_dang_dong === undefined
                    ? '--' : escapeHtml(dinhDangTienDoiSoat(r.von_dang_dong))}</td>
            <td>${escapeHtml(dinhDangTienDoiSoat(r.gia_hien_tai))}</td>
            <td>${deXuat}</td>
            <td>${conLai}</td>
            <td>${nut}</td>
        </tr>`;
    });
}

/** Mở form sửa sản phẩm với giá ĐÃ ĐIỀN SẴN mức đề xuất.
 *
 *  CỐ Ý không tự lưu. Đổi giá bán là quyết định của chủ shop, và một cú bấm
 *  nhầm ở đây là hạ giá cả lô hàng mà không ai hay. Máy chỉ điền hộ con số.
 */
function apDungGiaDeXuat(productId, giaMoi) {
    switchWarehouseSubTab('products');
    editProduct(productId);
    const oGia = document.getElementById('prodPrice');
    if (!oGia) return;
    oGia.value = giaMoi;
    oGia.focus();
    oGia.scrollIntoView({ behavior: 'smooth', block: 'center' });
    showToast(t('seller.clearance.filled', { price: dinhDangTienDoiSoat(giaMoi) }));
}

// ===== L1: dự báo nhập hàng =====

let duBaoRequestId = 0;
// Giữ lại kết quả vừa vẽ để đổi ngôn ngữ không phải gọi lại server. Có kèm
// shopId vì đổi shop xong mới đổi ngôn ngữ là vẽ số của shop cũ lên màn shop
// mới - lỗi im lặng, nhìn vẫn ra một bảng đầy đủ.
let duBaoCache = null;

/** Màu và nhãn của từng tình trạng. Trạng thái lạ (backend thêm sau) vẫn hiện
 *  được chuỗi thô chứ không làm vỡ bảng. */
const NHAN_DU_BAO = {
    HET_HANG:  { mau: '#B91C1C', khoa: 'seller.forecast.state_out' },
    NGUY_CAP:  { mau: '#B45309', khoa: 'seller.forecast.state_critical' },
    CAN_NHAP:  { mau: '#0369A1', khoa: 'seller.forecast.state_reorder' },
    ON_DINH:   { mau: '#15803D', khoa: 'seller.forecast.state_ok' },
    KHONG_BAN: { mau: '#64748B', khoa: 'seller.forecast.state_idle' }
};

async function loadDuBaoNhapHang() {
    const shopId = currentShopId;
    if (!shopId) return;
    const lead = Number(document.getElementById('forecastLeadTime')?.value) || 3;
    const dich = Number(document.getElementById('forecastTargetDays')?.value) || 7;
    const requestId = ++duBaoRequestId;
    const generation = currentShopGeneration;
    try {
        const d = await apiCall(
            `/forecast/${shopId}?thoi_gian_dat_hang=${lead}&muon_du_cho=${dich}`
        );
        // Đổi shop giữa chừng thì kết quả cũ không được ghi đè lên màn mới.
        if (requestId !== duBaoRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId) return;
        duBaoCache = { shopId, data: d };
        veDuBaoNhapHang(d);
    } catch (e) {
        if (requestId === duBaoRequestId && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function veDuBaoNhapHang(d) {
    const danhSach = d.danh_sach || [];
    const soCanNhap = Number(d.so_mat_hang_can_nhap || 0);

    document.getElementById('forecastNeedCount').innerText = dinhDangSoSeller(soCanNhap);
    document.getElementById('forecastPeriod').innerText =
        t('seller.forecast.period', { from: d.tu_ngay || '', to: d.den_ngay || '' });

    // Ô tiền chỉ dựng khi server thực sự gửi số. Không có quyền xem giá vốn thì
    // các khóa đó KHÔNG có mặt - hiện 0 ở đây là nói dối chứ không phải giấu.
    const oTien = document.getElementById('forecastMoneyCard');
    if (oTien) {
        oTien.style.display = d.xem_duoc_gia_von ? '' : 'none';
        if (d.xem_duoc_gia_von) {
            document.getElementById('forecastTotalMoney').innerText =
                dinhDangTienDoiSoat(Number(d.tong_tien_can_nhap || 0));
            const thieu = Number(d.so_mat_hang_chua_khai_gia_von || 0);
            document.getElementById('forecastMissingCost').innerText = thieu
                ? t('seller.forecast.missing_cost', { count: dinhDangSoSeller(thieu) })
                : '';
        }
    }

    const badge = document.getElementById('forecastBadge');
    if (badge) {
        badge.innerText = soCanNhap > 99 ? '99+' : String(soCanNhap);
        badge.style.display = soCanNhap > 0 ? 'inline-block' : 'none';
    }

    const tbody = document.getElementById('forecastList');
    tbody.innerHTML = '';
    if (!danhSach.length) {
        tbody.innerHTML = `<tr><td colspan="8" style="color: var(--text-muted);">`
            + `${escapeHtml(t('seller.forecast.empty'))}</td></tr>`;
        return;
    }

    danhSach.forEach(r => {
        const kieu = NHAN_DU_BAO[r.trang_thai];
        const nhan = kieu
            ? `<span style="color:${kieu.mau}; font-weight:700; white-space:nowrap;">`
              + `${escapeHtml(t(kieu.khoa))}</span>`
            : escapeHtml(r.trang_thai || '');

        // "Đủ bán mấy ngày nữa" là con số chủ shop nhìn để quyết định, nên nó
        // phải đọc ra được ngay: null nghĩa là không bán được món nào trong kỳ,
        // chia cho 0 không ra ngày nào cả.
        const conLai = (r.con_ban_duoc_ngay === null || r.con_ban_duoc_ngay === undefined)
            ? '--'
            : t('seller.forecast.days_value', { days: r.con_ban_duoc_ngay });

        const nhapVe = r.can_nhap > 0
            ? `<strong>${escapeHtml(dinhDangSoSeller(r.can_nhap))}</strong>`
            : '--';

        // Hàng theo lô: tồn khả dụng đã loại phần hết hạn, nên nói rõ tổng tồn
        // để người xem không tưởng hệ thống đếm thiếu hàng của họ.
        const ghiChuTon = (r.theo_lo && r.ton_tong !== r.ton_kho)
            ? `<br><small style="color:#B45309;">`
              + `${escapeHtml(t('seller.forecast.expired_note', { total: dinhDangSoSeller(r.ton_tong) }))}</small>`
            : '';
        const ghiChuDangVe = Number(r.dang_ve) > 0
            ? `<br><small style="color:#0369A1;">${escapeHtml(t('seller.forecast.incoming_note', { quantity: dinhDangSoSeller(r.dang_ve) }))}</small>`
            : '';

        const canhBaoDuLieu = r.du_lieu_yeu
            ? ` <span title="${escapeHtml(t('seller.forecast.weak_data_title'))}" `
              + `style="color:#B45309; cursor:help;">*</span>`
            : '';

        const ncc = r.nha_cung_cap
            ? escapeHtml(r.nha_cung_cap.ten || '')
              + (r.nha_cung_cap.dien_thoai
                  ? `<br><small style="color:#64748B;">${escapeHtml(r.nha_cung_cap.dien_thoai)}</small>`
                  : '')
            : `<small style="color:#64748B;">${escapeHtml(t('seller.forecast.no_supplier'))}</small>`;

        // Chọn NCC bằng chính dòng Forecast. Nút chỉ điền đơn đặt hàng;
        // người dùng vẫn phải tự lưu nháp rồi tự xác nhận đã đặt.
        const coTheLapPhieu = MY_ROLE === 'SELLER'
            && r.can_nhap > 0
            && r.nha_cung_cap
            && Number.isInteger(Number(r.nha_cung_cap.id));
        const lapPhieu = coTheLapPhieu
            ? `<button class="btn-outline" type="button" `
              + `onclick="lapPhieuNhapTuDuBao(${Number(r.nha_cung_cap.id)})">`
              + `<i class="ph ph-note-pencil"></i> `
              + `${escapeHtml(t('seller.forecast.purchase_button'))}</button>`
            : '—';

        tbody.innerHTML += `<tr>
            <td>${escapeHtml(r.ten || '')}</td>
            <td>${nhan}</td>
            <td>${escapeHtml(dinhDangSoSeller(r.ton_kho))}${ghiChuTon}${ghiChuDangVe}</td>
            <td>${escapeHtml(String(r.ban_moi_ngay))}${canhBaoDuLieu}</td>
            <td style="white-space:nowrap;">${escapeHtml(conLai)}</td>
            <td>${nhapVe}</td>
            <td>${ncc}</td>
            <td>${lapPhieu}</td>
        </tr>`;
    });
}

async function lapPhieuNhapTuDuBao(supplierId) {
    if (MY_ROLE !== 'SELLER') {
        showToast(t('seller.purchasing.owner_only'));
        return;
    }
    if (!duBaoCache || duBaoCache.shopId !== currentShopId) {
        showToast(t('seller.purchasing.forecast_unavailable'));
        return;
    }
    const idNcc = Number(supplierId);
    const items = (duBaoCache.data?.danh_sach || [])
        .filter(r => Number(r.nha_cung_cap?.id) === idNcc && Number(r.can_nhap) > 0)
        .map(r => ({
            product_id: Number(r.product_id),
            quantity: Number(r.can_nhap)
        }));
    if (!Number.isInteger(idNcc) || idNcc <= 0 || !items.length) {
        showToast(t('seller.purchasing.forecast_unavailable'));
        return;
    }
    await window.FSellingPurchasing?.prefillPurchaseOrderFromForecast?.({
        shopId: currentShopId,
        generation: currentShopGeneration,
        supplierId: idNcc,
        leadDays: Number(duBaoCache.data?.thoi_gian_dat_hang) || 0,
        items
    });
}

// ===== F5: hàng sắp hết hạn và đã hết hạn =====

let hanSuDungRequestId = 0;
// Giữ kết quả vừa vẽ để đổi ngôn ngữ không phải gọi lại server. Kèm shopId vì
// đổi shop xong mới đổi ngôn ngữ là vẽ số của shop cũ lên màn shop mới.
let hanSuDungCache = null;

async function loadHanSuDung() {
    const shopId = currentShopId;
    if (!shopId) return;
    const soNgay = Number(document.getElementById('expiryDays')?.value) || 30;
    const requestId = ++hanSuDungRequestId;
    const generation = currentShopGeneration;
    try {
        const d = await apiCall(`/products/${shopId}/batches?days=${soNgay}`);
        // Đổi shop giữa chừng thì kết quả cũ không được ghi đè lên màn mới.
        if (requestId !== hanSuDungRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId) return;
        hanSuDungCache = { shopId, data: d };
        veHanSuDung(d);
    } catch (e) {
        if (requestId === hanSuDungRequestId && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function veHanSuDung(d) {
    const soHetHan = Number(d.expired_quantity || 0);
    const soSapHet = Number(d.expiring_soon_quantity || 0);
    document.getElementById('expiredQty').innerText = dinhDangSoSeller(soHetHan);
    document.getElementById('soonQty').innerText = dinhDangSoSeller(soSapHet);

    // Giá trị tồn chỉ có mặt khi người gọi được xem giá vốn; không có thì để
    // trống chứ không hiện 0 - 0 ở đây đọc ra là "hàng không đáng tiền nào".
    const veTien = (id, gia_tri) => {
        const el = document.getElementById(id);
        el.innerText = (gia_tri === undefined || gia_tri === null)
            ? ''
            : t('seller.expiry.value', { amount: dinhDangTienDoiSoat(gia_tri) });
    };
    veTien('expiredValue', d.expired_value);
    veTien('soonValue', d.expiring_soon_value);

    // Nút hủy chỉ hiện cho chủ shop VÀ chỉ khi thực sự có hàng hết hạn. Đây
    // CHỈ là lớp giao diện cho gọn mắt - server mới là chỗ chặn thật
    // (require_cost_visibility trong write_off_service).
    const nutHuy = document.getElementById('btnHuyHetHan');
    if (nutHuy) {
        nutHuy.style.display =
            (XEM_DUOC_GIA_VON && soHetHan > 0) ? 'inline-flex' : 'none';
    }

    // Badge trên tab con: chỉ đếm hàng ĐÃ hỏng, đó mới là thứ phải xử lý ngay.
    const badge = document.getElementById('expiryBadge');
    if (badge) {
        badge.innerText = soHetHan > 99 ? '99+' : String(soHetHan);
        badge.style.display = soHetHan > 0 ? 'inline-block' : 'none';
    }

    const tbody = document.getElementById('expiryList');
    tbody.innerHTML = '';
    const dong = [
        ...(d.expired || []).map(r => ({ ...r, hong: true })),
        ...(d.expiring_soon || []).map(r => ({ ...r, hong: false }))
    ];
    if (!dong.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="color: var(--text-muted);">`
            + `${escapeHtml(t('seller.expiry.empty'))}</td></tr>`;
        return;
    }
    dong.forEach(r => {
        const nhan = r.hong
            ? `<span style="color:#B91C1C; font-weight:700;">${escapeHtml(t('seller.expiry.expired'))}</span>`
            : `<span style="color:#B45309; font-weight:600;">${escapeHtml(t('seller.expiry.soon'))}</span>`;
        tbody.innerHTML += `<tr>
            <td>${escapeHtml(r.product_name || '')}</td>
            <td style="white-space:nowrap;">${escapeHtml(r.expiry_date || '')}</td>
            <td>${escapeHtml(dinhDangSoSeller(r.quantity))}</td>
            <td>${nhan}</td>
        </tr>`;
    });
}

// ===== F6: lịch sử phiếu hủy =====

let phieuHuyRequestId = 0;

/** Nhãn tiếng Việt của lý do hủy. Lý do lạ (backend thêm sau này) vẫn hiện được
 *  chuỗi thô chứ không làm vỡ bảng. */
function nhanLyDoHuy(reason) {
    const nhan = {
        EXPIRED: t('seller.writeoff.reason_expired'),
        DAMAGED: t('seller.writeoff.reason_damaged'),
        LOST: t('seller.writeoff.reason_lost')
    };
    return nhan[reason] || reason || '--';
}

async function loadPhieuHuy() {
    const shopId = currentShopId;
    if (!shopId || !XEM_DUOC_GIA_VON) return;
    const requestId = ++phieuHuyRequestId;
    const generation = currentShopGeneration;
    try {
        const d = await apiCall(`/products/${shopId}/write-offs`);
        if (requestId !== phieuHuyRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId) return;
        vePhieuHuy(d.write_offs || []);
    } catch (e) {
        if (requestId === phieuHuyRequestId && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function vePhieuHuy(phieu) {
    const tbody = document.getElementById('woList');
    const trong = document.getElementById('woEmpty');
    if (!tbody || !trong) return;

    // `total_cost` NULL = phiếu đó còn dòng chưa khai giá vốn. Cộng nó như 0 là
    // báo tổng lỗ THẤP hơn thực tế, nên đếm riêng và nói ra (bẫy 13 và 24).
    const tongSoLuong = phieu.reduce((s, p) => s + (Number(p.total_quantity) || 0), 0);
    const coGiaVon = phieu.filter(p => p.total_cost !== null && p.total_cost !== undefined);
    const tongLo = coGiaVon.reduce((s, p) => s + Number(p.total_cost), 0);
    const soThieuGiaVon = phieu.length - coGiaVon.length;

    document.getElementById('woTongSoLuong').innerText = dinhDangSoSeller(tongSoLuong);
    document.getElementById('woTongLo').innerText = dinhDangTienDoiSoat(tongLo);
    const ghiChuThieu = document.getElementById('woThieuGiaVon');
    ghiChuThieu.innerText = soThieuGiaVon
        ? t('seller.writeoff.missing_cost_note', {
            count: soThieuGiaVon,
            formattedCount: dinhDangSoSeller(soThieuGiaVon)
        })
        : '';

    tbody.innerHTML = '';
    if (!phieu.length) {
        trong.style.display = 'block';
        trong.innerHTML = `<i class="ph ph-check-circle" style="display:block; margin-bottom:0.5rem; color:var(--success); font-size:2.2rem;"></i>`
            + escapeHtml(t('seller.writeoff.empty'));
        return;
    }
    trong.style.display = 'none';

    phieu.forEach(p => {
        // Liệt kê tối đa 3 dòng rồi tóm tắt phần còn lại: một phiếu hủy cuối
        // tháng có thể có mấy chục lô, đổ hết ra thì bảng không đọc được.
        const dong = p.items || [];
        const moTa = dong.slice(0, 3).map(d => {
            const han = d.expiry_date
                ? ` <span style="color:#94A3B8;">(${escapeHtml(t('seller.stocktake.expiry', { date: d.expiry_date }))})</span>`
                : '';
            return `${escapeHtml(d.product_name || '--')} ×${dinhDangSoSeller(d.quantity)}${han}`;
        }).join('<br>');
        const conLai = dong.length > 3
            ? `<br><span style="color:#64748B; font-size:0.8rem;">`
              + escapeHtml(t('seller.writeoff.more_items', {
                  count: dong.length - 3,
                  formattedCount: dinhDangSoSeller(dong.length - 3)
              })) + '</span>'
            : '';
        const giaTri = (p.total_cost === null || p.total_cost === undefined)
            ? `<span style="color:#B45309;">${escapeHtml(t('seller.writeoff.cost_unknown'))}</span>`
            : dinhDangTienDoiSoat(p.total_cost);
        const ghiChu = p.note
            ? `<br><span style="color:#64748B; font-size:0.8rem;">${escapeHtml(p.note)}</span>`
            : '';

        tbody.innerHTML += `<tr>
            <td style="white-space:nowrap;">${escapeHtml(dinhDangNgayGio(p.created_at))}</td>
            <td>${escapeHtml(nhanLyDoHuy(p.reason))}${ghiChu}</td>
            <td>${moTa}${conLai}</td>
            <td>${dinhDangSoSeller(p.total_quantity)}</td>
            <td style="color:#B91C1C; font-weight:600;">${giaTri}</td>
            <td>${escapeHtml(p.created_by || '--')}</td>
        </tr>`;
    });
}

// ===== F6: phiếu hủy hàng hết hạn =====
//
// Hàng hết hạn trước F6 chỉ có đường ra là xuất kho, mà đường đó không ghi lý do
// và không chốt giá vốn - số hàng đó biến mất khỏi báo cáo và lãi bị thổi lên
// đúng bằng phần vốn đã mất. Phiếu hủy ghi nhận khoản lỗ đó.

// Một mã cho đúng một lần bấm. Bấm hai lần là TRỪ KHO HAI LẦN, nên giữ nguyên
// mã khi người dùng bấm lại sau lỗi mạng - server trả về chính phiếu cũ.
let maThaoTacHuyHang = null;
let deXuatHuyHang = null;

async function moPhieuHuyHetHan() {
    const shopId = currentShopId;
    if (!shopId) return;
    const generation = currentShopGeneration;
    let d;
    try {
        d = await apiCall(`/products/${shopId}/write-off/expired`);
    } catch (e) {
        return showToast(e.message);
    }
    if (generation !== currentShopGeneration || currentShopId !== shopId) return;

    if (!d.items.length) {
        return showToast(t('seller.writeoff.nothing_expired'));
    }
    deXuatHuyHang = d;
    maThaoTacHuyHang = `wo-${shopId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    // Liệt kê tối đa 8 dòng: hộp xác nhận cuộn dài thì người ta bấm cho xong
    // chứ không đọc, mà đây là thao tác KHÔNG có đường lùi.
    const dong = d.items.slice(0, 8)
        .map(r => `• ${r.product_name} — HSD ${r.expiry_date} — ${dinhDangSoSeller(r.quantity)}`)
        .join('\n');
    const con_lai = d.items.length > 8
        ? '\n' + t('seller.writeoff.more_lines', {
            count: dinhDangSoSeller(d.items.length - 8)
        })
        : '';
    // Giá trị NULL nghĩa là có lô chưa khai giá vốn - nói ra chứ đừng hiện 0.
    const tien = d.total_cost === null || d.total_cost === undefined
        ? t('seller.writeoff.cost_unknown')
        : dinhDangTienDoiSoat(d.total_cost);

    showCustomConfirm(
        t('seller.writeoff.confirm_title', {
            count: dinhDangSoSeller(d.items.length)
        }),
        t('seller.writeoff.confirm_message', {
            quantity: dinhDangSoSeller(d.total_quantity),
            cost: tien
        }) + '\n\n' + dong + con_lai,
        () => guiPhieuHuy(shopId, generation),
        t('seller.writeoff.confirm_button')
    );
}

async function guiPhieuHuy(shopId, generation) {
    if (!deXuatHuyHang || generation !== currentShopGeneration
        || currentShopId !== shopId) return;
    const items = deXuatHuyHang.items.map(r => ({
        product_id: r.product_id,
        batch_id: r.batch_id,
        quantity: r.quantity
    }));
    try {
        const res = await apiCall(`/products/${shopId}/write-off`, 'POST', {
            reason: 'EXPIRED',
            items,
            operation_id: maThaoTacHuyHang
        });
        if (generation !== currentShopGeneration || currentShopId !== shopId) return;
        showToast(t(
            res.repeated ? 'seller.writeoff.already_done' : 'seller.writeoff.done',
            { quantity: dinhDangSoSeller(res.total_quantity) }
        ));
        deXuatHuyHang = null;
        maThaoTacHuyHang = null;
        loadHanSuDung();
        loadProducts();
        loadPhieuHuy();   // phiếu vừa ghi phải có mặt ngay ở tab lịch sử
    } catch (e) {
        // KHÔNG xóa `maThaoTacHuyHang`: bấm lại sau lỗi mạng phải dùng lại đúng
        // mã đó, nếu không lần trước đã ghi được mà lần này trừ kho thêm lần nữa.
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function displayCategoryName(name) {
    return name === 'Chưa phân loại'
        ? t('seller.first_run.default_category')
        : name;
}

function renderCategories(cats) {
    const sel = document.getElementById('catSelect');
    if (sel) {
        const previousCategory = sel.value;
        sel.innerHTML = '';
        const activeCats = cats.filter(c => c.is_active !== false);
        activeCats.forEach(c => sel.innerHTML += `<option value="${c.id}">${escapeHtml(displayCategoryName(c.name))}</option>`);
        if (activeCats.some(c => String(c.id) === previousCategory)) {
            sel.value = previousCategory;
        }
    }

    const filterSel = document.getElementById('filterCatSelect');
    if (filterSel) {
        const prevVal = filterSel.value;
        filterSel.innerHTML = `<option value="">${escapeHtml(t('seller.filters.all'))}</option>`;
        cats.forEach(c => {
            const suffix = c.is_active === false ? t('seller.filters.hidden_suffix') : '';
            filterSel.innerHTML += `<option value="${c.id}">${escapeHtml(displayCategoryName(c.name))}${suffix}</option>`;
        });
        if (cats.find(c => c.id == prevVal)) {
            filterSel.value = prevVal;
        }
    }

    renderCategoriesTable(cats);
}

async function loadCategories() {
    if(!currentShopId) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const requestId = ++categoriesRequestId;
    try {
        const cats = await apiCall(`/categories/${shopId}`);
        if (
            requestId !== categoriesRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId
        ) return;
        currentCategories = cats;
        currentCategoriesShopId = shopId;
        renderCategories(cats);
    } catch(e) {
        if (
            requestId === categoriesRequestId
            && generation === currentShopGeneration
            && currentShopId === shopId
        ) {
            console.error("Error loading categories:", e);
        }
    }
}

let editingCategoryId = null;

function renderCategoriesTable(cats) {
    const tbody = document.getElementById('categoryTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    cats.forEach(c => {
        const activeText = c.is_active !== false
            ? `<span style="color:var(--success); font-weight:600; font-size: 0.8rem;">${escapeHtml(t('seller.status.active'))}</span>`
            : `<span style="color:#ef4444; font-weight:600; font-size: 0.8rem;">${escapeHtml(t('seller.status.inactive'))}</span>`;
        const categoryId = Number(c.id);
        if (!Number.isInteger(categoryId)) return;
            
        tbody.innerHTML += `<tr>
            <td>${categoryId}</td>
            <td><strong>${escapeHtml(displayCategoryName(c.name))}</strong></td>
            <td>${activeText}</td>
            <td>
                <button type="button" class="btn-outline" data-category-edit-id="${categoryId}" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('seller.actions.edit'))}" aria-label="${escapeHtml(t('seller.actions.edit'))}"><i class="ph ph-pencil"></i></button>
            </td>
        </tr>`;
    });
    tbody.querySelectorAll('[data-category-edit-id]').forEach(button => {
        button.addEventListener('click', () => {
            editCategory(Number(button.dataset.categoryEditId));
        });
    });
}

function editCategory(id) {
    if (!damBaoCacheShopHienTai(currentCategoriesShopId)) return;
    const category = currentCategories.find(item => Number(item.id) === Number(id));
    if (!category) return;
    editingCategoryId = id;
    document.getElementById('editingCatId').value = id;
    document.getElementById('catNameInput').value = category.name;
    document.getElementById('catStatusSelect').value =
        category.is_active !== false ? 'active' : 'inactive';
    
    document.getElementById('catFormTitle').innerText = t('seller.categories.edit_title');
    document.getElementById('btnSaveCategory').innerHTML = `<i class="ph ph-check-circle"></i> <span data-seller-action-label="category-save">${escapeHtml(t('seller.categories.update'))}</span>`;
    document.getElementById('btnCancelEditCategory').style.display = 'block';
}

function cancelEditCategory() {
    editingCategoryId = null;
    document.getElementById('editingCatId').value = '';
    document.getElementById('catNameInput').value = '';
    document.getElementById('catStatusSelect').value = 'active';
    
    document.getElementById('catFormTitle').innerText = t('seller.categories.add_title');
    document.getElementById('btnSaveCategory').innerHTML = `<i class="ph ph-plus-circle"></i> <span data-seller-action-label="category-save">${escapeHtml(t('seller.categories.save'))}</span>`;
    document.getElementById('btnCancelEditCategory').style.display = 'none';
}

async function saveCategory() {
    if(!currentShopId) return;
    if (!damBaoCacheShopHienTai(currentCategoriesShopId)) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const categoryId = editingCategoryId;
    const isEditing = categoryId !== null;
    const name = document.getElementById('catNameInput').value.trim();
    const status = document.getElementById('catStatusSelect').value;
    
    if(!name) {
        return showToast(t('seller.categories.name_required_error'));
    }
    
    try {
        if(isEditing) {
            const body = {
                name: name,
                is_active: status === 'active'
            };
            await apiCall(`/categories/${categoryId}`, 'PUT', body);
        } else {
            await apiCall(`/categories?name=${encodeURIComponent(name)}&shop_id=${shopId}`, 'POST');
        }
        if (generation !== currentShopGeneration || currentShopId !== shopId) {
            return;
        }
        showToast(t(isEditing ? 'seller.categories.updated' : 'seller.categories.created'));
        cancelEditCategory();
        loadCategories();
        loadProducts();
    } catch(e) {
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

// ===== Nhập sản phẩm Excel/CSV: chủ shop, xem trước trước khi ghi =====
function taoProductImportOperationId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `import-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function capNhatQuyenImportSanPham() {
    const panel = document.getElementById('productImportPanel');
    if (!panel) return;
    panel.hidden = MY_ROLE !== 'SELLER';
    if (MY_ROLE !== 'SELLER') resetProductImportState();
}

function datTrangThaiImportSanPham(message = '', state = '') {
    const status = document.getElementById('productImportStatus');
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
}

function capNhatNutImportSanPham() {
    const previewButton = document.getElementById('productImportPreview');
    const commitButton = document.getElementById('productImportCommit');
    const undoButton = document.getElementById('productImportUndo');
    if (previewButton) previewButton.disabled = productImportBusy || !productImportFile;
    if (commitButton) {
        commitButton.disabled = productImportBusy
            || !productImportFile
            || !productImportPreviewData?.can_commit;
    }
    if (undoButton) {
        undoButton.hidden = !productImportLastOperationId;
        undoButton.disabled = productImportBusy;
    }
}

function resetProductImportState() {
    productImportFile = null;
    productImportPreviewData = null;
    productImportOperationId = null;
    productImportLastOperationId = null;
    productImportBusy = false;
    const input = document.getElementById('productImportFile');
    if (input) input.value = '';
    const label = document.getElementById('productImportFileLabel');
    if (label) label.textContent = t('seller.product_import.choose_file');
    const results = document.getElementById('productImportResults');
    if (results) {
        results.hidden = true;
        results.innerHTML = '';
    }
    datTrangThaiImportSanPham();
    capNhatNutImportSanPham();
}

function chonFileImportSanPham(file) {
    productImportFile = null;
    productImportPreviewData = null;
    productImportOperationId = null;
    productImportLastOperationId = null;
    const results = document.getElementById('productImportResults');
    if (results) {
        results.hidden = true;
        results.innerHTML = '';
    }
    const extension = String(file?.name || '').toLowerCase();
    if (!file || (!extension.endsWith('.xlsx') && !extension.endsWith('.csv'))
        || file.size <= 0 || file.size > 2 * 1024 * 1024) {
        const input = document.getElementById('productImportFile');
        if (input) input.value = '';
        const label = document.getElementById('productImportFileLabel');
        if (label) label.textContent = t('seller.product_import.choose_file');
        datTrangThaiImportSanPham(t('seller.product_import.invalid_file'), 'error');
        capNhatNutImportSanPham();
        return;
    }
    productImportFile = file;
    const label = document.getElementById('productImportFileLabel');
    if (label) label.textContent = file.name;
    datTrangThaiImportSanPham(
        t('seller.product_import.file_selected', { name: file.name }),
        'ready'
    );
    capNhatNutImportSanPham();
}

function nhanHanhDongImport(action) {
    return t(`seller.product_import.action_${String(action || '').toLowerCase()}`);
}

function renderProductImportPreview(data) {
    const results = document.getElementById('productImportResults');
    if (!results) return;
    const rows = Array.isArray(data?.rows) ? data.rows : [];
    if (!rows.length) {
        results.hidden = false;
        results.innerHTML = `<div class="product-import-empty">${escapeHtml(t('seller.product_import.no_rows'))}</div>`;
        return;
    }
    const visibleRows = rows.slice(0, 100);
    const stat = data.stats || {};
    const missingCategories = (data.missing_categories || []).join(', ');
    results.innerHTML = `
        <div class="product-import-summary">
            <div><strong>${escapeHtml(dinhDangSoSeller(data.total_rows || 0))}</strong><span>${escapeHtml(t('seller.product_import.summary_total'))}</span></div>
            <div data-kind="create"><strong>${escapeHtml(dinhDangSoSeller(stat.create || 0))}</strong><span>${escapeHtml(t('seller.product_import.summary_create'))}</span></div>
            <div data-kind="skip"><strong>${escapeHtml(dinhDangSoSeller(stat.skip || 0))}</strong><span>${escapeHtml(t('seller.product_import.summary_skip'))}</span></div>
            <div data-kind="error"><strong>${escapeHtml(dinhDangSoSeller(stat.error || 0))}</strong><span>${escapeHtml(t('seller.product_import.summary_error'))}</span></div>
        </div>
        ${missingCategories ? `<p class="product-import-categories"><i class="ph ph-folder-plus"></i> ${escapeHtml(t('seller.product_import.categories_to_create', { names: missingCategories }))}</p>` : ''}
        <div class="product-import-table-wrap">
            <table class="db-table product-import-table">
                <thead><tr>
                    <th>${escapeHtml(t('seller.product_import.row'))}</th>
                    <th>${escapeHtml(t('seller.product_import.name'))}</th>
                    <th>${escapeHtml(t('seller.product_import.action'))}</th>
                    <th>${escapeHtml(t('seller.product_import.reason'))}</th>
                </tr></thead>
                <tbody>${visibleRows.map(row => {
                    const action = String(row.action || '').toLowerCase();
                    const reason = (row.errors || []).join('; ')
                        || (action === 'skip' ? t('seller.product_import.existing_skip') : '—');
                    return `<tr>
                        <td>${escapeHtml(row.row_number)}</td>
                        <td>${escapeHtml(row.data?.name || '—')}</td>
                        <td><span class="product-import-badge" data-kind="${escapeHtml(action)}">${escapeHtml(nhanHanhDongImport(row.action))}</span></td>
                        <td>${escapeHtml(reason)}</td>
                    </tr>`;
                }).join('')}</tbody>
            </table>
        </div>
        ${rows.length > visibleRows.length ? `<p class="product-import-overflow">${escapeHtml(t('seller.product_import.more_rows', { count: rows.length - visibleRows.length }))}</p>` : ''}`;
    results.hidden = false;
}

async function xemTruocImportSanPham() {
    if (MY_ROLE !== 'SELLER' || !productImportFile || !currentShopId || productImportBusy) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    productImportBusy = true;
    productImportPreviewData = null;
    productImportOperationId = null;
    datTrangThaiImportSanPham(t('seller.product_import.previewing'), 'loading');
    capNhatNutImportSanPham();
    const form = new FormData();
    form.append('file', productImportFile, productImportFile.name);
    try {
        const preview = await apiCall(`/products/${shopId}/imports/preview`, 'POST', form);
        if (shopId !== currentShopId || generation !== currentShopGeneration) return;
        productImportPreviewData = preview;
        renderProductImportPreview(preview);
        datTrangThaiImportSanPham(
            preview.can_commit
                ? t('seller.product_import.preview_ready')
                : t('seller.product_import.commit_blocked'),
            preview.can_commit ? 'success' : 'error'
        );
    } catch (error) {
        if (shopId !== currentShopId || generation !== currentShopGeneration) return;
        datTrangThaiImportSanPham(
            `${error.message} ${t('seller.product_import.network_retry')}`,
            'error'
        );
    } finally {
        if (shopId === currentShopId && generation === currentShopGeneration) {
            productImportBusy = false;
            capNhatNutImportSanPham();
        }
    }
}

async function xacNhanImportSanPham() {
    if (MY_ROLE !== 'SELLER' || !productImportFile
        || !productImportPreviewData?.can_commit || !currentShopId || productImportBusy) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    productImportOperationId ||= taoProductImportOperationId();
    productImportBusy = true;
    datTrangThaiImportSanPham(t('seller.product_import.importing'), 'loading');
    capNhatNutImportSanPham();
    const form = new FormData();
    form.append('operation_id', productImportOperationId);
    form.append('file', productImportFile, productImportFile.name);
    try {
        const result = await apiCall(`/products/${shopId}/imports/commit`, 'POST', form);
        if (shopId !== currentShopId || generation !== currentShopGeneration) return;
        productImportLastOperationId = result.can_undo ? result.operation_id : null;
        productImportPreviewData = null;
        datTrangThaiImportSanPham(
            t('seller.product_import.success', {
                created: dinhDangSoSeller(result.created || 0),
                skipped: dinhDangSoSeller(result.skipped || 0)
            }),
            'success'
        );
        await Promise.all([loadCategories(), loadProducts()]);
    } catch (error) {
        if (shopId !== currentShopId || generation !== currentShopGeneration) return;
        datTrangThaiImportSanPham(
            `${error.message} ${t('seller.product_import.network_retry')}`,
            'error'
        );
    } finally {
        if (shopId === currentShopId && generation === currentShopGeneration) {
            productImportBusy = false;
            capNhatNutImportSanPham();
        }
    }
}

function hoanTacImportSanPham() {
    if (MY_ROLE !== 'SELLER' || !productImportLastOperationId || productImportBusy) return;
    showCustomConfirm(
        t('seller.product_import.undo_confirm_title'),
        t('seller.product_import.undo_confirm_text'),
        async () => {
            const shopId = currentShopId;
            const generation = currentShopGeneration;
            const operationId = productImportLastOperationId;
            productImportBusy = true;
            datTrangThaiImportSanPham(t('seller.product_import.undoing'), 'loading');
            capNhatNutImportSanPham();
            try {
                const result = await apiCall(
                    `/products/${shopId}/imports/${encodeURIComponent(operationId)}/undo`,
                    'POST'
                );
                if (shopId !== currentShopId || generation !== currentShopGeneration) return;
                productImportLastOperationId = null;
                datTrangThaiImportSanPham(
                    t('seller.product_import.undone', {
                        count: dinhDangSoSeller(result.hidden || 0)
                    }),
                    'success'
                );
                await loadProducts();
            } catch (error) {
                if (shopId !== currentShopId || generation !== currentShopGeneration) return;
                datTrangThaiImportSanPham(
                    `${error.message} ${t('seller.product_import.network_retry')}`,
                    'error'
                );
            } finally {
                if (shopId === currentShopId && generation === currentShopGeneration) {
                    productImportBusy = false;
                    capNhatNutImportSanPham();
                }
            }
        },
        t('seller.product_import.undo')
    );
}

let currentProducts = [];
let editingProductId = null;

async function loadProducts() {
    if(!currentShopId) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const requestId = ++productsRequestId;
    try {
        const products = await apiCall(`/products/${shopId}`);
        if (
            requestId !== productsRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId
        ) return;
        currentProducts = products;
        currentProductsShopId = shopId;
        await napGiaVon(shopId, generation, requestId);
        if (
            requestId !== productsRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId
        ) return;
        filterProducts();
        window.FSellingPurchasing?.productsUpdated?.();
    } catch (e) {
        if (
            requestId === productsRequestId
            && generation === currentShopGeneration
            && currentShopId === shopId
        ) showToast(e.message);
    }
}

/** Nạp giá vốn cho shop đang xem. Nhân viên không gọi, và lỗi thì bỏ qua im
 *  lặng: thiếu giá vốn không được phép làm hỏng cả bảng kho hàng. */
async function napGiaVon(shopId, generation, requestId) {
    if (!XEM_DUOC_GIA_VON) {
        giaVonTheoSanPham = {};
        soSanPhamChuaKhaiGiaVon = 0;
        return;
    }
    try {
        const res = await apiCall(`/products/${shopId}/costs`);
        if (
            requestId !== productsRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId
        ) return;
        const bang = {};
        (res.costs || []).forEach(d => { bang[d.product_id] = d.cost_price; });
        giaVonTheoSanPham = bang;
        soSanPhamChuaKhaiGiaVon = res.chua_khai || 0;
    } catch (e) {
        giaVonTheoSanPham = {};
        soSanPhamChuaKhaiGiaVon = 0;
    }
}

/** Giá vốn của một sản phẩm, hoặc null nếu chưa khai. `undefined` (chưa nạp
 *  xong) cũng quy về null - không được đoán là 0. */
function giaVonCua(productId) {
    const gia = giaVonTheoSanPham[productId];
    return (gia === undefined || gia === null) ? null : gia;
}

/** Dòng giá vốn nhỏ dưới ô giá bán trong bảng kho hàng.
 *
 *  Sản phẩm chưa khai được nêu rõ bằng chữ chứ không để trống: chỗ trống đọc ra
 *  là "giá vốn bằng 0", và chính những dòng này mới là thứ đang khiến báo cáo
 *  lãi thiếu đơn. */
function dongGiaVon(productId) {
    if (!XEM_DUOC_GIA_VON) return '';
    const gia = giaVonCua(productId);
    if (gia === null) {
        return `<br><span style="font-size:0.72rem; color:#B45309;">`
            + `${escapeHtml(t('seller.products.cost_missing'))}</span>`;
    }
    return `<br><span style="font-size:0.72rem; color:#64748B;">`
        + `${escapeHtml(t('seller.products.cost_short'))} `
        + `${escapeHtml(dinhDangTienDoiSoat(gia))}</span>`;
}

// Ô "Tên sản phẩm" mang hai nghĩa tùy theo có khai biến thể hay không, nên nhãn
// của nó phải nói ra nghĩa đang dùng. Không đổi nhãn thì người dùng gõ
// "Áo thun đỏ L" vào ô tên rồi lại gõ "Đỏ / L" vào ô biến thể, ra tên đầy đủ
// "Áo thun đỏ L - Đỏ / L" - sai mà không có gì báo.
function capNhatNhanTenSanPham() {
    const nhan = document.getElementById('prodNameLabel');
    if (!nhan) return;
    const coBienThe = Boolean(document.getElementById('prodVariant')?.value.trim());
    nhan.innerText = t(
        coBienThe ? 'seller.products.group_required' : 'seller.products.name_required'
    );
}

// Khóa gom nhóm của một dòng. Sản phẩm đơn lẻ tự đứng thành nhóm một mình nên
// đoạn sắp xếp bên dưới không cần rẽ nhánh.
function _khoaNhom(p) {
    return p.variant_group || p.name || '';
}

/** Sắp xếp để các biến thể cùng nhóm nằm liền nhau, và trong nhóm thì theo tên
 *  biến thể. Không gộp thành một dòng: mỗi biến thể vẫn cần đủ nút sửa, nhập
 *  xuất kho, bật tắt và xóa của riêng nó. */
function _sapXepTheoNhom(danhSach) {
    return [...danhSach].sort((a, b) => {
        const nhom = _khoaNhom(a).localeCompare(_khoaNhom(b), 'vi');
        if (nhom !== 0) return nhom;
        return (a.variant_name || '').localeCompare(b.variant_name || '', 'vi');
    });
}

function filterProducts() {
    const tbody = document.getElementById('prodList');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!cacheThuocShop(currentProductsShopId, currentShopId)) return;
    const filterCatId = document.getElementById('filterCatSelect').value;

    const filtered = filterCatId
        ? currentProducts.filter(p => p.category_id == filterCatId)
        : currentProducts;

    _sapXepTheoNhom(filtered).forEach(p => {
        const activeText = p.is_active
            ? `<span style="color:var(--success); font-weight:600; font-size: 0.8rem;">${escapeHtml(t('seller.status.active'))}</span>`
            : `<span style="color:#ef4444; font-weight:600; font-size: 0.8rem;">${escapeHtml(t('seller.status.inactive'))}</span>`;
        // Tên nhóm mờ ở trên, tên biến thể đậm ở dưới. Nhắc lại tên nhóm ở MỌI
        // dòng chứ không chỉ dòng đầu nhóm: bảng này cuộn được và lọc được theo
        // danh mục, nên dòng "Size 32" đứng một mình là chuyện thường - lúc đó
        // không đọc ra được nó là quần hay áo. Chữ mờ lặp lại vẫn đủ để mắt gom
        // nhóm, mà không dòng nào mất nghĩa.
        const oTen = p.variant_group
            ? `<div style="color:#64748B; font-size:0.8rem;">${escapeHtml(p.variant_group)}</div>`
              + `<b>${escapeHtml(p.variant_name)}</b>`
            : escapeHtml(p.name);
        const nutThemBienThe = p.variant_group
            ? `<button class="btn-outline" onclick="themBienTheCungNhom(${p.id})" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('seller.products.add_variant'))}" aria-label="${escapeHtml(t('seller.products.add_variant'))}"><i class="ph ph-copy"></i></button>`
            : '';
        tbody.innerHTML += `<tr>
            <td>${escapeHtml(p.code||'--')}${p.barcode ? `<br><span style="font-size:0.75rem; color:#64748B;" title="${escapeHtml(t('seller.products.barcode'))}"><i class="ph ph-barcode"></i> ${escapeHtml(p.barcode)}</span>` : ''}</td>
            <td>${oTen} <br>${activeText}</td>
            <td>${dinhDangTienDoiSoat(p.price)}${dongGiaVon(p.id)}</td>
            <td>${dinhDangSoSeller(p.stock)}</td>
            <td style="display:flex; justify-content: center; align-items: center; gap:0.5rem; height: 7rem; flex-wrap: wrap;">
                <button class="btn-outline" onclick="editProduct(${p.id})" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('common.edit'))}" aria-label="${escapeHtml(t('common.edit'))}"><i class="ph ph-pencil-simple"></i></button>
                ${nutThemBienThe}
                <button class="btn-outline" onclick="nhapXuatKho(${p.id})" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('seller.actions.stock_adjust'))}" aria-label="${escapeHtml(t('seller.actions.stock_adjust'))}"><i class="ph ph-stack"></i></button>
                <button class="btn-outline" onclick="toggleProductStatus(${p.id})" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('seller.actions.toggle'))}" aria-label="${escapeHtml(t('seller.actions.toggle'))}"><i class="ph ph-power"></i></button>
                <button class="btn-outline" onclick="deleteProduct(${p.id})" style="padding: 0.2rem 0.5rem; color:#ef4444;" title="${escapeHtml(t('common.delete'))}" aria-label="${escapeHtml(t('common.delete'))}"><i class="ph ph-trash"></i></button>
            </td>
        </tr>`;
    });
}

/** Điền sẵn form để thêm một biến thể nữa vào cùng nhóm.
 *
 *  Khai tám size áo mà phải gõ lại tên nhóm, giá, danh mục tám lần thì đến size
 *  thứ ba là bắt đầu có dòng lệch. CỐ Ý bỏ trống ô biến thể và ô mã vạch: đó là
 *  đúng hai thứ bắt buộc phải khác nhau giữa các biến thể. */
function themBienTheCungNhom(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const goc = currentProducts.find(p => p.id === id);
    if (!goc || !goc.variant_group) return;
    cancelEditProduct();
    document.getElementById('prodName').value = goc.variant_group;
    document.getElementById('prodPrice').value = goc.price;
    document.getElementById('catSelect').value = String(goc.category_id);
    const oLo = document.getElementById('prodTrackBatches');
    if (oLo) oLo.checked = Boolean(goc.track_batches);
    const oBienThe = document.getElementById('prodVariant');
    if (oBienThe) { oBienThe.value = ''; oBienThe.focus(); }
    capNhatNhanTenSanPham();
    showToast(t('seller.products.add_variant_ready', { group: goc.variant_group }));
}

// Khi SỬA sản phẩm, ô tồn kho bị khóa: thay đổi tồn kho đi qua nút Điều chỉnh
// kho (cộng trừ theo delta), tránh ghi đè làm mất hàng khi bán song song.
function _khoaOTonKho(khoa) {
    const el = document.getElementById('prodStock');
    if (!el) return;
    el.disabled = khoa;
    el.title = khoa ? t('seller.products.edit_stock_hint') : '';
    el.style.opacity = khoa ? '0.5' : '1';
}

function editProduct(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const product = currentProducts.find(p => p.id === id);
    if(!product) return;
    editingProductId = id;
    document.getElementById('prodCode').value = product.code || '';
    document.getElementById('prodBarcode').value = product.barcode || '';
    // Với biến thể, ô tên mang tên NHÓM chứ không phải tên đầy đủ: điền
    // "Áo thun - Đỏ / L" vào đây rồi bấm Lưu là server ghép thêm lần nữa thành
    // "Áo thun - Đỏ / L - Đỏ / L".
    document.getElementById('prodName').value = product.variant_group || product.name;
    const oBienThe = document.getElementById('prodVariant');
    if (oBienThe) oBienThe.value = product.variant_name || '';
    capNhatNhanTenSanPham();
    document.getElementById('prodPrice').value = product.price;
    const oGiaVon = document.getElementById('prodCost');
    if (oGiaVon) {
        // Chưa khai thì để TRỐNG chứ không điền 0. Điền 0 là ghi đè một con số
        // sai vào lúc người dùng chỉ định sửa cái tên.
        const giaVon = giaVonCua(product.id);
        oGiaVon.value = giaVon === null ? '' : giaVon;
    }
    document.getElementById('prodStock').value = product.stock;
    _khoaOTonKho(true);
    const oLo = document.getElementById('prodTrackBatches');
    if (oLo) {
        oLo.checked = Boolean(product.track_batches);
        oLo.disabled = true;   // xem mục 'chỉ gửi khi tạo mới' ở createProduct
    }
    document.getElementById('catSelect').value = String(product.category_id);
    document.getElementById('productFormTitle').innerText = t('seller.products.edit_title');
    document.getElementById('btnSaveProduct').innerHTML = `<i class="ph ph-floppy-disk"></i> <span data-seller-action-label="product-save">${escapeHtml(t('seller.products.update'))}</span>`;
    document.getElementById('btnCancelEditProduct').style.display = 'block';
}

function cancelEditProduct() {
    editingProductId = null;
    document.getElementById('prodCode').value = '';
    document.getElementById('prodBarcode').value = '';
    document.getElementById('prodName').value = '';
    const oBienTheMoi = document.getElementById('prodVariant');
    if (oBienTheMoi) oBienTheMoi.value = '';
    capNhatNhanTenSanPham();
    document.getElementById('prodPrice').value = '';
    const oGiaVon = document.getElementById('prodCost');
    if (oGiaVon) oGiaVon.value = '';
    const oLoMoi = document.getElementById('prodTrackBatches');
    if (oLoMoi) { oLoMoi.checked = false; oLoMoi.disabled = false; }
    document.getElementById('prodStock').value = '100';
    _khoaOTonKho(false);
    document.getElementById('prodImage').value = '';
    document.getElementById('productFormTitle').innerText = t('seller.products.add_title');
    document.getElementById('btnSaveProduct').innerHTML = `<i class="ph ph-plus"></i> <span data-seller-action-label="product-save">${escapeHtml(t('seller.products.save'))}</span>`;
    document.getElementById('btnCancelEditProduct').style.display = 'none';
}

async function nhapXuatKho(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const product = currentProducts.find(p => p.id === id);
    if(!product) return;
    // Hỏi hết trong MỘT hộp thay vì ba `prompt()` nối tiếp. Hạn và đơn giá chỉ
    // có nghĩa khi NHẬP kho, nhưng vẫn hiện sẵn kèm ghi chú: chưa gõ số lượng
    // thì chưa biết là nhập hay xuất, mà hỏi thêm một hộp nữa sau khi biết là
    // quay lại đúng cái vòng hộp-nối-hộp vừa bỏ.
    const giaVonHienTai = giaVonCua(id);
    const truong = [{
        ten: 'delta',
        nhan: t('seller.products.stock_delta_label'),
        goiY: t('seller.products.stock_delta_hint')
    }, {
        ten: 'lyDo',
        nhan: t('seller.products.stock_reason_label'),
        kieu: 'textarea',
        goiY: t('seller.products.stock_reason_placeholder'),
        ghiChu: t('seller.products.stock_reason_note')
    }];
    if (product.track_batches) {
        truong.push({
            ten: 'han',
            nhan: t('seller.products.expiry_label'),
            kieu: 'date',
            ghiChu: t('seller.products.expiry_note')
        });
    }
    if (XEM_DUOC_GIA_VON) {
        truong.push({
            ten: 'donGia',
            nhan: t('seller.products.unit_cost_label'),
            ghiChu: t('seller.products.unit_cost_note', {
                current: giaVonHienTai === null
                    ? t('seller.products.cost_missing')
                    : dinhDangTienDoiSoat(giaVonHienTai)
            })
        });
    }

    const kq = await hoiThongTin({
        tieuDe: t('seller.products.stock_title', { name: product.name }),
        moTa: t('seller.products.stock_desc', {
            stock: dinhDangSoSeller(product.stock)
        }),
        truong,
        nhanXacNhan: t('seller.products.stock_submit')
    });
    if (kq === null) return;

    const delta = parseInt(String(kq.delta ?? '').replace(/[.,\s]/g, ''), 10);
    if(isNaN(delta) || delta === 0) return showToast(t('seller.products.stock_number_required'));
    const reason = String(kq.lyDo || '').trim();
    if (!reason) return showToast(t('seller.products.stock_reason_required'));

    // Đây chỉ là điều chỉnh tồn có giải trình, không phải phiếu mua hàng và
    // tuyệt đối không tự sinh công nợ nhà cung cấp.
    const body = { delta, reason };

    // Sản phẩm theo lô: nhập hàng bắt buộc khai hạn, nếu không server từ chối.
    // Xuất kho thì bỏ qua ô hạn kể cả khi người dùng có điền - lô nào bị trừ là
    // do FEFO quyết, không phải do người xuất chọn.
    if (delta > 0 && product.track_batches) {
        const han = (kq.han || '').trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(han)) {
            return showToast(t('seller.products.expiry_invalid'));
        }
        body.expiry_date = han;
    }

    // Đơn giá chỉ gửi khi NHẬP. Xuất kho không làm đổi đơn giá bình quân của số
    // hàng còn lại, và backend từ chối thẳng nếu gửi kèm.
    if (XEM_DUOC_GIA_VON && delta > 0) {
        const chuoi = (kq.donGia || '').trim();
        if (chuoi !== '') {
            const unitCost = _soTienTuChuoi(chuoi);
            if (isNaN(unitCost) || unitCost < 0) {
                return showToast(t('seller.products.cost_nonnegative'));
            }
            // Gửi cả khi bằng 0: hàng tặng có đơn giá 0 thật và phải kéo bình
            // quân xuống. Bỏ trống mới là "không khai", và khi đó không gửi.
            body.unit_cost = unitCost;
        }
    }
    try {
        const res = await apiCall(`/products/${id}/stock`, 'POST', body);
        if (generation !== currentShopGeneration || currentShopId !== shopId) return;
        showToast(t(
            delta > 0 ? 'seller.products.stock_updated_in' : 'seller.products.stock_updated_out',
            { stock: dinhDangSoSeller(res.stock) }
        ));
        loadProducts();
    } catch(e) {
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

// ===== Quét mã vạch ở màn Kho hàng =====

/** Tab Kho hàng đang mở và đang hiện phần sản phẩm (không phải phần danh mục). */
function _khoHangDangMo() {
    const tab = document.getElementById('warehouse');
    if (!tab || !tab.classList.contains('active')) return false;
    const noiDung = document.getElementById('warehouseContent');
    if (!noiDung || noiDung.style.display === 'none') return false;
    const phanSP = document.getElementById('warehouseProductsSection');
    return !!phanSP && phanSP.style.display !== 'none';
}

function xuLyQuetKho(ma) {
    // Con trỏ đang nằm trong ô mã vạch của form: người dùng đang muốn GÁN mã cho
    // sản phẩm, không phải điều chỉnh kho. Đây là cách phân biệt hai ý định mà
    // không cần thêm nút bật/tắt chế độ.
    const oMaVach = document.getElementById('prodBarcode');
    if (oMaVach && document.activeElement === oMaVach) {
        oMaVach.value = ma;
        BarcodeScanner.bipOk();
        return;
    }

    if (!_khoHangDangMo()) {
        BarcodeScanner.bipLoi();
        showToast(t('seller.products.open_warehouse_to_scan'));
        return;
    }
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;

    // Mã vạch và mã nội bộ đều duy nhất theo shop, nên khớp được là chắc chắn.
    const sp = currentProducts.find(p => p.barcode && p.barcode.toUpperCase() === ma)
        || currentProducts.find(p => p.code && p.code.toUpperCase() === ma);
    if (!sp) {
        BarcodeScanner.bipLoi();
        showToast(t('seller.products.not_found_by_code', { code: ma }));
        return;
    }
    BarcodeScanner.bipOk();
    nhapXuatKho(sp.id);
}

// ===== Kiểm kê =====

// Phiếu đếm đang mở. Hai dạng dòng, phân biệt bằng cờ `theoLo`:
//
//   { theoLo: false, name, counted, stock_snapshot }
//   { theoLo: true,  name, lo: { <batch_id>: { counted, quantity_snapshot,
//                                              expiry_date } } }
//
// `stock_snapshot` / `quantity_snapshot` là số lúc BẮT ĐẦU đếm. Server so lại
// với số hiện tại lúc áp dụng; lệch nghĩa là có bán/nhập xen vào giữa và dòng
// đó bị bỏ qua thay vì ghi đè làm mất số hàng vừa bán.
//
// Hàng theo lô so ở mức LÔ chứ không mức tổng: hai lô đổi ngược chiều nhau (bán
// 3 của lô cũ, nhập 3 vào lô mới) làm tổng đứng yên trong khi cả hai đã khác.
let phieuKiemKe = {};

// product_id -> [{batch_id, expiry_date, quantity}], nạp một lần khi mở tab.
// Hỏi từng sản phẩm lúc quét là mỗi lượt quét một vòng mạng, giữa lúc người ta
// đang cầm máy quét chạy dọc kệ hàng.
let loKiemKeTheoSanPham = {};
let deficitKiemKeTheoSanPham = {};

function _kiemKeDangMo() {
    const tab = document.getElementById('kiemke');
    return !!tab && tab.classList.contains('active');
}

/** Nạp danh sách lô để đếm. Lỗi thì bỏ qua im lặng: hàng KHÔNG theo lô vẫn
 *  đếm được bình thường, chặn cả màn vì phần lô là thiệt hại lớn hơn. */
async function kkNapLo() {
    const shopId = currentShopId;
    if (!shopId) return;
    try {
        const d = await apiCall(`/products/${shopId}/stocktake/batches`);
        if (currentShopId !== shopId) return;
        const bang = {};
        const deficit = {};
        (d.products || []).forEach(p => {
            bang[p.product_id] = p.batches || [];
            deficit[p.product_id] = {
                quantity: p.offline_deficit_qty || 0,
                snapshot: p.offline_deficit_snapshot || null,
                stock: p.stock
            };
        });
        loKiemKeTheoSanPham = bang;
        deficitKiemKeTheoSanPham = deficit;
    } catch (e) {
        loKiemKeTheoSanPham = {};
        deficitKiemKeTheoSanPham = {};
    }
}

function kkDem(sp, soLuong) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    if (sp.track_batches) return kkThemTheoLo(sp);
    const cu = phieuKiemKe[sp.id];
    if (cu && !cu.theoLo) {
        cu.counted += soLuong;
        if (cu.counted < 0) cu.counted = 0;
    } else {
        // Token evidence tồn âm phải chụp lúc BẮT ĐẦU đếm, đúng như
        // `stock_snapshot`: server dùng nó để từ chối đóng một bằng chứng đã đổi
        // trong lúc đếm. Hàng không có evidence thì không có token, và server
        // cũng không đòi.
        const deficit = deficitKiemKeTheoSanPham[sp.id] || {};
        phieuKiemKe[sp.id] = {
            theoLo: false,
            counted: Math.max(0, soLuong),
            stock_snapshot: sp.stock,
            offline_deficit_snapshot: deficit.snapshot || null,
            name: sp.name
        };
    }
    kkVeBang();
}

/** Quét một sản phẩm theo lô thì đưa TẤT CẢ lô của nó vào phiếu, số đếm bắt
 *  đầu từ 0 để người đếm điền theo từng hạn.
 *
 *  Không tự cộng 1 như hàng thường: máy quét đọc mã sản phẩm chứ không đọc ra
 *  hạn, mà cộng bừa vào một lô nào đó là ghi sai hạn của số hàng đang cầm. */
function kkThemTheoLo(sp) {
    const lo = loKiemKeTheoSanPham[sp.id] || [];
    const deficit = deficitKiemKeTheoSanPham[sp.id] || {};
    if (!lo.length && !(deficit.quantity > 0)) {
        showToast(t('seller.stocktake.no_batch'));
        return;
    }
    if (phieuKiemKe[sp.id]) {
        showToast(t('seller.stocktake.batch_already_added'));
        return;
    }
    const bang = {};
    lo.forEach(b => {
        bang[b.batch_id] = {
            counted: 0,
            quantity_snapshot: b.quantity,
            expiry_date: b.expiry_date
        };
    });
    phieuKiemKe[sp.id] = {
        theoLo: true,
        name: sp.name,
        lo: bang,
        offline_deficit_qty: deficit.quantity || 0,
        offline_deficit_snapshot: deficit.snapshot || null,
        stock_snapshot: deficit.stock
    };
    kkVeBang();
}

function kkDatSo(id, giaTri) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const dong = phieuKiemKe[id];
    if (!dong || dong.theoLo) return;
    const n = parseInt(giaTri, 10);
    dong.counted = (isNaN(n) || n < 0) ? 0 : n;
    kkVeBang();
}

function kkDatSoLo(productId, batchId, giaTri) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const dong = phieuKiemKe[productId];
    if (!dong || !dong.theoLo) return;
    const lo = dong.lo[batchId];
    if (!lo) return;
    const n = parseInt(giaTri, 10);
    lo.counted = (isNaN(n) || n < 0) ? 0 : n;
    kkVeBang();
}

function kkBo(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    delete phieuKiemKe[id];
    kkVeBang();
}

function kkXoaHet() {
    phieuKiemKe = {};
    const kq = document.getElementById('kkKetQua');
    if (kq) kq.style.display = 'none';
    kkVeBang();
}

/** Mọi dòng sẽ hiện trên bảng, đã trải phẳng hàng theo lô thành từng lô. */
function kkCacDong() {
    const dong = [];
    Object.entries(phieuKiemKe).forEach(([id, d]) => {
        const productId = parseInt(id, 10);
        if (!d.theoLo) {
            dong.push({
                productId,
                batchId: null,
                ten: d.name,
                han: null,
                truoc: d.stock_snapshot,
                dem: d.counted
            });
            return;
        }
        if (!Object.keys(d.lo).length && d.offline_deficit_qty > 0) {
            dong.push({
                productId,
                batchId: null,
                ten: d.name,
                han: null,
                truoc: d.stock_snapshot,
                dem: 0,
                deficitOnly: true
            });
            return;
        }
        Object.entries(d.lo).forEach(([batchId, l]) => {
            dong.push({
                productId,
                batchId: parseInt(batchId, 10),
                ten: d.name,
                han: l.expiry_date,
                truoc: l.quantity_snapshot,
                dem: l.counted
            });
        });
    });
    return dong;
}

/** Tóm tắt đúng hiệu ứng mà server sẽ ghi nếu snapshot vẫn còn mới.
 *
 * Không được cộng các dòng `kkCacDong()` ở đây: với hàng theo lô có TON_AM,
 * các dòng đó chỉ là tổng lô (5), còn tồn Product trước kiểm kê là 3. Server
 * giữ chênh này làm evidence và, khi kiểm kê đủ lô, thêm một dòng reconciliation
 * tổng hợp để Product.stock đi từ 3 lên tổng lô. Preview phải nhìn theo sản
 * phẩm để phần chênh ấy xuất hiện đúng MỘT lần.
 */
function kkTomTatPreview() {
    const sanPham = Object.entries(phieuKiemKe).map(([id, d]) => {
        const productId = parseInt(id, 10);
        if (!d.theoLo) {
            const truoc = Number(d.stock_snapshot);
            const sau = Number(d.counted);
            return {
                productId,
                truoc,
                sau,
                lech: sau - truoc,
                // Hàng thường có đúng một dòng response khi số đếm đổi.
                soDongLech: truoc === sau ? 0 : 1
            };
        }

        const cacLo = Object.values(d.lo);
        const truoc = Number(d.stock_snapshot);
        // Server rebuild Product.stock từ tổng batch counted, kể cả khi không
        // còn lô dương nào (deficit-only).
        const sau = cacLo.reduce((tong, lo) => tong + Number(lo.counted), 0);
        const soLoLech = cacLo.filter(
            lo => Number(lo.counted) !== Number(lo.quantity_snapshot)
        ).length;
        const coReconcileOffline = Number(d.offline_deficit_qty || 0) > 0;
        return {
            productId,
            truoc,
            sau,
            lech: sau - truoc,
            // Response có một dòng cho mỗi lô thực sự đổi, và đúng một dòng
            // synthetic khi evidence TON_AM được đóng thành công.
            soDongLech: soLoLech + (coReconcileOffline ? 1 : 0)
        };
    });

    return {
        sanPham,
        tongTon: sanPham.reduce((tong, dong) => tong + dong.truoc, 0),
        tongDem: sanPham.reduce((tong, dong) => tong + dong.sau, 0),
        soDongLech: sanPham.reduce((tong, dong) => tong + dong.soDongLech, 0)
    };
}

function kkVeBang() {
    const tbody = document.getElementById('kkList');
    if (!tbody) return;
    const cacDong = kkCacDong();
    tbody.innerHTML = '';

    let thieu = 0, thua = 0, khop = 0;
    cacDong.forEach(d => {
        const lech = d.dem - d.truoc;
        if (lech < 0) thieu++; else if (lech > 0) thua++; else khop++;
        const mau = lech < 0 ? '#ef4444' : (lech > 0 ? 'var(--success)' : '#94A3B8');
        // Dòng theo lô phải nêu HẠN, nếu không cùng một sản phẩm hiện mấy dòng
        // trông giống hệt nhau và người đếm không biết điền vào ô nào.
        const oTen = d.batchId === null
            ? escapeHtml(d.ten)
            : `${escapeHtml(d.ten)}<br><span style="font-size:0.75rem; color:#94A3B8;">`
              + `<i class="ph ph-calendar-x"></i> ${escapeHtml(t('seller.stocktake.expiry', { date: d.han || '--' }))}</span>`;
        const doiSo = d.batchId === null
            ? `kkDatSo(${d.productId}, this.value)`
            : `kkDatSoLo(${d.productId}, ${d.batchId}, this.value)`;
        const disabled = d.deficitOnly ? ' disabled' : '';
        // Nút xóa gỡ cả sản phẩm: bỏ lẻ một lô rồi giữ các lô còn lại là chuyện
        // chưa gặp trong thực tế, mà thêm nút riêng thì bảng chật thêm.
        tbody.innerHTML += `<tr>
            <td>${oTen}</td>
            <td>${dinhDangSoSeller(d.truoc)}</td>
            <td><input type="number" min="0" value="${d.dem}" onchange="${doiSo}"${disabled}
                       style="width:80px; padding:0.3rem; border-radius:6px; border:1px solid #334155; background:#0F172A; color:#F8FAFC;"></td>
            <td style="color:${mau}; font-weight:600;">${lech > 0 ? '+' : ''}${lech}</td>
            <td><button class="btn-outline" onclick="kkBo(${d.productId})" style="padding:0.2rem 0.5rem; color:#ef4444;"><i class="ph ph-x"></i></button></td>
        </tr>`;
    });

    document.getElementById('kkSoSP').innerText =
        dinhDangSoSeller(Object.keys(phieuKiemKe).length);
    document.getElementById('kkSoThieu').innerText = dinhDangSoSeller(thieu);
    document.getElementById('kkSoThua').innerText = dinhDangSoSeller(thua);
    document.getElementById('kkSoKhop').innerText = dinhDangSoSeller(khop);
    document.getElementById('kkBtnApDung').disabled = cacDong.length === 0;
}

/** Tìm SP theo mã rồi cộng 1 vào phiếu đếm. */
function kkQuet(ma) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const sp = currentProducts.find(p => p.barcode && p.barcode.toUpperCase() === ma)
        || currentProducts.find(p => p.code && p.code.toUpperCase() === ma);
    if (!sp) {
        BarcodeScanner.bipLoi();
        showToast(t('seller.products.not_found_by_code', { code: ma }));
        return;
    }
    BarcodeScanner.bipOk();
    kkDem(sp, 1);
}

function kkNhapMaTay() {
    const o = document.getElementById('kkNhapTay');
    const ma = BarcodeScanner.chuanHoa(o.value);
    if (!ma) return;
    o.value = '';
    kkQuet(ma);
}

function kkQuetCamera() {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    // Để mở để quét liên tiếp cả kệ hàng, không phải bấm lại từng lần.
    BarcodeCamera.mo();
}

async function kkApDung() {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    // Hàng theo lô gửi `batches`, hàng thường gửi số tổng. Hai dạng loại trừ
    // nhau - server từ chối dòng nào khai lẫn lộn.
    const items = Object.entries(phieuKiemKe).map(([id, d]) => (
        d.theoLo
            ? {
                product_id: parseInt(id, 10),
                offline_deficit_snapshot: d.offline_deficit_snapshot,
                batches: Object.entries(d.lo).map(([batchId, l]) => ({
                    batch_id: parseInt(batchId, 10),
                    counted: l.counted,
                    quantity_snapshot: l.quantity_snapshot
                }))
            }
            : {
                product_id: parseInt(id, 10),
                counted: d.counted,
                stock_snapshot: d.stock_snapshot,
                offline_deficit_snapshot: d.offline_deficit_snapshot
            }
    ));
    if (!items.length) return;

    const cacDong = kkCacDong();
    const preview = kkTomTatPreview();

    // Nêu thẳng mức thay đổi của TỔNG tồn kho. Chỉ nói "N sản phẩm bị lệch" là
    // không đủ: quét thử mỗi món một lần rồi bấm Áp dụng sẽ đặt tồn về 1 cho
    // tất cả, tổng tồn có thể tụt từ vài trăm xuống vài đơn vị mà con số đó
    // không hiện ra ở đâu cả. Với hàng theo lô còn nguy hơn: quét xong mà chưa
    // điền ô nào thì mọi lô của nó đang là 0.
    const tongTon = preview.tongTon;
    const tongDem = preview.tongDem;
    const chenh = tongDem - tongTon;
    const moTaChenh = chenh === 0
        ? t('seller.stocktake.no_change')
        : t(
            chenh < 0 ? 'seller.stocktake.decrease' : 'seller.stocktake.increase',
            { amount: dinhDangSoSeller(Math.abs(chenh)) }
        );

    showCustomConfirm(
        t('seller.stocktake.confirm_title', {
            count: dinhDangSoSeller(cacDong.length)
        }),
        t('seller.stocktake.confirm_message', {
            stock: dinhDangSoSeller(tongTon),
            counted: dinhDangSoSeller(tongDem),
            change: moTaChenh,
            different: dinhDangSoSeller(preview.soDongLech)
        }),
        async () => {
            if (
                generation !== currentShopGeneration
                || currentShopId !== shopId
                || !damBaoCacheShopHienTai(currentProductsShopId)
            ) return;
            try {
                const res = await apiCall(`/products/${shopId}/stocktake`, 'POST', { items });
                if (generation !== currentShopGeneration || currentShopId !== shopId) return;
                kkHienKetQua(res);
                phieuKiemKe = {};
                // Token evidence vừa dùng xong là token chết: server sẽ từ chối
                // nó ở phiếu sau. Nạp lại map trước khi người dùng bắt đầu đếm
                // tiếp, nếu không phiếu thứ hai trong cùng tab nhận 409 và
                // trông như chức năng bị hỏng.
                await kkNapLo();
                kkVeBang();
                loadProducts();
            } catch (e) {
                if (generation === currentShopGeneration && currentShopId === shopId) {
                    showToast(e.message);
                }
            }
        },
        t('seller.stocktake.apply')
    );
}

function kkHienKetQua(res) {
    const box = document.getElementById('kkKetQua');
    const boQua = res.bo_qua || [];
    box.style.display = 'block';
    box.style.background = boQua.length ? '#422006' : '#052e16';
    box.style.border = `1px solid ${boQua.length ? '#a16207' : '#15803d'}`;
    box.style.color = '#F8FAFC';

    const tongLech = `${res.tong_lech > 0 ? '+' : ''}${dinhDangSoSeller(res.tong_lech)}`;
    let html = `<b>${escapeHtml(t('seller.stocktake.result', {
        adjusted: dinhDangSoSeller(res.da_dieu_chinh.length),
        difference: tongLech,
        matched: dinhDangSoSeller(res.khong_doi)
    }))}</b>`;
    if (boQua.length) {
        html += `<br><b style="color:#fbbf24;">${escapeHtml(t('seller.stocktake.skipped', {
            count: dinhDangSoSeller(boQua.length)
        }))}</b><ul style="margin:0.4rem 0 0 1.1rem;">`
            + boQua.map(b => `<li>${escapeHtml(b.name || t('seller.stocktake.product_fallback', { id: b.product_id }))}: ${escapeHtml(b.ly_do)}</li>`).join('')
            + `</ul>`;
    }
    box.innerHTML = html;
    showToast(t(boQua.length ? 'seller.stocktake.done_skipped' : 'seller.stocktake.done'));
}

// ===== Định tuyến lượt quét theo tab đang mở =====

/** Máy quét USB bắt ở tầm document nên phải tự phân biệt người dùng đang ở đâu. */
function xuLyQuetSeller(ma) {
    if (_kiemKeDangMo()) return kkQuet(ma);
    return xuLyQuetKho(ma);
}

BarcodeScanner.batDau(xuLyQuetSeller);

/** Nút camera cạnh ô mã vạch: quét xong điền thẳng vào ô, không đụng tồn kho.
 *  Phải truyền handler riêng vì camera mở lên là ô mất focus, nên `xuLyQuetKho`
 *  không còn suy ra được ý định "đang gán mã" như khi dùng máy quét USB. */
function quetMaVachBangCamera() {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    BarcodeCamera.mo({
        dongSauKhiQuet: true,
        xuLy: (ma) => {
            if (generation !== currentShopGeneration || currentShopId !== shopId) return;
            document.getElementById('prodBarcode').value = ma;
            BarcodeScanner.bipOk();
            showToast(t('seller.products.barcode_filled', { code: ma }));
        }
    });
}

/** Nút camera trên danh sách: quét xong mở hộp điều chỉnh kho cho SP đó. */
function quetNhapXuatBangCamera() {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    BarcodeCamera.mo({ dongSauKhiQuet: true });
}

function deleteProduct(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    const product = currentProducts.find(item => Number(item.id) === Number(id));
    if (!product) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    showCustomConfirm(
        t('seller.products.delete_title'),
        t('seller.products.delete_confirm'),
        async () => {
            if (
                generation !== currentShopGeneration
                || currentShopId !== shopId
                || !damBaoCacheShopHienTai(currentProductsShopId)
            ) return;
            try {
                await apiCall(`/products/${id}`, 'DELETE');
                if (generation !== currentShopGeneration || currentShopId !== shopId) return;
                showToast(t('seller.products.deleted'));
                loadProducts();
            } catch(e) {
                if (generation === currentShopGeneration && currentShopId === shopId) {
                    showToast(e.message);
                }
            }
        }
    );
}

async function toggleProductStatus(id) {
    if (!damBaoCacheShopHienTai(currentProductsShopId)) return;
    if (!currentProducts.some(item => Number(item.id) === Number(id))) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    try {
        await apiCall(`/products/${id}/status`, 'PUT');
        if (generation !== currentShopGeneration || currentShopId !== shopId) return;
        showToast(t('seller.products.status_updated'));
        loadProducts();
    } catch(e) {
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

async function createProduct() {
    if(!currentShopId) return;
    if (
        !damBaoCacheShopHienTai(currentProductsShopId)
        || !damBaoCacheShopHienTai(currentCategoriesShopId)
    ) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const catSelect = document.getElementById('catSelect');
    if(!catSelect || !catSelect.value) {
        return showToast(t('seller.products.category_required'));
    }
    const formData = new FormData();
    formData.append('code', document.getElementById('prodCode').value);
    // Luôn gửi, kể cả khi rỗng: backend phân biệt "không gửi" (giữ mã vạch cũ)
    // với "gửi rỗng" (xóa mã vạch). Xóa trắng ô phải thực sự gỡ được mã.
    formData.append('barcode', document.getElementById('prodBarcode').value);
    formData.append('name', document.getElementById('prodName').value);
    // Luôn gửi, cùng lý do với mã vạch: rỗng nghĩa là GỠ biến thể để sản phẩm
    // trở lại đơn lẻ. Không gửi thì backend hiểu là giữ nguyên biến thể cũ, và
    // người dùng xóa trắng ô sẽ thấy nó tự hiện lại sau khi lưu.
    formData.append('variant_name', document.getElementById('prodVariant')?.value ?? '');
    const priceStr = document.getElementById('prodPrice').value;
    const stockStr = document.getElementById('prodStock').value;
    
    if(parseFloat(priceStr) <= 0) return showToast(t('seller.products.price_positive'));
    if(parseInt(stockStr) < 0) return showToast(t('seller.products.stock_nonnegative'));

    formData.append('price', priceStr);
    // CHỈ chủ shop mới gửi field này. Nhân viên kho mà gửi kèm (dù rỗng) thì
    // backend hiểu là đang cố sửa giá vốn và trả 403 - cả thao tác sửa tên sản
    // phẩm cũng hỏng theo. Không gửi = giữ nguyên giá vốn đang có.
    if (XEM_DUOC_GIA_VON) {
        const costStr = document.getElementById('prodCost')?.value ?? '';
        if (costStr !== '' && parseFloat(costStr) < 0) {
            return showToast(t('seller.products.cost_nonnegative'));
        }
        // Gửi cả khi rỗng: rỗng nghĩa là XÓA giá vốn, dùng khi khai nhầm.
        formData.append('cost_price', costStr);
    }
    // Chỉ gửi khi TẠO mới: đổi cờ này trên sản phẩm đã có tồn kho sẽ làm tổng
    // lô lệch với tồn (chưa có lô nào mà cờ đã bật), nên form sửa không đụng tới.
    if (editingProductId === null && document.getElementById('prodTrackBatches')?.checked) {
        formData.append('track_batches', 'true');
    }
    formData.append('stock', stockStr);
    formData.append('category_id', catSelect.value);
    const img = document.getElementById('prodImage').files[0];
    if(img) formData.append('image', img);

    try {
        const isEditing = editingProductId !== null;
        const url = isEditing
            ? `/api/products/${editingProductId}`
            : `/api/products?shop_id=${shopId}`;
        const res = await fetch(url, {
            method: isEditing ? 'PUT' : 'POST',
            headers: {
                'Authorization': `Bearer ${getToken()}`,
                'Accept-Language': getCurrentLocale()
            },
            body: formData
        });
        if(!res.ok) {
            let errMsg = t('seller.products.save_error');
            try {
                const errData = await res.json();
                if(errData.detail) {
                    let msg = errData.detail;
                    if (Array.isArray(msg) && msg.length > 0 && msg[0].msg) {
                        errMsg = msg[0].msg;
                    } else if (typeof msg === 'object') {
                        errMsg = JSON.stringify(msg);
                    } else {
                        errMsg = msg;
                    }
                }
            } catch(err) {}
            throw new Error(errMsg);
        }
        if (generation !== currentShopGeneration || currentShopId !== shopId) return;
        showToast(t(isEditing ? 'seller.products.updated' : 'seller.products.created'));
        cancelEditProduct();
        loadProducts();
    } catch(e) {
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e instanceof TypeError ? t('common.network_error') : e.message);
        }
    }
}

let currentVouchers = [];
let editingVoucherId = null;

function renderVouchers(vouchers) {
    const tbody = document.getElementById('voucherList');
    tbody.innerHTML = '';
    vouchers.forEach(v => {
        tbody.innerHTML += `<tr>
            <td><strong>${escapeHtml(v.code)}</strong></td>
            <td>${v.discount_type === 'flat' ? 'VND' : '%'}</td>
            <td>${dinhDangSoSeller(v.discount_value)}</td>
            <td>${dinhDangSoSeller(v.usage_count)}/${v.usage_limit === -1 ? '∞' : dinhDangSoSeller(v.usage_limit)}</td>
            <td style="display:flex; gap:0.3rem;">
                <button class="btn-outline" onclick="editVoucher(${v.id})" style="padding:0.2rem 0.5rem;" title="${escapeHtml(t('common.edit'))}" aria-label="${escapeHtml(t('common.edit'))}"><i class="ph ph-pencil"></i></button>
                <button class="btn-outline" onclick="deleteVoucher(${v.id})" style="padding:0.2rem 0.5rem; color:#ef4444;" title="${escapeHtml(t('common.delete'))}" aria-label="${escapeHtml(t('common.delete'))}"><i class="ph ph-trash"></i></button>
            </td>
        </tr>`;
    });
}

async function loadVouchers() {
    if(!currentShopId) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const requestId = ++vouchersRequestId;
    try {
        const vouchers = await apiCall(`/vouchers/${shopId}`);
        if (
            requestId !== vouchersRequestId
            || generation !== currentShopGeneration
            || currentShopId !== shopId
        ) return;
        currentVouchers = vouchers;
        currentVouchersShopId = shopId;
        renderVouchers(vouchers);
    } catch (e) {
        if (
            requestId === vouchersRequestId
            && generation === currentShopGeneration
            && currentShopId === shopId
        ) showToast(e.message);
    }
}

function editVoucher(id) {
    if (!damBaoCacheShopHienTai(currentVouchersShopId)) return;
    const v = currentVouchers.find(x => x.id === id);
    if(!v) return;
    editingVoucherId = id;
    document.getElementById('vCode').value = v.code;
    document.getElementById('vType').value = v.discount_type;
    document.getElementById('vVal').value = v.discount_value;
    document.getElementById('vMin').value = v.min_order_value;
    document.getElementById('vLimit').value = v.usage_limit;
    document.getElementById('btnSaveVoucher').innerHTML = `<i class="ph ph-check-circle"></i> <span data-seller-action-label="voucher-save">${escapeHtml(t('seller.vouchers.update'))}</span>`;
    document.getElementById('btnCancelEditVoucher').style.display = 'block';
}

function cancelEditVoucher() {
    editingVoucherId = null;
    document.getElementById('vCode').value = '';
    document.getElementById('vType').value = 'flat';
    document.getElementById('vVal').value = '';
    document.getElementById('vMin').value = '0';
    document.getElementById('vLimit').value = '-1';
    document.getElementById('btnSaveVoucher').innerHTML = `<i class="ph ph-plus-circle"></i> <span data-seller-action-label="voucher-save">${escapeHtml(t('seller.vouchers.create_code'))}</span>`;
    document.getElementById('btnCancelEditVoucher').style.display = 'none';
}

async function createOrUpdateVoucher() {
    if(!currentShopId) return;
    if (!damBaoCacheShopHienTai(currentVouchersShopId)) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    const voucherId = editingVoucherId;
    const isEditing = voucherId !== null;

    const code = document.getElementById('vCode').value.trim();
    if (!code) {
        return showToast(t('seller.vouchers.code_required_error'));
    }

    const discountType = document.getElementById('vType').value;
    const discountValStr = document.getElementById('vVal').value;
    const minOrderValStr = document.getElementById('vMin').value;
    const limitStr = document.getElementById('vLimit').value;

    if (!discountValStr) {
        return showToast(t('seller.vouchers.value_required_error'));
    }
    const discountVal = parseFloat(discountValStr);
    if (isNaN(discountVal) || discountVal < 1) {
        return showToast(t('seller.vouchers.value_min'));
    }

    if (discountType === 'percentage') {
        if (discountVal <= 0 || discountVal > 100) {
            return showToast(t('seller.vouchers.percent_range'));
        }
    }

    const minOrderVal = parseFloat(minOrderValStr || '0');
    if (isNaN(minOrderVal) || minOrderVal < 0) {
        return showToast(t('seller.vouchers.minimum_nonnegative'));
    }

    const body = {
        code: code.toUpperCase(),
        discount_type: discountType,
        discount_value: discountVal,
        min_order_value: minOrderVal,
        max_discount: 0,
        usage_limit: parseInt(limitStr || '-1')
    };
    try {
        if(isEditing) {
            await apiCall(`/vouchers/${voucherId}`, 'PUT', body);
        } else {
            await apiCall(`/vouchers?shop_id=${shopId}`, 'POST', body);
        }
        if (generation !== currentShopGeneration || currentShopId !== shopId) return;
        showToast(t(isEditing ? 'seller.vouchers.updated' : 'seller.vouchers.created'));
        cancelEditVoucher();
        loadVouchers();
    } catch(e) {
        if (generation === currentShopGeneration && currentShopId === shopId) {
            showToast(e.message);
        }
    }
}

function deleteVoucher(id) {
    if (!damBaoCacheShopHienTai(currentVouchersShopId)) return;
    if (!currentVouchers.some(item => Number(item.id) === Number(id))) return;
    const shopId = currentShopId;
    const generation = currentShopGeneration;
    showCustomConfirm(
        t('seller.vouchers.delete_title'),
        t('seller.vouchers.delete_confirm'),
        async () => {
            if (
                generation !== currentShopGeneration
                || currentShopId !== shopId
                || !damBaoCacheShopHienTai(currentVouchersShopId)
            ) return;
            try {
                await apiCall(`/vouchers/${id}`, 'DELETE');
                if (generation !== currentShopGeneration || currentShopId !== shopId) return;
                showToast(t('seller.vouchers.deleted'));
                loadVouchers();
            } catch(e) {
                if (generation === currentShopGeneration && currentShopId === shopId) {
                    showToast(e.message);
                }
            }
        }
    );
}

async function downloadExcel() {
    if(!dashboardShopId) return showToast(t('seller.export.choose_shop'));
    try {
        const res = await fetch(`/api/export/seller/${dashboardShopId}`, {
            headers: {
                'Authorization': `Bearer ${getToken()}`,
                'Accept-Language': getCurrentLocale()
            },
            cache: 'no-store'
        });
        if (!res.ok) {
            if (res.status === 403) return showToast(t('seller.export.forbidden'));
            return showToast(t('seller.export.failed'));
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'seller_transactions.xlsx';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) {
        showToast(t('common.network_error'));
    }
}

// ĐỔI MẬT KHẨU
function showChangePasswordModal() {
    document.getElementById('changePasswordModal').style.display = 'flex';
    document.getElementById('changePasswordErrorMsg').innerText = '';
    document.getElementById('changePasswordSuccessMsg').innerText = '';
    document.getElementById('oldPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmNewPassword').value = '';
}

function closeChangePasswordModal() {
    document.getElementById('changePasswordModal').style.display = 'none';
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('changePasswordForm');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const old_password = document.getElementById('oldPassword').value;
        const new_password = document.getElementById('newPassword').value;
        const confirm = document.getElementById('confirmNewPassword').value;
        const errorMsg = document.getElementById('changePasswordErrorMsg');
        const successMsg = document.getElementById('changePasswordSuccessMsg');
        errorMsg.innerText = '';
        successMsg.innerText = '';
        
        if (new_password !== confirm) {
            errorMsg.innerText = t('seller.password.mismatch');
            return;
        }
        
        const regex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?\":{}|<>_]).+$/;
        if (!regex.test(new_password)) {
            errorMsg.innerText = t('seller.password.requirements');
            return;
        }
        
        try {
            const res = await apiCall('/auth/change-password', 'POST', { old_password, new_password });
            successMsg.innerText = t('seller.password.success');
            localStorage.setItem('token', res.access_token);
            if (res.staff_role) localStorage.setItem('staff_role', res.staff_role);
            showToast(t('seller.password.success'));
            closeChangePasswordModal();
        } catch (err) {
            errorMsg.innerText = err.message;
        }
    });
});

let confirmCallback = null;

/**
 * Hộp NHẬP LIỆU dựng trong trang, trả về `Promise<object|null>`.
 *
 * Thay cho `prompt()` của trình duyệt, cùng lý do với `xacNhan()` bên `pos.js`:
 * Chrome cho người dùng tick "chặn hộp thoại của trang này" sau vài hộp liên
 * tiếp, và từ lúc đó `prompt()` trả `null` NGAY mà không hiện gì. Code cũ xử lý
 * `null` bằng `return` im lặng, nên người bán bấm "Thu nợ" là không có chuyện
 * gì xảy ra - không lỗi, không thông báo, không cách nào biết vì sao. Chỗ thu
 * nợ hỏi hai lần liên tiếp và chỗ nhập kho hỏi ba lần, đúng kiểu chạm ngưỡng
 * đó nhanh nhất.
 *
 * Gộp mọi ô vào MỘT hộp thay vì hỏi nối tiếp: người dùng thấy hết những gì phải
 * khai trước khi bắt đầu, sửa lại ô trước được, và bấm Hủy một lần là xong.
 *
 * Trả `null` = người dùng hủy. Trả object = đã bấm xác nhận (giá trị từng ô có
 * thể rỗng, việc kiểm là của bên gọi).
 *
 * `truong`: [{ ten, nhan, kieu, giaTriDau, goiY, ghiChu, luaChon }]
 *   kieu: 'text' | 'textarea' | 'date' | 'select'   (mặc định 'text')
 *   luaChon (chỉ với 'select'): [{ gia, nhan }]
 */
function hoiThongTin({ tieuDe, moTa = '', truong = [], nhanXacNhan }) {
    return new Promise(resolve => {
        const modal = document.getElementById('inputModal');
        const khungO = document.getElementById('inputModalFields');
        const nutOk = document.getElementById('btnInputOk');
        const nutHuy = document.getElementById('btnInputCancel');
        if (!modal || !khungO || !nutOk || !nutHuy) return resolve(null);

        document.getElementById('inputModalTitle').innerText = tieuDe || '';
        document.getElementById('inputModalDesc').innerText = moTa || '';
        nutOk.innerText = nhanXacNhan || t('common.confirm');

        const kieuO = 'width:100%; padding:0.65rem; border-radius:8px;'
            + ' border:1px solid var(--border-color); font-size:0.95rem;'
            + ' margin-bottom:0.25rem;';
        // escapeHtml cho MỌI giá trị nội suy: nhãn có nhúng tên sản phẩm, mà
        // tên sản phẩm là dữ liệu người dùng gõ.
        khungO.innerHTML = truong.map(o => {
            const id = `inputField_${o.ten}`;
            let oNhap;
            if (o.kieu === 'select') {
                const chon = (o.luaChon || []).map(c =>
                    `<option value="${escapeHtml(c.gia)}"${c.gia === o.giaTriDau ? ' selected' : ''}>`
                    + `${escapeHtml(c.nhan)}</option>`
                ).join('');
                oNhap = `<select id="${id}" style="${kieuO}">${chon}</select>`;
            } else if (o.kieu === 'textarea') {
                // Lý do/ghi chú cần bàn phím chữ; nhánh text mặc định bên dưới
                // chủ ý bật bàn phím số vì đang phục vụ các ô tiền.
                oNhap = `<textarea id="${id}" rows="3" style="${kieuO}"`
                    + ` placeholder="${escapeHtml(o.goiY ?? '')}">`
                    + `${escapeHtml(o.giaTriDau ?? '')}</textarea>`;
            } else {
                // inputmode="numeric" cho bàn phím số trên điện thoại mà vẫn là
                // ô text: type="number" từ chối thẳng chuỗi "250.000" người
                // Việt hay gõ, còn ô text thì bên gọi tự bỏ dấu ngăn cách.
                const them = o.kieu === 'date'
                    ? 'type="date"'
                    : 'type="text" inputmode="numeric" autocomplete="off"';
                oNhap = `<input id="${id}" ${them} style="${kieuO}"`
                    + ` value="${escapeHtml(o.giaTriDau ?? '')}"`
                    + ` placeholder="${escapeHtml(o.goiY ?? '')}">`;
            }
            const ghiChu = o.ghiChu
                ? `<small style="display:block; color:var(--text-muted); font-size:0.75rem; margin-bottom:0.75rem;">${escapeHtml(o.ghiChu)}</small>`
                : '<div style="margin-bottom:0.75rem;"></div>';
            return `<label style="display:block; font-size:0.85rem; font-weight:600;`
                + ` margin-bottom:0.3rem; color:var(--text-main);" for="${id}">`
                + `${escapeHtml(o.nhan || '')}</label>${oNhap}${ghiChu}`;
        }).join('');

        modal.style.display = 'flex';
        setTimeout(() => document.getElementById(`inputField_${truong[0]?.ten}`)?.focus(), 0);

        const dong = ketQua => {
            modal.style.display = 'none';
            khungO.innerHTML = '';
            nutOk.onclick = null;
            nutHuy.onclick = null;
            modal.onclick = null;
            khungO.onkeydown = null;
            document.removeEventListener('keydown', khiNhanPhim);
            resolve(ketQua);
        };
        const layGiaTri = () => {
            const kq = {};
            truong.forEach(o => {
                kq[o.ten] = document.getElementById(`inputField_${o.ten}`)?.value ?? '';
            });
            return kq;
        };
        // Escape ở tầng document để bấm ra ngoài ô nhập vẫn thoát được.
        const khiNhanPhim = e => { if (e.key === 'Escape') dong(null); };

        nutOk.onclick = () => dong(layGiaTri());
        nutHuy.onclick = () => dong(null);
        modal.onclick = e => { if (e.target === modal) dong(null); };
        khungO.onkeydown = e => {
            // Enter trong ô nhập = bấm xác nhận. Không áp cho <select> vì trên
            // một số trình duyệt Enter đang dùng để chốt lựa chọn đang mở.
            if (e.key === 'Enter' && e.target.tagName !== 'SELECT') {
                e.preventDefault();
                dong(layGiaTri());
            }
        };
        document.addEventListener('keydown', khiNhanPhim);
    });
}

function showCustomConfirm(title, message, onConfirm, confirmLabel = t('common.confirm')) {
    const titleEl = document.getElementById('confirmTitle');
    const msgEl = document.getElementById('confirmMessage');
    const modalEl = document.getElementById('confirmModal');
    const okEl = document.getElementById('btnConfirmOk');
    if (titleEl) titleEl.innerText = title;
    if (msgEl) msgEl.innerText = message;
    if (okEl) {
        okEl.innerHTML = `<i class="ph ph-check-circle"></i> <span data-seller-action-label="confirm-ok">${escapeHtml(confirmLabel)}</span>`;
    }
    confirmCallback = onConfirm;
    if (modalEl) modalEl.style.display = 'flex';
}

function closeCustomConfirm() {
    const modalEl = document.getElementById('confirmModal');
    if (modalEl) modalEl.style.display = 'none';
    confirmCallback = null;
}

const btnCancel = document.getElementById('btnConfirmCancel');
if (btnCancel) btnCancel.onclick = closeCustomConfirm;

const btnOk = document.getElementById('btnConfirmOk');
if (btnOk) {
    btnOk.onclick = () => {
        if (confirmCallback) confirmCallback();
        closeCustomConfirm();
    };
}

// Bắt cả giao dịch chuyển thêm sau khi POS đã xuất hóa đơn và dọn đơn hiện
// tại. Khi tab ẩn chỉ tải 1 dòng để cập nhật badge; khi tab mở tải lại danh sách.
if (coQuyenNhanVien('RECONCILIATION')) {
    setInterval(lamMoiBadgeDoiSoatNen, 30000);
}

// ===== Phân trang + lọc ngày cho danh sách đơn (nhóm B) =====
let trangDonHienTai = 1;
const DON_MOI_TRANG = 50;

function chuoiThamSoNgay() {
    const tu = document.getElementById('filterTuNgay')?.value;
    const den = document.getElementById('filterDenNgay')?.value;
    const p = new URLSearchParams();
    if (tu) p.set('tu_ngay', tu);
    if (den) p.set('den_ngay', den);
    const s = p.toString();
    return s ? `?${s}` : '';
}

function chuoiThamSoDon() {
    const tu = document.getElementById('filterTuNgay')?.value;
    const den = document.getElementById('filterDenNgay')?.value;
    const p = new URLSearchParams({ page: String(trangDonHienTai) });
    p.set('per_page', String(DON_MOI_TRANG));
    if (tu) p.set('tu_ngay', tu);
    if (den) p.set('den_ngay', den);
    return `?${p.toString()}`;
}

function capNhatDieuKhienTrang(res) {
    const info = document.getElementById('thongTinTrang');
    const truoc = document.getElementById('btnTrangTruoc');
    const sau = document.getElementById('btnTrangSau');
    if (!info) return;

    const tong = res.total_orders ?? res.orders.length;
    if (tong === 0) {
        info.innerText = t('seller.pagination.no_orders_in_range');
    } else {
        const dau = (res.page - 1) * res.per_page + 1;
        const cuoi = Math.min(res.page * res.per_page, tong);
        info.innerText = t('seller.pagination.showing_orders', {
            from: dinhDangSoSeller(dau),
            to: dinhDangSoSeller(cuoi),
            total: dinhDangSoSeller(tong)
        });
    }
    if (truoc) truoc.disabled = res.page <= 1;
    if (sau) sau.disabled = !res.has_more;
}

function doiTrang(buoc) {
    const moi = trangDonHienTai + buoc;
    if (moi < 1) return;
    trangDonHienTai = moi;
    if (dashboardShopId) loadDashboardShop(dashboardShopId);
}

function apDungLocNgay() {
    const tu = document.getElementById('filterTuNgay').value;
    const den = document.getElementById('filterDenNgay').value;
    if (tu && den && tu > den) return showToast(t('seller.dashboard.date_range_invalid'));
    trangDonHienTai = 1;
    if (dashboardShopId) loadDashboardShop(dashboardShopId);
}

function xoaLocNgay() {
    document.getElementById('filterTuNgay').value = '';
    document.getElementById('filterDenNgay').value = '';
    trangDonHienTai = 1;
    if (dashboardShopId) loadDashboardShop(dashboardShopId);
}

// ===== Xem chi tiết đơn (nhóm B + D4 đối soát) =====

function nhanLoaiButToan(entryType) {
    const nhan = {
        BANK_IN: t('seller.ledger.bank_in'),
        CASH_TOPUP: t('seller.ledger.cash_topup'),
        REFUND_CASH: t('seller.ledger.refund_cash'),
        REFUND_TRANSFER: t('seller.ledger.refund_transfer'),
        DEBT_CASH: t('seller.ledger.debt_cash'),
        DEBT_TRANSFER: t('seller.ledger.debt_transfer'),
        BANK_UNAPPLIED: t('seller.ledger.bank_unapplied')
    };
    return nhan[entryType] || entryType || t('seller.ledger.transaction');
}

function nhanGhiChuButToan(note) {
    const value = String(note || '');
    if (value === 'SYSTEM_POS_CASH_TOPUP' || value === 'Thu bù tại quầy POS') {
        return t('seller.order_detail.system_pos_cash_topup');
    }
    return value;
}

function veChiTietDoiSoat(d) {
    const box = document.getElementById('odDoiSoat');
    const tomTat = document.getElementById('odDoiSoatTomTat');
    const paymentSection = document.getElementById('odThanhToanSection');
    const paymentList = document.getElementById('odThanhToanList');
    if (!box || !tomTat || !paymentSection || !paymentList) return;

    // Đơn ghi nợ có tiền về giữ `reconciliation_reason` NULL và không
    // `reconciliation_pending`, nên nếu chỉ xét hai cờ đó thì bấm "Xem chi
    // tiết" ra một khung trống - đúng lúc người bán cần mã giao dịch để tra
    // sao kê.
    const coTienVeChuaGhi = (d.payments || []).some(
        p => String(p.entry_type) === 'BANK_UNAPPLIED'
    );
    const reason = d.reconciliation_reason;
    if (!reason && !d.reconciliation_pending && !coTienVeChuaGhi) {
        box.style.display = 'none';
        tomTat.innerHTML = '';
        paymentSection.style.display = 'none';
        paymentList.innerHTML = '';
        return;
    }

    const meta = thongTinLoaiDoiSoat(
        reason || (coTienVeChuaGhi ? 'DEBT_TRANSFER_NOTICE' : reason)
    );
    let chenhLech = `${escapeHtml(t('seller.reconciliation.received'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.received_amount))}</strong>`;
    if (reason === 'UNDERPAID') {
        chenhLech = `${escapeHtml(t('seller.reconciliation.remaining'))}: <strong style="color:#B45309;">${escapeHtml(dinhDangTienDoiSoat(d.remaining_amount))}</strong>`;
    } else if (reason === 'OVERPAID' || reason === 'LATE_PAYMENT') {
        chenhLech = `${escapeHtml(t('seller.reconciliation.refund_customer'))}: <strong style="color:#B91C1C;">${escapeHtml(dinhDangTienDoiSoat(d.refund_due_amount))}</strong>`;
    }

    tomTat.innerHTML = `
        <div style="display:flex; justify-content:space-between; gap:0.75rem; align-items:flex-start; flex-wrap:wrap;">
            <strong>${escapeHtml(meta.title)}</strong>
            <span class="doi-soat-state ${meta.stateClass}">${escapeHtml(meta.stateLabel)}</span>
        </div>
        <div class="doi-soat-detail-summary-grid">
            <div>${escapeHtml(t('seller.reconciliation.order_total'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.total_amount))}</strong></div>
            <div>${chenhLech}</div>
            <div>${escapeHtml(t('seller.reconciliation.bank_amount'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.bank_paid_amount))}</strong></div>
            <div>${escapeHtml(t('seller.reconciliation.cash_topup'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.cash_paid_amount))}</strong></div>
        </div>
        <p style="margin:0.65rem 0 0; color:var(--text-muted); font-size:0.8rem; line-height:1.5;">${escapeHtml(meta.note)}</p>`;

    const payments = Array.isArray(d.payments) ? d.payments : [];
    paymentSection.style.display = payments.length ? 'block' : 'none';
    paymentList.innerHTML = payments.map(payment => {
        const entryType = String(payment.entry_type || '');
        const laHoan = entryType === 'REFUND_CASH' || entryType === 'REFUND_TRANSFER';
        // Tiền đã về nhưng CHƯA được ghi vào đơn. Hiện dấu '+' màu xanh như các
        // khoản thu thật là nói dối đúng chỗ nguy hiểm nhất - người bán đọc
        // lướt sẽ tưởng đơn đã thu được khoản đó rồi.
        const chuaGhiNhan = entryType === 'BANK_UNAPPLIED';
        const metaParts = [];
        if (payment.created_at) metaParts.push(dinhDangNgayGio(payment.created_at));
        if (payment.bank_txn_id) {
            metaParts.push(t('seller.order_detail.bank_transaction', {
                value: payment.bank_txn_id
            }));
        }
        if (payment.reference) {
            metaParts.push(t('seller.order_detail.reference', {
                value: payment.reference
            }));
        }
        if (payment.note) {
            metaParts.push(t('seller.order_detail.note', {
                value: nhanGhiChuButToan(payment.note)
            }));
        }
        const mau = chuaGhiNhan ? '#B45309' : (laHoan ? '#B91C1C' : '#166534');
        const dau = chuaGhiNhan ? '' : (laHoan ? '− ' : '+ ');
        const ghiChuThem = chuaGhiNhan
            ? `<div class="doi-soat-payment-meta" style="color:#B45309;">`
              + `${escapeHtml(t('seller.ledger.bank_unapplied_hint'))}</div>`
            : '';
        return `
            <div class="doi-soat-payment-row">
                <strong>${escapeHtml(nhanLoaiButToan(entryType))}</strong>
                <strong style="text-align:right; color:${mau};">
                    ${dau}${dinhDangTienDoiSoat(payment.amount)}
                </strong>
                <div class="doi-soat-payment-meta">${escapeHtml(metaParts.join(' · '))}</div>
                ${ghiChuThem}
            </div>`;
    }).join('');

    if (!payments.length && reason === 'LEGACY_REVIEW') {
        paymentSection.style.display = 'block';
        paymentList.innerHTML = `<div style="color:var(--text-muted); font-size:0.82rem;">${escapeHtml(t('seller.order_detail.no_ledger'))}</div>`;
    }
    box.style.display = 'block';
}

function renderChiTietDon(d) {
    document.getElementById('odMaDon').innerText = `#${d.id}`;

    const tt = window.moTaTrangThaiDon(d.status);
    const ngay = dinhDangNgayGio(d.created_at);
    const coChuyenKhoan = Number(d.bank_paid_amount || 0) > 0;
    const coTienMat = Number(d.cash_paid_amount || 0) > 0;
    const pttt = coChuyenKhoan && coTienMat
        ? t('seller.order_detail.mixed_payment', {
            defaultValue: 'Chuyển khoản + tiền mặt'
        })
        : t(coChuyenKhoan ? 'common.payment.transfer' : 'common.payment.cash');
    document.getElementById('odThongTin').innerText =
        `${ngay} • ${pttt} • ${tt.label}`;

    const khachHang = document.getElementById('odKhachHang');
    if (d.customer) {
        khachHang.innerText = t('seller.order_detail.customer', {
            name: d.customer.name,
            phone: d.customer.phone
        });
        khachHang.style.display = 'block';
    } else {
        khachHang.innerText = '';
        khachHang.style.display = 'none';
    }
    veChiTietDoiSoat(d);

    const tbody = document.getElementById('odDanhSach');
    tbody.innerHTML = '';
    (d.items || []).forEach(i => {
        tbody.innerHTML += `<tr>
            <td>${escapeHtml(i.product_name)}</td>
            <td style="text-align:right;">${escapeHtml(dinhDangTienDoiSoat(i.price || 0))}</td>
            <td style="text-align:right;">${escapeHtml(dinhDangSoSeller(i.quantity))}</td>
            <td style="text-align:right;">${escapeHtml(dinhDangTienDoiSoat(i.line_total || 0))}</td>
        </tr>`;
    });

    let tongKet = `<div>${escapeHtml(t('seller.order_detail.subtotal'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.subtotal || 0))}</strong></div>`;
    if (d.discount_amount > 0) {
        const ma = d.voucher_code ? ` (${d.voucher_code})` : '';
        tongKet += `<div style="color: #F59E0B;">${escapeHtml(t('seller.order_detail.discount', { code: ma }))}: −${escapeHtml(dinhDangTienDoiSoat(d.discount_amount))}</div>`;
    }
    if (Number(d.loyalty_discount_amount || 0) > 0) {
        tongKet += `<div style="color:#2563EB;">${escapeHtml(t('seller.order_detail.loyalty_discount', {
            points: dinhDangSoSeller(d.loyalty_points_redeemed || 0)
        }))}: −${escapeHtml(dinhDangTienDoiSoat(d.loyalty_discount_amount))}</div>`;
    }
    if (Number(d.loyalty_points_earned || 0) > 0) {
        tongKet += `<div style="color:#2563EB;">${escapeHtml(t('seller.order_detail.loyalty_earned', {
            points: dinhDangSoSeller(d.loyalty_points_earned)
        }))}</div>`;
    }
    tongKet += `<div style="font-size: 1.2rem; margin-top: 0.5rem;">${escapeHtml(t('seller.order_detail.grand_total'))}: <strong>${escapeHtml(dinhDangTienDoiSoat(d.total_amount || 0))}</strong></div>`;
    document.getElementById('odTongKet').innerHTML = tongKet;
    veLichSuTraHang(d);

    document.getElementById('orderDetailModal').style.display = 'flex';
}

/** Lịch sử khách trả hàng của đơn, hiện ngay dưới phần tổng kết.
 *
 *  Chỉ ĐỌC. Việc nhận trả nằm ở POS - đó mới là chỗ thu ngân đứng khi khách
 *  mang hàng đến, và là nơi có ca thu ngân để trừ tiền mặt ra khỏi két. */
function veLichSuTraHang(d) {
    const khoi = document.getElementById('odTraHang');
    if (!khoi) return;
    const phieu = d.returns || [];
    if (!phieu.length) {
        khoi.style.display = 'none';
        khoi.innerHTML = '';
        return;
    }

    let html = `<div style="border-top:1px solid var(--border-color); padding-top:0.75rem;">
        <strong style="color:#B91C1C;">${escapeHtml(t('seller.order_detail.returns_title'))}</strong>
        <div style="margin-top:0.35rem; font-weight:600;">
            ${escapeHtml(t('seller.order_detail.returned_total', {
                amount: dinhDangTienDoiSoat(d.returned_total || 0)
            }))}
        </div>`;
    phieu.forEach(p => {
        const cach = p.refund_method
            ? t(p.refund_method === 'cash'
                ? 'common.payment.cash'
                : 'common.payment.transfer')
            : '—';
        html += `<div style="margin-top:0.5rem; font-size:0.85rem; color:#475569;">
            <div>${escapeHtml(dinhDangNgayGio(p.created_at))} • ${escapeHtml(cach)}
                • <strong>${escapeHtml(dinhDangTienDoiSoat(p.refund_amount || 0))}</strong></div>`;
        (p.items || []).forEach(i => {
            // Nói rõ dòng nào KHÔNG quay lại kho: đó là hàng hỏng, và là phần
            // chênh giữa tiền hoàn với vốn thu hồi được trong báo cáo lãi.
            const kho = i.restocked
                ? ''
                : ` <span style="color:#B45309;">(${escapeHtml(t('seller.order_detail.not_restocked'))})</span>`;
            html += `<div style="padding-left:0.75rem;">• ${escapeHtml(i.product_name || '')}
                × ${escapeHtml(dinhDangSoSeller(i.quantity))}${kho}</div>`;
        });
        if (p.reason) {
            html += `<div style="padding-left:0.75rem; font-style:italic;">${escapeHtml(p.reason)}</div>`;
        }
        if (Number(p.loyalty_points_restored || 0) || Number(p.loyalty_points_reversed || 0)) {
            html += `<div style="padding-left:0.75rem; color:#2563EB;">${escapeHtml(t('seller.order_detail.return_points', {
                restored: dinhDangSoSeller(p.loyalty_points_restored || 0),
                reversed: dinhDangSoSeller(p.loyalty_points_reversed || 0)
            }))}</div>`;
        }
        html += '</div>';
    });
    html += '</div>';
    khoi.innerHTML = html;
    khoi.style.display = 'block';
}

async function xemChiTietDon(orderId) {
    try {
        const d = await apiCall(`/orders/${orderId}/detail`);
        openOrderDetailId = Number(orderId);
        orderDetailCache = d;
        renderChiTietDon(d);
    } catch (e) { showToast(e.message); }
}

function dongChiTietDon() {
    document.getElementById('orderDetailModal').style.display = 'none';
    openOrderDetailId = null;
}


// ===== Cài đặt Đọc tiền (nằm ở tab Cài Đặt, không ở POS) =====

function dtLuuBat() {
    DocTien.datBat(document.getElementById('dtBat').checked);
    dtCapNhatHien();
}

function dtCapNhatHien() {
    document.getElementById('dtChiTiet').style.display =
        document.getElementById('dtBat').checked ? 'block' : 'none';
}

function dtKhoiTao() {
    const bat = document.getElementById('dtBat');
    if (!bat || typeof DocTien === 'undefined') return;
    bat.checked = DocTien.dangBat();
    dtCapNhatHien();
}

// ===== H1: chủ shop tự cài chương trình tích điểm =====

function _giaTriSoLoyalty(id) {
    const raw = (document.getElementById(id)?.value || '').trim();
    return raw === '' ? null : Number(raw);
}

function _loyaltySoNguyenHopLe(value, minimum = 0) {
    return Number.isInteger(value) && value >= minimum;
}

function capNhatLoyaltyPreview() {
    const preview = document.getElementById('loyaltyPreview');
    if (!preview) return;
    const earnAmount = _giaTriSoLoyalty('loyaltyEarnAmount');
    const earnPoints = _giaTriSoLoyalty('loyaltyEarnPoints');
    const redeemPoints = _giaTriSoLoyalty('loyaltyRedeemPoints');
    const redeemAmount = _giaTriSoLoyalty('loyaltyRedeemAmount');
    const minRedeem = _giaTriSoLoyalty('loyaltyMinRedeem');
    const maxPercent = _giaTriSoLoyalty('loyaltyMaxPercent');
    const expiryDays = _giaTriSoLoyalty('loyaltyExpiryDays');
    if (
        !_loyaltySoNguyenHopLe(earnAmount, 1)
        || !_loyaltySoNguyenHopLe(earnPoints, 1)
        || !_loyaltySoNguyenHopLe(redeemPoints, 1)
        || !_loyaltySoNguyenHopLe(redeemAmount, 1)
        || !_loyaltySoNguyenHopLe(minRedeem, 0)
        || !_loyaltySoNguyenHopLe(maxPercent, 1)
        || maxPercent > 100
        || (expiryDays !== null && !_loyaltySoNguyenHopLe(expiryDays, 1))
    ) {
        preview.innerText = t('seller.loyalty.preview_incomplete');
        return;
    }
    const expiry = expiryDays === null
        ? t('seller.loyalty.no_expiry')
        : t('seller.loyalty.expires_in', { days: expiryDays });
    preview.innerText = t('seller.loyalty.preview', {
        earnAmount: dinhDangTienDoiSoat(earnAmount),
        earnPoints: dinhDangSoSeller(earnPoints),
        redeemPoints: dinhDangSoSeller(redeemPoints),
        redeemAmount: dinhDangTienDoiSoat(redeemAmount),
        maxPercent: dinhDangSoSeller(maxPercent),
        expiry
    });
}

function renderLoyaltyProgram(program, shopId) {
    const shop = allShops.find(item => Number(item.id) === Number(shopId));
    const name = document.getElementById('loyaltyShopName');
    if (name) name.innerText = shop?.name || '';
    const enabled = document.getElementById('loyaltyEnabled');
    if (enabled) enabled.checked = program?.enabled === true;
    const values = {
        loyaltyEarnAmount: program?.earn_amount,
        loyaltyEarnPoints: program?.earn_points,
        loyaltyRedeemPoints: program?.redeem_points,
        loyaltyRedeemAmount: program?.redeem_amount,
        loyaltyMinRedeem: program?.min_redeem_points ?? 0,
        loyaltyMaxPercent: program?.max_redeem_percent ?? 100,
        loyaltyExpiryDays: program?.expiry_days
    };
    Object.entries(values).forEach(([id, value]) => {
        const input = document.getElementById(id);
        if (input) input.value = value === null || value === undefined ? '' : String(value);
    });
    const status = document.getElementById('loyaltyStatusText');
    if (status) {
        status.innerText = t(program?.enabled
            ? 'seller.loyalty.status_enabled'
            : 'seller.loyalty.status_disabled');
        status.style.color = program?.enabled ? 'var(--success)' : 'var(--text-muted)';
    }
    capNhatLoyaltyPreview();
}

function khoaFormLoyalty(locked) {
    document.querySelectorAll('#loyalty input, #btnSaveLoyalty').forEach(control => {
        control.disabled = Boolean(locked);
    });
    const commonShop = document.getElementById('shopChungSelect');
    if (commonShop) commonShop.disabled = Boolean(locked);
}

async function loadLoyaltyProgram() {
    if (MY_ROLE !== 'SELLER' || !currentShopId) return;
    if (loyaltySaveBusy) return;
    const shopId = Number(currentShopId);
    const generation = currentShopGeneration;
    const requestId = ++loyaltyRequestId;
    const status = document.getElementById('loyaltyStatusText');
    const shop = allShops.find(item => Number(item.id) === shopId);
    const name = document.getElementById('loyaltyShopName');
    if (name) name.innerText = shop?.name || '';
    if (status) {
        status.innerText = t('common.loading');
        status.style.color = 'var(--text-muted)';
    }
    try {
        const program = await apiCall(`/loyalty/${shopId}`);
        if (
            requestId !== loyaltyRequestId
            || Number(currentShopId) !== shopId
            || generation !== currentShopGeneration
        ) return;
        loyaltyProgramCache = program;
        loyaltyProgramShopId = shopId;
        renderLoyaltyProgram(program, shopId);
    } catch (e) {
        if (requestId === loyaltyRequestId && Number(currentShopId) === shopId) {
            if (status) status.innerText = e.message;
            showToast(e.message);
        }
    }
}

async function saveLoyaltyProgram() {
    if (MY_ROLE !== 'SELLER') return showToast(t('seller.loyalty.owner_only'));
    const shopId = Number(currentShopId);
    if (!shopId || Number(loyaltyProgramShopId) !== shopId) {
        return showToast(t('common.loading'));
    }
    const enabled = document.getElementById('loyaltyEnabled')?.checked === true;
    const earnAmount = _giaTriSoLoyalty('loyaltyEarnAmount');
    const earnPoints = _giaTriSoLoyalty('loyaltyEarnPoints');
    const redeemPoints = _giaTriSoLoyalty('loyaltyRedeemPoints');
    const redeemAmount = _giaTriSoLoyalty('loyaltyRedeemAmount');
    const minRedeem = _giaTriSoLoyalty('loyaltyMinRedeem');
    const maxPercent = _giaTriSoLoyalty('loyaltyMaxPercent');
    const expiryDays = _giaTriSoLoyalty('loyaltyExpiryDays');

    if (earnAmount !== null && !_loyaltySoNguyenHopLe(earnAmount, 1)) {
        return showToast(t('seller.loyalty.amount_integer'));
    }
    if (redeemAmount !== null && !_loyaltySoNguyenHopLe(redeemAmount, 1)) {
        return showToast(t('seller.loyalty.amount_integer'));
    }
    if (enabled && (
        earnAmount === null
        || !_loyaltySoNguyenHopLe(earnPoints, 1)
        || !_loyaltySoNguyenHopLe(redeemPoints, 1)
        || redeemAmount === null
    )) return showToast(t('seller.loyalty.rates_required'));
    if (earnPoints !== null && !_loyaltySoNguyenHopLe(earnPoints, 1)) {
        return showToast(t('seller.loyalty.points_integer'));
    }
    if (redeemPoints !== null && !_loyaltySoNguyenHopLe(redeemPoints, 1)) {
        return showToast(t('seller.loyalty.points_integer'));
    }
    if (!_loyaltySoNguyenHopLe(minRedeem, 0)) {
        return showToast(t('seller.loyalty.min_invalid'));
    }
    if (!_loyaltySoNguyenHopLe(maxPercent, 1) || maxPercent > 100) {
        return showToast(t('seller.loyalty.percent_invalid'));
    }
    if (expiryDays !== null && !_loyaltySoNguyenHopLe(expiryDays, 1)) {
        return showToast(t('seller.loyalty.expiry_invalid'));
    }

    const body = {
        enabled,
        earn_amount: earnAmount,
        earn_points: earnPoints,
        redeem_points: redeemPoints,
        redeem_amount: redeemAmount,
        min_redeem_points: minRedeem,
        max_redeem_percent: maxPercent,
        expiry_days: expiryDays
    };
    const generation = currentShopGeneration;
    // Vô hiệu mọi GET cũ đang bay; nếu không response cũ có thể về sau PUT và
    // vẽ đè cấu hình vừa lưu. Khóa cả form để sửa trong lúc chờ không bị
    // response ghi đè rồi mất mà người dùng không biết.
    const requestId = ++loyaltyRequestId;
    loyaltySaveBusy = true;
    khoaFormLoyalty(true);
    try {
        const saved = await apiCall(`/loyalty/${shopId}`, 'PUT', body);
        if (
            Number(currentShopId) !== shopId
            || generation !== currentShopGeneration
            || requestId !== loyaltyRequestId
        ) return;
        loyaltyProgramCache = saved;
        loyaltyProgramShopId = shopId;
        renderLoyaltyProgram(saved, shopId);
        showToast(t('seller.loyalty.saved'));
    } catch (e) {
        if (
            Number(currentShopId) === shopId
            && requestId === loyaltyRequestId
        ) showToast(e.message);
    } finally {
        // Nút/form dùng chung giữa các shop; dù người dùng đổi shop lúc request
        // đang bay vẫn phải mở khóa lại, nếu không chỉ reload trang mới cứu được.
        loyaltySaveBusy = false;
        khoaFormLoyalty(false);
    }
}

[
    'loyaltyEnabled', 'loyaltyEarnAmount', 'loyaltyEarnPoints',
    'loyaltyRedeemPoints', 'loyaltyRedeemAmount', 'loyaltyMinRedeem',
    'loyaltyMaxPercent', 'loyaltyExpiryDays'
].forEach(id => document.getElementById(id)?.addEventListener('input', capNhatLoyaltyPreview));

// ===== C1d: phân biệt vai trò SELLER / STAFF trên giao diện =====
function applyRoleUI() {
    capNhatQuyenImportSanPham();
    const tabActionCenter = document.getElementById('tabActionCenter');
    if (tabActionCenter && MY_ROLE === 'SELLER') tabActionCenter.style.display = '';
    const tabLoyalty = document.getElementById('tabLoyalty');
    if (tabLoyalty && MY_ROLE === 'SELLER') tabLoyalty.style.display = '';
    const tabPurchasing = document.getElementById('tabPurchasing');
    if (tabPurchasing && ['SELLER', 'ADMIN'].includes(MY_ROLE)) tabPurchasing.style.display = '';
    const tabSubscription = document.getElementById('tabSubscription');
    if (tabSubscription && MY_ROLE === 'SELLER') tabSubscription.style.display = '';
    if (!XEM_DUOC_GIA_VON) {
        // Ô giá vốn ở form sản phẩm. Nhân viên kho vẫn thêm/sửa sản phẩm được,
        // chỉ là không thấy và không gửi field này (xem createProduct).
        const oGiaVon = document.getElementById('prodCostGroup');
        if (oGiaVon) oGiaVon.style.display = 'none';
    } else {
        // Tab lịch sử hàng hủy: mặc định ẩn trong HTML, chỉ chủ shop mới mở.
        // Số lỗ chính là giá vốn nhân số lượng nên nó thuộc cùng vòng bí mật.
        const tabHuy = document.getElementById('whSubTabWriteOff');
        if (tabHuy) tabHuy.style.display = '';
        // Xả Hàng Tồn cùng vòng bí mật: giá sàn và vốn đọng đều là giá vốn.
        const tabXaHang = document.getElementById('whSubTabClearance');
        if (tabXaHang) tabXaHang.style.display = '';
        // Dòng Tiền cũng vậy: lãi ròng nói ra giá vốn.
        const tabDongTien = document.getElementById('tabCashflow');
        if (tabDongTien) tabDongTien.style.display = '';
    }
    const btnBackAdmin = document.getElementById('btnBackAdmin');
    if (btnBackAdmin && MY_ROLE === 'ADMIN') btnBackAdmin.style.display = 'flex';
    if (MY_ROLE === 'ADMIN') {
        // Lát cắt hẹp: không biến trang Seller thành trang vận hành cho ADMIN.
        // Chỉ để lại tab Nhập Hàng và ô chọn shop chung.
        document.querySelectorAll('.tab-btn[data-main-tab]').forEach(button => {
            button.style.display = button.dataset.mainTab === 'purchasing' ? '' : 'none';
        });
        document.querySelectorAll('.tab-content').forEach(section => {
            section.style.display = section.id === 'purchasing' ? '' : 'none';
        });
        const btnOpenPos = document.getElementById('btnOpenPos');
        if (btnOpenPos) btnOpenPos.style.display = 'none';
        capNhatNhomDieuHuong();
        switchTab('purchasing', tabPurchasing);
        return;
    }
    if (MY_ROLE !== 'STAFF') {
        capNhatNhomDieuHuong();
        return;
    }
    // Nhân viên KHÔNG quản lý cửa hàng, nhưng vẫn phải chỉnh được phần Đọc tiền:
    // họ mới là người đứng quầy POS và nghe cái loa đó. Nên giữ tab Cài Đặt,
    // chỉ giấu phần cấu hình cửa hàng bên dưới.
    document.querySelectorAll('#settings > *').forEach(el => {
        if (
            el.id !== 'khoiDocTien'
            && !(MY_STAFF_ROLE === 'MANAGER' && el.id === 'returnApprovalSettings')
        ) el.style.display = 'none';
    });

    const allowedTabs = {
        CASHIER: new Set(['customers', 'settings']),
        SERVICE: new Set(),
        WAREHOUSE: new Set(['warehouse', 'kiemke', 'settings']),
        MANAGER: new Set([
            'dashboard', 'reconciliation', 'warehouse', 'kiemke',
            'voucher', 'customers', 'settings'
        ])
    }[MY_STAFF_ROLE] || new Set(['settings']);

    document.querySelectorAll('.tab-btn[data-main-tab]').forEach(button => {
        if (!allowedTabs.has(button.dataset.mainTab)) button.style.display = 'none';
    });
    document.querySelectorAll('.tab-content').forEach(section => {
        if (!allowedTabs.has(section.id)) section.style.display = 'none';
    });

    const btnOpenPos = document.getElementById('btnOpenPos');
    if (btnOpenPos && !coQuyenNhanVien('SALE')) btnOpenPos.style.display = 'none';

    const defaultTab = MY_STAFF_ROLE === 'CASHIER'
        ? 'customers'
        : (MY_STAFF_ROLE === 'WAREHOUSE' ? 'warehouse' : 'dashboard');
    capNhatNhomDieuHuong();
    switchTab(defaultTab);
}

// ===== C1d: quản lý nhân viên (chỉ chủ shop) =====
function renderStaffShopOptions() {
    const sel = document.getElementById('staffShopSelect');
    if (!sel) return;
    sel.innerHTML = allShops.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
    if (allShops.length) loadStaff();
}

function renderReturnManagerPinShopOptions() {
    const select = document.getElementById('returnManagerPinShop');
    if (!select) return;
    select.innerHTML = allShops.map(
        shop => `<option value="${shop.id}">${escapeHtml(shop.name)}</option>`
    ).join('');
}

async function saveReturnManagerPin() {
    const shopId = Number(document.getElementById('returnManagerPinShop').value);
    const pinField = document.getElementById('returnManagerPin');
    const status = document.getElementById('returnManagerPinStatus');
    if (!shopId || !/^\d{4,6}$/.test(pinField.value)) {
        status.textContent = t('seller.return_pin.invalid');
        return;
    }
    try {
        await apiCall(`/shops/${shopId}/manager-pin`, 'PATCH', { pin: pinField.value });
        pinField.value = '';
        status.textContent = t('seller.return_pin.saved');
    } catch (error) {
        pinField.value = '';
        status.textContent = error.message;
    }
}

function renderStaff(list) {
    const tbody = document.getElementById('staffList');
    tbody.innerHTML = '';
    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="3" style="color: var(--text-muted);">${escapeHtml(t('seller.staff.empty'))}</td></tr>`;
        return;
    }
    list.forEach(nv => {
        const selectedRole = nv.staff_role || 'MANAGER';
        const staffId = Number(nv.id);
        if (!Number.isInteger(staffId)) return;
        tbody.innerHTML += `<tr>
            <td>${escapeHtml(nv.username)}</td>
            <td>
                <select onchange="capNhatVaiTroNhanVien(${staffId}, this)" style="margin: 0; min-width: 8.5rem;">
                    <option value="CASHIER" ${selectedRole === 'CASHIER' ? 'selected' : ''}>${escapeHtml(t('common.role.cashier'))}</option>
                    <option value="SERVICE" ${selectedRole === 'SERVICE' ? 'selected' : ''}>${escapeHtml(t('seller.staff.role_service'))}</option>
                    <option value="WAREHOUSE" ${selectedRole === 'WAREHOUSE' ? 'selected' : ''}>${escapeHtml(t('common.role.warehouse'))}</option>
                    <option value="MANAGER" ${selectedRole === 'MANAGER' ? 'selected' : ''}>${escapeHtml(t('common.role.manager'))}</option>
                    <option value="KITCHEN" ${selectedRole === 'KITCHEN' ? 'selected' : ''}>${escapeHtml(t('common.role.kitchen'))}</option>
                    <option value="BAR" ${selectedRole === 'BAR' ? 'selected' : ''}>${escapeHtml(t('common.role.bar'))}</option>
                </select>
            </td>
            <td style="text-align:right;">
                ${MY_ROLE === 'SELLER' ? `<button type="button" class="btn-outline" data-staff-sessions-id="${staffId}" style="padding: 0.2rem 0.5rem;" title="${escapeHtml(t('seller.staff.sessions'))}" aria-label="${escapeHtml(t('seller.staff.sessions'))}"><i class="ph ph-devices"></i></button>` : ''}
                <button type="button" class="btn-outline" data-staff-remove-id="${staffId}" style="padding: 0.2rem 0.5rem; color:#ef4444;" title="${escapeHtml(t('seller.actions.stop_account'))}" aria-label="${escapeHtml(t('seller.actions.stop_account'))}"><i class="ph ph-user-minus"></i></button>
            </td>
        </tr>`;
    });
    tbody.querySelectorAll('[data-staff-remove-id]').forEach(button => {
        button.addEventListener('click', () => {
            xoaNhanVien(Number(button.dataset.staffRemoveId));
        });
    });
    tbody.querySelectorAll('[data-staff-sessions-id]').forEach(button => {
        button.addEventListener('click', () => {
            const staff = currentStaff.find(item => Number(item.id) === Number(button.dataset.staffSessionsId));
            if (staff) SessionDeviceR4.openStaff(staff.id, staff.username);
        });
    });
}

async function loadStaff() {
    const sel = document.getElementById('staffShopSelect');
    if (!sel || !sel.value) return;
    const shopId = Number(sel.value);
    const requestId = ++staffRequestId;
    if (!cacheThuocShop(currentStaffShopId, shopId)) {
        currentStaff = [];
        currentStaffShopId = null;
        renderStaff([]);
    }
    try {
        const list = await apiCall(`/staff/${shopId}`);
        if (requestId !== staffRequestId || Number(sel.value) !== shopId) return;
        currentStaff = list;
        currentStaffShopId = shopId;
        renderStaff(list);
    } catch (e) {
        if (requestId === staffRequestId && Number(sel.value) === shopId) {
            showToast(e.message);
        }
    }
}

async function taoNhanVien() {
    const sel = document.getElementById('staffShopSelect');
    const username = document.getElementById('staffUsername').value.trim();
    const password = document.getElementById('staffPassword').value;
    const staffRole = document.getElementById('staffRole').value;
    if (!sel || !sel.value) return showToast(t('seller.staff.choose_shop'));
    const shopId = damBaoCacheShopTrongSelector(currentStaffShopId, 'staffShopSelect');
    if (!shopId) return;
    if (!username) return showToast(t('seller.staff.username_required'));
    if (!password) return showToast(t('seller.staff.password_required'));
    try {
        await apiCall(`/staff/${shopId}`, 'POST', {
            username,
            password,
            staff_role: staffRole
        });
        if (shopDangChon('staffShopSelect') !== shopId) return;
        showToast(t('seller.staff.added'));
        document.getElementById('staffUsername').value = '';
        document.getElementById('staffPassword').value = '';
        loadStaff();
    } catch (e) {
        if (shopDangChon('staffShopSelect') === shopId) showToast(e.message);
    }
}

async function capNhatVaiTroNhanVien(id, select) {
    const shopId = damBaoCacheShopTrongSelector(currentStaffShopId, 'staffShopSelect');
    if (!shopId) {
        loadStaff();
        return;
    }
    const staff = currentStaff.find(item => Number(item.id) === Number(id));
    if (!staff) return;
    const previousRole = staff?.staff_role;
    if (staff) staff.staff_role = select.value;
    try {
        await apiCall(`/staff/member/${id}/role`, 'PUT', { staff_role: select.value });
        if (shopDangChon('staffShopSelect') === shopId) {
            showToast(t('seller.staff.role_updated'));
        }
    } catch (e) {
        if (
            shopDangChon('staffShopSelect') === shopId
            && cacheThuocShop(currentStaffShopId, shopId)
        ) {
            staff.staff_role = previousRole;
            showToast(e.message);
            loadStaff();
        }
    }
}

function xoaNhanVien(id) {
    const shopId = damBaoCacheShopTrongSelector(currentStaffShopId, 'staffShopSelect');
    if (!shopId) return;
    const staff = currentStaff.find(item => Number(item.id) === Number(id));
    if (!staff) return;
    showCustomConfirm(
        t('seller.staff.stop_title'),
        t('seller.staff.stop_confirm', { username: staff.username }),
        async () => {
            if (
                shopDangChon('staffShopSelect') !== shopId
                || !cacheThuocShop(currentStaffShopId, shopId)
            ) return;
            try {
                await apiCall(`/staff/member/${id}`, 'DELETE');
                if (shopDangChon('staffShopSelect') !== shopId) return;
                showToast(t('seller.staff.stopped'));
                loadStaff();
            } catch (e) {
                if (shopDangChon('staffShopSelect') === shopId) showToast(e.message);
            }
        }
    );
}

// Chạy sau khi DOM và allShops sẵn sàng. init() sẽ gọi renderStaffShopOptions.
applyRoleUI();
dtKhoiTao();


// ===== C2d: quản lý khách hàng (CRM) =====
let editingCustomerId = null;

function renderCustomerShopOptions() {
    const sel = document.getElementById('custShopSelect');
    if (!sel) return;
    sel.innerHTML = allShops.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
    if (allShops.length) loadCustomers();
}

/** Dòng công nợ nhỏ dưới tên khách. Khách không nợ thì không hiện gì cho gọn. */
function dongCongNo(kh) {
    const no = Number(kh.debt_amount || 0);
    if (no <= 0) return '';
    const hanMuc = kh.credit_limit;
    const chamTran = hanMuc !== null && hanMuc !== undefined && no >= Number(hanMuc);
    const mau = chamTran ? '#B91C1C' : '#B45309';
    return `<br><span style="font-size:0.75rem; color:${mau}; font-weight:600;">`
        + `${escapeHtml(t('seller.customers.owes', {
            amount: dinhDangTienDoiSoat(no)
        }))}</span>`;
}

function dongDiemKhach(kh) {
    const points = Number(kh.points_balance || 0);
    const color = points < 0 ? '#B91C1C' : '#2563EB';
    return `<br><span style="font-size:0.75rem; color:${color}; font-weight:600;">`
        + `<i class="ph ph-star"></i> ${escapeHtml(t('seller.customers.points', {
            points: dinhDangSoSeller(points)
        }))}</span>`;
}

function renderCustomers(list) {
    const tbody = document.getElementById('customerList');
    tbody.innerHTML = '';
    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="3" style="color: var(--text-muted);">${escapeHtml(t('seller.customers.empty'))}</td></tr>`;
        return;
    }
    list.forEach(kh => {
        const customerId = Number(kh.id);
        if (!Number.isInteger(customerId)) return;
        const inactive = kh.is_active === false;
        const inactiveBadge = inactive
            ? `<br><span style="font-size:0.72rem; color:#B91C1C; font-weight:700;">${escapeHtml(t('seller.customers.inactive'))}</span>`
            : '';
        const lastButton = inactive
            ? `<button type="button" class="btn-outline" style="padding:0.2rem 0.5rem; color:var(--success);" data-customer-status-id="${customerId}" title="${escapeHtml(t('seller.customers.use_again'))}" aria-label="${escapeHtml(t('seller.customers.use_again'))}"><i class="ph ph-arrow-counter-clockwise"></i></button>`
            : `<button type="button" class="btn-outline" style="padding:0.2rem 0.5rem; color:#ef4444;" data-customer-delete-id="${customerId}" title="${escapeHtml(t('common.delete'))}" aria-label="${escapeHtml(t('common.delete'))}"><i class="ph ph-trash"></i></button>`;
        tbody.innerHTML += `<tr style="${inactive ? 'opacity:0.68;' : ''}">
            <td>${escapeHtml(kh.name)}${inactiveBadge}${dongCongNo(kh)}${dongDiemKhach(kh)}</td>
            <td>${escapeHtml(kh.phone)}</td>
            <td style="text-align:right; white-space:nowrap;">
                <button type="button" class="btn-outline" style="padding:0.2rem 0.5rem;" data-customer-history-id="${customerId}" title="${escapeHtml(t('seller.customers.history_action'))}" aria-label="${escapeHtml(t('seller.customers.history_action'))}"><i class="ph ph-clock-counter-clockwise"></i></button>
                <button type="button" class="btn-outline" style="padding:0.2rem 0.5rem;" data-customer-edit-id="${customerId}" title="${escapeHtml(t('common.edit'))}" aria-label="${escapeHtml(t('common.edit'))}"><i class="ph ph-pencil-simple"></i></button>
                ${lastButton}
            </td>
        </tr>`;
    });
    tbody.querySelectorAll('[data-customer-history-id]').forEach(button => {
        button.addEventListener('click', () => {
            xemLichSuKhach(Number(button.dataset.customerHistoryId));
        });
    });
    tbody.querySelectorAll('[data-customer-edit-id]').forEach(button => {
        button.addEventListener('click', () => {
            editCustomer(Number(button.dataset.customerEditId));
        });
    });
    tbody.querySelectorAll('[data-customer-delete-id]').forEach(button => {
        button.addEventListener('click', () => {
            deleteCustomer(Number(button.dataset.customerDeleteId));
        });
    });
    tbody.querySelectorAll('[data-customer-status-id]').forEach(button => {
        button.addEventListener('click', () => {
            updateCustomerStatus(Number(button.dataset.customerStatusId), true);
        });
    });
}

async function loadCustomers() {
    const sel = document.getElementById('custShopSelect');
    if (!sel || !sel.value) return;
    const shopId = Number(sel.value);
    const q = (document.getElementById('custSearch')?.value || '').trim();
    const requestId = ++customersRequestId;
    if (!cacheThuocShop(currentCustomersShopId, shopId)) {
        window._customersCache = [];
        currentCustomersShopId = null;
        currentCustomersQuery = '';
        renderCustomers([]);
        cancelEditCustomer();
        customerHistoryRequestId += 1;
        customerHistoryCache = null;
        customerHistoryShopId = null;
        dongLichSuKhach();
    }
    try {
        const params = new URLSearchParams({ include_inactive: 'true' });
        if (q) params.set('q', q);
        const url = `/customers/${shopId}?${params.toString()}`;
        const list = await apiCall(url);
        const currentQuery = (document.getElementById('custSearch')?.value || '').trim();
        if (
            requestId !== customersRequestId
            || Number(sel.value) !== shopId
            || currentQuery !== q
        ) return;
        window._customersCache = list;
        currentCustomersShopId = shopId;
        currentCustomersQuery = q;
        renderCustomers(list);
    } catch (e) {
        const currentQuery = (document.getElementById('custSearch')?.value || '').trim();
        if (
            requestId === customersRequestId
            && Number(sel.value) === shopId
            && currentQuery === q
        ) showToast(e.message);
    }
}

async function saveCustomer() {
    const sel = document.getElementById('custShopSelect');
    const name = document.getElementById('custName').value.trim();
    const phone = document.getElementById('custPhone').value.trim();
    const address = document.getElementById('custAddress').value.trim();
    const note = document.getElementById('custNote').value.trim();
    if (!sel || !sel.value) return showToast(t('seller.staff.choose_shop'));
    const shopId = damBaoCacheShopTrongSelector(currentCustomersShopId, 'custShopSelect');
    if (!shopId) return;
    const customerId = editingCustomerId;
    const isEditing = customerId !== null;
    if (
        isEditing
        && !(window._customersCache || []).some(
            customer => Number(customer.id) === Number(customerId)
        )
    ) return;
    if (!name) return showToast(t('seller.customers.name_required_error'));
    if (!phone) return showToast(t('seller.customers.phone_required_error'));
    // Ô trống = không giới hạn (gửi null), KHÁC HẲN 0 = cấm khách này nợ.
    const hanMucRaw = (document.getElementById('custCreditLimit')?.value || '').trim();
    if (hanMucRaw !== '' && Number(hanMucRaw) < 0) {
        return showToast(t('seller.customers.credit_limit_negative'));
    }
    const body = {
        name, phone, address, note,
        credit_limit: hanMucRaw === '' ? null : Number(hanMucRaw)
    };
    try {
        if (isEditing) {
            await apiCall(`/customers/member/${customerId}`, 'PUT', body);
        } else {
            await apiCall(`/customers/${shopId}`, 'POST', body);
        }
        if (shopDangChon('custShopSelect') !== shopId) return;
        showToast(t(isEditing ? 'seller.customers.updated' : 'seller.customers.added'));
        cancelEditCustomer();
        loadCustomers();
    } catch (e) {
        if (shopDangChon('custShopSelect') === shopId) showToast(e.message);
    }
}

function editCustomer(id) {
    if (!damBaoCacheShopTrongSelector(currentCustomersShopId, 'custShopSelect')) return;
    const kh = (window._customersCache || []).find(c => c.id === id);
    if (!kh) return;
    editingCustomerId = id;
    document.getElementById('custName').value = kh.name || '';
    document.getElementById('custPhone').value = kh.phone || '';
    document.getElementById('custAddress').value = kh.address || '';
    document.getElementById('custNote').value = kh.note || '';
    const oHanMuc = document.getElementById('custCreditLimit');
    // Chưa đặt hạn mức thì để TRỐNG, không điền 0 - 0 mang nghĩa cấm nợ.
    if (oHanMuc) oHanMuc.value = (kh.credit_limit === null || kh.credit_limit === undefined)
        ? '' : kh.credit_limit;
    document.getElementById('custFormTitle').innerText = t('seller.customers.edit_title');
    document.getElementById('btnSaveCustomer').innerHTML = `<i class="ph ph-floppy-disk"></i> <span data-seller-action-label="customer-save">${escapeHtml(t('seller.customers.update'))}</span>`;
    document.getElementById('btnCancelEditCustomer').style.display = 'block';
}

function cancelEditCustomer() {
    editingCustomerId = null;
    ['custName', 'custPhone', 'custAddress', 'custNote', 'custCreditLimit'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('custFormTitle').innerText = t('seller.customers.add_title');
    document.getElementById('btnSaveCustomer').innerHTML = `<i class="ph ph-plus"></i> <span data-seller-action-label="customer-save">${escapeHtml(t('seller.customers.save'))}</span>`;
    document.getElementById('btnCancelEditCustomer').style.display = 'none';
}

function deleteCustomer(id) {
    const shopId = damBaoCacheShopTrongSelector(
        currentCustomersShopId,
        'custShopSelect'
    );
    if (!shopId) return;
    const customer = (window._customersCache || []).find(
        item => Number(item.id) === Number(id)
    );
    if (!customer) return;
    showCustomConfirm(
        t('seller.customers.delete_title'),
        t('seller.customers.delete_confirm', { name: customer.name }),
        async () => {
            if (
                shopDangChon('custShopSelect') !== shopId
                || !cacheThuocShop(currentCustomersShopId, shopId)
            ) return;
            try {
                const result = await apiCall(`/customers/member/${id}`, 'DELETE');
                if (shopDangChon('custShopSelect') !== shopId) return;
                showToast(t(result?.msg === 'Deactivated'
                    ? 'seller.customers.deactivated'
                    : 'seller.customers.deleted'));
                loadCustomers();
            } catch (e) {
                if (shopDangChon('custShopSelect') === shopId) showToast(e.message);
            }
        }
    );
}

async function updateCustomerStatus(id, isActive) {
    const shopId = damBaoCacheShopTrongSelector(
        currentCustomersShopId,
        'custShopSelect'
    );
    if (!shopId) return;
    if (!(window._customersCache || []).some(item => Number(item.id) === Number(id))) return;
    try {
        await apiCall(`/customers/member/${id}/status`, 'PUT', { is_active: isActive });
        if (shopDangChon('custShopSelect') !== shopId) return;
        showToast(t(isActive
            ? 'seller.customers.reactivated'
            : 'seller.customers.deactivated'));
        loadCustomers();
    } catch (e) {
        if (shopDangChon('custShopSelect') === shopId) showToast(e.message);
    }
}

function renderCustomerHistory(d) {
    document.getElementById('chTen').innerText = `${d.customer.name} (${d.customer.phone})`;
    document.getElementById('chTongKet').innerText =
        t('seller.customers.history_summary', {
            count: d.order_count,
            formattedCount: dinhDangSoSeller(d.order_count),
            amount: dinhDangTienDoiSoat(d.total_paid || 0)
        }) + ` • ${t('seller.customers.points', {
            points: dinhDangSoSeller(d.customer.points_balance || 0)
        })}`;
    const tbody = document.getElementById('chDanhSach');
    tbody.innerHTML = '';
    if (!d.orders.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="color: var(--text-muted);">${escapeHtml(t('seller.customers.no_orders'))}</td></tr>`;
    } else {
        d.orders.forEach(o => {
            const tt = window.moTaTrangThaiDon(o.status);
            const ngay = dinhDangNgayGio(o.date);
            const conNo = Number(o.remaining || 0);
            const oThuNo = conNo > 0
                ? `<br><button type="button" class="btn-outline" style="padding:0.15rem 0.45rem; font-size:0.75rem; margin-top:0.25rem;" data-debt-order-id="${o.id}" data-debt-remaining="${conNo}">`
                  + `<i class="ph ph-hand-coins"></i> ${escapeHtml(t('seller.customers.collect_debt'))}</button>`
                : '';
            const dongConNo = conNo > 0
                ? `<br><span style="font-size:0.75rem;">${escapeHtml(t('seller.customers.still_owes', {
                    amount: dinhDangTienDoiSoat(conNo)
                  }))}</span>`
                : '';
            tbody.innerHTML += `<tr>
                <td><strong>#${o.id}</strong></td>
                <td>${ngay}</td>
                <td>${escapeHtml(dinhDangTienDoiSoat(o.total || 0))}</td>
                <td style="color:${tt.color}; font-weight:600;">${escapeHtml(tt.label)}${dongConNo}${oThuNo}</td>
            </tr>`;
        });
        tbody.querySelectorAll('[data-debt-order-id]').forEach(nut => {
            nut.addEventListener('click', () => thuNo(
                Number(nut.dataset.debtOrderId),
                Number(nut.dataset.debtRemaining)
            ));
        });
    }
    document.getElementById('customerHistoryModal').style.display = 'flex';
}

/** Số tiền người dùng gõ -> số. Bỏ dấu ngăn cách nghìn vì người Việt gõ
 *  "250.000" là chuyện thường; ô nhập để text chính vì vậy. */
function _soTienTuChuoi(raw) {
    return parseFloat(String(raw ?? '').replace(/[.,\s]/g, ''));
}

/** Thu bớt nợ của một đơn. Trả bao nhiêu cũng được, nên phải hỏi số tiền.
 *
 *  Hỏi cả số tiền lẫn hình thức trong MỘT hộp. Bản cũ dùng hai `prompt()` nối
 *  tiếp - vừa dễ chạm ngưỡng chặn hộp thoại của Chrome (từ đó nút chết câm),
 *  vừa bắt người dùng gõ đúng chữ "TM" hoặc "CK" mới nhận. */
async function thuNo(orderId, conNo) {
    const kq = await hoiThongTin({
        tieuDe: t('seller.customers.collect_title', { id: orderId }),
        moTa: t('seller.customers.collect_desc', {
            remaining: dinhDangTienDoiSoat(conNo)
        }),
        truong: [
            {
                ten: 'soTien',
                nhan: t('seller.customers.collect_amount_label'),
                giaTriDau: String(Math.round(conNo))
            },
            {
                ten: 'cach',
                nhan: t('seller.customers.collect_method_label'),
                kieu: 'select',
                giaTriDau: 'cash',
                luaChon: [
                    { gia: 'cash', nhan: t('seller.customers.method_cash') },
                    { gia: 'transfer', nhan: t('seller.customers.method_transfer') }
                ]
            }
        ],
        nhanXacNhan: t('seller.customers.collect_submit')
    });
    if (kq === null) return;

    const soTien = _soTienTuChuoi(kq.soTien);
    if (!isFinite(soTien) || soTien <= 0) {
        return showToast(t('seller.customers.collect_amount_invalid'));
    }
    if (soTien > conNo) {
        return showToast(t('seller.customers.collect_too_much', {
            remaining: dinhDangTienDoiSoat(conNo)
        }));
    }
    const tienMat = kq.cach === 'cash';
    try {
        const res = await apiCall(`/orders/${orderId}/debt-payment`, 'POST', {
            amount: soTien,
            method: tienMat ? 'cash' : 'transfer',
            // Một mã cho đúng một lần bấm: bấm lại vì mạng chậm không thu hai lần.
            operation_id: taoOperationIdHoanTien()
        });
        showToast(t('seller.customers.collect_done', {
            amount: dinhDangTienDoiSoat(soTien),
            remaining: dinhDangTienDoiSoat(res.remaining_amount || 0)
        }));
        if (openCustomerHistoryId !== null) xemLichSuKhach(openCustomerHistoryId);
        loadCustomers();
    } catch (e) {
        showToast(e.message);
    }
}


async function xemLichSuKhach(id) {
    const shopId = damBaoCacheShopTrongSelector(
        currentCustomersShopId,
        'custShopSelect'
    );
    if (!shopId) return;
    if (!(window._customersCache || []).some(item => Number(item.id) === Number(id))) return;
    const requestId = ++customerHistoryRequestId;
    try {
        const d = await apiCall(`/customers/member/${id}/history`);
        if (
            requestId !== customerHistoryRequestId
            || shopDangChon('custShopSelect') !== shopId
            || !cacheThuocShop(currentCustomersShopId, shopId)
        ) return;
        openCustomerHistoryId = Number(id);
        customerHistoryCache = d;
        customerHistoryShopId = shopId;
        renderCustomerHistory(d);
    } catch (e) {
        if (
            requestId === customerHistoryRequestId
            && shopDangChon('custShopSelect') === shopId
        ) showToast(e.message);
    }
}

function dongLichSuKhach() {
    document.getElementById('customerHistoryModal').style.display = 'none';
    openCustomerHistoryId = null;
    customerHistoryRequestId += 1;
}

const refundModalEl = document.getElementById('refundModal');
if (refundModalEl) {
    refundModalEl.addEventListener('click', event => {
        if (event.target === refundModalEl) dongModalHoanTien();
    });
}

document.addEventListener('keydown', event => {
    if (
        event.key === 'Escape'
        && document.getElementById('refundModal')?.style.display === 'flex'
    ) {
        dongModalHoanTien();
    }
});

function capNhatNhanFormDongSeller() {
    const reconciliationCount = document.getElementById('doiSoatCount');
    if (reconciliationCount) {
        reconciliationCount.innerText = t(
            'seller.reconciliation.waiting_cases',
            {
                count: doiSoatBadgeCount,
                formattedCount: dinhDangSoSeller(doiSoatBadgeCount)
            }
        );
    }
    if (!allShops.length) {
        document.getElementById('statRev').innerText = dinhDangTienDoiSoat(0);
    }

    const shopFormOpen =
        document.getElementById('shopFormContainer')?.style.display !== 'none';
    const shop = allShops.find(item => item.id === editShopId);
    const shopTitleKey = editShopId && shop
        ? 'seller.shops.edit_title'
        : (shopFormOpen ? 'seller.shops.create_title' : 'seller.shops.business_info');
    capNhatNhanNut(
        '[data-seller-action-label="shop-form-title"]',
        shopTitleKey
    );
    const shopTitle = document.querySelector(
        '[data-seller-action-label="shop-form-title"]'
    );
    if (shopTitle && editShopId && shop) {
        shopTitle.textContent = t('seller.shops.edit_title', { name: shop.name });
    }
    capNhatNhanNut(
        '[data-seller-action-label="shop-save"]',
        editShopId ? 'seller.shops.save_update' : (
            shopFormOpen ? 'seller.shops.create_confirm' : 'seller.shops.save_settings'
        )
    );

    document.getElementById('productFormTitle').innerText = t(
        editingProductId === null
            ? 'seller.products.add_title'
            : 'seller.products.edit_title'
    );
    capNhatNhanNut(
        '[data-seller-action-label="product-save"]',
        editingProductId === null ? 'seller.products.save' : 'seller.products.update'
    );
    _khoaOTonKho(editingProductId !== null);

    document.getElementById('catFormTitle').innerText = t(
        editingCategoryId === null
            ? 'seller.categories.add_title'
            : 'seller.categories.edit_title'
    );
    capNhatNhanNut(
        '[data-seller-action-label="category-save"]',
        editingCategoryId === null ? 'seller.categories.save' : 'seller.categories.update'
    );
    capNhatNhanNut(
        '[data-seller-action-label="voucher-save"]',
        editingVoucherId === null ? 'seller.vouchers.create_code' : 'seller.vouchers.update'
    );
    document.getElementById('custFormTitle').innerText = t(
        editingCustomerId === null
            ? 'seller.customers.add_title'
            : 'seller.customers.edit_title'
    );
    capNhatNhanNut(
        '[data-seller-action-label="customer-save"]',
        editingCustomerId === null ? 'seller.customers.save' : 'seller.customers.update'
    );

    const confirmModal = document.getElementById('confirmModal');
    if (confirmModal?.style.display !== 'flex') {
        document.getElementById('confirmTitle').innerText =
            t('seller.confirm.default_title');
        document.getElementById('confirmMessage').innerText =
            t('seller.confirm.default_message');
        capNhatNhanNut(
            '[data-seller-action-label="confirm-ok"]',
            'seller.confirm.delete'
        );
    }

    if (refundOrderId) {
        document.getElementById('refundOrderLabel').innerText =
            refundReason === 'LATE_PAYMENT'
                ? t('seller.refund.cancelled_order', { id: refundOrderId })
                : t('seller.refund.completed_order', { id: refundOrderId });
        document.getElementById('refundAmount').innerText =
            dinhDangTienDoiSoat(refundDueAmount);
    } else {
        document.getElementById('refundAmount').innerText =
            dinhDangTienDoiSoat(0);
    }
    if (!dangLuuHoanTien) {
        capNhatNhanNut(
            '[data-seller-action-label="refund-submit"]',
            'seller.reconciliation.mark_refunded'
        );
    }
    triggerPreview();
}

function capNhatSellerTheoNgonNgu() {
    capNhatNhanFormDongSeller();
    renderBankOptions();
    renderShopsList();
    capNhatBadgeDoiSoat(doiSoatBadgeCount);

    if (currentProductsShopId === currentShopId) {
        filterProducts();
        kkVeBang();
    }
    if (
        coQuyenNhanVien('INVENTORY')
        && currentCategoriesShopId === currentShopId
    ) {
        renderCategories(currentCategories);
    }
    if (
        coQuyenNhanVien('VOUCHER')
        && currentVouchersShopId === currentShopId
    ) {
        renderVouchers(currentVouchers);
    }
    const selectedStaffShopId = Number(
        document.getElementById('staffShopSelect')?.value
    );
    if (
        MY_ROLE !== 'STAFF'
        && currentStaffShopId === selectedStaffShopId
    ) {
        renderStaff(currentStaff);
    }
    const selectedCustomerShopId = Number(
        document.getElementById('custShopSelect')?.value
    );
    const selectedCustomerQuery =
        (document.getElementById('custSearch')?.value || '').trim();
    if (
        coQuyenNhanVien('CUSTOMER')
        && window._customersCache
        && currentCustomersShopId === selectedCustomerShopId
        && currentCustomersQuery === selectedCustomerQuery
    ) {
        renderCustomers(window._customersCache);
    }
    if (
        dashboardOrdersCache
        && dashboardOrdersCache.shopId === dashboardShopId
        && dashboardOrdersCache.key === khoaDashboardOrders(
            dashboardShopId,
            chuoiThamSoDon()
        )
        && coQuyenNhanVien('REPORT')
    ) {
        renderDashboardOrders(dashboardShopId, dashboardOrdersCache.data);
    }
    if (
        dashboardStatsCache
        && dashboardStatsCache.shopId === dashboardShopId
        && dashboardStatsCache.key === khoaDashboardStats(
            dashboardShopId,
            chuoiThamSoNgay()
        )
        && coQuyenNhanVien('REPORT')
    ) {
        renderDashboardStats(dashboardStatsCache.data);
    }
    if (
        shiftHistoryCache
        && shiftHistoryCache.shopId === dashboardShopId
        && coQuyenNhanVien('REPORT')
    ) {
        renderShiftHistory(shiftHistoryCache.data);
    }
    if (document.getElementById('reconciliation')?.classList.contains('active')) {
        if (!doiSoatShopId) {
            renderDoiSoatKhongCoShop();
        } else if (
            reconciliationCache
            && reconciliationCache.shopId === doiSoatShopId
        ) {
            renderDoiSoatResponse(reconciliationCache.data);
        }
    }
    if (
        openOrderDetailId
        && orderDetailCache
        && Number(orderDetailCache.id) === openOrderDetailId
    ) {
        renderChiTietDon(orderDetailCache);
    }
    if (
        openCustomerHistoryId
        && customerHistoryCache
        && customerHistoryShopId === selectedCustomerShopId
        && Number(customerHistoryCache.customer?.id) === openCustomerHistoryId
    ) {
        renderCustomerHistory(customerHistoryCache);
    }
    if (
        loyaltyProgramCache
        && Number(loyaltyProgramShopId) === Number(currentShopId)
    ) {
        // Đổi ngôn ngữ chỉ vẽ lại chữ/preview từ giá trị ĐANG GÕ. Gọi
        // renderLoyaltyProgram ở đây sẽ lấy cache cũ ghi đè các ô chưa bấm Lưu.
        const status = document.getElementById('loyaltyStatusText');
        if (status) {
            status.innerText = t(loyaltyProgramCache.enabled
                ? 'seller.loyalty.status_enabled'
                : 'seller.loyalty.status_disabled');
            status.style.color = loyaltyProgramCache.enabled
                ? 'var(--success)'
                : 'var(--text-muted)';
        }
        capNhatLoyaltyPreview();
    }
    if (hanSuDungCache && Number(hanSuDungCache.shopId) === Number(currentShopId)) {
        veHanSuDung(hanSuDungCache.data);
    }
    if (duBaoCache && Number(duBaoCache.shopId) === Number(currentShopId)) {
        veDuBaoNhapHang(duBaoCache.data);
    }
    if (xaHangCache && Number(xaHangCache.shopId) === Number(currentShopId)) {
        veXaHangTon(xaHangCache.data);
    }
    window.FSellingPurchasing?.rerender?.();
}

document.addEventListener('fselling:localechange', capNhatSellerTheoNgonNgu);
capNhatNhanFormDongSeller();
init();
