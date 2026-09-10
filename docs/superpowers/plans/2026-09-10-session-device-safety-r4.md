# Plan 4 Session and Device Safety R4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not create subagents for this plan.

**Goal:** Thay cơ chế một `users.session_id` bằng registry phiên bền vững, cho phép đa thiết bị nhưng vẫn chặn phiên bị thu hồi trước mọi mutation tiền, kho, đơn, ca, approval và F&B.

**Architecture:** JWT bearer hiện hữu tiếp tục mang `sid`; `auth_sessions` trở thành nguồn xác thực và audit. Dependency bind phiên đã xác thực vào `Session.info`, còn một write-fence nhỏ được gọi tại các điểm domain lock dùng chung để sắp thứ tự transaction giữa revoke và mutation. UI dùng một module native `<dialog>` dùng chung, không thêm dependency hay redesign các màn hình.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, Pydantic, vanilla JavaScript/CSS, Node built-in test runner, pytest, migration framework nội bộ.

**Spec:** `docs/superpowers/specs/2026-09-10-session-device-safety-r4-design.md`

## Global Constraints

- Baseline bắt buộc: branch `codex/roles-returns-approval-plan3`, commit `1acf0977d1a6431d88de0ffc590904af0b0f47f7`.
- Đọc spec đầy đủ trước Task 1; nếu baseline không còn đúng commit thì dừng và báo owner.
- Chỉ dùng DB giả/copy riêng; không đọc hoặc sửa dữ liệu thật.
- Không gọi provider thật, secret thật, ngrok hoặc dịch vụ ngoài.
- Không thêm dependency, WebSocket, refresh token, OAuth, passkey, policy engine, remote/cross-device approval hoặc broad UI redesign.
- Không thay thuật toán tiền, kho, voucher, loyalty, batch/cost, revision, operation ID hoặc idempotency Plan 1–3.
- Auth session, offline lease, cash shift, F&B service session và approval token tiếp tục là các loại state riêng.
- Mỗi `user_id + device_id` có tối đa một auth session chưa thu hồi; device metadata không cấp quyền.
- Không log JWT, raw offline lease token, PIN, password, OTP, full user-agent hoặc full device ID.
- Không chạy full suite giữa các task. Chỉ chạy focused tests ghi trong task và `git diff --check`.
- Không commit tay giữa các task. Sau independent safety review và owner kiểm tra danh sách file, chỉ chạy full suite/commit đúng một lần bằng `test-commit.ps1`.
- Không push, merge, rebase hoặc deploy trong plan này.

## File Map

| Trách nhiệm | File |
|---|---|
| ORM registry và liên kết audit | Tạo `fselling/models/auth_session.py`; sửa `fselling/models/__init__.py`, `system_log.py`, `offline.py`, `fnb.py`, `user.py` |
| Migration/backfill/verifier | Tạo `migrations/versions/0014_session_device_safety_r4.py`; sửa `migrations/checksums.json`; tạo `tests/test_migration_0014_session_device_safety_r4.py` |
| Contract HTTP | Sửa `fselling/schemas/auth.py`, `fselling/routers/auth.py`, `fselling/routers/staff.py` |
| Registry, revoke và request binding | Tạo `fselling/services/auth_session_service.py`; sửa `fselling/services/auth_service.py`, `fselling/dependencies.py`, `fselling/services/staff_service.py`, `fselling/services/shop_service.py` |
| Offline device revoke | Sửa `fselling/services/offline_lease_service.py`; auth/staff routes gọi service revoke dùng chung |
| Approval binding | Sửa `fselling/services/approval_service.py`, `fselling/services/return_service.py`, `fselling/services/fnb_service.py`, `fselling/services/shift_service.py` |
| Mutation write-fence | Sửa các lock dùng chung trong `order_service.py`, `inventory_service.py`, `shift_service.py`, `fnb_service.py`; chỉ thêm lời gọi trực tiếp ở mutation không đi qua các lock này |
| UI dùng chung | Tạo `static/js/session-device-r4.js`, `static/css/session-device-r4.css`; sửa `static/js/api.js`, `auth.js`, `seller.js`, locale và năm protected HTML page |
| Evidence | Tạo `SESSION_DEVICE_R4_UAT_REPORT.md` và focused Python/JS tests nêu trong từng task |

---

### Task 1: Registry ORM, migration additive và backfill token legacy

**Files:**

- Create: `fselling/models/auth_session.py`
- Modify: `fselling/models/__init__.py`
- Modify: `fselling/models/user.py`
- Modify: `fselling/models/system_log.py`
- Modify: `fselling/models/offline.py`
- Modify: `fselling/models/fnb.py`
- Create: `migrations/versions/0014_session_device_safety_r4.py`
- Modify: `migrations/checksums.json`
- Create: `tests/test_migration_0014_session_device_safety_r4.py`

**Interfaces:**

- Produces: `models.AuthSession` with `session_id: str`, `user_id: int`, `device_id: str`, `device_name: str`, `device_type: str`, UTC timestamps, nullable revoke fields.
- Produces: nullable `auth_session_id` on `SystemLog` and `FnbActionLog`; nullable `issued_by_auth_session_id` on `OfflineLease`; nullable `actor_auth_session_id` on `FnbManagerApproval`.
- Preserves: `User.session_id` as deprecated storage for one release; no new code after Task 2 may read or write it.

- [ ] **Step 1: Write migration tests that describe the complete schema contract**

Use the `MigrationCoordinator`, `StaticInventory` and temporary-database pattern from `tests/test_migration_0013_roles_returns_approval_r3.py`. Add explicit assertions equivalent to:

```python
assert coordinator.current_revision(conn) == "0014_session_device_safety_r4"
assert {
    "session_id", "user_id", "device_id", "device_name", "device_type",
    "created_at", "last_seen_at", "expires_at", "revoked_at",
    "revoked_by_user_id", "revoke_reason",
} <= column_names(conn, "auth_sessions")
assert has_partial_unique_index(
    conn, "auth_sessions", ("user_id", "device_id"), "revoked_at IS NULL"
)
assert "auth_session_id" in column_names(conn, "system_logs")
assert "auth_session_id" in column_names(conn, "fnb_action_logs")
assert "issued_by_auth_session_id" in column_names(conn, "offline_leases")
assert "actor_auth_session_id" in column_names(conn, "fnb_manager_approvals")
```

Implement the test helper with SQLite PRAGMA rather than a migration-only dependency:

```python
def has_partial_unique_index(conn, table, columns, where_sql):
    for row in conn.execute(f"PRAGMA index_list({table})"):
        if not row[2] or not row[4]:
            continue
        index_name = row[1]
        actual_columns = tuple(
            item[2] for item in conn.execute(f"PRAGMA index_info({index_name})")
        )
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()[0]
        if actual_columns == columns and where_sql.upper() in ddl.upper():
            return True
    return False
```

Cover four databases: fresh install, a Plan 3 DB at revision `0013`, a legacy user with a non-null `session_id`, and a fault-injected migration whose transaction must roll back. Assert the legacy row becomes `device_id = 'legacy:' + session_id`, `device_type = 'UNKNOWN'`, expires no later than migration time plus 24 hours, and existing order/shift/offline/F&B rows are unchanged.

- [ ] **Step 2: Run the migration test and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_migration_0014_session_device_safety_r4.py
```

Expected: collection or assertions fail because revision `0014_session_device_safety_r4` and `AuthSession` do not exist.

- [ ] **Step 3: Add the minimal ORM model and nullable relationships**

Define constants in `auth_session.py` without a new enum table:

```python
AUTH_DEVICE_TYPES = frozenset({"DESKTOP", "TABLET", "MOBILE", "KDS", "UNKNOWN"})

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    session_id = Column(String(128), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(128), nullable=False)
    device_name = Column(String(80), nullable=False)
    device_type = Column(String(16), nullable=False, default="UNKNOWN")
    created_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(String(48), nullable=True)
```

Add an ordinary lookup index and a SQLite partial unique index for active `user_id + device_id`. Export `AuthSession` from `models/__init__.py`. Add only the four nullable FK columns required by the spec; do not create a `devices` table or copy auth fields into business records.

- [ ] **Step 4: Implement migration, backfill and verifier**

Follow revision `0013` structure exactly: additive DDL, table rebuild only where SQLite requires the new FK, atomic transaction, forward-only downgrade. Backfill at most one row per non-null legacy `users.session_id`; use one captured UTC migration timestamp for all three legacy timestamps and `+ timedelta(hours=24)` for expiry. Reject rows where `last_seen_at < created_at`, `expires_at <= created_at`, active device pairs are duplicated, required FK/indexes are absent, or a new approval row names an unknown auth session.

Regenerate only the `0014` SHA-256 entry in `migrations/checksums.json` using the repository migration checksum convention; do not rewrite older entries.

- [ ] **Step 5: Run focused migration verification GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_migration_0014_session_device_safety_r4.py tests/test_migration_0013_roles_returns_approval_r3.py
python -m migration.cli verify
git diff --check
```

Expected: both migration test modules pass; verifier reports revision `0014_session_device_safety_r4`; diff check is clean.

### Task 2: Session registry primitives, login và request authentication

**Files:**

- Create: `fselling/services/auth_session_service.py`
- Modify: `fselling/core/security.py`
- Modify: `fselling/schemas/auth.py`
- Modify: `fselling/services/auth_service.py`
- Modify: `fselling/dependencies.py`
- Modify: `fselling/routers/auth.py`
- Create: `tests/test_auth_sessions_r4.py`
- Modify: `tests/test_auth.py`

**Interfaces:**

- Produces: `create_session(db: Session, user: User, device_id: str, device_name: str, device_type: str, now: datetime | None = None) -> tuple[AuthSession, str]`.
- Produces: `require_live_session(db: Session, user_id: int, session_id: str, *, touch: bool = False) -> AuthSession`.
- Produces: `bind_request_session(db: Session, auth_session: AuthSession) -> None`, storing `auth_session_id`, `auth_user_id` and `auth_session_required=True` in `db.info`.
- Produces: `fence_live_auth_session(db: Session) -> None`; it is a no-op only for direct internal service calls whose DB session has no `auth_session_required` marker.
- Produces: additive login request fields `device_id`, `device_name`, `device_type`; additive token response field `session`.

- [ ] **Step 1: Write failing auth-session tests**

Add tests for:

```python
def test_two_devices_for_one_user_remain_live(client, seller): ...
def test_relogin_same_device_revokes_only_previous_device_session(client, seller): ...
def test_signed_token_with_missing_wrong_user_revoked_or_expired_sid_is_401(client): ...
def test_legacy_login_without_device_metadata_uses_unknown_device(client, seller): ...
def test_session_check_touches_last_seen_at_at_most_once_per_five_minutes(client): ...
```

For the first two, login with `device-a` and `device-b`, call `/api/auth/session-check` with both tokens, then login again with `device-a`. Assert the old A token returns 401 with `AUTH_SESSION_REVOKED`, new A and B return 200. Replace the old `test_single_session_dang_nhap_moi_vo_hieu_token_cu` assertion: global invalidation is no longer the intended behavior.

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_sessions_r4.py tests/test_auth.py::test_two_device_logins_do_not_invalidate_each_other
```

Expected: fail because login still overwrites `users.session_id` and no registry lookup exists.

- [ ] **Step 3: Implement normalization and session creation**

Keep validation at the trust boundary:

```python
def normalize_device_metadata(
    device_id: str | None,
    device_name: str | None,
    device_type: str | None,
) -> tuple[str, str, str]:
    normalized_id = (device_id or f"legacy-{secrets.token_urlsafe(18)}").strip()
    normalized_name = (device_name or "Thiết bị chưa đặt tên").strip()
    normalized_type = (device_type or "UNKNOWN").upper()
    if not 1 <= len(normalized_id) <= 128:
        raise ValueError("AUTH_DEVICE_ID_INVALID")
    if not 1 <= len(normalized_name) <= 80:
        raise ValueError("AUTH_DEVICE_NAME_INVALID")
    if normalized_type not in AUTH_DEVICE_TYPES:
        normalized_type = "UNKNOWN"
    return normalized_id, normalized_name, normalized_type
```

`create_session` must run in the caller transaction: revoke an existing active row for the same `user_id + device_id` with reason `SAME_DEVICE_LOGIN`, generate server-side `sid`, store the same expiry as the JWT, flush the row, and return row plus bearer token. It must never accept a session ID from the client.

- [ ] **Step 4: Switch authentication dependency to registry lookup**

After JWT signature/expiry and user lookup, call `require_live_session`. On success, call `bind_request_session`. Map failure codes exactly:

- unknown sid or wrong user: `401 AUTH_SESSION_INVALID`;
- `revoked_at` set: `401 AUTH_SESSION_REVOKED`;
- `expires_at <= now`: `401 AUTH_SESSION_EXPIRED`;
- inactive user: keep the baseline inactive-account error.

Update `last_seen_at` only from `/session-check`, and only when it is at least five minutes old. Do not commit from the dependency; the route/service owns commit.

- [ ] **Step 5: Make login response additive and preserve legacy clients**

Extend the response with:

```json
{
  "session": {
    "session_id": "server-generated",
    "device_id": "browser-profile-id",
    "device_name": "Quầy thu ngân 1",
    "device_type": "DESKTOP",
    "created_at": "UTC ISO-8601",
    "last_seen_at": "UTC ISO-8601",
    "expires_at": "UTC ISO-8601",
    "current": true
  }
}
```

Remove all new reads/writes of `User.session_id`. Keep the column untouched. A client missing metadata gets a generated legacy-compatible device identity and still logs in.

- [ ] **Step 6: Run focused auth regression GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_sessions_r4.py tests/test_auth.py
git diff --check
```

Expected: new multi-device contract and existing register/verify/password/login protections pass.

### Task 3: Server logout, self-management và owner-scoped staff management

**Files:**

- Modify: `fselling/schemas/auth.py`
- Modify: `fselling/routers/auth.py`
- Modify: `fselling/routers/staff.py`
- Modify: `fselling/services/auth_session_service.py`
- Modify: `fselling/services/staff_service.py`
- Modify: `fselling/models/system_log.py`
- Create: `tests/test_auth_session_management_r4.py`

**Interfaces:**

- Produces: `list_user_sessions(db, target_user_id: int, current_session_id: str) -> list[AuthSessionView]`.
- Produces: `rename_session(db, actor: User, target_user_id: int, session_id: str, device_name: str) -> AuthSessionView`.
- Produces: `revoke_session(db, actor: User, target_user_id: int, session_id: str, reason: str) -> bool`, returning `True` only for the first state change.
- Produces routes: `POST /api/auth/logout`, `GET /api/auth/sessions`, `PATCH /api/auth/sessions/{session_id}`, `DELETE /api/auth/sessions/{session_id}`.
- Produces owner routes: `GET /api/staff/{staff_id}/sessions`, `DELETE /api/staff/{staff_id}/sessions/{session_id}`.

- [ ] **Step 1: Write authorization and idempotency tests**

Test self-list ordering (`current` first, then newest), safe fields only, trim/length validation for rename, server logout, repeated revoke, owner access to staff in own shop, cross-shop staff/session IDs returning 404, and non-owner receiving 403. Assert repeated revoke does not add a second `AUTH_SESSION_REVOKE` log.

Representative assertion:

```python
response = owner_client.delete(f"/api/staff/{foreign_staff.id}/sessions/{sid}")
assert response.status_code == 404
assert "device_id" not in response.text
```

- [ ] **Step 2: Run focused management tests and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_management_r4.py
```

Expected: 404 for missing routes or import error for management functions.

- [ ] **Step 3: Implement self routes and lifecycle audit**

Read actor session ID only from `db.info`. `POST /logout` revokes current row with reason `LOGOUT` and commits before replying. `DELETE` is idempotent: an already-revoked session returns the same safe terminal representation without a second audit event. `PATCH` accepts only `{ "device_name": "..." }` and uses the same 1–80-character validation as login.

Write only these event codes here: `AUTH_SESSION_LOGOUT`, `AUTH_SESSION_REVOKE`. Audit details may contain target user ID, device type, sanitized device name and reason, but not bearer token or full device ID.

- [ ] **Step 4: Implement owner staff scope by reusing existing shop authorization**

Resolve the staff row before the session row and reuse the owner/own-shop check already used by staff role/delete routes. Only after that may the service query target sessions. Return 404 for a staff ID outside the shop and for a session not owned by the scoped staff. Do not add an admin support console or a global session-list endpoint.

- [ ] **Step 5: Run focused management and role authorization GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_management_r4.py tests/test_staff_roles.py
git diff --check
```

Expected: lifecycle, idempotency and cross-shop tests pass; existing staff restrictions stay green.

### Task 4: Password/role/account lifecycle và lost-device revoke with offline leases

**Files:**

- Modify: `fselling/services/auth_session_service.py`
- Modify: `fselling/services/auth_service.py`
- Modify: `fselling/services/staff_service.py`
- Modify: `fselling/services/shop_service.py`
- Modify: `fselling/services/offline_lease_service.py`
- Modify: `fselling/schemas/auth.py`
- Modify: `fselling/routers/auth.py`
- Create: `tests/test_auth_session_lifecycle_r4.py`
- Modify: `tests/test_offline_lease_identity.py`

**Interfaces:**

- Produces: `revoke_all_user_sessions(db, *, target_user_id: int, actor_user_id: int | None, reason: str, except_session_id: str | None = None) -> int`.
- Produces: `revoke_device(db, *, actor: User, target_user_id: int, device_id: str, reason: str) -> DeviceRevokeResult`.
- `DeviceRevokeResult` contains integer counts `sessions_revoked` and `offline_leases_revoked`.
- Produces: `POST /api/auth/devices/revoke`; owner staff form is `POST /api/staff/{staff_id}/devices/revoke`.

- [ ] **Step 1: Write the lifecycle matrix as focused tests**

Define the result type in `auth_session_service.py`:

```python
@dataclass(frozen=True)
class DeviceRevokeResult:
    sessions_revoked: int
    offline_leases_revoked: int
```

Cover:

- self password change revokes all old sessions and creates exactly one new session on the current device;
- forgot/reset password revokes every session and returns no bearer token;
- role change and account disable revoke every online session in the same transaction;
- failed-login temporary lockout does not revoke an already-valid session;
- device revoke affects all active sessions with the target `device_id` and only offline leases whose `issued_by_auth_session_id` points to one of those sessions;
- nullable legacy leases remain intact and continue through baseline recovery rules;
- a forced exception rolls back both online-session and lease revocation.

- [ ] **Step 2: Run lifecycle tests and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_lifecycle_r4.py tests/test_offline_lease_identity.py
```

Expected: lifecycle assertions fail because code still rotates one user column and leases have no issuing-session behavior.

- [ ] **Step 3: Replace single-column rotation with bulk registry revocation**

Use one conditional SQL update per target user, not a loop. For change-password, capture current device metadata, revoke all old rows with reason `PASSWORD_CHANGE`, then create the replacement session in the same transaction. For role change and disable, call `revoke_all_user_sessions` before the existing commit. Preserve the baseline rule that staff with an OPEN cash shift cannot be disabled.

Emit one aggregate event per lifecycle action with the affected count: `AUTH_SESSIONS_REVOKE_PASSWORD`, `AUTH_SESSIONS_REVOKE_ROLE_CHANGE`, or `AUTH_SESSIONS_REVOKE_ACCOUNT_DISABLE`.

- [ ] **Step 4: Link newly issued offline leases and implement device revoke atomically**

At lease issue, copy `db.info["auth_session_id"]` into `issued_by_auth_session_id`. For device revoke, collect affected issuing session IDs, lock affected shops in sorted ID order using the existing inventory/shop lock primitive, then conditionally revoke active sessions and linked active leases before one commit. Do not delete receipts, seal state or lease history; do not infer ownership for legacy null links.

Return only counts and safe device display metadata. Audit event is `AUTH_DEVICE_REVOKE`; no full device ID in details.

- [ ] **Step 5: Run focused lifecycle/offline GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_lifecycle_r4.py tests/test_offline_lease_identity.py tests/test_offline_lease_revocation.py tests/test_offline_recovery.py tests/test_staff_roles.py
git diff --check
```

Expected: lifecycle matrix, offline normal/recovery path and staff safeguards pass.

### Task 5: Approval binding to actor auth session and exact cash shift

**Files:**

- Modify: `fselling/services/approval_service.py`
- Modify: `fselling/services/return_service.py`
- Modify: `fselling/services/fnb_service.py`
- Modify: `fselling/services/shift_service.py`
- Modify: `fselling/services/shop_service.py`
- Modify: `fselling/schemas/fnb.py`
- Create: `tests/test_approval_session_r4.py`
- Modify: `tests/test_return_approval_r3.py`
- Modify: `tests/test_fnb_r1b_cancel.py`

**Interfaces:**

- Extends: `issue_approval(..., actor_auth_session_id: str, cash_shift_id: int | None) -> str`.
- Extends: `consume_approval(..., actor_auth_session_id: str, cash_shift_id: int | None) -> FnbManagerApproval`.
- Produces: `invalidate_approvals_for_approver(db: Session, approver_user_id: int, reason: str) -> int`.
- Preserves: one-use, five-minute, actor user/shop/action/entity/revision/context binding and atomic consumption from Plan 3.

- [ ] **Step 1: Write approval-session and shift tests**

Create tests that issue a token in session A and assert consume from session B returns 403 without side effects; revoke A and assert its token is invalid; change approver PIN/role/active state and assert outstanding tokens fail; use a cash-return token after closing/opening a shift and assert `409 RETURN_CONTEXT_CHANGED`; verify non-cash F&B cancel approval does not require a cash shift.

Assert the approval row stores `actor_auth_session_id`, and cash context stores the exact open shift ID used at issue.

- [ ] **Step 2: Run approval tests and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_approval_session_r4.py tests/test_return_approval_r3.py tests/test_fnb_r1b_cancel.py
```

Expected: cross-session consumption succeeds or the new column/context assertion fails.

- [ ] **Step 3: Bind issue and consume to server-side request session**

Read `actor_auth_session_id` from `db.info`, never request JSON. Include it in the signed/hashed approval context and database row. At consume, require exact equality before marking used. Continue checking approver active status, current membership and `RETURN_APPROVE`; do not treat a valid session as cached authorization.

For cash returns, resolve and lock the actor's current OPEN shift, add its ID to the context fingerprint at both issue and consume, and return `RETURN_CONTEXT_CHANGED` if the old shift is closed or replaced. Reuse `_lock_open_shift`; do not bind unrelated F&B cancellation to a cash shift.

- [ ] **Step 4: Invalidate approval tokens on PIN change**

Call `invalidate_approvals_for_approver` inside the existing PIN-change transaction. Mark only unused/unexpired approvals issued by that approver, and write one `APPROVALS_INVALIDATE_PIN_CHANGE` audit event with the count. Role loss/account disable remains fail-closed at consume and may also mark matching tokens invalid in the same lifecycle transaction for clearer state.

- [ ] **Step 5: Run focused approval/return GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_approval_session_r4.py tests/test_return_approval_r3.py tests/test_fnb_r1b_cancel.py tests/test_manager_approval.py
git diff --check
```

Expected: session/shift binding passes while Plan 3 one-use, expiry, revision and context behavior remains green.

### Task 6: Transaction write-fence, revoke races và cross-session idempotency

**Files:**

- Modify: `fselling/services/auth_session_service.py`
- Modify: `fselling/services/order_service.py`
- Modify: `fselling/services/inventory_service.py`
- Modify: `fselling/services/shift_service.py`
- Modify: `fselling/services/fnb_service.py`
- Modify: `fselling/services/return_service.py`
- Modify: any protected mutation service discovered by the inventory command below that bypasses all four shared lock points
- Create: `tests/test_auth_session_mutation_fence_r4.py`
- Modify: `tests/test_fnb_r1b_send.py`
- Modify: `tests/test_fnb_r1b_cancel.py`

**Interfaces:**

- Consumes: `fence_live_auth_session(db: Session) -> None` from Task 2.
- Contract: the fence performs a conditional no-op `UPDATE auth_sessions SET last_seen_at = last_seen_at WHERE session_id = :sid AND user_id = :uid AND revoked_at IS NULL AND expires_at > :now`; zero rows raises 401 and caller transaction rolls back.
- Contract: the fence runs after the existing domain/shop/shift lock and before the first side effect.

- [ ] **Step 1: Inventory every protected mutation before editing**

Run and save the output in the task notes:

```powershell
rg -n "@router\.(post|put|patch|delete)" fselling/routers
rg -n "_lock_shop_for_order|lock_shop_for_inventory|_lock_open_shift|_prepare_locked_shop" fselling/services fselling/routers
```

Map each money, stock, order, return, shift, F&B session/ticket/KDS and approval mutation to one existing lock point. Add a direct fence only for a mapped mutation that reaches none of them. Read every caller before changing a shared lock helper.

- [ ] **Step 2: Write deterministic race and idempotency tests**

Use two SQLAlchemy sessions against a temporary SQLite file and barriers/events, not `sleep`. Cover representative shared lock paths:

```python
def test_revoke_commit_before_retail_sale_blocks_all_side_effects(): ...
def test_sale_commit_before_revoke_returns_durable_result(): ...
def test_revoke_commit_before_fnb_send_blocks_ticket_and_action_log(): ...
def test_revoke_commit_before_kds_mutation_blocks_ticket_state(): ...
def test_new_session_retry_same_operation_id_returns_old_result_once(): ...
def test_new_session_retry_changed_payload_keeps_409_collision(): ...
```

Assert row counts for order/ledger/stock/ticket/approval-use/action log, not only HTTP status. For the winning mutation, assert `auth_session_id` is recorded in the same transaction.

- [ ] **Step 3: Run race tests and confirm RED**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_mutation_fence_r4.py
```

Expected: revoked request can still commit after revoke, or no session attribution exists.

- [ ] **Step 4: Implement one fence and call it from the fewest shared choke points**

Implement `fence_live_auth_session` as the conditional update above. If `auth_session_required=True` but session/user IDs are absent, raise `AUTH_SESSION_INVALID`; if the request marker is absent, return immediately so migration/CLI/direct service tests remain valid.

Insert it into `_lock_shop_for_order`, `lock_shop_for_inventory`, `_lock_open_shift` and `_prepare_locked_shop` only after confirming no nested path double-fences. If `_prepare_locked_shop` already calls `lock_shop_for_inventory`, do not add a second call. Add explicit fences to approval/staff/session mutation boundaries only where no shared lock owns the transaction.

- [ ] **Step 5: Attribute audit without changing idempotency keys**

Populate `SystemLog.auth_session_id` and `FnbActionLog.auth_session_id` from `db.info` on new protected mutations touched by this task. Do not append auth session ID to operation ID, fingerprint, order key or business uniqueness constraints. A retry from a replacement session must return the already-durable result without adding a ledger/stock/ticket/log row.

- [ ] **Step 6: Run focused money/inventory/F&B/recovery GREEN**

Run:

```powershell
python -m pytest -q -p no:warnings tests/test_auth_session_mutation_fence_r4.py tests/test_fnb_r1b_send.py tests/test_fnb_r1b_cancel.py tests/test_orders.py tests/test_stock_adjust.py tests/test_shifts.py tests/test_return_approval_r3.py
git diff --check
```

Expected: deterministic ordering, atomic rollback and cross-session retry contracts pass with no money/stock duplication.

### Task 7: Shared browser identity, session/device UI và owner staff entry point

**Files:**

- Modify: `static/js/api.js`
- Modify: `static/js/auth.js`
- Create: `static/js/session-device-r4.js`
- Create: `static/css/session-device-r4.css`
- Modify: `static/js/seller.js`
- Modify: `static/js/locales/common.js`
- Modify: `static/js/locales/seller.js`
- Modify: `static/index.html`
- Modify: `static/seller.html`
- Modify: `static/pos.html`
- Modify: `static/fnb.html`
- Modify: `static/fnb-station.html`
- Modify: `static/admin.html`
- Create: `tests/js/auth-sessions-r4.test.js`

**Interfaces:**

- Produces browser helpers: `getOrCreateAuthDeviceId() -> string`, `guessAuthDeviceType() -> string`, `buildAuthDeviceMetadata() -> object`.
- Produces `window.SessionDeviceR4.openSelf()` and `window.SessionDeviceR4.openStaff(staffId, displayName)`.
- Extends `apiCall` 401 handling with stable `{ code, mutationOutcomeUnknown }` information for UI recovery.
- Preserves IndexedDB device/seal behavior in `offline-ban.js`; no auth session token is written to IndexedDB.

- [ ] **Step 1: Write Node tests for browser identity and recovery state**

Use the repository's existing DOM/localStorage fakes. Test that device ID is generated once with `crypto.randomUUID()` and reused across tabs; metadata never uses the device type for authorization; login body includes the metadata; logout calls `/api/auth/logout` before local cleanup but still clears local auth if the network call fails; a 401 after a mutation marks the outcome unknown and preserves its draft/operation ID; rendered hostile device names use `textContent`, not `innerHTML`.

- [ ] **Step 2: Run JS tests and confirm RED**

Run:

```powershell
node --test tests/js/auth-sessions-r4.test.js
```

Expected: helper/module imports or behavioral assertions fail.

- [ ] **Step 3: Add stable browser-profile metadata to login**

Store only opaque device ID and display defaults in localStorage under versioned `fselling_auth_device_r4_*` keys. Prefer `crypto.randomUUID()`; use `crypto.getRandomValues()` fallback already available in browsers. Infer display-only type from viewport/user agent into `DESKTOP`, `TABLET`, `MOBILE` or `UNKNOWN`; KDS page explicitly uses `KDS`. Send metadata in login JSON and cache the returned session summary, never the raw session ID outside the existing bearer token payload/use.

Change the session check interval from 3 seconds to 15 seconds. Server-side five-minute write throttling remains authoritative.

- [ ] **Step 4: Make logout server-side and preserve unknown-result recovery**

Call `POST /api/auth/logout`; in `finally`, perform the existing local token/role cleanup and offline seal behavior. For a mutation whose connection fails or returns session 401 after dispatch, keep its operation ID/draft and show the exact recovery guidance: result may already have committed; sign in and retry/lookup with the same operation ID before creating a new action.

- [ ] **Step 5: Build one accessible native dialog shared by protected pages**

`session-device-r4.js` creates a `<dialog>` with heading, close button, live status region and a list showing device name, type, current badge, created, last-seen and expiry. Buttons cover rename, revoke session and revoke device. Require confirmation for device revoke and state that linked offline authorization will be revoked while local receipts remain for recovery.

Use native buttons, labels, focus return, Escape close and `textContent`. Add the smallest CSS needed for 390×844, 1024×768 and desktop. Add one “Phiên và thiết bị” menu/button to seller, POS, F&B, KDS and admin; do not alter page navigation or visual system.

- [ ] **Step 6: Add owner staff access by reusing the shared dialog**

In the existing staff row renderer in `seller.js`, add one button that calls `SessionDeviceR4.openStaff(staff.id, staff.username)`. It must not fetch until clicked. Hide it for non-owner flows using the same owner capability check already used for role/delete controls.

Add exact Vietnamese and English locale keys to `common.js`; add only the staff-specific label to `seller.js`. No hard-coded duplicate copy across HTML pages.

- [ ] **Step 7: Load versioned assets and run focused JS GREEN**

Load `session-device-r4.css` and `session-device-r4.js` after `api.js` on the five protected pages. Load device metadata helper before `auth.js` on `index.html`; if keeping one file satisfies both, make `session-device-r4.js` safe when no protected-page controls exist.

Run:

```powershell
node --test tests/js/auth-sessions-r4.test.js
node --test tests/js/*.test.js
git diff --check
```

Expected: focused module and existing JS suite pass; no page throws when session controls are absent.

### Task 8: Focused integration, browser UAT, independent review và one final owner gate

**Files:**

- Create: `SESSION_DEVICE_R4_UAT_REPORT.md`
- Modify: only files required to fix concrete findings from the checks below

**Interfaces:**

- Produces: evidence split into `AUTOMATED`, `BROWSER`, and `TEST_GAP` sections.
- Produces: a clean candidate for the owner-controlled one-time full suite and commit.

- [ ] **Step 1: Run focused integration only**

Run the focused R4 modules plus the exact baseline safety set:

```powershell
python -m pytest -q -p no:warnings tests/test_migration_0014_session_device_safety_r4.py tests/test_auth_sessions_r4.py tests/test_auth_session_management_r4.py tests/test_auth_session_lifecycle_r4.py tests/test_auth_session_mutation_fence_r4.py tests/test_approval_session_r4.py tests/test_offline_lease_identity.py tests/test_return_approval_r3.py tests/test_fnb_r1b_cancel.py tests/test_shifts.py
node --test tests/js/auth-sessions-r4.test.js
git diff --check
```

Expected: all focused tests pass. Do not run the full Python suite here.

- [ ] **Step 2: Exercise browser UAT against a copied, migrated fake DB**

Record viewport, role, named device, exact control, HTTP result and durable row/result for:

1. Owner at 390×844 and desktop: two sessions, rename, revoke one, other survives.
2. SERVICE mobile: two accounts active; revoked phone gets 401 on next mutation; other waiter survives.
3. CASHIER at 1024×768 and tablet: replacement session continues the same user/shop shift; same operation ID does not duplicate money/order/return.
4. KDS kitchen and bar: named stations; revoking kitchen does not revoke bar; revoked station cannot mutate ticket.
5. Manager/owner: actor-device approval succeeds; copied token on another session fails; no remote approval inbox exists.
6. Owner staff panel: own-shop list/revoke works; tampered foreign staff/session ID returns 404 without identity leak.
7. Password, role, account, PIN and close-shift rows of the design matrix.

Never call ngrok/provider or use real data. Mark actual lost-device, network split, push and any unharnessed race as `TEST_GAP`; do not label them browser PASS.

- [ ] **Step 3: Write the evidence report**

In `SESSION_DEVICE_R4_UAT_REPORT.md`, include baseline commit, migration revision, commands with actual counts, browser matrix, durable evidence, `TEST_GAP` list, and residual bearer-token risk. Do not copy secrets, tokens, passwords, PINs, email addresses or real usernames into the report.

- [ ] **Step 4: Perform independent safety review without a subagent**

Start a fresh review pass after clearing implementation context. Review for:

- any protected mutation missing request authentication or write-fence;
- fence order relative to domain locks and first side effect;
- cross-shop enumeration and privilege errors;
- approval session/shift binding and atomic one-use;
- offline legacy nullable-link recovery;
- logs containing secrets/full device IDs;
- business idempotency keys polluted by session identity;
- unrequested dependency, WebSocket, policy engine or redesign.

Record every concrete finding in the UAT report with `OPEN` or `FIXED`. Re-run the smallest failing test after each fix, then repeat the focused command from Step 1.

- [ ] **Step 5: Inspect the final file set and whitespace**

Run:

```powershell
git status --short
git diff --stat
git diff --check
rg -n "TO.DO|TB.D|FIX.ME" fselling migrations static tests SESSION_DEVICE_R4_UAT_REPORT.md
```

Expected: only intended R4 implementation, tests, design/plan and UAT report appear; `git diff --check` is silent; placeholder scan is empty. If unrelated user files appear, stop before the commit gate and ask the owner how to preserve them because `test-commit.ps1` stages all files.

- [ ] **Step 6: Owner-controlled one-time full suite and commit**

After the owner approves the file list and confirms no unrelated changes are present, run exactly once:

```powershell
.\test-commit.ps1 "feat: add session and device safety r4"
```

Expected: the script runs the repository full suite, stages the reviewed files and creates one commit. If it fails, preserve its output, fix only the evidenced defect, rerun the smallest focused test, and request owner direction before another full-suite attempt. Do not push, merge, rebase or deploy.

## Completion Gate

Implementation is complete only when all 14 acceptance criteria in the spec map to passing focused evidence, browser evidence or an explicit `TEST_GAP`; the independent review has no open P0/P1 safety finding; the owner-controlled full suite passes once; and the resulting commit contains only reviewed R4 files.
