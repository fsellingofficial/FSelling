const BASE_URL = '/api';
let cachedToken = localStorage.getItem('token');
let cachedUsername = localStorage.getItem('username');
const OFFLINE_SEAL_MARKER_PREFIX = 'fselling.offline-seal.v1:';
const AUTH_STORAGE_KEYS = Object.freeze([
    'token',
    'role',
    'staff_role',
    'username',
    'currentShopId',
    'register_email',
    'otp_send_time'
]);

function currentLanguage() {
    return window.FSellingI18n?.getLocale?.() || 'vi';
}

// Chỉ xóa dữ liệu phiên đăng nhập. Lựa chọn ngôn ngữ và cài đặt đọc tiền
// thuộc về thiết bị nên phải còn nguyên sau logout/401.
function taoOfflineSealGeneration() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    if (window.crypto?.getRandomValues) {
        const words = new Uint32Array(4);
        window.crypto.getRandomValues(words);
        return Array.from(words, value => value.toString(16).padStart(8, '0')).join('');
    }
    return `${Date.now()}:${Math.floor(window.performance?.now?.() || 0)}`;
}

function ghiOfflineSealMarker(username) {
    const oldUsername = String(username || '');
    if (!oldUsername) return null;
    const key = OFFLINE_SEAL_MARKER_PREFIX + encodeURIComponent(oldUsername);
    const value = JSON.stringify({
        username: oldUsername,
        generation: taoOfflineSealGeneration()
    });
    localStorage.setItem(key, value);
    return { key, value, username: oldUsername };
}

async function sealOfflineIdentityV1(username) {
    const marker = ghiOfflineSealMarker(username);
    if (!marker) return;
    if (window.OfflineBan?.sealIdentityV1) {
        try {
            // Seal local-only: không xóa token/receipt và không tự revoke lease
            // server. Marker được ghi TRƯỚC transaction để trang không nạp
            // module offline hoặc tab đóng giữa chừng vẫn fail-closed lần sau.
            await window.OfflineBan.sealIdentityV1({ username: marker.username });
            if (localStorage.getItem(marker.key) === marker.value) {
                localStorage.removeItem(marker.key);
            }
        } catch (e) {
            // Giữ marker bền; startup/read v1 sẽ seal trước normal use.
        }
    }
}

async function prepareAuthIdentityChangeV1(nextUsername) {
    const next = String(nextUsername || '');
    if (cachedUsername && cachedUsername !== next) {
        await sealOfflineIdentityV1(cachedUsername);
    }
}

async function clearAuthState() {
    // Dùng identity được cache riêng của document, không đọc localStorage ở đây:
    // storage event chỉ chạy sau khi tab khác đã overwrite username bằng user mới.
    await sealOfflineIdentityV1(cachedUsername);
    AUTH_STORAGE_KEYS.forEach(key => localStorage.removeItem(key));
    cachedToken = null;
    cachedUsername = null;
}

// Dùng URL mới sau logout/401 để trình duyệt không khôi phục một bản HTML
// đăng nhập cũ từ back-forward cache. Locale vẫn nằm trong localStorage và
// được i18n.js áp dụng ngay khi trang mới nạp xong.
function redirectToLogin() {
    window.location.replace(`/?auth=${Date.now()}`);
}

function navigateToPage(path) {
    const separator = path.includes('?') ? '&' : '?';
    window.location.href = `${path}${separator}entry=${Date.now()}`;
}

function hasLocalAccessToPage(pathname) {
    const token = localStorage.getItem('token');
    const role = localStorage.getItem('role');
    if (!token) return false;
    if (pathname === '/admin') return role === 'ADMIN';
    if (pathname === '/seller') {
        // ADMIN chỉ dùng lát cắt Nhập Hàng của seller.html; applyRoleUI ẩn toàn
        // bộ tab vận hành khác. POS vẫn không mở cho ADMIN.
        return role === 'ADMIN' || role === 'SELLER' || role === 'STAFF';
    }
    if (pathname === '/pos') {
        // Global ADMIN may open POS solely for owner-authorized offline
        // recovery; the server remains the authority for every action.
        return role === 'ADMIN' || role === 'SELLER' || role === 'STAFF';
    }
    return true;
}

// Back/Forward Cache có thể khôi phục nguyên một trang được bảo vệ mà không
// chạy lại các dòng kiểm tra ở đầu file trang. Kiểm tra lại khi pageshow để
// nút Back sau logout không làm lộ dashboard/POS cũ.
window.addEventListener('pageshow', event => {
    if (
        !event.persisted
        || !['/admin', '/seller', '/pos'].includes(window.location.pathname)
    ) {
        return;
    }
    if (!hasLocalAccessToPage(window.location.pathname)) {
        redirectToLogin();
        return;
    }
    // Nếu người dùng đã đăng nhập tài khoản khác rồi bấm Back, document trong
    // BFCache vẫn mang cachedToken và dữ liệu của phiên cũ. Nạp lại trang để
    // lấy đúng tài khoản, locale và dữ liệu hiện tại.
    if (localStorage.getItem('token') !== cachedToken) {
        window.location.replace(
            `${window.location.pathname}?session=${Date.now()}`
        );
        return;
    }
    const storedLocale = localStorage.getItem(
        window.FSellingI18n?.STORAGE_KEY || 'fselling.locale'
    );
    const locale = window.FSellingI18n?.SUPPORTED?.includes(storedLocale)
        ? storedLocale
        : 'vi';
    if (locale !== currentLanguage()) {
        setLanguage(locale, { persist: false });
    }
});

// Escape dữ liệu người dùng trước khi chèn vào innerHTML để chống XSS.
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Khóa bản dịch + màu cho trạng thái đơn hàng.
// Trạng thái lạ (do backend thêm sau này) vẫn hiển thị được, không vỡ giao diện.
const NHAN_TRANG_THAI_DON = {
    PENDING:      { key: 'common.status.pending',      color: '#F59E0B' },
    PAID:         { key: 'common.status.paid',         color: 'var(--success)' },
    CANCELLED:    { key: 'common.status.cancelled',    color: '#94A3B8' },
    UNRECONCILED: { key: 'common.status.unreconciled', color: '#EF4444' },
    // F4: bán ghi nợ. Thiếu dòng này thì mọi màn hiện chữ "DEBT" trần.
    DEBT:         { key: 'common.status.debt',         color: '#B45309' }
};

function moTaTrangThaiDon(status) {
    const description = NHAN_TRANG_THAI_DON[status];
    if (!description) return { label: status, color: '#94A3B8' };
    return { label: t(description.key), color: description.color };
}

/**
 * Đổi mốc thời gian do server trả về thành giờ địa phương đọc được.
 *
 * Server lưu bằng `datetime.utcnow()` nên chuỗi trả về là giờ UTC nhưng KHÔNG
 * kèm ký hiệu múi giờ, ví dụ "2026-07-29T18:41:52". Trình duyệt gặp chuỗi kiểu
 * đó sẽ hiểu là giờ địa phương, nên giờ hiển thị bị lệch đúng bằng múi giờ máy
 * - ở Việt Nam là 7 tiếng, khiến đơn bán buổi tối bị ghi lùi sang hôm trước.
 * Thêm "Z" để trình duyệt hiểu đúng là UTC rồi tự quy về giờ máy.
 *
 * Chuỗi đã có sẵn múi giờ ("Z" hoặc "+07:00") thì giữ nguyên.
 */
function dinhDangNgayGio(chuoi) {
    if (window.FSellingI18n?.formatDateTime) {
        return window.FSellingI18n.formatDateTime(chuoi);
    }
    if (!chuoi) return '';
    let s = String(chuoi);
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += 'Z';
    const d = new Date(s);
    return isNaN(d.getTime()) ? String(chuoi) : d.toLocaleString('vi-VN');
}

// Ghi đè localStorage.setItem để cập nhật cachedToken riêng cho tab này
const originalSetItem = localStorage.setItem;
localStorage.setItem = function(key, value) {
    if (key === 'token') {
        cachedToken = value;
    } else if (key === 'username') {
        cachedUsername = String(value);
    }
    originalSetItem.apply(this, arguments);
};

function getToken() {
    return cachedToken;
}

async function apiCall(endpoint, method = 'GET', body = null, requestUi = {}) {
    const isFormData = body instanceof FormData;
    const headers = {
        'Accept-Language': currentLanguage()
    };
    // Với FormData, trình duyệt phải tự thêm multipart boundary. Gắn thủ công
    // Content-Type sẽ làm server không đọc được file.
    if (!isFormData) headers['Content-Type'] = 'application/json';
    
    const token = getToken();
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const options = { method, headers, cache: 'no-store' };
    if (body) {
        options.body = isFormData ? body : JSON.stringify(body);
    }

    let res, rawBody, responseError;
    let deadlineTimer, slowTimer;
    const controller = requestUi.timeoutMs > 0 ? new AbortController() : null;
    if (controller) options.signal = controller.signal;
    const readResponse = async () => {
        try {
            res = await fetch(`${BASE_URL}${endpoint}`, options);
            if (!res.headers.get('Content-Disposition')) rawBody = await res.text();
        } catch (cause) {
            const error = new Error(t('common.network_error'));
            if (res) error.status = res.status;
            markMutationOutcomeUnknown(error, method, body);
            throw error;
        }
    };
    try {
        if (requestUi.onSlow && requestUi.slowAfterMs > 0) {
            slowTimer = setTimeout(() => { try { requestUi.onSlow(); } catch (_) { /* UI cannot change a request outcome. */ } }, requestUi.slowAfterMs);
        }
        const reading = readResponse();
        if (controller) {
            await Promise.race([reading, new Promise((_, reject) => {
                deadlineTimer = setTimeout(() => {
                    const error = new Error(t('common.request.timeout'));
                    error.code = 'CLIENT_TIMEOUT';
                    markMutationOutcomeUnknown(error, method, body);
                    reject(error);
                    controller.abort();
                }, requestUi.timeoutMs);
            })]);
        } else await reading;
    } catch (error) {
        responseError = error;
    } finally {
        clearTimeout(deadlineTimer);
        clearTimeout(slowTimer);
    }
    if (responseError && (res?.status !== 401 || endpoint.includes('/auth/login'))) throw responseError;
    if (res.headers.get('Content-Disposition')) return res;
    let data = null;
    let parseError;
    try {
        data = rawBody ? JSON.parse(rawBody) : null;
    } catch (_) {
        parseError = new Error(t('common.api_error'));
        parseError.status = res.status;
        markMutationOutcomeUnknown(parseError, method, body);
    }
    if (res.status === 401 && !endpoint.includes('/auth/login')) {
        let message = typeof data?.detail?.message === 'string'
            ? data.detail.message
            : t('common.api_error');
        const error = new Error(message);
        error.status = 401;
        error.detail = data?.detail || null;
        error.code = typeof data?.detail?.code === 'string' ? data.detail.code : null;
        markMutationOutcomeUnknown(error, method, body);
        if (error.mutationOutcomeUnknown) nhanSangTrangSau(error.message);
        if (localStorage.getItem('token') === cachedToken) await clearAuthState();
        redirectToLogin();
        throw error;
    }
    if (parseError) throw parseError;
    if (!res.ok) {
        let msg = data?.detail || t('common.api_error');
        if (Array.isArray(msg) && msg.length > 0 && msg[0].msg) {
            msg = msg[0].msg;
        } else if (typeof msg === 'object') {
            // Public API errors may carry a stable code beside localized text.
            // Keep that code on Error.code; never render a raw object in POS.
            msg = typeof msg.message === 'string' ? msg.message : t('common.api_error');
        }
        const error = new Error(msg);
        error.status = res.status;
        error.detail = (data && typeof data.detail === 'object') ? data.detail : null;
        // Stable API codes are intentionally separate from localized text.  The
        // offline queue needs them to choose a durable, safe local state.
        error.code = typeof data?.detail?.code === 'string' ? data.detail.code : null;
        if (res.status >= 500) markMutationOutcomeUnknown(error, method, body);
        throw error;
    }
    return data;
}

function markMutationOutcomeUnknown(error, method, body) {
    if (['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase())) return error;
    error.mutationOutcomeUnknown = true;
    error.operationId = body?.operation_id || null;
    // Chỉ giữ draft có operation ID để retry. Body đăng nhập/PIN/mật khẩu
    // không được gắn vào Error rồi vô tình lọt vào logger của caller.
    error.draft = error.operationId && !Object.keys(body).some(key => /password|pin|token|secret/i.test(key)) ? body : null;
    error.message = `${error.message} ${t('common.session.mutation_unknown')}`;
    return error;
}

function showToast(msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

// Câu nhắn dành cho TRANG KẾ TIẾP.
//
// Dùng khi thao tác thành công rồi chuyển trang ngay (đăng ký -> /verify, xác
// minh -> đăng nhập): toast trên trang hiện tại biến mất cùng lúc trang bị thay,
// nên người dùng không kịp đọc gì. Trước đây chỗ này dùng `alert()` để chặn cho
// kịp đọc - nhưng Chrome cho tick "chặn hộp thoại của trang này", và từ lúc đó
// alert() không hiện nữa, người dùng bị đá sang trang mới mà không hiểu vì sao.
//
// `sessionStorage` chứ không phải `localStorage`: câu nhắn chỉ có nghĩa trong
// đúng tab đang thao tác, và phải tự mất khi đóng tab. `clearAuthState()` cũng
// không đụng tới nó nên đăng xuất giữa chừng không nuốt mất câu nhắn.
const FLASH_KEY = 'fselling.flash';

function nhanSangTrangSau(message) {
    try {
        sessionStorage.setItem(FLASH_KEY, String(message ?? ''));
    } catch (e) {
        // Trình duyệt chặn sessionStorage (chế độ ẩn danh nghiêm ngặt): mất câu
        // nhắn thì đành chịu, nhưng KHÔNG được để nó chặn luồng chuyển trang.
    }
}

function hienNhanTuTrangTruoc() {
    let message = null;
    try {
        message = sessionStorage.getItem(FLASH_KEY);
        if (message) sessionStorage.removeItem(FLASH_KEY);
    } catch (e) {
        return;
    }
    // Chờ hết lượt hiện tại để i18n kịp nạp và #toast chắc chắn có trong DOM.
    if (message) setTimeout(() => showToast(message), 0);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hienNhanTuTrangTruoc);
} else {
    hienNhanTuTrangTruoc();
}

async function logout() {
    try {
        if (getToken()) await apiCall('/auth/logout', 'POST');
    } catch (error) {
        // Đăng xuất cục bộ vẫn phải hoàn tất khi máy chủ không phản hồi.
    } finally {
        await clearAuthState();
        redirectToLogin();
    }
}

// Tự động phát hiện khi đăng nhập ở tab khác trên cùng trình duyệt (Lập tức logout tab cũ)
window.addEventListener('storage', async (e) => {
    if (e.key === 'token') {
        if (e.newValue !== cachedToken) {
            await sealOfflineIdentityV1(cachedUsername);
            redirectToLogin();
        }
    }
});

// Định kỳ kiểm tra phiên đăng nhập với server (Lập tức logout nếu đăng nhập ở thiết bị/trình duyệt khác)
setInterval(async () => {
    const token = getToken();
    if (token) {
        try {
            const res = await fetch(`${BASE_URL}/auth/session-check`, {
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Accept-Language': currentLanguage()
                },
                cache: 'no-store'
            });
            if (res.status === 401) {
                if (localStorage.getItem('token') === cachedToken) {
                    await clearAuthState();
                }
                redirectToLogin();
            }
        } catch (e) {
            // Lỗi mạng tạm thời, bỏ qua để tránh logout nhầm
        }
    }
}, 15000);
