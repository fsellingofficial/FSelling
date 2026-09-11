# Device Usability & PWA R5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. If the owner chooses delegation, use superpowers:subagent-driven-development instead. Steps use checkbox (`- [ ]`) syntax for tracking. Do not launch agents just because this header mentions them.

**Goal:** Thực hiện D1–D5 đã duyệt để thao tác đúng trên desktop/tablet/mobile và PWA, không làm yếu hợp đồng tiền, quyền, lifecycle hoặc session/device của Plan 1–4.

**Architecture:** Giữ controller và state machine hiện hữu; sửa utility tại component chung, truyền trạng thái chờ có chủ đích từ transport đến từng screen. SERVICE thay bố cục của cùng DOM; KDS tách freshness khỏi pending; PWA giữ offline engine riêng và dùng worker waiting. Không tái cấu trúc các file lớn hoặc tạo framework UI.

**Tech Stack:** FastAPI/Python, vanilla HTML/CSS/JS, Service Worker, IndexedDB hiện hữu; Node `assert`/`vm` và pytest hiện có; browser UAT local.

**Spec:** `docs/superpowers/specs/2026-09-11-device-usability-pwa-r5-design.md` — DESIGN APPROVED, owner đã duyệt D1–D5. Đọc cả spec và plan trước thực hiện.

## Trạng thái thực hiện — 2026-09-11

D1–D5 đã triển khai trực tiếp trong worktree. Tasks1–8: code + focused checks hoàn tất; Task9: report/review/regression hoàn tất, nghiệm thu release và thiết bị thật còn OPEN. Xem `DEVICE_USABILITY_R5_UAT_REPORT.md` ở root để đối chiếu từng F/AC/U, lệnh test và TEST_GAP. Các checkbox dưới đây mô tả gói bước ban đầu (nhiều dòng gộp cả code và UAT); chưa tích toàn bộ vì phần UAT vật lý/fault chưa chứng minh. Không dùng ô trống để suy rằng runtime chưa triển khai.

Vòng tiếp tục: sửa stepper mobile, hoàn tất SERVICE2món→ghi chú→gửi→giao, KDS recovery và hai ca fault-after-commit có DB idempotency proof. Chi tiết/giới hạn ở phần bổ sung đầu UAT report.

Bổ sung POS: fault-after-pay-commit96/97 đã kiểm DB không nhân ledger; sửa copy unknown và giữ cash_pay_payload qua reload. Focused tests/browser/API evidence xem đầu report.

Owner đã xác nhận full-suite với concurrency PASS sau correction19ca. Chốt local tại nhánh `codex/device-usability-pwa-plan5`; TEST_GAP và release acceptance giữ nguyên.

## Global Constraints

- Baseline đã xác minh khi viết plan: `18eca4aa6299fe9072cbf9dff3faaa62c658ec4e`, ref `codex/session-device-safety-plan4`; linked worktree `C:\Users\nguye\.codex\worktrees\7a90\python_app`, detached HEAD.
- Giữ file spec đang untracked và mọi thay đổi owner xuất hiện sau đó. Không reset/revert, không ghi đè, không push/merge/rebase.
- Chỉ local/demo. Không ngrok, deploy, webhook/chuyển tiền thật, production DB, Gemini hay TTS server.
- `GEMINI_ENABLED=0`, `TTS_SERVER_ENABLED=0`; không thêm framework/dependency/CDN/font mới.
- Giữ operation ID + exact payload/fingerprint khi kết quả chưa xác định; không tạo operation mới để thoát lỗi.
- Không lưu PIN/password/approval token vào draft, log hay local receipt.
- SERVICE không thanh toán/trả hàng; KITCHEN/BAR chỉ các hành động đúng trạm. Không thay backend/schema/endpoint/permission/ledger/offline lease hay transaction fence.
- SW không cache `/api/*`, không xử lý mutation như offline queue, không dùng dữ liệu cũ để cấp quyền.
- D3: slow hint 10s; read deadline 15s; mutation 30s chuyển unknown. Abort không chứng minh server rollback. Không áp deadline mù vào export/upload hoặc background sync.
- D4: SW waiting, không tự reload, không “Cập nhật ngay” cưỡng bức, không tự xóa receipt/site data; không thêm offline F&B.
- D5: P1 trước polish. UI target 44×44; CTA gửi/thu/nhận làm hướng tới 48px. Chữ thường 4.5:1, chữ lớn 3:1. Giá/tổng không ellipsis.
- Viewports: 320×568, 360×640, 390×844, 768×1024, 1024×768, 1366×768; KDS bổ sung 1920×1080. Viewport mô phỏng không phải thiết bị/keyboard/PWA thật.
- Chỉ focused tests trong vòng lặp. Không gọi `test-commit.ps1` hoặc full suite theo từng task. `CLAUDE.md` yêu cầu commit qua script đó, nên gom commit vào gate cuối do owner chạy; không dùng `git add .`/`git add -A` hoặc tự commit tay.
- Source assertions/harness/HTTP 200 không thay browser PASS. TEST_GAP phải còn trong báo cáo nếu chưa quan sát được.
- Đây là kế hoạch, chưa thực thi. Không câu lệnh nào bên dưới được coi là đã chạy chỉ vì xuất hiện trong tài liệu.

## File map và thứ tự

Một plan chung vì utility/transport là dependency của các màn; chia thành task độc lập để review từng lát, không tách nhiều tài liệu phải đồng bộ.

| Task | File sở hữu / trách nhiệm | Dependencies |
|---|---|---|
| 1 | `static/js/session-device-r4.js`, `static/css/session-device-r4.css`, `static/css/style.css`, HTML utility slots | Không |
| 2 | `static/js/api.js`, `tests/js/api-request-r5.test.js` mới: deadline, slow signal và unknown | Không; chưa bật deadline ở caller |
| 3 | `static/js/fnb-station-r1b.js`, CSS/HTML trạm, locale: freshness/pending/error | 1, 2 |
| 4 | `static/js/fnb-r1a.js`, CSS/HTML F&B, locale: view một tay và pending trong ngữ cảnh | 1, 2 |
| 5 | `static/js/pos.js`, CSS/HTML POS, locale: pending checkout, card/sheet/focus | 1, 2 |
| 6 | `static/js/seller.js`, CSS/HTML Seller, locale: xử lý nhanh, partial read | 1, 2 |
| 7 | `static/js/pwa.js`, utility CSS: install/help/update status | 1 |
| 8 | `static/sw.js`, `static/offline.html` mới: waiting và cold fallback | 7 |
| 9 | `DEVICE_USABILITY_R5_UAT_REPORT.md` mới: evidence, regression và release gate | 1–8 |

Locales sửa trong file hiện hữu: `static/js/locales/common.js`, `fnb.js`, `pos.js`, `seller.js`; thêm key song ngữ đúng module. KDS dùng locale F&B và nạp file đó trong `static/fnb-station.html`.

HTML tham chiếu asset chung phải rà toàn bộ: `static/index.html`, `register.html`, `verify.html`, `seller.html`, `pos.html`, `fnb.html`, `fnb-station.html`, `admin.html`. Bump `?v=` của đúng asset sửa tại mọi caller, giữ query parameters khác; không bump toàn bộ assets vô cớ.

## Chuẩn bị trước Task 1

- [ ] Đọc `CLAUDE.md`, spec approved và kiểm Git. Dùng `superpowers:using-git-worktrees` để xác nhận linked worktree hiện có; không tạo worktree thứ hai nếu đã cách ly. Không mất spec/plan untracked khi chuyển môi trường.

```powershell
git status --short
git rev-parse HEAD
git rev-parse --git-dir
git rev-parse --git-common-dir
git show-ref --heads codex/session-device-safety-plan4
```

- [ ] Ghi baseline và danh sách thay đổi sẵn vào report. Nếu HEAD khác đầu vào, xác định commit/thay đổi hợp lệ trước sửa, không reset về baseline.
- [ ] Dùng DB demo đã tạo ở thư mục cha nếu còn và đúng nguồn giả; verify migration trước start. Nếu tạo mới, dùng đường dẫn mới rõ `r5-demo`, không gọi seeder với default DB. Seeder baseline có hai lỗi đã ghi trong spec, không nới password/return validation để seed.

```powershell
$env:DB_PATH='C:/Users/nguye/.codex/worktrees/7a90/plan5-runtime-demo.db'
$env:GEMINI_ENABLED='0'
$env:TTS_SERVER_ENABLED='0'
$env:PYTHONUTF8='1'
$env:LOG_FILE='C:/Users/nguye/.codex/worktrees/7a90/plan5-server.log'
python -m fselling.migration.cli verify
```

Server local cần khóa ký chỉ dành demo do executor tạo trong process env và `ALLOWED_ORIGINS=http://127.0.0.1:8515`; login mới nếu khóa đổi. Không đọc secret thật hoặc nhúng token/password vào report. Kiểm port trước launch; bind `127.0.0.1`.

## Task 1 — Utility không che tác vụ, dialog dễ đọc (F01/F03)

**Files:** Modify `static/js/session-device-r4.js:87–105,247–253`, `static/css/session-device-r4.css`, `static/css/style.css`; tám HTML trong file map; `static/js/locales/common.js`. Test hiện hữu `tests/js/auth-sessions-r4.test.js`; browser U02.

**Interfaces:** Giữ `SessionDeviceR4.openSelf(): Promise<void>` và `openStaff(staffId, displayName)`. Thêm markup slot `[data-ui-tools]`; nhiều slot trong F&B được phép nhưng mỗi view chỉ có slot của nó visible. Không đổi endpoint/session metadata.

- [ ] Lưu ảnh/rect baseline overlap B04 và màu B03; mở dialog trên Seller và F&B. Với CSS thuần không viết test đếm class; use browser red evidence.
- [ ] Đặt slot vào header/menu hiện hữu, ở F&B cả header chung và order header. Tái dùng `<details>` cho menu Khác nếu màn chưa có menu; không thêm custom popup framework.

```html
<details class="ui-tools">
  <summary data-i18n="common.tools">Khác</summary>
  <div data-ui-tools></div>
</details>
```

- [ ] `installLauncher()` tạo button trong từng slot, không append body. Dùng `textContent`/helper `element` hiện hữu, scope slot trong page protected theo check đang có. Dialog open từ slot nào phải trả focus slot đó; render error tại `.session-device-status`.

```js
document.querySelectorAll('[data-ui-tools]').forEach(slot => {
    const button = element('button', 'session-device-launcher', tr('common.sessions.open'));
    button.type = 'button';
    button.addEventListener('click', () => open('self'));
    slot.append(button);
});
```

- [ ] Thay fixed bottom và màu dialog tại component, kiểm cả heading/card/input/disabled/confirm bị global styles ảnh hưởng; màu đỏ chỉ hành động thu hồi, không mọi button.

```css
.session-device-launcher { position: static; min-height: 44px; }
.session-device-dialog { color: #1e293b; background: #fff; }
.session-device-dialog input { color: #1e293b; background: #fff; }
.session-device-dialog :focus-visible { outline: 3px solid #1d4ed8; outline-offset: 2px; }
```

- [ ] Chạy `node tests/js/auth-sessions-r4.test.js`; giữ safe text rendering và R4 seals. Browser U02: tất cả role, dialog Tab/Shift+Tab/Escape/close/rename; read màu computed và rect CTA tại các viewport. Nút thu hồi không tự thao tác khi chỉ mở menu.
- [ ] Bump asset URLs, `git diff --check`, review diff Task 1; chưa commit/full suite.

## Task 2 — Deadline có chủ đích, không mất unknown outcome (F06)

**Files:** Modify `static/js/api.js:206–289`; Create `tests/js/api-request-r5.test.js`; giữ test R4. Chưa bật deadlines trong offline engine, auth polling, upload/export.

**Interfaces:** Mở rộng tương thích `apiCall(endpoint, method='GET', body=null, requestUi={})`. `requestUi` có `timeoutMs?: number`, `slowAfterMs?: number`, `onSlow?: () => void`; không truyền body/token qua callback/event. Ba-argument caller giữ nguyên timing. Giữ Error `status`, `code`, `detail`, `mutationOutcomeUnknown`, `operationId`, `draft`.

- [ ] Tạo VM harness theo `auth-sessions-r4.test.js`: fake local/session storage, document tối thiểu, locale function, setInterval stub; dùng timers giả lưu callback để advance 10/15/30s tức thì, không ngủ thật. `fetch` fake hỗ trợ AbortSignal và body bị treo. Đặt test main catch exit 1.
- [ ] Thêm các check chạy thật dưới harness đã mô tả (callback timer được gọi trực tiếp), trước sửa phải FAIL ở deadline hoặc unknown parse:

```js
const body = { operation_id: 'r5-op-1', quantity: 1 };
// fetch trả headers 200 nhưng text() reject: chưa chứng minh nghiệp vụ thất bại.
context.fetch = async () => ({
  status: 200, ok: true, headers: { get: () => null },
  text: async () => { throw new Error('lost body'); }
});
await assert.rejects(context.apiCall('/fnb/sessions/1/send', 'POST', body),
  e => e.mutationOutcomeUnknown === true && e.operationId === 'r5-op-1');
context.fetch = async () => ({
  status: 503, ok: false, headers: { get: () => null },
  text: async () => '{"detail":"unavailable"}'
});
await assert.rejects(context.apiCall('/orders/1', 'POST', body),
  e => e.status === 503 && e.mutationOutcomeUnknown === true);
```

- [ ] Thêm case: GET timeout không có mutation flag; POST timeout giữ ID/body; malformed 2xx body unknown; 403/409 valid JSON giữ code/detail; 401 vẫn seal+redirect; POST login không giữ password trong Error; slow callback đúng một lần, timer được clear khi success/error; body treo cũng hết hạn.
- [ ] Implement bằng native AbortController và promise race bao trùm fetch **lẫn đọc body**, `finally` clear timer. Chỉ dùng timeout khi caller opt-in, không retry transport tự động. Marker unknown áp cho mutation network/timeout/body-unreadable/5xx/401; response 4xx có payload xác định giữ xử lý business hiện hữu. Không gán `status=408` cho client timeout vì callers đang coi 4xx là definitive.

```js
// Hợp đồng error cho client timeout; đặt trong nhánh deadline của apiCall.
const timeoutError = new Error(t('common.request.timeout'));
timeoutError.code = 'CLIENT_TIMEOUT';
markMutationOutcomeUnknown(timeoutError, method, body);
// Không đặt timeoutError.status thành HTTP 4xx.
```

- [ ] Chạy `node tests/js/api-request-r5.test.js` và `node tests/js/auth-sessions-r4.test.js`; kiểm no unhandled rejection nếu fetch/body hoàn tất muộn. Không attach raw request options vào error.
- [ ] Rà caller trước Task 3–6 bằng `rg -n 'apiCall\(|mutationOutcomeUnknown|definitive|\.status' static/js`; ghi mapping endpoint → read/mutation/export/sync vào report. Chính sách timing opt-in được bật khi screen có nơi hiển thị/recovery phù hợp, không trước đó. Review/bump mọi HTML dùng api.js.

## Task 3 — KDS freshness và pending đúng nghĩa (F04/F05/F11)

**Files:** Modify `static/js/fnb-station-r1b.js`, `static/fnb-station.html`, `static/css/fnb-station-r1b.css`, `static/js/locales/fnb.js`, `tests/js/fnb-station-r1b.test.js`.

**Interfaces:** Giữ `createStationController(deps)`, `start`, `load`, `transition`, `retryPending`, `dispose`, `getState`. Thêm state `lastReadAt:number|null`; render event `{type:'synced', at:number}` cho GET thành công kể cả unchanged. `deps.request` vẫn ba arguments; adapter mount gọi `apiCall` với requestUi. `pending` luôn authoritative cho mutation UI.

- [ ] Trong harness hiện hữu thêm regression độc lập, đặt trước log success. Code baseline phải FAIL vì không có `synced` sau reconnect:

```js
let mode = 'ok'; const events = [];
const c = createStationController({
  request: async () => {
    if (mode === 'down') throw new Error('offline');
    return mode === 'same' ? { changed: false } : queue();
  },
  render: e => events.push(e), setTimeoutFn: () => 1, clearTimeoutFn: () => {}
});
await c.start(1, 'KITCHEN');
mode = 'down'; await assert.rejects(c.load());
mode = 'same'; await c.load();
assert.equal(events.at(-1).type, 'synced');
```

- [ ] Bổ sung request giả 403 và 409: pending definitive được clear và UI event mang error; retry button phải là load/review chứ không mutation. Unknown sau timeout/5xx/401 không clear chỉ dựa status; test pending body deepEqual qua retry và GET không giải quyết pending. Deferred POST + GET changed phải còn khóa mutation.
- [ ] Mỗi GET thành công đặt lastReadAt và emit synced, không rebuild DOM unchanged. Renderer queue không tự reset mutation status; sau render queue luôn derive buttons từ `getState().pending`. Tách `#fnbStationStatus` (read) và một `<p role="status" id="fnbStationMutationStatus">` (write).

```js
state.lastReadAt = Date.now();
deps.render({ type: 'synced', at: state.lastReadAt });
// Nhánh reject mutation: chỉ clear khi không phải unknown và là business 4xx.
const definitive = !error?.mutationOutcomeUnknown
  && Number(error?.status) >= 400 && Number(error?.status) < 500;
```

- [ ] Mount request adapter dùng read 15000 / mutation 30000 và slowAfterMs 10000. Slow mutation viết vào mutation status; slow read viết vào read status; callbacks kiểm controller/context chưa dispose. 401 không render lại protected content sau redirect.
- [ ] Retry đọc entity bằng `controller.load(true)` và session GET đã có; nếu chưa xác định chính thao tác đã thành công, không tự dismiss pending chỉ vì phiếu rời lane. Có exact pending thì retry cùng payload; đã 403/409 thì thông báo rồi tải lại. KDS pending qua reload vẫn RAM-only ở lát này: hiển thị hướng kiểm phiếu sau mở lại, không quảng cáo automatic replay/persistence mới. Đây là giới hạn được spec cho phép, không viết kho credential mới.
- [ ] Chuyển text trạm sang `fnb.js` song ngữ; heading “Bếp”/“Bar” không “đang chờ”; giữ timer có `Z`. Rerender changed giữ focus theo ticket/action nếu tồn tại; nếu phiếu rời lane, focus heading lane và thông báo ngắn.
- [ ] Chạy harness KDS; browser U06/U07: Bếp+Bar, unchanged reconnect, 403/409, pending+poll, empty/50 phiếu; nút đọc từ xa/touch. Bump assets và review.

## Task 4 — SERVICE Món/Bill/Đã gửi và cảnh báo tại bàn (F02/F06/F07)

**Files:** Modify `static/fnb.html`, `static/css/fnb-r1a.css`, `static/js/fnb-r1a.js`, `static/js/locales/fnb.js`, `tests/js/fnb-r1a.test.js`; Test `tests/test_fnb_r1a_ui.py`.

**Interfaces:** Giữ `FnbR1A.createController` và capabilities. View local trong mount `setOrderView(view)` nhận `'menu'|'bill'|'sent'`, không sửa state.server/revision. `pendingMutation`/persistPending và `showPendingRecovery` vẫn sở hữu recovery. Không clone controller/line DOM.

- [ ] Ghi red B06 (bill 28px). Bổ sung harness hiện hữu: đổi view không tạo API call/operation ID; same pending qua đóng/mở view; SERVICE không lộ checkout. Sử dụng mount harness đang có trong `fnb-r1a.test.js`, không tạo DOM framework mới.
- [ ] Tách ba wrapper của nội dung hiện hữu trong `fnbSessionPanel`, đưa sent/service board vào wrapper sent. Thêm các button view và một status visible trong order header; giữ sr-only live announcement riêng tránh đọc đôi.

```html
<nav class="fnb-order-views" aria-label="Nội dung bàn">
  <button type="button" data-order-view="menu" aria-pressed="true">Món</button>
  <button type="button" data-order-view="bill" aria-pressed="false">Bill</button>
  <button type="button" data-order-view="sent" aria-pressed="false">Đã gửi</button>
</nav>
<p id="fnbOrderConnection" role="status"></p>
```

- [ ] Implement `setOrderView` tại mount: validate enum, dataset trên panel, aria-pressed và hidden/inert cho view không active **chỉ mobile**; desktop hiện đầy đủ. media change phải tháo hidden/inert trước chuyển desktop. Lưu scroll từng view trong RAM theo session, không tự gửi món khi đổi view.

```css
@media (max-width: 760px), (max-height: 560px) and (max-width: 1024px) {
  .fnb-order-layout { display: flex; flex-direction: column; min-height: 0; }
  .fnb-order-layout > [data-order-pane] { flex: 1; min-height: 0; overflow-y: auto; }
  .fnb-order-views { display: flex; gap: 8px; }
  .fnb-order-views button { flex: 1; min-height: 44px; }
}
```

- [ ] Dọn các rule grid 54dvh/mobile footer cạnh tranh thay vì append override vô hạn. Footer chỉ view buttons + CTA chính. Hủy phiên trống chỉ khi hợp lệ; handoff text ngắn/contextual, không chiếm thường trực nhiều dòng. Bill phải còn tổng và entry Tính tiền đúng capabilities cho owner/cashier.
- [ ] Thay empty draft “Không có món chưa gửi”; badge Bill dùng unsent + pending khác nhau. F&B floor-error/online/slow phải cập nhật `fnbOrderConnection` khi panel đang mở; refresh read không xóa write unknown. Adapter controller dùng requestUi Task 2, ordinary read 15s/write 30s; giữ exceptions approval/PIN không persist secret.
- [ ] Regression HTTP 200/body malformed giữ exact pending dù error.status=200, 401 giữ R4 transition, definitive 409 buộc xét lại. Không sửa `definitive` chỉ để tests xanh. Test kiểm semantic unknown trước status ở các nhánh hiện có.
- [ ] Chạy `node tests/js/fnb-r1a.test.js`, `python -m pytest -q tests/test_fnb_r1a_ui.py`; nếu static test cần đổi vì structure, thay expectation bằng semantic control/state tương ứng, không bỏ guard assertion.
- [ ] Browser U03/U05: 360×640/390×844, landscape thấp, ghi chú/variant, send/serve, conflict, role handoff; desktop 1024/1366 vẫn song song. Soft keyboard thật còn TEST_GAP nếu chưa có thiết bị. Bump và review.

## Task 5 — POS card/sheet và pending checkout bền (F06/F08)

**Files:** Modify `static/pos.html`, `static/css/pos.css`, `static/js/pos.js`, `static/js/locales/pos.js`; Test `tests/js/pos-offline-ui.test.js`, `tests/test_pos_retry_ui.py`, `tests/test_pos_stabilization_r1_ui.py`, `tests/test_pos_return_r3_ui.py`.

**Interfaces:** Giữ `checkout`, `thuTaoDonDangDo`, `thuTienMatDonDangCho`, `docCheckoutDangDo`, `luuCheckoutDangDo`, `capNhatNutCheckout`. Thêm status `#posCheckoutStatus` trong column, không duplicate pendingCheckoutState. Không thay offline-ban.js synchronization.

- [ ] Reproduce giá bị cắt B08, check active field/CTA trên 1024 ngang và 768 dọc. Ghi baseline các catch checkout theo `rg -n 'definitive|\.status|mutationOutcomeUnknown|luuCheckoutDangDo' static/js/pos.js`.
- [ ] Regression cho body-unreadable 2xx, deadline và 401: retain same operation/exact payload; zero new checkout calls khi busy; no automatic new order on retry. Mở rộng harness dùng pattern vm extraction đang có hoặc harness controller liên quan; kiểm state/call count, không snapshot nguyên source.

```js
// Dùng trong harness checkout với request giả ghi call bodies.
assert.equal(firstAttempt.operation_id, retriedAttempt.operation_id);
assert.deepEqual(retriedAttempt, firstAttempt);
assert.equal(successfulOrderIds.size, 1);
```

`firstAttempt`/`retriedAttempt` là hai body thu từ stub request của ca lost-response; `successfulOrderIds` là Set ID server demo trả. Không hard-code chúng bằng cùng literal để tạo test luôn xanh.
- [ ] Bật requestUi chỉ cho reads/checkout endpoints đã trace; slow/unknown vào status bền gắn order ID/action. Những thao tác tiền chưa có replay contract (ví dụ mở/đóng ca) chỉ cho kiểm ca/lịch sử trước; tuyệt đối không thêm retry POST mù. Mọi catch phải xử lý `mutationOutcomeUnknown` trước trường hợp status definitive.
- [ ] Chuyển price/stock khác hàng, giữ toàn bộ số, name/variant đủ trong giỏ và confirmation. Giảm ưu tiên category, không tăng density bằng font nhỏ hơn. Thí dụ sửa ngay grid hiện có:

```css
.product-grid { grid-auto-rows: minmax(150px, auto); }
.product-card { grid-template-columns: minmax(0, 1fr); }
.product-price { overflow: visible; white-space: normal; text-overflow: clip; }
.product-stock { grid-column: 1; }
```

- [ ] Sửa accessible name back/remove/+/− gồm tên món; sheet có label, inert khi đóng, focus return, receipt/Đơn mới dễ tìm. Không đổi tender math, voucher, return/approval dialogs. Bổ sung inputmode numeric ở field tiền chưa có, không parser mới.
- [ ] Chạy ba pytest UI files bên trên và Node offline UI. Browser U04 cash thiếu/đủ/dư, portrait receipt, pending/re-auth, U10 offline cash theo engine hiện hữu; ledger/stock kiểm riêng khi force lost response. Bump và review.

## Task 6 — Owner mobile và partial dashboard (F09)

**Files:** Modify `static/seller.html`, `static/css/seller.css`, `static/js/seller.js`, `static/js/locales/seller.js`; Test `tests/test_seller_sidebar_ui.py`, `tests/test_dashboard_filters.py`; Create `tests/js/seller-partial-r5.test.js` nếu test hiện hữu không chạy được partial render.

**Interfaces:** Giữ `switchTab(tabId, buttonEl=null)`, `loadActionCenter()`, `loadDashboardShop(id)`, requestId/cache keys. New mobile shortcuts kích hoạt nút tab hiện hữu để giữ permission checks, không tạo navigation/API mới.

- [ ] Red case: dashboard orders thành công, stats lỗi; orders phải giữ, stats hiện lỗi + retry + kỳ dữ liệu chứ không 0. Case đổi shop/ngày khi response cũ về không overwrite. VM harness tách đúng load/render functions theo pattern repo và mock `apiCall`, không cần dependency.

```js
// Sau response lỗi stats trong harness, DOM state phải phản ánh partial.
assert.equal(orderRegion.textContent.includes('#92'), true);
assert.equal(statsError.hidden, false);
assert.equal(statsRetry.disabled, false);
assert.equal(statsRegion.textContent.includes('0 ₫'), false);
```

Các biến trên lấy từ fake document bằng ID của wrappers sẽ đặt trong seller.html; tạo data orders #92 và chưa có stats cũ. Có stats cũ thì test riêng nhãn timestamp thay vì assertion không có 0.
- [ ] Thêm vùng mobile quick action trước stats; controls delegate click tới `tabActionCenter`, `tabDoiSoat` nếu visible/allowed, và `SessionDeviceR4.openSelf`. Count lấy existing badge state, error count không đổi thành 0. Không gọi “hôm nay” cho tổng mặc định mọi thời gian.
- [ ] Chia loading/error/status riêng orders và stats trong `loadDashboardShop` bằng catch từng read, kiểm requestId+shop+query trước mọi render và finally. Retry giữ filter và chỉ load vùng lỗi. Last success timestamp chỉ update khi response vùng đó success. Thêm 15s/10s requestUi cho reads này, không toàn seller mutation.
- [ ] Chart container `min-width:0`, canvas theo kích thước vùng hiện có; bảng rộng overflow trong region có label/tabindex khi cần cuộn keyboard. Không chart library mới. Sidebar mobile đóng dùng inert/aria-hidden đúng, mở trả focus; tránh hidden offscreen navigation còn tab được.
- [ ] Chạy focused tests/harness; browser U01/U13 xem nhanh→chi tiết→back, nhiều shop, partial fail, 390/360 và desktop. Bump/review.

## Task 7 — Install/help và update notification trong utility (F10)

**Files:** Modify `static/js/pwa.js`, `static/css/style.css`, `static/js/locales/common.js`; HTML slot Task 1. Create `tests/js/pwa-r5.test.js`; Test `tests/test_pwa.py`.

**Interfaces:** Giữ `FSellingPWA.coTheCai()`, `caiDat()`, `hienNut()` để không gãy callers; `caiDat()` trả false khi không event. Một deferred event dùng chung mọi slot. Không gọi `xoaCache`/`goCaiDat` từ UI recovery. Standalone vẫn đăng ký SW và theo dõi update trước khi suppress install UI.

- [ ] Harness VM capture window listeners và fake registration. Đặt slot fake document; dispatch beforeinstallprompt có preventDefault/prompt/userChoice. Test prompt chỉ sau click, một event chỉ prompt một lần; dismissed không xóa entry help; localStorage getItem throw không crash.

```js
let promptCalls = 0;
const installEvent = {
  preventDefault() {}, prompt() { promptCalls++; },
  userChoice: Promise.resolve({ outcome: 'dismissed' })
};
listeners.get('beforeinstallprompt')(installEvent);
assert.equal(promptCalls, 0);
assert.equal(context.FSellingPWA.caiDat(), true);
await Promise.resolve();
assert.equal(promptCalls, 1);
```

- [ ] Thay fixed banner bằng button/help region trong utility slots. No-event click mở hướng dẫn: “Trình duyệt này chưa cung cấp nút cài tự động. Dùng menu trình duyệt nếu có mục cài/thêm vào màn hình chính.” Không khẳng định platform unsupported chỉ vì event chưa phát.
- [ ] Dismiss chỉ đóng lời mời, entry vẫn tồn tại; `hienNut()` mở entry. Read/write localStorage đều try/catch. Installed mode không mời cài nhưng vẫn hiển thị update/help. UserChoice rejection phải trả trạng thái rõ, không unhandled rejection.
- [ ] Theo dõi registration.waiting/updatefound/statechange: khi có worker waiting hiện message “Có bản cập nhật. Hoàn tất việc đang làm rồi đóng các cửa sổ F-Selling và mở lại.” Không send skipWaiting, không reload, không kiểm queue rồi tự cho phép forced update.
- [ ] Chạy Node harness + `python -m pytest -q tests/test_pwa.py`. Browser U12 install/no-event/dismiss/standalone theo nền tảng có thật; không fake event rồi ghi install PASS. Bump/review.

## Task 8 — SW waiting và cold offline informational shell (F10)

**Files:** Modify `static/sw.js`; Create `static/offline.html`; Extend `tests/js/pwa-r5.test.js` bằng VM worker harness, `tests/test_pwa.py`. Không sửa manifest start_url hoặc backend routes.

**Interfaces:** Cache resource `/offline.html`, được phục vụ từ `static/offline.html` bởi mount `/` dùng `StaticFiles(directory=STATIC_DIR, html=True)` tại `fselling/main.py`. Failed navigation trả resource này từ cache khi chưa có trang đích trong cache. Chỉ một shell HTML, không chép nội dung vào SW và không thêm backend route.

- [ ] Test worker fetch event: API GET và mọi POST không gọi respondWith; versioned JS dùng đúng URL; navigation online luôn fetch; failed navigation chưa cache không trả login landing giả thành đúng màn. Tests chạy handler, không chỉ regex source.

```js
let intercepted = false;
fetchHandler({
  request: new Request('http://localhost/api/orders/1'),
  respondWith() { intercepted = true; }
});
assert.equal(intercepted, false);
fetchHandler({
  request: new Request('http://localhost/api/orders/1', { method: 'POST' }),
  respondWith() { intercepted = true; }
});
assert.equal(intercepted, false);
```

- [ ] Bỏ unconditional `skipWaiting` và `clients.claim` cho update; native lifecycle là authority. Activate chỉ dọn cache có prefix F-Selling và version cũ khi worker thực sự activate; không xóa cache khác origin app sử dụng, không chạm IndexedDB/localStorage.
- [ ] Thay final fallback `/` của navigation bằng shell tĩnh không auth/business data khi route chưa cache. Shell content:

```html
<!doctype html><html lang="vi"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cần kết nối — F-Selling</title>
<main><h1>Cần kết nối để mở màn này</h1>
<p>Chưa tải được dữ liệu. Đừng tạo lại đơn chỉ vì màn hình chưa mở.</p>
<p>Kết nối lại rồi tải lại địa chỉ hiện tại bằng trình duyệt.</p></main></html>
```

- [ ] Bổ sung cho shell metadata theo các HTML hiện hữu: manifest `/manifest.json`, theme-color, apple-touch-icon và script pwa.js với cùng version mới của Task 7. Dùng CSS tối thiểu inline để thông tin đọc được khi offline. Giữ coverage mọi HTML của `tests/test_pwa.py`; không nạp protected data/API trong shell.
- [ ] Thêm `/fnb`, `/fnb/station/kitchen`, `/fnb/station/bar` vào khung nếu GET local xác minh 200 và static shell; không prefetch API/menu/credential. Cold assets thiếu vẫn informational fallback, không hứa offline đầy đủ.
- [ ] Rerun worker harness và PWA tests; browser U11/U12 warm/cold/server down, installed update với hai tab+pending. Nếu tool không hỗ trợ actual update hoặc platform, đánh TEST_GAP và không release lời hứa đã kiểm. Review SW cache exclusions riêng.

## Task 9 — Kiểm chứng cuối và bàn giao

**Files:** Create `DEVICE_USABILITY_R5_UAT_REPORT.md`; update trạng thái thực hiện trong plan. Spec approved chỉ đổi nếu owner đồng ý thay quyết định, không âm thầm sửa scope để khớp code.

- [ ] Đối chiếu coverage: F01/F03→1, F06→2/4/5/6, F04/F05/F11→3, F02/F07→4, F08→5, F09→6, F10→7/8. AC01–08 và U01–14 đều có cột result/evidence/gap trong report.
- [ ] Chạy regression liên quan phần thay đổi một lần ở gate cuối; không lặp mọi task nếu không có diff mới/failure. Ngoài các focused UI checks đã ghi, chạy nhóm bảo toàn hợp đồng dưới đây khi integration chạm shared api/recovery:

```powershell
python -m pytest -q tests/test_auth_session_lifecycle_r4.py tests/test_auth_session_mutation_fence_r4.py tests/test_approval_session_r4.py tests/test_return_approval_r3.py tests/test_offline_lease_identity.py tests/test_fnb_r1c_checkout.py
git diff --check
```

- [ ] Browser UAT theo spec §8: tiền mặt/đơn demo có ID, F&B send/serve/split/approval, KDS poll/unknown/403, PWA/network/identity, overflow/accessibility. Lost-response cần chứng minh server đã commit trước response mất bằng harness local, rồi một effect sau retry; không chỉ tắt server trước request. Không ép policy/thiết bị để lấy PASS.
- [ ] Report dùng các nhãn `AUTOMATED`, `BROWSER`, `TEST_GAP`, `OPEN`. Với mỗi lỗi đã sửa có baseline và sau sửa, runtime viewport và file/line cuối. Record signature/version asset và DB demo origin, không token/secret.
- [ ] Tự review hợp đồng và diff cuối; dùng `superpowers:requesting-code-review` khi executor tới gate hoàn tất. Independent reviewer chỉ khi mode/owner cho phép; báo rõ nếu chưa có independent review. Không tự nhận safety ACCEPTED chỉ vì tests xanh.
- [ ] Rà Git chỉ đúng files dự định, không DB/log/credentials. Full-suite/commit gate do owner chạy qua script repo sau khi xem report và staging scope; đây là gate riêng, không lệnh tự chạy trong vòng lặp. Không push/merge/deploy.
- [ ] Dừng server/tab do task tạo, reset viewport; giữ DB giả ngoài repo nếu cần tái hiện. Bàn giao file report, checks thực chạy, TEST_GAP, HEAD/branch/dirty state, quyết định release còn mở.

## Self-review của implementation plan

- Coverage đã map F01–F11, AC01–08, U01–14; mỗi task có file, interface, red check và focused validation.
- Spec approved không mở thêm quyền/offline F&B/backend. KDS reload chỉ kiểm phiếu, không hứa auto replay; forced PWA update bị loại.
- RequestUi opt-in chặn tác động upload/export/sync. Unknown 2xx-body-failure/5xx/401 phải được caller hiểu trước status, nếu không dễ giải phóng operation sai.
- Tasks 3–6 dùng đúng signature Task 2; utilities dùng đúng `[data-ui-tools]`; KDS `synced` không xóa pending.
- Execution evidence sau triển khai nằm trong report ở root; baseline giữ trong spec. Không nhận full browser acceptance khi TEST_GAP còn mở.
