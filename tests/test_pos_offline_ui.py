"""Khóa các móc UI của bán hàng khi mất mạng ở màn POS.

Test quan trọng nhất là `test_chi_luu_offline_khi_mat_mang_han`. Điều kiện đó
là thứ DUY NHẤT ngăn một đơn bị ghi hai lần: lỗi mạng có thể xảy ra SAU khi
server đã tạo đơn xong, và lúc đó lưu thêm phiếu offline là ghi lần thứ hai với
một khóa khác — không khóa idempotency nào chặn được.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CAU_OFFLINE = [
    "pos.offline.chi_tien_mat",
    "pos.offline.dung_ban_chup",
    "pos.offline.da_luu_phieu",
    "pos.offline.mat_mang",
    "pos.offline.cho_gui",
    "pos.offline.da_dong_bo",
    "pos.offline.co_phieu_loi",
]


def _doc(duong_dan: str) -> str:
    return (ROOT / duong_dan).read_text(encoding="utf-8")


def _bo_chu_thich(ma: str) -> str:
    return "\n".join(dong.split("//")[0] for dong in ma.splitlines())


def test_nut_tim_va_them_khach_pos_co_ten_truy_cap_duoc():
    html = _doc("static/pos.html")

    tim = html[html.index('onclick="timKhachPOS()"'):]
    tim = tim[:tim.index("</button>")]
    them = html[html.index('onclick="hienFormKhachMoi()"'):]
    them = them[:them.index("</button>")]

    assert 'data-i18n-aria-label="pos.customer.search_placeholder"' in tim
    assert 'data-i18n-aria-label="pos.customer.add_title"' in them


# ---------- Điều kiện an toàn ----------
def test_chi_luu_offline_khi_mat_mang_han():
    """Ba điều kiện phải cùng có mặt trong nhánh cứu hộ của `thuTaoDonDangDo`.

    `dangOffline()` là điều kiện chặt nhất và cũng là điều kiện quan trọng nhất:
    nó nghĩa là máy không có đường mạng nào, tức request chưa từng rời khỏi máy.
    Bỏ nó đi là mạng chập chờn cũng lưu phiếu, trong khi server có thể đã tạo
    đơn rồi — thành ra ghi hai lần.
    """
    js = _doc("static/js/pos.js")
    dau = js.index("async function thuTaoDonDangDo(")
    ma = _bo_chu_thich(js[dau:js.index("async function checkout(")])
    assert "OfflineBan.dangOffline()" in ma
    assert "state.payment_method === 'cash'" in ma
    assert "cashTenderedAmount >=" in ma
    assert "luuBanOffline(state)" in ma


def test_module_offline_kiem_navigator_online():
    js = _doc("static/js/offline-ban.js")
    assert "navigator.onLine === false" in js


def test_khong_ban_chuyen_khoan_hay_ghi_no_khi_mat_mang():
    """Chuyển khoản cần QR + webhook ngân hàng; ghi nợ cần kiểm hạn mức trên
    server. Cả hai không làm được khi offline."""
    js = _doc("static/js/pos.js")
    dau = js.index("async function checkout(")
    ma = _bo_chu_thich(js[dau:dau + 2000])
    assert "dangOffline()" in ma
    assert "paymentMethod !== 'cash'" in ma


# ---------- Hàng chờ ----------
def test_phieu_hong_duoc_giu_lai_chu_khong_xoa():
    """Server trả 4xx nghĩa là phiếu này không bao giờ gửi được. Xóa đi là mất
    trắng dấu vết một lần bán CÓ THẬT."""
    js = _doc("static/js/offline-ban.js")
    dau = js.index("async function dongBo(")
    ma = _bo_chu_thich(js[dau:js.index("function luuAnhChupSanPham(")])
    assert "danhDauLoi(" in ma
    vi_tri_4xx = ma.index("ma >= 400")
    doan_4xx = ma[vi_tri_4xx:vi_tri_4xx + 300]
    assert "xoaPhieu" not in doan_4xx, "nhánh 4xx không được xóa phiếu"


def test_loi_mang_giua_chung_thi_dung_lai_giu_phan_con_lai():
    js = _doc("static/js/offline-ban.js")
    dau = js.index("async function dongBo(")
    ma = _bo_chu_thich(js[dau:js.index("function luuAnhChupSanPham(")])
    assert "break" in ma, "gặp lỗi mạng phải dừng vòng lặp, không xóa tiếp"


def test_phieu_gui_theo_dung_thu_tu_da_ban():
    js = _doc("static/js/offline-ban.js")
    assert "luc_luu" in js
    dau = js.index("async function dongBo(")
    ma = _bo_chu_thich(js[dau:js.index("function luuAnhChupSanPham(")])
    assert ".sort(" in ma


def test_gui_lai_thanh_cong_thi_xoa_khoi_hang_cho():
    """`created: false` cũng là đã ghi trên server — đó là lần gửi lại của một
    phiếu đã vào sổ, không phải thất bại."""
    js = _doc("static/js/offline-ban.js")
    dau = js.index("async function dongBo(")
    ma = js[dau:js.index("function luuAnhChupSanPham(")]
    assert "xoaPhieu(phieu.offline_uuid)" in ma


def test_dem_cho_khong_tinh_phieu_da_loi():
    """Phiếu lỗi cần người xử lý, không phải chờ mạng. Đếm lẫn vào là con số
    trên huy hiệu không bao giờ về 0 và thu ngân học cách phớt lờ nó."""
    js = _doc("static/js/offline-ban.js")
    dau = js.index("function demCho(")
    ma = _bo_chu_thich(js[dau:dau + 400])
    assert "!p.loi" in ma


# ---------- Danh mục khi mất mạng ----------
def test_co_ban_chup_danh_muc_de_con_ban_duoc():
    """Mất mạng thì GET /api/products hỏng. Không có bản chụp thì màn POS trống
    trơn và hàng chờ có cũng vô nghĩa."""
    js = _doc("static/js/pos.js")
    dau = js.index("async function loadProducts(")
    ma = _bo_chu_thich(js[dau:dau + 1200])
    assert "luuAnhChupSanPham(" in ma
    assert "docAnhChupSanPham(" in ma
    assert "prepareV1(" in ma


# ---------- Nối vào trang ----------
def test_offline_ban_js_nap_truoc_pos_js():
    """`pos.js` dùng `window.OfflineBan` ngay ở đoạn khởi động cuối file."""
    html = _doc("static/pos.html")
    assert html.index("/js/offline-ban.js") < html.index("/js/pos.js?v=")


def test_pos_html_co_huy_hieu_hang_cho():
    html = _doc("static/pos.html")
    assert 'id="offlineBadge"' in html
    vi_tri = html.index('id="offlineBadge"')
    assert "display: none" in html[vi_tri:vi_tri + 160], "ẩn khi không có gì chờ"


def test_da_bump_phien_ban_pos():
    html = _doc("static/pos.html")
    assert "/js/pos.js?v=20260802-bien-the" not in html
    assert "/js/locales/pos.js?v=20260802-bien-the" not in html
    assert "/js/offline-ban.js?v=" in html
    assert "/js/offline-ban.js?v=20260813-i09-f3&g=20260813-i09-g2h6" in html
    assert "/js/pos.js?v=20260912-r5-9" in html
    assert "/js/api.js?v=20260911-r5-5" in html


@pytest.mark.parametrize("khoa", CAU_OFFLINE)
@pytest.mark.parametrize("ngon_ngu", ["vi", "en"])
def test_du_ban_dich_hai_ngon_ngu(khoa, ngon_ngu):
    locale = _doc("static/js/locales/pos.js")
    khoi = locale.split(f"resources.{ngon_ngu}.translation")[1]
    ket = khoi.find("resources.")
    if ket > 0:
        khoi = khoi[:ket]
    assert f"'{khoa}'" in khoi, f"thiếu bản dịch {khoa} ({ngon_ngu})"


def test_cau_bao_da_luu_noi_ro_khong_can_ban_lai():
    """Thu ngân đang đứng trước mặt khách. Mơ hồ một chữ là họ bán lại lần nữa
    và cửa hàng thu tiền hai lần."""
    locale = _doc("static/js/locales/pos.js")
    vi = locale.split("resources.en.translation")[0]
    dong = [d for d in vi.splitlines() if "'pos.offline.da_luu_phieu'" in d][0]
    assert "tự gửi" in dong or "không cần bán lại" in dong
