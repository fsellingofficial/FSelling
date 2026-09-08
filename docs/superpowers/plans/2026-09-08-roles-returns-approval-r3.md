# Plan 3 — Roles, Returns and Approval R3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Use `superpowers:test-driven-development` for every behavior change, `superpowers:systematic-debugging` for failures, and `superpowers:verification-before-completion` before any completion claim. Do not delegate implementation to additional agents; one Sol High task owns the coherent diff, and one separate Astra High task performs the final independent review.

**Goal:** Tách quyền phục vụ khỏi quyền bán hàng, tách trả hàng khỏi quyền `SALE`, và buộc mọi ngoại lệ trả hàng đi qua một lượt duyệt quản lý ngắn hạn, gắn đúng ngữ cảnh, không làm yếu các bất biến tiền–kho–điểm–ca hiện hữu.

**Architecture:** Giữ nguyên transaction core của `return_service.create_return()` và đưa toàn bộ phần tính/kiểm tra trước mutation vào một helper dùng chung cho endpoint tạo phiếu và endpoint xin duyệt. Thêm đúng hai permission preset và một role `SERVICE`. Trích các primitive PIN/token đang nằm trong F&B sang một service phê duyệt dùng chung; endpoint F&B cũ chỉ làm adapter tương thích. Migration `0013` chỉ thêm hai cột nullable và không đổi tên bảng/cột legacy.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic v2, Alembic framework nội bộ, vanilla HTML/CSS/JavaScript, i18next catalogs, pytest, Node `assert`.

**Spec:** `docs/superpowers/specs/2026-09-08-roles-returns-approval-r3-design.md`

**Baseline:** `9f9957d23eae71b4c4a8e462c18da6f6b1b374cf` on branch `codex/roles-returns-approval-plan3`; design commit `b9120d28c5ce01e808728a6e6a32686d9762f613`.

## Global Constraints

- Work only in `C:\Users\nguye\.codex\worktrees\7f9e\python_app`. Before code, assert the branch, baseline ancestry and a clean worktree. Never edit the primary checkout or its uncommitted Seller files.
- Do not change return amount, voucher allocation, loyalty math, batch/cost provenance, cash-shift accounting, shop lock, operation-ID replay or existing payment ledger semantics except where this plan explicitly adds a guard or audit link.
- Keep `fnb_manager_approvals` and `users.fnb_manager_pin_hash` physically named as-is. Do not add a policy engine, amount threshold, remote approval queue, WebSocket, dependency or framework.
- Server authorization is authoritative. Every UI restriction needs a direct API test; hiding a button is never the security control.
- PIN and raw approval token are write-only. They must not enter logs, URLs, `localStorage`, saved drafts, snapshots or error text.
- All return mutations, including approval consumption/direct-approval evidence, remain in the same DB transaction. Any exception must roll back return, payment, shift, stock, batch, cost, loyalty, approval and log changes together.
- Every implementation task follows RED → GREEN → focused regression → `git diff --check`. Do not run the full suite in the loop and do not invoke `test-commit.ps1` automatically.
- Use checkpoints, not per-task commits. After all focused tests and browser UAT pass, the owner runs `\.\test-commit.ps1 -TestOnly` exactly once. Only then stage the exact Plan 3 file list and create one implementation commit.
- The final release review is a separate Astra High task. It must inspect the committed diff and report `ACCEPT`, `CHANGES REQUIRED`, or evidence gaps with file:line proof. A green suite alone is not acceptance.

## Stable Contracts to Preserve

```text
F&B approval: action=CANCEL_SENT_LINE, entity_type=SESSION,
entity_id=session.id, revision=session.revision, five-minute token.

Retail approval: action=ORDER_RETURN_EXCEPTION, entity_type=ORDER,
entity_id=order.id, revision=0, context_fingerprint is authoritative.
```

Stable Retail errors:

| HTTP | Code | Client recovery |
|---:|---|---|
| 403 | `RETURN_APPROVAL_REQUIRED` | Open approval dialog using the server summary |
| 403 | `RETURN_APPROVAL_INVALID` | Clear token and approve again |
| 409 | `RETURN_CONTEXT_CHANGED` | Close dialog, reload order, review again |
| 400 | `RETURN_REASON_REQUIRED` | Preserve draft and focus reason |
| 400 | `RETURN_REFERENCE_REQUIRED` | Preserve draft and focus reference |
| 409 | Existing operation-ID conflict | Keep the operation ID and review changed request |

---

## Task 1: Add the `SERVICE` preset and dedicated return permissions

**Files:**

- Modify: `fselling/dependencies.py`
- Modify: `fselling/schemas/staff.py`
- Modify: `fselling/services/return_service.py`
- Modify: `tests/test_staff_roles.py`
- Modify: `tests/test_tra_hang.py`

### Step 1: Write failing role and authorization tests

Extend `tests/test_staff_roles.py` with a full preset assertion, not only route visibility:

```python
def test_service_chi_co_quyen_catalog_va_phuc_vu(client):
    ctx = seller_with_shop(client)
    username, _ = new_staff(client, ctx, "SERVICE")
    session = SessionLocal()
    try:
        user = session.query(models.User).filter_by(username=username).one()
        assert effective_staff_role(user) == "SERVICE"
        assert has_staff_permission(user, PERMISSION_CATALOG_READ)
        assert has_staff_permission(user, PERMISSION_FNB_SERVICE)
        for denied in (
            PERMISSION_SALE, PERMISSION_ORDER_RETURN, PERMISSION_RETURN_APPROVE,
            PERMISSION_FNB_CHECKOUT, PERMISSION_FNB_MANAGE,
            PERMISSION_INVENTORY, PERMISSION_RECONCILIATION,
        ):
            assert not has_staff_permission(user, denied)
    finally:
        session.close()
```

Also assert:

- `CASHIER` has its existing permission set plus `ORDER_RETURN`, but not `RETURN_APPROVE`.
- `MANAGER` has both new permissions.
- legacy STAFF with null `staff_role` still resolves to `MANAGER` and therefore gets both permissions.
- unknown role strings remain fail-closed.
- changing `SERVICE → CASHIER` rotates `session_id`; the old token receives 401.

In `tests/test_tra_hang.py`, change the direct authorization contract so a cashier may return, while `SERVICE`, warehouse, kitchen and bar receive 403 even if they call the endpoint directly.

### Step 2: Run RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_staff_roles.py tests/test_tra_hang.py -k 'service or permission or nhan_tra_hang'
```

Expected: schema rejects `SERVICE`, permission constants do not exist, and the return endpoint still follows `SALE`.

### Step 3: Add the minimum preset definitions

In `fselling/dependencies.py`, add and export exactly:

```python
STAFF_ROLE_SERVICE = "SERVICE"
PERMISSION_ORDER_RETURN = "ORDER_RETURN"
PERMISSION_RETURN_APPROVE = "RETURN_APPROVE"
```

Add `STAFF_ROLE_SERVICE` to `STAFF_ROLES`. Add this preset:

```python
STAFF_ROLE_SERVICE: frozenset({
    PERMISSION_CATALOG_READ,
    PERMISSION_FNB_SERVICE,
}),
```

Add `PERMISSION_ORDER_RETURN` to `CASHIER`, and both new permissions to `MANAGER`. Do not modify other presets. In `fselling/schemas/staff.py`, add `"SERVICE"` to the existing `StaffRole` `Literal`.

In `return_service.create_return()` replace only `PERMISSION_SALE` with `PERMISSION_ORDER_RETURN`; Task 4 will move the check into the final flow without changing its meaning.

### Step 4: Run GREEN and nearby regressions

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_staff_roles.py tests/test_tra_hang.py
git diff --check -- fselling/dependencies.py fselling/schemas/staff.py `
  tests/test_staff_roles.py tests/test_tra_hang.py
```

Checkpoint: role model and direct return authorization are reviewable. Do not change frontend yet.

---

## Task 2: Add migration `0013` and durable approval linkage

**Files:**

- Create: `migrations/versions/0013_roles_returns_approval_r3.py`
- Modify: `migrations/checksums.json`
- Modify: `fselling/models/fnb.py`
- Modify: `fselling/models/order.py`
- Create: `tests/test_migration_0013_roles_returns_approval_r3.py`
- Modify: `tests/test_migration_0004_offline.py`
- Modify: `tests/test_migration_0005_offline_issue_lifecycle.py`
- Modify: `tests/test_migration_0006_offline_receipt_items.py`
- Modify: `tests/test_migration_0007_qr_payment_domain.py`
- Modify: `tests/test_migration_0009_fnb_r1a.py`
- Modify: `tests/test_migration_0010_fnb_r1b.py`
- Modify: `tests/test_migration_0011_fnb_r1c.py`
- Modify: `tests/test_migration_0012_fnb_ticket_service_handoff.py`
- Modify: `tests/test_migration_i04.py`
- Modify: `tests/test_migration_i05.py`
- Modify: `tests/test_offline_lease_identity.py`

### Step 1: Write migration tests first

`tests/test_migration_0013_roles_returns_approval_r3.py` must prove:

1. Upgrade from `0012` adds nullable `fnb_manager_approvals.context_fingerprint VARCHAR(64)` and nullable `order_returns.manager_approval_id INTEGER REFERENCES fnb_manager_approvals(id)`.
2. Existing approval and return rows survive unchanged with null new fields.
3. `verify()` rejects a non-null fingerprint not exactly 64 lowercase hex characters.
4. `verify()` rejects an orphan `manager_approval_id` and a linked approval whose shop/order/action/entity do not match the return.
5. A fault after the first DDL rolls back schema and version atomically.
6. `downgrade()` raises `FORWARD_ONLY_MIGRATION`.

Do not invent approvers for legacy returns.

### Step 2: Run RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_migration_0013_roles_returns_approval_r3.py
```

### Step 3: Implement the additive migration

The revision header is fixed:

```python
revision = "0013_roles_returns_approval_r3"
down_revision = "0012_fnb_ticket_service_handoff"
```

Before DDL, inspect `PRAGMA table_info` and fail closed if either legacy table or required key columns are absent. Add only the two columns from the spec. `verify()` must enforce shape and relational consistency for non-null links; existing null rows are valid. Use the same `_execute()` fault hook and forward-only downgrade pattern as `0012`.

Update ORM fields:

```python
# fnb.py — legacy table name retained for compatibility
context_fingerprint = Column(String(64), nullable=True)

# order.py
manager_approval_id = Column(
    Integer, ForeignKey("fnb_manager_approvals.id"), nullable=True
)
manager_approval = relationship("FnbManagerApproval")
```

### Step 4: Update the linear-head fixtures mechanically

Define `PLAN3 = "0013_roles_returns_approval_r3"` in each listed migration test that currently treats `PLAN2` as head. Append `PLAN3` only to paths that mean “upgrade to current head”; keep tests that deliberately target `PLAN2` by ID unchanged. In fixture-pruning tests, remove both `0012` and `0013` plus both manifest entries when simulating an old binary.

After the migration content is final, calculate its SHA-256 with the same bytes policy used by the coordinator and add one manifest entry. Never hand-type a guessed checksum.

### Step 5: Run GREEN and migration regressions

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_migration_0013_roles_returns_approval_r3.py `
  tests/test_migration_0012_fnb_ticket_service_handoff.py `
  tests/test_migration_i04.py tests/test_migration_i05.py `
  tests/test_offline_lease_identity.py
.\.venv\Scripts\python.exe -B -m fselling.migration.cli verify
git diff --check -- migrations fselling/models tests/test_migration_0013_roles_returns_approval_r3.py
```

Expected: current revision/head is `0013_roles_returns_approval_r3`; existing rows remain nullable and readable.

---

## Task 3: Extract the shared PIN/token primitives without breaking F&B

**Files:**

- Create: `fselling/services/approval_service.py`
- Modify: `fselling/services/fnb_service.py`
- Modify: `fselling/services/shop_service.py`
- Modify: `fselling/routers/shops.py`
- Modify: `fselling/routers/fnb.py`
- Modify: `fselling/schemas/shop.py`
- Modify: `fselling/schemas/fnb.py`
- Create: `tests/test_manager_approval.py`
- Modify: `tests/test_fnb_r1b_cancel.py`
- Modify: `tests/test_contract.py`

### Step 1: Freeze the F&B compatibility contract in tests

Add focused tests around the current F&B routes before refactoring:

- `PATCH /api/fnb/shops/{shop_id}/manager-pin` keeps the same request, success response and F&B error codes.
- `POST /api/fnb/manager-approvals` keeps action/entity/revision binding.
- wrong username and wrong PIN both perform one password-hash verification path and return `FNB_PIN_INVALID`.
- fifth failed attempt within 15 minutes triggers `FNB_PIN_RATE_LIMITED` with retry metadata.
- successful token is stored only as SHA-256, expires in 300 seconds and is one-use.
- rollback in F&B cancellation restores `used_at=None`.

Use monkeypatch/counters around `verify_password` and `burn_password_time`; do not use wall-clock timing as a flaky security assertion.

### Step 2: Add generic PIN route tests

In `tests/test_manager_approval.py`, cover:

```text
PATCH /api/shops/{shop_id}/manager-pin
body: {"pin": "1234"}
success: {"shop_id": <id>, "manager_pin_configured": true}
```

Only the shop owner or active same-shop manager with `RETURN_APPROVE` may set their own PIN. Cashier/service/cross-shop calls must be non-enumerating 403/404 according to existing shop-access conventions. Never return the hash.

### Step 3: Run RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_manager_approval.py tests/test_fnb_r1b_cancel.py -k 'pin or approval'
```

### Step 4: Create one small shared service

`fselling/services/approval_service.py` owns only these concrete primitives:

```python
def set_manager_pin(db, current_user, shop_id: int, pin: str) -> dict:
    """Authorize owner/manager, hash the PIN, and commit no other state."""

def issue_pin_approval(
    db, *, shop, actor, approver_username: str, pin: str,
    action: str, entity_type: str, entity_id: int, revision: int,
    context_fingerprint: str | None = None,
) -> tuple[str, models.FnbManagerApproval]:
    """Verify/rate-limit the approver and add one five-minute hashed token."""

def consume_approval(
    db, *, token: str, shop_id: int, actor_user_id: int,
    action: str, entity_type: str, entity_id: int, revision: int,
    context_fingerprint: str | None = None,
) -> models.FnbManagerApproval:
    """Lock and mark one exactly-bound approval used without committing."""
```

Use existing `hash_password`, `verify_password`, `burn_password_time`, `secrets.token_urlsafe(32)`, SHA-256, five attempts/15 minutes and five-minute expiry. Approval lookup is one query scoped by every supplied binding and `used_at/expires_at`. It sets `used_at` but never commits.

The service may raise neutral structured codes `APPROVAL_PIN_INVALID`, `APPROVAL_RATE_LIMITED`, `APPROVAL_INVALID` and `MANAGER_PIN_REQUIRED`. Keep the mapping to legacy `FNB_*` codes in thin `fnb_service` adapters. Do not create an interface, repository class, policy registry or configurable expiry.

Move `FnbManagerPinSet` to `fselling/schemas/shop.py` as `ManagerPinSet`; import that schema in both shop and F&B routers so the old JSON contract is identical. The new generic route calls `shop_service.set_manager_pin()`, which delegates to the shared service after existing shop access checks.

Replace the bodies of `fnb_service.set_manager_pin`, `create_manager_approval` and `_approval_for_sent_cancel` with adapters over the shared functions. Existing F&B session-revision validation remains in `fnb_service` before token issuance.

### Step 5: Run GREEN and full F&B approval regression

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_manager_approval.py tests/test_fnb_r1b_cancel.py tests/test_contract.py
git diff --check -- fselling/services/approval_service.py fselling/services/fnb_service.py `
  fselling/services/shop_service.py fselling/routers/shops.py fselling/routers/fnb.py `
  fselling/schemas/shop.py fselling/schemas/fnb.py tests/test_manager_approval.py `
  tests/test_fnb_r1b_cancel.py tests/test_contract.py
```

Checkpoint: F&B behavior must be byte-for-byte compatible at the HTTP contract where tests freeze it.

---

## Task 4: Build one server-authoritative return preview and approval context

**Files:**

- Modify: `fselling/schemas/order.py`
- Modify: `fselling/services/return_service.py`
- Modify: `fselling/routers/orders.py`
- Create: `tests/test_return_approval_r3.py`
- Modify: `tests/test_tra_hang.py`
- Modify: `tests/test_contract.py`

### Step 1: Define the request schemas with no unsafe defaults

Refactor only the shared fields; do not duplicate the draft contract:

```python
class OrderReturnItemCreate(BaseModel):
    order_item_id: int
    quantity: int
    restock: bool


class OrderReturnDraft(BaseModel):
    items: List[OrderReturnItemCreate]
    method: Optional[Literal["cash", "transfer"]] = None
    # Kept nullable at schema boundary so service can preserve the stable
    # HTTP 400 RETURN_REASON_REQUIRED contract instead of FastAPI's 422.
    reason: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=128)
    operation_id: str = Field(min_length=8, max_length=128)


class OrderReturnCreate(OrderReturnDraft):
    approval_token: Optional[str] = Field(default=None, min_length=32, max_length=256)


class OrderReturnApprovalCreate(OrderReturnDraft):
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approver_username: str = Field(min_length=1, max_length=100)
    pin: str = Field(pattern=r"^\d{4,6}$")
```

In `_kiem_yeu_cau()`, reject missing/blank-after-trim reason with structured HTTP 400. This deliberately keeps the field nullable only at the Pydantic boundary; it is mandatory before any lock/mutation. Do not globally alter validation handling:

```python
reason = (request.reason or "").strip()
if not reason:
    raise HTTPException(
        status_code=400,
        detail={
            "code": "RETURN_REASON_REQUIRED",
            "message": "Phải nhập lý do trả hàng",
        },
    )
```

### Step 2: Write RED tests for classification and preview

In `tests/test_return_approval_r3.py`, create table-driven cases:

| Source evidence | Requested refund | Result |
|---|---|---|
| order `cash`, no contradictory incoming entry | cash | ordinary |
| order `transfer`, bank-family only | transfer + reference | ordinary |
| order `cash` plus bank-family entry | either | exceptional `AMBIGUOUS_SOURCE` |
| order `transfer` plus cash-family entry | either | exceptional `AMBIGUOUS_SOURCE` |
| debt-origin order, even if later settled one way | either | exceptional `DEBT_SOURCE` |
| unrecognized source/entry combination | either | exceptional `AMBIGUOUS_SOURCE` |
| positive transfer refund without reference | transfer | `400 RETURN_REFERENCE_REQUIRED` |
| any selected `restock=false` | source method | exceptional `NON_RESTOCK` |
| unambiguous source but method changed | other method | exceptional `METHOD_CHANGE` |
| zero refund, all restock | method omitted | ordinary unless source itself is exceptional |

Incoming cash family is `CASH_TOPUP`/`DEBT_CASH`; incoming transfer family is `BANK_IN`/`DEBT_TRANSFER`. Ignore outgoing `REFUND_*` and `RETURN_*` when classifying the original funding source. Unknown positive incoming entry types fail closed as ambiguous.

Assert the approval fingerprint changes when any of these changes: actor, shop, order, operation ID, line/quantity/restock, normalized method/reason/reference, source class, order status, selected line cumulative return/refund/cost counters, or server refund amount. Sorting item IDs must make equivalent item ordering stable.

### Step 3: Run RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_return_approval_r3.py -k 'schema or source or context or reference'
```

### Step 4: Extract the pre-mutation helper

Inside `return_service.py`, reuse the current validation/calculation code in one helper; do not reimplement return math in the approval endpoint:

```python
def _prepare_return_context(
    db: Session,
    current_user: models.User,
    order: models.Order,
    request: OrderReturnDraft,
) -> Dict[str, Any]:
    """Read and validate the locked order; perform no durable mutation."""
```

It returns the existing `dong_don`, `da_tra`, `chi_tiet`, `tien_hoan` plus:

```python
{
    "source_class": "cash-only" | "transfer-only" | "debt" | "ambiguous",
    "reason_codes": tuple(sorted(reason_codes)),
    "non_restock_item_count": int,
    "context_fingerprint": sha256(canonical_json).hexdigest(),
}
```

Canonical JSON uses `ensure_ascii=False`, `sort_keys=True`, compact separators, trimmed values and sorted item IDs. Include cumulative fields already held on each selected `OrderItem`: `returned_total_qty`, `returned_known_qty`, `returned_unknown_qty`, `returned_cost_basis_vnd`, `returned_refund_vnd`, and `cost_return_version`.

The helper runs only after `order_service._lock_shop_for_order()` and `db.refresh(order)`. It must not add, update, flush or commit anything.

### Step 5: Add the return-specific approval endpoint

Add exactly:

```python
@router.post("/{order_id}/returns/approval")
def approve_return(
    order_id: int,
    request: OrderReturnApprovalCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return return_service.create_return_approval(
        db, current_user, order_id, request
    )
```

`create_return_approval()` must:

1. Find the order without leaking cross-shop existence; require shop access and `ORDER_RETURN`.
2. Acquire the same shop lock as final return.
3. Recompute `_prepare_return_context()` while holding the shop lock; keep that transaction through approval insertion so the token cannot be minted for a context that changed between preview and insert.
4. Return `409 RETURN_CONTEXT_CHANGED` if submitted and computed fingerprints differ.
5. Return 400 if the current request is no longer exceptional; the cashier should submit it directly, not mint an unnecessary token.
6. Call `approval_service.issue_pin_approval()` with the exact Retail binding.
7. Commit only the approval row and return `{approval_token, expires_in_seconds: 300, approval_context}`. Never echo PIN.

The safe `approval_context` is fixed to order ID, refund VND, method, non-restock count, sorted reason codes and fingerprint. It contains no customer PII.

### Step 6: Run GREEN and ensure no mutation during preview

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_return_approval_r3.py -k 'schema or source or context or reference or endpoint' `
  tests/test_contract.py
git diff --check -- fselling/schemas/order.py fselling/services/return_service.py `
  fselling/routers/orders.py tests/test_return_approval_r3.py tests/test_contract.py
```

For every 400/403/409 test, snapshot counts and values for returns, payments, shifts, products, batches, order-line counters, loyalty, approvals and logs; they must be unchanged.

---

## Task 5: Enforce approval atomically in final return creation

**Files:**

- Modify: `fselling/services/return_service.py`
- Modify: `fselling/models/order.py`
- Modify: `tests/test_return_approval_r3.py`
- Modify: `tests/test_tra_hang.py`
- Modify: `tests/test_nhat_ky_shop.py`

### Step 1: Write the failure matrix before implementation

Add tests for missing, malformed, expired, used, cross-actor, cross-shop, cross-order, wrong-action and fingerprint-mismatched tokens. Every case returns a non-enumerating `403 RETURN_APPROVAL_INVALID` and no side effect.

Add context-race tests:

- approval is issued, then another return changes a selected line counter;
- approval is issued, then order/payment source evidence changes;
- approval is issued, then the draft changes while operation ID stays the same.

These return `409 RETURN_CONTEXT_CHANGED`, do not consume the token, and require a fresh preview.

Add direct manager/owner tests: the owning seller or an active same-shop STAFF manager with `RETURN_APPROVE` may perform the exceptional return without entering their own PIN, but the transaction creates a used `ORDER_RETURN_EXCEPTION` approval evidence row with approver=actor and links it from `OrderReturn.manager_approval_id`. An ADMIN who is not the shop owner does not gain this direct-approval shortcut merely because STAFF presets do not limit admins.

### Step 2: Write atomicity and retry tests

Inject failures at these existing mutation boundaries: after approval selection, return flush, loyalty entry, first batch/stock update, payment add, audit add and immediately before commit. For each, assert the entire before-snapshot is restored, including `approval.used_at`.

Then cover:

- post-commit lost response + same operation ID/same draft returns the same durable return;
- retry does not require or consume a second token;
- same operation ID with changed draft keeps the existing 409 idempotency conflict;
- two concurrent requests cannot both consume one approval or return the same remaining unit.

### Step 3: Run RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_return_approval_r3.py -k 'token or manager or rollback or retry or concurrent'
```

### Step 4: Integrate the policy at the last safe point before mutation

The final order is fixed:

```text
authenticate/authorize → validate request → idempotency replay → shop lock →
repeat replay check → refresh order → prepare server context → cash-shift lock →
approval/direct-manager evidence → all existing return side effects → one commit
```

If `reason_codes` is empty, ignore any supplied token and create no approval link. If exceptional:

- owner/authorized manager: create an already-used evidence row with random token hash, exact context fingerprint and approver=actor; never expose a token;
- other `ORDER_RETURN` actor with no token: roll back and raise `RETURN_APPROVAL_REQUIRED` with safe server summary;
- actor with a token: recompute context and call `approval_service.consume_approval()`; translate absence/mismatch/expiry/use to `RETURN_APPROVAL_INVALID` and context drift to `RETURN_CONTEXT_CHANGED`.

Set `phieu.manager_approval_id = approval.id` only after flushing the approval evidence. Do not commit until the existing return flow is complete.

Extend `_serialize_return()` with nullable fields only:

```python
"manager_approval_id": ban_ghi.manager_approval_id,
"approved_by_user_id": (
    ban_ghi.manager_approval.approver_user_id
    if ban_ghi.manager_approval is not None else None
),
```

Extend the `ORDER_RETURN` log description with method, non-restock count, trimmed reason, actor and approver IDs/usernames. Keep relational rows authoritative and never include token/PIN.

### Step 5: Run GREEN and financial/inventory regressions

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_return_approval_r3.py tests/test_tra_hang.py tests/test_nhat_ky_shop.py `
  tests/test_fnb_r1b_cancel.py
git diff --check -- fselling/services/return_service.py fselling/models/order.py `
  tests/test_return_approval_r3.py tests/test_tra_hang.py tests/test_nhat_ky_shop.py
```

Checkpoint: reviewers must trace one cash ordinary path, one transfer ordinary path, one approved non-restock path and every rollback boundary before frontend work begins.

---

## Task 6: Implement the cashier return form and recovery controller

**Files:**

- Modify: `static/pos.html`
- Modify: `static/js/pos.js`
- Modify: `static/css/pos.css`
- Modify: `static/js/locales/pos.js`
- Create: `tests/js/pos-return-r3.test.js`
- Create: `tests/test_pos_return_r3_ui.py`

### Step 1: Lock the DOM and controller contract with failing tests

The Python static test must prove:

- the checked restock checkbox is absent;
- each selected line exposes two radio choices with neither preselected;
- reason is required and transfer reference is conditionally required;
- the approval dialog has order/actor/amount/method/non-restock/reason/reason-code summary, username and password/PIN inputs;
- PIN uses `type="password"`, appropriate autocomplete suppression and no URL/localStorage binding;
- status regions use `role="status"`/`aria-live`; field errors connect with `aria-describedby`.

The Node harness loads only the return controller dependencies and tests payload/state transitions without a browser framework.

### Step 2: Run RED

```powershell
node tests/js/pos-return-r3.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_pos_return_r3_ui.py
```

### Step 3: Replace the unsafe checkbox with explicit condition choices

For a selected quantity, render:

```html
<fieldset class="return-condition" data-order-item-id="42">
  <legend>Tình trạng hàng</legend>
  <label><input type="radio" name="return-condition-42" value="restock">Bán lại được — nhập lại kho</label>
  <label><input type="radio" name="return-condition-42" value="discard">Hỏng/không đủ điều kiện — không nhập kho</label>
</fieldset>
```

Neither radio is checked by default. Do not submit until every positive-quantity line has one choice. Map `restock → true`, `discard → false` explicitly.

The visible summary shows the client estimate, method, cash-shift effect, count/quantity returning to stock, loyalty note and “có thể cần quản lý duyệt”; after a server 403 it replaces estimates with the trusted `approval_context`.

### Step 4: Implement one pending envelope and exact recovery

Keep one in-memory envelope only:

```javascript
{
  operationId,
  draft,
  approvalToken: null,
  state: 'editing' | 'submitting' | 'approval-required' | 'approving' | 'unknown'
}
```

Rules:

- generate `operationId` once when submission begins; network retry reuses it;
- editing item/condition/method/reason/reference discards `approvalToken`;
- `RETURN_APPROVAL_REQUIRED` opens the dialog with server summary;
- correct PIN response retries the unchanged draft with the same operation ID and returned token;
- wrong PIN clears only PIN, retaining draft, username and summary;
- `RETURN_CONTEXT_CHANGED` closes the dialog, clears token, reloads the order and requires review;
- response loss/timeout shows “Chưa biết phiếu đã được ghi hay chưa” and offers retry with the same operation ID;
- success renders durable return ID/effects and clears the envelope;
- no return is queued in the offline receipt system.

Do not introduce a generic state machine class, persistence layer or new dependency.

### Step 5: Add bilingual copy and bump only affected asset versions

Add Vietnamese and English keys for condition choices, required fields, approval states, role handoff, unknown result and safe recovery. Bump the `pos.css`, `locales/pos.js` and `pos.js` query versions in `static/pos.html` together. Update static tests to the new exact version; do not touch unrelated asset versions.

### Step 6: Run GREEN

```powershell
node tests/js/pos-return-r3.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_pos_return_r3_ui.py tests/test_tra_hang.py tests/test_return_approval_r3.py
git diff --check -- static/pos.html static/js/pos.js static/css/pos.css `
  static/js/locales/pos.js tests/js/pos-return-r3.test.js tests/test_pos_return_r3_ui.py
```

---

## Task 7: Finish role-aware navigation, shared PIN settings and evidence

**Files:**

- Modify: `static/js/auth.js`
- Modify: `static/js/fnb-r1a.js`
- Modify: `static/fnb.html`
- Modify: `static/js/locales/fnb.js`
- Modify: `static/js/seller.js`
- Modify: `static/seller.html`
- Modify: `static/css/seller.css`
- Modify: `static/js/locales/seller.js`
- Modify: `tests/js/auth-offline-seal.test.js`
- Modify: `tests/js/fnb-r1a.test.js`
- Modify: `tests/test_fnb_r1a_ui.py`
- Modify: `tests/test_seller_sidebar_ui.py`
- Modify: `tests/test_staff_roles.py`
- Create: `FNB_PLAN3_UAT_REPORT.md`

### Step 1: Write role/device tests first

Extend the existing JS/static harnesses to prove:

- `SERVICE` login routes to `/fnb`.
- kitchen/bar still route to their fixed stations; cashier still routes to `/pos`; warehouse behavior is unchanged.
- `SERVICE` may reach table/menu/send/transfer/cancel-request/ready-to-serve controls.
- payment, return, F&B/shop/station setup and manager PIN controls are absent for `SERVICE`.
- cashier retains the F&B checkout route.
- Seller staff create/edit lists `SERVICE`, preserves all existing roles and shows task-based descriptions.
- the generic Seller manager-PIN form calls `/shops/{shop_id}/manager-pin`; the existing F&B form continues calling its legacy alias.

### Step 2: Run RED

```powershell
node tests/js/auth-offline-seal.test.js
node tests/js/fnb-r1a.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_ui.py tests/test_seller_sidebar_ui.py tests/test_staff_roles.py
```

### Step 3: Apply the smallest role-aware UI change

In `auth.js`, add one `SERVICE → /fnb` branch before the generic STAFF fallback. In `fnb-r1a.js`, derive capabilities from the same explicit role matrix already present; add no backend-like permission engine. Hide financial/setup controls for `SERVICE` and give a short localized handoff message when the next action requires cashier/manager.

In Seller:

- add `SERVICE` to both staff-role selects;
- update `ROLE_PERMISSIONS` and allowed-tab mirrors without granting Seller admin tabs to `SERVICE`;
- add concise descriptions for service/cashier/warehouse/kitchen/bar/manager;
- expose the generic manager PIN setting only to owner/manager UI;
- keep the old F&B PIN control operational for compatibility.

Before editing the five Seller files, record their Plan 3 starting hashes in the UAT report. Before later integration, compare those files against the primary checkout and reconcile; never overwrite the primary checkout wholesale.

### Step 4: Run all focused automated gates

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_staff_roles.py tests/test_manager_approval.py `
  tests/test_return_approval_r3.py tests/test_tra_hang.py tests/test_nhat_ky_shop.py `
  tests/test_fnb_r1b_cancel.py tests/test_contract.py `
  tests/test_pos_return_r3_ui.py tests/test_fnb_r1a_ui.py `
  tests/test_seller_sidebar_ui.py `
  tests/test_migration_0013_roles_returns_approval_r3.py `
  tests/test_migration_0012_fnb_ticket_service_handoff.py `
  tests/test_migration_i04.py tests/test_migration_i05.py `
  tests/test_offline_lease_identity.py
node tests/js/auth-offline-seal.test.js
node tests/js/fnb-r1a.test.js
node tests/js/pos-return-r3.test.js
.\.venv\Scripts\python.exe -m compileall -q fselling
git diff --check
```

Record exact commands, counts, exit codes and limitations. Focused green is not full-suite evidence.

### Step 5: Run local browser UAT with demo-only data

Use a copied/migrated local demo DB only. Run `migration.cli upgrade head` and `verify` against that copy before starting the app. Never use `.env`, ngrok, real providers, secrets or production data.

Record viewport, role, operation ID, return/approval IDs and before/after evidence for:

1. `SERVICE`, 390×844: login routes to `/fnb`; table/menu/send/transfer/cancel request/READY are usable one-handed; payment/return/setup/PIN absent; role handoff is understandable.
2. `CASHIER`, 1024×768: ordinary cash return from lookup to success; correct shift balance decreases once and stock increases once.
3. Desktop cashier: transfer return requires reference and leaves cash shift unchanged.
4. Damaged/non-restock exception: wrong PIN preserves draft and clears PIN; correct PIN creates one return, consumes one approval and does not restock.
5. Method-change exception and debt/mixed source exception: manager summary explains the reason code.
6. Owner changes `SERVICE → CASHIER`: old session becomes 401; fresh login routes to POS and gains only cashier preset.
7. Delayed/lost response: UI shows unknown result and retry with the same operation ID resolves to the same return.
8. Owner/manager activity view traces source order, actor, approver, method, amount, reason and non-restock count.

If network delay/response loss cannot be induced and observed in the browser, mark it `TEST_GAP`; do not promote automated retry tests to browser evidence. Mark payment provider, deployment and real migration behavior `UNKNOWN-PROD`.

### Step 6: Owner-run full-suite gate and one implementation commit

Stop and hand the owner exactly:

```powershell
cd C:\Users\nguye\.codex\worktrees\7f9e\python_app
.\test-commit.ps1 -TestOnly
$testExit = $LASTEXITCODE
Write-Host "PYTEST_EXIT_CODE=$testExit"
```

Proceed only after the owner returns exit code 0 from the current run. Do not reuse an older pass.

Then inspect `git status --short`, stage only files named by this plan plus `FNB_PLAN3_UAT_REPORT.md`, review `git diff --cached --stat` and `git diff --cached --check`, and create one commit:

```powershell
git commit -m "feat: enforce roles and return approvals R3"
```

Do not push, merge, rebase or open a PR unless the owner separately asks.

### Step 7: Independent Astra High review gate

Create one separate task on the same project/branch with this bounded objective:

```text
Review the committed Plan 3 diff against
docs/superpowers/specs/2026-09-08-roles-returns-approval-r3-design.md and
docs/superpowers/plans/2026-09-08-roles-returns-approval-r3.md.
Inspect money, inventory, authorization, approval binding/atomicity, migration,
idempotency, F&B compatibility and browser-evidence claims. Do not edit files or
run the full suite. You may run focused read-only tests when needed. Return only
ACCEPT or CHANGES REQUIRED, then file:line findings classified CONFIRMED,
TEST_GAP or UNKNOWN-PROD.
```

Use Astra High for this one review only. If it reports `CHANGES REQUIRED`, return the branch to the Sol implementation task for a focused correction, rerun only affected focused tests, update UAT evidence, and request one final Astra re-review. Do not open parallel review loops.

---

## Final Self-Review Checklist

Before claiming Plan 3 complete, the implementation owner must verify every item:

- [ ] `SERVICE` is accepted, routed to `/fnb`, and lacks sale/checkout/return/manage permissions at the API boundary.
- [ ] Cashier has `ORDER_RETURN`; manager has `ORDER_RETURN` and `RETURN_APPROVE`; null legacy role still maps to manager.
- [ ] `restock` has no default and every return reason is non-blank after trim.
- [ ] Transfer refund with positive value requires a reference.
- [ ] Source classification is conservative and ignores outgoing refund entries.
- [ ] Approval context includes actor/shop/order/operation/draft/source/status/cumulative counters/server amount.
- [ ] Missing/invalid/context-changed approval errors are stable and side-effect free.
- [ ] Direct manager exception creates durable used approval evidence and links the return.
- [ ] Token use and all return effects roll back together.
- [ ] Same operation ID retry returns one durable result; changed material request conflicts.
- [ ] F&B PIN/approval endpoints, codes, rate limit, expiry and cancellation remain compatible.
- [ ] Migration `0013` is additive, checksummed, verified, forward-only and preserves legacy null rows.
- [ ] PIN/raw tokens are absent from logs, URLs, storage, drafts and returned history.
- [ ] Seller-file starting hashes and reconciliation warning are recorded.
- [ ] Browser observations are separated from automated coverage and production unknowns.
- [ ] Owner-run current full suite exited 0 once; independent Astra review returned `ACCEPT`.

## Deliberately Skipped

- Configurable thresholds/policy engine: add only after real operations justify more than the fixed R3 exceptions.
- Remote approval inbox or cross-device push: revisit with Plan 4 session/device design.
- Offline return queue: keep fail-closed until money/stock conflict resolution has a separate design.
- Broad POS/F&B visual redesign: Plan 5 owns device-specific usability beyond these safety controls.
