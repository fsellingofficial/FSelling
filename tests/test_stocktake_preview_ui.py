"""Hồi quy phép tính modal xác nhận kiểm kê theo lô.

Các giá trị này được chạy bằng Node trên đúng helper trong seller.js: kiểm tra
chuỗi đơn thuần sẽ không bắt được lỗi cũ, nơi modal cộng batch rows và bỏ qua
dòng reconciliation synthetic của backend.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tom_tat(phieu: dict[str, dict]) -> dict:
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    start = js.index("function kkTomTatPreview()")
    end = js.index("function kkVeBang()", start)
    helper = js[start:end]
    script = (
        f"let phieuKiemKe = {json.dumps(phieu)};\n"
        f"{helper}\n"
        "process.stdout.write(JSON.stringify(kkTomTatPreview()));\n"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def _tracked(*, stock: int, batches: dict[str, tuple[int, int]], deficit: int) -> dict:
    return {
        "theoLo": True,
        "stock_snapshot": stock,
        "offline_deficit_qty": deficit,
        "lo": {
            batch_id: {"quantity_snapshot": snapshot, "counted": counted}
            for batch_id, (snapshot, counted) in batches.items()
        },
    }


def test_preview_tracked_ton_am_hien_dung_reconciliation_server_tra_ve():
    """Product 3, batch 5, deficit 2, đếm vẫn 5 phải báo 3 -> 5 và 1 dòng.

    Một dòng ở đây chính là `offline_deficit_reconciled` synthetic của server;
    batch không đổi nên không được tính thêm một lần.
    """
    result = _tom_tat({"42": _tracked(stock=3, batches={"8": (5, 5)}, deficit=2)})

    assert result["tongTon"] == 3
    assert result["tongDem"] == 5
    assert result["tongDem"] - result["tongTon"] == 2
    assert result["soDongLech"] == 1
    assert result["sanPham"] == [
        {"productId": 42, "truoc": 3, "sau": 5, "lech": 2, "soDongLech": 1}
    ]


def test_preview_deficit_only_batch_delta_va_nhieu_san_pham_khong_double_count():
    """Giữ cả deficit-only, batch delta + deficit và hàng thường trong một phiếu."""
    result = _tom_tat({
        # Không còn batch dương: server vẫn rebuild 0 rồi đóng deficit, -2 -> 0.
        "1": _tracked(stock=-2, batches={}, deficit=2),
        # Batch giảm 1 và reconciliation +2: Product.stock 3 -> 4, không phải 5.
        "2": _tracked(stock=3, batches={"9": (5, 4)}, deficit=2),
        # Hàng thường không có deficit vẫn giữ công thức cũ.
        "3": {"theoLo": False, "stock_snapshot": 10, "counted": 8},
        # Hàng theo lô bình thường cũng chỉ lấy tổng batch counted.
        "4": _tracked(stock=7, batches={"10": (7, 9)}, deficit=0),
    })

    assert [(row["truoc"], row["sau"], row["lech"], row["soDongLech"])
            for row in result["sanPham"]] == [
        (-2, 0, 2, 1),
        (3, 4, 1, 2),
        (10, 8, -2, 1),
        (7, 9, 2, 1),
    ]
    assert result["tongTon"] == 18
    assert result["tongDem"] == 21
    assert result["tongDem"] - result["tongTon"] == 3
    # 1 synthetic deficit-only + (batch change + synthetic) + normal + batch.
    assert result["soDongLech"] == 5


def test_modal_dung_summary_theo_product_va_giu_snapshot_payload():
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    begin = js.index("async function kkApDung()")
    end = js.index("function kkHienKetQua(", begin)
    apply = js[begin:end]

    assert "const preview = kkTomTatPreview();" in apply
    assert "const tongTon = preview.tongTon;" in apply
    assert "const tongDem = preview.tongDem;" in apply
    assert "different: dinhDangSoSeller(preview.soDongLech)" in apply
    # Token ABA và body giao tiếp server không đổi khi sửa preview.
    assert "offline_deficit_snapshot: d.offline_deficit_snapshot" in apply
    assert "batches: Object.entries(d.lo).map" in apply
    # Hàng KHÔNG theo lô cũng phải echo token, nếu không server từ chối cả phiếu
    # bằng OFFLINE_DEFICIT_SNAPSHOT_REQUIRED khi sản phẩm còn evidence tồn âm.
    assert apply.count("offline_deficit_snapshot: d.offline_deficit_snapshot") == 2


def test_kkdem_chup_token_evidence_ngay_luc_bat_dau_dem():
    """Token phải chụp cùng lúc với `stock_snapshot`, không phải lúc bấm Áp dụng.

    Chụp muộn là chụp trạng thái đã đổi trong lúc đếm — đúng thứ token sinh ra
    để chặn.
    """
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    begin = js.index("function kkDem(sp, soLuong)")
    end = js.index("function kkThemTheoLo(sp)", begin)
    dem = js[begin:end]

    assert "deficitKiemKeTheoSanPham[sp.id]" in dem
    assert "offline_deficit_snapshot: deficit.snapshot || null" in dem
    assert "stock_snapshot: sp.stock" in dem


def test_ap_dung_xong_phai_nap_lai_token_truoc_phieu_ke_tiep():
    """Token đã dùng xong là token chết.

    Xóa `phieuKiemKe` mà không nạp lại map deficit thì phiếu thứ hai trong cùng
    tab gửi lại đúng token vừa bị tiêu, và người dùng nhận 409 cho tới khi mở
    lại tab — lỗi trông như hỏng chức năng chứ không như bảo vệ.
    """
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    begin = js.index("async function kkApDung()")
    end = js.index("function kkHienKetQua(", begin)
    apply = js[begin:end]

    reset = apply.index("phieuKiemKe = {};")
    assert "await kkNapLo();" in apply[reset:], "phải nạp lại token sau khi áp dụng"


def test_html_bump_cache_buster_khi_seller_js_doi():
    """Quên bump `?v=` là người dùng chạy code cũ trong im lặng."""
    html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    assert "/js/seller.js?v=20260911-r5-5" in html
