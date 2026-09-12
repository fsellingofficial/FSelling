# Device Usability & PWA R5 — báo cáo kiểm chứng

## Trạng thái hiện hành RC blockers — 2026-09-12

**ACCEPTED WITHIN SCOPE — OWNER FULL-SUITE PASS.** Reviewer Astra High độc lập đã chấp nhận diff RC blockers sau correction; không còn actionable finding P0/P1/P2 trong phạm vi đã review. Owner đã gửi kết quả gate TEST PASS trong 2.442,5 giây (40 phút 42,5 giây), bỏ qua commit theo TestOnly. Đây là code state chưa commit; chưa có release GO.

- Baseline/source branch: `codex/device-usability-pwa-plan5`, commit `9e13cfc04f6f6aa5fb9fd90af622ffee0a89f846`.
- Worktree: `C:/Users/nguye/.codex/worktrees/5be6/python_app`. Xác minh sạch trước sửa; hiện vẫn **detached HEAD** đúng commit trên, thay đổi unstaged. Không tạo branch/commit, không reset/revert/rebase/push/merge/deploy; không sửa checkout khác.
- Phạm vi: 4 Python services, 2 JS controllers/engine, cache URL trong POS HTML, regression và báo cáo. Không đổi schema, quyền, operation-id, ledger contract, offline seal/lease; không thêm dependency.
- Môi trường: API/service dùng DB tạm riêng do `tests/conftest.py` init/upgrade/verify; Node dùng production POS/offline engine với FakeIndexedDB và HTTP/DOM giả. Không chạy server ngoài, ngrok, webhook/payment thật, Gemini hoặc TTS.

| Blocker | Root cause và fix | Vị trí runtime hiện hành |
|---|---|---|
| B1 / P1 — FIXED, reviewed | Authentication chỉ kiểm tại đầu request; các write boundary tiền mặt và voucher chưa fence. Thêm `fence_live_auth_session(db)` tại `_current_cash_shift` khi ghi (kể cả owner/manager được phép không có ca), `apply_transition` (bao gồm admin hủy đơn mồ côi), trước refund ledger và trước create/update/delete voucher. Review độc lập phát hiện ADMIN đối soát unscoped dùng `BEGIN IMMEDIATE` riêng; bổ sung fence ngay sau lock cho cả ba action không tạo payment. Helper auth giữ no-op cho background/system DB không bind session. | `fselling/services/order_service.py:287,309,1554`; `fselling/services/voucher_service.py:125,172,206`; `fselling/services/qr_reconciliation_service.py:566` |
| B2 / P1 — FIXED, reviewed | `_refresh_session_status` suy lại session từ checks, ghi đè CLOSED/CANCELLED; GET checks còn sync/commit ngoài lock. Chặn refresh status ngoài tập active có sẵn; GET dùng `_prepare_locked_shop`, đọc lại quyền/session dưới lock/fence, serialize trong transaction, commit khi có thay đổi và rollback khi lỗi/không đổi. | `fselling/services/fnb_service.py:1116,1285` |
| B3 / P2 — FIXED, reviewed | Allocation abort không có receipt/binding nhưng POS giữ offline_pending và online guard từ chối retry. Online recovery giờ kiểm receipts/bindings trong cùng transaction; chỉ khi chứng minh chưa allocation mới lưu binding `online_only` và trả `definitely_not_allocated`. POS chuyển sang creating bằng đúng key/payload/cart/tender gốc. Binding ngăn retry offline muộn cấp phiếu sau online dispatch. Mọi lỗi đọc/commit, receipt durable, orphan DRAFT, v0/ACK cũ không xác định được binding vẫn `durable_or_unknown`. | `static/js/offline-ban.js:1218,1249,1303`; `static/js/pos.js:3077,3104` |

Trace B1 đã bao phủ callers/siblings: create/pay/cash-topup/debt/refund/cancel; return approval/create; F&B pay; QR reconciliation; voucher apply/resolve/release; background expiry và webhook. Debt/return/F&B/cancel thường đã có shop/inventory fence. Voucher apply chỉ tính preview; resolve/release thuộc transaction đã khóa của caller. Không thêm fence auth bắt buộc cho job hệ thống. Regression revoke bằng **transaction DB khác sau API authentication**, so toàn bộ field của order/items/payment, shift/movement, voucher, audit, customer/loyalty, product/batch trước/sau. Owner có/không ca, manager không ca và cashier có ca đều bị chặn đúng 401 `AUTH_SESSION_REVOKED`; policy cashier bắt buộc ca vẫn được kiểm bởi bộ checkout hiện hữu.

B2 regression chạy đúng DIRECT send → DEBT → close CLOSED → thu đủ nợ bằng transfer → GET checks. Check thành PAID, session vẫn CLOSED, không active table link; check/session/shop revision tăng đúng một lần, đọc lại không ghi thêm, audit/link/table lịch sử không đổi. CANCELLED được kiểm như trạng thái terminal còn lại của model bằng fixture lịch sử; không gọi đây là hành trình public DEBT → CANCELLED. Có ca revoke trước sync và ca DB đổi trạng thái/revision sau lần đọc đầu để chứng minh read-under-lock.

B3 regression chạy production `checkout`, `thuLuuOfflineDangDo`, `thuTaoDonDangDo`, `guiYeuCauTaoDonDangDo`, `hoanTatTienMatDangCho` và offline engine; chỉ HTTP/DOM/IndexedDB được giả lập. Allocation abort có 0 receipt/0 binding; retry và reload online đều đúng 1 create + 1 pay, không cấp key mới. DRAFT→READY failure, READY/SYNCING/RETRYABLE/ACK replay, v0 binding, sealed/expired credential, intent mismatch, IDB unavailable, online-binding abort, old unbound v0 và pre-binding v1 receipt được giữ trong harness. POS/offline URLs tăng `r5-10`; locale không đổi.

**Review độc lập và correction — 2026-09-12:** reviewer `astra_rc_review`, GPT-6 Astra reasoning High, đọc diff chưa commit so với `9e13cfc` và trace callers. Vòng đầu CHANGES REQUIRED: nhánh ADMIN `intended_shop_id=None` của reconciliation không đi qua shop auth fence; probe revoke sau API authentication bằng transaction khác vẫn trả 200 và ghi durable action cho `KEEP_OPEN`, `REJECT_NOT_OURS`, `MARK_REFUNDED_EXTERNALLY`. Hai action terminal còn đổi disposition/revision 0→1. Đây là thiếu sót B1 có sẵn còn bỏ lọt, không phải regression mới do patch. Log/script probe: `.pytest_cache/rc-blockers/astra_unscoped_probe.log` và `.py`.

Correction thêm đúng fence sau `BEGIN IMMEDIATE`, trong nhánh rollback/reraise HTTPException. Ba regression mới tại `tests/test_auth_session_mutation_fence_r4.py:411` dùng actual collision workflow trên TestClient/DB tạm, revoke sau auth và snapshot toàn bộ field tài chính, bank event/action/intent/audit. RED 3 failed; sau fix, nhóm 78 test PASS. Re-review xác nhận finding đã đóng, scoped lock/CAS/audit và no-op system sessions giữ nguyên; B2/B3 không có actionable finding. Verdict cuối **ACCEPTED within scope** thay verdict vòng đầu. Reviewer kiểm log RED/GREEN, SHA256 nguồn QR và diff-check; sau verdict chỉ cập nhật báo cáo, không đổi runtime/test.

**Owner final gate:** owner gửi output của lượt test theo lệnh đã bàn giao `test-commit.ps1 -TestOnly -WithConcurrency`: `TEST PASS - toan bo test deu xanh`, thời gian `2,442.5 giay`, và `(-TestOnly: bo qua buoc commit)`. Đây là owner-reported evidence; đoạn output không có tổng số test nên không suy ra số lượng. Kiểm tra trực tiếp sau kết quả: HEAD vẫn `9e13cfc04f6f6aa5fb9fd90af622ffee0a89f846`; hash toàn bộ 17 file đang sửa khớp snapshot bàn giao sau review. Chỉ cập nhật báo cáo này, không đổi runtime/test, không chạy lại full suite, không tạo branch/commit.

**Bằng chứng RED/GREEN và lệnh đã chạy** (PowerShell tại worktree trên):

| Nhóm | RED | GREEN / exit |
|---|---|---|
| B1, 13 regression mới | 11 failed, 2 passed, 9 deselected; exit 1. Những ca fail đổi money/loyalty/audit/voucher/stock; debt và cancel thường đã bảo vệ. | Nhóm B1 cùng checkout/voucher/reconciliation/cancel: **91 passed**, exit 0, 96.25s |
| B2, 4 regression mới | 4 failed, 14 deselected; exit 1. CLOSED/CANCELLED bị ghi thành PARTIALLY_SETTLED; revoke vẫn ghi. | Nhóm F&B: **39 passed**, exit 0, 50.42s |
| B3 actual POS + IDB | Harness exit 1: online create `0 !== 1`. Bổ sung unbound-v0 RED exit 1: bị phân loại nhầm definitely_not_allocated. | Nhóm client cuối: **22 passed**, exit 0, 0.87s; harness nằm trong nhóm, không cộng thành test mới |
| Correction B1 sau review: ADMIN QR unscoped | **3 failed, 22 deselected**, exit 1, 6.83s | Auth mutation + QR reconciliation/account fence: **78 passed**, exit 0, 132.46s |
| High-risk, 47 file rõ phạm vi | Trước correction ADMIN QR; không phải full suite | **866 passed, 1 skipped**, exit 0, 741.85s |
| Concurrency opt-in bị skip ở nhóm trên | `RUN_CONCURRENCY_TESTS` không được bật ở lần 47 file | Chạy riêng đúng ca với env bật: **1 passed**, exit 0, 1.09s |
| Toàn bộ Node harness cuối | 16 file trong `tests/js` | **16 passed, 0 failed/skipped**, exit 0 |
| Syntax và whitespace | Hai JS runtime đã sửa | `node --check` cả hai và `git diff --check`: exit 0 |

Không cộng các lượt rerun hoặc các nhóm trùng phạm vi thành tổng test giả. Lần 866 test thuộc bản trước correction ADMIN QR; sau correction chỉ chạy 78 test liên quan, không lặp bộ 866 hoặc full suite. JS không đổi ở correction; guard legacy cuối của JS đã được xác nhận bằng nhóm 22 client và 16 Node sau sửa. Reviewer cũng chạy 17 regression B1/B2 và Node harness ở vòng đầu, không cộng thành test riêng. Warning hiện hữu: Starlette/httpx deprecation; Git có thông báo LF/CRLF nhưng diff-check không lỗi. Log local (git-ignored): `.pytest_cache/rc-blockers/` gồm `rc-blocker1-red.log`, `rc-blocker1-green.log`, `rc-blocker2-red.log`, `rc-blocker2-green.log`, `rc-blocker3-red.log`, `unbound-v0-harness-red.log`, `rc-blocker3-final-green.log`, `high-risk.log`, `concurrency-targeted.log`, `node-final.log`, `rc-review-qr-red.log`, `rc-review-qr-green.log`.

```powershell
# B1 RED trước sửa
python -m pytest tests/test_auth_session_mutation_fence_r4.py -k 'revoke_after_api_auth' -o addopts= -q --tb=short
# B1 GREEN
python -m pytest tests/test_auth_session_mutation_fence_r4.py tests/test_cashier_checkout.py tests/test_vouchers.py tests/test_reconciliation.py tests/test_cancel_order.py -o addopts= -q --tb=short
# Correction ADMIN QR RED trước sửa
python -m pytest tests/test_auth_session_mutation_fence_r4.py -k 'unscoped_admin_reconciliation' -o addopts= -q --tb=short
# Correction GREEN cuối
python -m pytest tests/test_auth_session_mutation_fence_r4.py tests/test_qr_reconciliation.py tests/test_qr_account_change_fence.py -o addopts= -q --tb=short
# B2 RED trước sửa
python -m pytest tests/test_fnb_r1c_checkout.py -k 'terminal_debt_session or checks_sync' -o addopts= -q --tb=short
# B2 GREEN
python -m pytest tests/test_fnb_r1c_checkout.py tests/test_fnb_r1c_checks.py tests/test_fnb_r1a_sessions.py tests/test_fnb_r1b_cancel.py -o addopts= -q --tb=short
# B3 RED trước sửa và GREEN sau sửa
node tests/js/offline-ban-v1.test.js
python -m pytest tests/test_offline_client_v1.py tests/test_offline_client_sync_v1.py tests/test_pos_retry_ui.py -o addopts= -q --tb=short
# Node/syntax/diff cuối
$nodeHarnesses = @(rg --files tests/js -g '*.test.js')
node --test @nodeHarnesses
node --check static/js/offline-ban.js
node --check static/js/pos.js
git diff --check
# Một ca concurrency opt-in, không phải full-suite gate
$env:RUN_CONCURRENCY_TESTS = '1'
python -m pytest tests/test_order_status_machine.py::test_dong_thoi_chi_mot_luong_thang -o addopts= -q --tb=short
```

<details>
<summary>Lệnh high-risk — đúng 47 file, không chạy toàn bộ test suite</summary>

```powershell
$rcTests = @(
    "tests/test_auth_sessions_r4.py"
    "tests/test_auth_session_lifecycle_r4.py"
    "tests/test_auth_session_management_r4.py"
    "tests/test_auth_session_mutation_fence_r4.py"
    "tests/test_approval_session_r4.py"
    "tests/test_orders.py"
    "tests/test_order_status_machine.py"
    "tests/test_cashier_checkout.py"
    "tests/test_vouchers.py"
    "tests/test_reconciliation.py"
    "tests/test_cancel_order.py"
    "tests/test_shifts.py"
    "tests/test_tich_diem.py"
    "tests/test_loyalty_shop_policy.py"
    "tests/test_tra_hang.py"
    "tests/test_return_approval_r3.py"
    "tests/test_cong_no.py"
    "tests/test_webhook.py"
    "tests/test_webhook_so_tien.py"
    "tests/test_webhook_transaction_persistence.py"
    "tests/test_qr_account_change_fence.py"
    "tests/test_qr_reconciliation.py"
    "tests/test_fnb_r1a_setup.py"
    "tests/test_fnb_r1a_sessions.py"
    "tests/test_fnb_r1b_send.py"
    "tests/test_fnb_r1b_cancel.py"
    "tests/test_fnb_r1c_checks.py"
    "tests/test_fnb_r1c_checkout.py"
    "tests/test_ban_offline.py"
    "tests/test_offline_client_v1.py"
    "tests/test_offline_client_sync_v1.py"
    "tests/test_offline_recovery.py"
    "tests/test_offline_issue_lifecycle.py"
    "tests/test_offline_lease_identity.py"
    "tests/test_offline_lease_revocation.py"
    "tests/test_offline_fingerprint.py"
    "tests/test_offline_time_anchor.py"
    "tests/test_offline_rollout.py"
    "tests/test_offline_deficit_allocation.py"
    "tests/test_pos_retry_ui.py"
    "tests/test_pos_offline_ui.py"
    "tests/test_pos_offline_sync_ui.py"
    "tests/test_loyalty_ui.py"
    "tests/test_qr_pos_ui.py"
    "tests/test_i05_money.py"
    "tests/test_i05_cost_provenance.py"
    "tests/test_migration_0014_session_device_safety_r4.py"
)
python -m pytest @rcTests -o addopts= -q --tb=short
```

</details>

**Code fingerprints chưa commit** (SHA-256 của file hiện hành; log test không thay cho review):

| File | SHA-256 |
|---|---|
| `fselling/services/order_service.py` | `bdda6569d71aa1cb9dd6a79a94c0cfc52f66461e3a4aa186e7d48e33cfc7bce4` |
| `fselling/services/voucher_service.py` | `3841db612bb3491dd0887cecefea7aa8146013bd50705818f1e8701ea250ce73` |
| `fselling/services/fnb_service.py` | `4c2568d3653df7e18ccf23776955432a9af47de28c7f5a4346fb567a86774657` |
| `fselling/services/qr_reconciliation_service.py` | `eec6406edcd8051ce73a6d2538e36e93ec1bdc9a998cc8d80904a8e394895274` |
| `static/js/offline-ban.js` | `dbda9c69bf42d61923c60dc5292d52e61bf055a5277c35bf333a6522109b7fed` |
| `static/js/pos.js` | `2a0ff0251e2289b110d1d7145a0de0b4d9cde313e870b68a4a5ffbfdd2fc495b` |
| `static/pos.html` | `3a54a289751a26b784441cca28fd65641c3e5b7e2e8d8e3b1ecfc1f2d0663b42` |

**OPEN / TEST_GAP:** owner full-suite gate đã PASS; các gap kiểm chứng hành vi dưới đây vẫn OPEN. Chưa có browser/thiết bị thật cho revoke-in-flight, terminal debt refresh, allocation-abort/reconnect/reload, kill process giữa online reservation và dispatch, hoặc nhiều tab/thiết bị cùng retry. Node harness dùng FakeIndexedDB; không suy rộng thành browser/PWA/OS evidence. Giữ các gap role × device, cold-start/force-stop, network split, update hai tab và accessibility của lịch sử. CANCELLED+debt là fixture terminal-state, không phải một flow UI mới. Code review ACCEPTED không đóng các gap này.

Binding giữ lâu dài như baseline; online-only binding cũng phải giữ để chặn retry cũ. Recovery quét receipts/bindings cục bộ; có unbound legacy không xác định được thì chặn chuyển online và cần đối soát, không tự xóa phiếu. Hiệu năng kho IndexedDB lớn và browser race thật chưa đo. Không khẳng định toàn bộ hệ thống không còn blocker ngoài ba finding đã nhận.

**Bước bàn giao:** independent Astra High đã ACCEPTED trong phạm vi và owner final gate đã PASS. Lệnh đã dùng được lưu bên dưới để truy vết, không yêu cầu chạy lại. Owner quyết định tạo branch/commit; hiện vẫn detached HEAD, 17 file thay đổi unstaged. Chưa push/merge/deploy và chưa có release GO.

```powershell
cd C:\Users\nguye\.codex\worktrees\5be6\python_app
.\test-commit.ps1 -TestOnly -WithConcurrency
```

---

> **LỊCH SỬ SUPERSEDED.** Mọi verdict/PASS/"hiện tại"/"mới nhất" bên dưới chỉ áp dụng tại thời điểm lịch sử, không xác nhận diff RC chưa commit ở đầu báo cáo. Các đoạn về bản vá trên HEAD a0505c5 sau đó được chốt tại 9e13cfc; bản vá trên HEAD 5750da9 được chốt tại a0505c5; triển khai trên HEAD 18eca4a được chốt tại 5750da9. Nhãn commit ở mỗi tiêu đề xác định bản runtime tương ứng, còn nguyên văn bên dưới giữ bằng chứng thời điểm đó.

## SUPERSEDED — commit 9e13cfc — Review cuối bản sửa v0/v1 — đạt code-readiness

Astra High (`astra_final_review`) review read-only diff cuối so với HEAD `a0505c5`: không còn P0/P1/P2 có bằng chứng trong phạm vi review. Xác nhận đã xử lý DRAFT→READY lỗi, reconnect→ACK→retry, fallback v0, immutable intent/identity và credential gốc khi finalize. Ba harness pos-request-r5, offline-ban-v1, offline-ban-sync-v1 và git diff --check PASS. Probe hai request cùng key tạo đúng một receipt và một binding; một caller có thể gặp lỗi finalize cạnh tranh nhưng retry cùng key thành công, intent khác bị từ chối.

Full-suite owner PASS 2.823,9 giây vẫn là gate của bản runtime/test hiện tại; sau đó chỉ sửa báo cáo. Review này thay thế trạng thái thiếu review do quota ở các mục lịch sử bên dưới. Chưa commit/push/merge/deploy. Giới hạn giữ nguyên: binding lưu lâu dài; phiếu đã dọn hoặc credential sealed cần đối soát/recovery; TEST_GAP browser/thiết bị không được nâng thành PASS.

## SUPERSEDED — commit 9e13cfc — Owner full-suite PASS — bản sửa offline v0/v1

Owner gửi kết quả `test-commit.ps1 -TestOnly -WithConcurrency`: TEST PASS, 2.823,9 giây (47 phút 3,9 giây), bỏ qua commit đúng theo TestOnly. Kiểm tra lại worktree `C:\Users\nguye\.codex\worktrees\7a90\python_app`, nhánh `codex/device-usability-pwa-plan5`, HEAD `a0505c5`; bản sửa vẫn chưa commit. Sau kết quả này chỉ cập nhật báo cáo, không sửa runtime/test hay chạy lại full-suite.

Phạm vi bản sửa được test gồm offline_pending của POS và binding bền vững cho cả v1 lẫn fallback v0 Phase A. Review Astra vòng v1 đã xác nhận hướng mapping xử lý ACK, nhưng phát hiện v0 chưa idempotent. Sau đó đã sửa v0 ghi binding cùng transaction phiếu, giữ binding sau xóa/đồng bộ để retry không tạo lại; regression v0 tái hiện hai UUID trước sửa và PASS sau sửa. Nhóm 123 focused test cũng PASS sau thay đổi v0.

Review độc lập bản sửa v0 cuối cùng còn thiếu vì Astra chạm hạn mức trước khi review lại. Full-suite PASS không thay thế review hoặc kiểm chứng giao diện retry mới trên thiết bị. Chưa commit/push/merge/deploy. Mục này thay thế các ghi chú lịch sử bên dưới còn nói full-suite chưa chạy hoặc review đang chạy.

## SUPERSEDED — commit 9e13cfc — Bản sửa P1 lưu offline dở dang — 2026-09-12, chưa commit

Vòng tiếp theo: Astra chạy lại được, xác nhận thêm P1 sau auto-sync ACK: tombstone bỏ creation_key nên retry vẫn tạo phiếu mới; trạng thái RETRYABLE cũng kẹt finalize. Đã bổ sung mapping `creation:key` trong meta_v1, ghi cùng transaction DRAFT, chứa UUID/hash request và identity, không chứa token hay nội dung giỏ. Lookup trước allocation/catalog mới; replay READY/SYNCING/RETRYABLE/ACKED không đổi trạng thái đồng bộ. Lease cũ đã seal hoặc DRAFT không còn đủ điều kiện vẫn chặn; ACK hết hạn bị cleanup thì mapping ngăn tạo lại và yêu cầu đối soát. Mapping giữ vô thời hạn để không mở lại khả năng tạo trùng qua retry cũ.

Regression actual POS + offline engine + sync engine/FakeIndexedDB: trước sửa mapping, ACK rồi retry tạo 2 receipt (RED); sau sửa chỉ 1 ACKED order901. Kiểm thêm SYNCING/RETRYABLE không đổi state, replay sau đổi catalog/lease, từ chối đổi tender/user và cleanup ACK 30 ngày vẫn không tạo phiếu mới. Node harness PASS, 123 focused pytest PASS sau sửa engine; toàn bộ static JavaScript qua node --check. Astra đang review vòng mapping mới. Chưa chạy full-suite cho bản này.

Trạng thái mới nhất: worktree `7a90`, nhánh `codex/device-usability-pwa-plan5`, HEAD `a0505c5`; có thay đổi runtime/test chưa commit. Các mục bên dưới ghi lại bằng chứng của bản trước, không thay thế kiểm chứng bản sửa này.

POS lưu trạng thái `offline_pending` với cùng operation/creation key, giỏ và tiền khách đưa vào sessionStorage trước khi gọi offline engine. Nếu không lưu được retry state thì dừng trước allocation. Khi lưu DRAFT rồi lỗi READY, giữ giao dịch để retry/reload cùng intent; không dispatch online create và không cấp key mới. Khoá sửa tiền trong trạng thái này. Asset POS JS/locale đổi sang `20260912-r5-9`.

Automated: nhóm pytest POS retry, offline client v1/sync/UI, loyalty UI, QR và i18n gồm 123 test PASS (exit 0). Sau đó tăng cường harness bằng production taoTrangThaiCheckout/phucHoiCheckoutDangDo thay cho stub; Node offline-ban-v1 và pos-request-r5 đều PASS. Fault sau DRAFT durable được tái hiện bằng FakeIndexedDB: reload khôi phục đúng key, quantity2 và tender60000; retry rồi recovery không tăng receipt count, không gọi online create. Session storage failure dừng trước allocation. `git diff --check` PASS. Đây là automated integration với DOM/IndexedDB giả, chưa phải fault test trên điện thoại.

Lượt follow-up Astra High đầu tiên báo hết hạn mức; đã chạy lại và tìm lỗi ACK nêu trên. Verdict CHANGES REQUIRED bên dưới là của bản a0505c5 trước sửa; vòng mapping mới chưa có verdict. Full-suite owner PASS 2.893,6 giây và Android happy path cũng thuộc bản trước, không nhận là PASS cho runtime hiện tại. Còn chờ review độc lập, full-suite và kiểm chứng giao diện retry mới. Chưa commit/push/merge/deploy.

## SUPERSEDED — commit a0505c5 — Final review Astra High — CHANGES REQUIRED

Reviewer `astra_final_review`, GPT-6 Astra reasoning High, read-only range18eca4a..a0505c5, xác nhận một P1 tại static/js/pos.js:3291–3304: fresh offline checkout không giữ exact creation_key/intent nếu lưu lỗi sau DRAFT durable. Offline engine commit DRAFT+sequence trước fingerprint/READY; lỗi ở write READY khiến UI chỉ toast rồi mở khóa. Bấm lại cấp operation mới; recovery có thể đưa cả DRAFT cũ và phiếu mới thành READY, dẫn tới hai sale cho một lần thu tiền.

Reviewer tái hiện bằng actual checkout + actual offline engine và FakeIndexedDB hiện hữu, inject QuotaExceededError riêng tại write READY sau digest. Sau lỗi: pending=null, operation=null, DRAFT fresh-op-1 sequence1. Sau bấm lại và recovery: READY fresh-op-1 sequence1 total52000 và READY fresh-op-2 sequence2 total52000. Backend dedup UUID/lease+sequence không gộp hai intent khác nhau này.

Cần sửa: giữ durable offline-specific exact key+intent trước allocation và retry cùng intent qua lỗi/reload; không đưa vào phase creating để gọi nhầm online create. Thêm regression fail sau DRAFT durable (khác fail allocation), kiểm đúng1READY qua retry/reload/recovery. Chưa sửa runtime trong vòng review này.

8 Node harness hiện có PASS: api-request-r5, pos-request-r5, offline-ban-v1, fnb-r1a, fnb-station-r1b, seller-partial-r5, pwa-r5, auth-sessions-r4; custom fault probe vẫn phát hiện P1. Không xác nhận thêm actionable P0/P1/P2 trong phần trace. Verdict chưa đạt code readiness; full-suite owner và Android happy path không bao phủ lỗi lưu dở dang này. Chưa chuẩn bị release/merge acceptance.

## SUPERSEDED — commit a0505c5 — Android thật: owner xác nhận offline → mở lại → đồng bộ

Owner xác nhận giỏ 1 Lavie tổng5.000đ/tender10.000đ; sau hướng dẫn tắt Wi-Fi và dữ liệu di động, báo lưu thành công/giỏ trống/1đơn chờ; về màn hình chính mở lại PWA vẫn còn phiếu; bật mạng đồng bộ thành công. Đây là owner-reported physical-device evidence, không phải agent trực tiếp quan sát điện thoại.

Đối chiếu read-only DB pilot `../android-pwa-20260912/demo.db`: chỉ1đơn mới #98 PAID, 1item product1 quantity1, total5.000/tender10.000/change5.000; chỉ1ledger #97 SALE_CASH5.000; tồn Lavie14→13 so DB nguồn dùng để sao chép. Registry xác nhận contract_version=1, state=INGESTED: đây là phiếu offline v1. Offline UUID `off-cbcc1287-54f9-4bee-bfed-31e3d89ab591`. Không lẫn với #98 ở DB browser mô phỏng khác. Phạm vi này xác nhận luồng offline thực tế giữ phiếu qua chuyển nền/mở lại rồi đồng bộ không nhân đơn/ledger trong lần thử. Không suy rộng thành cold-start/force-stop hoặc mất mạng đúng lúc server commit.

## SUPERSEDED — commit a0505c5 — Trạng thái hiện tại sau nghiệm thu Android — 2026-09-12

- Nhánh `codex/device-usability-pwa-plan5`, runtime HEAD `a0505c5`, triển khai Plan5 tại `5750da9`. Bản vá đã commit; hiện chỉ báo cáo nghiệm thu thay đổi.
- Full-suite có concurrency: owner PASS 2.893,6 giây; không chạy lại khi runtime/test không đổi. Focused112 và17 có phạm vi trùng nhau, không cộng thành tổng test riêng.
- Android thật: owner xác nhận cài/mở PWA, bán tiền mặt khi tắt Wi-Fi/dữ liệu di động, chuyển nền/mở lại giữ1phiếu, bật mạng đồng bộ. DB độc lập xác nhận offline v1 INGESTED, đúng1đơn/1ledger/1lần trừ kho. Không yêu cầu lặp lại các bước này.
- Review bản vá của GPT-5.6 Sol không còn actionable finding. Review cuối Astra High trên range18eca4a..a0505c5: CHANGES REQUIRED, 1 P1 partial-persistence offline (xem đầu báo cáo).
- Còn chưa chứng minh: cold-start/force-stop lúc offline, reconnect giữa transaction IndexedDB, mất phản hồi đúng lúc commit trên thiết bị thật, update PWA hai tab, accessibility/role-device matrix đầy đủ. Các ca browser mô phỏng và automated giữ nhãn riêng; không nhận toàn bộ release production acceptance.
- Tiếp theo: xử lý findings nếu có, chốt báo cáo và chuẩn bị PR để owner duyệt. Chưa push/merge/deploy. Các mục bên dưới là nhật ký theo thời điểm; trạng thái hiện tại ở mục này và bằng chứng Android phía trên được ưu tiên khi có ghi chú cũ đã bị thay thế.

## SUPERSEDED — commit a0505c5 — Owner gate bản vá offline — 2026-09-12

Owner duyệt ngoại lệ commit trực tiếp sau full-suite PASS để tránh chạy lại gate; vẫn kiểm tra diff và chặn tên file/giá trị secret trước commit. Không sửa script commit hoặc bỏ test khỏi bộ kiểm tra.

Owner đã gửi output `test-commit.ps1 -TestOnly -WithConcurrency` tại worktree7a90: TEST PASS, 2.893,6 giây (48 phút13,6 giây), bỏ qua commit đúng theo TestOnly. Nhánh kiểm tra lại: `codex/device-usability-pwa-plan5`, HEAD5750da9; bản vá offline r5-8 và harness tích hợp v1 vẫn chưa commit. Sau output PASS chỉ cập nhật báo cáo này, không thay runtime/test.

Kết quả này thay thế các ghi chú lịch sử bên dưới còn nói full-suite bản vá chưa chạy. Các TEST_GAP browser v1, reconnect giữa transaction IndexedDB và thiết bị/PWA thật vẫn OPEN. Chưa push/merge/deploy.

## SUPERSEDED — commit a0505c5 — Bổ sung automated POS → offline v1

Harness `tests/js/offline-ban-v1.test.js` đã nối production checkout và luuBanOffline với production OfflineBan.luuPhieuTuPOS/createReceiptV1, dùng FakeIndexedDB có sẵn, lease/catalog fixture hợp lệ. Node harness PASS: fresh cash checkout tạo đúng một receipt_v1, không ghi phieu v0, không dispatch online, dọn giỏ và mở khóa; restored previously-sent operation đi nhánh retry, không tăng receipt count. Không đổi runtime trong bước này.

Đây là automated integration với IndexedDB giả, không đóng TEST_GAP browser v1/PWA vật lý hoặc reconnect giữa transaction. Lượt pytest ban đầu bị sandbox từ chối tạo uploads dưới Temp. Sau cấp quyền, 17 ca trong test_offline_client_v1.py và test_pos_retry_ui.py PASS (exit0), gồm harness tích hợp mới; không cộng vào112ca trước vì có trùng phạm vi. Full-suite cho bản vá vẫn chưa chạy.

## SUPERSEDED — commit a0505c5 — Browser verification bản vá offline r5-8

Dùng Codex in-app browser tại localhost:8515, DB copy riêng `../r5-offline-uat.db`; bộ mô phỏng nằm ngoài repo (`r5-offline-uat-server.py`, `r5-uat-controls.html`). Đây là browser với mạng/fault mô phỏng, không phải ngắt mạng OS/PWA vật lý. Đã quan sát AX và screenshot hóa đơn #99.

1. Fresh offline: chọn 1 Lavie 5.000đ, khách đưa 10.000đ; UI báo lưu thành công, giỏ trống, đúng 1 phiếu cũ chờ gửi (v0). Online lại tự đồng bộ đúng đơn #98, 1 SALE_CASH 5.000đ, kho14→13. Không nhận kết quả này là bằng chứng fresh receipt v1.
2. Create commit rồi mất phản hồi: wrapper ghi upstream200 và thay response503. DB đã có #99 PENDING với operation `504840db-e571-46a8-ad2e-0f7701c3161b`. Reload giữ đơn đang chờ; ô tender lúc creating về trống nên nhập lại10.000đ trước retry (chưa phải pay-unknown). Retry khi offline giữ trạng thái và 0 phiếu chờ; online retry mở hóa đơn đúng #99, 1 CASH_TOPUP 5.000đ, kho giữ12.
3. Mạng trở lại ngay tại entry của helper lưu: v1 guard từ chối vì navigator.onLine=true; UI giữ giỏ Lavie/tender10.000 và nút hoạt động. Bấm lại online hoàn tất #100, 1 CASH_TOPUP 5.000đ, kho11. Đây là timing trước persistence, chưa chứng minh reconnect giữa transaction IndexedDB.

DB cuối có đúng3 đơn mới #98–100 PAID, 3ledger, tender10.000/change5.000 mỗi đơn. Snapshot `../r5-offline-browser-evidence.json`. Không có dữ liệu demo hay test controls trong repo. TEST_GAP còn: fresh offline v1 end-to-end, reconnect giữa IndexedDB transaction, mạng OS và PWA điện thoại thật. Chưa chạy full suite cho bản vá; chưa commit/push/merge.

## SUPERSEDED — commit a0505c5 — Correction sau review thanh toán commit 5750da9

- Review độc lập phát hiện P1: fresh cash checkout khi đã offline bị early return `mutationOutcomeUnknown`, không tới nhánh lưu phiếu. Repro cùng điều kiện: baseline lưu 1 phiếu; 5750da9 lưu 0 phiếu và giữ trạng thái creating.
- Bản sửa chưa commit: fresh checkout đã xác nhận đi thẳng vào `luuBanOffline` trước mọi request/trạng thái online. Retry của giao dịch đã gửi (kể cả reload) giữ nguyên đường exact retry, không chuyển thành phiếu offline. Khóa checkout trong lúc lưu; lỗi lưu giữ giỏ và mở lại nút. Asset POS JS tăng `20260911-r5-8`.
- Test mới RED trước sửa, GREEN sau sửa: fresh offline lưu phiếu/không dispatch/không để trạng thái online; restored unknown retry/không sinh phiếu thứ hai. 112 pytest trong các file retry, loyalty UI, offline client/sync/UI và QR UI PASS; Node pos-request-r5, api-request-r5, pos-offline-ui PASS; syntax và diff check PASS.
- Reviewer độc lập `review_payment_final` (GPT-5.6 Sol) đọc bản sửa và helper persistence, không tìm thấy actionable finding. Đây không phải Astra High acceptance.
- Full-suite PASS ở commit 5750da9 không được tính cho bản vá chưa commit này. Chưa chạy lại full suite, chưa commit/push/merge. Browser/thiết bị thật cho luồng fresh offline mới còn TEST_GAP.


SUPERSEDED — commit 5750da9. Ngày: 2026-09-11. Trạng thái: **D1–D5 đã triển khai; owner xác nhận full-suite có concurrency PASS; nghiệm thu thiết bị/PWA thật còn OPEN**.

## SUPERSEDED — commit 5750da9 — Chốt baseline Plan 5

Owner đã xác nhận “test pas” sau khi được cung cấp lệnh `test-commit.ps1 -TestOnly -WithConcurrency` cho worktree7a90. Đây là kết quả owner báo, không tự gán số test hoặc thời gian khi chưa có output đầy đủ; cache lastfailed hiện rỗng. Kết quả này thay thế trạng thái full-suite còn chờ ở các ghi chú lịch sử bên dưới.

Nhánh bàn giao: `codex/device-usability-pwa-plan5`, tạo từ HEAD18eca4a và giữ nguyên mọi thay đổi Plan5 đang kiểm chứng. Chốt local qua script repo; không push/merge/deploy. Script commit sẽ tự chạy lại gate trước khi ghi commit; không bỏ qua test hoặc commit tay. Các TEST_GAP thiết bị/PWA thật, ma trận role/device chưa đủ và review độc lập cho correction tiền mặt cuối vẫn giữ nguyên; full-suite PASS không đồng nghĩa production acceptance.

## SUPERSEDED — commit 5750da9 — Correction sau full-suite do owner chạy

Owner chạy `test-commit.ps1 -TestOnly -WithConcurrency` tại worktree7a90/HEAD18eca4a; output gửi lại ghi19FAILED, thời gian1.849,3giây, không commit. Đã đối chiếu cache lastfailed đúng19node IDs.

Nguyên nhân:18ca source assertions còn pin version asset trước R5; receipt Node harness thiếu `scrollIntoView` và selector/focus DOM mới. Cập nhật expected version riêng từng asset (seller/css/common/api r5-5, POS JS/locale r5-7; không bump asset không đổi). Bổ sung DOM mock receipt và assert thực sự scroll/focus. Không xóa/skip test, không thêm version cũ vào HTML để né assertion, không sửa production code trong correction này. Một assertion API cũ bị che sau assertion POS đầu tiên cũng đã cập nhật.

Kết quả chạy lại **đúng19ca owner báo:19PASS**; `git diff --check` đạt. Đây là targeted rerun, chưa phải full-suite mới PASS. Gate full-suite với concurrency vẫn cần owner chạy lại ở cùng worktree trước commit.

## SUPERSEDED — commit 5750da9 — Bổ sung kiểm chứng thu tiền POS — 2026-09-11

- **Lỗi phát hiện bằng browser + DB:** fault sau `/api/orders/96/pay` đã commit200 rồi wrapper trả503. DB đơn96 PAID, nhưng UI cũ nói “chưa ghi nhận thanh toán”. Tiền khách đưa cũng chưa được khóa theo payload retry. Đây là thông báo sai mức chắc chắn và nguy cơ người dùng đổi số tiền trong lúc kết quả chưa rõ.
- **Đã sửa:** copy VI/EN nói chưa xác nhận kết quả, yêu cầu kiểm tra/retry đúng đơn và không thu thêm tiền chỉ vì thiếu hóa đơn. `cash_pay_payload` giữ số tiền gốc trong checkout sessionStorage trước khi gửi; unknown giữ payload qua reload; definitive4xx cho sửa lại. Khóa input/quick amounts khi busy hoặc còn payload chưa xác định. Không thêm endpoint/ledger/offline engine.
- **Browser đơn96:** giá5.000, khách10.000, fault sau pay commit; reload và retry trả hóa đơn96 đúng khách10.000/thối5.000. DB trước/sau cùng ledger id95 CASH_TOPUP5.000, không thêm lần thu. Đơn96 tạo trước bản khóa payload nên số tiền được nhập lại đúng10.000 khi kiểm thử; không nhận durable-lock PASS từ ca này.
- **Browser bản mới đơn97:** giá5.000, khách20.000. Fault sau pay commit, UI báo chưa xác nhận, input và quick amounts disabled. Reload vẫn20.000 disabled, tiền thối15.000, retry khả dụng. DB độc lập trước retry đã PAID, ledger id96 CASH_TOPUP5.000, tồn Lavie14.
- Tab bị đóng khi lượt tương tác được khởi tạo lại nên click retry97 qua browser không thực hiện. Hoàn tất retry qua **API thật** với20.000; assert so toàn tuple order/operation/fingerprint/money, ledger rows và stock trước/sau bằng nhau. Không gọi bước API này là browser PASS. Operation đơn97 `85377d07-79d1-43f2-b8c0-554dbd42751c`, fingerprint `19c7c950d6348a81c56c51a3cd6af9b464b7ebffc2503f4acab515527f26a146`.
- **Checks:** pos-request-r5 harness thêm unknown→serialize/reload→retry giữ20k/10k gốc dù biến UI đổi, definitive400 giải phóng payload; passed. pos-offline-ui harness passed. Pytest9ca retry/stabilization +10ca cashier/return UI passed; includes concurrent create retry/shop lock và cash server totals. `node --check` POS/locales và `git diff --check` đạt. Không cộng rerun thành tổng unique mới.
- Asset POS và locale POS bump `20260911-r5-7`; F&B CSS giữr5-6, asset khácr5-5. Bản sửa tiền mặt mới được self-review và focused checks, chưa review độc lập mới/full-suite.

Bằng chứng số liệu ngoài repo: `../r5-pos-before-retry.json`, `../r5-pos97-before-retry.json`, `../r5-fault-evidence.log`. Remaining TEST_GAP: browser retry97 sau reload bị ngắt, actual network disconnect/body truncation thay vì503, multi-tab, PWA/thiết bị thật và ma trận UAT chưa phủ đủ. Gate full-suite vẫn do owner chạy. Không commit/push/deploy.

## SUPERSEDED — commit 5750da9 — Bổ sung vòng tiếp tục — 2026-09-11

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

## SUPERSEDED — commit 5750da9 — Phạm vi và môi trường

- Worktree: `C:/Users/nguye/.codex/worktrees/7a90/python_app`, detached HEAD `18eca4aa6299fe9072cbf9dff3faaa62c658ec4e` (baseline Plan 4, xác minh lại cuối vòng).
- Thay đổi chỉ HTML/CSS/JS, focused tests và tài liệu. Không đổi backend, migration, permission, ledger, transaction fence hoặc offline lease; không thêm dependency.
- Runtime local `http://127.0.0.1:8515`, DB giả ngoài repo `C:/Users/nguye/.codex/worktrees/7a90/plan5-runtime-demo.db`. Bản demo riêng được nâng migration/verify tới 0014 (14 migration); không dùng DB production. Gemini/TTS tắt.
- Không commit, push, merge hay deploy. Full-suite/commit là gate do owner chạy, chưa thực hiện trong vòng này.
- Nhãn: `AUTOMATED` là test/source/DB; `BROWSER` là thao tác và quan sát UI thật trong trình duyệt mô phỏng viewport; `TEST_GAP` chưa chứng minh; `OPEN` là quyết định nghiệm thu còn mở. Không coi harness là browser UAT.

## SUPERSEDED — commit 5750da9 — Kết quả theo lỗi và task

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

## SUPERSEDED — commit 5750da9 — Bằng chứng browser / DB

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

## SUPERSEDED — commit 5750da9 — Checks đã chạy

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

## SUPERSEDED — commit 5750da9 — Review độc lập

Reviewer `review_r5` (GPT-5.6 Sol) chỉ đọc diff/chạy focused Node. Đã phát hiện và đóng:

- P1: thiếu deadline ở manager PIN/approval →30s/slow10s.
- P2: generic403 còn recoverable reapply →clear pending/draft, permission error đưa vềPOS; lỗi validation vẫn cho sửa.
- P2: thiếu3route F&B trong SW shell →đã bổ sung sau verify200.
- P2: PIN callback cũ ghi đè shop mới →generation+shop guard cho slow/success/error, regression pass.

Reviewer xác nhận không còn blocker trong phần đã review. Đây không phải Astra High gate hoặc chứng nhận production. Hai chỉnh cuối thuần copy KDS và màu utility được self-review/syntax/UI-source tests; chưa có vòng review độc lập mới cho hai chỉnh đó.

## SUPERSEDED — commit 5750da9 — Acceptance coverage

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

## SUPERSEDED — commit 5750da9 — UAT U01–U14

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

## SUPERSEDED — commit 5750da9 — Bàn giao

Mã và focused regression sẵn sàng owner review; release acceptance vẫn OPEN vì các TEST_GAP trên. Giữ worktree và DB giả để tái hiện. Owner có thể chạy gate không commit trước:

```powershell
cd C:\Users\nguye\.codex\worktrees\7a90\python_app
.\test-commit.ps1 -TestOnly
```

Không chạy commit mode khi chưa xem staging/scope (script có hành vi staging riêng). Không tự hợp nhất vào baseline đã accepted.

## SUPERSEDED — commit 5750da9 — Asset signature

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
