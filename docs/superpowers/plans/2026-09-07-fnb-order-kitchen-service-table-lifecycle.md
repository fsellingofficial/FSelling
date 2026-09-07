# F&B Plan 2 — Order, bếp/bar, phục vụ và vòng đời bàn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Also use superpowers:test-driven-development, superpowers:systematic-debugging for failures, and superpowers:verification-before-completion before any completion claim.

**Goal:** Loại bỏ hai đường gây kẹt/sai vòng đời F&B (merge làm lệch đồ thị dữ liệu và đóng bàn khi món chưa giao), rồi nối một luồng tối thiểu, quan sát được từ gửi món → bếp/bar → sẵn sàng → đã giao → thanh toán/đóng bàn mà không làm yếu các bảo vệ tiền của R1.

**Architecture:** Giữ nguyên state machine vé `NEW → IN_PROGRESS → DONE/CANCELLED`; biểu diễn “đã giao” bằng hai cột nullable trên vé và suy ra `READY`/`SERVED` ở read model. Mọi mutation tiếp tục chạy dưới shop lock, optimistic revision và exact operation ID hiện hữu. Merge target chuyển sang fail-closed; close kiểm tra toàn bộ vé đã terminal và đã giao. Frontend dùng một pending envelope duy nhất, explicit retry và request epoch; không thêm hàng đợi, WebSocket, framework hay dependency.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic, Alembic, vanilla HTML/CSS/JavaScript, i18next catalogs, pytest, Node `assert`.

**Spec:** `docs/superpowers/specs/2026-09-07-fnb-order-kitchen-service-table-lifecycle-design.md`

## Global Constraints

- Bắt đầu từ commit đúng `7c17fff097f3892ca41b6d962f17ed9348dd819d`; trước khi triển khai phải tạo/checkout nhánh Plan 2 cô lập và xác nhận worktree sạch.
- Không sửa năm file Seller: `static/css/seller.css`, `static/js/locales/seller.js`, `static/js/seller.js`, `static/seller.html`, `tests/test_seller_sidebar_ui.py`.
- Không làm yếu cash tender, shift, voucher, loyalty, permission, manager approval, optimistic revision, exact operation ID, stock allocation, migration checksum hoặc startup verification của R1.
- Không dùng dữ liệu thật, secret, `.env`, provider thanh toán thật hoặc dịch vụ ngoài.
- Không thêm role `WAITER`, WebSocket, item-level kitchen state, âm thanh/rung hoặc dependency mới trong Plan 2.
- Mỗi task theo vòng RED → GREEN → focused regression → `git diff --check`. Không chạy full suite trong vòng lặp.
- Kế hoạch này dùng checkpoint thay cho commit sau từng task để tránh chạy full suite lặp lại. Chỉ sau focused tests + browser UAT đạt và chủ dự án tự chạy `.\test-commit.ps1 -TestOnly` exit code 0 mới stage đúng danh sách file Plan 2 và commit một lần.
- Bất kỳ conflict nào liên quan tiền, revision, operation ID, allocation, approval hoặc migration đều fail closed; không tự đoán trạng thái thành công.

## File Map

| File | Purpose |
|---|---|
| `fselling/models/fnb.py` | Hai cột bàn giao món trên ticket |
| `migrations/versions/0012_fnb_ticket_service_handoff.py` | Migration cộng thêm, verifier và rollback |
| `migrations/checksums.json` | Chuỗi checksum migration tuyến tính |
| `fselling/schemas/fnb.py` | Payload serve/resume có revision và operation ID |
| `fselling/services/fnb_service.py` | Merge/close guard, lifecycle, read model, partial settlement |
| `fselling/routers/fnb.py` | Hai route ticket tối thiểu |
| `static/fnb.html`, `static/css/fnb-r1a.css`, `static/js/fnb-r1a.js`, `static/js/locales/fnb.js` | Trạng thái phục vụ và explicit recovery |
| `static/fnb-station.html`, `static/css/fnb-station-r1b.css`, `static/js/fnb-station-r1b.js` | KDS request epoch, resume và trạng thái kết nối |
| `tests/test_fnb_r1a_sessions.py` | Merge fail-closed và partial settlement |
| `tests/test_fnb_r1b_send.py` | Ticket lifecycle, revision và KDS scope |
| `tests/test_fnb_r1b_cancel.py` | Cancel sau READY/SERVED |
| `tests/test_fnb_r1c_checkout.py` | Close gate và bất biến thanh toán |
| `tests/test_contract.py` | Route allowlist |
| `tests/test_migration_0012_fnb_ticket_service_handoff.py` | Upgrade/downgrade/verifier/checksum |
| `tests/test_migration_0010_fnb_r1b.py`, `tests/test_migration_0011_fnb_r1c.py` | Cập nhật head kỳ vọng sau migration 0012 |
| `tests/test_migration_i04.py`, `tests/test_migration_i05.py` | Head, revision count và topology gate hiện hữu |
| `tests/test_migration_0004_offline.py`, `tests/test_migration_0005_offline_issue_lifecycle.py`, `tests/test_migration_0006_offline_receipt_items.py`, `tests/test_migration_0007_qr_payment_domain.py`, `tests/test_migration_0009_fnb_r1a.py`, `tests/test_offline_lease_identity.py` | Giữ fixture binary cũ và kỳ vọng head đồng bộ với migration 0012 |
| `tests/js/fnb-r1a.test.js`, `tests/js/fnb-station-r1b.test.js`, `tests/test_fnb_r1a_ui.py` | Controller, DOM contract và asset version |
| `FNB_PLAN2_UAT_REPORT.md` | Bằng chứng focused gate và browser UAT sau triển khai |

---

## Task 1: Khóa ngay hai đường P0 ở service hiện hữu

**Files:**

- Modify: `tests/test_fnb_r1a_sessions.py`
- Modify: `tests/test_fnb_r1c_checkout.py`
- Modify: `fselling/services/fnb_service.py`

### Step 1: Viết test merge target fail-closed

Trong `tests/test_fnb_r1a_sessions.py`, tạo target session có lần lượt ticket, allocation, check line hoặc order đã tồn tại. Gọi merge và khẳng định:

```python
assert response.status_code == 409
assert response.json()["detail"]["code"] == "FNB_TARGET_SESSION_HAS_ARTIFACTS"
assert source_table.active_session_id == source_session.id
assert target_table.active_session_id == target_session.id
assert target_line.session_id == target_session.id
assert target_ticket.session_id == target_session.id
```

Ít nhất một test phải có line đã gửi, ticket và allocation thật để bắt đúng lỗi helper luôn trả `True`; một test phải có check/order terminal để bảo vệ R1C.

### Step 2: Viết test close fail-closed khi còn vé bếp/bar

Trong `tests/test_fnb_r1c_checkout.py`, thanh toán check đúng quy trình R1 rồi để ticket `NEW` hoặc `IN_PROGRESS`:

```python
response = client.post(
    f"/api/fnb/sessions/{session_id}/close",
    json={"expected_session_revision": revision, "operation_id": op_id},
    headers=cashier_headers,
)
assert response.status_code == 409
assert response.json()["detail"]["code"] == "FNB_ACTIVE_TICKETS"
assert refreshed_session.status != "CLOSED"
assert refreshed_table.active_session_id == session_id
```

Snapshot lỗi phải có danh sách ticket blocker đủ để UI giải thích, nhưng không chứa dữ liệu nhạy cảm.

### Step 3: Chạy RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_sessions.py -k 'merge and artifacts' `
  tests/test_fnb_r1c_checkout.py -k 'close and active_ticket'
```

Expected: test mới fail vì merge hiện vẫn di chuyển line và close hiện vẫn nhả bàn.

### Step 4: Thay helper merge bằng truy vấn fail-closed

Trong `fselling/services/fnb_service.py`, thay `session_has_only_r1a_drafts()` bằng một predicate đọc đủ đồ thị target. Chỉ trả `True` nếu session target:

- chưa có `FnbKitchenTicket` hoặc `FnbStockAllocation`;
- chưa có check line, order hoặc payment liên kết;
- mọi line đều `sent_quantity == 0` và `cancelled_quantity == 0`;
- mọi check chỉ là draft rỗng, chưa có side effect tài chính.

Không di chuyển ticket/check/allocation giữa session. Nếu có bất kỳ artifact nào, trả `409 FNB_TARGET_SESSION_HAS_ARTIFACTS` trước mutation đầu tiên.

### Step 5: Thêm close guard tối thiểu

Ngay sau revision/check-state validation nhưng trước khi nhả bàn, query ticket của session. Nếu còn `NEW` hoặc `IN_PROGRESS`, trả conflict:

```python
raise_fnb_conflict(
    "FNB_ACTIVE_TICKETS",
    "Bàn còn món đang chờ bếp/bar.",
    snapshot={"tickets": blockers},
)
```

Task 3 sẽ mở rộng cùng một helper để chặn cả `DONE` chưa giao; không tạo guard thứ hai.

### Step 6: Chạy GREEN và regression gần nhất

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_sessions.py tests/test_fnb_r1c_checkout.py
git diff --check -- fselling/services/fnb_service.py tests/test_fnb_r1a_sessions.py tests/test_fnb_r1c_checkout.py
```

Expected: hai file test pass; không có whitespace error. Dừng checkpoint để review, chưa commit.

---

## Task 2: Migration cộng thêm cho dấu mốc đã giao

**Files:**

- Create: `migrations/versions/0012_fnb_ticket_service_handoff.py`
- Modify: `migrations/checksums.json`
- Modify: `fselling/models/fnb.py`
- Create: `tests/test_migration_0012_fnb_ticket_service_handoff.py`
- Modify: `tests/test_migration_i04.py`
- Modify: `tests/test_migration_i05.py`

### Step 1: Viết migration tests trước

Kiểm tra bốn trường hợp:

1. Upgrade từ `0011` tạo `served_by_user_id` và `served_at`, đều nullable.
2. Dữ liệu `0011` hiện hữu được giữ nguyên.
3. Verifier từ chối half-pair và `served_at` trên ticket không `DONE`.
4. Lỗi giữa hai statement rollback cả DDL và version; `downgrade()` tiếp tục fail
   bằng `FORWARD_ONLY_MIGRATION` theo policy của repo.

Cập nhật hai contract hiện hữu: danh sách `down_revision`/head ở
`tests/test_migration_i04.py::test_linear_graph_and_checksum_manifest`, và head,
revision count, upgrade list ở
`tests/test_migration_i05.py::test_fresh_0001_0002_0003_and_i04_upgrade_are_linear`.

### Step 2: Chạy RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_migration_0012_fnb_ticket_service_handoff.py
```

Expected: fail vì revision/model chưa tồn tại.

### Step 3: Viết migration tuyến tính, additive

Revision phải là:

```python
revision = "0012_fnb_ticket_service_handoff"
down_revision = "0011_fnb_checkout_r1c"
```

`upgrade()` chỉ chạy hai statement SQLite additive qua `_execute()` giống các
revision F&B hiện hữu:

```python
DDL = (
    "ALTER TABLE fnb_kitchen_tickets "
    "ADD COLUMN served_by_user_id INTEGER REFERENCES users(id)",
    "ALTER TABLE fnb_kitchen_tickets ADD COLUMN served_at DATETIME",
)

def upgrade():
    for statement in DDL:
        _execute(statement)

def downgrade():
    raise RuntimeError("FORWARD_ONLY_MIGRATION")
```

Theo pattern verifier của `0010`/`0011`, kiểm tra read-only:

```sql
(served_by_user_id IS NULL) = (served_at IS NULL)
AND (served_at IS NULL OR status = 'DONE')
```

Không backfill, không đổi enum/status constraint, không import helper mutable ngoài Alembic.

### Step 4: Cập nhật model và manifest

Trong `FnbKitchenTicket`, thêm đúng hai field nullable và relationship không cần thiết thì bỏ. Tính checksum bằng cùng thuật toán chuẩn hóa CRLF/LF của `fselling/migration/graph.py:_source_checksum`, không chép hash thủ công từ file chưa ổn định:

```powershell
.\.venv\Scripts\python.exe -B -c "from pathlib import Path; from fselling.migration.graph import _source_checksum; print(_source_checksum(Path('migrations/versions/0012_fnb_ticket_service_handoff.py'))[0])"
```

Ghi hash đó vào entry `0012` trong `migrations/checksums.json` với đúng `down_revision` và path.

### Step 5: Chạy GREEN và graph gate

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_migration_0012_fnb_ticket_service_handoff.py `
  tests/test_migration_i04.py::test_linear_graph_and_checksum_manifest `
  tests/test_migration_i05.py::test_fresh_0001_0002_0003_and_i04_upgrade_are_linear
git diff --check -- migrations/versions/0012_fnb_ticket_service_handoff.py migrations/checksums.json fselling/models/fnb.py tests/test_migration_0012_fnb_ticket_service_handoff.py tests/test_migration_i04.py tests/test_migration_i05.py
```

Expected: migration tests và graph/checksum tests pass. Dừng checkpoint, chưa commit.

---

## Task 3: Hoàn tất backend lifecycle READY/SERVED và revision propagation

**Files:**

- Modify: `fselling/schemas/fnb.py`
- Modify: `fselling/routers/fnb.py`
- Modify: `fselling/services/fnb_service.py`
- Modify: `tests/test_fnb_r1b_send.py`
- Modify: `tests/test_fnb_r1b_cancel.py`
- Modify: `tests/test_fnb_r1c_checkout.py`
- Modify: `tests/test_contract.py`

### Step 1: Viết contract/API tests

Thêm các test sau:

- `start`: `NEW → IN_PROGRESS`, tăng ticket revision, session revision và shop revision.
- `done`: chỉ `IN_PROGRESS → DONE`; bị chặn nếu còn `out_of_stock_reason`.
- `resume`: đúng station permission, xóa lý do hết món, tăng cả ba revision; exact replay không tăng lần hai.
- `serve`: cần `FNB_SERVICE`, ticket phải `DONE`, chưa served, đúng ticket/session revision; ghi actor/time một lần; exact replay trả cùng kết quả.
- close: `DONE` chưa served trả `FNB_UNSERVED_TICKETS`; served rồi mới cho close.
- cancel: READY cho RESTOCK/WASTE theo R1; SERVED không cho RESTOCK, WASTE vẫn cần approval gắn revision hiện hành.
- station isolation, wrong role, stale ticket/session revision đều không có side effect.
- `tests/test_contract.py` nhận đúng hai route mới, không mở wildcard.

Payload tối thiểu:

```json
{
  "expected_state_version": 3,
  "expected_session_revision": 8,
  "operation_id": "uuid"
}
```

### Step 2: Chạy RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1b_send.py tests/test_fnb_r1b_cancel.py `
  tests/test_fnb_r1c_checkout.py tests/test_contract.py
```

Expected: route/schema/lifecycle assertions mới fail.

### Step 3: Thêm schema và route nhỏ nhất

Trong `fselling/schemas/fnb.py`, tái dùng base request đang ép `extra='forbid'`:

```python
class FnbTicketMutation(FnbRequest):
    expected_state_version: int = Field(ge=0)
    expected_session_revision: int = Field(ge=0)
```

Thêm hai route dưới namespace hiện có:

```text
POST /api/fnb/tickets/{ticket_id}/resume
POST /api/fnb/tickets/{ticket_id}/serve
```

Không thêm endpoint status riêng; GET session và station tickets là read model duy nhất.

### Step 4: Dùng một helper mutation ticket chung

Trong `fnb_service.py`, giữ shop lock và exact-operation replay hiện có. Mọi `start`, `done`, `out-of-stock`, `resume`, `serve` phải:

1. authenticate shop + permission/station;
2. kiểm tra fingerprint/exact replay;
3. kiểm tra ticket state version;
4. kiểm tra session revision;
5. mutate một lần;
6. tăng ticket `state_version`, session `revision`, shop `fnb_revision`;
7. record operation và commit qua `_finish` hiện có.

Không tạo một transaction framework mới; trích helper chỉ khi cả năm đường thật sự dùng chung.

Read model suy ra:

```python
def ticket_service_stage(ticket):
    if ticket.status == "CANCELLED":
        return "CANCELLED"
    if ticket.served_at is not None:
        return "SERVED"
    if ticket.status == "DONE":
        return "READY"
    return ticket.status
```

Session snapshot thêm `service_stage`, `service_summary` và các `service_tickets` đang cần hành động; floor summary chỉ thêm counts/stage, không nhúng toàn bộ ticket.

### Step 5: Nối close/cancel với cùng invariant

Thay close guard tạm ở Task 1 bằng một query chung:

- `NEW`/`IN_PROGRESS` → `FNB_ACTIVE_TICKETS`;
- `DONE` và `served_at IS NULL` → `FNB_UNSERVED_TICKETS`;
- chỉ `CANCELLED` hoặc `DONE + served_at` không chặn.

Không đổi thứ tự các guard tiền/shift/check/allocation hiện hữu. Guard vé chạy trước mutation nhả bàn.

### Step 6: Chạy GREEN

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1b_send.py tests/test_fnb_r1b_cancel.py `
  tests/test_fnb_r1c_checkout.py tests/test_contract.py
git diff --check -- fselling/schemas/fnb.py fselling/routers/fnb.py fselling/services/fnb_service.py tests/test_fnb_r1b_send.py tests/test_fnb_r1b_cancel.py tests/test_fnb_r1c_checkout.py tests/test_contract.py
```

Expected: lifecycle, authorization, replay, rollback và close tests pass. Dừng checkpoint, chưa commit.

---

## Task 4: Cho phép gọi thêm món sau thanh toán một phần mà không sửa hóa đơn cũ

**Files:**

- Modify: `tests/test_fnb_r1a_sessions.py`
- Modify: `tests/test_fnb_r1b_send.py`
- Modify: `tests/test_fnb_r1c_checks.py`
- Modify: `tests/test_fnb_r1c_checkout.py`
- Modify: `fselling/services/fnb_service.py`

### Step 1: Viết test trạng thái session trước

Tạo session có hai check, thanh toán một check để trạng thái thành `PARTIALLY_SETTLED`, rồi khẳng định:

- add/update/cancel line chưa gửi và send món mới vẫn được phép;
- move table vẫn được phép; merge chỉ được phép khi target vượt guard Task 1;
- `PAYMENT_PENDING`, `CLOSED`, `CANCELLED` đều bị chặn;
- check/order/payment đã terminal giữ nguyên amount, status, voucher, loyalty và line allocations;
- món gửi sau vào một check `OPEN` mới, được đánh primary; check terminal cũ không bị mở lại;
- exact replay của send không tạo check hoặc line allocation trùng.

### Step 2: Chạy RED

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_sessions.py tests/test_fnb_r1b_send.py `
  tests/test_fnb_r1c_checks.py tests/test_fnb_r1c_checkout.py -k 'partial or additional_order'
```

Expected: mutation hiện bị `_require_open` chặn hoặc send cố dùng primary terminal.

### Step 3: Thêm hai helper hẹp

Không nới `_require_open` toàn cục. Thêm helper riêng cho service mutation:

```python
def _require_service_mutable(session):
    if session.status not in {"OPEN", "PARTIALLY_SETTLED"}:
        raise_fnb_conflict(
            "FNB_SESSION_NOT_SERVICEABLE",
            "Phiên bàn không còn nhận thay đổi phục vụ.",
            snapshot=serialize_session(session),
        )
```

Chỉ add/update/cancel line, send, move và source merge dùng helper này. `cancel_session` vẫn yêu cầu `OPEN`; pay/close giữ state machine hiện hữu.

Trước khi phân bổ sent quantity, lấy check OPEN primary. Nếu không có:

1. bỏ cờ primary khỏi check cũ nhưng không sửa status/amount/order/payment;
2. tạo một check OPEN mới với nhãn ổn định `Bill bổ sung N`;
3. đánh check mới primary;
4. gắn sent quantity mới vào check mới.

Không “reopen” check terminal và không clone order.

### Step 4: Chạy GREEN và money regressions gần nhất

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_fnb_r1a_sessions.py tests/test_fnb_r1b_send.py `
  tests/test_fnb_r1c_checks.py tests/test_fnb_r1c_checkout.py `
  tests/test_cashier_checkout.py tests/test_shifts.py tests/test_vouchers.py tests/test_loyalty_shop_policy.py
git diff --check -- fselling/services/fnb_service.py tests/test_fnb_r1a_sessions.py tests/test_fnb_r1b_send.py tests/test_fnb_r1c_checks.py tests/test_fnb_r1c_checkout.py
```

Expected: partial-settlement tests và các money regressions pass. Dừng checkpoint, chưa commit.

---

## Task 5: Sửa recovery của màn phục vụ thành một pending envelope rõ ràng

**Files:**

- Modify: `tests/js/fnb-r1a.test.js`
- Modify: `tests/test_fnb_r1a_ui.py`
- Modify: `static/js/fnb-r1a.js`
- Modify: `static/js/locales/fnb.js`

### Step 1: Viết Node tests cho lỗi gốc

Thêm test cho:

- mutation B trong lúc A pending không được gửi và không tự retry A;
- nút “Thử lại thao tác đang chờ” gửi lại đúng method/path/body/operation ID của A;
- reload khôi phục pending envelope không nhạy cảm và reconcile session trước khi retry;
- `approval_token`, manager PIN và payload approval không bao giờ vào `sessionStorage`;
- conflict move/merge/send/serve không đi qua nhánh `addLine`/`updateLine`;
- definitive 4xx xóa pending; network/5xx giữ pending; exact replay trả success một lần.

### Step 2: Chạy RED

```powershell
node --check static/js/fnb-r1a.js
node tests/js/fnb-r1a.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_fnb_r1a_ui.py::test_fnb_controller_node_harness
```

Expected: test mới bắt việc `startMutation()` đang gọi lại pending cũ và `reapply` đang định tuyến sai.

### Step 3: Dùng một envelope, không hàng đợi

Envelope tối thiểu:

```javascript
{
  kind: "send-session",
  method: "POST",
  path: "/api/fnb/sessions/123/send",
  body: { expected_session_revision: 8, operation_id: "5b8aa29e-16b7-4c3a-9840-1daf1247580c" },
  sessionId: 123,
  shopId: 9
}
```

`startMutation()` khi đã có pending chỉ render cảnh báo và trả về. `retryPending()` là đường duy nhất phát lại. `reconcilePending()` reload đúng shop/session, rồi:

- nếu read model chứng minh operation đã áp dụng, xóa pending và hiển thị thành công;
- nếu chưa chứng minh được, giữ pending và yêu cầu người dùng bấm retry;
- không tự phát lại mutation phá hủy sau reload.

Serializer persistence phải từ chối mọi envelope có `approval_token`, PIN hoặc kind approval/cancel progressed. Các thao tác đó yêu cầu approval mới sau reload.

### Step 4: Chạy GREEN

```powershell
node --check static/js/fnb-r1a.js
node tests/js/fnb-r1a.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_fnb_r1a_ui.py
git diff --check -- static/js/fnb-r1a.js static/js/locales/fnb.js tests/js/fnb-r1a.test.js tests/test_fnb_r1a_ui.py
```

Expected: syntax, Node harness và UI contract pass. Dừng checkpoint, chưa commit.

---

## Task 6: Hiển thị READY/SERVED trên màn phục vụ và floor

**Files:**

- Modify: `static/fnb.html`
- Modify: `static/css/fnb-r1a.css`
- Modify: `static/js/fnb-r1a.js`
- Modify: `static/js/locales/fnb.js`
- Modify: `tests/js/fnb-r1a.test.js`
- Modify: `tests/test_fnb_r1a_ui.py`

### Step 1: Viết UI contract tests

Khẳng định controller/DOM có:

- nhóm “Đang chờ bếp/bar”, “Sẵn sàng giao”, “Đã giao”;
- nút “Đã giao” chỉ ở ticket READY và gửi ticket/session revision + operation ID;
- floor card có chip count/stage, không dùng màu làm tín hiệu duy nhất;
- close disabled và giải thích khi còn active/ready ticket;
- `aria-live` cho mutation/recovery status, focus hợp lý sau dialog;
- bản dịch Việt/Anh đủ key, không render raw key;
- asset query version được tăng đồng bộ sau sửa JS/CSS.

### Step 2: Chạy RED

```powershell
node tests/js/fnb-r1a.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_fnb_r1a_ui.py
```

Expected: thiếu DOM IDs/keys/stage renderer.

### Step 3: Thêm layout nhỏ nhất

Giữ bố cục hai cột desktop và footer mobile hiện hữu. Trong session pane, thêm một status strip phía trên bill:

```text
┌ Bàn A3 · 4 món ─────────────────────┐
│ Chờ bếp 1 │ Đang làm 1 │ Sẵn sàng 2 │
├──────────────────────────────────────┤
│ Sẵn sàng giao                       │
│ 2× Phở bò — Bếp        [Đã giao]    │
└──────────────────────────────────────┘
```

Ở 390px, ticket card một cột, CTA full width và touch target tối thiểu 44px. Không tạo tab/route mới; render từ session snapshot hiện có.

### Step 4: Nối API và close explanation

Handler “Đã giao” dùng pending envelope Task 5. Sau success, reload session/floor theo revision. Khi close trả `FNB_ACTIVE_TICKETS` hoặc `FNB_UNSERVED_TICKETS`, giữ bàn đang mở, focus status block và chỉ ra nhóm blocker; không tự chuyển KDS state.

### Step 5: Chạy GREEN

```powershell
node --check static/js/fnb-r1a.js
node tests/js/fnb-r1a.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_fnb_r1a_ui.py
git diff --check -- static/fnb.html static/css/fnb-r1a.css static/js/fnb-r1a.js static/js/locales/fnb.js tests/js/fnb-r1a.test.js tests/test_fnb_r1a_ui.py
```

Expected: UI contract pass; versioned assets thống nhất. Dừng checkpoint, chưa commit.

---

## Task 7: Làm KDS chống late response và hết món có đường phục hồi

**Files:**

- Modify: `static/fnb-station.html`
- Modify: `static/css/fnb-station-r1b.css`
- Modify: `static/js/fnb-station-r1b.js`
- Modify: `static/js/locales/fnb.js`
- Modify: `tests/js/fnb-station-r1b.test.js`
- Modify: `tests/test_fnb_r1a_ui.py`

### Step 1: Viết Node tests

Thêm các test:

- response của shop/station cũ đến muộn không được ghi đè queue mới;
- chọn action B khi A pending không phát lại A;
- explicit retry giữ nguyên operation ID;
- “Báo hết món” làm nút “Hoàn tất” disabled và hiện lý do;
- “Tiếp tục chế biến” gọi resume rồi mới cho start/done hợp lệ;
- online/offline/loading/error cập nhật badge text + timestamp; không giữ `● Đang cập nhật` màu xanh khi request lỗi.

### Step 2: Chạy RED

```powershell
node --check static/js/fnb-station-r1b.js
node tests/js/fnb-station-r1b.test.js
```

Expected: late response và implicit replay tests fail.

### Step 3: Thêm request epoch và explicit recovery

Tái dùng pattern `requestEpoch` từ `fnb-r1a.js`, không trích shared module mới. Mỗi đổi shop/station hoặc start lại view tăng epoch; response chỉ áp dụng khi epoch và shop/station key còn khớp.

Giữ một pending mutation trong RAM; khi có pending, action khác bị khóa với thông báo rõ. Chỉ nút retry gửi lại exact request. KDS không cần persistence qua reload vì station có thể GET queue để reconcile.

Đổi copy `DONE` từ “Hoàn tất” thành “Sẵn sàng giao”; server vẫn giữ status `DONE`.

### Step 4: Chạy GREEN

```powershell
node --check static/js/fnb-station-r1b.js
node tests/js/fnb-station-r1b.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_fnb_r1a_ui.py
git diff --check -- static/fnb-station.html static/css/fnb-station-r1b.css static/js/fnb-station-r1b.js static/js/locales/fnb.js tests/js/fnb-station-r1b.test.js tests/test_fnb_r1a_ui.py
```

Expected: Node/UI tests pass và asset query version được bump. Dừng checkpoint, chưa commit.

---

## Task 8: Focused release gate, browser UAT và handoff commit

**Files:**

- Create: `FNB_PLAN2_UAT_REPORT.md`
- Verify: toàn bộ file trong File Map; tuyệt đối không stage năm file Seller được bảo vệ

### Step 1: Chạy static/focused gate một lần

```powershell
.\.venv\Scripts\python.exe -B -m py_compile fselling/models/fnb.py fselling/schemas/fnb.py fselling/routers/fnb.py fselling/services/fnb_service.py migrations/versions/0012_fnb_ticket_service_handoff.py
node --check static/js/fnb-r1a.js
node --check static/js/fnb-station-r1b.js
node tests/js/fnb-r1a.test.js
node tests/js/fnb-station-r1b.test.js
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q -p no:cacheprovider `
  tests/test_migration_0012_fnb_ticket_service_handoff.py `
  tests/test_migration_i04.py::test_linear_graph_and_checksum_manifest `
  tests/test_migration_i05.py::test_fresh_0001_0002_0003_and_i04_upgrade_are_linear `
  tests/test_fnb_r1a_sessions.py tests/test_fnb_r1b_send.py tests/test_fnb_r1b_cancel.py `
  tests/test_fnb_r1c_checks.py tests/test_fnb_r1c_checkout.py `
  tests/test_cashier_checkout.py tests/test_shifts.py tests/test_vouchers.py `
  tests/test_loyalty_shop_policy.py tests/test_reconciliation.py `
  tests/test_staff_roles.py tests/test_contract.py tests/test_fnb_r1a_ui.py
```

Chỉ ghi “pass” khi process đạt 100% và exit code 0. Nếu fail, dùng systematic debugging và chạy lại test hẹp bị ảnh hưởng, không chạy lặp toàn bộ gate.

### Step 2: Browser UAT bằng dữ liệu demo cô lập

Không dùng database sản xuất. Với local copied/migrated demo DB, kiểm tra tối thiểu:

| Flow | Viewports | Expected |
|---|---|---|
| Gửi món → KDS nhận → sẵn sàng → phục vụ giao | 390×844, 1024×768, 1366×768 | revision đồng bộ; không mất bàn/tên bàn |
| Hết món → tiếp tục → sẵn sàng | KDS desktop + tablet | done bị khóa khi còn reason; resume rõ ràng |
| Thanh toán một phần → gọi thêm → thanh toán tiếp | 390×844 + desktop | bill cũ bất biến; bill bổ sung nhận món mới |
| Mất mạng sau send/serve | 390×844 | một pending; explicit retry cùng operation ID; không nhân đôi |
| Đổi shop/station lúc request KDS chậm | KDS desktop | response cũ bị bỏ |
| Merge target có artifact | desktop | conflict, cả hai bàn/session nguyên trạng |
| Close còn NEW/IN_PROGRESS/READY | mobile + desktop | bị chặn với lý do; bàn không nhả |
| Close sau SERVED và payment terminal | mobile + desktop | đóng đúng một lần |

Kiểm tra keyboard-only, focus return, 200% zoom, contrast/text signal, long Vietnamese copy và touch target 44px. Chụp bằng chứng trước/sau cho từng P0/P1.

### Step 3: Viết UAT report

`FNB_PLAN2_UAT_REPORT.md` phải ghi:

- commit/baseline và môi trường demo;
- exact commands, exit codes, pass counts, warnings;
- browser/viewports/role × device × task;
- ảnh/bằng chứng và giới hạn chưa kiểm chứng;
- xác nhận năm file Seller không đổi;
- xác nhận không gọi provider/dữ liệu thật.

### Step 4: Kiểm tra scope trước full suite

```powershell
git status --short
git diff --check
git diff --name-only
git diff --name-only -- static/css/seller.css static/js/locales/seller.js static/js/seller.js static/seller.html tests/test_seller_sidebar_ui.py
```

Expected: lệnh cuối không in gì; diff chỉ chứa File Map đã duyệt.

### Step 5: Dừng và yêu cầu chủ dự án chạy full-suite gate

Gửi đúng lệnh, không tự chạy:

```powershell
.\test-commit.ps1 -TestOnly
```

Chỉ tiếp tục khi chủ dự án báo cùng checkout, không có thay đổi xen giữa, exit code 0. Nếu có thay đổi hoặc kết quả không rõ, xác minh lại status và focused tests bị ảnh hưởng; không tự suy diễn pass.

### Step 6: Stage chính xác và commit sau khi được xác nhận

Sau approval riêng cho commit, stage bằng danh sách path rõ ràng, không dùng `git add -A`. Xác minh `git diff --cached --name-only` không chứa file ngoài scope, rồi commit một lần với message đã duyệt, ví dụ:

```powershell
git commit -m "feat(fnb): complete kitchen service table lifecycle"
```

Không push, mở PR hoặc deploy nếu chưa có yêu cầu riêng.

## Stop Conditions During Execution

Dừng và báo chủ dự án nếu xảy ra một trong các tình huống:

- baseline không còn là hậu duệ nguyên vẹn của `7c17fff` hoặc worktree có thay đổi ngoài scope;
- cần sửa một trong năm file Seller được bảo vệ;
- migration không còn tuyến tính/checksum không xác minh được;
- giải pháp yêu cầu thay đổi contract tiền, shift, voucher, loyalty, approval hoặc provider;
- read model không đủ để reconcile exact operation ID mà phải tự động phát lại mutation phá hủy;
- cần thêm role `WAITER` để hoàn tất flow — tách thành plan khác thay vì lách permission hiện hữu.

## Definition of Done

- Hai P0 có regression test và fail closed trước mutation.
- Vé có luồng quan sát được `NEW → IN_PROGRESS → READY → SERVED` mà DB vẫn dùng state machine R1.
- Ticket mutation tăng ticket/session/shop revisions và giữ exact replay.
- Close không nhả bàn khi còn active/ready ticket; merge không làm lệch session graph.
- Thanh toán một phần vẫn gọi thêm món được mà không sửa hóa đơn/order/payment terminal.
- Main UI và KDS không implicit retry action cũ, không nhận late response sai shop/station và không persist approval secret.
- Focused gate + browser UAT có bằng chứng; full suite do chủ dự án chạy một lần và báo exit code 0.
- Năm file Seller không đổi; không dependency mới, không full-suite tự chạy, không push/PR/deploy.
