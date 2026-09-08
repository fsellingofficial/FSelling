(function (global) {
    'use strict';

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[character]));
    }

    function clone(value) {
        return typeof structuredClone === 'function'
            ? structuredClone(value)
            : JSON.parse(JSON.stringify(value));
    }

    function partitionLines(lines) {
        return {
            draft: (lines || []).filter(line => Number(line.unsent_quantity || 0) > 0),
            sent: (lines || []).filter(line => Number(line.active_sent_quantity || 0) > 0),
        };
    }

    function groupMenuProducts(products, query = '') {
        const needle = String(query || '').trim().toLocaleLowerCase();
        const matches = (products || []).filter(product => !needle || [
            product.name, product.variant_group, product.variant_name,
        ].some(value => String(value || '').toLocaleLowerCase().includes(needle)));
        const entries = [];
        const groupIndexes = new Map();
        matches.forEach(product => {
            if (!product.variant_group) {
                entries.push({ group: null, products: [product] });
                return;
            }
            const existing = groupIndexes.get(product.variant_group);
            if (existing !== undefined) {
                entries[existing].products.push(product);
                return;
            }
            groupIndexes.set(product.variant_group, entries.length);
            entries.push({ group: product.variant_group, products: [product] });
        });
        return entries;
    }

    function adjustmentValueForApi(kind, value) {
        return kind === 'PERCENT' ? Math.round(Number(value || 0) * 100) : Number(value || 0);
    }

    function adjustmentValueForForm(kind, value) {
        return kind === 'PERCENT' ? Number(value || 0) / 100 : Number(value || 0);
    }

    function cashTenderedForPayment(method, rawValue) {
        if (method !== 'cash') return {};
        const normalized = String(rawValue ?? '').trim();
        if (normalized === '') return { error: 'required' };
        return { cash_tendered_vnd: Number(normalized) };
    }

    function cashExactAllowed(voucherCode, loyaltyPoints) {
        return String(voucherCode || '').trim() === ''
            && Number(loyaltyPoints || 0) === 0;
    }

    function createController(deps) {
        const state = {
            shopId: null,
            floorRevision: null,
            floor: { areas: [] },
            setupFloor: { areas: [] },
            session: null,
            checks: null,
            requestEpoch: 0,
            pendingMutation: null,
            recoverableDraft: null,
            recoverableAction: null,
            pollTimer: null,
            disposed: false,
        };
        let pendingPromise = null;
        const canManageSetup = deps.role === 'SELLER'
            || (deps.role === 'STAFF' && deps.staffRole === 'MANAGER');
        const capabilities = Object.freeze({
            service: deps.role === 'SELLER'
                || (deps.role === 'STAFF'
                    && ['SERVICE', 'CASHIER', 'MANAGER'].includes(deps.staffRole)),
            checkout: deps.role === 'SELLER'
                || (deps.role === 'STAFF'
                    && ['CASHIER', 'MANAGER'].includes(deps.staffRole)),
            setup: canManageSetup,
        });
        const pollDelay = () => deps.isHidden?.() ? 10_000 : 2_000;
        const uuid = () => deps.uuid?.()
            || global?.crypto?.randomUUID?.()
            || `fnb-${Date.now()}-${Math.random().toString(16).slice(2)}`;

        deps.render({ type: 'setup-access', allowed: canManageSetup });
        deps.render({ type: 'role-access', capabilities });

        function findTable(tableId, source = state.floor) {
            for (const area of source.areas || []) {
                const table = (area.tables || []).find(row => Number(row.id) === Number(tableId));
                if (table) return table;
            }
            return null;
        }

        function draftKey() {
            return `fnb.draft:${deps.username || 'anonymous'}:${state.shopId}:${state.session?.id || 'none'}`;
        }

        function pendingKey(sessionId = state.session?.id, shopId = state.shopId) {
            return `fnb.pending:${deps.username || 'anonymous'}:${shopId}:${sessionId || 'none'}`;
        }

        function storageGet(key) {
            return deps.storage?.get?.(key) ?? deps.storage?.getItem?.(key);
        }

        function storageSet(key, value) {
            deps.storage?.set?.(key, value);
            deps.storage?.setItem?.(key, value);
        }

        function storageDelete(key) {
            deps.storage?.delete?.(key);
            deps.storage?.removeItem?.(key);
        }

        function saveDraft(value) {
            if (!state.session?.id) return;
            storageSet(draftKey(), JSON.stringify(value));
        }

        function clearDraft() {
            if (!state.session?.id) return;
            storageDelete(draftKey());
        }

        function getDraft() {
            if (!state.session?.id) return null;
            const raw = storageGet(draftKey());
            if (!raw) return null;
            try { return JSON.parse(raw); } catch (error) { return null; }
        }

        async function loadSession(sessionId) {
            const expectedShop = state.shopId;
            const result = await deps.request(`/fnb/sessions/${Number(sessionId)}`, 'GET');
            if (!result || state.disposed || expectedShop !== state.shopId) return;
            state.session = result;
            deps.render({ type: 'session', value: result, draft: getDraft(), saved: false });
            restorePending();
            return result;
        }

        async function refreshOpenSessionFromFloor() {
            if (!state.session || state.pendingMutation) return;
            const summaries = (state.floor.areas || []).flatMap(area =>
                (area.tables || []).map(table => table.session).filter(Boolean)
            );
            const summary = summaries.find(row => Number(row.id) === Number(state.session.id));
            if (!summary) {
                state.session = null;
                clearDraft();
                deps.render({ type: 'session-closed' });
            } else if (Number(summary.revision) !== Number(state.session.revision)) {
                await loadSession(summary.id);
            }
        }

        async function loadFloor(force = false) {
            const shopId = Number(state.shopId);
            if (!Number.isSafeInteger(shopId) || state.disposed) return;
            const epoch = ++state.requestEpoch;
            const suffix = !force && Number.isInteger(state.floorRevision)
                ? `&after_revision=${state.floorRevision}` : '';
            try {
                const result = await deps.request(`/fnb/floor?shop_id=${shopId}${suffix}`, 'GET');
                if (!result || state.disposed || epoch !== state.requestEpoch || shopId !== state.shopId) return;
                if (result.changed !== false) {
                    state.floor = result;
                    state.floorRevision = Number(result.fnb_revision);
                    deps.render({ type: 'floor', value: result });
                    await refreshOpenSessionFromFloor();
                } else {
                    deps.render({ type: 'floor-synced' });
                }
            } catch (error) {
                if (!state.disposed && epoch === state.requestEpoch && shopId === state.shopId) {
                    if ((error.code || error.detail?.code) === 'FNB_DISABLED') {
                        dispose();
                        state.floor = { areas: [] };
                        state.session = null;
                        deps.render({ type: 'feature-disabled', error });
                    } else {
                        deps.render({
                            type: 'floor-error',
                            error,
                            hasData: state.floor.areas.length > 0,
                        });
                    }
                }
            } finally {
                if (!state.disposed && epoch === state.requestEpoch && shopId === state.shopId) {
                    state.pollTimer = deps.setTimeoutFn(() => loadFloor(false), pollDelay());
                }
            }
        }

        async function loadSetup() {
            if (!canManageSetup || !Number.isSafeInteger(Number(state.shopId))) return;
            deps.render({ type: 'setup-loading' });
            try {
                const result = await deps.request(
                    `/fnb/floor?shop_id=${Number(state.shopId)}&include_inactive=true`,
                    'GET',
                );
                if (!result || state.disposed) return;
                state.setupFloor = result;
                state.floorRevision = Number(result.fnb_revision);
                deps.render({ type: 'setup', value: result });
                return result;
            } catch (error) {
                deps.render({ type: 'setup-error', error });
                throw error;
            }
        }

        async function loadChecks() {
            if (!state.session?.id) return;
            const sessionId = Number(state.session.id);
            const result = await deps.request(`/fnb/sessions/${sessionId}/checks`, 'GET');
            if (!result || state.disposed || sessionId !== Number(state.session?.id)) return;
            state.checks = result;
            state.session.revision = Number(result.session_revision);
            state.session.status = result.session_status;
            deps.render({ type: 'checks', value: result });
            return result;
        }

        async function selectShop(shopId) {
            deps.clearTimeoutFn(state.pollTimer);
            state.requestEpoch += 1;
            state.shopId = Number(shopId);
            state.floorRevision = null;
            state.floor = { areas: [] };
            state.setupFloor = { areas: [] };
            state.session = null;
            state.checks = null;
            state.recoverableDraft = null;
            state.recoverableAction = null;
            state.pendingMutation = null;
            pendingPromise = null;
            deps.render({ type: 'loading' });
            return loadFloor(true);
        }

        function clearPending() {
            if (state.pendingMutation?.storageKey) {
                storageDelete(state.pendingMutation.storageKey);
            }
            state.pendingMutation = null;
            pendingPromise = null;
        }

        function hasRecoverySecret(value) {
            if (!value || typeof value !== 'object') return false;
            return Object.entries(value).some(([key, item]) =>
                ['approval_token', 'pin'].includes(key)
                || (item && typeof item === 'object' && hasRecoverySecret(item))
            );
        }

        function persistPending() {
            const mutation = state.pendingMutation;
            if (!mutation || !state.session?.id || hasRecoverySecret(mutation.body)) return;
            const key = pendingKey(state.session.id, state.shopId);
            mutation.storageKey = key;
            storageSet(key, JSON.stringify({
                action: mutation.action,
                endpoint: mutation.endpoint,
                method: mutation.method,
                body: mutation.body,
                attempt: mutation.attempt,
                scope: mutation.scope,
                shopId: Number(state.shopId),
                sessionId: Number(state.session.id),
            }));
        }

        function restorePending() {
            if (state.pendingMutation || !state.session?.id) return;
            const key = pendingKey(state.session.id, state.shopId);
            const raw = storageGet(key);
            if (!raw) return;
            try {
                const mutation = JSON.parse(raw);
                if (
                    Number(mutation.shopId) !== Number(state.shopId)
                    || Number(mutation.sessionId) !== Number(state.session.id)
                    || hasRecoverySecret(mutation.body)
                ) {
                    storageDelete(key);
                    return;
                }
                state.pendingMutation = { ...mutation, storageKey: key, inFlight: false };
                deps.render({ type: 'mutation-recovery', value: clone(state.pendingMutation) });
            } catch (error) {
                storageDelete(key);
            }
        }

        function isDefinitive4xx(error) {
            const status = Number(error?.status);
            return status >= 400 && status < 500;
        }

        function errorCode(error) {
            return error?.code || error?.detail?.code || '';
        }

        function isCancelActionRequired(mutation, error) {
            return mutation.action === 'cancel-line' && [
                'FNB_CANCELLATION_DECISION_REQUIRED',
                'FNB_APPROVAL_REQUIRED',
            ].includes(errorCode(error));
        }

        function performPending() {
            if (!state.pendingMutation) return Promise.resolve();
            if (state.pendingMutation.inFlight && pendingPromise) return pendingPromise;
            state.pendingMutation.inFlight = true;
            deps.render({ type: 'mutation-pending', value: clone(state.pendingMutation) });
            const mutation = state.pendingMutation;
            pendingPromise = (async () => {
                try {
                    const result = await deps.request(
                        mutation.endpoint,
                        mutation.method,
                        mutation.body,
                    );
                    clearPending();
                    state.recoverableDraft = null;
                    state.recoverableAction = null;
                    if (mutation.scope === 'session') {
                        clearDraft();
                        if (result?.status === 'CANCELLED') {
                            state.session = null;
                            deps.render({ type: 'session-cancelled', value: result });
                        } else {
                            state.session = result;
                            deps.render({
                                type: 'session', value: result, draft: null,
                                saved: ['add-line', 'update-line', 'cancel-line'].includes(mutation.action),
                            });
                        }
                    } else if (mutation.scope === 'checks') {
                        if (result?.checks) {
                            state.checks = result;
                        } else if (result?.check && state.checks) {
                            state.checks = {
                                ...state.checks,
                                session_revision: Number(result.session_revision),
                                session_status: result.session_status,
                                checks: state.checks.checks.map(check =>
                                    Number(check.id) === Number(result.check.id) ? result.check : check
                                ),
                            };
                        }
                        if (state.session && Number.isInteger(Number(result?.session_revision))) {
                            state.session.revision = Number(result.session_revision);
                            state.session.status = result.session_status;
                        }
                        deps.render({ type: 'checks', value: state.checks, result });
                    } else if (mutation.scope === 'close-paid') {
                        clearDraft();
                        state.session = null;
                        state.checks = null;
                        deps.render({ type: 'session-closed', value: result });
                    } else if (mutation.scope === 'ticket') {
                        await loadSession(mutation.attempt.session_id);
                    } else {
                        if (Number.isInteger(Number(result?.fnb_revision))) {
                            state.floorRevision = Number(result.fnb_revision);
                        }
                        deps.render({ type: 'setup-success', value: result });
                    }
                    await loadFloor(true);
                    return result;
                } catch (error) {
                    const code = errorCode(error);
                    const definitive = isDefinitive4xx(error);
                    if (definitive) clearPending();
                    if (
                        mutation.action === 'cancel-line'
                        && (code === 'FNB_SESSION_CHANGED' || code === 'FNB_LINE_CHANGED')
                        && error?.detail?.snapshot
                    ) {
                        clearDraft();
                        state.session = error.detail.snapshot;
                        state.recoverableDraft = null;
                        deps.render({ type: 'cancel-conflict', value: state.session, error });
                        throw error;
                    }
                    if (isCancelActionRequired(mutation, error)) {
                        const attempt = clone(mutation.attempt);
                        clearDraft();
                        state.recoverableDraft = null;
                        deps.render({
                            type: 'cancel-action-required',
                            value: state.session,
                            error,
                            attempt,
                        });
                        throw error;
                    }
                    if (
                        (code === 'FNB_SESSION_CHANGED' || code === 'FNB_LINE_CHANGED')
                        && error?.detail?.snapshot
                    ) {
                        state.session = error.detail.snapshot;
                        state.recoverableDraft = clone(mutation.attempt);
                        state.recoverableAction = mutation.action;
                        saveDraft(state.recoverableDraft);
                        deps.render({
                            type: 'conflict',
                            value: state.session,
                            draft: state.recoverableDraft,
                        });
                    } else if (mutation.action === 'serve-ticket' && code === 'FNB_TICKET_CHANGED') {
                        await loadSession(mutation.attempt.session_id);
                        state.recoverableDraft = clone(mutation.attempt);
                        state.recoverableAction = mutation.action;
                        deps.render({
                            type: 'conflict',
                            value: state.session,
                            draft: state.recoverableDraft,
                        });
                    } else if (code === 'FNB_FLOOR_CHANGED') {
                        await loadFloor(true);
                        deps.render({
                            type: 'setup-error',
                            error,
                            value: clone(mutation.attempt),
                        });
                    } else if (code === 'FNB_DISABLED') {
                        dispose();
                        state.floor = { areas: [] };
                        state.session = null;
                        deps.render({ type: 'feature-disabled', error });
                    } else if (mutation.scope === 'setup') {
                        deps.render({
                            type: 'setup-error',
                            error,
                            value: clone(mutation.attempt),
                        });
                    } else if (mutation.scope === 'checks' || mutation.scope === 'close-paid') {
                        deps.render({ type: 'checkout-error', error });
                    } else {
                        state.recoverableDraft = clone(mutation.attempt);
                        state.recoverableAction = mutation.action;
                        if (definitive) saveDraft(state.recoverableDraft);
                        deps.render({
                            type: 'mutation-error',
                            error,
                            draft: state.recoverableDraft,
                        });
                    }
                    if (!definitive && state.pendingMutation) {
                        state.pendingMutation.inFlight = false;
                        pendingPromise = null;
                        persistPending();
                    }
                    throw error;
                }
            })();
            return pendingPromise;
        }

        function startMutation(action, endpoint, method, body, attempt, scope) {
            if (state.pendingMutation) {
                const error = new Error('A mutation is already pending');
                error.code = 'FNB_MUTATION_PENDING';
                deps.render({ type: 'mutation-blocked', value: clone(state.pendingMutation) });
                return Promise.reject(error);
            }
            const operationId = uuid();
            state.pendingMutation = {
                action,
                endpoint,
                method,
                body: { ...body, operation_id: operationId },
                operationId,
                attempt: clone(attempt),
                scope,
                inFlight: false,
            };
            persistPending();
            return performPending();
        }

        function retryPending() {
            return performPending();
        }

        function reapplyRecoverable() {
            const draft = clone(state.recoverableDraft);
            switch (state.recoverableAction) {
                case 'add-line': return addLine(draft);
                case 'update-line': return updateLine(draft.line_id, draft);
                case 'move-table': return moveTable(draft.from_table_id, draft.to_table_id);
                case 'merge-table': return mergeTable(draft.target_table_id);
                case 'send-session': return sendSession();
                case 'serve-ticket': return serveTicket(draft.ticket_id);
                default: return Promise.reject(new Error('No recoverable mutation'));
            }
        }

        function createArea(values) {
            return startMutation('create-area', '/fnb/areas', 'POST', {
                shop_id: Number(state.shopId),
                name: values.name,
                sort_order: Number(values.sort_order || 0),
                expected_revision: Number(state.floorRevision),
            }, values, 'setup');
        }

        function updateArea(areaId, values) {
            return startMutation('update-area', `/fnb/areas/${Number(areaId)}`, 'PATCH', {
                ...values,
                ...(values.sort_order === undefined ? {} : { sort_order: Number(values.sort_order) }),
                expected_revision: Number(state.floorRevision),
            }, values, 'setup');
        }

        function createTable(values) {
            return startMutation('create-table', '/fnb/tables', 'POST', {
                shop_id: Number(state.shopId),
                area_id: Number(values.area_id),
                name: values.name,
                sort_order: Number(values.sort_order || 0),
                expected_revision: Number(state.floorRevision),
            }, values, 'setup');
        }

        function updateTable(tableId, values) {
            const setupTable = findTable(tableId, state.setupFloor);
            const liveTable = findTable(tableId);
            const table = Number(liveTable?.state_version) > Number(setupTable?.state_version)
                ? liveTable : (setupTable || liveTable);
            return startMutation('update-table', `/fnb/tables/${Number(tableId)}`, 'PATCH', {
                ...values,
                ...(values.area_id === undefined ? {} : { area_id: Number(values.area_id) }),
                ...(values.sort_order === undefined ? {} : { sort_order: Number(values.sort_order) }),
                expected_revision: Number(state.floorRevision),
                expected_state_version: Number(table?.state_version || 0),
            }, values, 'setup');
        }

        function openTable(tableId) {
            const table = findTable(tableId);
            if (!table) return Promise.reject(new Error('Table unavailable'));
            if (table.session?.id) return loadSession(table.session.id);
            return startMutation('open-table', '/fnb/sessions', 'POST', {
                shop_id: Number(state.shopId),
                table_id: Number(table.id),
                expected_revision: Number(state.floorRevision),
                expected_table_version: Number(table.state_version),
            }, { table_id: Number(table.id) }, 'session');
        }

        function addLine(draft) {
            const attempt = { product_id: Number(draft.product_id), quantity: Number(draft.quantity), note: draft.note || '' };
            return startMutation('add-line', `/fnb/sessions/${Number(state.session.id)}/lines`, 'POST', {
                ...attempt,
                expected_revision: Number(state.session.revision),
            }, attempt, 'session');
        }

        function updateLine(lineId, draft) {
            const line = (state.session?.lines || []).find(row => Number(row.id) === Number(lineId));
            const attempt = { line_id: Number(lineId), quantity: Number(draft.quantity), note: draft.note || '' };
            return startMutation('update-line', `/fnb/lines/${Number(lineId)}`, 'PATCH', {
                quantity: attempt.quantity,
                note: attempt.note,
                expected_line_version: Number(line?.state_version || 0),
                expected_revision: Number(state.session.revision),
            }, attempt, 'session');
        }

        function cancelLine(lineId, quantity, details = {}) {
            const line = (state.session?.lines || []).find(row => Number(row.id) === Number(lineId));
            const attempt = { line_id: Number(lineId), quantity: Number(quantity), ...details };
            return startMutation('cancel-line', `/fnb/sessions/${Number(state.session.id)}/cancel-line`, 'POST', {
                ...attempt,
                expected_line_version: Number(line?.state_version || 0),
                expected_revision: Number(state.session.revision),
            }, attempt, 'session');
        }

        function sendSession() {
            return startMutation('send-session', `/fnb/sessions/${Number(state.session.id)}/send`, 'POST', {
                expected_revision: Number(state.session.revision),
            }, { session_id: Number(state.session.id) }, 'session');
        }

        function serveTicket(ticketId) {
            const ticket = (state.session?.service_tickets || []).find(
                row => Number(row.id) === Number(ticketId),
            );
            if (!ticket) return Promise.reject(new Error('Ticket unavailable'));
            const attempt = {
                ticket_id: Number(ticketId),
                session_id: Number(state.session.id),
            };
            return startMutation('serve-ticket', `/fnb/tickets/${Number(ticketId)}/serve`, 'POST', {
                expected_state_version: Number(ticket.state_version),
                expected_session_revision: Number(state.session.revision),
            }, attempt, 'ticket');
        }

        function updateProductStation(productId, station) {
            const attempt = { product_id: Number(productId), station };
            return startMutation('update-product-station', `/fnb/menu-items/${Number(productId)}/station`, 'PATCH', {
                station,
                expected_revision: Number(state.floorRevision),
            }, attempt, 'setup');
        }

        function moveTable(fromTableId, toTableId) {
            const source = (state.session?.tables || []).find(row => Number(row.id) === Number(fromTableId));
            const target = findTable(toTableId);
            const attempt = { from_table_id: Number(fromTableId), to_table_id: Number(toTableId) };
            return startMutation('move-table', `/fnb/sessions/${Number(state.session.id)}/move-table`, 'POST', {
                ...attempt,
                expected_revision: Number(state.session.revision),
                expected_from_state_version: Number(source?.state_version || 0),
                expected_to_state_version: Number(target?.state_version || 0),
            }, attempt, 'session');
        }

        function mergeTable(targetTableId) {
            const target = findTable(targetTableId);
            const attempt = { target_table_id: Number(targetTableId) };
            return startMutation('merge-table', `/fnb/sessions/${Number(state.session.id)}/merge-table`, 'POST', {
                ...attempt,
                expected_revision: Number(state.session.revision),
                expected_target_session_revision: target?.session
                    ? Number(target.session.revision) : null,
                expected_target_table_version: Number(target?.state_version || 0),
            }, attempt, 'session');
        }

        function cancelSession(reason = '') {
            const attempt = { reason };
            return startMutation('cancel-session', `/fnb/sessions/${Number(state.session.id)}/cancel`, 'POST', {
                expected_revision: Number(state.session.revision),
                reason,
            }, attempt, 'session');
        }

        function currentCheck(checkId) {
            return state.checks?.checks?.find(check => Number(check.id) === Number(checkId));
        }

        function splitCheck(checkId, lines, label) {
            const check = currentCheck(checkId);
            const attempt = { check_id: Number(checkId), lines, label };
            return startMutation('split-check', `/fnb/checks/${Number(checkId)}/split`, 'POST', {
                lines,
                label,
                expected_revision: Number(check?.revision),
                expected_session_revision: Number(state.checks?.session_revision ?? state.session?.revision),
            }, attempt, 'checks');
        }

        function updateCheckAdjustments(checkId, values) {
            const check = currentCheck(checkId);
            return startMutation('adjust-check', `/fnb/checks/${Number(checkId)}/adjustments`, 'PATCH', {
                ...values,
                expected_revision: Number(check?.revision),
                expected_session_revision: Number(state.checks?.session_revision ?? state.session?.revision),
            }, { check_id: Number(checkId), ...values }, 'checks');
        }

        function payCheck(checkId, values) {
            const check = currentCheck(checkId);
            return startMutation('pay-check', `/fnb/checks/${Number(checkId)}/pay`, 'POST', {
                ...values,
                expected_revision: Number(check?.revision),
                expected_session_revision: Number(state.checks?.session_revision ?? state.session?.revision),
            }, { check_id: Number(checkId), ...values }, 'checks');
        }

        function closePaidSession() {
            return startMutation('close-paid-session', `/fnb/sessions/${Number(state.session.id)}/close`, 'POST', {
                expected_revision: Number(state.checks?.session_revision ?? state.session?.revision),
            }, { session_id: Number(state.session.id) }, 'close-paid');
        }

        function dispose() {
            state.disposed = true;
            deps.clearTimeoutFn(state.pollTimer);
        }

        return {
            selectShop,
            loadFloor,
            loadSetup,
            loadSession,
            loadChecks,
            getState: () => clone(state),
            seedSession: sessionValue => { state.session = clone(sessionValue); },
            saveDraft,
            getDraft,
            clearDraft,
            retryPending,
            reapplyRecoverable,
            dispose,
            createArea,
            updateArea,
            createTable,
            updateTable,
            openTable,
            addLine,
            updateLine,
            cancelLine,
            sendSession,
            serveTicket,
            updateProductStation,
            moveTable,
            mergeTable,
            cancelSession,
            splitCheck,
            updateCheckAdjustments,
            payCheck,
            closePaidSession,
        };
    }

    const api = Object.freeze({
        createController, escapeHtml, partitionLines,
        adjustmentValueForApi, adjustmentValueForForm, groupMenuProducts,
        cashTenderedForPayment, cashExactAllowed,
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (global) global.FnbR1A = api;

    if (!global?.document) return;

    function mount() {
        const role = localStorage.getItem('role');
        const staffRole = (localStorage.getItem('staff_role') || 'MANAGER').toUpperCase();
        if (!localStorage.getItem('token')) return redirectToLogin();
        if (role === 'STAFF' && ['KITCHEN', 'BAR'].includes(staffRole)) {
            return navigateToPage(`/fnb/station/${staffRole.toLowerCase()}`);
        }
        if (role === 'STAFF' && staffRole === 'WAREHOUSE') {
            nhanSangTrangSau(t('fnb.auth.warehouse'));
            return navigateToPage('/pos');
        }
        if (!['SELLER', 'STAFF'].includes(role)) {
            nhanSangTrangSau(t('fnb.auth.no_access'));
            return navigateToPage('/pos');
        }
        if (role === 'STAFF' && staffRole === 'CASHIER') {
            document.querySelectorAll('.fnb-queue-link').forEach(link => { link.hidden = true; });
        }

        const elements = Object.fromEntries([
            'fnbShopSelect', 'fnbFloor', 'fnbFloorSummary', 'fnbAreaTabs', 'fnbLiveStatus', 'fnbRetry', 'fnbRefreshNote',
            'fnbSetupOpen', 'fnbSetupDialog', 'fnbSetupAreas', 'fnbSetupStatus',
            'fnbAreaForm', 'fnbAreaName', 'fnbTableForm', 'fnbTableArea', 'fnbTableName',
            'fnbSessionPanel', 'fnbSessionBackdrop', 'fnbSessionClose', 'fnbSessionTitle',
            'fnbProductSearch', 'fnbCategoryTabs', 'fnbProductGrid', 'fnbDraftLines', 'fnbSentLines', 'fnbSubtotal',
            'fnbServiceTickets',
            'fnbVariantDialog', 'fnbVariantTitle', 'fnbVariantHint', 'fnbVariantList',
            'fnbConflict', 'fnbSessionStatus', 'fnbTableActions', 'fnbTargetTable', 'fnbMergeTable',
            'fnbCancelSession', 'fnbSend', 'fnbStationList', 'fnbPinForm', 'fnbManagerPin',
            'fnbApprovalDialog', 'fnbApprovalForm', 'fnbApproverUsername', 'fnbApprovalPin',
            'fnbCancelResolution', 'fnbCancelReason', 'fnbApprovalStatus',
            'fnbCheckoutOpen', 'fnbCheckoutDialog', 'fnbCheckoutTitle', 'fnbCheckoutStatus', 'fnbCheckList',
            'fnbCheckDetail', 'fnbSplitPanel', 'fnbSplitForm', 'fnbSplitLines', 'fnbSplitLabel',
            'fnbSplitButton', 'fnbAdjustmentPanel', 'fnbAdjustmentForm', 'fnbDiscountKind',
            'fnbDiscountValue', 'fnbServiceKind', 'fnbServiceValue', 'fnbAdjustmentButton',
            'fnbPayForm', 'fnbCashTenderedField', 'fnbCashTendered', 'fnbCashExact',
            'fnbCashTenderedHelp', 'fnbCashTenderedError', 'fnbCustomerField',
            'fnbCustomer', 'fnbVoucherCode', 'fnbLoyaltyPoints', 'fnbPayButton',
            'fnbPrintProvisional', 'fnbClosePaidSession', 'fnbCheckoutHint',
            'fnbReceiptPrint', 'fnbRoleHandoff'
        ].map(id => [id, document.getElementById(id)]));
        let shops = [];
        let products = [];
        let categories = [];
        let selectedAreaId = null;
        let selectedCategoryId = null;
        let lastTableTrigger = null;
        let setupAllowed = false;
        let roleCapabilities = { service: true, checkout: true, setup: false };
        let pendingCancelLineId = null;
        let pendingCancelSessionRevision = null;
        let approvalDialogGeneration = 0;
        let selectedCheckId = null;
        let customers = [];
        let lastPaymentResult = null;
        let visibleMenuEntries = [];
        let selectedVariantEntry = null;
        let lastVariantTrigger = null;

        const storage = {
            get: key => sessionStorage.getItem(key),
            set: (key, value) => sessionStorage.setItem(key, value),
            delete: key => sessionStorage.removeItem(key),
        };

        function money(value) {
            return global.FSellingI18n?.formatMoney?.(value)
                || `${Number(value || 0).toLocaleString('vi-VN')} ₫`;
        }

        function live(key, options) {
            elements.fnbLiveStatus.textContent = key ? t(key, options) : '';
        }

        function setupStatus(message) {
            elements.fnbSetupStatus.textContent = message || '';
        }

        function sessionStatus(message) {
            elements.fnbSessionStatus.textContent = message || '';
        }

        function serviceBlockerCount(value = controller.getState().session) {
            const summary = value?.service_summary || {};
            return Number(summary.NEW || 0) + Number(summary.IN_PROGRESS || 0) + Number(summary.READY || 0);
        }

        function showPendingRecovery() {
            elements.fnbConflict.hidden = false;
            elements.fnbConflict.innerHTML = `<p>${escapeHtml(t('fnb.state.pending_retry'))}</p><button type="button" data-action="retry-pending">${escapeHtml(t('fnb.action.retry_pending'))}</button>`;
        }

        function resetApprovalDialog() {
            approvalDialogGeneration += 1;
            elements.fnbApprovalPin.value = '';
            elements.fnbApprovalStatus.textContent = '';
            elements.fnbCancelReason.value = '';
            elements.fnbCancelResolution.value = 'WASTE';
            pendingCancelLineId = null;
            pendingCancelSessionRevision = null;
        }

        function closeApprovalAfterConflict() {
            resetApprovalDialog();
            if (elements.fnbApprovalDialog.open) elements.fnbApprovalDialog.close();
        }

        function checkoutStatus(message) {
            elements.fnbCheckoutStatus.textContent = message || '';
        }

        function skeletons() {
            elements.fnbFloor.innerHTML = Array.from(
                { length: 6 },
                () => '<div class="fnb-skeleton" aria-hidden="true"></div>',
            ).join('');
            live('fnb.state.loading');
        }

        function emptyPanel(titleKey, bodyKey, action) {
            elements.fnbFloor.innerHTML = `<section class="fnb-empty"><h3>${escapeHtml(t(titleKey))}</h3><p>${escapeHtml(t(bodyKey))}</p>${action || ''}</section>`;
        }

        function floorTables(value) {
            return (value.areas || []).flatMap(area => area.tables || []);
        }

        function renderFloor(value) {
            const nonemptyAreas = (value.areas || []).filter(area => (area.tables || []).length);
            if (!nonemptyAreas.length) {
                elements.fnbFloorSummary.innerHTML = '';
                elements.fnbAreaTabs.innerHTML = '';
                const ownerAction = setupAllowed
                    ? `<button type="button" data-action="open-setup">${escapeHtml(t('fnb.setup.open'))}</button>`
                    : '';
                emptyPanel(
                    'fnb.state.no_tables',
                    setupAllowed ? 'fnb.state.no_tables_owner' : 'fnb.state.no_tables_staff',
                    ownerAction,
                );
                live('fnb.state.no_tables');
                return;
            }
            if (!nonemptyAreas.some(area => Number(area.id) === Number(selectedAreaId))) {
                selectedAreaId = nonemptyAreas[0].id;
            }
            elements.fnbAreaTabs.innerHTML = nonemptyAreas.map(area =>
                `<button type="button" data-action="select-area" data-id="${Number(area.id)}" aria-current="${Number(area.id) === Number(selectedAreaId)}">${escapeHtml(area.name)}</button>`
            ).join('');
            const area = nonemptyAreas.find(row => Number(row.id) === Number(selectedAreaId));
            const allTables = floorTables(value);
            const emptyCount = allTables.filter(table => table.state !== 'SERVING').length;
            const servingCount = allTables.length - emptyCount;
            const unsentCount = allTables.reduce(
                (sum, table) => sum + Number(table.session?.unsent_quantity || 0), 0,
            );
            const readyCount = allTables.reduce(
                (sum, table) => sum + Number(table.session?.service_summary?.READY || 0), 0,
            );
            elements.fnbFloorSummary.innerHTML = [
                ['fnb.floor.total', allTables.length, ''],
                ['fnb.floor.empty', emptyCount, ''],
                ['fnb.floor.serving', servingCount, 'is-serving'],
                ['fnb.floor.unsent', unsentCount, unsentCount ? 'is-attention' : ''],
                ['fnb.floor.ready', readyCount, readyCount ? 'is-ready' : ''],
            ].map(([key, count, className]) => `<div class="fnb-summary-card ${className}"><span>${escapeHtml(t(key))}</span><strong>${Number(count)}</strong></div>`).join('');
            elements.fnbFloor.innerHTML = (area.tables || []).map(table => {
                const serving = table.state === 'SERVING';
                const opened = table.session?.opened_at ? new Date(table.session.opened_at).getTime() : Date.now();
                const minutes = Math.max(0, Math.floor((Date.now() - opened) / 60000));
                const waiting = Number(table.session?.service_summary?.NEW || 0)
                    + Number(table.session?.service_summary?.IN_PROGRESS || 0);
                const ready = Number(table.session?.service_summary?.READY || 0);
                const service = [
                    waiting ? t('fnb.table.waiting', { count: waiting }) : '',
                    ready ? t('fnb.table.ready', { count: ready }) : '',
                ].filter(Boolean).map(label => `<span class="fnb-table-service">${escapeHtml(label)}</span>`).join('');
                return `<button type="button" class="fnb-table-card${serving ? ' is-serving' : ''}" data-action="open-table" data-id="${Number(table.id)}"><strong>${escapeHtml(table.name)}</strong><span class="fnb-table-state">${escapeHtml(t(serving ? 'fnb.table.serving' : 'fnb.table.empty'))}</span>${serving ? `<span class="fnb-table-meta"><span>${escapeHtml(t('fnb.table.elapsed', { minutes }))}</span><span class="fnb-table-total">${escapeHtml(money(table.session?.subtotal_vnd || 0))}</span><span class="fnb-table-unsent">${escapeHtml(t('fnb.table.unsent', { count: Number(table.session?.unsent_quantity || 0) }))}</span>${service}</span>` : ''}</button>`;
            }).join('');
            elements.fnbRetry.hidden = true;
            elements.fnbRefreshNote.hidden = true;
            live('');
        }

        function renderCategories() {
            const available = categories.filter(category => products.some(
                product => Number(product.category_id) === Number(category.id),
            ));
            if (selectedCategoryId && !available.some(category => Number(category.id) === Number(selectedCategoryId))) {
                selectedCategoryId = null;
            }
            elements.fnbCategoryTabs.innerHTML = [
                `<button type="button" data-action="select-menu-category" data-id="0" aria-current="${!selectedCategoryId}">${escapeHtml(t('fnb.menu.all'))}</button>`,
                ...available.map(category => `<button type="button" data-action="select-menu-category" data-id="${Number(category.id)}" aria-current="${Number(category.id) === Number(selectedCategoryId)}">${escapeHtml(category.name)}</button>`),
            ].join('');
        }

        function renderProducts() {
            const query = elements.fnbProductSearch.value;
            const categoryProducts = selectedCategoryId
                ? products.filter(product => Number(product.category_id) === Number(selectedCategoryId))
                : products;
            visibleMenuEntries = groupMenuProducts(categoryProducts, query);
            renderCategories();
            elements.fnbProductGrid.innerHTML = visibleMenuEntries.length
                ? visibleMenuEntries.map((entry, index) => {
                    const ids = entry.products.map(product => Number(product.id)).join(' ');
                    if (!entry.group) {
                        const product = entry.products[0];
                        const soldOut = Number(product.stock) <= 0;
                        return `<button type="button" data-action="add-product" data-id="${Number(product.id)}" data-product-ids="${ids}" ${soldOut ? 'disabled' : ''}><strong>${escapeHtml(product.name)}</strong><small class="fnb-product-price">${escapeHtml(money(product.price))}</small><small>${escapeHtml(soldOut ? t('fnb.menu.out_of_stock') : t('fnb.menu.stock', { count: Number(product.stock) }))}</small></button>`;
                    }
                    const prices = entry.products.map(product => Number(product.price) || 0);
                    const price = Math.min(...prices) === Math.max(...prices)
                        ? money(prices[0]) : `${money(Math.min(...prices))} – ${money(Math.max(...prices))}`;
                    const soldOut = entry.products.every(product => Number(product.stock) <= 0);
                    return `<button type="button" class="fnb-product-group" data-action="choose-variant" data-index="${index}" data-product-ids="${ids}" aria-haspopup="dialog" ${soldOut ? 'disabled' : ''}><strong>${escapeHtml(entry.group)}</strong><small class="fnb-product-price">${escapeHtml(price)}</small><span class="fnb-variant-count">${escapeHtml(soldOut ? t('fnb.menu.out_of_stock') : t('fnb.menu.variant_count', { count: entry.products.length }))}</span></button>`;
                }).join('')
                : `<p>${escapeHtml(t('fnb.menu.empty'))}</p>`;
        }

        function renderVariantDialog() {
            if (!selectedVariantEntry) return;
            elements.fnbVariantTitle.textContent = selectedVariantEntry.group;
            const available = selectedVariantEntry.products.filter(product => Number(product.stock) > 0);
            elements.fnbVariantHint.textContent = available.length
                ? t('fnb.menu.variant_help') : t('fnb.menu.variant_none');
            elements.fnbVariantList.innerHTML = selectedVariantEntry.products.map(product => {
                const soldOut = Number(product.stock) <= 0;
                return `<button type="button" class="fnb-variant-option" data-action="add-variant" data-id="${Number(product.id)}" ${soldOut ? 'disabled' : ''}><span><strong>${escapeHtml(product.variant_name || product.name)}</strong><small>${escapeHtml(t(`fnb.station.${String(product.fnb_station || 'DIRECT').toLowerCase()}`))}</small></span><span class="fnb-variant-price"><strong>${escapeHtml(money(product.price))}</strong><small>${escapeHtml(soldOut ? t('fnb.menu.out_of_stock') : t('fnb.menu.stock', { count: Number(product.stock) }))}</small></span></button>`;
            }).join('');
        }

        function openVariantDialog(entry, trigger) {
            selectedVariantEntry = entry;
            lastVariantTrigger = trigger;
            renderVariantDialog();
            elements.fnbVariantDialog.showModal();
            (elements.fnbVariantList.querySelector('button:not(:disabled)')
                || elements.fnbVariantDialog.querySelector('.fnb-icon-button')).focus();
        }

        function renderTargets() {
            const state = controller.getState();
            const attached = new Set((state.session?.tables || []).map(table => Number(table.id)));
            const tables = floorTables(state.floor).filter(table => !attached.has(Number(table.id)));
            elements.fnbTargetTable.innerHTML = `<option value="">${escapeHtml(t('fnb.table_actions.select'))}</option>${tables.map(table => `<option value="${Number(table.id)}">${escapeHtml(table.name)} — ${escapeHtml(t(table.state === 'SERVING' ? 'fnb.table.serving' : 'fnb.table.empty'))}</option>`).join('')}`;
        }

        function renderServiceTickets(value) {
            const tickets = value.service_tickets || [];
            const pending = controller.getState().pendingMutation;
            const groups = [
                ['waiting', tickets.filter(ticket => ['NEW', 'IN_PROGRESS'].includes(ticket.service_stage))],
                ['ready', tickets.filter(ticket => ticket.service_stage === 'READY')],
                ['served', tickets.filter(ticket => ticket.service_stage === 'SERVED')],
            ];
            elements.fnbServiceTickets.innerHTML = groups.map(([stage, rows]) => `
                <section class="fnb-service-group is-${stage}">
                    <h4>${escapeHtml(t(`fnb.service.${stage}`))} <span>${rows.length}</span></h4>
                    ${rows.length ? rows.map(ticket => `
                        <article class="fnb-service-ticket">
                            <header><strong>${escapeHtml(t('fnb.service.ticket', { sequence: Number(ticket.sequence) }))}</strong><span>${escapeHtml(t(`fnb.station.${String(ticket.station).toLowerCase()}`))}</span></header>
                            <p>${(ticket.items || []).map(item => `${Number(item.quantity)}× ${escapeHtml(item.product_name)}`).join(' · ')}</p>
                            ${ticket.out_of_stock_reason ? `<p class="fnb-service-warning">${escapeHtml(t('fnb.service.out_of_stock', { reason: ticket.out_of_stock_reason }))}</p>` : ''}
                            ${stage === 'ready' ? `<button type="button" data-action="serve-ticket" data-id="${Number(ticket.id)}" ${pending ? 'disabled' : ''}>${escapeHtml(t('fnb.service.mark_served'))}</button>` : ''}
                        </article>
                    `).join('') : `<p class="fnb-service-empty">${escapeHtml(t('fnb.service.empty'))}</p>`}
                </section>
            `).join('');
        }

        function renderSession(value, draft) {
            if (!value) return;
            document.body.classList.add('fnb-order-open');
            elements.fnbSessionPanel.hidden = false;
            elements.fnbSessionPanel.inert = false;
            elements.fnbSessionBackdrop.hidden = true;
            elements.fnbConflict.hidden = true;
            elements.fnbConflict.innerHTML = '';
            elements.fnbSessionTitle.textContent = (value.tables || []).map(table => table.name).join(' + ');
            const pending = controller.getState().pendingMutation;
            const buckets = partitionLines(value.lines || []);
            const serverLines = buckets.draft.map(line => {
                    const saved = draft?.line_id === line.id ? draft : null;
                    const unsynced = pending?.attempt?.line_id === line.id || saved;
                    return `<article class="fnb-draft-row" data-line-id="${Number(line.id)}"><header><strong>${escapeHtml(line.product_name || `#${line.product_id}`)}</strong>${unsynced ? `<span class="fnb-unsynced">${escapeHtml(t('fnb.state.unsynced'))}</span>` : ''}</header><div class="fnb-draft-edit"><label><span>${escapeHtml(t('fnb.draft.quantity'))}</span><span class="fnb-quantity-stepper"><button type="button" class="fnb-secondary" data-action="quantity-minus" data-id="${Number(line.id)}" aria-label="${escapeHtml(t('fnb.action.decrease'))}">−</button><input data-field="quantity" type="number" min="1" inputmode="numeric" value="${Number(saved?.quantity ?? line.quantity)}" aria-label="${escapeHtml(t('fnb.draft.quantity'))}"><button type="button" class="fnb-secondary" data-action="quantity-plus" data-id="${Number(line.id)}" aria-label="${escapeHtml(t('fnb.action.increase'))}">+</button></span></label><label><span>${escapeHtml(t('fnb.draft.note'))}</span><input data-field="note" maxlength="500" value="${escapeHtml(saved?.note ?? line.note ?? '')}" placeholder="${escapeHtml(t('fnb.draft.note_placeholder'))}" aria-label="${escapeHtml(t('fnb.draft.note'))}"></label></div><button type="button" class="fnb-secondary" data-action="cancel-line" data-id="${Number(line.id)}">${escapeHtml(t('fnb.action.cancel_quantity'))}</button></article>`;
                }).join('');
            const localProduct = draft?.product_id && !draft.line_id
                ? products.find(product => Number(product.id) === Number(draft.product_id))
                : null;
            const localLine = localProduct
                ? `<article class="fnb-draft-row"><header><strong>${escapeHtml(localProduct.name)}</strong><span class="fnb-unsynced">${escapeHtml(t('fnb.state.unsynced'))}</span></header><div class="fnb-draft-edit"><input type="number" min="1" inputmode="numeric" value="${Number(draft.quantity)}" disabled aria-label="${escapeHtml(t('fnb.draft.quantity'))}"><input value="${escapeHtml(draft.note || '')}" disabled aria-label="${escapeHtml(t('fnb.draft.note'))}"><button type="button" data-action="reapply">${escapeHtml(t('fnb.action.retry'))}</button></div></article>`
                : '';
            elements.fnbDraftLines.innerHTML = serverLines || localLine
                ? serverLines + localLine
                : `<p>${escapeHtml(t('fnb.draft.empty'))}</p>`;
            elements.fnbSentLines.innerHTML = buckets.sent.length
                ? buckets.sent.map(line => `<article class="fnb-draft-row fnb-sent-row" data-line-id="${Number(line.id)}"><header><strong>${escapeHtml(line.product_name || `#${line.product_id}`)}</strong><span class="fnb-station-chip">${escapeHtml(t(`fnb.station.${String(line.station || 'DIRECT').toLowerCase()}`))}</span></header><p class="fnb-line-meta">${escapeHtml(t('fnb.sent.quantity', { count: Number(line.active_sent_quantity || 0) }))}${line.note ? ` · ${escapeHtml(line.note)}` : ''}</p><button type="button" class="fnb-secondary" data-action="cancel-line" data-id="${Number(line.id)}">${escapeHtml(t('fnb.action.cancel_quantity'))}</button></article>`).join('')
                : `<p>${escapeHtml(t('fnb.sent.empty'))}</p>`;
            renderServiceTickets(value);
            elements.fnbSubtotal.textContent = t('fnb.session.total', { amount: money(value.subtotal_vnd) });
            elements.fnbTableActions.hidden = !roleCapabilities.service;
            elements.fnbCancelSession.disabled = Number(value.subtotal_vnd || 0) > 0;
            elements.fnbSend.disabled = Number(value.unsent_quantity || 0) <= 0 || Boolean(pending);
            const sentQuantity = buckets.sent.reduce((sum, line) => sum + Number(line.active_sent_quantity || 0), 0);
            elements.fnbCheckoutOpen.disabled = sentQuantity <= 0 || Number(value.unsent_quantity || 0) > 0 || Boolean(pending);
            elements.fnbCheckoutOpen.title = Number(value.unsent_quantity || 0) > 0 ? t('fnb.checkout.unsent_block') : '';
            elements.fnbCheckoutHint.hidden = Number(value.unsent_quantity || 0) <= 0;
            elements.fnbCheckoutHint.textContent = elements.fnbCheckoutHint.hidden ? '' : t('fnb.checkout.unsent_block_count', { count: Number(value.unsent_quantity || 0) });
            if (controller.getState().checks) renderChecks(controller.getState().checks);
            renderTargets();
            renderProducts();
        }

        function closeSession() {
            closeApprovalAfterConflict();
            document.body.classList.remove('fnb-order-open');
            elements.fnbSessionPanel.hidden = true;
            elements.fnbSessionPanel.inert = true;
            elements.fnbSessionBackdrop.hidden = true;
            elements.fnbConflict.hidden = true;
            lastTableTrigger?.focus();
        }

        function checkStatusKey(status) {
            return `fnb.checkout.status.${String(status || 'OPEN').toLowerCase()}`;
        }

        function clearCashPaymentIntent() {
            lastPaymentResult = null;
            elements.fnbCashTendered.value = '';
            elements.fnbCashTenderedError.textContent = '';
        }

        function selectCheck(id) {
            if (Number(selectedCheckId) === Number(id)) return;
            selectedCheckId = id;
            clearCashPaymentIntent();
        }

        function activeCheck() {
            const checks = controller.getState().checks?.checks || [];
            return checks.find(check => Number(check.id) === Number(selectedCheckId)) || checks[0];
        }

        function renderChecks(value) {
            const checks = value?.checks || [];
            if (!checks.length) {
                selectCheck(null);
                elements.fnbCheckList.innerHTML = '';
                elements.fnbCheckDetail.innerHTML = `<p>${escapeHtml(t('fnb.checkout.no_checks'))}</p>`;
                updateCashControls();
                return;
            }
            if (!checks.some(check => Number(check.id) === Number(selectedCheckId))) {
                selectCheck((checks.find(check => check.status === 'OPEN') || checks[0]).id);
            }
            const check = checks.find(row => Number(row.id) === Number(selectedCheckId));
            const pending = Boolean(controller.getState().pendingMutation);
            const editable = check.status === 'OPEN' && !pending;
            elements.fnbCheckList.innerHTML = checks.map(row =>
                `<button type="button" class="fnb-check-tab${Number(row.id) === Number(check.id) ? ' is-selected' : ''}" data-action="select-check" data-id="${Number(row.id)}" aria-pressed="${Number(row.id) === Number(check.id)}"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(money(row.total_vnd))}</strong><small>${escapeHtml(t(checkStatusKey(row.status)))}</small></button>`
            ).join('');
            const payment = lastPaymentResult?.check?.id === check.id ? lastPaymentResult.order : null;
            const qr = payment?.qr_url
                ? `<div class="fnb-payment-result"><strong>${escapeHtml(t('fnb.checkout.transfer_wait'))}</strong><img src="${escapeHtml(payment.qr_url)}" alt="VietQR"></div>` : '';
            const finalReceiptUrl = Number(check.order_id) > 0 && ['PAID', 'DEBT'].includes(check.status)
                ? `/pos?receipt=${Number(check.order_id)}` : '';
            const finalReceipt = finalReceiptUrl
                ? `<div class="fnb-payment-result"><a class="fnb-queue-link" href="${finalReceiptUrl}">${escapeHtml(t('fnb.checkout.final_receipt'))}</a></div>` : '';
            const paid = ['PAID', 'DEBT'].includes(check.status);
            const documentLabel = paid ? t('fnb.checkout.paid_label') : t('fnb.checkout.provisional_label');
            elements.fnbCheckDetail.innerHTML = `<div class="fnb-receipt-head"><span class="${paid ? 'is-paid' : ''}">${escapeHtml(documentLabel)}</span><strong>${escapeHtml(check.label)}</strong></div><div class="fnb-receipt-lines">${check.lines.map(line => `<div><span>${escapeHtml(line.product_name)} × ${Number(line.quantity)}</span><strong>${escapeHtml(money(Number(line.unit_price_vnd) * Number(line.quantity)))}</strong></div>`).join('')}</div><dl class="fnb-check-totals"><div><dt>${escapeHtml(t('fnb.checkout.subtotal'))}</dt><dd>${escapeHtml(money(check.subtotal_vnd))}</dd></div>${Number(check.discount_vnd) ? `<div><dt>${escapeHtml(t('fnb.checkout.discount'))}</dt><dd>−${escapeHtml(money(check.discount_vnd))}</dd></div>` : ''}${Number(check.service_charge_vnd) ? `<div><dt>${escapeHtml(t('fnb.checkout.service_charge'))}</dt><dd>${escapeHtml(money(check.service_charge_vnd))}</dd></div>` : ''}<div class="is-total"><dt>${escapeHtml(t(paid ? 'fnb.checkout.paid_total' : 'fnb.checkout.total'))}</dt><dd>${escapeHtml(money(check.total_vnd))}</dd></div></dl>${qr}${finalReceipt}`;
            elements.fnbSplitLines.innerHTML = check.lines.map(line => `<label class="fnb-split-line" data-line-id="${Number(line.line_id)}"><input type="checkbox" ${editable ? '' : 'disabled'}><span>${escapeHtml(line.product_name)} · ${Number(line.quantity)}</span><input type="number" min="1" max="${Number(line.quantity)}" value="1" inputmode="numeric" aria-label="${escapeHtml(t('fnb.draft.quantity'))}" ${editable ? '' : 'disabled'}></label>`).join('');
            elements.fnbSplitLabel.disabled = !editable;
            elements.fnbSplitButton.disabled = !editable || check.lines.length < 2 && Number(check.lines[0]?.quantity || 0) < 2;
            elements.fnbDiscountKind.value = check.discount_kind;
            elements.fnbDiscountValue.value = adjustmentValueForForm(check.discount_kind, check.discount_value);
            elements.fnbServiceKind.value = check.service_charge_kind;
            elements.fnbServiceValue.value = adjustmentValueForForm(check.service_charge_kind, check.service_charge_value);
            elements.fnbAdjustmentForm.querySelectorAll('input, select, button').forEach(control => { control.disabled = !editable; });
            elements.fnbPayForm.querySelectorAll('input, select, button').forEach(control => { control.disabled = !editable || !navigator.onLine; });
            updateCashControls();
            elements.fnbPrintProvisional.disabled = false;
            const terminal = checks.every(row => ['PAID', 'DEBT', 'CANCELLED'].includes(row.status));
            const serviceBlockers = serviceBlockerCount();
            elements.fnbClosePaidSession.disabled = pending || !terminal || serviceBlockers > 0;
            elements.fnbClosePaidSession.title = serviceBlockers
                ? t('fnb.checkout.service_block', { count: serviceBlockers }) : '';
            if (terminal && serviceBlockers && !pending) {
                checkoutStatus(t('fnb.checkout.service_block', { count: serviceBlockers }));
            }
            if (!navigator.onLine) checkoutStatus(t('fnb.checkout.offline'));
        }

        function renderSetup(value) {
            const areas = value.areas || [];
            const areaOptions = areas.filter(area => area.active !== false).map(area =>
                `<option value="${Number(area.id)}">${escapeHtml(area.name)}</option>`
            ).join('');
            elements.fnbTableArea.innerHTML = areaOptions;
            elements.fnbSetupAreas.innerHTML = areas.length ? areas.map(area => {
                const areaState = area.active === false ? 'fnb.setup.inactive' : 'fnb.setup.active';
                const tables = (area.tables || []).map(table => {
                    const occupied = table.state === 'SERVING';
                    return `<div class="fnb-setup-row" data-kind="table" data-id="${Number(table.id)}"><input data-field="name" maxlength="100" value="${escapeHtml(table.name)}" aria-label="${escapeHtml(t('fnb.setup.table_name'))}"><input data-field="sort_order" type="number" min="0" value="${Number(table.sort_order || 0)}" aria-label="${escapeHtml(t('fnb.setup.sort_order'))}"><button type="button" class="fnb-secondary" data-action="save-table">${escapeHtml(t('fnb.action.save'))}</button><button type="button" class="fnb-secondary" data-action="toggle-table" data-active="${table.active !== false}" ${occupied ? 'disabled' : ''}>${escapeHtml(t(table.active === false ? 'fnb.action.restore' : 'fnb.action.hide'))}</button>${occupied ? `<small>${escapeHtml(t('fnb.setup.occupied'))}</small>` : ''}</div>`;
                }).join('');
                return `<article class="fnb-setup-area${area.active === false ? ' is-inactive' : ''}" data-kind="area" data-id="${Number(area.id)}"><h3>${escapeHtml(area.name)} · ${escapeHtml(t(areaState))}</h3><div class="fnb-setup-row"><input data-field="name" maxlength="100" value="${escapeHtml(area.name)}" aria-label="${escapeHtml(t('fnb.setup.area_name'))}"><input data-field="sort_order" type="number" min="0" value="${Number(area.sort_order || 0)}" aria-label="${escapeHtml(t('fnb.setup.sort_order'))}"><button type="button" class="fnb-secondary" data-action="save-area">${escapeHtml(t('fnb.action.save'))}</button><button type="button" class="fnb-secondary" data-action="toggle-area" data-active="${area.active !== false}">${escapeHtml(t(area.active === false ? 'fnb.action.restore' : 'fnb.action.hide'))}</button></div>${tables}</article>`;
            }).join('') : `<p>${escapeHtml(t('fnb.state.no_tables'))}</p>`;
            elements.fnbStationList.innerHTML = products.length
                ? products.map(product => `<div class="fnb-station-row" data-product-id="${Number(product.id)}"><strong>${escapeHtml(product.name)}</strong><select aria-label="${escapeHtml(t('fnb.station.title'))}"><option value="KITCHEN" ${product.fnb_station === 'KITCHEN' ? 'selected' : ''}>${escapeHtml(t('fnb.station.kitchen'))}</option><option value="BAR" ${product.fnb_station === 'BAR' ? 'selected' : ''}>${escapeHtml(t('fnb.station.bar'))}</option><option value="DIRECT" ${!product.fnb_station || product.fnb_station === 'DIRECT' ? 'selected' : ''}>${escapeHtml(t('fnb.station.direct'))}</option></select><button type="button" class="fnb-secondary" data-action="save-station">${escapeHtml(t('fnb.action.save'))}</button></div>`).join('')
                : `<p>${escapeHtml(t('fnb.menu.empty'))}</p>`;
            setupStatus('');
        }

        function renderer(event) {
            if (event.type === 'setup-access') {
                setupAllowed = event.allowed;
                elements.fnbSetupOpen.hidden = !(setupAllowed && shops.length);
            } else if (event.type === 'role-access') {
                roleCapabilities = event.capabilities;
                elements.fnbCheckoutOpen.hidden = !roleCapabilities.checkout;
                elements.fnbPinForm.hidden = !roleCapabilities.setup;
                elements.fnbMergeTable.hidden = !roleCapabilities.setup;
                elements.fnbRoleHandoff.hidden = roleCapabilities.checkout;
                elements.fnbRoleHandoff.textContent = roleCapabilities.checkout
                    ? '' : t('fnb.auth.service_handoff');
            } else if (event.type === 'loading') {
                skeletons();
            } else if (event.type === 'floor') {
                renderFloor(event.value);
            } else if (event.type === 'floor-synced') {
                elements.fnbRetry.hidden = true;
                elements.fnbRefreshNote.hidden = true;
                live('');
            } else if (event.type === 'floor-error') {
                if (Number(event.error?.status) === 403) {
                    controller.dispose();
                    elements.fnbFloor.innerHTML = '';
                    nhanSangTrangSau(t('fnb.state.permission_lost'));
                    return navigateToPage('/pos');
                }
                elements.fnbRetry.hidden = false;
                elements.fnbRefreshNote.hidden = false;
                elements.fnbRefreshNote.textContent = t('fnb.state.poll_error');
                if (!event.hasData) {
                    emptyPanel('fnb.state.poll_error', 'fnb.state.poll_error_empty');
                }
                live('fnb.state.poll_error');
            } else if (event.type === 'session') {
                const staleApproval = elements.fnbApprovalDialog.open
                    && pendingCancelSessionRevision !== null
                    && Number(event.value?.revision) !== pendingCancelSessionRevision;
                renderSession(event.value, event.draft);
                if (staleApproval) {
                    closeApprovalAfterConflict();
                    sessionStatus(t('fnb.cancel.changed'));
                } else {
                    sessionStatus(event.saved ? t('fnb.session.saved') : '');
                }
            } else if (event.type === 'session-closed' || event.type === 'session-cancelled') {
                if (elements.fnbCheckoutDialog.open) elements.fnbCheckoutDialog.close();
                closeSession();
                showToast(t(event.type === 'session-cancelled' ? 'fnb.session.cancelled' : 'fnb.checkout.close_done'));
            } else if (event.type === 'checks') {
                if (event.result?.order) lastPaymentResult = event.result;
                renderChecks(event.value);
                checkoutStatus(event.result?.order ? t(
                    event.result.check.status === 'PAYMENT_PENDING' ? 'fnb.checkout.pay_pending' : 'fnb.checkout.paid'
                ) : '');
            } else if (event.type === 'mutation-pending') {
                sessionStatus(t('fnb.state.pending'));
                elements.fnbConflict.hidden = false;
                elements.fnbConflict.innerHTML = `<p>${escapeHtml(t('fnb.state.pending'))}</p>`;
                if (['checks', 'close-paid'].includes(event.value.scope)) {
                    checkoutStatus(t('fnb.state.pending'));
                    if (controller.getState().checks) renderChecks(controller.getState().checks);
                }
                const mutation = event.value;
                const affected = mutation.action === 'open-table'
                    ? document.querySelector(`[data-action="open-table"][data-id="${Number(mutation.attempt.table_id)}"]`)
                    : mutation.attempt?.ticket_id
                        ? elements.fnbServiceTickets.querySelector(`[data-action="serve-ticket"][data-id="${Number(mutation.attempt.ticket_id)}"]`)
                    : mutation.attempt?.line_id
                        ? elements.fnbDraftLines.querySelector(`[data-line-id="${Number(mutation.attempt.line_id)}"]`)
                        : mutation.attempt?.product_id
                            ? elements.fnbProductGrid.querySelector(`[data-product-ids~="${Number(mutation.attempt.product_id)}"]`)
                            : null;
                if (affected?.matches?.('button')) affected.disabled = true;
                affected?.querySelectorAll?.('button, input, select').forEach(control => { control.disabled = true; });
            } else if (event.type === 'mutation-recovery' || event.type === 'mutation-blocked') {
                sessionStatus(t('fnb.state.pending_retry'));
                showPendingRecovery();
                if (controller.getState().checks) checkoutStatus(t('fnb.state.pending_retry'));
            } else if (event.type === 'cancel-action-required') {
                renderSession(event.value, null);
                sessionStatus(t('fnb.cancel.action_required'));
            } else if (event.type === 'cancel-conflict') {
                renderSession(event.value, null);
                closeApprovalAfterConflict();
                sessionStatus(t('fnb.cancel.changed'));
            } else if (event.type === 'mutation-error') {
                sessionStatus(`${t('fnb.state.unsynced')}. ${event.error?.message || t('fnb.action.retry')}`);
                renderSession(controller.getState().session, event.draft);
                if (controller.getState().pendingMutation) showPendingRecovery();
            } else if (event.type === 'checkout-error') {
                const code = event.error?.code || event.error?.detail?.code;
                const tickets = event.error?.detail?.tickets || [];
                checkoutStatus(['FNB_ACTIVE_TICKETS', 'FNB_UNSERVED_TICKETS'].includes(code)
                    ? t('fnb.checkout.service_block', { count: tickets.length })
                    : event.error?.message || t('fnb.checkout.error'));
                if (controller.getState().checks) renderChecks(controller.getState().checks);
                elements.fnbCheckoutStatus.focus();
            } else if (event.type === 'conflict') {
                renderSession(event.value, event.draft);
                elements.fnbConflict.hidden = false;
                elements.fnbConflict.innerHTML = `<p>${escapeHtml(t('fnb.state.conflict'))}</p><button type="button" data-action="reapply">${escapeHtml(t('fnb.action.reapply'))}</button>`;
            } else if (event.type === 'setup-loading') {
                setupStatus(t('fnb.setup.loading'));
            } else if (event.type === 'setup') {
                renderSetup(event.value);
            } else if (event.type === 'setup-success') {
                setupStatus(t('fnb.setup.saved'));
                controller.loadSetup().catch(() => {});
            } else if (event.type === 'setup-error') {
                const code = event.error?.code || event.error?.detail?.code;
                setupStatus(t(code === 'FNB_NAME_EXISTS' ? 'fnb.setup.duplicate' : code === 'FNB_FLOOR_CHANGED' ? 'fnb.setup.changed' : 'fnb.state.poll_error'));
            } else if (event.type === 'feature-disabled') {
                elements.fnbFloor.innerHTML = '';
                if (elements.fnbCheckoutDialog.open) elements.fnbCheckoutDialog.close();
                closeSession();
                nhanSangTrangSau(t('fnb.auth.feature_disabled'));
                navigateToPage('/pos');
            }
        }

        const controller = createController({
            request: (endpoint, method, body) => apiCall(endpoint, method, body),
            render: renderer,
            setTimeoutFn: (callback, delay) => setTimeout(callback, delay),
            clearTimeoutFn: timer => clearTimeout(timer),
            isHidden: () => document.hidden,
            storage,
            now: () => Date.now(),
            username: localStorage.getItem('username') || 'anonymous',
            role,
            staffRole,
        });

        async function loadProducts() {
            const shopId = Number(elements.fnbShopSelect.value);
            try {
                const result = await apiCall(`/products/${shopId}`);
                products = (result || []).filter(product => product.is_active !== false && product.category_is_active !== false);
                renderProducts();
            } catch (error) {
                products = [];
                renderProducts();
            }
        }

        async function loadCategories() {
            try {
                const result = await apiCall(`/categories/${Number(elements.fnbShopSelect.value)}`);
                categories = (result || []).filter(category => category.is_active !== false);
            } catch (error) {
                categories = [];
            }
            renderProducts();
        }

        async function loadCustomers() {
            try {
                customers = await apiCall(`/customers/${Number(elements.fnbShopSelect.value)}`) || [];
            } catch (error) {
                customers = [];
            }
            elements.fnbCustomer.innerHTML = `<option value="">${escapeHtml(t('fnb.checkout.customer_select'))}</option>${customers.map(customer => `<option value="${Number(customer.id)}">${escapeHtml(customer.name)} · ${escapeHtml(customer.phone || '')}</option>`).join('')}`;
        }

        function paymentMethod() {
            return elements.fnbPayForm.querySelector('[name="fnbPaymentMethod"]:checked')?.value || 'cash';
        }

        function updateCashControls({ clearTender = false } = {}) {
            const method = paymentMethod();
            const cash = method === 'cash';
            const check = activeCheck();
            const editable = check?.status === 'OPEN'
                && !controller.getState().pendingMutation
                && navigator.onLine;
            const exactAllowed = cashExactAllowed(
                elements.fnbVoucherCode.value,
                elements.fnbLoyaltyPoints.value,
            );
            if (clearTender) elements.fnbCashTendered.value = '';
            elements.fnbCashTenderedField.hidden = !cash;
            elements.fnbCashExact.disabled = !editable || !cash || !exactAllowed;
            elements.fnbCashTenderedHelp.textContent = t(
                exactAllowed ? 'fnb.checkout.cash_help' : 'fnb.checkout.cash_promotion_help'
            );
            elements.fnbCashTenderedError.textContent = '';
            elements.fnbPayButton.textContent = cash && elements.fnbCashTendered.value !== ''
                ? t('fnb.checkout.confirm_cash', {
                    amount: money(Number(elements.fnbCashTendered.value)),
                })
                : t('fnb.checkout.pay');
        }

        async function printProvisionalReceipt() {
            const check = activeCheck();
            if (!check) return;
            try {
                const receipt = await apiCall(`/fnb/checks/${Number(check.id)}/provisional-receipt`);
                elements.fnbReceiptPrint.hidden = false;
                elements.fnbReceiptPrint.innerHTML = `<h1>${escapeHtml(receipt.document_label)}</h1><p>${escapeHtml((receipt.tables || []).join(' + '))} · ${escapeHtml(receipt.check.label)}</p>${receipt.check.lines.map(line => `<div><span>${escapeHtml(line.product_name)} × ${Number(line.quantity)}</span><strong>${escapeHtml(money(Number(line.unit_price_vnd) * Number(line.quantity)))}</strong></div>`).join('')}<hr><div><strong>${escapeHtml(t('fnb.checkout.total'))}</strong><strong>${escapeHtml(money(receipt.check.total_vnd))}</strong></div>`;
                global.print();
                elements.fnbReceiptPrint.hidden = true;
            } catch (error) {
                checkoutStatus(error.message);
            }
        }

        async function chooseShop(shopId) {
            closeApprovalAfterConflict();
            if (elements.fnbVariantDialog.open) elements.fnbVariantDialog.close();
            localStorage.setItem('currentShopId', String(shopId));
            products = [];
            categories = [];
            selectedCategoryId = null;
            await Promise.all([controller.selectShop(Number(shopId)), loadProducts(), loadCategories()]);
        }

        async function loadShops() {
            const result = await apiCall('/shops');
            shops = (result || []).filter(shop => shop.is_active !== false && shop.fnb_enabled);
            elements.fnbSetupOpen.hidden = !(setupAllowed && shops.length);
            elements.fnbShopSelect.innerHTML = shops.map(shop =>
                `<option value="${Number(shop.id)}">${escapeHtml(shop.name)}</option>`
            ).join('');
            if (!shops.length) {
                elements.fnbShopSelect.disabled = true;
                const owner = role === 'SELLER';
                emptyPanel(
                    'fnb.state.no_shops',
                    owner ? 'fnb.state.no_shops_owner' : 'fnb.state.no_shops_staff',
                    owner ? `<a class="fnb-back" href="/seller?setup=fnb">${escapeHtml(t('fnb.setup.open'))}</a>` : '',
                );
                live('fnb.state.no_shops');
                return;
            }
            const stored = Number(localStorage.getItem('currentShopId'));
            const selected = shops.find(shop => Number(shop.id) === stored) || shops[0];
            elements.fnbShopSelect.value = String(selected.id);
            await chooseShop(selected.id);
        }

        document.addEventListener('click', event => {
            const button = event.target.closest('[data-action]');
            if (!button) return;
            const action = button.dataset.action;
            const id = Number(button.dataset.id || button.closest('[data-id]')?.dataset.id);
            if (action === 'select-area') {
                selectedAreaId = id;
                renderFloor(controller.getState().floor);
            } else if (action === 'select-menu-category') {
                selectedCategoryId = id || null;
                renderProducts();
            } else if (action === 'open-table') {
                lastTableTrigger = button;
                sessionStatus(t('fnb.session.loading'));
                controller.openTable(id)
                    .then(() => roleCapabilities.checkout ? controller.loadChecks() : undefined)
                    .catch(error => sessionStatus(error.message));
            } else if (action === 'select-check') {
                selectCheck(id);
                renderChecks(controller.getState().checks);
                updateCashControls();
            } else if (action === 'open-setup') {
                elements.fnbSetupDialog.showModal();
                controller.loadSetup().catch(() => {});
            } else if (action === 'add-product') {
                controller.addLine({ product_id: id, quantity: 1, note: '' }).catch(() => {});
            } else if (action === 'choose-variant') {
                const entry = visibleMenuEntries[Number(button.dataset.index)];
                if (entry) openVariantDialog(entry, button);
            } else if (action === 'add-variant') {
                elements.fnbVariantDialog.close();
                controller.addLine({ product_id: id, quantity: 1, note: '' }).catch(() => {});
            } else if (action === 'quantity-minus' || action === 'quantity-plus') {
                const row = button.closest('[data-line-id]');
                const quantity = row.querySelector('[data-field="quantity"]');
                quantity.value = String(Math.max(1, Number(quantity.value || 1) + (action === 'quantity-plus' ? 1 : -1)));
                controller.updateLine(id, {
                    quantity: Number(quantity.value),
                    note: row.querySelector('[data-field="note"]').value,
                }).catch(() => {});
            } else if (action === 'cancel-line') {
                controller.cancelLine(id, 1).catch(error => {
                    const code = error?.code || error?.detail?.code;
                    if (![
                        'FNB_CANCELLATION_DECISION_REQUIRED',
                        'FNB_APPROVAL_REQUIRED',
                    ].includes(code)) return;
                    resetApprovalDialog();
                    pendingCancelLineId = id;
                    pendingCancelSessionRevision = Number(controller.getState().session?.revision);
                    elements.fnbApprovalDialog.showModal();
                    elements.fnbCancelResolution.focus();
                });
            } else if (action === 'serve-ticket') {
                controller.serveTicket(id)
                    .then(() => showToast(t('fnb.service.served_done')))
                    .catch(error => sessionStatus(error.message));
            } else if (action === 'save-station') {
                const row = button.closest('[data-product-id]');
                controller.updateProductStation(
                    Number(row.dataset.productId), row.querySelector('select').value,
                ).then(loadProducts).catch(() => {});
            } else if (action === 'close-approval') {
                closeApprovalAfterConflict();
            } else if (action === 'reapply') {
                controller.reapplyRecoverable().catch(() => {});
            } else if (action === 'retry-pending') {
                controller.retryPending().catch(error => sessionStatus(error.message));
            } else if (action === 'move-table' || action === 'merge-table') {
                const targetId = Number(elements.fnbTargetTable.value);
                if (!targetId) return;
                const sourceId = controller.getState().session?.tables?.[0]?.id;
                const promise = action === 'move-table'
                    ? controller.moveTable(sourceId, targetId)
                    : controller.mergeTable(targetId);
                promise.then(() => sessionStatus(t('fnb.table_actions.done'))).catch(error => sessionStatus(error.message));
            } else if (['save-area', 'toggle-area', 'save-table', 'toggle-table'].includes(action)) {
                const row = button.closest('[data-kind]');
                const isArea = row.dataset.kind === 'area';
                const values = action.startsWith('toggle-')
                    ? { active: button.dataset.active !== 'true' }
                    : {
                        name: row.querySelector('[data-field="name"]').value,
                        sort_order: Number(row.querySelector('[data-field="sort_order"]').value),
                    };
                const promise = isArea
                    ? controller.updateArea(Number(row.dataset.id), values)
                    : controller.updateTable(Number(row.dataset.id), values);
                promise.catch(() => {});
            }
        });

        elements.fnbRetry.addEventListener('click', () => controller.loadFloor(true));
        elements.fnbSetupOpen.addEventListener('click', () => {
            elements.fnbSetupDialog.showModal();
            controller.loadSetup().catch(() => {});
        });
        elements.fnbSessionClose.addEventListener('click', closeSession);
        elements.fnbSessionBackdrop.addEventListener('click', closeSession);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !elements.fnbSessionPanel.hidden && !document.querySelector('dialog[open]')) {
                closeSession();
            }
        });
        elements.fnbVariantDialog.addEventListener('close', () => {
            selectedVariantEntry = null;
            (lastVariantTrigger?.isConnected ? lastVariantTrigger : elements.fnbProductSearch).focus();
        });
        elements.fnbProductSearch.addEventListener('input', renderProducts);
        elements.fnbShopSelect.addEventListener('change', event => chooseShop(Number(event.target.value)));
        elements.fnbCancelSession.addEventListener('click', () => controller.cancelSession().catch(error => sessionStatus(error.message)));
        elements.fnbSend.addEventListener('click', () => controller.sendSession()
            .then(() => roleCapabilities.checkout ? controller.loadChecks() : undefined)
            .then(() => showToast(t('fnb.send.done')))
            .catch(error => sessionStatus(error.message)));
        elements.fnbCheckoutOpen.addEventListener('click', async () => {
            selectedCheckId = null;
            lastPaymentResult = null;
            checkoutStatus(t('fnb.checkout.loading'));
            elements.fnbCheckoutTitle.textContent = t('fnb.checkout.table_title', { table: elements.fnbSessionTitle.textContent });
            elements.fnbCheckoutDialog.showModal();
            try {
                await Promise.all([controller.loadChecks(), loadCustomers()]);
                updateCashControls({ clearTender: true });
            } catch (error) {
                checkoutStatus(error.message);
            }
        });
        elements.fnbCheckoutDialog.addEventListener('close', () => {
            updateCashControls({ clearTender: true });
            elements.fnbCheckoutOpen.focus();
        });
        elements.fnbPayForm.addEventListener('change', event => {
            if (event.target.name === 'fnbPaymentMethod') updateCashControls({ clearTender: true });
        });
        [elements.fnbVoucherCode, elements.fnbLoyaltyPoints].forEach(input =>
            input.addEventListener('input', () => updateCashControls()));
        elements.fnbCashTendered.addEventListener('input', () => updateCashControls());
        elements.fnbCashExact.addEventListener('click', () => {
            const check = activeCheck();
            if (!check) return;
            elements.fnbCashTendered.value = String(check.total_vnd);
            updateCashControls();
        });
        elements.fnbSplitForm.addEventListener('submit', event => {
            event.preventDefault();
            const check = activeCheck();
            const lines = Array.from(elements.fnbSplitLines.querySelectorAll('.fnb-split-line'))
                .filter(row => row.querySelector('input[type="checkbox"]').checked)
                .map(row => ({
                    line_id: Number(row.dataset.lineId),
                    quantity: Number(row.querySelector('input[type="number"]').value),
                }));
            if (!check || !lines.length) return checkoutStatus(t('fnb.checkout.split_required'));
            controller.splitCheck(check.id, lines, elements.fnbSplitLabel.value.trim())
                .then(() => { elements.fnbSplitLabel.value = ''; checkoutStatus(t('fnb.checkout.split_done')); })
                .catch(error => checkoutStatus(error.message));
        });
        elements.fnbAdjustmentForm.addEventListener('submit', event => {
            event.preventDefault();
            const check = activeCheck();
            if (!check) return;
            controller.updateCheckAdjustments(check.id, {
                discount_kind: elements.fnbDiscountKind.value,
                discount_value: adjustmentValueForApi(elements.fnbDiscountKind.value, elements.fnbDiscountValue.value),
                service_charge_kind: elements.fnbServiceKind.value,
                service_charge_value: adjustmentValueForApi(elements.fnbServiceKind.value, elements.fnbServiceValue.value),
            }).then(() => checkoutStatus(t('fnb.checkout.adjustments_done')))
                .catch(error => checkoutStatus(error.message));
        });
        elements.fnbPayForm.addEventListener('submit', event => {
            event.preventDefault();
            const check = activeCheck();
            if (!check || !navigator.onLine) return checkoutStatus(t('fnb.checkout.offline'));
            const method = paymentMethod();
            const values = { payment_method: method };
            const customerId = Number(elements.fnbCustomer.value) || null;
            const voucherCode = elements.fnbVoucherCode.value.trim();
            const loyaltyPoints = Number(elements.fnbLoyaltyPoints.value) || 0;
            const cashInput = cashTenderedForPayment(method, elements.fnbCashTendered.value);
            if (cashInput.error === 'required') {
                elements.fnbCashTenderedError.textContent = t('fnb.checkout.cash_required');
                elements.fnbCashTendered.focus();
                return;
            }
            Object.assign(values, cashInput);
            if (customerId) values.customer_id = customerId;
            if (voucherCode) values.voucher_code = voucherCode;
            if (loyaltyPoints > 0) values.loyalty_points_to_use = loyaltyPoints;
            if ((method === 'debt' || loyaltyPoints > 0) && !customerId) {
                return checkoutStatus(t('fnb.checkout.customer_required'));
            }
            controller.payCheck(check.id, values).catch(error => checkoutStatus(error.message));
        });
        elements.fnbPrintProvisional.addEventListener('click', printProvisionalReceipt);
        elements.fnbClosePaidSession.addEventListener('click', () => controller.closePaidSession()
            .catch(error => checkoutStatus(error.message)));
        elements.fnbAreaForm.addEventListener('submit', event => {
            event.preventDefault();
            controller.createArea({ name: elements.fnbAreaName.value, sort_order: 0 })
                .then(() => { elements.fnbAreaName.value = ''; })
                .catch(() => {});
        });
        elements.fnbTableForm.addEventListener('submit', event => {
            event.preventDefault();
            controller.createTable({
                area_id: Number(elements.fnbTableArea.value),
                name: elements.fnbTableName.value,
                sort_order: 0,
            }).then(() => { elements.fnbTableName.value = ''; }).catch(() => {});
        });
        elements.fnbPinForm.addEventListener('submit', event => {
            event.preventDefault();
            apiCall(`/fnb/shops/${Number(elements.fnbShopSelect.value)}/manager-pin`, 'PATCH', {
                pin: elements.fnbManagerPin.value,
            }).then(() => {
                elements.fnbManagerPin.value = '';
                setupStatus(t('fnb.pin.saved'));
            }).catch(error => setupStatus(error.message));
        });
        elements.fnbApprovalForm.addEventListener('submit', async event => {
            event.preventDefault();
            const current = controller.getState().session;
            if (!current || !pendingCancelLineId) return;
            if (Number(current.revision) !== pendingCancelSessionRevision) {
                closeApprovalAfterConflict();
                sessionStatus(t('fnb.cancel.changed'));
                return;
            }
            const lineId = Number(pendingCancelLineId);
            const sessionId = Number(current.id);
            const sessionRevision = pendingCancelSessionRevision;
            const shopId = Number(elements.fnbShopSelect.value);
            const approverUsername = elements.fnbApproverUsername.value;
            const pin = elements.fnbApprovalPin.value;
            const resolution = elements.fnbCancelResolution.value;
            const reason = elements.fnbCancelReason.value.trim();
            if (!reason) {
                elements.fnbApprovalStatus.textContent = t('fnb.cancel.reason_required');
                elements.fnbCancelReason.focus();
                return;
            }
            const approvalAttempt = ++approvalDialogGeneration;
            elements.fnbApprovalStatus.textContent = t('fnb.state.pending');
            try {
                const approval = await apiCall('/fnb/manager-approvals', 'POST', {
                    shop_id: shopId,
                    approver_username: approverUsername,
                    pin,
                    action: 'CANCEL_SENT_LINE', entity_type: 'SESSION',
                    entity_id: sessionId, revision: sessionRevision,
                });
                if (approvalAttempt !== approvalDialogGeneration) return;
                const latest = controller.getState().session;
                if (
                    Number(latest?.id) !== sessionId
                    || Number(latest?.revision) !== sessionRevision
                ) {
                    closeApprovalAfterConflict();
                    sessionStatus(t('fnb.cancel.changed'));
                    return;
                }
                await controller.cancelLine(lineId, 1, {
                    resolution,
                    reason,
                    approval_token: approval.approval_token,
                });
                if (approvalAttempt !== approvalDialogGeneration) return;
                resetApprovalDialog();
                elements.fnbApprovalDialog.close();
            } catch (error) {
                if (approvalAttempt !== approvalDialogGeneration) return;
                const code = error?.code || error?.detail?.code;
                if (code === 'FNB_SESSION_CHANGED' || code === 'FNB_LINE_CHANGED') {
                    const snapshot = error?.detail?.snapshot;
                    if (snapshot) {
                        controller.seedSession(snapshot);
                        renderSession(snapshot, null);
                    }
                    closeApprovalAfterConflict();
                    sessionStatus(t('fnb.cancel.changed'));
                    return;
                }
                elements.fnbApprovalPin.value = '';
                elements.fnbApprovalStatus.textContent = error.message;
            }
        });
        elements.fnbDraftLines.addEventListener('input', event => {
            const row = event.target.closest('[data-line-id]');
            if (!row) return;
            controller.saveDraft({
                line_id: Number(row.dataset.lineId),
                quantity: Number(row.querySelector('[data-field="quantity"]').value),
                note: row.querySelector('[data-field="note"]').value,
            });
        });
        elements.fnbDraftLines.addEventListener('change', event => {
            const row = event.target.closest('[data-line-id]');
            if (!row || !event.target.matches('[data-field]')) return;
            controller.updateLine(Number(row.dataset.lineId), {
                quantity: Number(row.querySelector('[data-field="quantity"]').value),
                note: row.querySelector('[data-field="note"]').value,
            }).catch(() => {});
        });
        elements.fnbApprovalDialog.addEventListener('close', resetApprovalDialog);
        elements.fnbSetupDialog.addEventListener('close', () => elements.fnbSetupOpen.focus());
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) controller.loadFloor(false);
        });
        global.addEventListener('offline', () => {
            live('fnb.state.offline');
            if (elements.fnbCheckoutDialog.open) renderChecks(controller.getState().checks);
        });
        global.addEventListener('online', () => {
            controller.loadFloor(true);
            if (elements.fnbCheckoutDialog.open) controller.loadChecks().catch(error => checkoutStatus(error.message));
        });
        global.addEventListener('pagehide', () => {
            closeApprovalAfterConflict();
            controller.dispose();
        }, { once: true });
        document.addEventListener('fselling:localechange', () => {
            renderFloor(controller.getState().floor);
            renderCategories();
            if (controller.getState().session) renderSession(controller.getState().session, controller.getDraft());
            if (controller.getState().checks) renderChecks(controller.getState().checks);
            if (elements.fnbVariantDialog.open) renderVariantDialog();
        });

        skeletons();
        loadShops().catch(error => {
            emptyPanel('fnb.state.poll_error', 'fnb.state.poll_error_empty');
            live('fnb.state.poll_error');
            showToast(error.message);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount, { once: true });
    } else {
        mount();
    }
})(typeof window === 'undefined' ? null : window);
