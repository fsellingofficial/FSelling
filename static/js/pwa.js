/* Install/help lives in the page utilities; native worker lifecycle owns updates. */
(function () {
    'use strict';
    const KHOA_BO_QUA = 'fselling_bo_qua_cai_app';
    let loi_moi = null;
    let waiting = false;
    let installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    const regions = [];
    const dich = (vi, en) => (window.FSellingI18n?.getLocale?.() || 'vi') === 'en' ? en : vi;
    let dismissed = false;
    try { dismissed = localStorage.getItem(KHOA_BO_QUA) === '1'; } catch (_) { /* private mode */ }
    function refresh(message) {
        regions.forEach(({entry, help, install, dismiss}) => {
            entry.textContent = waiting ? dich('Ứng dụng · Có cập nhật', 'App · Update available') : dich('Cài đặt / Trợ giúp ứng dụng', 'Install / App help');
            install.hidden = installed || !loi_moi || dismissed;
            dismiss.hidden = install.hidden;
            help.textContent = waiting
                ? dich('Có bản cập nhật. Hoàn tất việc đang làm rồi đóng các cửa sổ F-Selling và mở lại.', 'An update is ready. Finish your work, then close all F-Selling windows and reopen.')
                : message || (installed ? dich('Đang dùng ứng dụng đã cài.', 'Using the installed app.')
                : dich('Dùng nút Cài nếu có, hoặc menu trình duyệt để cài / thêm vào màn hình chính. Trình duyệt có thể chưa cung cấp nút cài tự động.', 'Use Install when available, or the browser menu to install / add to the home screen. Automatic installation may not be available.'));
            if (waiting || message) help.hidden = false;
        });
    }
    function cai_dat() {
        if (!loi_moi || installed) return false;
        const event = loi_moi;
        loi_moi = null; // Consume synchronously: two buttons cannot prompt twice.
        try {
            Promise.resolve(event.prompt()).then(() => event.userChoice).then(choice => {
                if (choice?.outcome === 'accepted') installed = true;
                refresh();
            }).catch(() => refresh(dich('Chưa cài được. Hãy thử từ menu trình duyệt.', 'Could not install. Try the browser menu.')));
        } catch (_) { refresh(dich('Chưa cài được. Hãy thử từ menu trình duyệt.', 'Could not install. Try the browser menu.')); }
        refresh();
        return true;
    }
    function mount() {
        if (regions.length) return;
        document.querySelectorAll('[data-ui-tools]').forEach(slot => {
            const region = document.createElement('div');
            const entry = document.createElement('button');
            const help = document.createElement('p');
            const install = document.createElement('button');
            const dismiss = document.createElement('button');
            entry.type = install.type = dismiss.type = 'button';
            help.hidden = true;
            help.setAttribute('role', 'status');
            entry.addEventListener('click', () => { help.hidden = !help.hidden; entry.setAttribute('aria-expanded', String(!help.hidden)); });
            entry.setAttribute('aria-expanded', 'false');
            install.textContent = dich('Cài ứng dụng', 'Install app');
            install.addEventListener('click', cai_dat);
            dismiss.textContent = dich('Ẩn lời mời cài', 'Dismiss install invitation');
            dismiss.addEventListener('click', () => { dismissed = true; try { localStorage.setItem(KHOA_BO_QUA, '1'); } catch (_) {} refresh(); });
            region.append(entry, help, install, dismiss);
            slot.append(region);
            regions.push({entry, help, install, dismiss});
        });
        refresh();
    }
    if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', mount);
    else mount();
    window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); loi_moi = event; refresh(); });
    window.addEventListener('appinstalled', () => { loi_moi = null; installed = true; refresh(); });
    if ('serviceWorker' in navigator) window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').then(registration => {
            const changed = () => { waiting = Boolean(registration.waiting); refresh(); };
            changed();
            registration.addEventListener('updatefound', () => {
                registration.installing?.addEventListener('statechange', changed);
            });
        }).catch(() => refresh(dich('Chưa bật được hỗ trợ ứng dụng. Bạn vẫn có thể dùng trang web khi có mạng.', 'App support is unavailable. You can still use the website online.')));
    });
    window.FSellingPWA = {
        coTheCai: () => loi_moi !== null && !installed,
        caiDat: cai_dat,
        hienNut: () => { dismissed = false; try { localStorage.removeItem(KHOA_BO_QUA); } catch (_) {} mount(); refresh(); regions.forEach(({help}) => {help.hidden=false;}); },
        xoaCache: async function () {
            if (navigator.serviceWorker?.controller) {
                navigator.serviceWorker.controller.postMessage({ type: 'XOA_CACHE' });
            }
            if (window.caches) {
                const ten = await caches.keys();
                await Promise.all(ten.map(function (t) { return caches.delete(t); }));
            }
            console.info('[PWA] Da xoa cache. Tai lai trang bang Ctrl+Shift+R.');
        },
        goCaiDat: async function () {
            const ds = await navigator.serviceWorker?.getRegistrations?.() || [];
            await Promise.all(ds.map(function (r) { return r.unregister(); }));
            console.info('[PWA] Da go Service Worker. Tai lai trang.');
        },
    };
})();
