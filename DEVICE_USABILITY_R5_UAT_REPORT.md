# Device Usability & PWA R5 — báo cáo kiểm chứng

## Owner gate bản vá offline — 2026-09-12

Owner duyệt ngoại lệ commit trực tiếp sau full-suite PASS để tránh chạy lại gate; vẫn kiểm tra diff và chặn tên file/giá trị secret trước commit. Không sửa script commit hoặc bỏ test khỏi bộ kiểm tra.

Owner đã gửi output `test-commit.ps1 -TestOnly -WithConcurrency` tại worktree7a90: TEST PASS, 2.893,6 giây (48 phút13,6 giây), bỏ qua commit đúng theo TestOnly. Nhánh kiểm tra lại: `codex/device-usability-pwa-plan5`, HEAD5750da9; bản vá offline r5-8 và harness tích hợp v1 vẫn chưa commit. Sau output PASS chỉ cập nhật báo cáo này, không thay runtime/test.

Kết quả này thay thế các ghi chú lịch sử bên dưới còn nói full-suite bản vá chưa chạy. Các TEST_GAP browser v1, reconnect giữa transaction IndexedDB và thiết bị/PWA thật vẫn OPEN. Chưa push/merge/deploy.

## Bổ sung automated POS → offline v1

Harness `tests/js/offline-ban-v1.test.js` đã nối production checkout và luuBanOffline với production OfflineBan.luuPhieuTuPOS/createReceiptV1, dùng FakeIndexedDB có sẵn, lease/catalog fixture hợp lệ. Node harness PASS: fresh cash checkout tạo đúng một receipt_v1, không ghi phieu v0, không dispatch online, dọn giỏ và mở khóa; restored previously-sent operation đi nhánh retry, không tăng receipt count. Không đổi runtime trong bước này.

Đây là automated integration với IndexedDB giả, không đóng TEST_GAP browser v1/PWA vật lý hoặc reconnect giữa transaction. Lượt pytest ban đầu bị sandbox từ chối tạo uploads dưới Temp. Sau cấp quyền, 17 ca trong test_offline_client_v1.py và test_pos_retry_ui.py PASS (exit0), gồm harness tích hợp mới; không cộng vào112ca trước vì có trùng phạm vi. Full-suite cho bản vá vẫn chưa chạy.

## Browser verification bản vá offline r5-8

Dùng Codex in-app browser tại localhost:8515, DB copy riêng `../r5-offline-uat.db`; bộ mô phỏng nằm ngoài repo (`r5-offline-uat-server.py`, `r5-uat-controls.html`). Đây là browser với mạng/fault mô phỏng, không phải ngắt mạng OS/PWA vật lý. Đã quan sát AX và screenshot hóa đơn #99.

1. Fresh offline: chọn 1 Lavie 5.000đ, khách đưa 10.000đ; UI báo lưu thành công, giỏ trống, đúng 1 phiếu cũ chờ gửi (v0). Online lại tự đồng bộ đúng đơn #98, 1 SALE_CASH 5.000đ, kho14→13. Không nhận kết quả này là bằng chứng fresh receipt v1.
2. Create commit rồi mất phản hồi: wrapper ghi upstream200 và thay response503. DB đã có #99 PENDING với operation `504840db-e571-46a8-ad2e-0f7701c3161b`. Reload giữ đơn đang chờ; ô tender lúc creating về trống nên nhập lại10.000đ trước retry (chưa phải pay-unknown). Retry khi offline giữ trạng thái và 0 phiếu chờ; online retry mở hóa đơn đúng #99, 1 CASH_TOPUP 5.000đ, kho giữ12.
3. Mạng trở lại ngay tại entry của helper lưu: v1 guard từ chối vì navigator.onLine=true; UI giữ giỏ Lavie/tender10.000 và nút hoạt động. Bấm lại online hoàn tất #100, 1 CASH_TOPUP 5.000đ, kho11. Đây là timing trước persistence, chưa chứng minh reconnect giữa transaction IndexedDB.

DB cuối có đúng3 đơn mới #98–100 PAID, 3ledger, tender10.000/change5.000 mỗi đơn. Snapshot `../r5-offline-browser-evidence.json`. Không có dữ liệu demo hay test controls trong repo. TEST_GAP còn: fresh offline v1 end-to-end, reconnect giữa IndexedDB transaction, mạng OS và PWA điện thoại thật. Chưa chạy full suite cho bản vá; chưa commit/push/merge.

## Correction sau review thanh toán commit 5750da9

- Review độc lập phát hiện P1: fresh cash checkout khi đã offline bị early return `mutationOutcomeUnknown`, không tới nhánh lưu phiếu. Repro cùng điều kiện: baseline lưu 1 phiếu; 5750da9 lưu 0 phiếu và giữ trạng thái creating.
- Bản sửa chưa commit: fresh checkout đã xác nhận đi thẳng vào `luuBanOffline` trước mọi request/trạng thái online. Retry của giao dịch đã gửi (kể cả reload) giữ nguyên đường exact retry, không chuyển thành phiếu offline. Khóa checkout trong lúc lưu; lỗi lưu giữ giỏ và mở lại nút. Asset POS JS tăng `20260911-r5-8`.
- Test mới RED trước sửa, GREEN sau sửa: fresh offline lưu phiếu/không dispatch/không để trạng thái online; restored unknown retry/không sinh phiếu thứ hai. 112 pytest trong các file retry, loyalty UI, offline client/sync/UI và QR UI PASS; Node pos-request-r5, api-request-r5, pos-offline-ui PASS; syntax và diff check PASS.
- Reviewer độc lập `review_payment_final` (GPT-5.6 Sol) đọc bản sửa và helper persistence, không tìm thấy actionable finding. Đây không phải Astra High acceptance.
- Full-suite PASS ở commit 5750da9 không được tính cho bản vá chưa commit này. Chưa chạy lại full suite, chưa commit/push/merge. Browser/thiết bị thật cho luồng fresh offline mới còn TEST_GAP.


Ngày: 2026-09-11. Trạng thái: **D1–D5 đã triển khai; owner xác nhận full-suite có concurrency PASS; nghiệm thu thiết bị/PWA thật còn OPEN**.

## Chốt baseline Plan 5

Owner đã xác nhận “test pas” sau khi được cung cấp lệnh `test-commit.ps1 -TestOnly -WithConcurrency` cho worktree7a90. Đây là kết quả owner báo, không tự gán số test hoặc thời gian khi chưa có output đầy đủ; cache lastfailed hiện rỗng. Kết quả này thay thế trạng thái full-suite còn chờ ở các ghi chú lịch sử bên dưới.

Nhánh bàn giao: `codex/device-usability-pwa-plan5`, tạo từ HEAD18eca4a và giữ nguyên mọi thay đổi Plan5 đang kiểm chứng. Chốt local qua script repo; không push/merge/deploy. Script commit sẽ tự chạy lại gate trước khi ghi commit; không bỏ qua test hoặc commit tay. Các TEST_GAP thiết bị/PWA thật, ma trận role/device chưa đủ và review độc lập cho correction tiền mặt cuối vẫn giữ nguyên; full-suite PASS không đồng nghĩa production acceptance.

## Correction sau full-suite do owner chạy

Owner chạy `test-commit.ps1 -TestOnly -WithConcurrency` tại worktree7a90/HEAD18eca4a; output gửi lại ghi19FAILED, thời gian1.849,3giây, không commit. Đã đối chiếu cache lastfailed đúng19node IDs.

Nguyên nhân:18ca source assertions còn pin version asset trước R5; receipt Node harness thiếu `scrollIntoView` và selector/focus DOM mới. Cập nhật expected version riêng từng asset (seller/css/common/api r5-5, POS JS/locale r5-7; không bump asset không đổi). Bổ sung DOM mock receipt và assert thực sự scroll/focus. Không xóa/skip test, không thêm version cũ vào HTML để né assertion, không sửa production code trong correction này. Một assertion API cũ bị che sau assertion POS đầu tiên cũng đã cập nhật.

Kết quả chạy lại **đúng19ca owner báo:19PASS**; `git diff --check` đạt. Đây là targeted rerun, chưa phải full-suite mới PASS. Gate full-suite với concurrency vẫn cần owner chạy lại ở cùng worktree trước commit.

## Bổ sung kiểm chứng thu tiền POS — 2026-09-11

- **Lỗi phát hiện bằng browser + DB:** fault sau `/api/orders/96/pay` đã commit200 rồi wrapper trả503. DB đơn96 PAID, nhưng UI cũ nói “chưa ghi nhận thanh toán”. Tiền khách đưa cũng chưa được khóa theo payload retry. Đây là thông báo sai mức chắc chắn và nguy cơ người dùng đổi số tiền trong lúc kết quả chưa rõ.
- **Đã sửa:** copy VI/EN nói chưa xác nhận kết quả, yêu cầu kiểm tra/retry đúng đơn và không thu thêm tiền chỉ vì thiếu hóa đơn. `cash_pay_payload` giữ số tiền gốc trong checkout sessionStorage trước khi gửi; unknown giữ payload qua reload; definitive4xx cho sửa lại. Khóa input/quick amounts khi busy hoặc còn payload chưa xác định. Không thêm endpoint/ledger/offline engine.
- **Browser đơn96:** giá5.000, khách10.000, fault sau pay commit; reload và retry trả hóa đơn96 đúng khách10.000/thối5.000. DB trước/sau cùng ledger id95 CASH_TOPUP5.000, không thêm lần thu. Đơn96 tạo trước bản khóa payload nên số tiền được nhập lại đúng10.000 khi kiểm thử; không nhận durable-lock PASS từ ca này.
- **Browser bản mới đơn97:** giá5.000, khách20.000. Fault sau pay commit, UI báo chưa xác nhận, input và quick amounts disabled. Reload vẫn20.000 disabled, tiền thối15.000, retry khả dụng. DB độc lập trước retry đã PAID, ledger id96 CASH_TOPUP5.000, tồn Lavie14.
- Tab bị đóng khi lượt tương tác được khởi tạo lại nên click retry97 qua browser không thực hiện. Hoàn tất retry qua **API thật** với20.000; assert so toàn tuple order/operation/fingerprint/money, ledger rows và stock trước/sau bằng nhau. Không gọi bước API này là browser PASS. Operation đơn97 `85377d07-79d1-43f2-b8c0-554dbd42751c`, fingerprint `19c7c950d6348a81c56c51a3cd6af9b464b7ebffc2503f4acab515527f26a146`.
- **Checks:** pos-request-r5 harness thêm unknown→serialize/reload→retry giữ20k/10k gốc dù biến UI đổi, definitive400 giải phóng payload; passed. pos-offline-ui harness passed. Pytest9ca retry/stabilization +10ca cashier/return UI passed; includes concurrent create retry/shop lock và cash server totals. `node --check` POS/locales và `git diff --check` đạt. Không cộng rerun thành tổng unique mới.
- Asset POS và locale POS bump `20260911-r5-7`; F&B CSS giữr5-6, asset khácr5-5. Bản sửa tiền mặt mới được self-review và focused checks, chưa review độc lập mới/full-suite.

Bằng chứng số liệu ngoài repo: `../r5-pos-before-retry.json`, `../r5-pos97-before-retry.json`, `../r5-fault-evidence.log`. Remaining TEST_GAP: browser retry97 sau reload bị ngắt, actual network disconnect/body truncation thay vì503, multi-tab, PWA/thiết bị thật và ma trận UAT chưa phủ đủ. Gate full-suite vẫn do owner chạy. Không commit/push/deploy.

## Bổ sung vòng tiếp tục — 2026-09-11

- **BROWSER/DB:** SERVICE `r5service` tại360×640 mở Bàn02/session2, thêm bánh mì20.000+Coca10.000, sửa ghi chú “Cắt đôi — demo UAT!”, rà Bill và gửi. KITCHEN `r5kitchen` xử lý phiếu3: nhận làm→báo hết món→tiếp tục→sẵn sàng giao. Trở về SERVICE xác nhận giao: UI “Đã giao1”, tổng30.000. DB ticket3 status DONE, state_version5, served_by_user_id4, served_at có giá trị. Mỗi nhân viên thao tác đúng role; đổi login để mô phỏng hai nhân viên. Chưa kiểm variant/handoff thanh toán end-to-end.
- **Lỗi mới sửa:** CSS label override display:grid của stepper, media rule ép button grid-column1/-1. Browser trước sửa nút9.2×44px; sau loại stepper khỏi selector label và đặt grid-column:auto, đo được44×60px. Không đổi logic số lượng. CSS F&B bump r5-6 ở2HTML và source test.
- **Fault-after-commit F&B:** wrapper ASGI ngoài repo chờ backend hoàn tất rồi thay success200 bằng503; đọc DB connection riêng trước retry chứng minh đã durable. Session2 send operation `e95382f9-eb7f-4ca1-a6ff-5d244bc01cf2`, fingerprint `4ed7cc4caeb9066b3eeb247d6b656791a20114b087106266668d6f537855611c`, trước retry đã có ticket3 NEW. Browser báo unknown/khóa Gửi/chỉ mời retry pending. Sau retry action logs và fingerprint không đổi, đúng1ticket, draft empty đúng copy, hai dòng sang Đã gửi. Không suy rộng bằng chứng này thành mọi payment/stock path.
- **Fault-after-commit KDS:** start ticket3 đã IN_PROGRESS/version1 trước retry. Poll thành công đưa card vào Đang làm nhưng warning pending vẫn còn, khóa action/shop selector. Retry giữ nguyên action logs/fingerprint và version1, mở lại controls; không tạo thêm transition.
- **KDS read recovery:** tiêm503 cho GET tickets, UI dữ liệu cũ+nút tải lại. Bỏ fault, poll tự hồi Đã đồng bộ; fnb_revision trước/sau đều21. Đây là endpoint failure mô phỏng, không phải OS offline.
- **Responsive:** màn Đã gửi sau giao tại320×568,360×640,390×844,768×1024,1024×768,1366×768 không tràn ngang document. Nút Gửi cao48px, đáy558/630/834/1010/754/754. Chỉ chứng minh trạng thái/màn này, không toàn role×page matrix.
- **Utility:** mobile nút44px, foreground#1e293b/background#fff đã đo và xem dialog screenshot. Escape trả focus launcher. Tab qua các nút có một nhịp activeElement=body/browser chrome trước vào lại dialog; chưa nhận full focus-trap/reader PASS.
- **Checks mới:** 14ca `python -m pytest -q tests/test_fnb_r1a_ui.py` đạt; git diff --check đạt. Không cộng rerun14 vào157ca trước; không lặp safety93/full-suite cho diff CSS này. Correction mới chưa review độc lập bổ sung.

Fixture/wrapper/log ngoài repo tại `C:/Users/nguye/.codex/worktrees/7a90/`: `r5-uat-server.py`, `r5-fault.json`, `r5-fault-evidence.log`, `r5-before-retry.json`, `r5-kds-before-retry.json`, DB demo cũ. Không sửa backend/migration/permission, không đưa credential vào báo cáo. Browser evidence nằm trong lịch sử task; đã khôi phục sau reset công cụ để hoàn thành bước giao.

Kết quả mới thay thế gap tương ứng U03/U06/U07/U08 và AC02/04/05/08 ở bảng lịch sử dưới. Remaining: mọi fault POS/payment, multi-tab, actual installed PWA/OS offline, thiết bị thật, full role/page matrix và owner full-suite gate.

## Phạm vi và môi trường

- Worktree: `C:/Users/nguye/.codex/worktrees/7a90/python_app`, detached HEAD `18eca4aa6299fe9072cbf9dff3faaa62c658ec4e` (baseline Plan 4, xác minh lại cuối vòng).
- Thay đổi chỉ HTML/CSS/JS, focused tests và tài liệu. Không đổi backend, migration, permission, ledger, transaction fence hoặc offline lease; không thêm dependency.
- Runtime local `http://127.0.0.1:8515`, DB giả ngoài repo `C:/Users/nguye/.codex/worktrees/7a90/plan5-runtime-demo.db`. Bản demo riêng được nâng migration/verify tới 0014 (14 migration); không dùng DB production. Gemini/TTS tắt.
- Không commit, push, merge hay deploy. Full-suite/commit là gate do owner chạy, chưa thực hiện trong vòng này.
- Nhãn: `AUTOMATED` là test/source/DB; `BROWSER` là thao tác và quan sát UI thật trong trình duyệt mô phỏng viewport; `TEST_GAP` chưa chứng minh; `OPEN` là quyết định nghiệm thu còn mở. Không coi harness là browser UAT.

## Kết quả theo lỗi và task

| Lỗi / task | Trước sửa | Kết quả và bằng chứng |
|---|---|---|
| F01/F03 — Task 1 | Launcher góc dưới che CTA; dialog chữ tối trên nền tối | Utility trong header/menu native trên 8 trang; dialog màu #1e293b/#fff, nút 44px, focus return. AUTOMATED auth-sessions; BROWSER dialog đọc được, Escape trả focus. Cuối vòng thêm màu riêng cho nút utility; chưa chụp lại sau bản màu cuối. |
| F06 — Task 2 | Fetch/body có thể chờ vô hạn | API opt-in slow10s/read15s/write30s; timeout/5xx/body lỗi giữ unknown, không tự retry; 401 đã nhận header vẫn seal/logout khi body đọc lỗi. Không attach PIN/password/token vào Error.draft. AUTOMATED api-request-r5. |
| F04/F05/F11 — Task 3 | Poll unchanged không hồi freshness; poll có thể xóa warning pending | Mỗi GET thành công cập nhật lastReadAt; trạng thái đọc/ghi riêng, chỉ exact pending được retry, definitive403/409 không nút retry chết. Dynamic/static KDS copy VI/EN. AUTOMATED station harness; BROWSER phiếu #2 NEW → IN_PROGRESS → READY rồi rời lane. |
| F02/F06/F07 — Task 4 | Mobile chia menu/bill quá thấp; draft rỗng bị gọi là bàn chưa có món | Món/Bill/Đã gửi dùng một DOM/controller, hidden+inert, scroll theo view; cảnh báo đọc/ghi trong màn gọi món. Copy đúng “Không có món chưa gửi”. BROWSER desktop 1366×768 đo bill khoảng485px, nút gửi đáy754px; AUTOMATED mobile view switch không phát API/mất state. |
| F08/F06 — Task 5 | Giá bị cắt; unknown dễ bị hiểu như rollback; receipt mobile khó mở khi giỏ trống | Giá/kho tách dòng, card tối thiểu150px, icon có tên món. Unknown không tạo sale offline thứ hai. Giữ receipt trong sheet và dock, focus vào hành động hóa đơn. BROWSER #93/#94/#95 bên dưới; AUTOMATED pos-request-r5 và POS regression. |
| F09/F06 — Task 6 | Promise.all che cả orders khi stats lỗi; mobile thiếu entry nhanh | Orders/stats tải độc lập, giữ dữ liệu đúng shop/filter và timestamp, retry từng vùng; không biến lỗi thành0. Cần xử lý/Đối soát/Phiên trên mobile, sidebar offscreen inert. BROWSER390×844 không tràn toàn trang; AUTOMATED partial-failure, stale shop, retry không vô hiệu sibling. |
| F10 — Task 7/8 | Banner nổi; cập nhật cưỡng bức; fallback landing không đúng ngữ cảnh | Help/install trong utility, event dùng một lần, storage lỗi không crash. SW waiting, không skipWaiting/claim/reload; chỉ dọn cache F-Selling; shell offline thông tin; thêm3route F&B đã GET200 HTML. AUTOMATED pwa-r5; BROWSER thấy “Có bản cập nhật…” hướng dẫn hoàn tất việc rồi đóng cửa sổ. |
| Task 9 | Chưa có bằng chứng sau implementation | Report này, focused regression, independent review corrections, danh sách TEST_GAP và owner gate. |

## Bằng chứng browser / DB

Các screenshot và accessibility snapshots đã xem trực tiếp trong lịch sử task; không xuất thành bộ ảnh riêng. Viewport trình duyệt không thay cho thiết bị vật lý.

| Ca | Quan sát |
|---|---|
| POS ngang1024×768 | Demo ca #2 mở, tiền đầu ca0. Đơn #93: Lavie500ml ×1, tổng5.000, khách đưa5.000, thối0; tiền thiếu khóa hoàn tất. |
| POS dọc768×1024 | #94 tổng5.000, khách đưa10.000, thối5.000. Phát hiện section tiền mặt còn do inline style; đã sửa specificity bằng !important. |
| Receipt cuối768×1024 | #95 hiển thị đủ tổng5.000/khách10.000/thối5.000; không còn section tiền mặt dư. Bốn nút In/Chia sẻ/Sao chép/Đơn mới đều thấy; focus In. Đóng sheet → mở lại từ dock → Đơn mới hoạt động. Không in/chia sẻ ra ngoài. |
| DB read-only sau browser | Orders93/94/95 đều PAID, shift_id2, total_vnd5000; cash_tendered_vnd lần lượt5000/10000/10000; cash_change_vnd0/5000/5000. UI stock Lavie19→18→17→16 qua ba lần bán. Đây là success path, không phải chứng minh response-loss-after-commit. |
| Session dialog | Chữ tối/nền trắng đọc được; Escape trả focus launcher. Chưa chứng minh toàn bộ focus trap/reader/tất cả role. |
| F&B/KDS | Layout desktop và phiếu #2 đã quan sát; chuỗi SERVICE hai món/ghi chú/giao đủ theo U03 chưa hoàn tất. |
| Seller mobile390×844 | Entry nhanh ở đầu màn; không overflow toàn trang (document width375); sidebar ẩn không còn trong AX ở bản đã nạp. Partial response failure chỉ harness. |
| PWA menu | Native waiting-update notice nhìn thấy; không có automatic reload. Chưa install thực trên Android/iOS, chưa thử hai tab có pending. |

Runtime/browser tool bị reset hai lần trong vòng làm việc; nguồn vẫn giữ. Kiểm tra cuối không còn tab iab của task và không listener8515; không đóng tab Chrome của owner. Reset cuối làm mất cơ hội chụp lại màu utility và copy KDS cuối; ghi TEST_GAP, không nhận browser PASS cho chúng.

## Checks đã chạy

1. **64 tests UI/PWA pass**:

```powershell
python -m pytest -q tests/test_pwa.py tests/test_pos_retry_ui.py tests/test_pos_stabilization_r1_ui.py tests/test_pos_return_r3_ui.py tests/test_seller_sidebar_ui.py tests/test_fnb_r1a_ui.py
```

2. **93 tests safety/dashboard pass** (log ngoài repo `../r5-safety-tests.log`):

```powershell
python -m pytest -q tests/test_dashboard_filters.py tests/test_auth_session_lifecycle_r4.py tests/test_auth_session_mutation_fence_r4.py tests/test_approval_session_r4.py tests/test_return_approval_r3.py tests/test_offline_lease_identity.py tests/test_fnb_r1c_checkout.py
```

3. Các Node harness pass: `auth-sessions-r4`, `api-request-r5`, `fnb-station-r1b`, `fnb-r1a`, `pos-request-r5`, `seller-partial-r5`, `pwa-r5` (đường dẫn `tests/js/<name>.test.js`). Có check hành vi exact payload, 401/body lỗi, timeout, secret, permission403, regional retry và async PIN đổi shop.
4. Sau correction cuối chạy lại nhóm PWA/F&B/sidebar liên quan asset/copy: 51 ca đạt, một source assertion còn hardcode biểu thức shop cũ. Đã cập nhật assertion theo shopId được chụp tại submit và chạy lại cả5 ca sidebar đạt; station harness đạt. `node --check` các JS đã sửa và `git diff --check` đạt. Warning hiện hữu Starlette/httpx deprecation và Git LF/CRLF; không phải test failure.
5. Không cộng số lần rerun thành số ca mới; 157 là 64+93 ở hai nhóm trên. Không gọi đây là full-suite.

## Review độc lập

Reviewer `review_r5` (GPT-5.6 Sol) chỉ đọc diff/chạy focused Node. Đã phát hiện và đóng:

- P1: thiếu deadline ở manager PIN/approval →30s/slow10s.
- P2: generic403 còn recoverable reapply →clear pending/draft, permission error đưa vềPOS; lỗi validation vẫn cho sửa.
- P2: thiếu3route F&B trong SW shell →đã bổ sung sau verify200.
- P2: PIN callback cũ ghi đè shop mới →generation+shop guard cho slow/success/error, regression pass.

Reviewer xác nhận không còn blocker trong phần đã review. Đây không phải Astra High gate hoặc chứng nhận production. Hai chỉnh cuối thuần copy KDS và màu utility được self-review/syntax/UI-source tests; chưa có vòng review độc lập mới cho hai chỉnh đó.

## Acceptance coverage

| AC | Result / evidence | Gap |
|---|---|---|
| AC01 | BROWSER một số mobile/tablet/desktop; utility không floating đáy | TEST_GAP toàn ma trận320/360/390/768/1024/1366, keyboard/safe-area |
| AC02 | AUTOMATED views; BROWSER layout desktop | TEST_GAP SERVICE full flow hai món/ghi chú/serve trên360×640 |
| AC03 | BROWSER dialog màu/focus return, nút receipt; source targets | TEST_GAP contrast tất cả state, reader, trap, touch thật; utility màu cuối chưa chụp lại |
| AC04 | AUTOMATED unchanged freshness/pending/403/409/exact retry; BROWSER KDS happy path | TEST_GAP network recovery+pending trên browser |
| AC05 | AUTOMATED deadline/unknown/401/secret/PIN race | TEST_GAP body lost sau server commit, reauth/multi-tab browser |
| AC06 | AUTOMATED Seller partial/stale, POS unknown; BROWSER giá/receipt/entry nhanh | TEST_GAP long-content stress và VI/EN expansion toàn flow |
| AC07 | AUTOMATED worker lifecycle/cache/unknown route/install one-shot; BROWSER update notice | TEST_GAP actual install/standalone/OS offline/two-tab update |
| AC08 | AUTOMATED safety93 và UI64; DB success paths | OPEN full-suite owner gate; TEST_GAP fault-after-commit browser và acceptance độc lập Astra High |

## UAT U01–U14

| ID | Result và evidence | Phần chưa chứng minh |
|---|---|---|
| U01 | BROWSER Seller mobile; AUTOMATED partial/stale | TEST_GAP detail/back/filter end-to-end |
| U02 | BROWSER dialog/Escape; AUTOMATED launcher | TEST_GAP rename/revoke/tất cả role+viewport |
| U03 | AUTOMATED F&B views/recovery; BROWSER layout | TEST_GAP SERVICE hai món→ghi chú→gửi→giao đầy đủ |
| U04 | BROWSER #93/#94/#95 cash thiếu/đủ/dư, receipt/đơn mới | TEST_GAP cashier-role riêng và lịch sử end-to-end |
| U05 | AUTOMATED checkout/approval/revision regression | TEST_GAP split/discount/PIN/cash/transfer/debt browser |
| U06 | BROWSER Kitchen phiếu#2 start/ready | TEST_GAP Bar/out-of-stock/resume/50phiếu/read-distance |
| U07 | AUTOMATED unchanged recovery và pending poll | TEST_GAP browser stop/start+pending sau sửa |
| U08 | AUTOMATED never/body lỗi/unknown exact retry | TEST_GAP response loss sau commit và double-tap hai tab |
| U09 | AUTOMATED R4 fences/401,403,409 | TEST_GAP revoke/expire/role-change trong UI |
| U10 | AUTOMATED offline lease safety và POS không tạo sale thứ hai | TEST_GAP lease→offlinecash→sync/quarantine/seal browser |
| U11 | AUTOMATED SW cold shell/API exclusions | TEST_GAP warm/cold OS offline và context restore |
| U12 | AUTOMATED install event once/storage failure/waiting; BROWSER notice | TEST_GAP Android/iOS/standalone/dismiss/reopen/two-tab update |
| U13 | BROWSER Escape/focus return và receipt focus | TEST_GAP keyboard full, reader, zoom200/400%, reduced-motion,320px, safe-area |
| U14 | BROWSER giá range tablet rõ hơn | TEST_GAP1000products/50tickets/text expansion/long money |

## Bàn giao

Mã và focused regression sẵn sàng owner review; release acceptance vẫn OPEN vì các TEST_GAP trên. Giữ worktree và DB giả để tái hiện. Owner có thể chạy gate không commit trước:

```powershell
cd C:\Users\nguye\.codex\worktrees\7a90\python_app
.\test-commit.ps1 -TestOnly
```

Không chạy commit mode khi chưa xem staging/scope (script có hành vi staging riêng). Không tự hợp nhất vào baseline đã accepted.

## Asset signature

Các asset đã sửa dùng `v=20260911-r5-5`, riêng `/css/fnb-r1a.css` dùng `v=20260911-r5-6`, POS JS/locale dùng `v=20260911-r5-7`; SW cache version `v3-device-r5`. Hash dưới đây chốt source cuối để đối chiếu với runtime (không thay cho browser proof).

| File | SHA-256 |
|---|---|
| `static/js/api.js` | `12cdd5d949a36e0557853692556661aa29080253bc9cf9c168a58553eaac8104` |
| `static/js/fnb-r1a.js` | `cedd2bd8a7414dcf7513d94708260b31dc1b8d6b4dd22dbf334130c40c55655d` |
| `static/js/fnb-station-r1b.js` | `f8a24638887e1062372e24d9dedcf6b0f12c5545093bb8c969a57226fad22911` |
| `static/js/pos.js` | `ff708a3a5b0eb52ee08c23ffc3f145d583848a4353e74c817e705411d5a33f48` |
| `static/js/seller.js` | `7a60477401409e962a6d34b2f34c060b493be316db4df01d06822c6e175efc08` |
| `static/js/pwa.js` | `95c44f412f443f3d5288c802cb877483318093211ae0118672faadd10a22a329` |
| `static/sw.js` | `b45123f6f4dd9385f381e5af7f4748237d2e210f38a665aca2f048037b564208` |
| `static/css/style.css` | `8d017cfe61f534ba51ee597543e0f3f5e81497cc6028dcd1f58696c6a1cb96f7` |

CSS F&B sau correction mobile SHA-256: `09ddd8595cd8242f2548fe05f31558f44ddb4ec3fa79089beb9539f0330ebf47`.

Hash `static/js/locales/pos.js` sau correction POS: `3452967ff1b44344dd51c4dce886dbd18a78cb7b7f63c41ab5442759fffa8b01`.
