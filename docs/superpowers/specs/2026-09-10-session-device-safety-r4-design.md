# Plan 4 — An toàn phiên đăng nhập và đa thiết bị R4

**Ngày:** 2026-09-10

**Baseline bắt buộc:** `codex/roles-returns-approval-plan3`

**Commit đã xác minh:** `1acf0977d1a6431d88de0ffc590904af0b0f47f7`

**Đầu vào:** `FNB_PLAN3_UAT_REPORT.md`, `docs/superpowers/specs/2026-09-08-roles-returns-approval-r3-design.md`

## 1. Mục tiêu

Plan 4 thay cơ chế một `users.session_id` bằng danh sách phiên bền vững để nhiều
người và nhiều thiết bị F&B hoạt động đồng thời mà không làm yếu phân quyền,
tiền, kho, món, approval hoặc offline.

Kết quả cần đạt:

1. Login trên thiết bị mới không tự đá mọi thiết bị cũ của cùng tài khoản.
2. Mỗi phiên có thiết bị, tên hiển thị, loại thiết bị, thời điểm tạo/hoạt động
   gần nhất và trạng thái thu hồi.
3. Người dùng tự thu hồi phiên của mình; chủ shop thu hồi được phiên của nhân
   viên thuộc đúng shop; mất máy có thao tác thu hồi cả thiết bị.
4. Role, tài khoản, mật khẩu/PIN, ca và approval giữ đúng ranh giới; không dùng
   một thay đổi session để ngầm thay đổi quy tắc tiền/kho/offline.
5. Request đang bay và retry có kết quả xác định: hoặc nghiệp vụ thắng trước
   thu hồi, hoặc thu hồi thắng và không có side effect.

## 2. Baseline và bằng chứng runtime hiện tại

Worktree khởi đầu sạch, detached tại đúng commit yêu cầu. Ref
`codex/roles-returns-approval-plan3` cũng trỏ tới commit này. Không có
`AGENTS.md`; `CLAUDE.md` là chỉ dẫn repo hiện hữu đã được đọc.

Không mặc định tài liệu Plan 3 là runtime. Truy vết source cho thấy:

| Trạng thái hiện tại | Bằng chứng source/runtime |
|---|---|
| JWT HS256 chứa `sub`, `exp`, `sid`; token sống 24 giờ | `fselling/core/security.py:62-75`, `fselling/core/config.py:146-156` |
| Mỗi user chỉ có một `session_id` | `fselling/models/user.py:7-25` |
| Mỗi login ghi đè `users.session_id`, làm token cũ 401 | `fselling/services/auth_service.py:360-409`, `fselling/dependencies.py:98-138` |
| Logout chỉ xóa local state, không thu hồi server-side | `static/js/api.js:321-324` |
| Client giữ bearer token trong `localStorage`, kiểm `/session-check` mỗi 3 giây | `static/js/api.js:1-13`, `static/js/api.js:326-358` |
| Đổi role, reset password, disable staff đều xoay một `session_id` | `fselling/services/staff_service.py:93-181` |
| Ca là server-side theo shop + người mở, không theo browser/két; một người có tối đa một ca OPEN trong shop | `fselling/models/shift.py:1-37` |
| Đóng ca khóa hàng ca OPEN và không đụng auth session | `fselling/services/shift_service.py:576-658` |
| Offline lease đã là credential riêng theo user + shop + `device_id`, có token băm, version, expiry và revoke | `fselling/models/offline.py:13-31`, `fselling/services/offline_lease_service.py:356-458` |
| Offline ingest kiểm lease hiện tại, membership, role SALE, shop và binding thiết bị | `fselling/services/offline_lease_service.py:695-775` |
| Approval hiện bind actor user, shop, action, entity, revision, context; chưa bind auth session/thiết bị | `fselling/services/approval_service.py:56-180` |
| F&B idempotency bind shop + operation ID + fingerprint và ghi actor user | `fselling/models/fnb.py:109-132`, `fselling/services/fnb_service.py:103-165` |
| Plan 3 cố ý để multi-session cho Plan 4, remote/cross-device approval và broad redesign ngoài phạm vi | `docs/superpowers/specs/2026-09-08-roles-returns-approval-r3-design.md:39-47` |

Bằng chứng tự động tại baseline, không phải browser UAT mới:

```text
python -m pytest -q -p no:warnings `
  tests/test_auth.py::test_single_session_dang_nhap_moi_vo_hieu_token_cu `
  tests/test_auth.py::test_doi_mat_khau_vo_hieu_token_cu_va_tra_token_moi `
  tests/test_staff_roles.py::test_chu_shop_doi_service_sang_cashier_va_vo_hieu_phien_cu `
  tests/test_offline_lease_identity.py::test_role_downgrade_denies_reclaim_and_requires_normal_recovery `
  tests/test_shifts.py::test_staff_chi_xem_ca_cua_minh_nhung_chu_shop_xem_duoc
```

Kết quả thực tế: `5 passed`. Không chạy full suite. Plan 3 đã có browser evidence
cho SERVICE mobile, CASHIER, approval và role-change; Plan 4 chưa có UI để
browser-check ở bước design nên
không tuyên bố browser UAT mới.

## 3. Phạm vi

### 3.1 Trong phạm vi

- Nhiều auth session bền vững cho cùng một tài khoản.
- Định danh browser profile/thiết bị ở mức vận hành, đặt tên và phân loại thiết
  bị để người dùng nhận ra; đây không phải yếu tố mật mã.
- Danh sách, đổi tên, logout server-side, thu hồi từng phiên và thu hồi thiết bị.
- Owner quản lý phiên của staff cùng shop; self-service cho owner/staff/admin.
- Quy tắc vô hiệu hóa khi đổi role, disable, đổi/reset password, đổi PIN và đóng ca.
- Hàng rào cho mutation đang bay; giữ nguyên retry/idempotency qua phiên mới.
- Liên kết session với audit, approval phát trên thiết bị đó và offline lease
  được cấp từ session đó.
- Migration cộng thêm, tương thích token/row Plan 3 và focused UAT.

### 3.2 Ngoài phạm vi

- Remote approval inbox, push notification hoặc cross-device approval.
- WebSocket, refresh-token rotation, OAuth, passkey/WebAuthn, policy engine.
- Chuyển bearer token sang cookie/CSRF trong cùng lát cắt.
- Giới hạn phiên cấu hình theo role hoặc gói thuê bao khi chưa có số liệu vận hành.
- Ràng ca vào một browser, một máy in hoặc một két vật lý.
- Offline F&B/KDS; offline return; thay thuật toán tiền, kho, voucher, loyalty,
  batch/cost hoặc approval của Plan 3.
- Broad visual redesign cho mobile/tablet/desktop; thuộc Plan 5.
- Provider thật, secret thật, ngrok hoặc dịch vụ ngoài.

## 4. Các bất biến an toàn

1. Mỗi tài khoản đại diện một người hoặc một trạm cố định; không dùng một tài
   khoản chung cho nhiều nhân viên chỉ để né quản lý session.
2. Nhiều thiết bị có thể đăng nhập cùng tài khoản; mỗi cặp `user_id + device_id`
   chỉ có một auth session chưa thu hồi.
3. Login lại trên cùng browser profile chỉ thay phiên của thiết bị đó; không đá
   phiên của thiết bị khác.
4. `device_id`, tên/loại thiết bị và role client gửi lên không bao giờ cấp quyền.
   Server luôn đọc user, membership và role hiện tại.
5. Một `sid` phải tồn tại trong `auth_sessions`, đúng user, chưa thu hồi và chưa
   hết hạn. `users.session_id` không còn là nguồn xác thực sau migration.
6. Thu hồi đã commit chặn mọi request mới. Mutation quan trọng phải có session
   write-fence trong cùng transaction với side effect.
7. Không xóa hoặc viết lại business idempotency key khi đổi/thu hồi session.
   Retry từ phiên mới đọc lại kết quả bền, không tạo tiền/kho/món lần hai.
8. Ca/két vẫn thuộc shop + người mở. Đóng ca bỏ quyền ghi tiền vào ca, không
   logout người dùng.
9. Offline lease vẫn là credential riêng. Thu hồi auth session không được xóa
   receipt local; thu hồi thiết bị mới thu hồi các lease liên kết.
10. Approval token vẫn một lần, năm phút, bind đúng context; Plan 4 bind thêm
    auth session của actor và, với tiền mặt, ca OPEN cụ thể.
11. Không log JWT, raw offline lease token, PIN, password hoặc OTP.
12. Mọi list/revoke staff session phải scope qua chủ shop của đúng staff; ID đoán
    từ shop khác trả kết quả không tiết lộ.

## 5. Phương án kiến trúc

### A. Registry phiên bền vững, giữ JWT hiện tại — chọn

Thêm một bảng `auth_sessions`. JWT vẫn mang `sid`; dependency tra row thay vì so
với một cột trên `users`. Nhóm thiết bị được suy ra trực tiếp từ các row theo
`user_id + device_id`, không thêm bảng `devices`. Login cùng thiết bị thu hồi row
cũ rồi tạo row mới; login thiết bị khác giữ nguyên các row khác.

Ưu điểm: thay đổi nhỏ nhất, giữ được FastAPI/JWT/client hiện hữu, revoke tức thì,
migration/backfill rõ, không thêm dependency. Nhược điểm: bearer token đang hoạt
động vẫn replay được nếu bị đánh cắp; `device_id` không phải proof phần cứng.

### B. Singleton theo role/thiết bị

Đặt quota cứng, ví dụ KDS một phiên, CASHIER hai phiên, owner năm phiên.

Ưu điểm: ít row và giảm số phiên bị lộ. Nhược điểm: con số tùy ý, dễ khóa owner
khi mất máy, ép dùng chung tài khoản trạm, tăng nhánh UX và không giải quyết gốc
replay. Không chọn trong Plan 4. Có thể thêm một ceiling chống abuse sau khi có
số liệu, không biến nó thành policy engine.

### C. Opaque session/refresh token trong HttpOnly cookie

Ưu điểm: JavaScript không đọc được token, giảm tác động của XSS; có thể xoay
access token ngắn hạn. Nhược điểm: phải thiết kế CSRF, thay client/API/offline,
test/deploy lớn hơn nhiều và không cần để sửa lỗi multi-session. Hoãn thành một
security plan riêng nếu threat model hoặc kiểm thử XSS chứng minh cần.

## 6. Mô hình dữ liệu và migration

Migration bắt buộc: `0014_session_device_safety_r4`, nối thẳng từ
`0013_roles_returns_approval_r3`, forward-only.

### 6.1 Bảng `auth_sessions`

| Cột | Hợp đồng |
|---|---|
| `session_id` | PK, random do server sinh; chính là JWT `sid` |
| `user_id` | FK `users.id`, không null |
| `device_id` | Opaque ID browser profile, 1–128 ký tự; không phải secret |
| `device_name` | Tên người dùng nhận ra, trim, 1–80 ký tự |
| `device_type` | `DESKTOP`, `TABLET`, `MOBILE`, `KDS` hoặc `UNKNOWN`; chỉ hiển thị |
| `created_at` | UTC, không null |
| `last_seen_at` | Lần `/session-check` xác nhận gần nhất, UTC, không null |
| `expires_at` | Cùng hạn với JWT, UTC, không null |
| `revoked_at` | UTC nullable; null là chưa thu hồi |
| `revoked_by_user_id` | FK `users.id`, nullable cho expiry/system |
| `revoke_reason` | Mã ngắn, không chứa secret; nullable |

Index bắt buộc:

- tìm phiên theo `user_id`, `revoked_at`, `expires_at`;
- unique partial `user_id + device_id WHERE revoked_at IS NULL`.

Không lưu IP hay full user-agent trong Plan 4: chưa cần cho quyết định vận hành,
tăng dữ liệu nhạy cảm và không phải proof thiết bị.

### 6.2 Liên kết tối thiểu

- `system_logs.auth_session_id` nullable FK: log mới của lifecycle session và
  mutation tiền/kho/F&B thuộc Plan 4 ghi session; log cũ giữ null.
- `fnb_action_logs.auth_session_id` nullable FK: operation F&B mới ghi đúng
  session actor trong cùng transaction với result/idempotency; row cũ giữ null.
- `offline_leases.issued_by_auth_session_id` nullable FK: lease mới biết được
  session phát hành; lease cũ giữ null và vẫn đọc/recovery được.
- `fnb_manager_approvals.actor_auth_session_id` nullable FK: token mới bắt buộc
  có session actor; token cũ chưa dùng bị coi hết hiệu lực khi nâng cấp.

Không thêm `shop_id` vào auth session. Owner có thể có nhiều shop; shop access
vẫn được re-check ở từng request. Không bind business record vào device bằng
foreign key mới ngoài audit vì user/operation ID đang là nguồn nghiệp vụ.

### 6.3 Backfill và tương thích token hiện hữu

Mỗi `users.session_id` hiện hữu được backfill thành tối đa một `auth_sessions`
row:

- `device_id = 'legacy:' + session_id`;
- tên `Thiết bị trước Plan 4`, type `UNKNOWN`;
- `created_at = last_seen_at = migration_time_utc`;
- `expires_at = migration_time_utc + 24 giờ`.

JWT vẫn tự chặn theo `exp`, nên backfill 24 giờ không kéo dài token cũ. Cách này
tránh buộc logout đồng loạt và tránh seal nhầm receipt offline đang chờ. Khi user
login lại từ browser đã nâng cấp, row legacy tự hết hạn; không cần job cleanup.

Giữ cột `users.session_id` trong Plan 4 để tránh rebuild SQLite, nhưng code mới
không đọc/ghi nó. Không backfill suy đoán quan hệ lease cũ với thiết bị online.

Verifier phải kiểm table/column/FK/index/enum, row không có thời gian ngược,
unique active device và mọi approval Plan 4 có session actor hợp lệ. Migration
không import model/service mutable.

## 7. Hợp đồng API

### 7.1 Login và heartbeat

`POST /api/auth/login` nhận thêm các field optional để client cũ không vỡ:

```json
{
  "username": "cashier-a",
  "password": "...",
  "device_id": "dev_opaque_random",
  "device_name": "Quầy trước",
  "device_type": "DESKTOP"
}
```

Client mới luôn gửi đủ metadata. Server tự sinh session ID; không nhận `sid` từ
client. Nếu metadata thiếu, server tạo `device_id` riêng cho lần login, tên
`Thiết bị chưa đặt tên`, type `UNKNOWN`.

Response giữ `access_token`, `token_type`, `role`, `staff_role`; thêm object
`session` gồm ID, device, created/last-seen/expires và `current=true`. Không trả
raw credential khác.

`GET /api/auth/session-check` vẫn là auth heartbeat. Nó chỉ cập nhật
`last_seen_at` nếu đã cũ hơn 5 phút để không biến polling thành write storm trên
SQLite. Client đổi poll từ 3 giây thành 15 giây; mọi business API vẫn kiểm session
ngay khi nhận request nên poll chỉ phục vụ UX logout, không phải hàng rào quyền.

### 7.2 Self-service

- `POST /api/auth/logout`: thu hồi phiên hiện tại server-side, sau đó client seal
  local offline identity và xóa auth localStorage.
- `GET /api/auth/sessions`: danh sách phiên chưa hết hạn của chính user, current
  trước rồi `last_seen_at` mới nhất; không hiện token/IP.
- `PATCH /api/auth/sessions/{session_id}`: đổi `device_name` của phiên/nhóm thiết
  bị thuộc chính user; tên chỉ để hiển thị.
- `DELETE /api/auth/sessions/{session_id}`: thu hồi đúng một auth session.
- `POST /api/auth/devices/{device_id}/revoke`: thu hồi mọi auth session của
  chính user trên device và mọi offline lease mới liên kết với các session đó;
  yêu cầu lý do 10–500 ký tự.

Thu hồi current session trả success rồi client logout. Retry thu hồi là
idempotent và trả trạng thái đã thu hồi, không sinh log trùng.

### 7.3 Owner quản lý staff cùng shop

- `GET /api/staff/member/{staff_id}/sessions`
- `DELETE /api/staff/member/{staff_id}/sessions/{session_id}`
- `POST /api/staff/member/{staff_id}/devices/{device_id}/revoke`

Mọi route gọi `require_own_shop` qua `staff.staff_shop_id`. Owner không quản lý
session của owner khác, ADMIN qua các route này, hoặc staff shop khác. Trường hợp
không thuộc scope trả 404 thống nhất.

Không làm màn quản trị toàn cục mới cho ADMIN trong Plan 4; ADMIN tự quản lý
phiên của chính mình. Nhu cầu support toàn hệ thống cần spec/audit riêng.

### 7.4 Error code ổn định

| Code | HTTP | Ý nghĩa |
|---|---:|---|
| `AUTH_SESSION_INVALID` | 401 | sid thiếu/không thuộc user |
| `AUTH_SESSION_REVOKED` | 401 | phiên đã thu hồi |
| `AUTH_SESSION_EXPIRED` | 401 | row/JWT hết hạn |
| `AUTH_SESSION_CHANGED` | 409 | update/revoke tranh chấp state |
| `AUTH_DEVICE_INVALID` | 400 | ID/tên/type không hợp lệ |
| `AUTH_SESSION_NOT_FOUND` | 404 | không có phiên trong scope |
| `AUTH_DEVICE_NOT_FOUND` | 404 | không có thiết bị trong scope |

401 phải đưa client vào unknown-result recovery nếu request là mutation; không
được tự báo thất bại nghiệp vụ khi chưa tra bằng operation ID từ phiên mới.

## 8. Quy tắc vô hiệu hóa

| Sự kiện | Auth session | Offline lease/seal | Approval | Ca |
|---|---|---|---|---|
| Login thiết bị mới | Thêm một phiên; giữ thiết bị khác | Không đổi | Không đổi | Không đổi |
| Login lại cùng device | Thu hồi phiên cũ cùng device, tạo phiên mới | Không xóa receipt/lease | Token bind phiên cũ hết hiệu lực | Không đổi |
| Logout | Thu hồi current session | Seal identity local; không tự revoke lease server | Token của actor session hết hiệu lực | Ca vẫn OPEN |
| Thu hồi một session | Chỉ row mục tiêu | Không xóa/revoke lease | Token bind row đó hết hiệu lực | Không đổi |
| Thu hồi thiết bị/mất máy | Mọi session user + device | Revoke lease liên kết; local chỉ seal khi máy online lại | Token từ các session đó hết hiệu lực | Ca vẫn tồn tại, nhưng máy mất không ghi được |
| Đổi staff role | Thu hồi mọi auth session staff trong cùng transaction | Lease re-check role hiện tại; mất SALE thì normal sync/mutation chuyển recovery như baseline | Token có actor/approver không còn quyền bị từ chối | Ca không tự đóng |
| Disable staff/account | Thu hồi mọi auth session cùng transaction; `is_active=false` vẫn là chặn dự phòng | Membership hiện tại chặn normal use; receipt giữ cho owner recovery | Mọi token chưa dùng của actor/approver bị vô hiệu | Không cho disable staff khi còn ca OPEN như baseline |
| Sai password bị khóa tạm | Chỉ chặn login mới; không thu hồi phiên đang dùng | Không đổi | Không đổi | Không đổi |
| Self change password | Thu hồi mọi phiên cũ, tạo phiên mới cho current device | Không xóa lease/receipt; lease vẫn cần auth mới cùng user và policy hiện tại | Token bind phiên cũ hết hiệu lực | Không đổi |
| Forgot/reset staff password | Thu hồi tất cả; không cấp phiên mới | Không xóa lease/receipt | Token actor cũ hết hiệu lực | Không đổi |
| Đổi manager PIN | Không logout | Không đổi | Expire mọi token chưa dùng do approver đó cấp; ghi audit | Không đổi |
| Đóng ca | Không logout | Không đổi | Approval tiền mặt bind ca cũ thành context-changed; approval không liên quan tiền giữ nguyên | Ca chuyển CLOSED dưới lock hiện hữu |

Khóa tạm do sai password cố ý không logout phiên hợp lệ: nếu có, kẻ biết username
có thể làm gián đoạn quầy chỉ bằng cách nhập sai nhiều lần.

Role change phải re-check role từ DB, không tin `staff_role` đang cache ở client.
UI 401 thì seal/clear như hiện tại; lần login mới nhận role và route mới.

## 9. Request đang bay, stale token và idempotency

### 9.1 Hai lớp kiểm session

1. `get_current_user` kiểm JWT + row `auth_sessions` khi request vào.
2. Mutation tiền, kho, đơn, trả hàng, ca, F&B session/ticket/KDS và approval gọi
   một helper nhỏ `fence_live_auth_session` trong transaction hiện hữu, sau khi
   đã lấy domain/shop lock và trước side effect đầu tiên.

Helper thực hiện no-op conditional UPDATE theo `session_id`, `user_id`,
`revoked_at IS NULL`, `expires_at > now`. Trên SQLite đây là write-fence cùng DB:

- mutation lấy write lock trước: mutation commit rồi revoke mới commit; audit
  thể hiện nghiệp vụ xảy ra trước thu hồi;
- revoke commit trước: conditional UPDATE không match, transaction rollback toàn
  bộ và trả 401;
- không có trạng thái revoke đã thắng nhưng mutation cũ vẫn commit sau nó.

Không cố hủy socket/HTTP request đang chạy. Hợp đồng là thứ tự commit bền vững,
không phải thứ tự người dùng bấm nút trên hai màn hình.

Helper dùng `sid` đã decode server-side; không nhận session ID trong body. Read
endpoint chỉ cần dependency check, trừ KDS read nào làm acknowledgement/mutation.

### 9.2 Retry và chuyển thiết bị

Business idempotency vẫn độc lập với auth session:

- operation ID/fingerprint/order/shop/user hiện hữu không bị đổi khi session
  bị thu hồi;
- nếu response mất nhưng transaction đã commit, user login phiên mới và retry
  cùng payload + operation ID sẽ nhận kết quả cũ;
- retry khác payload tiếp tục 409 collision;
- retry không có quyền hiện tại vẫn bị 403; idempotency không phải bypass role;
- một retry thành công không ghi thêm ledger, stock, ticket, approval-use hoặc log.

UI khi gặp network error/401 sau mutation phải nói “chưa xác định kết quả”, giữ
draft + operation ID và cho đăng nhập lại/tra cứu trước khi cho bấm tạo mã mới.

### 9.3 Approval và ca

Approval phát từ PIN trên thiết bị actor hiện tại ghi
`actor_auth_session_id`. Consume phải khớp session đó, approver vẫn active/cùng
shop/còn `RETURN_APPROVE`, token chưa dùng/chưa hết hạn và context không đổi.
Đưa raw token sang thiết bị khác không dùng được.

Với cash return, context fingerprint thêm `cash_shift_id` hiện tại. Đóng ca hoặc
mở ca mới làm fingerprint khác và trả `RETURN_CONTEXT_CHANGED`; cashier phải
refresh và xin duyệt lại. F&B cancel approval không liên quan tiền giữ binding
session/revision hiện hữu và không bị gắn ca giả tạo.

Remote/cross-device approval không nằm trong Plan 4. Manager vẫn nhập PIN tại
thiết bị actor. Nếu sau này cần remote approval, phải thiết kế request/inbox,
auth approver và race/expiry riêng; không gửi approval token hiện hữu qua push.

## 10. Ranh giới các loại state

| State | Trả lời câu hỏi | Nguồn quyền | Khi auth session bị revoke |
|---|---|---|---|
| Auth session | Ai đang gọi online từ browser profile nào? | JWT + `auth_sessions` + user hiện tại | Request mới 401; mutation đang bay theo fence |
| Browser identity/seal | Local data đang thuộc username nào? | IndexedDB/local marker | Seal local khi logout/401; không xóa receipt |
| Offline lease | Thiết bị được tạo/sync receipt offline trong cửa sổ nào? | Lease token băm + user/shop/device + policy hiện tại | Cần một auth session hợp lệ để gọi server; chỉ device revoke mới revoke lease liên kết |
| Cash shift | Tiền mặt thuộc ca của người nào trong shop? | `cash_shifts`, shop/user/status/lock | Không tự đóng; session mới của cùng user có thể tiếp tục ca |
| Approval token | Ai duyệt ngoại lệ nào cho actor/context nào? | Hashed one-use token + approver/actor/session/context | Token bind session cũ hết hiệu lực |
| Audit/idempotency | Việc gì đã commit và retry có lặp side effect không? | Domain ledger/action log/system log | Giữ nguyên; session ID bổ sung attribution, không đổi kết quả |

Không dùng từ `session` trần trong code/tài liệu mới khi có thể nhầm với
`FnbServiceSession` hoặc `offline_session_id`; dùng `auth_session`,
`fnb_service_session`, `offline_lease` rõ nghĩa.

## 11. Role × device

### 11.1 Hệ thống chung

Mọi role có một panel bảo mật tối thiểu “Phiên và thiết bị” để xem current
session, đặt tên và logout/revoke phiên của chính mình. KDS chỉ cần mục nhỏ trong
menu trạm, không thêm dashboard mới. Owner thấy session/device của staff trong
luồng quản lý nhân viên hiện hữu. UI luôn hiện tên, type, current, last-seen và
expiry cạnh nút revoke; thao tác device revoke phải nói rõ offline lease liên
quan cũng bị thu hồi. Đây là thay đổi chức năng nhỏ, không phải redesign Plan 5.

### 11.2 Retail

| Ngữ cảnh | Chính sách Plan 4 | Rủi ro được chặn/giữ lại |
|---|---|---|
| Owner desktop/tablet/mobile | Cho nhiều thiết bị, tự list/revoke | Mất máy dùng device revoke; không cho shop khác xem phiên |
| CASHIER POS desktop/tablet | Mỗi người account riêng; một người có thể chuyển máy và tiếp tục ca | Session/device vào audit; close shift chặn tiền, không logout |
| Retail offline sale | Auth session và offline lease tách riêng, link tại lúc issue | Device revoke chặn normal ingest nhưng giữ receipt/recovery |
| ADMIN | Nhiều phiên tự quản lý; không thêm support console toàn hệ thống | Tránh quyền quản lý session rộng chưa được audit |

### 11.3 F&B

| Ngữ cảnh | Chính sách Plan 4 | Rủi ro được chặn/giữ lại |
|---|---|---|
| SERVICE mobile | Mỗi waiter account riêng; điện thoại phụ được phép, không role cap | Hai máy cùng sửa bàn vẫn dùng revision + operation ID; revoke fence chặn máy mất |
| CASHIER PC/tablet | Một người có thể chuyển PC ↔ tablet và tiếp tục ca; không chia một account cho hai người | Thiết bị nào làm mutation được ghi session; close shift chặn tiền, không logout |
| KDS bếp/bar cố định | Mỗi trạm có account KITCHEN/BAR và device name rõ (`Bếp nóng`, `Quầy bar`) | Không có SALE/offline lease; mọi ticket mutation có auth-session fence |
| Manager mobile/desktop | Nhiều phiên hợp lệ; role change/disable thu hồi đồng loạt | PIN không biến thành login; approval bind actor session |
| Owner mobile/desktop | Nhiều phiên hợp lệ, tự list/revoke; quản lý được staff đúng shop | Không có remote approval mặc định |

Không hardcode “KDS chỉ một phiên”: một bếp có thể có bếp nóng/bếp lạnh. Quy tắc
an toàn là một account cho một người/trạm và một active session cho mỗi device,
không phải quota theo tên role.

## 12. Failure và recovery

| Tình huống | Hành vi bắt buộc |
|---|---|
| Token JWT cũ sau login cùng device | 401 `AUTH_SESSION_REVOKED`; device khác vẫn hoạt động |
| Token bị copy khi session còn active | Có thể hoạt động trong thời hạn; đây là residual risk của bearer token. Revoke session/device chặn ngay request sau commit |
| Mất máy đang online | Owner/self device revoke; mọi online session + lease liên kết bị revoke |
| Mất máy đang offline | Server không thể xóa local hay ngăn giao hàng vật lý. Khi máy nối lại, normal sync bị chặn; owner recovery xử lý receipt/evidence |
| Đứt mạng sau bấm bán/trả/checkout | Giữ operation ID; login lại nếu cần; retry/lookup trước khi tạo thao tác mới |
| Revoke tranh chấp mutation | Một thứ tự commit duy nhất theo write-fence; loser rollback hoặc đọc durable winner |
| Server restart | Session rows bền vững; JWT vẫn phụ thuộc `SECRET_KEY` cấu hình hiện hữu. Không đưa secret vào DB/log/spec |
| Role đổi khi UI cũ đang mở | Mọi auth session cũ 401; local role không cấp quyền; login lại route theo role mới |
| Đóng ca khi request tiền đang chờ | Cùng shift lock: request thắng trước hoặc close thắng; không có ledger ghi vào ca đã CLOSED |
| Approval cũ sau đổi PIN/role/session/shift | 403 invalid hoặc 409 context changed; không side effect; UI xóa token và xin lại |
| Session list không tải được | Không tự logout phiên hợp lệ; hiển thị retry. Revoke không được báo thành công giả |
| Device name độc hại/trùng | Escape khi render; tên không unique và không dùng phân quyền; hiển thị thêm type/last-seen/current để phân biệt |

## 13. Security

### 13.1 Cross-shop và rò dữ liệu

- Auth session là user-scoped, không tự cấp shop.
- Staff session list/revoke phải resolve staff rồi `require_own_shop`; trả 404 cho
  ngoài scope.
- Session response không trả password/PIN/JWT hash/raw token/IP/full UA.
- `device_name` là dữ liệu không tin cậy: trim/giới hạn server, escape client.
- System log mới luôn có `shop_id` khi lifecycle action liên quan staff của shop.

### 13.2 Session fixation và replay

- Server luôn sinh sid mới sau password authentication; không nhận sid từ URL,
  body, cookie hoặc localStorage.
- Login cùng device thu hồi row cũ trong cùng transaction trước khi tạo row mới.
- JWT chỉ nhận qua Authorization header như baseline; không query string.
- Device ID không được quảng bá như anti-replay. Kẻ lấy được cả bearer token vẫn
  dùng được tới khi expiry/revoke; giảm rủi ro bằng TTL 24 giờ hiện hữu, session
  list, last-seen, server logout và revoke nhanh.
- Không thêm refresh token trong Plan 4. Nếu cần giảm residual risk, chọn phương
  án C trong một security slice độc lập.

### 13.3 Quyền từ thiết bị bị thu hồi

- Mọi online endpoint auth trước khi đọc business data.
- Mọi mutation tiền/kho/đơn/món/ca/approval dùng fence trước side effect.
- KDS không có offline path; thiết bị KDS revoke không thể mutate ticket.
- Offline receipt từ lease/device đã revoke không được normal ingest dựa trên
  client timestamp; chỉ owner recovery có bằng chứng rõ như baseline.

## 14. Audit

Lifecycle log tối thiểu:

- `AUTH_SESSION_LOGIN`
- `AUTH_SESSION_LOGOUT`
- `AUTH_SESSION_REVOKE`
- `AUTH_DEVICE_REVOKE`
- `AUTH_SESSIONS_REVOKE_ROLE_CHANGE`
- `AUTH_SESSIONS_REVOKE_PASSWORD`
- `AUTH_SESSIONS_REVOKE_ACCOUNT_DISABLE`
- `APPROVALS_INVALIDATE_PIN_CHANGE`

Mỗi log ghi actor user, target user khi có, shop khi có, auth session FK, device
name/type an toàn, số session/lease/approval bị ảnh hưởng và reason code. Không
ghi raw token, PIN, password, OTP hoặc full device ID trong `details`.

Mutation quan trọng mới/được chạm trong Plan 4 ghi `auth_session_id` vào audit
row cùng transaction. F&B ghi thẳng vào `fnb_action_logs.auth_session_id` cùng
operation result; Retail/shift/approval dùng `system_logs.auth_session_id` trong
transaction nghiệp vụ hiện hữu. Không tạo hệ thống audit thứ hai.

Expiry tự nhiên không ghi một log cho mỗi row để tránh noise/quota. Danh sách coi
row quá hạn là expired; lịch sử login/revoke vẫn đủ điều tra.

## 15. Compatibility và rollout

1. Migration additive, giữ mọi row Plan 3 và cột `users.session_id` cũ.
2. Token cũ được backfill, không logout đồng loạt; token vẫn hết hạn theo JWT.
3. Login client cũ thiếu device metadata vẫn thành công ở mode `UNKNOWN`.
4. Response fields mới là additive; role routing Plan 3 giữ nguyên.
5. Offline lease cũ nullable link vẫn heartbeat/recovery theo hợp đồng hiện tại;
   không tự gán nhầm thiết bị.
6. Approval Plan 3 chưa dùng tại lúc deploy được phép hết hiệu lực; tối đa phải
   xin lại sau 5 phút, không mang token actor-only vào multi-session.
7. Ca OPEN, order, ticket, receipt, return, ledger và operation ID không migrate
   ownership sang device.
8. Không thay `SECRET_KEY`, không dùng provider/secret/ngrok trong rollout.
9. Startup vẫn fail-closed nếu migration/verifier/checksum không khớp.

## 16. Kế hoạch kiểm thử và UAT của implementation tương lai

Đây là acceptance design, chưa phải implementation plan và chưa tạo test.

### 16.1 Focused automated coverage

1. Hai device khác nhau cùng user đều hợp lệ; login lại device A chỉ revoke A cũ.
2. JWT đúng chữ ký nhưng sid không có/sai user/revoked/expired đều 401.
3. Logout server-side; self list/rename/revoke; retry revoke không log trùng.
4. Owner list/revoke staff đúng shop; cross-shop/ADMIN-via-staff-route fail closed.
5. Role change, disable, self change password, forgot/reset password đúng ma trận.
6. Temporary password lockout không làm rớt session hợp lệ.
7. PIN change/role loss/session revoke làm approval cũ vô hiệu; cross-session
   token không consume được.
8. Cash approval bind shift; close/open shift mới trả context changed.
9. Race session revoke với Retail sale/return, shift cash mutation, F&B send/
   cancel/checkout và KDS mutation: đúng một thứ tự commit, không partial effect.
10. Retry cùng operation ID từ session mới trả durable result; payload khác 409.
11. Device revoke thu hồi mọi linked online session + lease trong một transaction;
    lease legacy nullable không bị suy đoán/xóa.
12. Migration fresh/legacy/head verify, backfill token cũ và forward-only downgrade.
13. JS tests cho stable device ID, localStorage multi-tab, 401 unknown-result,
    offline seal marker và không render token/secret.

Không chạy full suite trong design. Release sau này vẫn cần focused RED→GREEN,
`git diff --check`, owner-run full-suite một lần và independent safety review theo
quy trình đã chấp nhận.

### 16.2 Browser UAT bằng DB giả riêng

- Owner mobile 390×844 + desktop: thấy hai phiên, đổi tên, revoke một phiên;
  desktop khác vẫn hoạt động.
- SERVICE mobile: hai waiter account độc lập cùng phục vụ; revoke một điện thoại
  làm mutation kế tiếp 401 nhưng waiter khác không bị đá.
- CASHIER 1024×768 + tablet: chuyển thiết bị, tiếp tục đúng ca; lost-device revoke;
  unknown-result retry không nhân tiền/order/return.
- KDS bếp và bar cố định: tên trạm/last-seen rõ, revoke bếp không đá bar, không
  mutate ticket sau revoke.
- Manager/owner mobile + desktop: session list đúng scope; approval PIN tại actor
  device, cross-device token fail; không có remote approval inbox.
- Role change/disable/password/PIN/close-shift theo ma trận và audit UI.
- Cross-shop URL tampering không lộ username/device/session.

Browser evidence phải ghi viewport, role, device, control đã bấm, request/result
và trạng thái durable quan sát được. Mất máy thật, push, provider, network split
không được giả là browser PASS; ghi `TEST_GAP` nếu chưa có harness hợp lệ.

## 17. Acceptance criteria

1. Baseline/migration đúng `1acf0977…` → `0014`; verifier fail-closed.
2. Login mới không còn vô hiệu mọi phiên cùng user; chỉ thay phiên cùng device.
3. Session/device list có tên, type, created, last-seen, expiry, current/revoked
   state và không lộ secret.
4. Self/owner staff revoke đúng scope; cross-shop không enumerate.
5. Logout là server-side; token cũ 401 sau commit.
6. Role/account/password/PIN/shift tuân đúng ma trận mục 8.
7. Mutation từ session bị revoke không commit sau revoke; race có test bền vững.
8. Cash shift vẫn per-user; đóng ca chặn tiền nhưng không logout.
9. Approval token bind actor auth session; cash approval bind ca; one-use/5 phút/
   context/atomicity Plan 3 vẫn giữ.
10. Offline lease/receipt/seal không bị nhập chung auth session; device revoke
    chặn normal ingest nhưng giữ evidence/recovery.
11. Retail POS, SERVICE, CASHIER, KDS, manager và owner đều có UAT đúng thiết bị.
12. Idempotency/revision/shop locks và mọi invariant tiền/kho/món Plan 1–3 xanh.
13. Không có policy engine, WebSocket, dependency mới, remote approval hoặc broad
    device redesign.
14. Automated evidence, browser evidence và `TEST_GAP` được tách rõ.

## 18. Quyết định cần owner duyệt trước implementation plan

1. Chọn phương án A: durable session registry, giữ JWT bearer hiện tại.
2. Một account = một người/trạm; nhiều device hợp lệ; một active session cho mỗi
   user + device; chưa thêm quota theo role.
3. Ca vẫn theo user/shop, không theo device; session mới cùng user tiếp tục ca.
4. Logout/session revoke không tự revoke offline lease; device revoke có revoke
   lease liên kết và giữ owner recovery.
5. Role/password/account change revoke online session theo ma trận; temporary
   login lock không đá phiên đang dùng.
6. Approval bind actor auth session; cash approval bind exact OPEN shift; remote
   approval tiếp tục ngoài phạm vi.
7. Giữ residual risk của active bearer token trong Plan 4; cookie/refresh-token
   là security slice riêng nếu cần.
8. Backfill token legacy thay vì logout đồng loạt; giữ cột `users.session_id`
   deprecated trong một release.

Sau khi owner duyệt spec này mới được dùng `superpowers:writing-plans`. Trước đó
không sửa source/config/test sản phẩm và không tạo implementation plan.
