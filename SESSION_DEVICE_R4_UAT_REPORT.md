# Session and Device Safety R4 UAT evidence

Date: 2026-09-10
Baseline commit: `1acf0977d1a6431d88de0ffc590904af0b0f47f7`
Migration revision: `0014_session_device_safety_r4`

## Scope and environment

- Implementation and verification used the isolated Plan 4 worktree only.
- Browser checks used a temporary fake SQLite database at
  `%LOCALAPPDATA%\Temp\fselling_r4_uat_e82471f8067646969c07840acce1fead\uat.db`
  and a local-only server at `127.0.0.1:8765`.
- No real credentials, customer data, production database, external provider,
  tunnel, push, merge, rebase or deployment was used.
- The first owner-controlled full-suite gate ran and stopped without a commit:
  151 failures were recorded in pytest's last-failed cache.

## AUTOMATED

Focused Python gate:

```powershell
python -m pytest -q -p no:warnings `
  tests/test_migration_0014_session_device_safety_r4.py `
  tests/test_migration_0013_roles_returns_approval_r3.py `
  tests/test_auth_sessions_r4.py `
  tests/test_auth_session_management_r4.py `
  tests/test_auth_session_lifecycle_r4.py `
  tests/test_auth_session_mutation_fence_r4.py `
  tests/test_approval_session_r4.py `
  tests/test_auth.py tests/test_staff_roles.py `
  tests/test_offline_lease_identity.py `
  tests/test_offline_lease_revocation.py `
  tests/test_offline_recovery.py `
  tests/test_return_approval_r3.py `
  tests/test_fnb_r1b_cancel.py tests/test_fnb_r1b_send.py `
  tests/test_orders.py tests/test_stock_adjust.py tests/test_shifts.py
```

- Collected: `196` tests across 18 focused files.
- The last complete aggregate run exited `0` and reached `[100%]` before the
  final session-list ordering correction. A repeat aggregate run was
  interrupted without a final exit code; it is not counted as evidence.
- Post-correction regression: `tests/test_auth_session_management_r4.py` exited
  `0` with `3 passed`.
- This is focused evidence, not the repository full suite.

Final JavaScript gate after the inline-rename and cache-version corrections:

```powershell
node --test tests/js/*.test.js
```

- Exit code: `0`
- Result: `12 passed, 0 failed`.

Owner-gate failure recovery:

```powershell
C:\Python314\python.exe -m pytest --lf -q -p no:warnings
```

- Input: all `151` items recorded by the failed owner gate.
- Exit code: `0`; result reached `[100%]`.
- Root causes fixed without weakening runtime checks: historical migration tests
  now distinguish checked-in head `0014` from explicitly pinned revisions; the
  route contract includes the eight intended session endpoints; the offline ORM
  shape includes its nullable auth-session link; the subscription test helper
  creates a durable `AuthSession`; and `test-commit.ps1` selects only the first
  valid PATH Python application.

Post-recovery high-risk gate:

```powershell
C:\Python314\python.exe -m pytest -q -p no:warnings `
  tests/test_migration_0014_session_device_safety_r4.py `
  tests/test_auth_sessions_r4.py tests/test_auth_session_management_r4.py `
  tests/test_auth_session_lifecycle_r4.py `
  tests/test_auth_session_mutation_fence_r4.py `
  tests/test_approval_session_r4.py tests/test_offline_lease_identity.py `
  tests/test_return_approval_r3.py tests/test_fnb_r1b_cancel.py `
  tests/test_shifts.py tests/test_test_commit_script.py
```

- Exit code: `0`
- Result: `85 passed` across 11 focused files.
- The test-runner regression includes the multiple-Python PATH case that caused
  the owner gate to invoke a concatenated path.

Focused evidence covers:

- additive migration/backfill and checksum verification;
- independent multi-device sessions and same-device replacement;
- stable revoked/expired/disabled/role-changed authentication failures;
- self and owner-scoped session management, including cross-shop denial;
- password, role, account and lost-device bulk revocation;
- offline-lease linkage and revocation ordering;
- approval binding to the actor session and exact cash shift;
- transaction write-fence rollback after concurrent revocation;
- retry from a replacement session without duplicating the business operation;
- session attribution in system and F&B action logs;
- safe device-name rendering and no browser `prompt()` dependency.

## BROWSER

### Owner desktop

- Fresh login registered the browser as `DESKTOP` with platform metadata.
- The shared session dialog listed the current and additional active devices and
  marked the current session.
- Server-side logout completed and redirected to the login page.

### Owner mobile viewport, 390x844

- The same dialog fit the viewport, remained scrollable and exposed full-width
  actions.
- Rename used an inline, keyboard-focusable input with Save and Cancel controls.
- A hostile device name containing HTML was displayed as text. Read-only DOM
  evidence was `fired=false` and `imgCount=0`; no injected element executed.

### Browser corrections

- `FIXED`: the in-app browser does not support `prompt()` reliably. Rename now
  uses an inline input in the existing dialog.
- `FIXED`: stale browser assets initially produced untranslated metadata keys and
  an `UNKNOWN` device label. R4 asset URLs now carry a shared cache version.
- `FIXED`: generic network errors no longer retain login, password or PIN request
  bodies; only retryable drafts carrying an operation ID are attached.
- `FIXED`: session lists now exclude expired rows, place the current session
  first and order the remainder by newest `last_seen_at`.

## Durable evidence

- Each login creates or replaces an `AuthSession` row and the JWT `sid` points
  to that durable row without extending token lifetime.
- Revocation is durable and evaluated both at authentication time and at the
  shared write-fence before transaction commit.
- Approval records bind to the actor auth session and, for cash actions, the
  exact open cash shift.
- Offline leases, system logs and F&B action logs retain nullable auth-session
  linkage for audit while legacy rows remain readable.
- A replacement session may retry the same operation identifier; business
  idempotency remains scoped to the business operation, not the login session.

## TEST_GAP

- Browser revoke-button completion: the in-app browser became unrecoverable
  before confirmation. A database check showed the attempted click caused no
  revocation, so no browser PASS is claimed.
- Owner staff-session entry point: backend authorization is automated, but the
  complete owner UI path was not observed end to end.
- CASHIER tablet, SERVICE mobile, KDS desktop and 1024x768 desktop session UI
  were not exercised in this browser run.
- Approval-token copying across two live browsers is covered by automated API
  tests only.
- Actual lost-device transport, network split, response loss and an in-flight
  revoke race remain unharnessed browser scenarios.
- No push-notification or remote device-control channel exists in this plan.
- Independent second-agent safety review is pending after focused recovery.

## Findings and residual risk

- `OPEN` (out of Plan 4 scope): the existing demo seeder can stop while creating
  a synthetic staff account because its generated password may not satisfy the
  current password policy. The isolated database still contained enough fake
  owner/shop data for the completed browser checks.
- `OPEN`: access tokens remain bearer tokens in browser storage. Durable session
  revocation narrows replay lifetime and blocks protected writes after revoke,
  but it does not provide the stronger theft resistance of HttpOnly cookies or
  proof-of-possession tokens.

## Owner-controlled completion gate

The first owner gate failed and created no commit. After independent review, the
owner must review the corrected R4 file list and rerun the full-suite/commit gate:

```powershell
.\test-commit.ps1 "feat: add session and device safety r4"
```
