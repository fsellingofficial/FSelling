# Plan 5 — Device Usability & PWA R5

**Ngày:** 2026-09-11. **Trạng thái:** DESIGN APPROVED — owner đã duyệt cả 5 quyết định D1–D5 bằng phản hồi “duyệt cả 5”. Chưa triển khai runtime.

## 1. Baseline, mục tiêu và giới hạn

- Worktree: `C:\Users\nguye\.codex\worktrees\7a90\python_app`.
- Đầu vào đã kiểm: `18eca4aa6299fe9072cbf9dff3faaa62c658ec4e`; ref `codex/session-device-safety-plan4` trỏ cùng commit. Worktree detached HEAD, `git status --short` rỗng trước discovery.
- Đã đọc `CLAUDE.md`, quy trình repo, spec và báo cáo R4, source thực tế, các harness UI liên quan. Tài liệu cũ không thay thế bằng chứng source/runtime ở baseline này.
- Áp dụng `ux-heuristics`, `fortify`, `frontend-design` từ `.agents/skills` của workspace gốc và Ponytail: tận dụng HTML/CSS/JS, dialog, controller, i18n, offline panel có sẵn; không framework/dependency mới, không đổi thương hiệu.
- Mục tiêu: người dùng biết đang ở shop/bàn/đơn/ca nào, thao tác đã được ghi nhận hay chưa, và bước an toàn tiếp theo trên thiết bị của mình. Ưu tiên sai tiền, sai món, gửi trùng và giao dịch treo trước thẩm mỹ.
- Chỉ local/demo. Không ngrok, deploy, webhook/chuyển tiền thật, production DB, Gemini hay TTS server. Không push/merge/rebase/reset/revert. Discovery/design không chạy full suite, không viết implementation plan, không sửa runtime source/config/test; thiết kế nay đã được duyệt, bước kế tiếp là lập implementation plan dựa trên D1–D5.

### Hợp đồng Plan 1–4 giữ nguyên

1. Server tính giá/tổng/thuế/giảm giá/điểm, kiểm số tiền, tồn kho, ca và quyền. UI không xác nhận đã thu tiền trước kết quả server hoặc thay trạng thái tiền bằng trạng thái mạng.
2. Giữ operation ID + exact payload/fingerprint khi kết quả chưa xác định; không tạo operation mới để thoát lỗi. Revision conflict phải đọc lại và để người dùng xét lại ý định; không tự ghi đè.
3. Giữ phân bổ bill, lifecycle F&B, ticket state/version, trả hàng từng phần, tham chiếu hoàn tiền, audit/ledger và transaction fence. READY chế biến không phải PAID; giao món không phải thu tiền.
4. SERVICE không thanh toán/trả hàng; KITCHEN/BAR chỉ các hành động đúng trạm; không mở quyền bằng cách đổi navigation. Approval vẫn bind actor/session/shop/entity/revision/context/ca theo R4.
5. Offline Retail chỉ theo lease/catalog/identity/ca và loại thanh toán hiện có. Auth session và offline lease là hai trạng thái riêng. Logout/đổi người dùng/revoke vẫn seal/revoke theo R4. Không tự unseal hoặc dùng lại quyền từ cache.
6. Không lưu PIN/password/approval token vào draft, log hay local receipt. Bản nháp không phải bằng chứng server đã nhận. Không tự retry mutation không có hợp đồng idempotency.
7. SW không cache `/api/*`, không xử lý mutation như offline queue, không dùng dữ liệu cũ để cấp quyền. Không đổi schema/API/backend để làm UI trông thuận tiện hơn.

## 2. Bằng chứng discovery

### 2.1 Môi trường và cách phân loại

Ứng dụng chạy qua `uvicorn app:app --host 127.0.0.1 --port 8515`, DB giả riêng tại `C:\Users\nguye\.codex\worktrees\7a90\plan5-runtime-demo.db`, log tại `plan5-server.log` cùng thư mục cha. `GEMINI_ENABLED=0`, `TTS_SERVER_ENABLED=0`. Migration verify xác nhận đủ 14 revision, head `0014_session_device_safety_r4`. Không đọc/copy DB thật.

Seeder nguyên bản dừng ở password policy khi tạo staff. Lần seed DB mới dùng giá trị mật khẩu demo hợp lệ trong tiến trình Python, không sửa file; đi đến 91 đơn giả rồi dừng ở `RETURN_REFERENCE_REQUIRED`. Không nới validator. Phần dữ liệu đã tạo đủ cho discovery. Bổ sung SERVICE/KITCHEN/BAR, bật F&B, tạo một khu/ba bàn, gán hai sản phẩm demo sang Bếp/Bar bằng local API với revision + operation ID. Sản phẩm Retail dùng làm món thử bố cục, không phải thực đơn F&B đề xuất. Một DB demo dở khác `plan5-demo.db` cũng ở thư mục cha; không nằm trong Git.

Browser: Codex in-app browser trên Windows; viewport mô phỏng, không phải thiết bị vật lý hoặc standalone PWA. Ảnh đã xem trực tiếp trong task; các phép đo DOM bên dưới là bản ghi bền vững trong spec. Không có bộ PNG độc lập được xuất. Các trạng thái server/API tạo bằng script được ghi là setup hoặc automated, không phải browser PASS.

| ID | Vai trò / viewport | Quan sát thực tế |
|---|---|---|
| B01 | SELLER, 1366×768 | Login → dashboard có 91 đơn, thống kê và sidebar. Chỉ xác nhận hiển thị; không kiểm tính đúng tổng doanh thu từ UI |
| B02 | SELLER, 390×844 | Dashboard xếp thẻ thống kê dọc, biểu đồ phía dưới; điểm vào “Cần xử lý” ở menu. Không tràn ngang toàn document trong phép đo (`scrollWidth=375`, `innerWidth=390`); biểu đồ có nội dung bị cắt trong vùng con |
| B03 | SELLER, 390×844 | Mở “Phiên và thiết bị”; danh sách và nút có trong DOM, chữ rất tối. Computed color `rgb(30,41,59)`, background `rgb(15,23,42)`. Dialog khoảng 337×828 px |
| B04 | SELLER, 390×844 | Mở Bàn 01 trống → thêm 1 bánh mì 20.000 ₫ → “Đã lưu món”, chưa gửi. `Gửi món` rect x=199.2,y=790,w=177.2,h=44; launcher x=222.4,y=784,w=152,h=44. Launcher che phần lớn nút gửi |
| B05 | SERVICE, 390×844 | Login tự vào F&B; mở Bàn 01 đã có món; không có Tính tiền, có hướng dẫn bàn giao. Bấm Gửi món → “Đã gửi món đến đúng nơi chế biến”, phiếu #1 ở nhóm chờ Bếp. Ngay sau gửi, phần draft lại ghi “Bàn chưa gọi món” dù bill còn 20.000 ₫ |
| B06 | SERVICE, 360×640 | Menu cao 345.6 px; `.fnb-bill-scroll` cao 28 px nhưng scrollHeight 647; footer cao 217.4 px. Chỉ thấy dải nhãn Bill, không đủ xem món trước thao tác. Đây là viewport thấp, chưa phải phép thử bàn phím ảo |
| B07 | CASHIER, 1024×768 | Chưa mở ca → form mở ca với tiền đầu ca 0 → Ca #1. Chọn Lavie 5.000 ₫, chọn tiền mặt: hiện Còn thiếu 5.000 ₫, nút hoàn tất bị khóa. Chọn đưa đủ → confirmation đủ món/tổng/phương thức/khách đưa/thối → xác nhận → hóa đơn #92, “Thu tiền mặt thành công, thối 0 ₫” |
| B08 | CASHIER, 1024×768 | Nhiều giá product card bị ellipsis (`20.00…`, `85.00…`); vùng giỏ và thanh toán có cuộn riêng. Launcher nằm trên vùng chọn nhanh tiền. Tổng bill và confirmation của đơn #92 vẫn hiện đủ |
| B09 | CASHIER, 768×1024 | Đã xem bố cục sau đổi viewport: header nhiều hàng, giỏ chuyển thành sheet. Chưa hoàn thành một giao dịch ở portrait; không ghi PASS portrait |
| B10 | KITCHEN, 1366×768 | Login vào trạm Bếp → thấy phiếu #1/Bàn 01 → Nhận làm chuyển Mới sang Đang làm → Sẵn sàng giao làm phiếu rời hai lane. Hàng đợi rỗng có “Không có phiếu”. Chưa kiểm bước SERVICE giao món cuối cùng |
| B11 | KITCHEN, 1366×768 | Khi phiếu đang làm, dừng đúng server demo → UI “Dữ liệu có thể cũ”, vẫn giữ phiếu và nút. Bật lại server → log nhiều GET `after_revision=11` trả 200 nhưng UI vẫn báo cũ. Bấm Thử tải lại mới chuyển “Đã đồng bộ 07:54” |
| B12 | CASHIER / reload sau gián đoạn | Lần mở lại POS lúc server đã dừng cho “Đang kiểm tra ca…” và toast lỗi mạng. Bật lại rồi reload tải được shop/ca. Đây là mất server, không phải OS airplane mode hay offline-sale PASS |

Phiếu #1 được gửi trước khi task được tiếp tục nhiều giờ sau; tuổi 407–408 phút trên KDS không được coi là lỗi đồng hồ. Source serializer hiện thêm `Z` cho `created_at` (`fselling/services/fnb_service.py:2140`), nên không suy diễn lỗi lệch múi giờ từ ảnh này.

### 2.2 Automated/source evidence riêng

Đã chạy một lần, đều exit 0:

```text
node tests/js/fnb-station-r1b.test.js  -> fnb station controller ok
node tests/js/pos-offline-ui.test.js  -> pos-offline-ui harness: 1 passed
node tests/js/auth-sessions-r4.test.js -> auth sessions r4 harness: 1 passed
```

Một probe Node không sửa file, dùng controller KDS với request giả: load thành công → lỗi → `{changed:false}` để lại event cuối `error`; mutation 403 cho `pending=null`, event `transition-error`; gọi retry không phát thêm event. Đây là reproduction logic, không browser 403 PASS. Không chạy lại các gate R4 hay full suite.

## 3. Ma trận vai trò × thiết bị × hành trình

| Vai trò | Thiết bị chính / bổ trợ | Hành trình ưu tiên | Bố cục đề xuất | Evidence / phần chưa kiểm |
|---|---|---|---|---|
| Chủ shop | Desktop 1366×768, 1024×768 | Thống kê → đơn/đối soát → quản lý nhân viên/thiết bị; kho/nhập/kiểm kê sâu | Sidebar hiện hữu; bảng cho dữ liệu sâu; trạng thái tải theo vùng | B01; thao tác quản trị sâu, đa shop, thu hồi staff: TEST_GAP |
| Chủ shop | Mobile 390×844, 360×640 | Kiểm tra nhanh Cần xử lý → chi tiết → xử lý đúng quyền; tìm thiết bị mất | Ưu tiên Cần xử lý, số liệu có khoảng thời gian; quản trị sâu vẫn truy cập được từ menu | B02–B04; complete action-center journey: TEST_GAP |
| Thu ngân | PC/tablet ngang 1024×768+, tablet dọc 768×1024 | Mở ca → chọn/quét hàng → xem giỏ → thu tiền → receipt/lịch sử; F&B tính tiền và bàn giao | Hai vùng khi đủ rộng; portrait sheet rõ bước; tổng tiền/CTA đủ chỗ và không bị utility che | B07–B09; F&B checkout tablet, portrait payment, scanner: TEST_GAP |
| Phục vụ | Điện thoại 360×640 / 390×844, một tay | Chọn bàn → thêm/sửa ghi chú → xem bill → gửi → xem món sẵn sàng → giao; bàn giao thu ngân | Một vùng nội dung tại một thời điểm, thanh chuyển Món/Bill/Đã gửi gần ngón cái | B05–B06; giao món/ghi chú/variant complete và thiết bị thật: TEST_GAP |
| Bếp/bar | Màn cố định 1366×768, kiểm thêm 1920×1080 | Nhận phiếu → nhận làm → hết món/phục hồi → sẵn sàng giao | Hai lane, tên bàn/món/số lượng lớn; freshness và pending tách riêng | B10–B11 Bếp; BAR, hết món, khoảng cách đọc thực tế: TEST_GAP |
| Tất cả | Browser và installed PWA trên từng nền tảng | Login/expired/revoke; cài/mở lại/cập nhật; mạng chậm/mất/kết nối lại | Điểm vào Trợ giúp/Thiết bị trong header/menu; trạng thái nghiệp vụ tại vùng tác vụ | B03/B12 và source; install/offline/cold start/upgrade thật: TEST_GAP |

Mobile không bị cấm quản trị sâu và không có quyền mới. Chủ shop mobile ưu tiên nhanh là thay hierarchy/điểm vào, không viết dashboard API mới. CASHIER mobile hẹp là fallback; không ép giao diện SERVICE dùng chung sheet thanh toán của cashier.

## 4. Phát hiện P0–P3 và nguyên nhân gốc

P0 = mất/an toàn tiền/đơn/quyền bị phá đã có bằng chứng, cần chặn phát hành; P1 = cản trở tác vụ chính hoặc làm hiểu sai trạng thái; P2 = tăng thao tác/khó đọc/phục hồi kém ở tác vụ phụ; P3 = tinh chỉnh. **Chưa phát hiện P0 có bằng chứng trong discovery này**; không có nghĩa toàn sản phẩm không có P0.

| ID | Mức | Phát hiện, tác động và căn cứ | Nguyên nhân / nguyên tắc sửa |
|---|---|---|---|
| F01 | P1 | Utility che CTA: B04 đo giao nhau thực tế; B08 nằm trên quick-cash. `static/css/session-device-r4.css:1–6`, `static/js/session-device-r4.js:249`; PWA banner cũng fixed bottom/z9999 (`static/js/pwa.js:44–55`) nhưng va chạm banner chưa browser-reproduce | Launcher được append toàn cục, không có slot theo layout. Chuyển vào header/menu hiện hữu; không chữa bằng nâng z-index CTA hay dịch từng trang |
| F02 | P1 | Mất khả năng rà bill trên SERVICE mobile thấp, B06; `static/css/fnb-r1a.css:381`, `:441–461`, `:470–471` | Grid lấy tối thiểu 290px/54dvh cho menu, footer không co, phần bill bị ép. Dùng Món/Bill/Đã gửi đổi vùng hiển thị, một DOM/controller; không chia dọc hai vùng cuộn nhỏ |
| F03 | P1 | Dialog quản lý phiên gần như không đọc được, B03. `static/css/session-device-r4.css:8–18` đặt nền tối + color inherit; body F&B/Seller màu tối | Component không định nghĩa cặp foreground/background và trạng thái nguy hiểm rõ. Dùng palette sáng hiện hữu; scope tất cả child text/input/card; giữ confirmation, endpoint và quyền R4 |
| F04 | P1 | KDS mạng đã phục hồi vẫn báo cũ, B11; `static/js/fnb-station-r1b.js:23–50` chỉ render khi changed; `:184–218` gộp render queue/freshness | Đồng nhất “dữ liệu có đổi” với “request thành công”. Cập nhật freshness mỗi GET thành công, giữ DOM/focus khi unchanged. Không lấy `navigator.onLine` làm bằng chứng đồng bộ |
| F05 | P1 | KDS 4xx xóa pending nhưng UI vẫn mời retry mutation; probe và `static/js/fnb-station-r1b.js:70–74`, `:125–127`, `:203–211`. Trong khi pending, poll queue có thể xóa warning/hiện nút dù controller vẫn khóa (`:191–201`) | Render không phản ánh pending hiện tại và loại lỗi. Phân biệt 403/409/422/401 với unknown; nút retry chỉ hiện khi còn exact pending. GET thành công không xóa pending mutation |
| F06 | P1 | API chờ không có deadline; lỗi 401/mạng có đánh dấu unknown nhưng chờ vô hạn chưa tới catch. `static/js/api.js:206–289`; F&B đang mở ẩn floor/header (`fnb-r1a.css:441`), cảnh báo poll nằm ở floor (`fnb.html:44,52`, `fnb-r1a.js:1189–1203`). Nguy cơ người phục vụ không thấy lỗi trong màn gọi món; slow request chưa browser-force | Thiếu chính sách chờ và surface trạng thái trong tác vụ hiện tại. Giữ receipt/pending/revision đã có; thêm message chậm/unknown bền tại vùng đang dùng, deadline an toàn theo loại request |
| F07 | P2 | Sau gửi thành công, UI ghi “Bàn chưa gọi món” dù có bill và phiếu, B05; `static/js/fnb-r1a.js:1049`, `static/js/locales/fnb.js:71` | Empty của draft dùng copy cho toàn bàn. Đổi “Không có món chưa gửi”, thêm đường sang Đã gửi; không đổi lifecycle |
| F08 | P2 | Product card tablet cắt số tiền/tên, B08; `static/css/pos.css:625–695`. Icon-only POS back/remove và +/- có tên AX thiếu ngữ cảnh | Grid 126px + giá/tồn cùng hàng + ellipsis; tên icon không đủ. Giá một hàng riêng đủ số; card tăng chiều cao theo content, giảm số cột khi cần. Accessible name gồm thao tác + tên món |
| F09 | P2 | Chủ shop mobile phải đi qua thống kê lớn; biểu đồ bị cắt vùng con B02. Lỗi load dashboard chỉ toast 3s (`static/js/seller.js:925–968`, `static/js/api.js:292–297`) | Desktop hierarchy chuyển thành một cột; partial state không gắn vào widget. Đưa entry Cần xử lý lên trước trên mobile, giữ filter/time context và retry từng vùng; không hiển thị lỗi như 0 |
| F10 | P2; nâng P1 nếu upgrade làm kẹt giao dịch được tái hiện | Cài đặt phụ thuộc event, bỏ qua nhớ vĩnh viễn, không UI rõ cho hỗ trợ/cập nhật. `static/js/pwa.js:14,32–45,82–115`; SW skipWaiting/claim, dọn cache khi activate (`static/sw.js:61–80`), khung chưa có F&B (`:45–56`) | PWA chưa có trạng thái install/update theo tác vụ. Entry hỗ trợ cài lại, lifecycle mặc định waiting cho SW mới; không tự reload trong giao dịch, không quảng cáo offline đủ chức năng chỉ vì cài app |
| F11 | P3 | KDS header “Bếp đang chờ” giữ cả khi đang làm/rỗng, B10; UI trạm có nhiều text hard-coded (`static/js/fnb-station-r1b.js:158–218`) | Tên màn trộn trạng thái và địa điểm. “Bếp”/“Bar” là heading; lane thể hiện trạng thái. Hoàn thiện i18n theo phạm vi screen, không thêm hệ thống dịch |

Heuristic diagnostic tạm thời cho các bề mặt đã xem: **4/10**, không phải điểm usability toàn sản phẩm hay nghiên cứu với người dùng. Visibility/recovery chưa đạt (F04–06, −2); thao tác chính/touch bị che (F01–02, −2); đọc/nhãn không rõ (F03/F07/F08, −2). Navigation cơ bản, quyền SERVICE và confirmation tiền mặt đã có điểm tốt. Để xét mức 9–10 cần hết P1, đủ UAT touch/focus/reader/slow/offline, không chỉ đóng source findings.

## 5. Thiết kế đề xuất cho P1

### 5.1 Hệ thống trình bày tối thiểu — F01/F03

Giữ bố cục/quy ước hiện hữu: nền giấy `#FFFFFF`, nền vùng `#F3F6F9`, chữ `#1E293B`, chữ phụ `#64748B`, hành động nghiệp vụ `#9A3412`, nguy hiểm `#B91C1C`. Cam thương hiệu dùng tiết chế, không dùng cùng một màu cho Đổi tên và Thu hồi. Font hiện hữu/system cho nội dung, weight 700 cho heading; số tiền tabular-nums cùng font. Không thêm font/CDN. Dấu hiệu riêng của sản phẩm là **thanh ngữ cảnh có bàn/đơn + số tiền + trạng thái ghi nhận**, không thêm hero/decorative animation.

Tái dùng slot header hoặc menu Công cụ/Tài khoản cho “Phiên và thiết bị” và “Cài ứng dụng”. F&B trong màn bàn có nút “Khác” cạnh tên bàn, tối thiểu 44×44. KDS ở nhóm công cụ đầu trang. Login đặt entry cài app cạnh form; không nổi trên bottom CTA. Không còn launcher toàn cục fixed bottom. Dialog đang mở giữ close rõ, focus trong dialog, Escape/close trả focus về trigger; không tự đóng khi request thu hồi đang chưa rõ kết quả.

```text
Header: [← Bàn]  Bàn 01 · Sảnh demo       [Khác]
Khác:   Phiên và thiết bị | Cài ứng dụng | Trợ giúp

Phiên và thiết bị                              [Đóng]
Thiết bị này · Đang dùng
Tên / loại / hoạt động gần nhất / hết hạn
[Đổi tên]                 [Thu hồi phiên…]
                         [Thu hồi thiết bị…]
Thu hồi thiết bị “Máy quầy 1”?
Các phiên của thiết bị này sẽ bị thu hồi.
[Giữ lại]                              [Thu hồi thiết bị]
Trạng thái request/error ngay dưới xác nhận, không chỉ toast.
```

Giữ wording ảnh hưởng offline theo kết quả/hợp đồng R4; không hứa xóa dữ liệu từ xa. Đổi tên → inline input hiện hữu; network fail giữ tên đang nhập. Thu hồi lỗi xác định cho retry đúng thao tác; request chưa rõ thì tải lại danh sách trước khi khẳng định thành công. Màu/text phải đọc được trên Seller/POS/F&B/KDS; lỗi B03 sửa tại stylesheet component, không từng parent.

### 5.2 Phục vụ một tay — F02/F06/F07

Từ 760px trở xuống dùng một vùng nội dung với ba nút chuyển view trong cùng phiên bàn; màn thấp ngang cũng dùng bố cục này khi vùng bill không đủ. Đây là view UI, không tạo tab browser hoặc controller thứ hai. Desktop giữ menu/bill song song. Mỗi view nhớ scroll trong phiên bàn và tên view hiện tại bằng `aria-pressed`/nhãn; không tự chuyển focus khi polling.

```text
┌────────────────────────────────────┐
│ ← Bàn       Bàn 01          Khác   │
│ Đã cập nhật 14:30 / Dữ liệu có thể cũ│
├────────────────────────────────────┤
│ MÓN: [Tìm món                 ]    │
│ [Danh mục…]                       │
│ [Bánh mì 20.000 ₫] [Trà 10.000 ₫] │
│                                    │
│ hoặc BILL:                         │
│ Bánh mì                 [-] 1 [+] │
│ [Ghi chú…]             [Hủy…]     │
│ Tạm tính                20.000 ₫  │
│                                    │
│ hoặc ĐÃ GỬI: Chờ / Sẵn sàng / Đã giao│
│ Phiếu #1 · Bếp · 1× Bánh mì       │
├────────────────────────────────────┤
│ [Món] [Bill · 1 chưa gửi] [Đã gửi] │
│ Bill: [Gửi 1 món đến bếp/bar]      │
└────────────────────────────────────┘
```

- Chọn món vẫn gọi flow thêm line hiện tại. Phản hồi tức thì “Đang lưu món…”; chỉ hiện “Đã lưu” sau server. Không tự gọi đó là “đã gửi bếp”. Badge chưa gửi phản ánh server + trạng thái pending phân biệt rõ.
- Sau thêm ở Món, giữ vị trí menu, badge Bill cập nhật. Người dùng mở Bill để rà món/ghi chú rồi gửi bằng CTA đã có. Không thêm confirmation mọi lần gửi; chính Bill là bước rà. Hủy/chuyển/gộp vẫn theo approval/confirm hiện hữu.
- Bill có một vùng cuộn đủ chỗ; footer tối đa nhóm chuyển view + CTA chính, tránh hướng dẫn vai trò lặp nhiều dòng. Thông tin bàn giao đặt tại Khác/help và hiển thị ngắn khi cần thu tiền. Hủy phiên trống chỉ hiện khi hành động có ý nghĩa, không dành một hàng disabled thường trực.
- Đã gửi gom theo phiếu/trạm, phân biệt “Sẵn sàng giao” và “Đã giao”. SERVICE chỉ xác nhận giao món đúng API hiện có, không nút tiền. Các roles checkout vẫn có entry Tính tiền khi đủ điều kiện; layout không nới guard.
- Quay về bàn giữ pending/draft theo key username/shop/session hiện có. Không coi rời view là hủy server request. Nút Back/Escape không gửi lại. Nếu kết quả chưa rõ, badge/bảng phục hồi phải còn; không cho đổi identity bypass seal.
- Khi bàn phím mở: header/ngữ cảnh giữ gọn, nội dung cuộn đến field, CTA không che field; ưu tiên dòng chảy tài liệu khi chiều cao quá thấp thay vì ép min-height. Không dùng viewport giả định để tuyên bố bàn phím thật PASS.

### 5.3 Trạng thái tác vụ và retry — F04/F05/F06

Ba trục độc lập: **kết nối** (không rõ/đang kiểm/đã liên lạc/lỗi), **độ mới** (thời điểm GET thành công), **nghiệp vụ** (chưa gửi/đang gửi/chưa rõ kết quả/đã xác nhận/bị từ chối). Ví dụ mạng đã lên nhưng đơn còn pending: phải hiện cả hai, không một badge xanh thay tất cả.

```text
Bếp · Shop demo                   Lần cập nhật thành công 14:30:12
Không tải được phiếu mới. Đang giữ dữ liệu lúc 14:30:12. [Tải lại]

Phiếu #1 · Bàn 01 · Bánh mì ×1
Đang xác nhận “Sẵn sàng giao”…
Chưa xác định kết quả. Kiểm tra phiếu trước khi làm tiếp.
[Kiểm tra trạng thái]  [Thử lại thao tác đang chờ]
```

- 0s: disable thao tác lặp cùng nghiệp vụ ngay và hiển thị đang xử lý; không delay feedback thanh toán. 3s: tiếp tục indicator; 10s: “Máy chủ phản hồi chậm. Đang kiểm tra kết quả”; không fake phần trăm. Mốc là đề xuất UX cần owner duyệt.
- GET foreground deadline đề xuất 15s, nền 15s; error tại vùng tương ứng + retry. Request epoch chống response cũ ghi đè vẫn giữ. Tác vụ upload/export có policy riêng, không áp timeout cứng toàn `apiCall` mù quáng.
- Mutation thông thường đến 30s có thể kết thúc chờ phía client nhưng phải chuyển **unknown**, không thành rejected. Abort không có nghĩa server rollback. Response body lỗi/đứt sau headers và 5xx mutation cũng phải giữ trạng thái chưa rõ nếu không có bằng chứng từ chối. Không gộp mọi 4xx thành an toàn bỏ operation, đặc biệt 401 theo R4.
- Unknown: giữ exact pending đang có; offer đọc entity/check/order trước, rồi retry cùng ID/payload chỉ khi endpoint đã idempotent. Nếu không có ID/lookup phù hợp, hiển thị mã thực thể và hướng kiểm tra lịch sử, không nút tạo lại. Không thêm API mới trong Plan 5.
- KDS GET `changed:false`: cập nhật giờ thành công và lỗi tải; không thay `innerHTML` danh sách, không mất focus/scroll. `changed:true`: cập nhật phiếu nhưng không xóa pending chưa giải quyết. Nếu phiếu biến mất khỏi lane, phải đọc trạng thái xác định trước khi gọi action cũ thành công.
- KDS 403: “Bạn không còn quyền thực hiện thao tác này”; không retry mutation mù. 409: “Phiếu đã thay đổi”; tải version mới, yêu cầu rà lại. 422/validation: giữ field, chỉ rõ lỗi. 401: theo terminal auth R4, dừng protected action, login theo route guard, không đổi thành offline authorization.
- Chưa rõ kết quả: vùng thông báo bền, có tên thao tác + mã bàn/phiếu/đơn + next action. Toast chỉ bổ trợ. Refresh nền không được biến unknown thành “Đã đồng bộ” nghiệp vụ.
- Reuse controllers/`apiCall`/offline panel và error.code. Chỉ thêm state tối thiểu còn thiếu tại owner của flow; không service bus, generic workflow framework hay outbox thứ hai. KDS pending hiện chỉ trong RAM: trước khi hứa phục hồi qua reload phải duyệt việc tái dùng pattern lưu exact pending F&B, không secret; nếu chưa làm được phải cảnh báo hạn chế rõ và bắt đọc lại entity sau reload.

## 6. POS, owner mobile và PWA

### POS

Giữ hai vùng ở 1024×768; card dành riêng hàng giá đầy đủ và hàng tồn, category giảm ưu tiên. Tên dài được mở đủ trong giỏ/chi tiết trước chốt; size không bị cắt mất phần phân biệt. Số tiền không ellipsis. Giỏ luôn có đường cuộn rõ; vùng tổng/tiền khách đưa/thiếu hoặc thối/CTA không bị launcher che. Portrait dùng cart sheet sẵn có, có tên “Giỏ và thanh toán”, close/focus return, nút mở giỏ có số món/tổng; receipt phải có entry dễ tìm sau bán. Không thay phép tính hoặc confirmation tiền mặt đã chạy tốt ở B07.

Icon back/remove/increment/decrement phải có accessible name cụ thể; label không chỉ ký tự icon. Quét camera không tự xin quyền ngay page load; scanner phần cứng và soft keyboard phải không kích checkout ngoài ý muốn. Return/cancel/debt/close shift vẫn mở đúng dialog hiện hữu, luôn có mã đơn, số tiền, tác động và lý do/approval khi bắt buộc.

### Chủ shop mobile

Header shop + “Cần xử lý (n)” và hai entry “Đối soát”, “Thiết bị”; lấy từ dữ liệu/role gate hiện hữu. Thống kê giữ nguyên filter/khoảng thời gian, không tự gọi tổng mọi thời gian là “hôm nay”. Biểu đồ xuống sau danh sách tác vụ; vùng biểu đồ co theo container, không làm mất nhãn mà không có bảng thay thế. Đơn/ca bảng rộng có vùng cuộn được đặt tên; chi tiết tiền vẫn đầy đủ. Lỗi widget giữ data cũ có timestamp và retry, empty thật khác request fail. Không auto-generate dữ liệu demo trong production first run.

### PWA

1. “Cài ứng dụng” ở slot utility. Nếu có deferred install event, click mới prompt; đang installed không nhắc lại. Dismiss đóng lời mời, entry help vẫn tìm được. Nếu không có event, giải thích khả năng cài phụ thuộc browser và hướng dùng menu browser; không hiện nút Cài bấm không tác dụng. Platform instruction phải kiểm chứng trên thiết bị trong UAT.
2. UI phân biệt cài app với khả năng bán offline. Hiển thị rõ “Cài ứng dụng không tự bật bán offline”; offline Retail cần chuẩn bị hợp lệ theo flow hiện hữu. F&B/KDS không có nhận mutation offline mới; chỉ dữ liệu gần nhất/bản nháp/pending theo hợp đồng hiện có.
3. Update SW: bỏ kích hoạt cưỡng bức mặc định; để worker mới waiting, app hiện “Có bản cập nhật — mở lại sau khi hoàn tất việc đang làm”. Không tự reload, không cung cấp nút xóa cache/storage như cách sửa giao dịch. Không thêm giao thức điều phối nhiều tab chỉ để update. Muốn “Cập nhật ngay” sau này phải có kiểm chứng an toàn của mọi tab/pending/offline queue; chưa nằm trong phiên bản tối thiểu.
4. Tiếp tục HTML network-first, assets có version, API network-only. Không cache auth response hoặc dữ liệu tiền/kho. Thêm khung F&B/KDS chỉ cho offline informational shell và chỉ khi dependencies sẵn; cold navigation chưa cache cần offline page rõ “Cần kết nối để mở màn này”, không âm thầm đưa sang landing rồi coi thành công. Không prefetch dữ liệu protected.
5. Storage unavailable/quota/private mode: vẫn dùng web online nếu có thể; báo không thể đảm bảo lưu offline trước khi thu tiền offline. Không tự xóa receipt để nhường chỗ. Update thất bại giữ bản hiện tại và pending; người dùng không bị yêu cầu xóa site data.

Tham chiếu chính thức đã kiểm 2026-09-11: [MDN beforeinstallprompt](https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeinstallprompt_event) nêu event có mức hỗ trợ hạn chế; [web.dev SW lifecycle](https://web.dev/articles/service-worker-lifecycle) giải thích waiting và rủi ro worker mới điều khiển trang cũ khi skipWaiting. Đề xuất waiting là quyết định an toàn cho POS, không kết luận đã tái hiện lỗi upgrade ở baseline. Không cần tham chiếu đối thủ cho các lỗi này.

## 7. State inventory: thấy gì, làm gì, phục hồi ra sao

Áp dụng vocabulary chung nhưng giữ các state machine nghiệp vụ độc lập. Cột evidence ghi trạng thái hiện tại, không phải acceptance PASS của thiết kế mới.

| Trạng thái | Nội dung người dùng thấy | Hành động / đường phục hồi | Evidence hiện tại |
|---|---|---|---|
| Happy/default | Shop + role + bàn/đơn/ca + nội dung đúng screen | CTA chính theo quyền | B01/B05/B07/B10 |
| Initial loading | “Đang tải …”, khung vùng, chưa hiện số 0 giả | Chờ; deadline chuyển lỗi vùng | B01/B07/B10; slow TEST_GAP |
| Empty thật | Chưa có ca/bàn/món/phiếu, nói đúng đối tượng | Owner setup; staff liên hệ owner; draft rỗng sang Đã gửi | B05/B07/B10; first shop TEST_GAP |
| Search zero | Không có kết quả cho từ khóa | Xóa từ khóa/đổi danh mục, không mất giỏ | Source; browser TEST_GAP |
| Partial | Vùng nào tải được giữ vùng đó; vùng lỗi ghi thời điểm cũ | Retry vùng, giữ filter/scroll | Seller source; browser TEST_GAP |
| Slow read | Quá 10s báo chậm, quá deadline báo chưa tải được | Retry read; không gợi ý tạo chứng từ | Source gap F06; browser TEST_GAP |
| Submitting | Tên thao tác + thực thể + “Đang xác nhận…” | Khóa duplicate; không auto-success | B07/B10 happy transitions; forced delay TEST_GAP |
| Duplicate action | Một request đang xử lý, same pending | Chờ hoặc retry same ID sau kết quả chưa rõ | Harness KDS; browser double tap TEST_GAP |
| Success | Mã receipt/ticket, số tiền/trạm, state đã xác nhận | Đơn mới/in hoặc sang Đã gửi | B05/B07/B10 |
| Validation | Lỗi cạnh field; số tiền thiếu/lý do/reference | Sửa field, giữ input; focus lỗi đầu | B07 tender empty; các form khác TEST_GAP |
| Permission denied 403 | Không còn quyền, không gọi đó là mất mạng | Quay về vùng được phép; owner/manager nếu nghiệp vụ yêu cầu | KDS probe; browser TEST_GAP |
| Conflict 409 | Thực thể đã đổi; không tự merge tiền/món | Read mới → rà thay đổi → ý định mới theo contract | Source R1–R4; browser TEST_GAP |
| Session expired/revoked 401 | Lý do phiên + kết quả mutation chưa rõ nếu có | Seal/logout R4; login lại; kiểm tra entity trước retry | R4 harness; browser TEST_GAP |
| Offline / server unreachable | Dữ liệu cuối + timestamp + khả năng nào còn dùng được | Read retry; Retail dùng offline flow hợp lệ; F&B/KDS không gửi mới | B11/B12 server unreachable; OS offline TEST_GAP |
| Unknown mutation | “Chưa xác định kết quả”, ID + hành động | Read entity; same ID retry nếu hợp lệ; không tạo mới | Source/probe; lost response browser TEST_GAP |
| Reconnect | “Đang kiểm tra lại”, không ngay “mọi đơn đã đồng bộ” | Refresh identity/lease/revision; queue theo policy đã có | B11 GET reconnect; offline sync TEST_GAP |
| Ready/retryable offline receipt | Số phiếu chưa đồng bộ + lần retry/giờ dự kiến | Retry theo offline engine hiện có | `pos.js:2726–2802`, offline harness; browser TEST_GAP |
| Blocked/quarantined/sealed | “Cần xử lý” + lý do phù hợp quyền, không “đã bán xong trên server” | Mở recovery hiện có, tham chiếu phiếu local; không bỏ qua identity | `pos.js:2854–2975`, `offline-ban.js`; browser TEST_GAP |
| Reload/back/crash | Bản nháp và pending chỉ khi lưu được, rõ giới hạn | Restore đúng user/shop/session; đọc server, không replay tự động | F&B persist source; browser recovery TEST_GAP |
| Install unsupported/dismissed | Entry hướng dẫn, không nút chết | Dùng browser online; mở trợ giúp khi muốn cài | Source PWA; browser install TEST_GAP |
| Update waiting/failure | Có bản mới / hiện đang dùng bản cũ, không gián đoạn bill | Hoàn tất việc đang làm, mở lại; không clear data | Thiết kế đề xuất; TEST_GAP |
| Overflow/keyboard | Tên/số tiền/field không bị utility che; vùng cuộn rõ | Tìm kiếm/filter/scroll/focus; không scale cả desktop xuống | B02/B06/B08; keyboard vật lý/ảo TEST_GAP |

### Coverage theo bề mặt

| Bề mặt | State bắt buộc ngoài happy | Điểm phục hồi / giới hạn |
|---|---|---|
| Seller dashboard/action center | Loading, empty, partial, slow, error, 403/401, stale/overflow | Retry widget, giữ ngày/shop; tác vụ ghi giữ contract riêng |
| POS checkout/receipt | Empty, validation, submitting, duplicate, unknown, 401/403, offline, reconnect | Pending checkout/receipt hiện hữu; lịch sử server; không mất operation |
| F&B floor/menu/bill/checkout | Loading, empty, pending, conflict, unknown, offline, reconnect, disabled feature | Cùng controller, draft/pending hiện hữu; kiểm revision trước gửi/thu |
| KDS | Loading, empty, stale, unchanged success, mutation pending, 403/409, unknown | GET freshness tách mutation; đọc phiếu/phiên; không chuyển pending thành success do poll |
| Phiên/thiết bị | Loading, empty, rename, confirming, submitting, self revoke, error | Dialog hiện có; self revoke theo auth redirect; không xóa dấu vết nghiệp vụ |
| PWA shell/install | Unsupported, dismissed, installed, offline cold/warm, update waiting/fail, storage fail | Help slot; app online fallback; giữ receipt và auth/lease guards |

## 8. Acceptance criteria và browser-UAT matrix

### Tiêu chí đo được

- **AC01 / F01:** 320×568, 360×640, 390×844, 768×1024, 1024×768, 1366×768: không utility/banner/keyboard che CTA, tổng tiền, field focus. Không horizontal scroll toàn trang; bảng rộng chỉ cuộn trong vùng có tên.
- **AC02 / F02:** SERVICE mở bàn → thêm hai món → sửa ghi chú → Bill rà được từng món → gửi → Đã gửi → nhận món sẵn sàng → giao, không đổi role hoặc mất bàn/ngữ cảnh. 360×640 có thể đọc và thao tác dòng món đầy đủ bằng cuộn tự nhiên; không vùng bill 28px. Hẹp không nhân đôi DOM/state.
- **AC03 / F03:** Chữ thường contrast ít nhất 4.5:1; chữ lớn 3:1; focus visible và không bị che. Dialog có name, close, Tab/Shift+Tab, Escape, return focus; text/input/error đọc được trên từng page. Touch product target 44×44 tối thiểu, CTA gửi/thu/nhận làm hướng tới 48px; không gọi 44px là yêu cầu AA của WCAG 2.5.8 (AA có ngưỡng 24px và ngoại lệ).
- **AC04 / F04–05:** GET success unchanged xóa đúng lỗi đọc, cập nhật last success, không mất focus. Poll không xóa pending mutation. 403 không có nút retry chết; 409 reload và yêu cầu xét lại; unknown giữ same ID/payload, click lặp không thêm mutation.
- **AC05 / F06:** Delay/never/lost body phản hồi đúng các mốc đề xuất; không coi timeout/abort/5xx/401 là bằng chứng rollback. Không retry POST không idempotent. Sau re-auth kiểm entity và quyền; không restore approval token từ draft.
- **AC06 / F07–09:** Empty draft ghi đúng “Không có món chưa gửi”; giá/tổng không ellipsis; product/variant phân biệt được; icon có tên. Owner mobile có entry xử lý nhanh, không gọi lỗi data là 0 hoặc đổi nghĩa kỳ thống kê.
- **AC07 / F10:** Install/dismiss/reopen/unsupported có next step; standalone không nhắc cài; offline shell không hứa giao dịch offline F&B. Update không auto-reload hoặc xóa pending/local receipt. API response không trong cache, mutation không qua SW queue. Migration/backend không đổi.
- **AC08 / safety:** Cùng operation chỉ một business effect; kiểm ledger/stock/ticket count bằng automated/DB evidence riêng khi test fault. Browser success phải kèm state UI xác định, không suy từ HTTP 200 đơn lẻ. Full suite chỉ owner-controlled gate sau implementation, không ở design.

Chuẩn tham chiếu: [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/), [Target Size Minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), [Focus Not Obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html). Đây là acceptance target, chưa phải chứng nhận accessibility.

| UAT | Role/device | Kịch bản / kết quả cần thấy | Hiện tại |
|---|---|---|---|
| U01 | Owner desktop + mobile | Login, Cần xử lý → chi tiết → back; giữ shop/ngày; partial fail rõ | B01/B02 một phần; TEST_GAP end-to-end |
| U02 | Owner/Cashier/Service/KDS tất cả kích thước | Mở utility, rename/revoke confirmation, close/Tab/Escape; không overlap/contrast lỗi | B03/B04 fail baseline; TEST_GAP sau sửa và role khác |
| U03 | SERVICE mobile 360/390 | Mở bàn, thêm 2 món, variant/ghi chú, kiểm Bill, gửi đúng trạm, giao, bàn giao thu ngân | B05 một món/gửi; B06 fail layout; TEST_GAP phần còn lại |
| U04 | CASHIER ngang/dọc | Ca → cash thiếu/đủ/dư → confirm → receipt → lịch sử/đơn mới | B07 ngang một đơn tiền đủ; TEST_GAP dư/portrait/lịch sử |
| U05 | CASHIER F&B tablet | Split bill, discount, PIN nếu cần, cash/transfer/debt đúng policy, đóng phiên | TEST_GAP toàn bộ ở Plan 5; chỉ dùng local simulation cho transfer |
| U06 | KITCHEN và BAR cố định | NEW → làm → hết món/resume → ready; danh sách 0/1/50, đọc từ xa | B10 Bếp làm/ready/empty; TEST_GAP Bar và còn lại |
| U07 | KDS | Server stop/start không đổi revision; đổi revision; pending cùng poll | B11 fail unchanged recovery; TEST_GAP pending+poll browser |
| U08 | POS/F&B/KDS | 5s/15s/30s/never; response mất sau commit, body hỏng; double tap hai tab | TEST_GAP browser; harness không thay thế |
| U09 | Mọi role | Revoke/expire giữa dialog/mutation; 403 role change; 409 revision khác | TEST_GAP browser; R4 automated baseline không phải UAT mới |
| U10 | Retail cashier PWA | Chuẩn bị lease → offline cash → reconnect → synced; blocked/quarantine/seal/recovery | TEST_GAP browser; phải dùng giả lập mạng local và DB giả |
| U11 | SERVICE/KDS offline | Mở sẵn → mất mạng; cold start chưa cache; restore đúng context, không gửi mới | B11 server down chỉ một phần; TEST_GAP OS offline/cold |
| U12 | Android Chromium, iOS Safari, desktop PWA | Install/dismiss/reopen, standalone, update khi idle và khi pending, thiếu storage | TEST_GAP: chưa có thiết bị/platform thật |
| U13 | Keyboard/reader/touch | Tab/Shift+Tab/Escape/Enter, zoom 200/400%, reduced motion, 320px, keyboard ảo, safe area | TEST_GAP; screenshot responsive không chứng minh các ca này |
| U14 | Long content/volume | Tên VN dài/emoji, 40% text expansion, 0/1/50 phiếu/1000 sản phẩm, tiền dài | B04 tên bàn dài & B08 giá cắt; TEST_GAP stress toàn bộ |

Mỗi ca sau implementation phải ghi role, viewport, browser/version/standalone, dữ liệu giả, bước, expected/actual, screenshot hoặc DOM cần thiết. PASS chỉ cho phần quan sát; lỗi môi trường phân biệt với lỗi app. Không bật mạng công khai để lấp UAT gap.

## 9. Các lát thiết kế theo dependency

Đây là ranh giới giao việc sau khi duyệt, **không phải implementation plan**: không task-list code/test/commit hay lịch thực hiện.

| Lát | Sản phẩm bàn giao nhỏ | Phụ thuộc / tránh sửa UI hai lần |
|---|---|---|
| A | Slot utility + cặp màu dialog + focus/touch/copy trạng thái dùng chung | Chốt đầu tiên vì F&B/POS/PWA đều dùng; sửa component, không override từng màn |
| B | Mapping error/unknown/freshness và chính sách latency | Chốt trước thay layout; tận dụng state hiện hữu, phân biệt deadline read/mutation |
| C | KDS freshness/pending/recovery UI | Theo B và slot A; không đổi ticket backend |
| D | SERVICE một vùng Món/Bill/Đã gửi + trạng thái trong ngữ cảnh | Theo A/B; giữ controller, quyền, draft/revision; không sửa lại footer khi PWA thêm entry |
| E | POS card/portrait sheet và Owner mobile entry/partial states | Theo A/B; thực hiện riêng Retail và Seller, không gom redesign F&B |
| F | Install help, update waiting, cold shell | Theo A/B; giữ offline engine, không phát minh background-sync; rollout đợi UAT upgrade/offline |

Backend mới như notification push, remote approval, cross-device basket, thêm offline F&B, query theo operation mới, schema pending server, authentication cookie migration, thay seeder production: **ngoài phạm vi**. Nếu evidence cho thấy cần backend để đảm bảo recovery, đánh dấu BLOCKER của lát liên quan và đưa owner quyết định riêng; không tự làm workaround nới invariant.

## 10. Quyết định đã được owner duyệt và self-review

| Quyết định | Nội dung đã duyệt |
|---|---|
| D1 — UI P1 | Chuyển utility khỏi góc dưới vào header/menu, sửa màu dialog chung; giữ chức năng R4 |
| D2 — SERVICE P1 | Mobile dùng Món/Bill/Đã gửi một vùng, rà Bill trước gửi; desktop giữ song song |
| D3 — Recovery P1 | Chốt ba trục trạng thái; read deadline 15s, slow hint 10s, mutation 30s chuyển unknown; từng endpoint xác minh trước áp dụng |
| D4 — PWA | Phiên bản tối thiểu dùng SW waiting, không “Cập nhật ngay” cưỡng bức; cold shell chỉ thông tin, không thêm offline F&B |
| D5 — Scope release | P1 A–D ưu tiên trước polish; Owner mobile/POS/PWA vẫn trong Plan 5 nhưng chia lát, không full redesign |

**Self-review đã thực hiện:** đối chiếu findings ↔ wireframe ↔ AC/UAT ↔ slice; mọi P1 có nguyên nhân và surface sửa; không coi đọc source/harness là browser PASS; giữ tiền/quyền/offline/seal/revision/idempotency. Rà lại wording tránh hứa KDS reload recovery khi pending chỉ RAM. Phân biệt server-down với offline OS và thời gian task gián đoạn với tuổi phiếu. Không phát hiện P0 được xác nhận; các TEST_GAP quan trọng vẫn mở.

**Ghi nhận phê duyệt:** owner đã duyệt toàn bộ D1–D5 bằng phản hồi “duyệt cả 5”, sau khi được giải thích từng quyết định. Các TEST_GAP và giới hạn an toàn vẫn giữ nguyên; phê duyệt thiết kế không biến chúng thành PASS.

**Trạng thái bàn giao:** đã lập implementation plan tại `docs/superpowers/plans/2026-09-11-device-usability-pwa-r5.md` theo D1–D5 đã duyệt. Cập nhật trạng thái 2026-09-11: runtime/tests đã triển khai theo kế hoạch; evidence và TEST_GAP nằm trong `DEVICE_USABILITY_R5_UAT_REPORT.md` ở root. Các quyết định D1–D5 không đổi.
