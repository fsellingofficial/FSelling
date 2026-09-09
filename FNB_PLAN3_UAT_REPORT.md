# F&B Plan 3 UAT evidence

Date: 2026-09-08
Branch: `codex/roles-returns-approval-plan3`
Baseline commit: `31a5fa6f2ed5da2d15599c2f9d2458a3f9f68968`

## Asset starting hashes

Recorded before Task 7 edits (SHA-256):

- `static/js/auth.js`: `C94763A95B5377EB57C71251E05C9615D57E0E8BE4D46DC39B49DF4871A86426`
- `static/js/fnb-r1a.js`: `933BD55DAF8CC6C615850BEC9FD36F62C60A7E52F49C3D8261E468ED0E7FB935`
- `static/fnb.html`: `36FCBAF60E39CE46590EA78B64483C833D7E0BCC599E3E85D6DC9C45B61D1FD5`
- `static/js/locales/fnb.js`: `848A23B51C5BD27CCA231FBFB3FEC6A9B69BA9F7E2987D8D98377B5C5D705531`
- `static/js/seller.js`: `AA22CB659D62D3CEC20E671B24015313CC3FF779ECA04FD7AE581C076DBB2F02`
- `static/seller.html`: `43963449DAEF263BEA0F6EF583B660B0144A9BB90E06F296A425C0785E3B40D8`
- `static/css/seller.css`: `801FB7AA9D78C2041F1F8FCB1EE49831F6856889BCE115B36CA85DEF609CD2E8`
- `static/js/locales/seller.js`: `CDC01FEC67A60FBE8A5077197EEF667901419AC0505E9C5D1D3070265313B0CF`

## Automated focused gates

Tasks 1-7 were implemented test-first. Each task was observed RED before the
smallest implementation change, then run GREEN with its focused regression and
`git diff --check` before moving on.

Final Plan 3 focused gate (system Python is the same environment used for all
RED/GREEN loops):

```powershell
python -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_staff_roles.py tests/test_manager_approval.py `
  tests/test_return_approval_r3.py tests/test_tra_hang.py tests/test_nhat_ky_shop.py `
  tests/test_fnb_r1b_cancel.py tests/test_contract.py `
  tests/test_pos_return_r3_ui.py tests/test_fnb_r1a_ui.py `
  tests/test_seller_sidebar_ui.py `
  tests/test_migration_0013_roles_returns_approval_r3.py `
  tests/test_migration_0012_fnb_ticket_service_handoff.py `
  tests/test_migration_i04.py tests/test_migration_i05.py `
  tests/test_offline_lease_identity.py
```

- Exit code: `0`
- Result: `226 passed, 1 warning in 265.67s`
- Warning: upstream Starlette/httpx deprecation only.

Additional F&B role/API regression after browser discovery and correction:

```powershell
python -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_sessions.py tests/test_fnb_r1a_ui.py tests/test_staff_roles.py
```

- Exit code: `0`
- Result: `38 passed, 1 warning in 38.22s`

Final JS/syntax/whitespace gates:

```text
node tests/js/auth-offline-seal.test.js  -> exit 0, 1 passed
node tests/js/fnb-r1a.test.js            -> exit 0, controller ok
node tests/js/pos-return-r3.test.js      -> exit 0, PASS
python -m compileall -q fselling         -> exit 0
git diff --check                         -> exit 0
```

Owner-run full-suite gate:

- The first real full-suite run reached four legacy offline-return failures:
  those payloads did not yet include the new required return reason/reference.
- Compatibility correction: the four failures passed (`4 passed`), followed by
  both affected files as focused regression (`87 passed, 1 warning in 89.39s`)
  and `git diff --check` exit `0`.
- The owner then reran `test-commit.ps1 -TestOnly` in this Plan 3 worktree. It
  reached `[100%]`, printed `TEST PASS - toan bo test deu xanh`, completed in
  `2,863.5` seconds and returned `PYTEST_EXIT_CODE=0`.
- The pasted tail did not include pytest's total collected/passed count, so no
  exact full-suite count is claimed.
- A temporary test-runner correction selected the first PATH Python when this
  machine exposed three candidates. It was removed after the successful run and
  is not part of the Plan 3 diff.

The plan's literal `.\.venv\Scripts\python.exe ...` command exited `1` before
pytest started because this worktree has no `.venv`; no tests ran in that
attempt. This is why the established system Python environment was used.

## Browser UAT

Environment:

- Local-only app: `http://127.0.0.1:8765`
- Isolated demo-only DB: `%LOCALAPPDATA%\Temp\fselling-plan3-uat-01a07f02-r2\plan3-uat.db`
- `migration.cli upgrade head`: exit `0`
- `migration.cli verify`: exit `0`; current/head `0013`, revision count `13`
- No `.env`, real provider, tunnel, production database or real business data.
- The legacy demo seeder stopped after creating its sample catalog/orders when
  its old transfer-return example omitted the new required reference. The
  isolated DB was still migrated and verified before the app started.

### 1. SERVICE mobile, 390x844

- `phucvu` (`SERVICE`) logged in and routed to `/fnb`.
- Table 1 was opened, items were added/sent, then transferred to Table 2.
- Ticket `#1` used operation IDs `uat-r3-ticket-start-001` and
  `uat-r3-ticket-done-001`; SERVICE saw READY, marked it served, and saw the
  delivered state.
- A sent DIRECT item was cancelled from the service flow and the subtotal
  changed from 15,000 VND to 10,000 VND.
- Payment, return, merge/setup and manager-PIN controls were absent. The visible
  handoff said payment/return must go to cashier or manager.
- Browser-generated table/send/cancel operation UUIDs are not exposed by the
  public response or activity view, so their exact values could not be recorded
  without reading the database. Return/approval IDs below are exposed normally.

### 2. CASHIER ordinary cash return, 1024x768

- Cashier shift `#2`; order `#89`; product `#1`; return `#1`.
- Before: expected cash 500,000 VND, cash refunds 0, stock 20.
- After one restocked 5,000 VND return: expected cash 495,000 VND, cash refunds
  5,000 VND, stock 21.
- The UI completed lookup -> explicit quantity/condition/reason -> confirmation
  -> success and showed `Phiếu trả #1`.

### 3. Transfer return/reference

- Order `#88`, return `#2`, approval `#1`, reference
  `UAT-TRANSFER-88-001`.
- Selecting transfer exposed a required-reference error and kept submit disabled
  until the reference was entered.
- Manager summary showed `METHOD_CHANGE`. After approval, cashier shift `#2`
  stayed at expected cash 495,000 VND / cash refunds 5,000 VND.

### 4. Damaged/non-restock approval

- Order `#90`, return `#3`, used approval `#3`.
- Wrong PIN `0000` showed `PIN quản lý không đúng`; the return summary/draft
  remained visible and the PIN field was cleared.
- Correct PIN completed exactly one 10,000 VND cash return.
- Cashier shift moved once from 495,000 to 485,000 VND and cash refunds from
  5,000 to 15,000 VND. Product `#2` stock remained `4 -> 4`.

### 5. Source/method exception summaries

- Method-change summary exposed `METHOD_CHANGE` for order `#88`.
- Demo-only order `#92` was fully collected from debt using 5,000 VND transfer
  (`uat-r3-mixed-transfer-001`) plus 10,000 VND cash
  (`uat-r3-mixed-cash-001`). Its return draft exposed `DEBT_SOURCE` in both the
  server-confirmed summary and manager dialog. The dialog was cancelled; no
  return was created for this evidence case.

### 6. Live role change

- Staff `#4` changed `SERVICE -> CASHIER` through the owner API.
- Reloading the old SERVICE browser session redirected to login after the API
  rejected the invalidated session (`401` behavior).
- Fresh login routed to `/pos` and showed the cashier preset with no manager
  settings; it had no open cash shift.

### 7. Delayed/lost response

- An in-app browser submitted through a local response-dropping proxy to the
  real FastAPI return endpoint backed by an isolated temporary database. The
  proxy closed the socket only after the backend committed: the browser saw
  `TypeError: Failed to fetch`, controller state became `unknown`, and the
  database already contained exactly one durable return (`PASS`).
- Browser retry sent `uat-real-response-loss-001` and quantity `1` again. The
  backend returned `Lần trả hàng này đã được ghi nhận trước đó`; both captured
  requests had the same operation ID and payload quantity, while the durable
  return count remained one (`PASS`).

### 8. Owner activity view

The owner opened `Nhật ký hoạt động` and saw:

- Order `#89`: actor `nhanvien`, cash, 5,000 VND, reason, no approver.
- Order `#88`: actor `nhanvien`, approver `demo`, transfer, 5,000 VND, reason.
- Order `#90`: actor `nhanvien`, approver `demo`, cash, 10,000 VND, reason and
  `1 dòng KHÔNG nhập lại kho`.
- The `SERVICE -> CASHIER` role change was also visible.

### 9. Independent-review corrections

- Local browser harness: an invalid/expired approval response cleared the token,
  returned the controller to `approval-required`, cleared PIN and reopened the
  existing approval dialog (`PASS`).
- Focused backend evidence: PIN and approval-token validation errors no longer
  echo Pydantic `input`; stale approved quantity races return
  `409 RETURN_CONTEXT_CHANGED`; wrong token/actor/shop/order/action and expiry
  return non-enumerating invalid-approval errors without side effects.
- Atomic rollback coverage injects failure after approval selection, return
  flush, loyalty entry, first batch/provenance update, payment add, audit add
  and immediately before commit. Every case compares approval, return/payment,
  cash shift, stock/batch/cost, loyalty and audit state unchanged.

## Primary-checkout reconciliation warning

Read-only comparison on 2026-09-08 found uncommitted user-owned changes in the
primary checkout for all four Seller assets and `tests/test_seller_sidebar_ui.py`.
Primary hashes at comparison time:

- `static/js/seller.js`: `0BAA1C47B77DEDF1E12CA6F9764CA39DF038DA97BCA42801F763E38E81436C00`
- `static/seller.html`: `D8C11F9E5F037746DAC6B1B46B513BC31433374D6491207DB07C93FC791C6E65`
- `static/css/seller.css`: `07AC90C41EBD0D70DA7FD7F126B050504FD5C79E608BDBA15B88F9FD766A85C9`
- `static/js/locales/seller.js`: `967F8E4D9FDA641AC7D4B97A42BE9CB8544EC4D899404F4C2932ECFA57643ED3`

Those files were not copied from or written to the primary checkout. Their
user-owned primary changes must be reconciled deliberately at later integration;
the Plan 3 worktree must not overwrite them wholesale.

## Limitations

- Full suite: owner-run `test-commit.ps1 -TestOnly` exited `0` after the latest
  review corrections on 2026-09-09 (2,566.9 seconds).
- Payment provider, deployment and production migration behavior: `UNKNOWN-PROD`.
- Browser-generated operation UUIDs are not exposed by public UI/API evidence.
