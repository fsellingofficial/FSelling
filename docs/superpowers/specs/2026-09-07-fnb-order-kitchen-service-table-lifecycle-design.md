# F&B Plan 2 — Đặc tả vòng đời món, bếp/bar, phục vụ và bàn

Ngày: 2026-09-07
Trạng thái: Chờ chủ dự án duyệt; chưa triển khai
Baseline: `7c17fff097f3892ca41b6d962f17ed9348dd819d` trên nhánh nguồn `codex/fnb-safety-hotfix-r1`

## 1. Baseline và phạm vi bằng chứng

Baseline đã được xác minh trước khi đọc source:

- `HEAD` đúng bằng `7c17fff`; local branch và remote-tracking branch
  `codex/fnb-safety-hotfix-r1` cũng cùng trỏ tới commit này.
- Worktree là linked worktree cô lập: `GIT_DIR` nằm tại
  `.git/worktrees/python_app1`, khác `GIT_COMMON` của checkout chính.
- `git status --short --branch` chỉ trả `## HEAD (no branch)`: worktree sạch,
  đang detached tại đúng commit của Plan 1.
- Không chạy app, không đọc database, secret, `.env`, log hoặc dữ liệu thật;
  không gọi dịch vụ ngoài và không chạy full suite.
- Năm file Seller được bảo vệ không được đọc để suy diễn UI mới và không được
  đưa vào danh sách file triển khai: `static/css/seller.css`,
  `static/js/locales/seller.js`, `static/js/seller.js`, `static/seller.html`,
  `tests/test_seller_sidebar_ui.py`.

Nguồn bằng chứng chính là code và test tại baseline. `FNB_R1_UAT_REPORT.md`,
đặc tả R1 và plan R1 chỉ được dùng để khóa các invariant đã hoàn tất, không dùng
để thay thế hành vi đọc từ source.

## 2. Mục tiêu và nguyên tắc tối thiểu

Plan 2 phải làm cho mỗi vai trò trả lời được ba câu hỏi mà không đoán:

1. Món đang ở đâu và ai cần hành động tiếp?
2. Thao tác vừa rồi đã ghi nhận, bị từ chối hay chưa biết kết quả?
3. Bàn có thể chuyển, thanh toán hoặc đóng an toàn chưa?

Giải pháp đề xuất giữ nguyên các bảng và biên R1, chỉ bổ sung hai dấu mốc bền
vững `served_by_user_id` và `served_at` trên ticket. Không dựng framework state
machine, websocket, event bus, hàng đợi offline nhiều thao tác hoặc thiết kế lại
thẩm mỹ. `DONE` tiếp tục là trạng thái bếp/bar hoàn tất; trạng thái phục vụ được
suy ra là `READY` khi `DONE` nhưng chưa giao, và `SERVED` khi có `served_at`.

Mọi mutation tiếp tục dùng:

- shop write lock trước khi kiểm lại quyền/trạng thái;
- operation ID + fingerprint để exact replay và chặn collision;
- optimistic concurrency bằng session/check/table/ticket revision;
- một transaction cho mutation, audit và side effect;
- snapshot server là nguồn sự thật sau conflict;
- không optimistic-success cho tiền, tồn, hủy, chuyển bàn hoặc phục vụ món.

## 3. Ba phương án đã cân nhắc

### A. Dấu mốc phục vụ cộng với trạng thái suy ra — khuyến nghị

Thêm `served_by_user_id`/`served_at` vào ticket; giữ `NEW → IN_PROGRESS → DONE`,
suy ra `READY/SERVED`, đưa ticket chưa giao vào snapshot phiên, và thêm một
endpoint xác nhận đã giao. Đây là thay đổi nhỏ nhất tạo được biên bền vững giữa
“bếp đã làm xong” và “khách đã nhận”, đồng thời cho phép chặn đóng bàn đúng chỗ.

### B. State machine ở từng ticket item

Thêm trạng thái, version, lý do và timestamp cho từng `FnbKitchenTicketItem`.
Phương án này hỗ trợ một ticket có nhiều món hoàn tất/hết món từng phần chính
xác hơn, nhưng tăng migration, API, UI và số conflict đáng kể. Chưa có bằng
chứng vận hành buộc phải trả chi phí đó trong Plan 2.

### C. Chỉ sửa frontend từ `DONE` hiện có

Hiển thị `DONE` như “sẵn sàng” nhưng không ghi nhận “đã giao”. Phương án này
không thể chặn đóng bàn, không phục hồi sau reload và không tạo audit người giao;
do đó bị loại.

## 4. Bản đồ trạng thái hiện tại

### 4.1 Backend

| Thực thể | Trạng thái hiện có | Mutation chính | Bảo vệ hiện có |
|---|---|---|---|
| Phiên phục vụ | `OPEN`, `PARTIALLY_SETTLED`, `PAYMENT_PENDING`, `CLOSED`, `CANCELLED` | mở, chuyển/gộp bàn, hủy, đóng | `session.revision`, shop lock, operation log (`fselling/models/fnb.py:50-68`) |
| Bàn | `EMPTY/SERVING` được suy ra từ active link | mở, chuyển, gộp, release | `table.state_version` (`fselling/models/fnb.py:33-47`) |
| Dòng món | nháp/gửi/hủy được suy từ bốn quantity | thêm, sửa, gửi, hủy | `line.state_version`, session revision (`fselling/models/fnb.py:82-106`) |
| Ticket bếp/bar | `NEW`, `IN_PROGRESS`, `DONE`, `CANCELLED` | nhận làm, hoàn tất, báo hết | `ticket.state_version`, operation log (`fselling/models/fnb.py:135-158`) |
| Allocation | `CONSUMED`, `RESTOCKED`, `TRANSFERRED_TO_ORDER`, `WASTE` | gửi, hủy, thanh toán | provenance theo lô và operation ID (`fselling/models/fnb.py:172-189`) |
| Bill | `OPEN`, `PAYING`, `PAYMENT_PENDING`, `PAID`, `DEBT`, `CANCELLED` | tách, điều chỉnh, thanh toán | check revision + session revision (`fselling/models/fnb.py:208-246`) |

Luồng thật hiện tại:

```text
EMPTY table
  -> open_session -> OPEN session + primary OPEN check
  -> add/update draft line
  -> send_session
       -> stock deducted + allocation CONSUMED
       -> KITCHEN/BAR ticket NEW; DIRECT không có ticket
       -> line quantity được gắn vào primary check
  -> ticket start: NEW -> IN_PROGRESS
  -> ticket done: IN_PROGRESS -> DONE và biến mất khỏi KDS
  -> pay_check: check -> PAID / DEBT / PAYMENT_PENDING
  -> close_session: terminal checks + no unsent + no CONSUMED allocation
  -> CLOSED + release table
```

Khoảng trống cốt lõi: `DONE` không xuất hiện trong snapshot phục vụ và không có
dấu mốc khách đã nhận. Vì vậy backend hiện không thể phân biệt “bếp xong” với
“đã phục vụ”.

### 4.2 Frontend

- Màn phục vụ poll floor 2 giây khi visible, 10 giây khi hidden; chỉ reload
  session khi `session.revision` trong floor summary đổi
  (`static/js/fnb-r1a.js:83-84`, `129-178`).
- Dòng món chỉ được chia “chưa gửi” và “đã gửi”; không có đang làm/sẵn
  sàng/đã giao (`static/js/fnb-r1a.js:16-20`, `843-878`).
- Floor card chỉ có `Trống/Đang phục vụ`, tổng tiền và số chưa gửi
  (`static/js/fnb-r1a.js:755-772`).
- KDS có hai lane `Mới/Đang làm`; bấm `Hoàn tất` làm ticket biến mất
  (`static/js/fnb-station-r1b.js:131-161`).
- Cả hai controller giữ một mutation pending trong RAM và exact operation ID
  khi còn trang; không có outbox phục hồi sau reload
  (`static/js/fnb-r1a.js:67-80`, `387-400`;
  `static/js/fnb-station-r1b.js:17-20`, `46-86`).

## 5. Invariant đích

### 5.1 Tiền, bill và allocation — không được suy yếu R1

1. Thanh toán không thay đổi ticket preparation/service state.
2. Phục vụ món không thay đổi `Order`, payment, voucher, điểm, ca tiền hoặc
   allocation.
3. Allocation chỉ chuyển khỏi `CONSUMED` bằng hủy hợp lệ hoặc chuyển provenance
   sang order; retry không nhân đôi.
4. Dòng đã chuyển vào order đã thanh toán không được hủy qua F&B line cancel;
   phải đi đường trả hàng/hoàn tiền hiện có.
5. Cash tender null/short, non-cash tender, payment pending, revision và
   operation collision tiếp tục fail-closed đúng R1.

### 5.2 Chế biến và phục vụ

1. `NEW → IN_PROGRESS → DONE` chỉ do đúng quyền KITCHEN/BAR của ticket thực
   hiện.
2. `DONE + served_at is NULL` được trả ra API là `READY`.
3. `DONE + served_at is not NULL` được trả ra API là `SERVED`.
4. Chỉ quyền `FNB_SERVICE` được xác nhận `READY → SERVED`; request phải mang
   cả `expected_state_version`, `expected_session_revision` và operation ID.
5. `served_at` và `served_by_user_id` cùng null hoặc cùng có giá trị; `served_at`
   chỉ hợp lệ khi ticket `DONE`.
6. Ticket `NEW/IN_PROGRESS/READY` làm table không được đóng. Ticket `SERVED`
   không chặn đóng.
7. Hủy món đã `SERVED` không được `RESTOCK`; nếu nghiệp vụ cho phép hủy thì chỉ
   `WASTE` với reason + manager approval như R1.
8. Mỗi ticket transition làm tăng cả `ticket.state_version`, `session.revision`
   và `shop.fnb_revision`, để mọi thiết bị thấy thay đổi.

### 5.3 Phiên và bàn

1. `PAYMENT_PENDING` đóng băng mutation thay đổi món/bill; KDS vẫn được hoàn
   tất và phục vụ vẫn được xác nhận giao.
2. `PARTIALLY_SETTLED` không tự biến thành dead end: có thể tiếp tục phục vụ,
   chuyển bàn và thêm món vào bill primary đang mở. Nếu không còn bill primary
   mở, `send_session` tạo đúng một `Bill bổ sung` mới trong cùng transaction.
3. Gộp một bàn trống vào source session vẫn hợp lệ.
4. Gộp target session chỉ hợp lệ nếu target có đúng dữ liệu nháp R1A: không
   sent quantity, ticket, allocation, check line, order hoặc bill ngoài primary
   `OPEN` rỗng. Trường hợp khác phải trả `FNB_TARGET_SESSION_HAS_ARTIFACTS` và
   không đổi bất kỳ link/line/revision nào.
5. Chuyển bàn giữ nguyên session/ticket/check/allocation; ticket luôn hiển thị
   tên bàn active mới từ server.
6. Đóng bàn chỉ khi mọi bill terminal, không còn unsent, không còn allocation
   `CONSUMED`, và mọi ticket đã `SERVED` hoặc `CANCELLED`.

### 5.4 Retry, conflict và mất mạng

1. Một màn chỉ có tối đa một mutation chưa xác định kết quả.
2. Bấm một action khác khi còn pending không được âm thầm replay action cũ;
   UI phải chỉ rõ action cũ và cho `Thử lại đúng thao tác` hoặc `Kiểm tra trạng thái`.
3. Retry dùng nguyên endpoint, method, payload và operation ID.
4. Payload không chứa PIN/approval token được lưu trong `sessionStorage` theo
   username + shop + session/station. Không xây queue nhiều phần tử.
5. Cancellation có approval token không ghi token/PIN vào storage. Sau reload,
   client chỉ GET snapshot: nếu chưa hủy thì buộc tạo approval mới.
6. Response 4xx dứt khoát xóa pending. Network/5xx giữ pending. Conflict dùng
   snapshot mới và không tự retry destructive action.
7. Đổi shop/station tăng request epoch; response cũ không được ghi đè queue của
   shop/station mới.
8. Không action tài chính/tồn/hủy nào báo success trước response server.

## 6. Findings và gap P0–P3

### P0 — phải chặn trước khi mở rộng lifecycle

| ID | Finding | Bằng chứng source/test | Tác động |
|---|---|---|---|
| P0-1 | `session_has_only_r1a_drafts()` luôn trả `True` dù R1B/R1C đã có ticket, allocation và check. `merge_table` sau đó chỉ chuyển `FnbSessionLine.session_id`, nhưng để ticket/check/allocation ở target rồi hủy target. | `fselling/services/fnb_service.py:2766-2770`, `2842-2869`; verifier yêu cầu ticket item/check/allocation cùng session tại `migrations/versions/0010_fnb_kitchen_stock_r1b.py:238-265` và `0011_fnb_checkout_r1c.py:164-198`; test merge chỉ tạo draft tại `tests/test_fnb_r1a_sessions.py:380-428`. | Có thể làm lệch bill, ticket, allocation và khiến readiness fail-closed ở lần verify sau; món/tồn/tiền bị mắc ở phiên đã `CANCELLED`. |
| P0-2 | `close_session` không kiểm ticket active/ready. Sau thanh toán, allocation đã chuyển sang order nên ba gate hiện có đều có thể qua trong khi bếp còn `NEW/IN_PROGRESS`. | Gate đóng chỉ kiểm bill, unsent và `CONSUMED` tại `fselling/services/fnb_service.py:1771-1805`; UI bật nút đóng chỉ theo trạng thái bill tại `static/js/fnb-r1a.js:938-953`; ticket active vẫn được KDS lấy tại `fselling/services/fnb_service.py:2275-2290`. | Bàn bị phát hành cho khách mới trong khi bếp đang làm món của khách cũ; KDS mất tên bàn vì active link đã release. |

### P1 — gây kẹt hoặc mất phối hợp thường xuyên

| ID | Finding | Bằng chứng | Tác động |
|---|---|---|---|
| P1-1 | Không có biên bền vững `READY/SERVED`; `DONE` biến mất khỏi KDS và session snapshot không chứa ticket. | `fselling/models/fnb.py:149-158`; `fselling/services/fnb_service.py:814-884`, `2275-2290`; `static/js/fnb-station-r1b.js:57-60`. | Phục vụ không biết món nào cần lấy, chủ shop không biết bàn nào đang chờ, backend không thể chặn close đúng. |
| P1-2 | Ticket transition chỉ tăng shop revision, không tăng session revision; main controller chỉ reload session khi summary revision đổi. | `fselling/services/fnb_service.py:2339-2371`; `static/js/fnb-r1a.js:129-141`. | Bếp có đổi trạng thái nhưng điện thoại phục vụ đang mở bàn vẫn giữ snapshot cũ. |
| P1-3 | Báo hết món chỉ ghi một chuỗi ở cấp ticket rồi ticket vẫn có thể `start/done`; test hiện còn khóa hành vi đó. | `fselling/services/fnb_service.py:2347-2367`; `static/fnb-station.html:27-33`; `tests/test_fnb_r1b_send.py:121-165`. | Cảnh báo không có owner/recovery rõ; bếp có thể đánh dấu xong ticket đang báo hết, phục vụ không được thông báo trong màn bàn. |
| P1-4 | Recovery chung chỉ hiểu add/update line. Conflict của move/merge có nút `reapply`, nhưng handler không có nhánh tương ứng và rơi vào `addLine`; lỗi mạng send/close không có CTA retry rõ. | `static/js/fnb-r1a.js:336-375`, `499-568`, `1254-1260`. | Người dùng bấm “áp dụng lại” nhưng gửi request không liên quan hoặc bị mắc với pending ẩn. |
| P1-5 | Khi còn pending, bấm action khác tự gọi lại pending cũ; KDS cũng có hành vi tương tự. | `static/js/fnb-r1a.js:387-400`; `static/js/fnb-station-r1b.js:73-86`. | Người dùng nghĩ đang thao tác món B nhưng hệ thống retry món A; vi phạm match với ý định vật lý. |
| P1-6 | KDS không có request epoch; response của shop cũ có thể ghi đè queue sau khi đổi shop. | `static/js/fnb-station-r1b.js:23-43`, `89-95`, `210-226`; đối chiếu main controller đã có epoch tại `static/js/fnb-r1a.js:74`, `147-153`. | Seller/manager nhiều shop có thể nhìn và thao tác nhầm ticket shop cũ dưới nhãn shop mới. |
| P1-7 | `PARTIALLY_SETTLED` nằm trong active statuses nhưng `_require_open` chỉ nhận đúng `OPEN`; add/send/move/merge/cancel-line đều dùng guard này. | `fselling/services/fnb_service.py:71`, `1867-1869`, `2001-2003`, `2105-2106`, `2408-2419`, `2587-2589`, `2710-2711`, `2797-2798`. | Sau khi một bill đã trả còn bill/món khác, bàn có thể bị khóa khỏi vòng phục vụ tiếp theo. |
| P1-8 | Không có preset phục vụ riêng; `CASHIER` đồng thời có `FNB_SERVICE` và `FNB_CHECKOUT`. | `fselling/dependencies.py:16-29`, `44-76`; schema role tại `fselling/schemas/staff.py:5-24`. | Không cấp được quyền gọi/giao món mà không đồng thời cấp quyền thanh toán. Plan 2 không thể sửa vì UI Seller thuộc năm file đang được bảo vệ. |

### P2 — giảm tốc độ và độ tin cậy cảm nhận

- Floor chỉ cho thấy chưa gửi, không cho thấy đang làm/sẵn sàng/hết món
  (`static/js/fnb-r1a.js:755-772`).
- KDS badge `● Đang cập nhật` là nội dung tĩnh và không đổi khi offline/error
  (`static/fnb-station.html:21-25`; `static/js/fnb-station-r1b.js:150-170`).
- Main controller lưu draft theo session nhưng pending exact request chỉ ở RAM;
  reload làm mất operation ID (`static/js/fnb-r1a.js:98-118`, `387-400`).
- `out_of_stock_reason` không có actor/timestamp riêng; audit operation có actor
  nhưng UI không trình bày (`fselling/models/fnb.py:149-158`,
  `fselling/services/fnb_service.py:2370-2382`).

### P3 — chỉ làm khi có bằng chứng vận hành

- Phân trang/virtualize KDS cho hàng trăm ticket; hiện chưa có dữ liệu volume.
- Websocket/push thay polling 2 giây; polling hiện hữu đủ cho Plan 2 nếu trạng
  thái đúng và stale state được ghi rõ.
- Item-level preparation/out-of-stock; chỉ nâng từ ticket-level khi UAT chứng
  minh một ticket hỗn hợp thường hoàn tất từng phần.
- Âm thanh/voice/haptic KDS; cần thiết bị thật và quyền trình duyệt trước khi
  đưa vào scope.

## 7. Thiết kế backend đề xuất

### 7.1 Migration cộng thêm, không rebuild state machine

Revision mới `0012_fnb_ticket_service_handoff` thêm hai cột nullable:

```text
fnb_kitchen_tickets.served_by_user_id -> users.id
fnb_kitchen_tickets.served_at         -> DATETIME
```

Verifier bắt buộc:

```text
(served_by_user_id IS NULL) = (served_at IS NULL)
served_at IS NULL OR status = 'DONE'
served_by_user_id IS NULL OR user tồn tại
```

Không backfill `served_at`: ticket lịch sử `DONE` không được bịa thành đã giao.
Chỉ ticket thuộc active session mới tham gia close gate; ticket lịch sử không
làm khóa dữ liệu cũ.

### 7.2 Read model phiên

`serialize_session()` bổ sung:

```json
{
  "service_stage": "BLOCKED|READY|PREPARING|TO_SEND|PAYMENT|SETTLED|SERVING",
  "service_summary": {
    "new": 0,
    "in_progress": 1,
    "ready": 1,
    "blocked": 0
  },
  "service_tickets": [
    {
      "id": 42,
      "station": "KITCHEN",
      "sequence": 18,
      "service_status": "READY",
      "state_version": 3,
      "out_of_stock_reason": null,
      "items": [{"line_id": 9, "product_name": "Cơm bò", "quantity": 2}]
    }
  ]
}
```

`service_tickets` chỉ gồm ticket còn cần hành động: `NEW`, `IN_PROGRESS`,
`DONE` chưa served hoặc có cảnh báo hết món. Ticket served không cần gửi lại
trong snapshot active; audit/history vẫn nằm trong DB.

Thứ tự ưu tiên stage của floor:

```text
BLOCKED > READY > PAYMENT > PREPARING > TO_SEND > SETTLED > SERVING
```

Stage là read model, không phải cột trạng thái mới.

### 7.3 Endpoint lifecycle

Giữ ba route hiện có và thêm hai route rõ nghĩa:

```text
POST /api/fnb/tickets/{ticket_id}/serve
  permission: FNB_SERVICE
  body: expected_state_version, expected_session_revision, operation_id
  precondition: ticket.status == DONE, served_at is NULL, no out_of_stock_reason

POST /api/fnb/tickets/{ticket_id}/resume
  permission: đúng FNB_KITCHEN/FNB_BAR của ticket
  body: expected_state_version, operation_id
  precondition: out_of_stock_reason is not NULL, ticket NEW/IN_PROGRESS
```

`done` bị từ chối bằng `FNB_TICKET_BLOCKED` khi còn `out_of_stock_reason`.
`resume` chỉ xóa cảnh báo; không hoàn tồn và không tự tạo lại món. Việc hủy món
vẫn đi qua line cancellation + allocation + approval R1.

Mọi transition trả `session_revision` và `revision` của shop. Conflict ticket
trả snapshot ticket; conflict session khi serve trả snapshot session.

### 7.4 Gộp, thanh toán một phần và đóng bàn

- Thay helper giả `session_has_only_r1a_drafts()` bằng các query fail-closed.
- Cho add/send/update/cancel-line/move hoạt động khi session `OPEN` hoặc
  `PARTIALLY_SETTLED`; vẫn chặn `PAYMENT_PENDING`.
- Primary check là bill nhận món mới. Khi primary đã terminal và có món mới cần
  gửi, server tạo một primary `Bill bổ sung` mới trong cùng transaction, sau khi
  hạ cờ primary cũ. Không sửa bill/order đã terminal.
- Close gate query ticket của session: chỉ cho qua nếu mọi ticket `DONE` và
  `served_at` có giá trị, hoặc ticket `CANCELLED`.
- Merge target có artifact trả 409 trước bất kỳ update nào. Plan 2 không chuyển
  ownership của ticket/check/allocation giữa hai session.

## 8. Luồng và wireframe đủ triển khai P0/P1

### 8.1 Phục vụ — điện thoại một tay

```text
┌──────────────────────────────────┐
│ ← Bàn        Bàn 12       Mạng ● │
├──────────────────────────────────┤
│ CẦN XỬ LÝ                        │
│ Bar #18 · Hết Trà đào            │
│ [Mở món để xử lý]                │
├──────────────────────────────────┤
│ SẴN SÀNG · 2                     │
│ Bếp #21  2× Cơm bò               │
│                 [Đã giao khách]  │
├──────────────────────────────────┤
│ ĐANG LÀM · 1                     │
│ Bar #22   1× Cà phê sữa          │
├──────────────────────────────────┤
│ [Tìm món____________________]     │
│ [món] [món] [món] [món]         │
├──────────────────────────────────┤
│ Tạm tính 240.000đ                │
│ [Tính tiền] [Gửi 3 món]          │
└──────────────────────────────────┘
```

- Khối `CẦN XỬ LÝ` và `SẴN SÀNG` đứng trên menu/bill vì là việc cần phản ứng.
- CTA serve tối thiểu 44×44 px, text đầy đủ, không chỉ đổi màu.
- Bấm serve: pending ghi đúng ticket; success đổi stage; 409 tải snapshot và
  nói “Món vừa được cập nhật ở thiết bị khác”.
- Offline: giữ snapshot + timestamp, disable mutation, không giấu dữ liệu.

### 8.2 Bếp/bar — màn hình cố định nhìn xa

```text
┌──────────────────────────────────────────────────────────┐
│ BẾP · Cửa hàng A         Cập nhật 10:42:18 · Trực tuyến │
├───────────────────────────┬──────────────────────────────┤
│ MỚI · 4                   │ ĐANG LÀM · 3                │
│ #31  Bàn 5      03 phút   │ #28  Bàn 2       08 phút    │
│ 2× Cơm bò                 │ 1× Lẩu thái                 │
│ ít cay                    │ không hành                  │
│ [Nhận làm] [Báo hết món] │ [Sẵn sàng giao] [Báo hết]  │
└───────────────────────────┴──────────────────────────────┘
```

- Đổi copy `Hoàn tất` thành `Sẵn sàng giao`.
- Badge online/loading/stale/offline là trạng thái thật, kèm thời điểm snapshot.
- Pending banner gọi đúng ticket và có `Thử lại đúng thao tác`; click ticket
  khác không replay pending cũ.
- Đổi shop/station hủy hiệu lực response cũ bằng epoch.

### 8.3 Thu ngân — PC/tablet

Checkout R1 giữ nguyên. Bổ sung gate trước nút đóng:

```text
ĐÃ THANH TOÁN
Chưa thể đóng bàn: Bếp #21 đang làm, Bar #22 sẵn sàng chưa giao.
[Quay lại theo dõi món]              [Đóng bàn] (disabled)
```

Thanh toán trước khi món xong vẫn được phép; chỉ release bàn bị chặn.

### 8.4 Chủ shop — desktop/mobile

Floor card dùng một chip stage và một action count, không thêm dashboard mới:

```text
Bàn 12             SẴN SÀNG 2
42 phút · 240.000đ
Bar hết 1 món
```

Desktop vẫn dùng grid hiện có; mobile dùng cùng card co giãn. Setup và phân tích
chuyên sâu không nằm trong Plan 2.

## 9. State inventory UX/Fortify

| State | Người dùng thấy | Có thể làm | Phục hồi |
|---|---|---|---|
| Empty | Không có ticket/món, hướng dẫn action kế tiếp | mở bàn/chọn món | không dead end |
| Loading | skeleton đúng vùng, không xóa snapshot cũ khi refresh nền | chờ/đi màn khác | timeout chuyển stale |
| Pending | action + bàn/ticket cụ thể, controls liên quan khóa | chờ | sau timeout hiện retry/check |
| Success | động từ khớp CTA: “Đã gửi”, “Sẵn sàng”, “Đã giao” | làm bước kế | snapshot server mới |
| Validation 4xx | lỗi cụ thể tại control | sửa input | giữ dữ liệu không nhạy cảm |
| Conflict 409 | snapshot mới + action không tự replay | xem lại | tạo intent mới |
| Network/5xx | “Chưa biết kết quả” + operation đang giữ | retry exact/check | không success giả |
| Offline | snapshot gần nhất + thời điểm + banner | đọc; không mutation | online thì reconcile |
| Permission loss | ngừng poll/mutation, điều hướng về màn hợp lệ | đăng nhập/nhờ quản lý | không lộ dữ liệu khác shop |
| Overflow | card wrap, ticket list scroll; count `99+` | lọc station/area | không virtualize ở Plan 2 |

Điểm heuristic hiện tại: **5/10**. Hai P0 làm hỏng visibility/control ở luồng
cốt lõi; bốn diagnostic fail lớn là status món, error recovery, action đúng ý
định và close gate. Mục tiêu sau Plan 2 là **9/10**: không severity-4, mọi
mutation có state/recovery rõ, CTA chính nhìn thấy và touch target đạt 44 px.
Điểm 10/10 chỉ được tuyên bố sau browser UAT thật với bốn vai trò/thiết bị.

## 10. Ma trận kiểm thử

### 10.1 Focused backend

| Trục | Ca bắt buộc |
|---|---|
| Merge P0 | target draft-only thành công; target có sent/ticket/allocation/check line/order bị 409; stale target atomic; retry exact không nhân dòng/link |
| Close P0 | paid + NEW/IN_PROGRESS/READY bị chặn; served/cancelled cho đóng; direct-only không bị chặn; transfer pending vẫn bị chặn |
| Ticket | role/station scope; NEW→IN_PROGRESS→DONE; blocked không done; resume; serve chỉ từ DONE; stale ticket/session; exact replay/collision |
| Session propagation | mọi ticket transition tăng ticket/session/shop revision đúng một lần; GET floor/session phản ánh stage |
| Partial settlement | add/send/move khi `PARTIALLY_SETTLED`; không sửa paid order; supplemental primary check duy nhất; `PAYMENT_PENDING` vẫn khóa |
| Cancellation | NEW/IN_PROGRESS/DONE chưa giao giữ R1; served RESTOCK bị chặn; served WASTE cần approval; allocation/voucher/loyalty/shift không đổi ngoài contract |
| Migration | fresh upgrade; 0011→0012; interrupted statement rollback; verifier bắt cặp served fields sai; checksum/topology tuyến tính |

### 10.2 Controller/DOM

| Màn | Ca bắt buộc |
|---|---|
| Phục vụ | render BLOCKED/READY/PREPARING; serve body có hai revision + op ID; 409 dùng snapshot; 4xx clear; network giữ exact; action khác không replay pending |
| KDS | late response shop cũ bị bỏ; CTA `Sẵn sàng giao`; blocked/resume; badge online/loading/stale/offline; exact retry |
| Recovery reload | non-secret outbox restore; reconcile-before-retry; cancellation approval không persist token/PIN; terminal server state xóa outbox |
| Layout | 320/390/760/1024/1366 px; 200% zoom; 40% text expansion; focus/escape/dialog; touch target 44 px |

### 10.3 Browser UAT và recovery

1. Điện thoại phục vụ 390×844: mở bàn, gọi/gửi món, thấy `Đang làm`, nhận
   `Sẵn sàng`, bấm `Đã giao khách` bằng một tay.
2. KDS 1366×768 nhìn từ xa: nhận làm, báo hết, resume, sẵn sàng; font món và
   số lượng đọc được, action không đổi vị trí bất ngờ.
3. Thu ngân 1024×768: thanh toán cash/transfer/debt theo R1; đóng bàn bị chặn
   khi còn món chưa giao và mở ngay khi tất cả served.
4. Chủ shop desktop/mobile: floor stage/count đúng sau mỗi transition.
5. Hai thiết bị cùng start/done/serve: bên stale nhận 409 + snapshot, không
   nhân audit hay tự đổi action.
6. Đổi shop khi GET KDS cũ còn chậm: response cũ không xuất hiện.
7. Ngắt mạng sau request send/done/serve/pay: không báo success; reload, kiểm
   snapshot, retry nguyên operation ID nếu vẫn cần.
8. Ngắt mạng trong approval cancellation: không lưu PIN/token; reload buộc
   kiểm tra line và duyệt mới nếu hủy chưa xảy ra.
9. Gộp bàn có ticket/check: 409, hai phiên và mọi provenance giữ nguyên.
10. Thanh toán một bill rồi gọi thêm: bill đã trả bất biến, món mới vào đúng
    `Bill bổ sung`, close chỉ mở khi lifecycle hoàn tất.

Không dùng provider thật, QR/ngân hàng thật, ngrok, secret hoặc dữ liệu thật.

## 11. Phát hành, quan sát và rollback

Thứ tự phát hành:

1. P0 fail-closed merge/close guard.
2. Migration cộng thêm + verifier.
3. Backend read model/serve/revision propagation.
4. Main F&B và KDS cùng cache-bust asset.
5. Focused regression + browser UAT DB giả cô lập.

Không phát hành frontend hiểu `READY/SERVED` trước backend. Rollback application
có thể bỏ dùng hai cột mới nhưng không down migration; dữ liệu served giữ lại.
Theo dõi tối thiểu bằng action log hiện có: số conflict, exact replay,
`FNB_ACTIVE_TICKETS`, `FNB_TARGET_SESSION_HAS_ARTIFACTS`, serve và out-of-stock.
Không thêm hệ thống telemetry mới trong Plan 2.

## 12. Ngoài phạm vi

- Không sửa năm file Seller được bảo vệ.
- Không thêm role `WAITER` trong lát cắt này.
- Không item-level preparation, course/firing, printer routing, expo station,
  websocket, push notification, multi-action offline queue hoặc auto retry mutation.
- Không thay contract Retail, payment/webhook, return/refund, voucher, loyalty,
  cash shift hoặc authorization R1.
- Không redesign palette/type/card system hiện có.

## 13. Quyết định cần chủ dự án duyệt

1. **D1 — Duyệt phương án A (khuyến nghị):** `DONE` = bếp/bar sẵn sàng;
   `served_at` = phục vụ đã giao, thay vì state machine từng item.
2. **D2 — Đóng bàn:** cho phép thu tiền sớm nhưng tuyệt đối không release bàn
   cho đến khi mọi ticket served/cancelled.
3. **D3 — Món gọi thêm sau thanh toán:** tự tạo đúng một `Bill bổ sung` primary
   khi primary cũ đã terminal; không sửa bill/order đã trả.
4. **D4 — Gộp bàn:** Plan 2 chỉ cho gộp target trống hoặc draft-only; target đã
   gửi/đã tách/đã trả bị chặn, chưa chuyển cả artifact graph.
5. **D5 — Hết món:** giữ cảnh báo ticket-level + resume trong Plan 2; chỉ nâng
   item-level khi UAT cho thấy cần hoàn tất từng món trong cùng ticket.
6. **D6 — Recovery:** một pending mutation, manual reconcile/retry exact; không
   queue offline nhiều action và không auto retry destructive action.
7. **D7 — Quyền phục vụ:** tạm giữ `CASHIER = service + checkout` vì thêm role
   mới phải chạm UI Seller đang được bảo vệ. Có tách một plan riêng để thêm
   `WAITER` sau khi gỡ bảo vệ năm file đó không?
