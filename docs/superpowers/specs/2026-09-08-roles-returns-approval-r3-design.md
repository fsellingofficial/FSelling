# Plan 3 — Roles, Returns and Approval R3 Design

**Date:** 2026-09-08
**Baseline:** `9f9957d23eae71b4c4a8e462c18da6f6b1b374cf`
**Branch:** `codex/roles-returns-approval-plan3`

## 1. Purpose

Plan 3 closes two control gaps without rewriting F-Selling's transaction core:

1. A waiter currently needs a role broader than the task of serving tables.
2. Retail returns currently share `SALE` permission, default to restocking, allow an
   empty reason, and do not provide a manager-approval path for exceptions.

The result must make it clear who may sell, serve, return, approve, refund, and
change stock. A rejected or interrupted return must not partially change money,
cash shifts, stock, batches, cost pools, loyalty points, approval state, or logs.

## 2. Scope

### In scope

- Add a dedicated `SERVICE` staff role.
- Add task permissions `ORDER_RETURN` and `RETURN_APPROVE`.
- Separate return authorization from `SALE`.
- Require an explicit condition decision for each returned line.
- Require a return reason, and require a reference for transfer refunds.
- Distinguish ordinary returns from manager-approved exceptions.
- Generalize the existing PIN/token mechanics for safe reuse by Retail while
  keeping current F&B behavior compatible.
- Bind approval tokens to the exact actor, shop, order, operation and return
  context; make them one-use and five minutes long.
- Record the approver on the durable return record.
- Update role-aware UI for waiter, cashier, manager and owner contexts.
- Add focused automated coverage and browser UAT for authorization, money,
  inventory, approval, interruption and recovery.

### Out of scope

- Configurable monetary thresholds or a general policy engine.
- Remote approval queues, push notifications or approval from another device.
- Multiple simultaneous login sessions; that belongs to Plan 4.
- Offline return creation; existing fail-closed behavior remains.
- Real bank transfer execution or payment-provider calls.
- Reworking return price, voucher, loyalty, batch or cost-allocation algorithms.
- Redesigning all Seller/POS/F&B screens; broad device usability belongs to Plan 5.
- Renaming legacy approval tables or PIN columns only for naming consistency.

## 3. Safety invariants

The implementation must preserve these invariants:

1. The server, not DOM visibility or `localStorage`, is the authorization source.
2. A staff member with `SALE` but without `ORDER_RETURN` cannot create a return.
3. A cashier can execute only an ordinary return unless a valid approval token is
   supplied for the exact exceptional request.
4. An owner or staff manager with `RETURN_APPROVE` may execute an exceptional
   return directly; the durable record still identifies that actor.
5. Missing, invalid, expired, reused, cross-shop, cross-actor, cross-order or
   mismatched tokens fail before any side effect.
6. The final return amount is always computed by the server after the shop lock.
7. Cash refunds require the acting cashier's open cash shift before any mutation.
8. A return is atomic across the return record, payment ledger, cash shift, stock,
   batch provenance, cost pools, loyalty entries, approval use and system log.
9. Retries with the same operation ID and identical fingerprint return the same
   durable result; a different request with the same operation ID is rejected.
10. PINs and raw approval tokens never appear in logs, stored drafts, URLs or
    historical API responses.
11. Existing F&B cancellation approvals keep their session/revision binding,
    rate limit, expiry and one-use behavior.
12. Existing Seller changes in the primary checkout are never overwritten. Plan 3
    edits happen only in its isolated worktree and are reconciled explicitly later.

## 4. Role and permission model

### 4.1 Presets

| Staff role | Default task permissions |
|---|---|
| `SERVICE` | `CATALOG_READ`, `FNB_SERVICE` |
| `CASHIER` | Existing cashier permissions plus `ORDER_RETURN` |
| `WAREHOUSE` | Existing inventory and catalog-read permissions |
| `KITCHEN` | Existing kitchen and catalog-read permissions |
| `BAR` | Existing bar and catalog-read permissions |
| `MANAGER` | Existing manager permissions plus `ORDER_RETURN`, `RETURN_APPROVE` |

`CASHIER` retains `FNB_SERVICE` and `FNB_CHECKOUT` so a cashier can open a table
context and take payment. `SERVICE` deliberately lacks `SALE`, `FNB_CHECKOUT`,
`ORDER_RETURN`, `RETURN_APPROVE`, `FNB_MANAGE`, inventory and reconciliation.

### 4.2 Compatibility

- `StaffRole` adds `SERVICE`; unknown role strings remain fail-closed.
- A legacy STAFF row whose `staff_role` is null continues to resolve to `MANAGER`
  as the current compatibility contract requires.
- Changing a staff role continues to rotate `session_id`, forcing the old session
  to sign in again and pick up the new preset.
- Sellers and admins remain outside staff presets, subject to existing shop/admin
  access checks.

### 4.3 Backend enforcement

`return_service.create_return()` must require `ORDER_RETURN`, not `SALE`.
Approval issuance must accept only the shop owner or an active staff member in
that shop with `RETURN_APPROVE`. UI permission maps are presentational mirrors;
tests must prove direct API calls are also denied.

## 5. Ordinary and exceptional returns

### 5.1 Ordinary return

A cashier may complete a return without manager approval only when all applicable
conditions hold:

- The source order is `PAID` and every selected quantity is still returnable.
- Every selected line explicitly declares `restock=true`.
- `reason` is non-empty after trimming.
- A cash order is refunded by cash.
- A transfer order is refunded by transfer and has a non-empty reference.
- The order was not paid as debt and does not have an ambiguous/mixed source.
- A required cashier cash shift exists and can be locked.

A zero-value return may omit `method`, as today, but still requires explicit line
condition and reason. It becomes exceptional if any selected line is not restocked.

Payment source classification is server-side and conservative. `cash-only` means
the source order is cash and no later incoming transfer settlement contradicts it;
`transfer-only` means the source order and all incoming settlement entries are
transfer/bank based. Debt-origin orders and any order containing both cash-family
and transfer-family settlement entries are `ambiguous` and require approval.

### 5.2 Exceptional return

Manager approval is required if any condition is true:

- At least one selected line has `restock=false`.
- Refund method differs from the unambiguous source payment method.
- The order originated as debt, was settled from multiple sources, or its payment
  source cannot be classified safely.
- The server identifies another return-policy exception introduced within Plan 3.

There is no configurable amount threshold in R3. A future threshold must be based
on observed operations and requires its own design rather than a dormant setting.

## 6. Approval architecture

### 6.1 Reuse boundary

PIN verification, constant-time failure work, failed-attempt rate limiting, token
hashing, five-minute expiry and one-use consumption move into a shared approval
service. F&B keeps its current endpoint contract and calls the shared primitives.

The physical `fnb_manager_approvals` table and `users.fnb_manager_pin_hash` column
keep their legacy names in R3. Renaming them creates migration risk with no
operational value. Code comments must identify the compatibility compromise.

### 6.2 Durable changes

Migration `0013` adds only:

- nullable `context_fingerprint` to `fnb_manager_approvals`;
- nullable `manager_approval_id` foreign key on `order_returns`.

Existing F&B rows keep a null context fingerprint and remain valid under their
existing entity/revision checks. New `ORDER_RETURN_EXCEPTION` approvals require a
non-null context fingerprint. Return approvals use `entity_type='ORDER'`,
`entity_id=<order id>` and `revision=0`; the context fingerprint is authoritative
because Retail orders have no single revision column.

### 6.3 Context fingerprint

The server computes a stable SHA-256 fingerprint from normalized, sorted data:

- shop ID, actor user ID, order ID and operation ID;
- selected order-item IDs, quantities and `restock` decisions;
- normalized refund method, reason and reference;
- order status and source payment classification;
- current cumulative returned quantity/refund/cost counters for selected lines;
- server-calculated refund amount.

Changing the request or a concurrent return changes the fingerprint and invalidates
the approval. The client may display the fingerprint identifier for diagnosis but
must never be trusted to author it.

### 6.4 Token lifecycle

An approval token is bound to:

- token hash;
- shop and approving user;
- acting user;
- action `ORDER_RETURN_EXCEPTION`;
- entity `ORDER` and order ID;
- context fingerprint;
- creation, expiry and use timestamps.

The final return transaction consumes the approval only after authorization,
shop lock, idempotency replay check, request validation, returnability checks,
cash-shift checks and fingerprint recomputation succeed. Consumption and all return
side effects commit together. Any exception rolls them all back.

## 7. API contracts

### 7.1 Return request

`OrderReturnItemCreate.restock` becomes required; it has no default.
`OrderReturnCreate.reason` becomes required after trimming and adds optional
`approval_token`. Existing `operation_id` and server-side amount calculation remain.

Transfer refunds with a positive amount require `reference`. The backend enforces
this even when a non-official client bypasses the page.

### 7.2 Approval-required response

An exceptional request without a usable token returns HTTP 403:

```json
{
  "detail": {
    "code": "RETURN_APPROVAL_REQUIRED",
    "message": "Cần quản lý duyệt ngoại lệ trả hàng",
    "approval_context": {
      "order_id": 128,
      "refund_amount_vnd": 95000,
      "refund_method": "cash",
      "non_restock_item_count": 1,
      "reason_codes": ["NON_RESTOCK"],
      "context_fingerprint": "<sha256>"
    }
  }
}
```

This response is produced only after the server calculates the current return
context. No durable money, stock, loyalty, approval or log mutation occurs.

### 7.3 Return-specific approval endpoint

`POST /api/orders/{order_id}/returns/approval` accepts:

- the same normalized return draft and operation ID;
- the server-issued context fingerprint;
- approver username and PIN.

The endpoint recomputes the return context, verifies it matches, then issues a
one-use token. It does not accept a client-authored amount or policy decision.

Success returns only the token, expiry seconds and a safe approval summary. PIN is
write-only and excluded from logs/errors. A generic internal service is reused,
but the public endpoint remains return-specific so Plan 3 does not introduce an
untyped approval-policy API.

### 7.4 Manager PIN endpoint

A generic shop manager-PIN endpoint is exposed for owner/manager settings. The old
F&B route remains as a compatibility alias and uses the same service. Responses use
generic error codes; the existing F&B route preserves current F&B codes where its
tests and UI contract require them.

### 7.5 Stable error semantics

| Code | HTTP | Meaning and recovery |
|---|---:|---|
| `RETURN_APPROVAL_REQUIRED` | 403 | Show the approval dialog with server summary |
| `RETURN_APPROVAL_INVALID` | 403 | A supplied token is invalid, expired, used or mismatched; approve again |
| `RETURN_CONTEXT_CHANGED` | 409 | Order/return state changed; reload and review the draft |
| `RETURN_REASON_REQUIRED` | 400 | Preserve draft and focus reason |
| `RETURN_REFERENCE_REQUIRED` | 400 | Preserve draft and focus transfer reference |
| Existing idempotency conflict | 409 | Do not create a new operation; review the changed request |

Authorization failures must not disclose whether another shop's order, approver or
approval token exists.

## 8. User flows and interface

### 8.1 Return form on cashier PC/tablet

For each selected line, the quantity control reveals two required condition choices:

- `Bán lại được — nhập lại kho`
- `Hỏng/không đủ điều kiện — không nhập kho`

Neither is preselected. The current checked restock checkbox is removed.

The summary keeps the server as final authority and shows:

- customer refund amount and method;
- cash leaving the current shift;
- product quantity returning to available stock;
- loyalty adjustment as server-calculated on completion;
- whether manager approval is required and why.

The ordinary CTA is `Xác nhận trả và hoàn <amount>`. When the server requires
approval, the page opens the approval dialog rather than calling the failure
"not synchronized".

### 8.2 Approval dialog

The dialog repeats the trusted server summary: order, actor, amount, method,
non-restocked lines, return reason and reason codes. It asks for approver username
and PIN. A successful approval retries the unchanged return with the same operation
ID and the returned token.

Wrong PIN keeps the return draft and summary but clears the PIN. A context conflict
closes the approval dialog, refreshes the source order and asks the cashier to
review again. Editing any approved field discards the token.

### 8.3 Waiter mobile context

`SERVICE` login routes to `/fnb`. At 390x844 the waiter can reach table/session,
menu, send/transfer/cancel-request and ready-to-serve information with one hand.
Payment, return, shop setup, station setup and PIN controls are absent. When the
next step belongs to another role, the UI says which role must act rather than
leaving a disabled button without explanation.

### 8.4 Cashier and manager contexts

- Cashier login still routes to POS and retains a route into F&B checkout.
- Owner/manager can manage the new `SERVICE` preset and read a short task-based
  permission description beside each role.
- Manager PIN moves to a shared settings surface while the F&B setting remains
  compatible during R3.
- No approval inbox, remote push or cross-device workflow is added.

### 8.5 Seller-file boundary

Plan 3 necessarily changes staff-role controls in Seller files. Those edits occur
only on the isolated Plan 3 branch. Before later integration, compare and reconcile
the five protected primary-checkout files explicitly:

- `static/css/seller.css`
- `static/js/locales/seller.js`
- `static/js/seller.js`
- `static/seller.html`
- `tests/test_seller_sidebar_ui.py`

No merge strategy may overwrite the primary checkout's uncommitted versions.

## 9. State inventory and recovery

| State | User sees | Allowed recovery |
|---|---|---|
| Loading order | Busy state on lookup only | Cancel or wait; no submit |
| No returnable lines | Explicit explanation | Close and inspect history |
| Missing condition | Inline message on the selected line | Choose one condition |
| Missing reason/reference | Field-specific message, input preserved | Complete the field |
| Approval required | Trusted amount/stock summary and PIN dialog | Approve or return to edit |
| Wrong PIN | Specific error; PIN cleared; draft retained | Retry within rate limit |
| PIN rate-limited | Retry-after guidance | Wait or have authorized manager act directly |
| Context changed | "Chưa trả hàng" and refreshed order state | Review quantities and resubmit |
| Request pending | Button busy; no duplicate operation ID | Wait; graduated delay message |
| Unknown result | "Chưa biết phiếu đã được ghi hay chưa" | Retry the same operation ID |
| Success | Durable return ID, amount, stock/points effect | Close or inspect return history |
| Permission denied | Responsible role named | Ask cashier/manager; no dead button |
| Offline/backend unavailable | Return is not queued offline | Retry when connected with same draft/operation ID |

The UI must not optimistically show a return, refund, restock or approval as complete.

## 10. Audit trail

Each durable return must expose or link:

- source order ID and return ID;
- actor user ID/username;
- approving user and approval ID when applicable;
- reason, amount and refund method;
- line quantities and restock decisions;
- loyalty, stock/batch and shift effects;
- operation ID and timestamps.

`ORDER_RETURN` remains visible in the shop activity feed. The log summary includes
amount, method, non-restock count, reason, actor and approver where applicable.
Normalized relational records remain the source of truth; the free-text log is a
human-readable index, not the only audit evidence.

## 11. Security and privacy

- Approval lookup is scoped by shop, actor, action, entity and fingerprint.
- Invalid approver/token responses are non-enumerating across shops.
- Wrong username and wrong PIN perform equivalent password-hash work.
- Failed PIN attempts retain the current 5-per-15-minute actor/shop protection.
- Only token hashes are stored.
- PIN inputs use appropriate autocomplete suppression and are cleared after use,
  close, role/session change or conflict.
- Logs and test failure output must not print PINs or raw tokens.
- Direct API coverage is mandatory; UI hiding is not a security test.

## 12. Compatibility and migration

- Migration `0013` is fail-closed and must have upgrade, verifier, checksum and
  explicit forward-only downgrade tests consistent with the current migration
  framework.
- Upgrade verifies expected legacy approval/return structures before DDL.
- Existing rows are not rewritten or assigned invented approvers.
- Existing F&B approvals continue to work with null context fingerprint.
- Existing return records keep null `manager_approval_id` and remain readable.
- Old clients omitting required `restock` or reason receive validation errors and
  cannot silently regain the unsafe defaults.
- No production deploy, provider call or real data migration is authorized by this
  design.

## 13. Verification strategy

### 13.1 Focused automated gates

1. Role/schema/login tests for `SERVICE` and legacy null-role compatibility.
2. Backend authorization matrix for all staff presets and cross-shop attempts.
3. Ordinary return tests for cash, transfer, zero refund, voucher and loyalty.
4. Exception tests for non-restock, method change, debt and ambiguous source.
5. Approval tests for wrong PIN, rate limit, expiry, replay, actor/shop/order/action
   mismatch, context mutation and concurrent return.
6. Atomic rollback assertions covering return/payment/shift/stock/batch/cost/loyalty/
   approval/log counts and values.
7. Operation-ID timeout/retry tests for pre-commit and post-commit uncertainty.
8. Existing F&B approval/cancellation regressions.
9. Static/JS tests for role routing, permission-aware controls, required condition,
   approval recovery and unknown-result copy.
10. Migration `0013`, manifest checksum and verify tests.

### 13.2 Browser UAT

- Waiter at 390x844: login routing, table/menu/send/transfer/cancel request, no
  financial/setup controls, and an understandable handoff to cashier/manager.
- Cashier at 1024x768 and desktop: ordinary return from lookup through durable
  success, including the cash-shift effect.
- Exceptional non-restock return: wrong PIN then correct PIN.
- Method-change return and transfer-reference requirement.
- Permission denial and invalidated session after owner changes a role.
- Delayed/lost response followed by retry with the same operation ID.
- Owner/manager follows activity evidence from return to actor and approver.

Browser claims must be recorded as observations. Automated coverage must not be
promoted to browser evidence. External payment providers, deployment and production
data remain `UNKNOWN-PROD`.

### 13.3 Release gates

- Run focused tests during implementation; do not repeatedly run the full suite.
- Run `git diff --check`, Python/JS syntax checks and the required browser cases.
- The project owner runs `test-commit.ps1 -TestOnly` once at the final release gate.
- An independent correctness review must return `ACCEPT` before Plan 4 begins.

## 14. Acceptance criteria

Plan 3 is complete only when:

1. Waiters can perform service work without receiving sale/checkout/return rights.
2. Direct API calls enforce the same role boundaries as the UI.
3. A cashier ordinary return remains fast and preserves existing financial logic.
4. Unsafe restock defaults and empty return reasons no longer exist.
5. Every exceptional return is either performed by an authorized manager/owner or
   carries a valid one-use approval bound to its exact current context.
6. Interrupted/repeated requests cannot duplicate or partially apply money, stock,
   points, approvals or logs.
7. Return history identifies actor, approver and operational consequences without
   exposing credentials.
8. Existing F&B approval behavior and Plan 1/2 safety invariants remain green.
9. Protected Seller work in the primary checkout is preserved during integration.
10. Test/UAT gaps and production unknowns are reported explicitly.
