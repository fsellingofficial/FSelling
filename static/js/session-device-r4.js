(function (global) {
    'use strict';

    const DEVICE_ID_KEY = 'fselling_auth_device_r4_id';
    const DEVICE_NAME_KEY = 'fselling_auth_device_r4_name';
    const SESSION_SUMMARY_KEY = 'fselling_auth_device_r4_session';
    const protectedPaths = ['/seller', '/pos', '/fnb', '/admin'];
    let dialog;
    let list;
    let status;
    let scope = { type: 'self' };
    let returnFocus;

    function getOrCreateAuthDeviceId() {
        let value = localStorage.getItem(DEVICE_ID_KEY);
        if (value) return value;
        if (global.crypto?.randomUUID) {
            value = global.crypto.randomUUID();
        } else if (global.crypto?.getRandomValues) {
            const words = new Uint32Array(4);
            global.crypto.getRandomValues(words);
            value = Array.from(words, word => word.toString(16).padStart(8, '0')).join('');
        } else {
            value = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        }
        localStorage.setItem(DEVICE_ID_KEY, value);
        return value;
    }

    function guessAuthDeviceType() {
        if (global.location?.pathname?.startsWith('/fnb/station/')) return 'KDS';
        const agent = String(global.navigator?.userAgent || '');
        if (/iPad|Tablet/i.test(agent)) return 'TABLET';
        if (/Mobile|Android|iPhone/i.test(agent) || global.innerWidth <= 600) return 'MOBILE';
        if (global.innerWidth > 600) return global.innerWidth <= 1100 ? 'TABLET' : 'DESKTOP';
        return 'UNKNOWN';
    }

    function buildAuthDeviceMetadata() {
        const device_type = guessAuthDeviceType();
        let device_name = localStorage.getItem(DEVICE_NAME_KEY);
        if (!device_name) {
            device_name = `${device_type} · ${global.navigator?.platform || 'Browser'}`;
            localStorage.setItem(DEVICE_NAME_KEY, device_name);
        }
        return { device_id: getOrCreateAuthDeviceId(), device_name, device_type };
    }

    function cacheAuthSessionSummary(session) {
        if (!session) return;
        const safe = {
            device_name: session.device_name,
            device_type: session.device_type,
            created_at: session.created_at,
            last_seen_at: session.last_seen_at,
            expires_at: session.expires_at
        };
        localStorage.setItem(SESSION_SUMMARY_KEY, JSON.stringify(safe));
    }

    function tr(key) {
        return typeof global.t === 'function' ? global.t(key) : key;
    }

    function element(tag, className, textValue) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (textValue !== undefined) node.textContent = textValue;
        return node;
    }

    function formatTime(value) {
        if (typeof global.dinhDangNgayGio === 'function') return global.dinhDangNgayGio(value);
        if (!value) return '—';
        const date = new Date(String(value).match(/Z|[+-]\d\d:?\d\d$/) ? value : `${value}Z`);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
    }

    function field(label, value) {
        const row = element('p', 'session-device-field');
        row.append(element('strong', '', `${label}: `), document.createTextNode(value));
        return row;
    }

    function ensureDialog() {
        if (dialog || !document.body?.append || !document.createElement) return dialog;
        dialog = element('dialog', 'session-device-dialog');
        dialog.setAttribute('aria-labelledby', 'sessionDeviceTitle');
        const header = element('header', 'session-device-header');
        const title = element('h2', '', tr('common.sessions.title'));
        title.id = 'sessionDeviceTitle';
        const close = element('button', 'session-device-close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', tr('common.close'));
        close.addEventListener('click', () => dialog.close());
        header.append(title, close);
        status = element('p', 'session-device-status');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        list = element('div', 'session-device-list');
        dialog.append(header, status, list);
        dialog.addEventListener('close', () => returnFocus?.focus?.());
        document.body.append(dialog);
        return dialog;
    }

    function endpoint(path) {
        return scope.type === 'staff' ? `/staff/member/${scope.staffId}${path}` : `/auth${path}`;
    }

    async function loadSessions() {
        status.textContent = tr('common.loading');
        list.replaceChildren();
        try {
            const sessions = await global.apiCall(endpoint('/sessions'));
            renderSessions(sessions);
            status.textContent = sessions.length ? '' : tr('common.no_data');
        } catch (error) {
            status.textContent = error.message;
        }
    }

    async function renameSession(session, name) {
        await global.apiCall(`/auth/sessions/${encodeURIComponent(session.session_id)}`, 'PATCH', {
            device_name: name
        });
        localStorage.setItem(DEVICE_NAME_KEY, name.trim());
        await loadSessions();
    }

    function startRename(session, card) {
        if (card.querySelector('.session-device-rename')) return;
        const editor = element('div', 'session-device-rename');
        const label = element('label', '', tr('common.sessions.rename_prompt'));
        const input = element('input');
        input.value = session.device_name;
        input.maxLength = 80;
        label.append(input);
        editor.append(
            label,
            actionButton(tr('common.save'), () => renameSession(session, input.value)),
            actionButton(tr('common.cancel'), () => editor.remove())
        );
        card.append(editor);
        input.focus();
        input.select();
    }

    function startConfirm(message, card, handler) {
        if (card.querySelector('.session-device-confirm')) return;
        const confirmation = element('div', 'session-device-confirm');
        confirmation.setAttribute('role', 'group');
        confirmation.setAttribute('aria-label', message);
        confirmation.append(
            element('p', '', message),
            actionButton(tr('common.confirm'), async () => {
                await handler();
                confirmation.remove();
            }),
            actionButton(tr('common.cancel'), () => confirmation.remove())
        );
        card.append(confirmation);
    }

    async function revokeSession(session) {
        await global.apiCall(
            endpoint(`/sessions/${encodeURIComponent(session.session_id)}`),
            'DELETE'
        );
        if (session.current && scope.type === 'self') {
            await global.clearAuthState();
            global.redirectToLogin();
            return;
        }
        await loadSessions();
    }

    async function revokeDevice(session) {
        await global.apiCall(endpoint('/devices/revoke'), 'POST', { device_id: session.device_id });
        if (session.current && scope.type === 'self') {
            await global.clearAuthState();
            global.redirectToLogin();
            return;
        }
        await loadSessions();
    }

    function actionButton(label, handler) {
        const button = element('button', 'session-device-action', label);
        button.type = 'button';
        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                await handler();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        });
        return button;
    }

    function renderSessions(sessions) {
        list.replaceChildren();
        sessions.forEach(session => {
            const card = element('article', 'session-device-card');
            const heading = element('h3');
            heading.textContent = session.device_name;
            if (session.current) heading.append(element('span', 'session-device-current', tr('common.sessions.current')));
            card.append(
                heading,
                field(tr('common.sessions.type'), session.device_type),
                field(tr('common.sessions.created'), formatTime(session.created_at)),
                field(tr('common.sessions.last_seen'), formatTime(session.last_seen_at)),
                field(tr('common.sessions.expires'), formatTime(session.expires_at))
            );
            const actions = element('div', 'session-device-actions');
            if (scope.type === 'self') {
                actions.append(actionButton(tr('common.sessions.rename'), () => startRename(session, card)));
            }
            actions.append(
                actionButton(tr('common.sessions.revoke'), () => startConfirm(
                    tr('common.sessions.revoke_confirm'), card, () => revokeSession(session)
                )),
                actionButton(tr('common.sessions.revoke_device'), () => startConfirm(
                    tr('common.sessions.device_revoke_confirm'), card, () => revokeDevice(session)
                ))
            );
            card.append(actions);
            list.append(card);
        });
    }

    async function open(type, staffId, displayName) {
        const node = ensureDialog();
        if (!node) return;
        scope = type === 'staff' ? { type, staffId } : { type: 'self' };
        returnFocus = document.activeElement;
        document.getElementById('sessionDeviceTitle').textContent = type === 'staff'
            ? tr('common.sessions.staff_title').replace('{{name}}', displayName || '')
            : tr('common.sessions.title');
        node.showModal();
        await loadSessions();
    }

    function installLauncher() {
        if (!localStorage.getItem('token') || !protectedPaths.some(path => global.location?.pathname?.startsWith(path))) return;
        const button = element('button', 'session-device-launcher', tr('common.sessions.open'));
        button.type = 'button';
        button.addEventListener('click', () => open('self'));
        document.body?.append(button);
    }

    global.getOrCreateAuthDeviceId = getOrCreateAuthDeviceId;
    global.guessAuthDeviceType = guessAuthDeviceType;
    global.buildAuthDeviceMetadata = buildAuthDeviceMetadata;
    global.cacheAuthSessionSummary = cacheAuthSessionSummary;
    global.SessionDeviceR4 = {
        openSelf: () => open('self'),
        openStaff: (staffId, displayName) => open('staff', staffId, displayName)
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installLauncher);
    } else {
        installLauncher();
    }
})(window);
