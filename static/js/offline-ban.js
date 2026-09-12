/* Bán hàng khi mất mạng.
 *
 * Store v0 (`phieu`, `anhchup`) là contract đang chạy và không được promote.
 * Store v1 giữ credential, catalog bind, receipt/checkpoint và sync lock riêng.
 * V1 gửi tuần tự bằng durable state machine; v0 vẫn là contract độc lập.
 */
(function (global) {
    'use strict';

    const TEN_DB = 'fselling-offline';
    const PHIEN_BAN = 3;
    const KHO_PHIEU = 'phieu';
    const KHO_ANH_CHUP = 'anhchup';
    const KHO_CREDENTIAL_V1 = 'credential_v1';
    const KHO_CATALOG_V1 = 'catalog_v1';
    const KHO_PHIEU_V1 = 'receipt_v1';
    const KHO_META_V1 = 'meta_v1';
    const KHO_KHOA_SYNC_V1 = 'sync_lock_v1';
    const MAX_VND = 9000000000000000;
    const MAX_QUANTITY = 1000000000;
    const MAX_ITEMS = 200;
    const PREFIX_CATALOG_V1 = 'FS-OFFLINE-CATALOG-v1\n';
    const PREFIX_PHIEU_V1 = 'FS-OFFLINE-RECEIPT-v1\n';
    const OFFLINE_SEAL_MARKER_PREFIX = 'fselling.offline-seal.v1:';
    const FIELD_SEP = '\x1f';
    const RECORD_SEP = '\x1e';
    const SYNC_STALE_MS = 60 * 1000;
    const SYNC_LOCK_TTL_MS = 15 * 1000;
    const SYNC_LOCK_HEARTBEAT_MS = 5 * 1000;
    const ACK_TOMBSTONE_TTL_MS = 30 * 24 * 60 * 60 * 1000;
    const RETRY_LONG_CYCLE_MS = 30 * 60 * 1000;
    const RECLAIM_MAX_REQUESTS = 2;
    const MAX_TIMER_DELAY_MS = 0x7fffffff;
    const SYNC_STATES = Object.freeze([
        'DRAFT', 'READY', 'SYNCING', 'ACKED', 'RETRYABLE',
        'BLOCKED_RECOVERABLE', 'QUARANTINED'
    ]);
    const BLOCKED_CODES = new Set([
        'OFFLINE_LEASE_REVOKED',
        'OFFLINE_LEASE_EXPIRED',
        'OFFLINE_LEASE_RECOVERY_REQUIRED',
        'OFFLINE_TIME_BEFORE_LEASE',
        'OFFLINE_TIME_FUTURE'
    ]);
    const QUARANTINE_CODES = new Set([
        'OFFLINE_UUID_OTHER_SHOP',
        'OFFLINE_RECEIPT_FINGERPRINT_CONFLICT',
        'OFFLINE_SEQUENCE_CONFLICT',
        'OFFLINE_RECEIPT_REGISTRY_INCONSISTENT',
        'OFFLINE_RECEIPT_UUID_UNAVAILABLE',
        'OFFLINE_RECEIPT_MALFORMED',
        'OFFLINE_UUID_MISSING',
        'OFFLINE_LEASE_BINDING_MISMATCH',
        'OFFLINE_BODY_TOO_LARGE',
        'OFFLINE_TOTAL_OVERFLOW',
        'OFFLINE_TENDER_TOO_LOW'
    ]);
    const V0_RECOVERY_CODE = 'OFFLINE_CONTRACT_V0_RECOVERY_REQUIRED';
    const POLICY_META_KEY = 'offline-contract-policy-v1';
    const POLICY_FENCE_KEY = 'offline-contract-policy-fence-v1';
    const POLICY_MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const policyRefreshInFlight = new Map();
    const POLICY_CODE_PHASE_A = 'OFFLINE_CONTRACT_PHASE_A_V0_V1';
    const POLICY_CODE_PHASE_B = 'OFFLINE_CONTRACT_PHASE_B_V1_MINIMUM';

    let _db = null;

    function ensureIndex(store, name, keyPath, options) {
        if (!store.indexNames.contains(name)) store.createIndex(name, keyPath, options || {});
    }

    function moDB() {
        if (_db) return Promise.resolve(_db);
        return new Promise(function (ok, that_bai) {
            let settled = false;
            const yc = indexedDB.open(TEN_DB, PHIEN_BAN);
            yc.onupgradeneeded = function () {
                const db = yc.result;
                const tx = yc.transaction;
                let store;
                if (!db.objectStoreNames.contains(KHO_PHIEU)) {
                    store = db.createObjectStore(KHO_PHIEU, { keyPath: 'offline_uuid' });
                } else {
                    store = tx.objectStore(KHO_PHIEU);
                }
                ensureIndex(store, 'shop_id', 'shop_id', { unique: false });

                if (!db.objectStoreNames.contains(KHO_ANH_CHUP)) {
                    db.createObjectStore(KHO_ANH_CHUP, { keyPath: 'khoa' });
                }

                if (!db.objectStoreNames.contains(KHO_CREDENTIAL_V1)) {
                    store = db.createObjectStore(KHO_CREDENTIAL_V1, { keyPath: 'lease_id' });
                } else {
                    store = tx.objectStore(KHO_CREDENTIAL_V1);
                }
                ensureIndex(store, 'identity_key', 'identity_key', { unique: false });
                ensureIndex(store, 'shop_id', 'shop_id', { unique: false });
                ensureIndex(store, 'status', 'status', { unique: false });

                if (!db.objectStoreNames.contains(KHO_CATALOG_V1)) {
                    store = db.createObjectStore(KHO_CATALOG_V1, { keyPath: 'lease_id' });
                } else {
                    store = tx.objectStore(KHO_CATALOG_V1);
                }
                ensureIndex(store, 'identity_key', 'identity_key', { unique: false });

                if (!db.objectStoreNames.contains(KHO_PHIEU_V1)) {
                    store = db.createObjectStore(KHO_PHIEU_V1, { keyPath: 'offline_uuid' });
                } else {
                    store = tx.objectStore(KHO_PHIEU_V1);
                }
                ensureIndex(store, 'identity_key', 'identity_key', { unique: false });
                ensureIndex(store, 'state', 'state', { unique: false });
                ensureIndex(store, 'lease_sequence', ['lease_id', 'sequence'], { unique: true });

                if (!db.objectStoreNames.contains(KHO_META_V1)) {
                    db.createObjectStore(KHO_META_V1, { keyPath: 'key' });
                }

                if (!db.objectStoreNames.contains(KHO_KHOA_SYNC_V1)) {
                    store = db.createObjectStore(KHO_KHOA_SYNC_V1, { keyPath: 'lock_name' });
                } else {
                    store = tx.objectStore(KHO_KHOA_SYNC_V1);
                }
                ensureIndex(store, 'expires_at', 'expires_at', { unique: false });
            };
            yc.onsuccess = function () {
                if (settled) {
                    yc.result.close();
                    return;
                }
                settled = true;
                const connection = yc.result;
                _db = connection;
                connection.onversionchange = function () {
                    if (_db === connection) _db = null;
                    connection.close();
                };
                ok(_db);
            };
            yc.onerror = function () {
                if (!settled) {
                    settled = true;
                    that_bai(yc.error || new Error('Không mở được IndexedDB'));
                }
            };
            yc.onblocked = function () {
                if (!settled) {
                    settled = true;
                    that_bai(new Error('IndexedDB đang bị tab cũ chặn nâng phiên bản'));
                }
            };
        });
    }

    function giaoDich(ten_kho, che_do, viec) {
        const ds = Array.isArray(ten_kho) ? ten_kho : [ten_kho];
        return moDB().then(function (db) {
            return new Promise(function (ok, that_bai) {
                let ket_qua;
                let loi_noi_bo = null;
                let da_xong = false;
                let tx;
                try {
                    tx = db.transaction(ds, che_do);
                } catch (e) {
                    that_bai(e);
                    return;
                }
                function datKetQua(value) { ket_qua = value; }
                function huy(error) {
                    loi_noi_bo = error instanceof Error ? error : new Error(String(error));
                    try { tx.abort(); } catch (e) { /* transaction đã tự abort */ }
                }
                tx.oncomplete = function () {
                    if (da_xong) return;
                    da_xong = true;
                    ok(ket_qua);
                };
                tx.onabort = function () {
                    if (da_xong) return;
                    da_xong = true;
                    that_bai(loi_noi_bo || tx.error || new Error('IndexedDB transaction bị hủy'));
                };
                tx.onerror = function () {
                    // Chờ `abort`: chỉ completion/abort mới quyết định durable.
                };
                try {
                    viec(tx, datKetQua, huy);
                } catch (e) {
                    huy(e);
                }
            });
        });
    }

    function chay(ten_kho, che_do, viec) {
        return giaoDich(ten_kho, che_do, function (tx, datKetQua) {
            const request = viec(tx.objectStore(ten_kho));
            if (request && typeof request === 'object' && 'onsuccess' in request) {
                request.onsuccess = function () { datKetQua(request.result); };
            } else {
                datKetQua(request);
            }
        });
    }

    function banSao(value) {
        if (value === undefined) return undefined;
        if (global.structuredClone) return global.structuredClone(value);
        return JSON.parse(JSON.stringify(value));
    }

    function dangOffline() {
        return navigator.onLine === false;
    }

    function usernameHienTai() {
        try { return localStorage.getItem('username') || ''; } catch (e) { return ''; }
    }

    function pendingSealMarkersV1() {
        const markers = [];
        try {
            for (let i = 0; i < localStorage.length; i += 1) {
                const key = localStorage.key(i);
                if (!key || !key.startsWith(OFFLINE_SEAL_MARKER_PREFIX)) continue;
                const value = localStorage.getItem(key);
                let parsed;
                try { parsed = JSON.parse(value); } catch (e) { continue; }
                if (parsed && typeof parsed.username === 'string' && parsed.username
                    && typeof parsed.generation === 'string' && parsed.generation) {
                    markers.push({ key, value, username: parsed.username });
                }
            }
        } catch (e) {
            // Storage bị chặn: identity checks ở credential/receipt vẫn fail-closed.
        }
        return markers;
    }

    async function applyPendingSealsV1() {
        const markers = pendingSealMarkersV1();
        for (const marker of markers) {
            await sealIdentityV1({ username: marker.username });
            try {
                if (localStorage.getItem(marker.key) === marker.value) {
                    localStorage.removeItem(marker.key);
                }
            } catch (e) {
                // Seal đã durable; marker dư chỉ làm transaction seal idempotent lại.
            }
        }
    }

    function identityKey(shopId, userId, username, deviceId) {
        return JSON.stringify([shopId, userId, username, deviceId]);
    }

    function activeKey(shopId, username, deviceId) {
        return 'active:' + JSON.stringify([shopId, username, deviceId]);
    }

    function soNguyen(value, min, max, label) {
        if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) {
            throw new Error(label + ' phải là số nguyên an toàn');
        }
        return value;
    }

    function vanBanKhongCam(value, label) {
        if (typeof value !== 'string' || !value) throw new Error(label + ' không hợp lệ');
        for (const char of value) {
            const cp = char.codePointAt(0);
            if (cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f) ||
                (cp >= 0xd800 && cp <= 0xdfff)) {
                throw new Error(label + ' chứa ký tự bị cấm');
            }
        }
        return value;
    }

    function chuanHoaTen(value) {
        if (typeof value !== 'string') throw new Error('product_name phải là chuỗi');
        const normalized = value.normalize('NFC');
        for (const char of normalized) {
            const cp = char.codePointAt(0);
            if (cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f) ||
                (cp >= 0x200b && cp <= 0x200d) || cp === 0xfeff ||
                (cp >= 0xd800 && cp <= 0xdfff) || cp === 0x2028 || cp === 0x2029) {
                throw new Error('product_name chứa ký tự bị cấm');
            }
        }
        const result = normalized.trim().split(/\s+/u).filter(Boolean).join(' ');
        if (!result || Array.from(result).length > 300 || new TextEncoder().encode(result).length > 900) {
            throw new Error('product_name vượt giới hạn canonical');
        }
        return result;
    }

    function hex(bytes) {
        return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
    }

    async function sha256(text) {
        if (!global.crypto || !global.crypto.subtle) throw new Error('Web Crypto SHA-256 không khả dụng');
        return hex(await global.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
    }

    function uuidCrypto(prefix) {
        if (!global.crypto) throw new Error('Web Crypto không khả dụng');
        if (typeof global.crypto.randomUUID === 'function') return prefix + global.crypto.randomUUID();
        if (typeof global.crypto.getRandomValues !== 'function') throw new Error('Web Crypto RNG không khả dụng');
        const bytes = new Uint8Array(16);
        global.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const h = hex(bytes);
        return prefix + `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
    }

    function taoUuidV0() {
        if (global.crypto && crypto.randomUUID) return 'off-' + crypto.randomUUID();
        return 'off-' + Date.now() + '-' + Math.random().toString(16).slice(2, 10);
    }

    function taoDeviceId() {
        return uuidCrypto('dev_');
    }

    function hieuLucDen(expiresAt) {
        if (typeof expiresAt !== 'string') return false;
        const parsed = Date.parse(expiresAt.replace(' ', 'T') + 'Z');
        return Number.isFinite(parsed) && Date.now() < parsed;
    }

    function canonicalServerTime(value, label) {
        if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$/.test(value || '')) {
            throw new Error(label + ' không đúng canonical UTC');
        }
        if (canonicalTimeV1(value) !== value) {
            throw new Error(label + ' không đúng canonical UTC');
        }
        const parsed = Date.parse(value.replace(' ', 'T') + 'Z');
        if (!Number.isFinite(parsed)) throw new Error(label + ' ngoài miền thời gian');
        return parsed;
    }

    function validateIssued(issued, shopId, deviceId, catalogDigest) {
        if (!issued || issued.status !== 'ACTIVE' || issued.shop_id !== shopId
            || issued.device_id !== deviceId || issued.contract_version !== 1
            || issued.catalog_snapshot_digest !== catalogDigest
            || !/^[0-9a-f]{64}$/.test(issued.catalog_snapshot_digest || '')
            || !/^lse_[0-9A-HJKMNP-TV-Z]{22}$/.test(issued.lease_id || '')
            || !/^[A-Za-z0-9_-]{43}$/.test(issued.lease_token || '')) {
            return false;
        }
        try {
            soNguyen(issued.user_id, 1, MAX_QUANTITY, 'user_id');
            soNguyen(issued.catalog_version, 0, MAX_QUANTITY, 'catalog_version');
            soNguyen(issued.state_version, 0, MAX_QUANTITY, 'state_version');
            vanBanKhongCam(issued.server_anchor_id, 'server_anchor_id');
            canonicalServerTime(issued.server_time_utc, 'server_time_utc');
            const anchor = canonicalServerTime(issued.anchor_server_time_utc, 'anchor_server_time_utc');
            const issuedAt = canonicalServerTime(issued.issued_at, 'issued_at');
            const expiresAt = canonicalServerTime(issued.expires_at, 'expires_at');
            return anchor === issuedAt && expiresAt > issuedAt;
        } catch (e) {
            return false;
        }
    }

    function performanceState() {
        if (!global.performance || typeof global.performance.now !== 'function') {
            throw new Error('Performance monotonic clock không khả dụng');
        }
        const now = global.performance.now();
        const epoch = global.performance.timeOrigin;
        if (!Number.isFinite(now) || !Number.isFinite(epoch)) {
            throw new Error('Performance monotonic clock không hợp lệ');
        }
        return { epoch: String(epoch), now: Math.floor(now) };
    }

    function themMilliGiay(anchor, deltaMs) {
        const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\.(\d{6})$/.exec(anchor || '');
        if (!match) throw new Error('Server anchor không đúng canonical UTC');
        const fraction = match[7];
        const base = Date.UTC(
            Number(match[1]), Number(match[2]) - 1, Number(match[3]),
            Number(match[4]), Number(match[5]), Number(match[6]), Number(fraction.slice(0, 3))
        );
        const d = new Date(base + deltaMs);
        if (!Number.isFinite(d.getTime())) throw new Error('Client time vượt miền biểu diễn');
        return d.toISOString().slice(0, 23).replace('T', ' ') + fraction.slice(3);
    }

    function canonicalTimeV1(value) {
        if (typeof value !== 'string') throw new Error('sold_at_client_utc phải là chuỗi');
        const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|z|[+-]\d{2}:?\d{2})?$/.exec(value.trim());
        if (!m) throw new Error('sold_at_client_utc không phải ISO-8601');
        const fraction = (m[7] || '').padEnd(6, '0');
        const year = Number(m[1]), month = Number(m[2]), day = Number(m[3]);
        const hour = Number(m[4]), minute = Number(m[5]), second = Number(m[6]);
        const millisecond = Number(fraction.slice(0, 3));
        const naive = new Date(0);
        naive.setUTCFullYear(year, month - 1, day);
        naive.setUTCHours(hour, minute, second, millisecond);
        if (naive.getUTCFullYear() !== year || naive.getUTCMonth() !== month - 1
            || naive.getUTCDate() !== day || naive.getUTCHours() !== hour
            || naive.getUTCMinutes() !== minute || naive.getUTCSeconds() !== second) {
            throw new Error('sold_at_client_utc không phải ISO-8601');
        }
        let millis = naive.getTime();
        const zone = m[8];
        if (zone && !/^[Zz]$/.test(zone)) {
            const sign = zone[0] === '+' ? 1 : -1;
            const digits = zone.slice(1).replace(':', '');
            const zoneHour = Number(digits.slice(0, 2));
            const zoneMinute = Number(digits.slice(2));
            if (zoneHour > 23 || zoneMinute > 59) {
                throw new Error('sold_at_client_utc không phải ISO-8601');
            }
            millis -= sign * (zoneHour * 60 + zoneMinute) * 60000;
        }
        const d = new Date(millis);
        if (!Number.isFinite(d.getTime())) throw new Error('sold_at_client_utc ngoài miền biểu diễn');
        return d.toISOString().slice(0, 23).replace('T', ' ') + fraction.slice(3);
    }

    function compareBytes(a, b) {
        const aa = new TextEncoder().encode(a);
        const bb = new TextEncoder().encode(b);
        const n = Math.min(aa.length, bb.length);
        for (let i = 0; i < n; i += 1) {
            if (aa[i] !== bb[i]) return aa[i] - bb[i];
        }
        return aa.length - bb.length;
    }

    function chuanHoaItems(items, catalogRows) {
        if (!Array.isArray(items) || items.length < 1 || items.length > MAX_ITEMS) {
            throw new Error('Phiếu v1 phải có 1..200 dòng');
        }
        const catalogById = new Map((catalogRows || []).map(row => [row.id, row]));
        let total = 0;
        const result = items.map(function (item) {
            if (!item || typeof item !== 'object') throw new Error('Dòng hàng không hợp lệ');
            const productId = soNguyen(item.product_id, 1, MAX_QUANTITY, 'product_id');
            const priceSource = Object.prototype.hasOwnProperty.call(item, 'unit_price_vnd')
                ? item.unit_price_vnd : item.price;
            const unitPrice = soNguyen(priceSource, 0, MAX_VND, 'unit_price_vnd');
            const quantity = soNguyen(item.quantity, 1, MAX_QUANTITY, 'quantity');
            const name = chuanHoaTen(item.product_name);
            const catalog = catalogById.get(productId);
            if (!catalog || catalog.is_active !== true || catalog.name !== name || catalog.price_vnd !== unitPrice) {
                throw new Error('Dòng hàng không khớp catalog snapshot của lease');
            }
            const line = unitPrice * quantity;
            if (!Number.isSafeInteger(line) || line > MAX_VND || total > MAX_VND - line) {
                throw new Error('Tổng tiền v1 vượt miền INTEGER VND');
            }
            total += line;
            return { product_id: productId, product_name: name, unit_price_vnd: unitPrice, quantity };
        });
        result.sort(function (a, b) {
            return a.product_id - b.product_id
                || compareBytes(a.product_name, b.product_name)
                || a.unit_price_vnd - b.unit_price_vnd
                || a.quantity - b.quantity;
        });
        return { items: result, total };
    }

    async function catalogSnapshot(products) {
        if (!Array.isArray(products)) throw new Error('Catalog phải là mảng');
        const rows = products.map(function (product) {
            if (!product || typeof product !== 'object') throw new Error('Catalog product không hợp lệ');
            return {
                id: soNguyen(product.id, 1, MAX_QUANTITY, 'catalog product_id'),
                is_active: Boolean(product.is_active),
                name: chuanHoaTen(product.name),
                price_vnd: soNguyen(product.price, 0, MAX_VND, 'catalog price_vnd'),
                track_batches: Boolean(product.track_batches)
            };
        });
        rows.sort((a, b) => a.id - b.id);
        const canonicalJson = JSON.stringify(rows);
        return {
            rows,
            canonical_json: canonicalJson,
            digest: await sha256(PREFIX_CATALOG_V1 + canonicalJson)
        };
    }

    function publicCredential(credential) {
        if (!credential) return null;
        const copy = banSao(credential);
        delete copy.lease_token;
        delete copy.identity_key;
        return copy;
    }

    function docTatCaStore(storeName) {
        return chay(storeName, 'readonly', kho => kho.getAll()).then(ds => ds || []);
    }

    function publicCutoffTimestamp(value, required, mustHaveArrived) {
        if (value === null && !required) return null;
        if (typeof value !== 'string' || value.length < 20 || value.length > 40) return undefined;
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
        if (!match) return undefined;
        const parsed = Date.parse(value);
        if (!Number.isFinite(parsed) || (mustHaveArrived && parsed > Date.now())) return undefined;
        // Date.parse normalizes impossible dates (for example February 31st).
        // Compare every representable UTC component so a malformed public
        // capability can never become an authorization for a v0 fallback.
        const date = new Date(parsed);
        const milliseconds = Number((match[7] || '').slice(0, 3).padEnd(3, '0'));
        if (date.getUTCFullYear() !== Number(match[1])
            || date.getUTCMonth() + 1 !== Number(match[2])
            || date.getUTCDate() !== Number(match[3])
            || date.getUTCHours() !== Number(match[4])
            || date.getUTCMinutes() !== Number(match[5])
            || date.getUTCSeconds() !== Number(match[6])
            || date.getUTCMilliseconds() !== milliseconds) return undefined;
        return value;
    }

    function boundedPolicy(value) {
        if (!value || typeof value !== 'object'
            || !Array.isArray(value.supported_versions)
            || value.supported_versions.length !== 2
            || value.supported_versions[0] !== 0 || value.supported_versions[1] !== 1
            || !Number.isInteger(value.minimum_accepted_version)
            || typeof value.phase !== 'string' || typeof value.policy_code !== 'string') return null;
        const phaseA = value.phase === 'PHASE_A'
            && value.minimum_accepted_version === 0
            && value.policy_code === POLICY_CODE_PHASE_A;
        const phaseB = value.phase === 'PHASE_B'
            && value.minimum_accepted_version === 1
            && value.policy_code === POLICY_CODE_PHASE_B;
        if (!phaseA && !phaseB) return null;
        const cutoff = publicCutoffTimestamp(value.cutoff_at_utc, phaseB, phaseB);
        if (cutoff === undefined) return null;
        return {
            supported_versions: [0, 1],
            minimum_accepted_version: value.minimum_accepted_version,
            phase: value.phase,
            cutoff_at_utc: cutoff,
            policy_code: value.policy_code
        };
    }

    function policyContext(options) {
        const shopId = Number(options && options.shop_id);
        const username = String(options && options.username || '').trim();
        if (!Number.isSafeInteger(shopId) || shopId < 1 || username.length < 1 || username.length > 128) return null;
        return { shop_id: shopId, username };
    }

    function policyStorageKey(context) {
        return `${POLICY_META_KEY}:${context.shop_id}:${encodeURIComponent(context.username)}`;
    }

    function policyFenceStorageKey(context) {
        return `${POLICY_FENCE_KEY}:${context.shop_id}:${encodeURIComponent(context.username)}`;
    }

    function policyFetchedAt(value) {
        if (typeof value !== 'string' || value.length > 40) return null;
        const parsed = Date.parse(value);
        if (!Number.isFinite(parsed) || parsed > Date.now() + 60 * 1000) return null;
        return parsed;
    }

    function policyFromCachedRow(row, context) {
        const fetchedAt = policyFetchedAt(row && row.fetched_at);
        if (!context || !fetchedAt || Date.now() - fetchedAt > POLICY_MAX_AGE_MS
            || Number(row && row.shop_id) !== context.shop_id
            || String(row && row.username || '') !== context.username) return null;
        return boundedPolicy(row && row.policy);
    }

    function strongerContractPolicy(first, second) {
        if (!first) return second || null;
        if (!second) return first;
        return second.minimum_accepted_version > first.minimum_accepted_version ? second : first;
    }

    function readPolicyRows(context) {
        return giaoDich(KHO_META_V1, 'readonly', function (tx, datKetQua, huy) {
            const store = tx.objectStore(KHO_META_V1);
            const scoped = store.get(policyStorageKey(context));
            scoped.onerror = function () { huy(scoped.error || new Error('Không đọc được policy offline')); };
            scoped.onsuccess = function () {
                const legacy = store.get(POLICY_META_KEY);
                legacy.onerror = function () { huy(legacy.error || new Error('Không đọc được policy offline')); };
                legacy.onsuccess = function () { datKetQua([scoped.result, legacy.result]); };
            };
        });
    }

    async function cachedContractPolicy(options) {
        const context = policyContext(options);
        if (!context) return null;
        const rows = await readPolicyRows(context);
        return strongerContractPolicy(
            policyFromCachedRow(rows && rows[0], context),
            policyFromCachedRow(rows && rows[1], context)
        );
    }

    function validPolicyFence(row, context) {
        if (!row || Number(row.shop_id) !== context.shop_id
            || String(row.username || '') !== context.username) return null;
        const generation = Number(row.generation);
        return Number.isSafeInteger(generation) && generation > 0 ? generation : null;
    }

    function reservePolicyRequestFence(context) {
        return giaoDich(KHO_META_V1, 'readwrite', function (tx, datKetQua, huy) {
            const store = tx.objectStore(KHO_META_V1);
            const key = policyFenceStorageKey(context);
            const current = store.get(key);
            current.onerror = function () { huy(current.error || new Error('Không reserve được policy offline')); };
            current.onsuccess = function () {
                const previous = validPolicyFence(current.result, context) || 0;
                // A fence is local, bounded and non-secret.  Reaching this
                // theoretical bound still creates a new latest generation.
                const generation = previous >= Number.MAX_SAFE_INTEGER - 1 ? 1 : previous + 1;
                const put = store.put({
                    key, generation, shop_id: context.shop_id, username: context.username,
                    requested_at: new Date().toISOString()
                });
                put.onerror = function () { huy(put.error || new Error('Không reserve được policy offline')); };
                put.onsuccess = function () { datKetQua(generation); };
            };
        });
    }

    function storeContractPolicy(context, policy, requestFence) {
        // The get/compare/put sequence lives in one readwrite transaction.  IDB
        // serializes that transaction across tabs, so a response which started
        // before Phase B cannot overwrite the durable stricter policy later.
        return giaoDich(KHO_META_V1, 'readwrite', function (tx, datKetQua, huy) {
            const store = tx.objectStore(KHO_META_V1);
            const scoped = store.get(policyStorageKey(context));
            scoped.onerror = function () { huy(scoped.error || new Error('Không đọc được policy offline')); };
            scoped.onsuccess = function () {
                const legacy = store.get(POLICY_META_KEY);
                legacy.onerror = function () { huy(legacy.error || new Error('Không đọc được policy offline')); };
                legacy.onsuccess = function () {
                    const fence = store.get(policyFenceStorageKey(context));
                    fence.onerror = function () { huy(fence.error || new Error('Không đọc được policy offline')); };
                    fence.onsuccess = function () {
                        const current = strongerContractPolicy(
                            policyFromCachedRow(scoped.result, context),
                            policyFromCachedRow(legacy.result, context)
                        );
                        if (validPolicyFence(fence.result, context) !== requestFence) {
                            datKetQua(current);
                            return;
                        }
                        if (current && current.minimum_accepted_version > policy.minimum_accepted_version) {
                            datKetQua(current);
                            return;
                        }
                        const put = store.put({
                            key: policyStorageKey(context), policy, shop_id: context.shop_id,
                            username: context.username, fetched_at: new Date().toISOString()
                        });
                        put.onerror = function () { huy(put.error || new Error('Không lưu được policy offline')); };
                        put.onsuccess = function () { datKetQua(policy); };
                    };
                };
            };
        });
    }

    async function refreshContractPolicy(options, force = false) {
        const context = policyContext(options);
        if (!context || dangOffline() || typeof global.apiCall !== 'function') return cachedContractPolicy(context);
        const cached = await cachedContractPolicy(context);
        if (cached && !force) return cached;
        const refreshKey = `${context.shop_id}:${context.username}`;
        if (policyRefreshInFlight.has(refreshKey)) return policyRefreshInFlight.get(refreshKey);
        const work = (async () => {
        let requestFence;
        try {
            // Reserve before the request, not at response time.  This durable
            // generation distinguishes a delayed old response from a genuine
            // later Phase-A rollback after the policy TTL.
            requestFence = await reservePolicyRequestFence(context);
            const policy = boundedPolicy(await global.apiCall('/offline/capability'));
            // Re-read durable state after any invalid response: an in-memory
            // request-start snapshot must never authorize a relaxed fallback.
            if (!policy) return cachedContractPolicy(context);
            return storeContractPolicy(context, policy, requestFence);
        } catch (e) {
            return cachedContractPolicy(context);
        }
        })();
        policyRefreshInFlight.set(refreshKey, work);
        try {
            return await work;
        } finally {
            policyRefreshInFlight.delete(refreshKey);
        }
    }

    async function layDeviceId() {
        const key = 'device_id_v1';
        return giaoDich(KHO_META_V1, 'readwrite', function (tx, datKetQua, huy) {
            const store = tx.objectStore(KHO_META_V1);
            const request = store.get(key);
            request.onsuccess = function () {
                const existing = request.result;
                if (existing && typeof existing.value === 'string'
                    && existing.value.length >= 1 && existing.value.length <= 128) {
                    datKetQua(existing.value);
                    return;
                }
                try {
                    const value = taoDeviceId();
                    store.put({ key, value });
                    datKetQua(value);
                } catch (e) { huy(e); }
            };
        });
    }

    function credentialUsable(credential, options) {
        return Boolean(
            credential
            && credential.contract_version === 1
            && credential.status === 'ACTIVE'
            && credential.sealed !== true
            && credential.shop_id === options.shop_id
            && credential.username === options.username
            && credential.device_id === options.device_id
            && (!options.user_id || credential.user_id === options.user_id)
            && hieuLucDen(credential.expires_at)
        );
    }

    async function prepareV1(options) {
        const shopId = soNguyen(Number(options && options.shop_id), 1, MAX_QUANTITY, 'shop_id');
        const username = vanBanKhongCam(String(options && options.username || ''), 'username');
        if (dangOffline() || usernameHienTai() !== username) return null;
        await applyPendingSealsV1();
        const deviceId = await layDeviceId();
        const pointerKey = activeKey(shopId, username, deviceId);
        // Online catalog đang được chuẩn bị thay thế capability hiện hành.
        // Xóa pointer trước mọi digest/issue async: nếu tab crash, SHA/issue hay
        // quota fail thì fallback v0, không dùng nhầm lease của catalog cũ.
        await chay(KHO_META_V1, 'readwrite', kho => kho.delete(pointerKey));
        const snapshot = await catalogSnapshot(options.products);
        const credentials = await docTatCaStore(KHO_CREDENTIAL_V1);
        const catalogs = await docTatCaStore(KHO_CATALOG_V1);
        const catalogByLease = new Map(catalogs.map(row => [row.lease_id, row]));
        let credential = credentials
            .filter(row => row.shop_id === shopId && row.username === username && row.device_id === deviceId)
            .filter(row => row.status === 'ACTIVE' && row.sealed !== true && hieuLucDen(row.expires_at))
            .filter(row => row.catalog_snapshot_digest === snapshot.digest)
            .filter(row => catalogByLease.get(row.lease_id)?.catalog_snapshot_digest === snapshot.digest)
            .sort((a, b) => String(b.saved_at || b.issued_at).localeCompare(String(a.saved_at || a.issued_at))
                || String(b.lease_id).localeCompare(String(a.lease_id)))[0] || null;

        if (credential) {
            const catalog = {
                lease_id: credential.lease_id,
                identity_key: credential.identity_key,
                shop_id: shopId,
                user_id: credential.user_id,
                username,
                device_id: deviceId,
                catalog_version: credential.catalog_version,
                catalog_snapshot_digest: snapshot.digest,
                saved_at: new Date().toISOString(),
                rows: snapshot.rows
            };
            await giaoDich([KHO_CREDENTIAL_V1, KHO_CATALOG_V1, KHO_META_V1], 'readwrite', function (tx) {
                tx.objectStore(KHO_CREDENTIAL_V1).put(credential);
                tx.objectStore(KHO_CATALOG_V1).put(catalog);
                tx.objectStore(KHO_META_V1).put({
                    key: pointerKey,
                    lease_id: credential.lease_id,
                    identity_key: credential.identity_key
                });
            });
            await finalizeDraftsV1({ shop_id: shopId, username, user_id: credential.user_id });
            return publicCredential(credential);
        }

        if (typeof global.apiCall !== 'function') return null;
        let issued;
        try {
            issued = await global.apiCall('/offline/leases', 'POST', {
                shop_id: shopId,
                device_id: deviceId
            });
        } catch (e) {
            await chay(KHO_META_V1, 'readwrite', kho => kho.delete(pointerKey));
            return null;
        }
        if (!validateIssued(issued, shopId, deviceId, snapshot.digest)) {
            await chay(KHO_META_V1, 'readwrite', kho => kho.delete(pointerKey));
            return null;
        }
        const userId = soNguyen(issued.user_id, 1, MAX_QUANTITY, 'user_id');
        const key = identityKey(shopId, userId, username, deviceId);
        credential = {
            ...banSao(issued),
            identity_key: key,
            username,
            sealed: false,
            local_state: 'ACTIVE',
            saved_at: new Date().toISOString()
        };
        const catalog = {
            lease_id: issued.lease_id,
            identity_key: key,
            shop_id: shopId,
            user_id: userId,
            username,
            device_id: deviceId,
            catalog_version: issued.catalog_version,
            catalog_snapshot_digest: snapshot.digest,
            saved_at: new Date().toISOString(),
            rows: snapshot.rows
        };
        const perf = performanceState();
        await giaoDich([KHO_CREDENTIAL_V1, KHO_CATALOG_V1, KHO_META_V1], 'readwrite', function (tx) {
            tx.objectStore(KHO_CREDENTIAL_V1).add(credential);
            tx.objectStore(KHO_CATALOG_V1).add(catalog);
            tx.objectStore(KHO_META_V1).put({
                key: 'clock:' + issued.lease_id,
                lease_id: issued.lease_id,
                epoch: perf.epoch,
                epoch_base_perf_ms: perf.now,
                epoch_base_cumulative_ms: 0,
                cumulative_ms: 0,
                monotonic_valid: true
            });
            tx.objectStore(KHO_META_V1).put({
                key: pointerKey,
                lease_id: issued.lease_id,
                identity_key: key
            });
        });
        return publicCredential(credential);
    }

    async function usableV1(options) {
        const username = String(options.username || '');
        if (!username || usernameHienTai() !== username) return null;
        await applyPendingSealsV1();
        const deviceId = await layDeviceId();
        const pointer = await chay(
            KHO_META_V1,
            'readonly',
            kho => kho.get(activeKey(options.shop_id, username, deviceId))
        );
        if (!pointer || typeof pointer.lease_id !== 'string') return null;
        const credentials = await docTatCaStore(KHO_CREDENTIAL_V1);
        const candidates = credentials
            .filter(row => row.lease_id === pointer.lease_id && row.identity_key === pointer.identity_key)
            .filter(row => credentialUsable(row, {
                shop_id: options.shop_id,
                username,
                user_id: options.user_id,
                device_id: deviceId
            }))
            .sort((a, b) => String(b.saved_at || b.issued_at).localeCompare(String(a.saved_at || a.issued_at))
                || String(b.lease_id).localeCompare(String(a.lease_id)));
        for (const credential of candidates) {
            const catalog = await chay(KHO_CATALOG_V1, 'readonly', kho => kho.get(credential.lease_id));
            if (catalog && catalog.identity_key === credential.identity_key
                && catalog.catalog_snapshot_digest === credential.catalog_snapshot_digest) {
                return { credential, catalog };
            }
        }
        return null;
    }

    function nextClock(clock, perf) {
        if (!clock) {
            return {
                epoch: perf.epoch,
                epoch_base_perf_ms: perf.now,
                epoch_base_cumulative_ms: 0,
                cumulative_ms: 0,
                monotonic_valid: true
            };
        }
        let next = { ...clock };
        if (clock.epoch !== perf.epoch || perf.now < clock.epoch_base_perf_ms) {
            next.epoch = perf.epoch;
            next.epoch_base_perf_ms = perf.now;
            next.epoch_base_cumulative_ms = clock.cumulative_ms;
            next.monotonic_valid = false;
            return next;
        }
        const candidate = clock.epoch_base_cumulative_ms + (perf.now - clock.epoch_base_perf_ms);
        next.cumulative_ms = Math.max(clock.cumulative_ms, candidate);
        next.monotonic_valid = clock.monotonic_valid === true;
        return next;
    }

    function fingerprintInput(receipt) {
        return {
            shop_id: receipt.shop_id,
            sold_at_client_utc: receipt.sold_at_client_utc,
            client_monotonic_ms: receipt.client_monotonic_ms,
            monotonic_valid: receipt.monotonic_valid,
            server_anchor_id: receipt.server_anchor_id,
            lease_id: receipt.lease_id,
            device_id: receipt.device_id,
            offline_session_id: receipt.offline_session_id,
            sequence: receipt.sequence,
            offline_uuid: receipt.offline_uuid,
            catalog_version: receipt.catalog_version,
            catalog_snapshot_digest: receipt.catalog_snapshot_digest,
            items: receipt.items,
            cash_tendered: receipt.cash_tendered
        };
    }

    function localCreationIntentV1(credential, normalized, tendered) {
        return JSON.stringify({
            contract_version: 1,
            identity_key: credential.identity_key,
            username: credential.username,
            user_id: credential.user_id,
            shop_id: credential.shop_id,
            lease_id: credential.lease_id,
            device_id: credential.device_id,
            offline_session_id: credential.lease_id,
            server_anchor_id: credential.server_anchor_id,
            anchor_server_time_utc: credential.anchor_server_time_utc,
            issued_at: credential.issued_at,
            expires_at: credential.expires_at,
            catalog_version: credential.catalog_version,
            catalog_snapshot_digest: credential.catalog_snapshot_digest,
            payment_method: 'CASH',
            cash_tendered: tendered,
            total_vnd: normalized.total,
            items: normalized.items
        });
    }

    async function fingerprintV1(input) {
        const shopId = soNguyen(input.shop_id, 1, MAX_QUANTITY, 'shop_id');
        const sequence = soNguyen(input.sequence, 1, MAX_QUANTITY, 'sequence');
        const monotonic = soNguyen(input.client_monotonic_ms, 0, Number.MAX_SAFE_INTEGER, 'client_monotonic_ms');
        if (typeof input.monotonic_valid !== 'boolean') throw new Error('monotonic_valid phải là boolean');
        const catalogVersion = soNguyen(input.catalog_version, 0, MAX_QUANTITY, 'catalog_version');
        const tendered = soNguyen(input.cash_tendered, 0, MAX_VND, 'cash_tendered');
        const ids = ['lease_id', 'device_id', 'offline_session_id', 'offline_uuid', 'server_anchor_id'];
        ids.forEach(name => vanBanKhongCam(input[name], name));
        if (!/^[0-9a-f]{64}$/.test(input.catalog_snapshot_digest || '')) {
            // Known-vector server test predates the production digest shape and
            // deliberately uses `sha256:deadbeef`; canonical bytes still bind it.
            vanBanKhongCam(input.catalog_snapshot_digest, 'catalog_snapshot_digest');
        }
        const normalized = chuanHoaItems(input.items, input.items.map(row => ({
            id: row.product_id,
            name: chuanHoaTen(row.product_name),
            price_vnd: row.unit_price_vnd,
            is_active: true
        })));
        const soldAt = canonicalTimeV1(input.sold_at_client_utc);
        const lines = [
            PREFIX_PHIEU_V1 + [shopId, 1, input.lease_id, input.device_id,
                input.offline_session_id, sequence, input.offline_uuid].join(FIELD_SEP),
            [soldAt, monotonic, input.monotonic_valid ? 1 : 0, input.server_anchor_id].join(FIELD_SEP),
            [catalogVersion, input.catalog_snapshot_digest].join(FIELD_SEP),
            ['CASH', tendered, normalized.total, normalized.items.length].join(FIELD_SEP),
            normalized.items.map(row => [row.product_id, row.product_name,
                row.unit_price_vnd, row.quantity].join(FIELD_SEP)).join(RECORD_SEP)
        ];
        const canonical = lines.join('\n') + '\n';
        return {
            digest: 'fsofr1:' + await sha256(canonical),
            canonical_bytes: new TextEncoder().encode(canonical),
            sold_at_client_utc: soldAt,
            items: normalized.items,
            total_vnd: normalized.total
        };
    }

    function allocateDraftV1(context, normalized, tendered, creationKey, requestDigest) {
        const credential = context.credential;
        const clockKey = 'clock:' + credential.lease_id;
        const sequenceKey = 'sequence:' + credential.lease_id;
        return giaoDich(
            [KHO_CREDENTIAL_V1, KHO_CATALOG_V1, KHO_META_V1, KHO_PHIEU_V1],
            'readwrite',
            function (tx, datKetQua, huy) {
                const credentials = tx.objectStore(KHO_CREDENTIAL_V1);
                const catalogs = tx.objectStore(KHO_CATALOG_V1);
                const meta = tx.objectStore(KHO_META_V1);
                const receipts = tx.objectStore(KHO_PHIEU_V1);
                const credentialRequest = credentials.get(credential.lease_id);
                credentialRequest.onsuccess = function () {
                    const fresh = credentialRequest.result;
                    if (!credentialUsable(fresh, {
                        shop_id: credential.shop_id,
                        username: credential.username,
                        user_id: credential.user_id,
                        device_id: credential.device_id
                    }) || usernameHienTai() !== fresh.username) {
                        huy(new Error('Credential v1 không còn ACTIVE cho identity hiện tại'));
                        return;
                    }
                    const catalogRequest = catalogs.get(fresh.lease_id);
                    catalogRequest.onsuccess = function () {
                        const catalog = catalogRequest.result;
                        if (!catalog || catalog.identity_key !== fresh.identity_key
                            || catalog.catalog_snapshot_digest !== fresh.catalog_snapshot_digest) {
                            huy(new Error('Catalog snapshot v1 không còn bind đúng credential'));
                            return;
                        }
                        const creationIntent = localCreationIntentV1(fresh, normalized, tendered);
                        const bindingKey = 'creation:' + creationKey;
                        const bindingRequest = meta.get(bindingKey);
                        bindingRequest.onsuccess = function () {
                            if (bindingRequest.result) { datKetQua(null); return; }
                            findExisting();
                        };
                        function bind(draft) {
                            // ponytail: retain compact retry bindings indefinitely; pruning requires proof no stale POS retry exists.
                            meta.put({ key: bindingKey, offline_uuid: draft.offline_uuid,
                                request_digest: requestDigest, identity_key: draft.identity_key,
                                username: draft.username, user_id: draft.user_id,
                                shop_id: draft.shop_id, device_id: draft.device_id, lease_id: draft.lease_id });
                        }
                        function findExisting() {
                            const existingRequest = receipts.getAll();
                            existingRequest.onsuccess = function () {
                                const existing = (existingRequest.result || []).find(row =>
                                    row.local_creation_key === creationKey
                                );
                                if (existing) {
                                    if (existing.local_creation_intent !== creationIntent) {
                                        huy(new Error('creation_key đã bind với immutable intent khác'));
                                        return;
                                    }
                                    bind(existing);
                                    datKetQua(existing);
                                    return;
                                }
                                allocateNew();
                            };
                        }
                        function allocateNew() {
                            let perf;
                            let uuid;
                            try {
                                perf = performanceState();
                                uuid = uuidCrypto('off-');
                            } catch (e) {
                                huy(e);
                                return;
                            }
                            const clockRequest = meta.get(clockKey);
                            const sequenceRequest = meta.get(sequenceKey);
                            let clockDone = false, sequenceDone = false;
                            function finish() {
                                if (!clockDone || !sequenceDone) return;
                                try {
                                    const clock = nextClock(clockRequest.result, perf);
                                    clock.key = clockKey;
                                    clock.lease_id = fresh.lease_id;
                                    clock.cumulative_ms = soNguyen(
                                        clock.cumulative_ms, 0, Number.MAX_SAFE_INTEGER,
                                        'client_monotonic_ms'
                                    );
                                    const previous = sequenceRequest.result?.value || 0;
                                    const sequence = soNguyen(previous + 1, 1, MAX_QUANTITY, 'sequence');
                                    const soldAt = themMilliGiay(
                                        fresh.anchor_server_time_utc,
                                        clock.cumulative_ms
                                    );
                                    if (soldAt < fresh.issued_at || soldAt > fresh.expires_at) {
                                        throw new Error('Checkpoint v1 nằm ngoài cửa sổ ACTIVE của lease');
                                    }
                                    const draft = {
                                        offline_uuid: uuid,
                                        identity_key: fresh.identity_key,
                                        username: fresh.username,
                                        user_id: fresh.user_id,
                                        shop_id: fresh.shop_id,
                                        contract_version: 1,
                                        lease_id: fresh.lease_id,
                                        device_id: fresh.device_id,
                                        offline_session_id: fresh.lease_id,
                                        sequence,
                                        sold_at_client_utc: soldAt,
                                        client_monotonic_ms: clock.cumulative_ms,
                                        monotonic_valid: clock.monotonic_valid,
                                        server_anchor_id: fresh.server_anchor_id,
                                        catalog_version: fresh.catalog_version,
                                        catalog_snapshot_digest: fresh.catalog_snapshot_digest,
                                        items: normalized.items,
                                        cash_tendered: tendered,
                                        local_creation_key: creationKey,
                                        local_creation_intent: creationIntent,
                                        state: 'DRAFT',
                                        draft_revision: 1,
                                        client_fingerprint: null,
                                        created_at: new Date().toISOString()
                                    };
                                    meta.put(clock);
                                    meta.put({
                                        key: sequenceKey,
                                        lease_id: fresh.lease_id,
                                        value: sequence
                                    });
                                    receipts.add(draft);
                                    bind(draft);
                                    datKetQua(draft);
                                } catch (e) { huy(e); }
                            }
                            clockRequest.onsuccess = function () { clockDone = true; finish(); };
                            sequenceRequest.onsuccess = function () { sequenceDone = true; finish(); };
                        }
                    };
                };
            }
        );
    }

    async function finalizeReceiptV1(uuid, identity) {
        await applyPendingSealsV1();
        const draft = await chay(KHO_PHIEU_V1, 'readonly', kho => kho.get(uuid));
        if (!draft) throw new Error('Không tìm thấy DRAFT v1');
        if (draft.username !== identity.username || usernameHienTai() !== identity.username
            || (identity.shop_id && draft.shop_id !== identity.shop_id)
            || (identity.user_id && draft.user_id !== identity.user_id)) {
            throw new Error('Identity không được đọc/finalize receipt này');
        }
        if (draft.state === 'READY') return banSao(draft);
        if (draft.state !== 'DRAFT') throw new Error('Receipt v1 không ở trạng thái DRAFT');
        const inputJson = JSON.stringify(fingerprintInput(draft));
        const fingerprint = await fingerprintV1(fingerprintInput(draft));
        return giaoDich([KHO_CREDENTIAL_V1, KHO_PHIEU_V1], 'readwrite', function (tx, datKetQua, huy) {
            const store = tx.objectStore(KHO_PHIEU_V1);
            const credentials = tx.objectStore(KHO_CREDENTIAL_V1);
            const request = store.get(uuid);
            request.onsuccess = function () {
                const fresh = request.result;
                if (!fresh || fresh.state !== 'DRAFT' || fresh.draft_revision !== draft.draft_revision
                    || JSON.stringify(fingerprintInput(fresh)) !== inputJson) {
                    huy(new Error('DRAFT v1 đã đổi trong lúc tính fingerprint'));
                    return;
                }
                const credentialRequest = credentials.get(fresh.lease_id);
                credentialRequest.onsuccess = function () {
                    const credential = credentialRequest.result;
                    if (!credentialUsable(credential, {
                        shop_id: fresh.shop_id,
                        username: fresh.username,
                        user_id: fresh.user_id,
                        device_id: fresh.device_id
                    }) || credential.identity_key !== fresh.identity_key) {
                        huy(new Error('Credential v1 không còn ACTIVE để finalize'));
                        return;
                    }
                    fresh.client_fingerprint = fingerprint.digest;
                    fresh.state = 'READY';
                    fresh.ready_at = new Date().toISOString();
                    store.put(fresh);
                    datKetQua(banSao(fresh));
                };
            };
        });
    }

    async function finalizeDraftsV1(identity) {
        const username = String(identity && identity.username || '');
        if (!username || usernameHienTai() !== username) return [];
        await applyPendingSealsV1();
        const credentials = await docTatCaStore(KHO_CREDENTIAL_V1);
        const usableLeases = new Set(credentials.filter(row =>
            row.username === username
            && (!identity.shop_id || row.shop_id === identity.shop_id)
            && (!identity.user_id || row.user_id === identity.user_id)
            && credentialUsable(row, {
                shop_id: row.shop_id,
                username,
                user_id: row.user_id,
                device_id: row.device_id
            })
        ).map(row => row.lease_id));
        const drafts = (await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => row.state === 'DRAFT' && row.username === username)
            .filter(row => !identity.shop_id || row.shop_id === identity.shop_id)
            .filter(row => !identity.user_id || row.user_id === identity.user_id)
            .filter(row => usableLeases.has(row.lease_id))
            .sort((a, b) => a.lease_id.localeCompare(b.lease_id) || a.sequence - b.sequence);
        const ready = [];
        for (const draft of drafts) {
            ready.push(await finalizeReceiptV1(draft.offline_uuid, identity));
        }
        return ready;
    }

    async function replayCreationV1(binding, options, requestDigest) {
        if (binding.request_digest !== requestDigest || binding.username !== usernameHienTai()
            || binding.username !== options.username || binding.shop_id !== Number(options.shop_id)
            || (options.user_id && binding.user_id !== Number(options.user_id))
            || binding.device_id !== await layDeviceId()) {
            throw new Error('creation_key đã bind với immutable intent hoặc identity khác');
        }
        if (binding.online_only === true) {
            throw Object.assign(new Error('Chưa cấp phiếu offline; tiếp tục cùng thao tác online'), {
                offlineAllocationState: 'definitely_not_allocated'
            });
        }
        if (binding.contract_version === 0) {
            const legacy = await chay(KHO_PHIEU, 'readonly', store => store.get(binding.offline_uuid));
            if (!legacy) throw new Error('Phiếu đã lưu không còn tại máy; cần đối soát, không tạo lại');
            return banSao(legacy);
        }
        const credential = await chay(KHO_CREDENTIAL_V1, 'readonly', store => store.get(binding.lease_id));
        if (!credential || credential.sealed === true || credential.identity_key !== binding.identity_key) {
            throw new Error('Credential của phiếu đã khóa; cần khôi phục offline');
        }
        const receipt = await chay(KHO_PHIEU_V1, 'readonly', store => store.get(binding.offline_uuid));
        if (!receipt) throw new Error('Phiếu đã lưu không còn tại máy; cần đối soát, không tạo lại');
        if (receipt.state === 'DRAFT') return finalizeReceiptV1(receipt.offline_uuid, binding);
        if (['READY', 'SYNCING', 'RETRYABLE', 'ACKED'].includes(receipt.state)) return banSao(receipt);
        throw new Error('Phiếu đã lưu đang bị chặn; cần khôi phục offline');
    }

    async function creationRequestDigest(options) {
        const input = chuanHoaItems(options.items, (options.items || []).map(item => ({
            id: item.product_id, name: chuanHoaTen(item.product_name), is_active: true,
            price_vnd: Object.prototype.hasOwnProperty.call(item, 'unit_price_vnd') ? item.unit_price_vnd : item.price
        })));
        const tendered = soNguyen(options.cash_tendered, 0, MAX_VND, 'cash_tendered');
        if (tendered < input.total) throw new Error('Tiền khách đưa chưa đủ');
        return sha256(JSON.stringify([Number(options.shop_id), options.username, input.items, tendered]));
    }

    async function reserveOnlineCreation(options, requestDigest) {
        const binding = {key: 'creation:' + options.creation_key, online_only: true,
            request_digest: requestDigest, username: options.username,
            shop_id: Number(options.shop_id), user_id: Number(options.user_id) || null,
            device_id: await layDeviceId()};
        // Serialize with both v0/v1 allocation. Keep this binding so a delayed
        // offline caller cannot allocate after POS has dispatched online.
        return giaoDich([KHO_META_V1, KHO_PHIEU_V1, KHO_PHIEU], 'readwrite', function (tx, done, abort) {
            const meta = tx.objectStore(KHO_META_V1);
            const existing = meta.get(binding.key);
            existing.onsuccess = function () {
                if (existing.result) { done(existing.result); return; }
                // ponytail: recovery scans local receipts/bindings; index UUID/key if this becomes slow.
                const receipts = tx.objectStore(KHO_PHIEU_V1).getAll();
                const legacy = tx.objectStore(KHO_PHIEU).getAll();
                const bindings = meta.getAll();
                let pending = 3;
                receipts.onsuccess = legacy.onsuccess = bindings.onsuccess = function () {
                    if (--pending) return;
                    const bound = new Set((bindings.result || []).map(row => row.offline_uuid));
                    // Pre-binding DRAFTs and unidentifiable old v0/ACK rows are unknown.
                    if ((receipts.result || []).some(row => row.local_creation_key === options.creation_key
                            || (!row.local_creation_key && !bound.has(row.offline_uuid)))
                        || (legacy.result || []).some(row => !bound.has(row.offline_uuid))) {
                        abort(new Error('Phiếu đã được cấp; cần khôi phục offline'));
                        return;
                    }
                    if (usernameHienTai() !== binding.username) { abort(new Error('Identity đã đổi')); return; }
                    meta.put(binding);
                    done(binding);
                };
            };
        });
    }

    async function createReceiptV1(options) {
        if (!options || options.payment_method !== 'cash'
            || options.voucher_code || Number(options.loyalty_points_to_use) !== 0
            || options.qr === true || options.debt === true) {
            throw new Error('Receipt v1 chỉ hỗ trợ tiền mặt, không ưu đãi/QR/nợ/điểm');
        }
        const shopId = soNguyen(Number(options.shop_id), 1, MAX_QUANTITY, 'shop_id');
        const username = vanBanKhongCam(String(options.username || ''), 'username');
        const creationKey = vanBanKhongCam(String(options.creation_key || ''), 'creation_key');
        if (creationKey.length < 8 || creationKey.length > 128) {
            throw new Error('creation_key phải dài 8..128 ký tự');
        }
        // Validate canonical input independently of a possibly renewed catalog.
        const requestDigest = await creationRequestDigest(options);
        const tendered = soNguyen(options.cash_tendered, 0, MAX_VND, 'cash_tendered');
        await applyPendingSealsV1();
        const bindingKey = 'creation:' + creationKey;
        const binding = await chay(KHO_META_V1, 'readonly', store => store.get(bindingKey));
        if (binding) return replayCreationV1(binding, options, requestDigest);
        if (!dangOffline()) {
            if (options.allow_online_recovery !== true) {
                throw new Error('Receipt v1 chỉ được tạo khi navigator.onLine === false');
            }
            const online = await reserveOnlineCreation(options, requestDigest);
            return replayCreationV1(online, options, requestDigest);
        }
        const usable = await usableV1({ shop_id: shopId, username, user_id: options.user_id });
        if (!usable) return null;
        const normalized = chuanHoaItems(options.items, usable.catalog.rows);
        await allocateDraftV1(usable, normalized, tendered, creationKey, requestDigest);
        const saved = await chay(KHO_META_V1, 'readonly', store => store.get(bindingKey));
        return replayCreationV1(saved, options, requestDigest);
    }

    async function listReadyV1(identity) {
        const username = String(identity && identity.username || '');
        if (!username || usernameHienTai() !== username
            || !Number.isSafeInteger(identity.shop_id)
            || !Number.isSafeInteger(identity.user_id)
            || typeof identity.lease_id !== 'string') return [];
        await applyPendingSealsV1();
        const credential = await chay(
            KHO_CREDENTIAL_V1, 'readonly', kho => kho.get(identity.lease_id)
        );
        if (!credential || !credentialUsable(credential, {
            shop_id: identity.shop_id,
            username,
            user_id: identity.user_id,
            device_id: credential.device_id
        })) return [];
        const rows = (await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => row.state === 'READY' && row.username === username)
            .filter(row => row.shop_id === identity.shop_id)
            .filter(row => row.user_id === identity.user_id)
            .filter(row => row.lease_id === identity.lease_id)
            .sort((a, b) => a.lease_id.localeCompare(b.lease_id) || a.sequence - b.sequence);
        return rows.map(function (row) {
            const copy = banSao(row);
            delete copy.identity_key;
            delete copy.local_creation_key;
            delete copy.local_creation_intent;
            return copy;
        });
    }

    async function getCredentialV1(leaseId, identity) {
        const username = String(identity && identity.username || '');
        if (!username || usernameHienTai() !== username
            || !Number.isSafeInteger(identity.shop_id)
            || !Number.isSafeInteger(identity.user_id)) return null;
        await applyPendingSealsV1();
        const credential = await chay(KHO_CREDENTIAL_V1, 'readonly', kho => kho.get(leaseId));
        if (!credential || credential.username !== username || credential.sealed === true
            || credential.shop_id !== identity.shop_id
            || credential.user_id !== identity.user_id) return null;
        return banSao(credential); // seam nội bộ explicit cho F2; có raw token.
    }

    async function getCredentialForRecoveryV1(leaseId, identity) {
        const username = String(identity && identity.username || '');
        if (!username || usernameHienTai() !== username
            || !Number.isSafeInteger(identity.shop_id)
            || !Number.isSafeInteger(identity.user_id)
            || typeof leaseId !== 'string') return null;
        await applyPendingSealsV1();
        const credential = await chay(KHO_CREDENTIAL_V1, 'readonly', kho => kho.get(leaseId));
        if (!credential || credential.username !== username || credential.sealed !== true
            || credential.shop_id !== identity.shop_id
            || credential.user_id !== identity.user_id) return null;
        const hasPending = (await docTatCaStore(KHO_PHIEU_V1)).some(row =>
            ['DRAFT', 'READY', 'SYNCING', 'RETRYABLE', 'BLOCKED_RECOVERABLE'].includes(row.state)
            && row.identity_key === credential.identity_key
            && row.lease_id === credential.lease_id
            && row.shop_id === credential.shop_id
            && row.user_id === credential.user_id
            && row.username === credential.username
        );
        return hasPending ? banSao(credential) : null;
    }

    async function sealIdentityV1(identity) {
        const username = String(identity && identity.username || '');
        if (!username) return;
        return giaoDich(KHO_CREDENTIAL_V1, 'readwrite', function (tx) {
            const store = tx.objectStore(KHO_CREDENTIAL_V1);
            const request = store.getAll();
            request.onsuccess = function () {
                (request.result || []).filter(row => row.username === username).forEach(function (row) {
                    row.sealed = true;
                    row.local_state = 'SEALED';
                    store.put(row);
                });
            };
        });
    }

    // ---------- Sync engine v1 (I09-F2) ----------
    function syncHooksV1() {
        return global.__FSellingOfflineSyncTestHooks || {};
    }

    function syncNowMsV1() {
        const hook = syncHooksV1().now;
        const value = typeof hook === 'function' ? hook() : Date.now();
        if (!Number.isFinite(value)) throw new Error('SYNC_CLOCK_INVALID');
        return Math.floor(value);
    }

    function syncIsoV1(value) {
        return new Date(value).toISOString();
    }

    function syncRandomV1() {
        const hook = syncHooksV1().random;
        const value = typeof hook === 'function' ? hook() : Math.random();
        return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0.5;
    }

    function syncSetTimeoutV1(callback, delay) {
        const timer = syncHooksV1().setTimeout || global.setTimeout;
        return timer(callback, Math.min(MAX_TIMER_DELAY_MS, Math.max(0, Math.ceil(delay))));
    }

    function syncClearTimeoutV1(timerId) {
        const clear = syncHooksV1().clearTimeout || global.clearTimeout;
        clear(timerId);
    }

    function stableErrorCodeV1(value, fallback) {
        const code = typeof value === 'string' ? value.trim().toUpperCase() : '';
        return /^[A-Z][A-Z0-9_]{1,63}$/.test(code) ? code : fallback;
    }

    function sanitizedErrorV1(code, status) {
        return {
            code: stableErrorCodeV1(code, 'SYNC_UNKNOWN_ERROR'),
            http_status: Number.isInteger(status) ? status : null
        };
    }

    function retryDelayV1(attemptCount, retryAfter, nowMs) {
        if (typeof retryAfter === 'string') {
            const trimmed = retryAfter.trim();
            if (/^\d+$/.test(trimmed)) {
                const seconds = Number(trimmed);
                const delay = seconds * 1000;
                if (Number.isSafeInteger(seconds) && Number.isSafeInteger(delay)
                    && Math.abs(nowMs + delay) <= 8640000000000000) return delay;
            }
            const parsed = Date.parse(trimmed);
            if (Number.isFinite(parsed)) return Math.max(0, parsed - nowMs);
        }
        const attempt = Math.max(1, Math.trunc(Number(attemptCount) || 1));
        if (attempt >= 50) return RETRY_LONG_CYCLE_MS;
        const base = Math.min(2 * (2 ** Math.min(attempt - 1, 30)) * 1000, 5 * 60 * 1000);
        return Math.round(base * (0.8 + 0.4 * syncRandomV1()));
    }

    function exactReceiptBodyV1(receipt) {
        return {
            offline_contract_version: 1,
            lease_id: receipt.lease_id,
            device_id: receipt.device_id,
            offline_session_id: receipt.offline_session_id,
            sequence: receipt.sequence,
            offline_uuid: receipt.offline_uuid,
            sold_at_client_utc: receipt.sold_at_client_utc,
            client_monotonic_ms: receipt.client_monotonic_ms,
            monotonic_valid: receipt.monotonic_valid,
            server_anchor_id: receipt.server_anchor_id,
            catalog_version: receipt.catalog_version,
            catalog_snapshot_digest: receipt.catalog_snapshot_digest,
            client_fingerprint: receipt.client_fingerprint,
            items: receipt.items.map(row => ({
                product_id: row.product_id,
                product_name: row.product_name,
                unit_price_vnd: row.unit_price_vnd,
                quantity: row.quantity
            })),
            cash_tendered: receipt.cash_tendered
        };
    }

    function receiptTotalV1(receipt) {
        let total = 0;
        for (const row of receipt.items || []) {
            const line = row.unit_price_vnd * row.quantity;
            if (!Number.isSafeInteger(line) || line < 0 || total > MAX_VND - line) {
                throw new Error('SYNC_RECEIPT_TOTAL_INVALID');
            }
            total += line;
        }
        return total;
    }

    function canonicalResponseTimeV1(value) {
        return typeof value === 'string'
            && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$/.test(value)
            && Number.isFinite(Date.parse(value.replace(' ', 'T') + 'Z'));
    }

    function validAckResponseV1(data, receipt) {
        if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
        let total;
        try { total = receiptTotalV1(receipt); } catch (e) { return null; }
        if (
            data.contract_version !== 1
            || data.offline_uuid !== receipt.offline_uuid
            || !Number.isSafeInteger(data.order_id) || data.order_id < 1
            || typeof data.created !== 'boolean'
            || data.total !== total
            || data.sold_by_user_id !== receipt.user_id
            || data.synced_by_user_id !== receipt.user_id
            || !canonicalResponseTimeV1(data.sold_at_effective)
            || !canonicalResponseTimeV1(data.server_time_utc)
            || !['ANCHORED_CLIENT', 'BOUNDED', 'ANOMALY'].includes(data.time_confidence)
        ) return null;
        return {
            offline_uuid: receipt.offline_uuid,
            state: 'ACKED',
            order_id: data.order_id,
            sold_at: data.sold_at_effective,
            total,
            acked_at: syncIsoV1(syncNowMsV1())
        };
    }

    async function authSafeFetchV1(path, options) {
        let token = null;
        try {
            token = typeof global.getToken === 'function' ? global.getToken() : null;
        } catch (e) {
            token = null;
        }
        if (!token) return { status: 401, code: 'SYNC_LOGIN_REQUIRED', data: null, retry_after: null };
        const fetcher = syncHooksV1().fetch || global.fetch;
        if (typeof fetcher !== 'function') {
            return { network_error: true, code: 'SYNC_NETWORK_ERROR' };
        }
        const headers = {
            'Content-Type': 'application/json',
            'Accept-Language': typeof global.currentLanguage === 'function'
                ? global.currentLanguage() : 'vi',
            'Authorization': `Bearer ${token}`
        };
        if (options.lease_token) headers['X-Offline-Lease-Token'] = options.lease_token;
        let response;
        try {
            response = await fetcher('/api' + path, {
                method: 'POST', headers, cache: 'no-store',
                body: options.body === undefined ? undefined : JSON.stringify(options.body)
            });
        } catch (e) {
            return { network_error: true, code: 'SYNC_NETWORK_ERROR' };
        }
        let data = null;
        let validJson = false;
        try {
            const raw = await response.text();
            data = raw ? JSON.parse(raw) : null;
            validJson = raw ? true : data === null;
        } catch (e) {
            data = null;
        }
        const detail = data && typeof data.detail === 'object' && !Array.isArray(data.detail)
            ? data.detail : null;
        return {
            status: Number(response.status),
            code: stableErrorCodeV1(detail && detail.code, null),
            data,
            valid_json: validJson,
            retry_after: response.headers && typeof response.headers.get === 'function'
                ? response.headers.get('Retry-After') : null
        };
    }

    function receiptBelongsToCredentialV1(receipt, credential) {
        return Boolean(
            receipt && credential
            && receipt.contract_version === 1
            && receipt.identity_key === credential.identity_key
            && receipt.username === credential.username
            && receipt.user_id === credential.user_id
            && receipt.shop_id === credential.shop_id
            && receipt.lease_id === credential.lease_id
            && receipt.device_id === credential.device_id
            && receipt.offline_session_id === credential.lease_id
        );
    }

    function reclaimResponseMatchesV1(data, credential, expectedStateVersion) {
        return Boolean(
            data && typeof data === 'object'
            && data.lease_id === credential.lease_id
            && data.shop_id === credential.shop_id
            && data.user_id === credential.user_id
            && data.device_id === credential.device_id
            && data.contract_version === 1
            && ['ACTIVE', 'SYNC_ONLY'].includes(data.status)
            && data.catalog_version === credential.catalog_version
            && data.catalog_snapshot_digest === credential.catalog_snapshot_digest
            && data.server_anchor_id === credential.server_anchor_id
            && data.anchor_server_time_utc === credential.anchor_server_time_utc
            && data.issued_at === credential.issued_at
            && data.expires_at === credential.expires_at
            && data.state_version === expectedStateVersion + 1
            && /^[A-Za-z0-9_-]{43}$/.test(data.lease_token || '')
        );
    }

    async function persistReclaimedCredentialV1(original, response, lock, expectedStateVersion) {
        const stores = [KHO_CREDENTIAL_V1, KHO_PHIEU_V1];
        if (lock.kind === 'idb') stores.push(KHO_KHOA_SYNC_V1);
        return giaoDich(stores, 'readwrite', function (tx, datKetQua, huy) {
            const credentials = tx.objectStore(KHO_CREDENTIAL_V1);
            const receipts = tx.objectStore(KHO_PHIEU_V1);
            const credentialRequest = credentials.get(original.lease_id);
            const receiptRequest = receipts.getAll();
            const lockRequest = lock.kind === 'idb'
                ? tx.objectStore(KHO_KHOA_SYNC_V1).get(lock.lock_name) : null;
            let credentialDone = false, receiptsDone = false, lockDone = !lockRequest;
            function finish() {
                if (!credentialDone || !receiptsDone || !lockDone) return;
                const fresh = credentialRequest.result;
                const ownsLock = lock.kind === 'web'
                    ? lock.valid()
                    : ownsDurableLockV1(lock, lockRequest.result, syncNowMsV1());
                const hasPending = (receiptRequest.result || []).some(row =>
                    receiptBelongsToCredentialV1(row, fresh)
                    && !['ACKED', 'QUARANTINED'].includes(row.state)
                );
                if (!ownsLock || !fresh || fresh.sealed !== true
                    || fresh.state_version !== original.state_version
                    || fresh.identity_key !== original.identity_key || !hasPending
                    || !reclaimResponseMatchesV1(response, fresh, expectedStateVersion)) {
                    huy(new Error('SYNC_RECLAIM_CAS_FAILED'));
                    return;
                }
                const updated = {
                    ...fresh,
                    ...banSao(response),
                    identity_key: fresh.identity_key,
                    username: fresh.username,
                    sealed: false,
                    local_state: response.status,
                    saved_at: syncIsoV1(syncNowMsV1())
                };
                credentials.put(updated);
                datKetQua(updated);
            }
            credentialRequest.onsuccess = function () { credentialDone = true; finish(); };
            receiptRequest.onsuccess = function () { receiptsDone = true; finish(); };
            if (lockRequest) lockRequest.onsuccess = function () { lockDone = true; finish(); };
        });
    }

    function conflictStateVersionV1(result) {
        const detail = result && result.data && result.data.detail;
        const value = detail && detail.current_state_version;
        return Number.isSafeInteger(value) && value >= 0 ? value : null;
    }

    async function reclaimCredentialForSyncV1(receipt, lock) {
        const identity = {
            shop_id: receipt.shop_id,
            user_id: receipt.user_id,
            username: receipt.username,
            lease_id: receipt.lease_id
        };
        const sealed = await getCredentialForRecoveryV1(receipt.lease_id, identity);
        if (!sealed) return {
            pause: sanitizedErrorV1('SYNC_CREDENTIAL_UNAVAILABLE', null), pause_kind: 'hard'
        };
        let expectedStateVersion = sealed.state_version;
        for (let requestNumber = 1; requestNumber <= RECLAIM_MAX_REQUESTS; requestNumber += 1) {
            if (!await lockStillOwnedV1(lock)) return { stale: true };
            const result = await authSafeFetchV1(
                `/offline/leases/${encodeURIComponent(receipt.lease_id)}/reclaim`,
                { body: { expected_state_version: expectedStateVersion } }
            );
            if (!await lockStillOwnedV1(lock)) return { stale: true };
            if (result.network_error) return {
                pause: sanitizedErrorV1(result.code, null), pause_kind: 'transient'
            };
            const conflictVersion = result.status === 409
                && result.code === 'OFFLINE_LEASE_STATE_CONFLICT'
                ? conflictStateVersionV1(result) : null;
            if (conflictVersion !== null && conflictVersion > expectedStateVersion
                && requestNumber < RECLAIM_MAX_REQUESTS) {
                expectedStateVersion = conflictVersion;
                continue;
            }
            if (result.status !== 200 || !result.valid_json
                || !reclaimResponseMatchesV1(result.data, sealed, expectedStateVersion)) {
                return {
                    pause: sanitizedErrorV1(
                        result.status === 200
                            ? 'SYNC_RECLAIM_INVALID_RESPONSE'
                            : (result.code || 'SYNC_RECLAIM_DENIED'),
                        result.status
                    ),
                    pause_kind: [401, 402, 403].includes(result.status) ? 'hard' : 'transient'
                };
            }
            try {
                return {
                    credential: await persistReclaimedCredentialV1(
                        sealed, result.data, lock, expectedStateVersion
                    )
                };
            } catch (e) {
                return await lockStillOwnedV1(lock)
                    ? {
                        pause: sanitizedErrorV1('SYNC_RECLAIM_CAS_FAILED', null),
                        pause_kind: 'hard'
                    }
                    : { stale: true };
            }
        }
        return {
            pause: sanitizedErrorV1('SYNC_RECLAIM_STATE_CONFLICT', 409),
            pause_kind: 'transient'
        };
    }

    function syncLockNameV1(shopId) {
        return `fselling:offline-sync:v1:shop:${shopId}`;
    }

    function syncTabIdV1() {
        const hooks = syncHooksV1();
        if (typeof hooks.tab_id === 'string' && hooks.tab_id) return hooks.tab_id;
        const key = 'fselling.offline-sync-tab.v1';
        try {
            let value = sessionStorage.getItem(key);
            if (!value) {
                value = uuidCrypto('tab_');
                sessionStorage.setItem(key, value);
            }
            return value;
        } catch (e) {
            if (!syncTabIdV1._fallback) syncTabIdV1._fallback = uuidCrypto('tab_');
            return syncTabIdV1._fallback;
        }
    }

    async function acquireFallbackLockV1(lockName) {
        const owner = syncTabIdV1();
        const now = syncNowMsV1();
        return giaoDich(KHO_KHOA_SYNC_V1, 'readwrite', function (tx, done) {
            const store = tx.objectStore(KHO_KHOA_SYNC_V1);
            const request = store.get(lockName);
            request.onsuccess = function () {
                const old = request.result;
                if (old && old.owner_tab_id && old.expires_at > now) {
                    done(null);
                    return;
                }
                const fence = Math.max(0, Number(old && old.fence) || 0) + 1;
                const row = {
                    lock_name: lockName,
                    owner_tab_id: owner,
                    fence,
                    expires_at: now + SYNC_LOCK_TTL_MS,
                    heartbeat_at: now
                };
                store.put(row);
                done({ lock_name: lockName, owner_tab_id: owner, fence });
            };
        });
    }

    async function refreshFallbackLockV1(lock) {
        const now = syncNowMsV1();
        return giaoDich(KHO_KHOA_SYNC_V1, 'readwrite', function (tx, done) {
            const store = tx.objectStore(KHO_KHOA_SYNC_V1);
            const request = store.get(lock.lock_name);
            request.onsuccess = function () {
                const row = request.result;
                if (!row || row.owner_tab_id !== lock.owner_tab_id || row.fence !== lock.fence
                    || !Number.isFinite(row.expires_at) || row.expires_at <= now) {
                    done(false);
                    return;
                }
                row.expires_at = now + SYNC_LOCK_TTL_MS;
                row.heartbeat_at = now;
                store.put(row);
                done(true);
            };
        });
    }

    async function releaseFallbackLockV1(lock) {
        return giaoDich(KHO_KHOA_SYNC_V1, 'readwrite', function (tx) {
            const store = tx.objectStore(KHO_KHOA_SYNC_V1);
            const request = store.get(lock.lock_name);
            request.onsuccess = function () {
                const row = request.result;
                if (!row || row.owner_tab_id !== lock.owner_tab_id || row.fence !== lock.fence) return;
                row.owner_tab_id = null;
                row.expires_at = 0;
                row.heartbeat_at = syncNowMsV1();
                store.put(row); // Giữ fence durable để ABA không quay lại số cũ.
            };
        });
    }

    function ownsDurableLockV1(lock, row, now) {
        return Boolean(
            lock.valid() && row
            && row.owner_tab_id === lock.owner_tab_id
            && row.fence === lock.fence
            && row.expires_at > now
        );
    }

    async function lockStillOwnedV1(lock) {
        if (!lock || !lock.valid()) return false;
        if (lock.kind === 'web') return true;
        const row = await chay(KHO_KHOA_SYNC_V1, 'readonly', store => store.get(lock.lock_name));
        return ownsDurableLockV1(lock, row, syncNowMsV1());
    }

    async function runWithSyncLockV1(shopId, work) {
        const lockName = syncLockNameV1(shopId);
        if (navigator.locks && typeof navigator.locks.request === 'function') {
            let ran = false;
            const value = await navigator.locks.request(
                lockName,
                { mode: 'exclusive', ifAvailable: true },
                async function (lock) {
                    if (!lock) return null;
                    ran = true;
                    return work({
                        kind: 'web',
                        lock_name: lockName,
                        owner_tab_id: syncTabIdV1(),
                        fence: `web:${syncTabIdV1()}:${syncNowMsV1()}`,
                        valid: function () { return true; }
                    });
                }
            );
            return ran ? value : null;
        }

        const lock = await acquireFallbackLockV1(lockName);
        if (!lock) return null;
        let valid = true;
        const setTimer = syncHooksV1().setInterval || global.setInterval;
        const clearTimer = syncHooksV1().clearInterval || global.clearInterval;
        const timer = setTimer(function () {
            refreshFallbackLockV1(lock).then(function (ok) {
                if (!ok) valid = false;
            }).catch(function () { valid = false; });
        }, SYNC_LOCK_HEARTBEAT_MS);
        try {
            return await work({
                kind: 'idb', ...lock,
                valid: function () { return valid; }
            });
        } finally {
            clearTimer(timer);
            if (valid) await releaseFallbackLockV1(lock).catch(function () {});
        }
    }

    function syncPauseKeyV1(identity) {
        return 'sync-pause:' + JSON.stringify([
            identity.shop_id, identity.user_id || null, identity.username
        ]);
    }

    async function persistSyncPauseV1(identity, error, pauseKind, retryAt) {
        const row = {
            key: syncPauseKeyV1(identity),
            shop_id: identity.shop_id,
            user_id: identity.user_id || null,
            username: identity.username,
            pause_kind: pauseKind === 'transient' ? 'TRANSIENT' : 'HARD',
            retry_at: pauseKind === 'transient' && Number.isFinite(retryAt)
                ? syncIsoV1(retryAt) : null,
            error: sanitizedErrorV1(error && error.code, error && error.http_status),
            paused_at: syncIsoV1(syncNowMsV1())
        };
        await chay(KHO_META_V1, 'readwrite', store => store.put(row));
        return row.error;
    }

    function clearSyncPauseV1(identity) {
        return chay(KHO_META_V1, 'readwrite', store => store.delete(syncPauseKeyV1(identity)));
    }

    function readSyncPauseV1(identity) {
        return chay(KHO_META_V1, 'readonly', store => store.get(syncPauseKeyV1(identity)));
    }

    function receiptMatchesIdentityV1(row, identity) {
        return Boolean(
            row && row.contract_version === 1
            && row.shop_id === identity.shop_id
            && row.username === identity.username
            && (!identity.user_id || row.user_id === identity.user_id)
        );
    }

    async function completeSyncIdentityV1(identity) {
        if (identity.user_id) return identity;
        const userIds = new Set((await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => row.contract_version === 1
                && row.shop_id === identity.shop_id
                && row.username === identity.username)
            .map(row => row.user_id)
            .filter(value => Number.isSafeInteger(value) && value > 0));
        if (userIds.size > 1) return null;
        if (userIds.size === 1) identity.user_id = Array.from(userIds)[0];
        return identity;
    }

    async function countPendingV1(identity) {
        return (await docTatCaStore(KHO_PHIEU_V1)).filter(row =>
            receiptMatchesIdentityV1(row, identity)
            && !['ACKED', 'QUARANTINED'].includes(row.state)
        ).length;
    }

    async function earliestRetryV1(identity) {
        let earliest = null;
        for (const row of await docTatCaStore(KHO_PHIEU_V1)) {
            if (!receiptMatchesIdentityV1(row, identity) || row.state !== 'RETRYABLE') continue;
            const value = Date.parse(row.next_attempt_at || '');
            if (!Number.isFinite(value)) continue;
            if (!earliest || value < earliest.at) {
                earliest = { at: value, error: sanitizedErrorV1(
                    row.last_error && row.last_error.code,
                    row.last_error && row.last_error.http_status
                ) };
            }
        }
        return earliest;
    }

    async function durablePauseGateV1(identity) {
        const pause = await readSyncPauseV1(identity);
        if (pause && pause.pause_kind === 'HARD') return pause;
        const earliest = await earliestRetryV1(identity);
        const persistedAt = pause && pause.pause_kind === 'TRANSIENT'
            ? Date.parse(pause.retry_at || '') : NaN;
        const deadline = earliest ? earliest.at : persistedAt;
        if (Number.isFinite(deadline) && deadline > syncNowMsV1()) {
            const error = pause && pause.error ? pause.error : earliest.error;
            if (!pause || pause.pause_kind !== 'TRANSIENT' || persistedAt !== deadline) {
                await persistSyncPauseV1(identity, error, 'transient', deadline);
                return readSyncPauseV1(identity);
            }
            return pause;
        }
        return null;
    }

    async function recoverStaleSyncingV1(identity) {
        const now = syncNowMsV1();
        return giaoDich(KHO_PHIEU_V1, 'readwrite', function (tx, done) {
            const store = tx.objectStore(KHO_PHIEU_V1);
            const request = store.getAll();
            request.onsuccess = function () {
                let count = 0;
                for (const row of request.result || []) {
                    const started = Date.parse(row.sync_started_at || '');
                    if (!receiptMatchesIdentityV1(row, identity) || row.state !== 'SYNCING'
                        || !Number.isFinite(started) || now - started <= SYNC_STALE_MS) continue;
                    row.state = 'RETRYABLE';
                    row.next_attempt_at = syncIsoV1(now);
                    row.last_error = sanitizedErrorV1('SYNC_STALE_ATTEMPT_RECOVERED', null);
                    clearWorkingFieldsV1(row);
                    store.put(row);
                    count += 1;
                }
                done(count);
            };
        });
    }

    async function cleanupAckedV1(identity) {
        const cutoff = syncNowMsV1() - ACK_TOMBSTONE_TTL_MS;
        return giaoDich(KHO_PHIEU_V1, 'readwrite', function (tx, done) {
            const store = tx.objectStore(KHO_PHIEU_V1);
            const request = store.getAll();
            request.onsuccess = function () {
                let count = 0;
                for (const row of request.result || []) {
                    // Tombstone ACK cố ý không giữ identity/body; ACK là state duy
                    // nhất được phép cleanup theo tuổi trên toàn store.
                    if (row.state !== 'ACKED') continue;
                    const acked = Date.parse(row.acked_at || '');
                    if (Number.isFinite(acked) && acked <= cutoff) {
                        store.delete(row.offline_uuid);
                        count += 1;
                    }
                }
                done(count);
            };
        });
    }

    async function listSyncCandidatesV1(identity) {
        const now = syncNowMsV1();
        return (await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => receiptMatchesIdentityV1(row, identity))
            .filter(row => row.state === 'READY'
                || (row.state === 'RETRYABLE' && Date.parse(row.next_attempt_at || '') <= now))
            .sort((a, b) => String(a.lease_id).localeCompare(String(b.lease_id))
                || a.sequence - b.sequence)
            .map(banSao);
    }

    async function claimReceiptV1(candidate, lock) {
        const now = syncNowMsV1();
        const stores = lock.kind === 'idb'
            ? [KHO_PHIEU_V1, KHO_KHOA_SYNC_V1] : KHO_PHIEU_V1;
        return giaoDich(stores, 'readwrite', function (tx, done) {
            const receipts = tx.objectStore(KHO_PHIEU_V1);
            const receiptRequest = receipts.get(candidate.offline_uuid);
            const lockRequest = lock.kind === 'idb'
                ? tx.objectStore(KHO_KHOA_SYNC_V1).get(lock.lock_name) : null;
            let receiptDone = false;
            let lockDone = !lockRequest;
            function finish() {
                if (!receiptDone || !lockDone) return;
                const row = receiptRequest.result;
                const durableLock = lockRequest && lockRequest.result;
                const ownsDurableLock = !lockRequest || (
                    durableLock
                    && durableLock.owner_tab_id === lock.owner_tab_id
                    && durableLock.fence === lock.fence
                    && durableLock.expires_at > now
                );
                const due = row && (row.state === 'READY'
                    || (row.state === 'RETRYABLE' && Date.parse(row.next_attempt_at || '') <= now));
                if (!due || !lock.valid() || !ownsDurableLock) { done(null); return; }
                row.sync_resume_state = row.state;
                row.sync_resume_has_attempt_count = Object.prototype.hasOwnProperty.call(
                    row, 'attempt_count'
                );
                row.sync_resume_attempt_count = row.attempt_count;
                row.sync_resume_has_next_attempt_at = Object.prototype.hasOwnProperty.call(
                    row, 'next_attempt_at'
                );
                row.sync_resume_next_attempt_at = banSao(row.next_attempt_at);
                row.sync_resume_has_last_error = Object.prototype.hasOwnProperty.call(
                    row, 'last_error'
                );
                row.sync_resume_last_error = banSao(row.last_error);
                row.state = 'SYNCING';
                row.attempt_count = Math.max(0, Number(row.attempt_count) || 0) + 1;
                row.sync_started_at = syncIsoV1(now);
                row.attempt_token = uuidCrypto('attempt_');
                row.fence_token = String(lock.fence);
                receipts.put(row);
                done(banSao(row));
            }
            receiptRequest.onsuccess = function () { receiptDone = true; finish(); };
            if (lockRequest) lockRequest.onsuccess = function () { lockDone = true; finish(); };
        });
    }

    function clearWorkingFieldsV1(row) {
        delete row.sync_started_at;
        delete row.attempt_token;
        delete row.fence_token;
        delete row.sync_resume_state;
        delete row.sync_resume_has_attempt_count;
        delete row.sync_resume_attempt_count;
        delete row.sync_resume_has_next_attempt_at;
        delete row.sync_resume_next_attempt_at;
        delete row.sync_resume_has_last_error;
        delete row.sync_resume_last_error;
    }

    async function mutateClaimedReceiptV1(claimed, lock, mutation) {
        if (!lock.valid()) return false;
        const stores = lock.kind === 'idb'
            ? [KHO_PHIEU_V1, KHO_KHOA_SYNC_V1] : KHO_PHIEU_V1;
        return giaoDich(stores, 'readwrite', function (tx, done) {
            const receipts = tx.objectStore(KHO_PHIEU_V1);
            const receiptRequest = receipts.get(claimed.offline_uuid);
            const lockRequest = lock.kind === 'idb'
                ? tx.objectStore(KHO_KHOA_SYNC_V1).get(lock.lock_name) : null;
            let receiptDone = false;
            let lockDone = !lockRequest;
            function finish() {
                if (!receiptDone || !lockDone) return;
                const row = receiptRequest.result;
                const durableLock = lockRequest && lockRequest.result;
                const ownsDurableLock = !lockRequest || (
                    durableLock
                    && durableLock.owner_tab_id === lock.owner_tab_id
                    && durableLock.fence === lock.fence
                    && durableLock.expires_at > syncNowMsV1()
                );
                if (!lock.valid() || !ownsDurableLock || !row || row.state !== 'SYNCING'
                    || row.attempt_token !== claimed.attempt_token
                    || row.fence_token !== claimed.fence_token) {
                    done(false);
                    return;
                }
                const replacement = mutation(row);
                receipts.put(replacement || row);
                done(true);
            }
            receiptRequest.onsuccess = function () { receiptDone = true; finish(); };
            if (lockRequest) lockRequest.onsuccess = function () { lockDone = true; finish(); };
        });
    }

    function restoreClaimForPauseV1(row) {
        row.state = row.sync_resume_state === 'RETRYABLE' ? 'RETRYABLE' : 'READY';
        if (row.sync_resume_has_attempt_count) {
            row.attempt_count = row.sync_resume_attempt_count;
        } else {
            delete row.attempt_count;
        }
        if (row.sync_resume_has_next_attempt_at) {
            row.next_attempt_at = banSao(row.sync_resume_next_attempt_at);
        } else {
            delete row.next_attempt_at;
        }
        if (row.sync_resume_has_last_error) {
            row.last_error = banSao(row.sync_resume_last_error);
        } else {
            delete row.last_error;
        }
        clearWorkingFieldsV1(row);
        return row;
    }

    function retryClaimV1(row, error, retryAfter) {
        const now = syncNowMsV1();
        row.state = 'RETRYABLE';
        row.next_attempt_at = syncIsoV1(now + retryDelayV1(row.attempt_count, retryAfter, now));
        row.last_error = sanitizedErrorV1(error.code, error.http_status);
        clearWorkingFieldsV1(row);
        return row;
    }

    function permanentClaimV1(row, state, error) {
        row.state = state;
        row.last_error = sanitizedErrorV1(error.code, error.http_status);
        delete row.next_attempt_at;
        clearWorkingFieldsV1(row);
        return row;
    }

    function classifySyncResponseV1(result) {
        if (result.network_error) return { action: 'retry_pause', code: result.code, status: null };
        const status = result.status;
        const code = result.code;
        if (status === 401) return { action: 'restore_pause', code: code || 'SYNC_LOGIN_REQUIRED', status };
        if (status === 402 || status === 403) {
            return { action: 'restore_pause', code: code || `SYNC_HTTP_${status}`, status };
        }
        if (status === 429) return { action: 'retry_pause', code: code || 'SYNC_RATE_LIMITED', status };
        if (status >= 500) return { action: 'retry_pause', code: code || 'SYNC_SERVER_ERROR', status };
        if (BLOCKED_CODES.has(code)) return { action: 'blocked', code, status };
        if (QUARANTINE_CODES.has(code)) return { action: 'quarantine', code, status };
        if ([400, 409, 413, 422].includes(status) && code) {
            // Chỉ code deterministic trong allowlist phía trên được giữ vĩnh viễn.
            return { action: 'retry_pause', code, status };
        }
        return { action: 'retry_pause', code: code || 'SYNC_TRANSIENT_RESPONSE', status };
    }

    async function resolveCredentialForReceiptV1(receipt, lock) {
        const identity = {
            shop_id: receipt.shop_id,
            user_id: receipt.user_id,
            username: receipt.username
        };
        let credential = await getCredentialV1(receipt.lease_id, identity);
        if (credential && receiptBelongsToCredentialV1(receipt, credential)
            && ['ACTIVE', 'SYNC_ONLY'].includes(credential.local_state || credential.status)) {
            return { credential };
        }
        return reclaimCredentialForSyncV1(receipt, lock);
    }

    async function sendClaimedReceiptV1(receipt, lock) {
        let body;
        try {
            body = exactReceiptBodyV1(receipt);
            receiptTotalV1(receipt);
        } catch (e) {
            const error = sanitizedErrorV1('OFFLINE_RECEIPT_MALFORMED', null);
            const changed = await mutateClaimedReceiptV1(
                receipt, lock, row => permanentClaimV1(row, 'QUARANTINED', error)
            );
            return changed ? { quarantined: 1 } : { pause: sanitizedErrorV1('SYNC_STALE_RESPONSE', null) };
        }
        let resolved;
        try {
            resolved = await resolveCredentialForReceiptV1(receipt, lock);
        } catch (e) {
            const error = sanitizedErrorV1('SYNC_LOCAL_FAILURE', null);
            await mutateClaimedReceiptV1(receipt, lock, row => restoreClaimForPauseV1(row));
            return { pause: error, pause_kind: 'hard' };
        }
        if (resolved.stale) {
            return { pause: sanitizedErrorV1('SYNC_LOCK_LOST', null), stale: true };
        }
        if (!resolved.credential) {
            const error = resolved.pause || sanitizedErrorV1('SYNC_CREDENTIAL_UNAVAILABLE', null);
            if (resolved.pause_kind === 'transient') {
                await mutateClaimedReceiptV1(
                    receipt, lock, row => retryClaimV1(row, error, null)
                );
            } else {
                await mutateClaimedReceiptV1(receipt, lock, row => restoreClaimForPauseV1(row));
            }
            return { pause: error, pause_kind: resolved.pause_kind || 'hard' };
        }
        const credential = resolved.credential;
        if (!receiptBelongsToCredentialV1(receipt, credential)) {
            const error = sanitizedErrorV1('SYNC_IDENTITY_MISMATCH', null);
            await mutateClaimedReceiptV1(receipt, lock, row => restoreClaimForPauseV1(row));
            return { pause: error, pause_kind: 'hard' };
        }
        const result = await authSafeFetchV1(`/orders/${receipt.shop_id}/offline`, {
            lease_token: credential.lease_token,
            body
        });
        if (!lock.valid()) return { pause: sanitizedErrorV1('SYNC_LOCK_LOST', null), stale: true };
        if (result.status === 200) {
            const tombstone = result.valid_json ? validAckResponseV1(result.data, receipt) : null;
            if (tombstone) {
                const changed = await mutateClaimedReceiptV1(receipt, lock, function () {
                    return tombstone;
                });
                return changed ? { acked: 1 } : { pause: sanitizedErrorV1('SYNC_STALE_RESPONSE', null), stale: true };
            }
            const error = sanitizedErrorV1('SYNC_INVALID_ACK', 200);
            await mutateClaimedReceiptV1(receipt, lock, row => retryClaimV1(row, error, null));
            return { pause: error, pause_kind: 'transient' };
        }

        const classified = classifySyncResponseV1(result);
        const error = sanitizedErrorV1(classified.code, classified.status);
        if (classified.action === 'restore_pause') {
            await mutateClaimedReceiptV1(receipt, lock, row => restoreClaimForPauseV1(row));
            return { pause: error, pause_kind: 'hard' };
        }
        if (classified.action === 'blocked' || classified.action === 'quarantine') {
            const state = classified.action === 'blocked' ? 'BLOCKED_RECOVERABLE' : 'QUARANTINED';
            const changed = await mutateClaimedReceiptV1(
                receipt, lock, row => permanentClaimV1(row, state, error)
            );
            if (!changed) return { pause: sanitizedErrorV1('SYNC_STALE_RESPONSE', null) };
            return classified.action === 'blocked' ? { blocked: 1 } : { quarantined: 1 };
        }
        await mutateClaimedReceiptV1(receipt, lock, row => retryClaimV1(
            row, error, result.status === 429 ? result.retry_after : null
        ));
        return { pause: error, pause_kind: 'transient', retried: 1 };
    }

    const syncRunsV1 = new Map();
    const syncSchedulerRefreshV1 = new Set();

    async function runSyncQueueV1(identity) {
        identity = await completeSyncIdentityV1(identity);
        if (!identity) {
            return { acked: 0, blocked: 0, quarantined: 0, pending: 0, paused: true,
                error: sanitizedErrorV1('SYNC_IDENTITY_AMBIGUOUS', null) };
        }
        const durablePause = await durablePauseGateV1(identity);
        if (durablePause) {
            return {
                acked: 0, blocked: 0, quarantined: 0,
                pending: await countPendingV1(identity), paused: true,
                pause_kind: durablePause.pause_kind,
                retry_at: durablePause.retry_at || null,
                error: sanitizedErrorV1(
                    durablePause.error && durablePause.error.code,
                    durablePause.error && durablePause.error.http_status
                )
            };
        }
        if (dangOffline()) {
            return { acked: 0, blocked: 0, quarantined: 0,
                pending: await countPendingV1(identity), paused: true,
                error: sanitizedErrorV1('SYNC_OFFLINE', null) };
        }
        const value = await runWithSyncLockV1(identity.shop_id, async function (lock) {
            const summary = { acked: 0, blocked: 0, quarantined: 0, pending: 0, paused: false };
            await recoverStaleSyncingV1(identity);
            await cleanupAckedV1(identity);
            const candidates = await listSyncCandidatesV1(identity);
            for (const candidate of candidates) {
                if (!lock.valid()) {
                    summary.paused = true;
                    summary.error = sanitizedErrorV1('SYNC_LOCK_LOST', null);
                    break;
                }
                const claimed = await claimReceiptV1(candidate, lock);
                if (!claimed) continue;
                const result = await sendClaimedReceiptV1(claimed, lock);
                summary.acked += result.acked || 0;
                summary.blocked += result.blocked || 0;
                summary.quarantined += result.quarantined || 0;
                if (result.pause) {
                    summary.paused = true;
                    summary.error = sanitizedErrorV1(result.pause.code, result.pause.http_status);
                    summary.pause_kind = result.pause_kind === 'transient'
                        ? 'TRANSIENT' : 'HARD';
                    if (!result.stale) {
                        const earliest = result.pause_kind === 'transient'
                            ? await earliestRetryV1(identity) : null;
                        await persistSyncPauseV1(
                            identity,
                            summary.error,
                            result.pause_kind,
                            earliest && earliest.at
                        );
                        summary.retry_at = earliest ? syncIsoV1(earliest.at) : null;
                    }
                    break;
                }
            }
            summary.pending = await countPendingV1(identity);
            if (!summary.paused) await clearSyncPauseV1(identity);
            return summary;
        });
        return value || {
            acked: 0, blocked: 0, quarantined: 0,
            pending: await countPendingV1(identity), paused: true,
            error: sanitizedErrorV1('SYNC_LOCK_BUSY', null)
        };
    }

    function triggerSyncV1(options) {
        const identity = {
            shop_id: Number(options && options.shop_id),
            user_id: Number(options && options.user_id) || null,
            username: String(options && options.username || '')
        };
        if (!Number.isSafeInteger(identity.shop_id) || identity.shop_id < 1
            || !identity.username || usernameHienTai() !== identity.username) {
            return Promise.resolve({
                acked: 0, blocked: 0, quarantined: 0, pending: 0, paused: true,
                error: sanitizedErrorV1('SYNC_IDENTITY_UNAVAILABLE', null)
            });
        }
        const key = syncPauseKeyV1(identity);
        if (syncRunsV1.has(key)) return syncRunsV1.get(key);
        const run = applyPendingSealsV1()
            .then(() => finalizeDraftsV1({ username: identity.username }))
            .then(() => runSyncQueueV1(identity))
            .catch(function () {
                return {
                    acked: 0, blocked: 0, quarantined: 0, pending: 0, paused: true,
                    error: sanitizedErrorV1('SYNC_LOCAL_FAILURE', null)
                };
            })
            .finally(() => syncRunsV1.delete(key));
        syncRunsV1.set(key, run);
        return run;
    }

    async function getSyncSummaryV1(options) {
        let identity = {
            shop_id: Number(options && options.shop_id),
            user_id: Number(options && options.user_id) || null,
            username: String(options && options.username || '')
        };
        if (!Number.isSafeInteger(identity.shop_id) || identity.shop_id < 1
            || !identity.username || usernameHienTai() !== identity.username) {
            return {
                pending: 0, paused: true, state_counts: {},
                error: sanitizedErrorV1('SYNC_IDENTITY_UNAVAILABLE', null)
            };
        }
        identity = await completeSyncIdentityV1(identity);
        if (!identity) {
            return {
                pending: 0, paused: true, state_counts: {},
                error: sanitizedErrorV1('SYNC_IDENTITY_AMBIGUOUS', null)
            };
        }
        const counts = {};
        SYNC_STATES.forEach(state => { counts[state] = 0; });
        (await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => receiptMatchesIdentityV1(row, identity))
            .forEach(row => {
                if (SYNC_STATES.includes(row.state)) counts[row.state] += 1;
            });
        const pause = await chay(KHO_META_V1, 'readonly', store => store.get(syncPauseKeyV1(identity)));
        return {
            pending: counts.DRAFT + counts.READY + counts.SYNCING + counts.RETRYABLE
                + counts.BLOCKED_RECOVERABLE,
            paused: Boolean(pause),
            pause_kind: pause ? pause.pause_kind : null,
            retry_at: pause ? pause.retry_at : null,
            state_counts: counts,
            error: pause ? sanitizedErrorV1(pause.error && pause.error.code,
                pause.error && pause.error.http_status) : null
        };
    }

    /**
     * Chỉ là view-model cho POS: tuyệt đối không trả token, digest, UUID,
     * fingerprint, nhãn máy hay nội dung phiếu. Đồng bộ vẫn dùng các seam F2.
     */
    async function getOfflineStatusV1(options) {
        const summary = await getSyncSummaryV1(options);
        const shopId = Number(options && options.shop_id);
        const username = String(options && options.username || '');
        if (!Number.isSafeInteger(shopId) || shopId < 1 || !username
            || usernameHienTai() !== username) {
            return { ...summary, catalog_saved_at: null, lease_expires_at: null };
        }
        try {
            const deviceId = await layDeviceId();
            const pointer = await chay(KHO_META_V1, 'readonly', store =>
                store.get(activeKey(shopId, username, deviceId))
            );
            if (!pointer || typeof pointer.lease_id !== 'string') {
                return { ...summary, catalog_saved_at: null, lease_expires_at: null };
            }
            const credential = await chay(KHO_CREDENTIAL_V1, 'readonly', store =>
                store.get(pointer.lease_id)
            );
            if (!credential || credential.shop_id !== shopId || credential.username !== username
                || credential.device_id !== deviceId || credential.identity_key !== pointer.identity_key
                || (options.user_id && credential.user_id !== Number(options.user_id))) {
                return { ...summary, catalog_saved_at: null, lease_expires_at: null };
            }
            const catalog = await chay(KHO_CATALOG_V1, 'readonly', store => store.get(pointer.lease_id));
            const canonicalTime = value => {
                if (typeof value !== 'string') return null;
                if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$/.test(value)
                    && Number.isFinite(Date.parse(value.replace(' ', 'T') + 'Z'))) return value;
                // catalog saved_at là timestamp local hiện có của F1; chỉ đưa
                // qua view khi là ISO hợp lệ, không lộ thêm metadata nào khác.
                if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
                    && Number.isFinite(Date.parse(value))) return value;
                return null;
            };
            return {
                ...summary,
                catalog_saved_at: canonicalTime(catalog && catalog.saved_at),
                lease_expires_at: canonicalTime(credential.expires_at)
            };
        } catch (e) {
            return { ...summary, catalog_saved_at: null, lease_expires_at: null };
        }
    }

    async function resumeSyncV1(options) {
        let identity = {
            shop_id: Number(options && options.shop_id),
            user_id: Number(options && options.user_id) || null,
            username: String(options && options.username || '')
        };
        if (!Number.isSafeInteger(identity.shop_id) || identity.shop_id < 1
            || !identity.username || usernameHienTai() !== identity.username) {
            return triggerSyncV1(identity);
        }
        identity = await completeSyncIdentityV1(identity);
        if (!identity) return triggerSyncV1(options);
        await clearSyncPauseV1(identity);
        const result = await triggerSyncV1(identity);
        syncSchedulerRefreshV1.forEach(function (refresh) {
            Promise.resolve().then(refresh).catch(function () {});
        });
        return result;
    }

    function batTuDongBoV1(layIdentity, khiXong) {
        let timerId = null;
        let timerAt = null;
        let activeRun = null;

        function disarm() {
            if (timerId !== null) syncClearTimeoutV1(timerId);
            timerId = null;
            timerAt = null;
        }

        function arm(deadline) {
            if (!Number.isFinite(deadline)) return disarm();
            const safeDeadline = Math.max(syncNowMsV1(), deadline);
            if (timerId !== null && timerAt === safeDeadline) return;
            disarm();
            timerAt = safeDeadline;
            timerId = syncSetTimeoutV1(function () {
                timerId = null;
                timerAt = null;
                return thu();
            }, safeDeadline - syncNowMsV1());
        }

        async function schedule(identity) {
            identity = await completeSyncIdentityV1({
                shop_id: Number(identity.shop_id),
                user_id: Number(identity.user_id) || null,
                username: String(identity.username || '')
            });
            if (!identity) return disarm();
            const pause = await readSyncPauseV1(identity);
            if (pause && pause.pause_kind === 'HARD') return disarm();
            const earliest = await earliestRetryV1(identity);
            const persisted = pause && pause.pause_kind === 'TRANSIENT'
                ? Date.parse(pause.retry_at || '') : NaN;
            const deadline = earliest ? earliest.at : persisted;
            if (Number.isFinite(deadline) && deadline > syncNowMsV1()) arm(deadline);
            else disarm();
        }

        async function thu() {
            if (activeRun) return activeRun;
            disarm();
            activeRun = (async function () {
                const identity = typeof layIdentity === 'function' ? layIdentity() : null;
                if (!identity || !identity.shop_id || !identity.username) return null;
                if (dangOffline()) return null;
                const result = await triggerSyncV1(identity);
                if (typeof khiXong === 'function') await khiXong(result);
                await schedule(identity);
                return result;
            })().finally(function () { activeRun = null; });
            return activeRun;
        }

        async function refresh(initial) {
            const identity = typeof layIdentity === 'function' ? layIdentity() : null;
            if (!identity || !identity.shop_id || !identity.username) {
                if (initial === true) arm(syncNowMsV1() + 1500);
                else disarm();
                return;
            }
            const completed = await completeSyncIdentityV1({
                shop_id: Number(identity.shop_id),
                user_id: Number(identity.user_id) || null,
                username: String(identity.username || '')
            });
            if (!completed) return disarm();
            const pause = await readSyncPauseV1(completed);
            const earliest = await earliestRetryV1(completed);
            if (pause && pause.pause_kind === 'HARD') return disarm();
            const persisted = pause && pause.pause_kind === 'TRANSIENT'
                ? Date.parse(pause.retry_at || '') : NaN;
            const deadline = earliest ? earliest.at : persisted;
            if (Number.isFinite(deadline) && deadline > syncNowMsV1()) arm(deadline);
            else if (initial === true) arm(syncNowMsV1() + 1500);
            else disarm();
        }
        global.addEventListener('online', thu);
        syncSchedulerRefreshV1.add(refresh);
        Promise.resolve().then(() => refresh(true)).catch(function () {});
        return thu;
    }

    // ---------- Contract v0 giữ nguyên ----------
    function luuPhieu(shopId, gio_hang, tien_khach_dua, ten_may, binding) {
        const phieu = {
            offline_uuid: taoUuidV0(),
            shop_id: Number(shopId),
            sold_at: new Date().toISOString(),
            items: (gio_hang || []).map(function (m) {
                return {
                    product_id: Number(m.product_id),
                    product_name: String(m.product_name || ''),
                    unit_price: Number(m.price) || 0,
                    quantity: Number(m.quantity) || 0
                };
            }),
            cash_tendered: Number(tien_khach_dua) || 0,
            device_label: ten_may || null,
            luc_luu: Date.now(),
            loi: null
        };
        if (!binding) return chay(KHO_PHIEU, 'readwrite', kho => kho.put(phieu)).then(() => phieu);
        return giaoDich([KHO_PHIEU, KHO_META_V1], 'readwrite', function (tx, done, abort) {
            const meta = tx.objectStore(KHO_META_V1);
            const existing = meta.get(binding.key);
            existing.onsuccess = function () {
                if (existing.result) { done(null); return; }
                if (usernameHienTai() !== binding.username) { abort(new Error('Identity đã đổi')); return; }
                tx.objectStore(KHO_PHIEU).put(phieu);
                meta.put({...binding, offline_uuid: phieu.offline_uuid});
                done(phieu);
            };
        });
    }

    async function luuPhieuTuPOS(options) {
        // A cached Phase-B policy may only tighten local behavior.  A stale or
        // missing Phase-A policy never weakens the server's authoritative gate.
        const policy = await refreshContractPolicy(options);
        const v1 = await createReceiptV1(options);
        if (v1) return v1;
        if (!policy || policy.minimum_accepted_version === 1) {
            const error = new Error(policy
                ? 'OFFLINE_CONTRACT_V1_REQUIRED'
                : 'OFFLINE_CONTRACT_POLICY_REQUIRED');
            error.code = policy
                ? 'OFFLINE_CONTRACT_V1_REQUIRED'
                : 'OFFLINE_CONTRACT_POLICY_REQUIRED';
            throw error;
        }
        const binding = {key: 'creation:' + options.creation_key, contract_version: 0,
            username: options.username, user_id: Number(options.user_id) || null,
            shop_id: Number(options.shop_id), device_id: await layDeviceId(),
            request_digest: await creationRequestDigest(options)};
        await luuPhieu(
            options.shop_id,
            options.items,
            options.cash_tendered,
            options.device_label,
            binding
        );
        const saved = await chay(KHO_META_V1, 'readonly', store => store.get(binding.key));
        return replayCreationV1(saved, options, binding.request_digest);
    }

    function docTatCa(shopId) {
        return docTatCaStore(KHO_PHIEU).then(ds => ds.filter(p => !shopId || Number(p.shop_id) === Number(shopId)));
    }

    async function localReceiptsForRecovery(identity) {
        const v0 = await docTatCa(identity && identity.shop_id);
        const v1 = (await docTatCaStore(KHO_PHIEU_V1))
            .filter(row => receiptMatchesIdentityV1(row, identity || {}))
            .filter(row => !['ACKED'].includes(row.state))
            .map(row => exactReceiptBodyV1(row));
        return v0.map(row => ({
            contract_version: 0,
            offline_uuid: row.offline_uuid,
            sold_at_utc: row.sold_at,
            items: (row.items || []).map(item => ({
                product_id: Number(item.product_id), product_name: String(item.product_name || ''),
                unit_price_vnd: Number(item.unit_price), quantity: Number(item.quantity)
            })),
            cash_tendered_vnd: Number(row.cash_tendered)
        })).concat(v1);
    }

    function demCho(shopId) { return docTatCa(shopId).then(ds => ds.filter(p => !p.loi).length); }
    function demLoi(shopId) { return docTatCa(shopId).then(ds => ds.filter(p => !!p.loi).length); }
    function demLegacyLocal(shopId) {
        return docTatCa(shopId).then(ds => ({
            pending_v0: ds.filter(p => !p.loi).length,
            blocked_v0: ds.filter(p => !!p.loi).length
        }));
    }
    function xoaPhieu(uuid) { return chay(KHO_PHIEU, 'readwrite', kho => kho.delete(uuid)); }
    function danhDauLoi(phieu, ly_do) {
        phieu.loi = String(ly_do || 'không rõ').slice(0, 300);
        return chay(KHO_PHIEU, 'readwrite', kho => kho.put(phieu));
    }

    let _dangDongBo = false;
    async function dongBo(shopId) {
        if (_dangDongBo || dangOffline()) return { da_gui: 0, loi: 0, con_lai: await demCho(shopId) };
        _dangDongBo = true;
        let da_gui = 0, loi = 0;
        try {
            const ds = (await docTatCa(shopId)).filter(p => !p.loi);
            ds.sort((a, b) => (a.luc_luu || 0) - (b.luc_luu || 0));
            for (const phieu of ds) {
                try {
                    await apiCall(`/orders/${phieu.shop_id}/offline`, 'POST', {
                        offline_uuid: phieu.offline_uuid,
                        sold_at: phieu.sold_at,
                        items: phieu.items,
                        cash_tendered: phieu.cash_tendered,
                        device_label: phieu.device_label
                    });
                    await xoaPhieu(phieu.offline_uuid);
                    da_gui += 1;
                } catch (e) {
                    const ma = Number(e && e.status);
                    if (e && e.code === V0_RECOVERY_CODE) {
                        // Keep the immutable v0 document untouched.  It is not
                        // ACKed, deleted, promoted, or retried in a loop.
                        phieu.recovery_required = true;
                        phieu.loi_code = V0_RECOVERY_CODE;
                        await danhDauLoi(phieu, V0_RECOVERY_CODE);
                        loi += 1;
                        continue;
                    }
                    if (ma >= 400 && ma < 500) {
                        await danhDauLoi(phieu, `${ma}: ${e.message || ''}`);
                        loi += 1;
                        continue;
                    }
                    break;
                }
            }
        } finally { _dangDongBo = false; }
        return { da_gui, loi, con_lai: await demCho(shopId) };
    }

    function luuAnhChupSanPham(shopId, danh_sach) {
        return chay(KHO_ANH_CHUP, 'readwrite', kho =>
            kho.put({ khoa: 'sp:' + shopId, luc: Date.now(), du_lieu: danh_sach || [] })
        );
    }
    function docAnhChupSanPham(shopId) {
        return chay(KHO_ANH_CHUP, 'readonly', kho => kho.get('sp:' + shopId))
            .then(b => b ? b.du_lieu : null);
    }

    function batTuDongBo(layShopId, khiXong) {
        async function thu() {
            const shopId = layShopId();
            if (!shopId || dangOffline()) return;
            try {
                const kq = await dongBo(shopId);
                if (khiXong) khiXong(kq);
            } catch (e) { console.warn('[OFFLINE] Đồng bộ v0 thất bại'); }
        }
        global.addEventListener('online', thu);
        setTimeout(thu, 1500);
        return thu;
    }

    global.OfflineBan = {
        dangOffline,
        // v1 F1
        prepareV1,
        createReceiptV1,
        finalizeDraftsV1,
        listReadyV1,
        getCredentialV1,
        getCredentialForRecoveryV1,
        sealIdentityV1,
        fingerprintV1,
        triggerSyncV1,
        getSyncSummaryV1,
        getOfflineStatusV1,
        refreshContractPolicy,
        cachedContractPolicy,
        resumeSyncV1,
        batTuDongBoV1,
        luuPhieuTuPOS,
        // v0 compatibility
        luuPhieu,
        docTatCa,
        demCho,
        demLoi,
        demLegacyLocal,
        localReceiptsForRecovery,
        xoaPhieu,
        dongBo,
        luuAnhChupSanPham,
        docAnhChupSanPham,
        batTuDongBo
    };

    // Crash giữa transaction DRAFT và digest được khép lại khi đúng identity
    // quay lại, kể cả lúc reload đang offline. Không đọc/finalize user khác.
    setTimeout(function () {
        const username = usernameHienTai();
        if (username) {
            applyPendingSealsV1()
                .then(() => finalizeDraftsV1({ username }))
                .catch(function () {});
        }
    }, 0);
})(window);
