# F&B Plan 2 — Focused Verification and UAT Report

Date: 2026-09-07
Branch: `codex/fnb-order-lifecycle-plan2`
Baseline: `7c17fff097f3892ca41b6d962f17ed9348dd819d`

## Scope and environment

- Isolated worktree: `C:\Users\nguye\.codex\worktrees\7f9e\python_app`
- Isolated SQLite database: `C:\Users\nguye\AppData\Local\Temp\fselling-plan2-uat-7f9e\plan2.db`
- Local server only: `http://127.0.0.1:8017`
- No real customer data, secret, external service or payment provider was used.
- The five protected Seller files remained outside the diff.
- `seed_full_demo.py` migrated and partially seeded the temporary database, then stopped while creating staff because its generated password did not satisfy the current password policy. The utility was not changed; the minimum F&B fixtures were completed through the local API.

## Focused automated verification

| Gate | Result |
|---|---:|
| Baseline safety set | 24 passed |
| Task 1 merge/close guards | 28 passed |
| Migration 0012 | 14 passed |
| Task 3 lifecycle/revision | 31 passed |
| Task 4 partial settlement | 32 passed |
| Task 5 main UI controller/DOM | Node harness passed; 12 pytest passed |
| Task 6 floor/service UI | 13 passed |
| Task 7 KDS retry/epoch/connectivity | Node harness passed; 15 pytest passed |
| Combined focused gate | 137 passed, 1 failed in 287.56s |
| Corrected stale floor-summary expectation | 1 passed |
| Final representative regression | 11 passed; both Node harnesses passed |
| Initial user-run full suite after Plan 2 | 2303 passed, 133 failed, 1 skipped in 2395.32s |
| Focused rerun of all six failing files after topology fix | 216 passed |
| Final user-run full repository suite after correction | PASS at 100%; `PYTEST_EXIT_CODE=0`; count not captured |

The only combined-gate failure was an existing exact dictionary expectation that did not yet include the newly approved `service_stage` and zeroed `service_summary` fields. The expectation was updated and that exact test passed on rerun. The combined gate was not repeated because the plan forbids duplicate long test runs in the implementation loop. The observed warning was Starlette's deprecation warning; no Plan 2 assertion warning was introduced.

The first user-run full suite exposed one additional migration-test maintenance gap: six legacy test files still treated revision `0011` as head, and copied old-binary fixtures removed revisions only through `0011`, leaving `0012` with a missing parent. This single topology mismatch cascaded into 133 parameterized failures. Their head expectations and fixture manifests were updated for `0012`; all 216 tests in those six files then passed in one focused process.

After that correction, the project owner ran the full repository suite again. The supplied terminal evidence reached `[100%]` and immediately reported `PYTEST_EXIT_CODE=0`. The final test count was not present in the supplied output, so none is inferred here. This successful run is the final Plan 2 release-gate state; the earlier 133-failure row is retained only as discovery history.

Static checks also passed: Python compilation, both Node controller harnesses and `git diff --check` (line-ending notices only).

## Browser UAT

Browser: Codex in-app browser, local application only.

| Role / viewport | Task | Evidence | Result |
|---|---|---|---|
| Owner, desktop 1366×768 | Open table, add and send `Cơm gà UAT` | Table session changed from empty draft to ticket #1 in `Đang chờ bếp/bar` | Pass |
| Owner with F&B management permission, KDS tablet 1024×768 | Receive ticket, report out-of-stock, resume, start and mark READY | Out-of-stock reason persisted; `Tiếp tục chế biến`, `Nhận làm`, then `Sẵn sàng giao` appeared in order | Pass |
| Owner, desktop 1366×768 | Observe READY and serve | Session group and floor summary both showed READY = 1; after `Đã giao cho khách`, READY returned to 0 and SERVED became 1 | Pass |
| Owner, mobile 390×844 | Inspect active table session | Menu, bill pane and payment actions remained reachable; responsive panes stacked without losing the core controls | Pass |
| Owner with F&B management permission, KDS tablet 1024×768 | Inspect active ticket card | Ticket #2, table, elapsed time, item, `Nhận làm` and `Báo hết món` were visible without overlap | Pass |
| Owner/cashier flow, desktop 1366×768 | Pay while ticket #2 is READY | Payment completed, but `Đóng bàn đã thanh toán` remained disabled while READY = 1 | Pass |
| Owner/cashier flow, desktop 1366×768 | Serve final ticket and close table | Close button became enabled only after READY = 0; table returned to `Trống` and all floor counters returned to zero | Pass |

Main and KDS console error logs were empty at the end of UAT. The viewport override was reset afterward.

Kitchen-only authorization and stale/late network responses were verified by focused automated tests, not by a separate browser login. Merge conflicts, partial settlement, cancellation conflicts, exact-operation retry, migration downgrade and restart verification are also automated-test evidence; they were not manually forced in the browser.

## Correction after independent review

The independent review of `c82a537` returned **CHANGES REQUIRED**. That verdict remains part of the history and is not relabeled as acceptance. The correction produced `c40e03a` (ticket lifecycle and migration guards), `ce3d5ac` (recovery CTA render order), and `500ab1c` (touch-sized navigation); final evidence is recorded after those commits.

### Focused correction verification

| Gate | Fresh result |
|---|---:|
| Ticket mutation, terminal-session, legacy ghost and lifecycle regressions (`test_fnb_r1b_send.py`) | 15 passed |
| Full/partial cancellation and exact replay (`test_fnb_r1b_cancel.py`) | 5 passed |
| Checkout, partial settlement and close blockers (`test_fnb_r1c_checkout.py`) | 14 passed |
| Migration 0012 plus manifest checksum | 10 passed |
| Main F&B UI/static contract | 13 passed |
| Recovery controller and mounted DOM harness | Node harness passed |
| `git diff --check` for the correction worktree | passed |

Migration 0012 remains undeployed and was amended in place with a new manifest checksum. Upgrade now stops before DDL on legacy `DONE` tickets that still carry `out_of_stock_reason`; verification rejects the same invalid lifecycle. Remediation is deliberately fail-closed: reconcile the ticket to the real operational state and clear the contradictory reason (or move it back to an active preparation state) before retrying upgrade. The downgrade test now calls `downgrade()` and verifies `FORWARD_ONLY_MIGRATION` directly.

### Fresh browser correction UAT

Environment: in-app browser against local-only server `127.0.0.1:8018`, isolated database `C:\Users\nguye\AppData\Local\Temp\fselling-plan2-correction-uat-20260907-002\plan2.db`, synthetic account/product only.

| Scenario | Fresh browser evidence | Result |
|---|---|---:|
| Full cancel of a NEW ticket | Ticket #1 changed the order view from waiting = 1 to waiting = 0; bill became 0 VND and product stock returned from 9 to 10 | Pass |
| Legacy/terminal ghost visibility in KDS | Immediately after full cancel, KDS showed `Mới 0` and `Đang làm 0` | Pass for the corrected full-cancel path; legacy-row coverage remains automated |
| Keyboard navigation | KDS back link received keyboard focus and Enter navigated to `/fnb`; the input call timed out, but the required re-observation confirmed the destination URL | Pass for this navigation control only |
| Touch target | DOM measurement first exposed the KDS back link at 24 px high; after the shared `.fnb-back` correction and asset-version bump it measured 44 px | Pass |
| Recovery CTA after reload | Mounted DOM regression proves the retry CTA is the final visible render after session reload | `TEST_GAP`: not forced in the real browser |
| Lost network after send / serve | Exact operation retry remains covered by controller/service tests | `TEST_GAP`: this browser surface exposes no network-throttling control |
| Partial settlement | Service regression verifies supplemental-check behavior | `TEST_GAP`: not repeated manually after correction |
| Late shop/station response | Controller epoch regressions remain green | `TEST_GAP`: not forced manually after correction |
| Merge conflict | Conflict/reapply controller and service tests remain green | `TEST_GAP`: not forced manually after correction |
| Close with NEW / IN_PROGRESS ticket | Service checkout blocker regression remains green | `TEST_GAP`: not forced manually after correction |
| 200% zoom | Two browser zoom shortcuts did not expose a changed zoom level or viewport metric | `TEST_GAP`: no honest 200% assertion |

The automated rows above are intentionally not promoted to browser evidence. External providers, deployment behavior and production data remain `UNKNOWN-PROD` and were not touched.

## Release boundary

- Full repository suite: the owner-run gate before this correction passed at 100% with exit code 0. Because correction commits followed it, one new owner-run full-suite gate is pending.
- Plan 2 implementation commit: `6f18d01`.
- Push, PR and deploy: not performed.
