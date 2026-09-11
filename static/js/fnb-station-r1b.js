(function (global) {
    'use strict';

    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));

    function ticketAgeMinutes(createdAt, now = Date.now()) {
        const created = new Date(createdAt).getTime();
        return Number.isFinite(created) ? Math.max(0, Math.floor((now - created) / 60_000)) : 0;
    }

    function ticketAgeClass(minutes) {
        return minutes >= 10 ? 'is-late' : minutes >= 5 ? 'is-warn' : '';
    }

    function createStationController(deps) {
        const state = { shopId: null, station: null, revision: null, tickets: [], pending: null, lastReadAt: null, timer: null, requestEpoch: 0, disposed: false };
        let pendingPromise = null;
        const uuid = () => deps.uuid?.() || global?.crypto?.randomUUID?.() || `ticket-${Date.now()}`;
        const delay = () => deps.isHidden?.() ? 10_000 : 2_000;

        async function load(force = false) {
            if (state.disposed || !state.shopId) return;
            if (state.timer !== null) {
                deps.clearTimeoutFn(state.timer);
                state.timer = null;
            }
            const shopId = Number(state.shopId);
            const station = state.station;
            const epoch = ++state.requestEpoch;
            const suffix = !force && Number.isInteger(state.revision) ? `&after_revision=${state.revision}` : '';
            try {
                const result = await deps.request(`/fnb/stations/${station}/tickets?shop_id=${shopId}${suffix}`, 'GET');
                if (state.disposed || epoch !== state.requestEpoch || shopId !== state.shopId || station !== state.station) return;
                if (result?.changed !== false) {
                    state.revision = Number(result.revision);
                    state.tickets = result.tickets || [];
                    deps.render({ type: 'queue', value: result });
                }
                state.lastReadAt = Date.now();
                deps.render({ type: 'synced', at: state.lastReadAt });
                return result;
            } catch (error) {
                if (state.disposed || epoch !== state.requestEpoch || shopId !== state.shopId || station !== state.station) return;
                deps.render({ type: 'error', error, hasData: state.tickets.length > 0 });
                throw error;
            } finally {
                if (!state.disposed && epoch === state.requestEpoch && shopId === state.shopId && station === state.station) {
                    state.timer = deps.setTimeoutFn(() => load(false).catch(() => {}), delay());
                }
            }
        }

        async function performPending() {
            if (!state.pending) return;
            if (state.pending.inFlight && pendingPromise) return pendingPromise;
            state.pending.inFlight = true;
            deps.render({ type: 'pending', value: state.pending });
            pendingPromise = (async () => {
                try {
                    const result = await deps.request(state.pending.endpoint, 'POST', state.pending.body);
                    state.pending = null;
                    pendingPromise = null;
                    state.revision = Number(result.revision);
                    state.tickets = state.tickets
                        .map(ticket => Number(ticket.id) === Number(result.id) ? result : ticket)
                        .filter(ticket => !['DONE', 'CANCELLED'].includes(ticket.status));
                    deps.render({ type: 'queue', value: { tickets: state.tickets, revision: state.revision } });
                    return result;
                } catch (error) {
                    if (!error?.mutationOutcomeUnknown && Number(error?.status) >= 400 && Number(error?.status) < 500) state.pending = null;
                    else if (state.pending) state.pending.inFlight = false;
                    pendingPromise = null;
                    deps.render({ type: 'transition-error', error });
                    throw error;
                }
            })();
            return pendingPromise;
        }

        function transition(ticketId, action, reason = null) {
            if (state.pending) {
                const error = new Error('A ticket mutation is already pending');
                error.code = 'FNB_MUTATION_PENDING';
                deps.render({ type: 'blocked', value: state.pending });
                return Promise.reject(error);
            }
            const ticket = state.tickets.find(row => Number(row.id) === Number(ticketId));
            if (!ticket) return Promise.reject(new Error('Ticket unavailable'));
            state.pending = {
                endpoint: `/fnb/tickets/${Number(ticketId)}/${action}`,
                body: {
                    expected_state_version: Number(ticket.state_version || 0),
                    expected_session_revision: Number(ticket.session_revision || 0),
                    operation_id: uuid(),
                    ...(reason ? { reason } : {}),
                },
                inFlight: false,
            };
            return performPending();
        }

        async function start(shopId, station) {
            if (state.pending) {
                const error = new Error('A ticket mutation is already pending');
                error.code = 'FNB_MUTATION_PENDING';
                deps.render({ type: 'blocked', value: state.pending });
                throw error;
            }
            if (state.timer !== null) deps.clearTimeoutFn(state.timer);
            state.requestEpoch += 1;
            state.shopId = Number(shopId);
            state.station = String(station).toUpperCase();
            state.revision = null;
            state.tickets = [];
            deps.render({ type: 'loading' });
            return load(true);
        }

        function dispose() {
            state.disposed = true;
            state.requestEpoch += 1;
            deps.clearTimeoutFn(state.timer);
        }

        function retryPending() {
            return performPending();
        }

        return { start, load, transition, retryPending, dispose, getState: () => ({ ...state, tickets: [...state.tickets] }) };
    }

    const api = Object.freeze({ createStationController, escapeHtml, ticketAgeMinutes, ticketAgeClass });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (global) global.FnbStationR1B = api;
    if (!global?.document) return;

    function mount() {
        if (!localStorage.getItem('token')) return redirectToLogin();
        const station = location.pathname.split('/').filter(Boolean).at(-1)?.toUpperCase();
        const role = localStorage.getItem('role');
        const staffRole = (localStorage.getItem('staff_role') || 'MANAGER').toUpperCase();
        if (!['KITCHEN', 'BAR'].includes(station)
            || (role === 'STAFF' && !['MANAGER', station].includes(staffRole))) {
            return navigateToPage('/fnb');
        }
        const title = document.getElementById('fnbStationTitle');
        const list = document.getElementById('fnbStationTickets');
        const status = document.getElementById('fnbStationStatus');
        const mutationStatus = document.getElementById('fnbStationMutationStatus');
        let readFailed = false;
        const tr = key => t('fnb.station.' + key);
        const connection = document.getElementById('fnbStationConnection');
        const shopSelect = document.getElementById('fnbStationShop');
        const retry = document.getElementById('fnbStationRetry');
        const stockDialog = document.getElementById('fnbOutOfStockDialog');
        const stockForm = document.getElementById('fnbOutOfStockForm');
        const stockReason = document.getElementById('fnbOutOfStockReason');
        let pendingStockTicketId = null;
        title.textContent = tr(station === 'KITCHEN' ? 'kitchen' : 'bar');

        function ticketCard(ticket) {
            const minutes = ticketAgeMinutes(ticket.created_at);
            const primary = ticket.out_of_stock_reason
                ? `<button type="button" data-action="resume">${escapeHtml(tr('resume'))}</button>`
                : ticket.status === 'NEW'
                    ? `<button type="button" data-action="start">${escapeHtml(tr('start'))}</button>`
                    : `<button type="button" data-action="done">${escapeHtml(tr('done'))}</button>`;
            const stock = ticket.out_of_stock_reason
                ? '' : `<button type="button" class="fnb-secondary" data-action="out-of-stock">${escapeHtml(tr('stock'))}</button>`;
            return `<article class="fnb-ticket-card ${ticketAgeClass(minutes)}" data-ticket-id="${Number(ticket.id)}" data-created-at="${escapeHtml(ticket.created_at || '')}"><header><div><span class="fnb-ticket-number">${escapeHtml(tr('ticket'))} #${Number(ticket.sequence)}</span><h3>${escapeHtml((ticket.tables || []).join(' + '))}</h3></div><span class="fnb-ticket-time">${minutes} ${tr('minutes')}</span></header><ul>${(ticket.items || []).map(item => `<li><strong class="fnb-ticket-quantity">${Number(item.quantity)}×</strong><span class="fnb-ticket-item">${escapeHtml(item.product_name)}</span>${item.note ? `<span class="fnb-ticket-note">${escapeHtml(item.note)}</span>` : ''}</li>`).join('')}</ul>${ticket.out_of_stock_reason ? `<p class="fnb-ticket-warning">${escapeHtml(tr('stock'))}: ${escapeHtml(ticket.out_of_stock_reason)}</p>` : ''}<footer>${primary}${stock}</footer></article>`;
        }

        function ticketLane(label, tickets) {
            return `<section class="fnb-ticket-lane"><header><h2 tabindex="-1">${label}</h2><span class="fnb-ticket-count">${tickets.length}</span></header><div class="fnb-ticket-list">${tickets.length ? tickets.map(ticketCard).join('') : `<p class="fnb-lane-empty">${escapeHtml(tr('empty'))}</p>`}</div></section>`;
        }

        function refreshTicketTimers() {
            list.querySelectorAll('[data-created-at]').forEach(card => {
                const minutes = ticketAgeMinutes(card.dataset.createdAt);
                card.classList.remove('is-warn', 'is-late');
                const ageClass = ticketAgeClass(minutes);
                if (ageClass) card.classList.add(ageClass);
                card.querySelector('.fnb-ticket-time').textContent = `${minutes} ${tr('minutes')}`;
            });
        }

        function render(event) {
            if (!localStorage.getItem('token')) return;
            if (event.type === 'loading') {
                readFailed = false;
                connection.className = 'fnb-live-badge is-syncing';
                connection.textContent = tr('loading');
                status.textContent = tr('loading');
                mutationStatus.textContent = '';
                list.innerHTML = '<div class="fnb-skeleton" aria-hidden="true"></div>';
            } else if (event.type === 'queue') {
                const focused = document.activeElement;
                const id = focused?.closest?.('[data-ticket-id]')?.dataset.ticketId;
                const action = focused?.dataset?.action;
                const tickets = event.value.tickets || [];
                list.innerHTML = ticketLane(tr('new'), tickets.filter(row => row.status === 'NEW'))
                    + ticketLane(tr('doing'), tickets.filter(row => row.status === 'IN_PROGRESS'));
                if (id && action) {
                    const replacement = list.querySelector(`[data-ticket-id="${Number(id)}"] [data-action="${action}"]`);
                    (replacement || list.querySelector('h2'))?.focus();
                }
                if (!controller.getState().pending) mutationStatus.textContent = '';
            } else if (event.type === 'synced') {
                readFailed = false;
                connection.className = 'fnb-live-badge is-online';
                connection.textContent = tr('synced') + ' ' + new Date(event.at).toLocaleTimeString(currentLanguage(), {hour:'2-digit',minute:'2-digit'});
                status.textContent = '';
            } else if (event.type === 'pending') {
                mutationStatus.textContent = tr('pending');
            } else if (event.type === 'blocked' || event.type === 'transition-error') {
                mutationStatus.textContent = controller.getState().pending
                    ? tr('unknown') : (event.error?.message || tr('rejected'));
                if (!controller.getState().pending) readFailed = true;
            } else if (event.type === 'error') {
                readFailed = true;
                connection.className = 'fnb-live-badge is-stale';
                connection.textContent = navigator.onLine ? tr('stale') : tr('offline');
                status.textContent = tr('read_error');
                if (!event.hasData) list.innerHTML = `<p class="fnb-empty">${escapeHtml(tr('read_error'))}</p>`;
            }
            const pending = controller.getState().pending;
            list.querySelectorAll('button[data-action]').forEach(button => { button.disabled = Boolean(pending); });
            shopSelect.disabled = Boolean(pending);
            retry.hidden = !pending && !readFailed;
            retry.disabled = Boolean(pending?.inFlight);
            retry.dataset.mode = pending ? 'mutation' : 'load';
            retry.textContent = pending ? tr('retry_mutation') : tr('retry_read');
        }

        const controller = createStationController({
            request: (endpoint, method, body) => apiCall(endpoint, method, body, {
                timeoutMs: method === 'GET' ? 15000 : 30000, slowAfterMs: 10000,
                onSlow: () => { if (!controller.getState().disposed) (method === 'GET' ? status : mutationStatus).textContent = t('common.request.slow'); }
            }),
            render,
            setTimeoutFn: (callback, wait) => setTimeout(callback, wait),
            clearTimeoutFn: timer => clearTimeout(timer),
            isHidden: () => document.hidden,
        });

        document.addEventListener('click', event => {
            const button = event.target.closest('[data-action]');
            if (!button) return;
            const ticketId = Number(button.closest('[data-ticket-id]')?.dataset.ticketId);
            const action = button.dataset.action;
            if (action === 'close-stock') {
                pendingStockTicketId = null;
                stockDialog.close();
                return;
            }
            let reason = null;
            if (action === 'out-of-stock') {
                pendingStockTicketId = ticketId;
                stockReason.value = '';
                stockDialog.showModal();
                stockReason.focus();
                return;
            }
            controller.transition(ticketId, action, reason?.trim()).catch(() => {});
        });
        stockForm.addEventListener('submit', event => {
            event.preventDefault();
            const reason = stockReason.value.trim();
            if (!pendingStockTicketId || !reason) return;
            controller.transition(pendingStockTicketId, 'out-of-stock', reason)
                .then(() => { pendingStockTicketId = null; stockDialog.close(); })
                .catch(() => {});
        });
        retry.addEventListener('click', () => (
            retry.dataset.mode === 'mutation' ? controller.retryPending() : controller.load(true)
        ).catch(() => {}));
        shopSelect.addEventListener('change', () => controller.start(Number(shopSelect.value), station).catch(() => {}));
        global.addEventListener('online', () => controller.load(true).catch(() => {}));
        global.addEventListener('offline', () => {
            render({type:'error',hasData:controller.getState().tickets.length > 0});
        });
        const clock = global.setInterval(refreshTicketTimers, 30_000);
        global.addEventListener('pagehide', () => {
            global.clearInterval(clock);
            controller.dispose();
        }, { once: true });

        apiCall('/shops', 'GET', null, {timeoutMs:15000}).then(shops => {
            const available = (shops || []).filter(shop => shop.is_active !== false && shop.fnb_enabled);
            shopSelect.innerHTML = available.map(shop => `<option value="${Number(shop.id)}">${escapeHtml(shop.name)}</option>`).join('');
            const saved = Number(localStorage.getItem('currentShopId'));
            const selected = available.find(shop => Number(shop.id) === saved) || available[0];
            if (!selected) return render({ type: 'error', error: new Error(tr('no_shop')) });
            shopSelect.value = String(selected.id);
            controller.start(selected.id, station).catch(() => {});
        }).catch(error => render({ type: 'error', error }));
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
    else mount();
})(typeof window === 'undefined' ? null : window);
