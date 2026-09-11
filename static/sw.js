/* Service Worker cua F-Selling.
 *
 * MUC TIEU: cai duoc len man hinh chinh, mo lai duoc khi mat mang.
 * KHONG PHAI muc tieu: tao don khi offline - viec do dung toi tien va ton kho,
 * lam rieng.
 *
 * ===================== BON LUAT KHONG DUOC PHA =====================
 *
 * 1. KHONG BAO GIO cache /api/*. Gia, ton kho, cong no phai lay tu mang.
 *    Tra ve mot con so cu tu cache la noi doi dung chO nguy hiem nhat: thu
 *    ngan nhin thay "con 5 cai" trong khi kho da het.
 *
 * 2. KHONG BAO GIO dung SW cho request khac GET. POST tao don, PUT sua hang -
 *    de nguyen cho mang xu ly, hong thi bao hong.
 *
 * 3. Trang HTML phai NETWORK-FIRST. Day la luat quan trong nhat va de sai
 *    nhat. File HTML chua cac dau `?v=` tro toi CSS/JS. Neu cache HTML truoc
 *    thi bump `?v=` se KHONG BAO GIO co tac dung, va bay "cache JS rat dai"
 *    trong CLAUDE.md tro thanh khong the sua duoc nua.
 *
 * 4. Chi cache-first voi file CO `?v=`. Doi noi dung -> doi `?v=` -> doi URL
 *    -> cache tu mien. Khong co `?v=` thi network-first cho an toan.
 *
 * ===================== KHI CAN TAT KHAN CAP =====================
 *
 * Neu SW gay loi la (nguoi dung ket o ban cu, trang trang...):
 *   Chrome > F12 > Application > Service Workers > Unregister
 *   Hoac day len mot ban sw.js chi co dung dong nay:
 *       self.addEventListener('install', () => self.registration.unregister());
 *   Moi may se tu go trong lan kiem tra cap nhat ke tiep.
 */

// Doi so nay khi muon xoa sach cache cua moi nguoi dung (vi du sau khi sua mot
// loi lien quan toi cache). Doi so = moi cache cu bi xoa o buoc activate.
const PHIEN_BAN = 'v3-device-r5';
const CACHE_VO = `fselling-vo-${PHIEN_BAN}`;      // khung app, nap san luc cai
const CACHE_CHAY = `fselling-chay-${PHIEN_BAN}`;  // file gap gi cache nay
const CACHE_HOP_LE = [CACHE_VO, CACHE_CHAY];

// CO Y khong liet ke file CSS/JS o day. Chung mang `?v=` va doi lien tuc; ghi
// vao day la moi lan bump `?v=` lai phai sua ca file nay, quen mot lan la hong.
// Chung duoc cache khi dung toi lan dau (xem phan fetch ben duoi).
const KHUNG_APP = [
    '/',
    '/offline.html',
    '/pos',
    '/fnb',
    '/fnb/station/kitchen',
    '/fnb/station/bar',
    '/seller',
    '/admin',
    '/register',
    '/verify',
    '/manifest.json',
    '/img/icon-192.png',
    '/img/icon-512.png',
];

const CDN = [
    'https://unpkg.com',
    'https://fonts.googleapis.com',
    'https://fonts.gstatic.com',
];

self.addEventListener('install', (su_kien) => {
    su_kien.waitUntil((async () => {
        const kho = await caches.open(CACHE_VO);
        // Nap tung file mot va BO QUA file loi. Dung cache.addAll thi chi can
        // MOT dia chi hong la ca buoc cai that bai va SW khong bao gio chay.
        await Promise.allSettled(KHUNG_APP.map((dia_chi) => kho.add(dia_chi)));
        // Wait until existing clients close; never replace a worker during a sale.
    })());
});

self.addEventListener('activate', (su_kien) => {
    su_kien.waitUntil((async () => {
        const ten = await caches.keys();
        await Promise.all(
            ten.filter((t) => /^fselling-(vo|chay)-/.test(t) && !CACHE_HOP_LE.includes(t)).map((t) => caches.delete(t))
        );
    })());
});

/** Luu vao cache neu phan hoi dung duoc. Phan hoi CDN kieu opaque co status 0
 *  nhung van dung duoc de hien icon/font khi offline. */
async function luu(ten_cache, yeu_cau, phan_hoi) {
    if (!phan_hoi) return;
    if (!phan_hoi.ok && phan_hoi.type !== 'opaque') return;
    const kho = await caches.open(ten_cache);
    await kho.put(yeu_cau, phan_hoi.clone());
}

/** Cache truoc, khong co thi ra mang. Cho file co `?v=` va anh - doi noi dung
 *  la doi URL nen khong the cu duoc. */
async function cache_truoc(yeu_cau) {
    const co_san = await caches.match(yeu_cau);
    if (co_san) return co_san;
    const phan_hoi = await fetch(yeu_cau);
    await luu(CACHE_CHAY, yeu_cau, phan_hoi);
    return phan_hoi;
}

/** Mang truoc, hong mang thi lay cache. Cho HTML va moi thu khong co `?v=`.
 *
 * `la_trang` = day la mot lan dieu huong (mo trang), khong phai tai file le.
 * Luc do phai lui thêm hai buoc nua, VI MOT LY DO DA DO DUOC THAT:
 *
 *   `auth.js` chuyen huong ve `/?auth=<timestamp>` va timestamp doi MOI LAN.
 *   Cache giu `/?auth=1785949778146`, lan sau trinh duyet hoi
 *   `/?auth=1785949873816` - khac URL nen khong khop, va nguoi dung offline
 *   nhan trang loi cua trinh duyet thay vi app. Loi nay khong lo ra khi thu
 *   bang `fetch()`, chi lo ra khi dieu huong that.
 *
 * Nen: khong khop y nguyen thi bo qua phan `?query` (cung mot trang), van
 * khong co thi hien thong tin can ket noi `/offline.html`. Chi ap dung cho dieu huong - file le
 * (CSS/JS/anh) van phai khop chinh xac, vi `?v=` o do la de PHAN BIET phien
 * ban, bo qua no la phuc vu dung ban cu ma minh vua cong suc chan.
 */
async function mang_truoc(yeu_cau, la_trang) {
    try {
        const phan_hoi = await fetch(yeu_cau);
        await luu(CACHE_CHAY, yeu_cau, phan_hoi);
        return phan_hoi;
    } catch (loi) {
        let co_san = await caches.match(yeu_cau);
        if (!co_san && la_trang) co_san = await caches.match(yeu_cau, { ignoreSearch: true });
        if (!co_san && la_trang) co_san = await caches.match('/offline.html');
        if (co_san) return co_san;
        throw loi;
    }
}

/** Tra cache ngay cho nhanh, dong thoi lam moi ngam. Cho font va icon CDN. */
async function vua_dung_vua_lam_moi(yeu_cau) {
    const co_san = await caches.match(yeu_cau);
    const dang_tai = fetch(yeu_cau)
        .then((ph) => { luu(CACHE_CHAY, yeu_cau, ph); return ph; })
        .catch(() => null);
    return co_san || (await dang_tai) || Response.error();
}

self.addEventListener('fetch', (su_kien) => {
    const yeu_cau = su_kien.request;

    // LUAT 2: chi dung toi GET.
    if (yeu_cau.method !== 'GET') return;

    const dia_chi = new URL(yeu_cau.url);

    // LUAT 1: /api khong bao gio di qua cache. De nguyen cho mang, hong thi
    // frontend tu bao loi nhu khi chua co SW.
    if (dia_chi.origin === self.location.origin && dia_chi.pathname.startsWith('/api/')) {
        return;
    }

    // CDN font va icon: co cache thi dung ngay, nen offline van con giao dien.
    if (CDN.some((goc) => dia_chi.origin === goc)) {
        su_kien.respondWith(vua_dung_vua_lam_moi(yeu_cau));
        return;
    }

    // Khac tên miền va khong nam trong danh sach CDN: khong dinh vao.
    if (dia_chi.origin !== self.location.origin) return;

    // LUAT 3: trang HTML luon hoi mang truoc, de `?v=` moi duoc nhin thay.
    if (yeu_cau.mode === 'navigate') {
        su_kien.respondWith(mang_truoc(yeu_cau, true));
        return;
    }

    // LUAT 4: co `?v=` thi cache truoc - doi noi dung la doi URL.
    if (dia_chi.searchParams.has('v')) {
        su_kien.respondWith(cache_truoc(yeu_cau));
        return;
    }

    // Anh va am thanh tinh: cache truoc cho nhe mang.
    if (/^\/(img|audio|uploads)\//.test(dia_chi.pathname)) {
        su_kien.respondWith(cache_truoc(yeu_cau));
        return;
    }

    // Con lai (ke ca CSS/JS li quen bump `?v=`): mang truoc cho an toan.
    su_kien.respondWith(mang_truoc(yeu_cau));
});

// Cong tac de trang goi vao khi can xoa sach cache ma khong phai mo DevTools.
self.addEventListener('message', (su_kien) => {
    if (su_kien.data && su_kien.data.type === 'XOA_CACHE') {
        su_kien.waitUntil(
            caches.keys().then((ten) => Promise.all(ten.map((t) => caches.delete(t))))
        );
    }
});
