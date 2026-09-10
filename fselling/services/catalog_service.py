"""Nghiệp vụ danh mục & sản phẩm (gồm kiểm tra file ảnh upload)."""
from __future__ import annotations

import os
import pathlib
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..core import thoi_gian
from ..core.config import (
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_SIZE,
    UPLOAD_DIR,
)
from ..core.i18n import tr
from ..core.money import ExactMoneyError, checked_multiply, exact_vnd
from ..core.numeric_limits import MAX_SAFE_QUANTITY
from ..dependencies import (
    PERMISSION_CATALOG_READ,
    PERMISSION_INVENTORY,
    PERMISSION_SALE,
    has_cost_visibility,
    has_shop_operator_access,
    require_any_staff_permission,
    require_cost_visibility,
    require_shop_access,
    require_staff_permission,
)
from ..schemas.catalog import CategoryUpdate
from . import auth_session_service, inventory_service
from .log_service import log_system_action
from .offline_fingerprint import canonical_time_text

DEFAULT_PRODUCT_IMAGE = "https://placehold.co/150x150/1E293B/FFF?text=SP"
DEFAULT_CATEGORY_NAME = "Chưa phân loại"

# Chữ, số và dấu gạch ngang. CỐ Ý không kiểm checksum EAN-13/UPC: rất nhiều shop
# tự in mã nội bộ dạng Code128 không theo chuẩn EAN, ép checksum sẽ chặn oan.
_BARCODE_PATTERN = re.compile(r"^[A-Z0-9\-]{4,64}$")


def normalize_barcode(raw: Optional[str]) -> Optional[str]:
    """Chuẩn hóa mã vạch về dạng lưu trong DB, hoặc None nếu bỏ trống.

    Viết hoa toàn bộ để tra cứu không phụ thuộc hoa/thường: người dùng gõ tay
    'abc-123' và máy quét bắn ra 'ABC-123' phải khớp cùng một sản phẩm.
    Khoảng trắng bị loại bỏ vì một số máy quét chèn thêm khi đọc mã dài.
    """
    if raw is None:
        return None
    cleaned = "".join(raw.split()).upper()
    if not cleaned:
        return None
    if not _BARCODE_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Mã vạch chỉ gồm chữ, số và dấu gạch ngang, dài 4-64 ký tự"
            ),
        )
    return cleaned


def _ensure_barcode_unique(
    db: Session,
    shop_id: int,
    barcode: Optional[str],
    exclude_product_id: Optional[int] = None,
) -> None:
    """Chặn hai sản phẩm cùng shop dùng chung một mã vạch.

    Có unique index ở tầng DB đỡ phía sau, nhưng kiểm ở đây để báo lỗi nêu rõ
    sản phẩm nào đang giữ mã, thay vì để IntegrityError bật lên thành 500.
    """
    if not barcode:
        return
    query = db.query(models.Product).filter(
        models.Product.shop_id == shop_id,
        models.Product.barcode == barcode,
    )
    if exclude_product_id is not None:
        query = query.filter(models.Product.id != exclude_product_id)
    holder = query.first()
    if holder:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Mã vạch '{barcode}' đã được dùng cho sản phẩm '{name}'",
                barcode=barcode,
                name=holder.name,
            ),
        )


# Ràng buộc duy nhất ở tầng DB -> thông báo cho người dùng. Khóa là mảnh chuỗi
# xuất hiện trong thông báo lỗi của SQLite ("UNIQUE constraint failed: <cột>").
_RANG_BUOC_DUY_NHAT = {
    "products.code": "Mã sản phẩm vừa được sản phẩm khác dùng. Vui lòng thử lại.",
    "products.barcode": "Mã vạch vừa được sản phẩm khác dùng. Vui lòng thử lại.",
    "products.name": "Tên sản phẩm vừa được sản phẩm khác dùng. Vui lòng thử lại.",
    # ux_products_shop_variant. SQLite nêu cả ba cột của index, và cột đầu tiên
    # trong đó không phải `shop_id` mà là cột thứ hai khi khớp chuỗi con - nên
    # bắt theo `products.variant_group` là đủ và không đụng khóa nào khác.
    "products.variant_group": (
        "Biến thể này vừa được tạo trong cùng nhóm. Vui lòng đổi tên biến thể."
    ),
}

# Ghép "<nhóm> - <biến thể>" thành `Product.name`. Ký tự ngăn cách để dấu gạch
# nối có khoảng trắng hai bên vì tên hàng tiếng Việt hay có sẵn dấu gạch trong
# từ ("bánh mì - que"), còn " - " thì gần như không.
_NGAN_CACH_BIEN_THE = " - "

_DAI_TOI_DA_NHOM = 200
_DAI_TOI_DA_BIEN_THE = 100


def _chuan_hoa_ten_bien_the(raw: Optional[str]) -> Optional[str]:
    """Tên biến thể đã cắt khoảng trắng, hoặc None nếu bỏ trống.

    Rỗng và None đều quy về None ("sản phẩm đơn lẻ"): ô này là ô tùy chọn trên
    form nên form cũ không gửi field, còn form mới gửi field rỗng khi người dùng
    không dùng biến thể — hai đường đó phải ra cùng một kết quả (bẫy #3).
    """
    if raw is None:
        return None
    cleaned = " ".join(str(raw).split())
    if not cleaned:
        return None
    if len(cleaned) > _DAI_TOI_DA_BIEN_THE:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Tên biến thể tối đa {n} ký tự", n=str(_DAI_TOI_DA_BIEN_THE)
            ),
        )
    return cleaned


def _ten_va_nhom(
    ten_nguoi_dung: str, ten_bien_the: Optional[str]
) -> tuple[str, Optional[str], Optional[str]]:
    """(name lưu vào DB, variant_group, variant_name) từ dữ liệu trên form.

    Ô "Tên sản phẩm" mang hai nghĩa tùy theo có khai biến thể hay không: không
    khai thì nó là tên sản phẩm, có khai thì nó là **tên nhóm**. Làm vậy để form
    chỉ phải thêm đúng MỘT ô, và để `variant_group` không bao giờ lệch khỏi tên
    mà người dùng nhìn thấy.

    `name` vẫn là cột thật và vẫn duy nhất theo shop: mọi chỗ đang đọc
    `Product.name` (dòng đơn hàng, hóa đơn, Excel, log) tự có tên đầy đủ kèm
    biến thể mà không phải sửa một dòng nào.
    """
    if not ten_bien_the:
        return ten_nguoi_dung, None, None
    if len(ten_nguoi_dung) > _DAI_TOI_DA_NHOM:
        raise HTTPException(
            status_code=400,
            detail=tr("Tên nhóm tối đa {n} ký tự", n=str(_DAI_TOI_DA_NHOM)),
        )
    return (
        f"{ten_nguoi_dung}{_NGAN_CACH_BIEN_THE}{ten_bien_the}",
        ten_nguoi_dung,
        ten_bien_the,
    )


def _ghi_bat_trung(db: Session, ghi) -> None:
    """Chạy `ghi()` (flush hoặc commit), đổi lỗi ràng buộc duy nhất thành 400.

    Các hàm bên dưới đều kiểm trùng trước khi ghi, nhưng giữa lúc kiểm và lúc
    ghi vẫn có khe: hai request cùng gửi một mã có thể cùng vượt qua bước kiểm.
    Unique index chặn được ở tầng dưới, nhưng nó ném `IntegrityError` nên người
    dùng nhận 500 thay vì biết mình cần đổi mã.

    Phải bọc CẢ `flush()` lẫn `commit()`: `create_product` flush để lấy id nên
    câu INSERT chạy ngay tại đó, còn `update_product` không flush nên câu UPDATE
    chạy lúc commit.

    CHỈ dịch những ràng buộc có tên trong `_RANG_BUOC_DUY_NHAT`; mọi
    `IntegrityError` khác được ném tiếp để vẫn nổ 500 - đó là lỗi lập trình cần
    sửa, không được giấu đi.

    Lưu ý: cách nhận biết dựa vào chuỗi thông báo của SQLite. Nếu sau này đổi
    sang database khác, chuỗi sẽ khác và hàm này lặng lẽ hết tác dụng (quay về
    500) - không sai nghiêm trọng, nhưng phải nhớ mà sửa.
    """
    try:
        ghi()
    except IntegrityError as e:
        db.rollback()
        chi_tiet = str(getattr(e, "orig", e))
        for khoa, thong_bao in _RANG_BUOC_DUY_NHAT.items():
            if khoa in chi_tiet:
                raise HTTPException(status_code=400, detail=tr(thong_bao)) from e
        raise


def _flush_bat_trung(db: Session) -> None:
    _ghi_bat_trung(db, db.flush)


def _commit_bat_trung(db: Session) -> None:
    _ghi_bat_trung(db, db.commit)


def _ensure_code_unique(
    db: Session,
    shop_id: int,
    code: Optional[str],
    exclude_product_id: Optional[int] = None,
) -> None:
    """Chặn hai sản phẩm cùng shop dùng chung một mã nội bộ.

    Trước đây không có kiểm tra này, và mã tự sinh dựa trên timestamp theo giây
    nên mọi sản phẩm tạo trong cùng một giây đều trùng mã. Mã trùng làm hỏng
    việc tra cứu: quét/tìm ra mã đó thì không biết là sản phẩm nào.
    """
    if not code:
        return
    query = db.query(models.Product).filter(
        models.Product.shop_id == shop_id,
        models.Product.code == code,
    )
    if exclude_product_id is not None:
        query = query.filter(models.Product.id != exclude_product_id)
    holder = query.first()
    if holder:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Mã sản phẩm '{code}' đã được dùng cho sản phẩm '{name}'",
                code=code,
                name=holder.name,
            ),
        )


def find_by_barcode(
    db: Session, shop_id: int, barcode: str
) -> Optional[models.Product]:
    """Tra sản phẩm theo mã vạch, giới hạn trong một shop.

    Chỉ trả sản phẩm đang hiện: quét trúng SP đã ẩn mà vẫn bán được thì việc ẩn
    sản phẩm thành vô nghĩa.
    """
    normalized = normalize_barcode(barcode)
    if not normalized:
        return None
    return (
        db.query(models.Product)
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.barcode == normalized,
            models.Product.is_active == True,  # noqa: E712 - SQLAlchemy cần so sánh ==
        )
        .first()
    )


def is_valid_image(data: bytes) -> bool:
    """Kiểm tra magic bytes để xác nhận file thực sự là JPEG/PNG/WEBP."""
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":  # JPEG
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WEBP
        return True
    return False


def _require_shop_operator_403(
    db: Session,
    shop_id: int,
    current_user: models.User,
    permission: Optional[str] = PERMISSION_INVENTORY,
) -> models.Shop:
    """Cho phép chủ shop / ADMIN / nhân viên của shop thao tác. Nếu không -> 403.
    (Giữ 403 'Not your shop' như hành vi cũ của các endpoint tạo SP/danh mục.)"""
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if not shop or not has_shop_operator_access(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Bạn không có quyền thao tác cửa hàng này"),
        )
    if permission is not None:
        require_staff_permission(current_user, permission)
    return shop


# Ba trạng thái của ô giá vốn trên form sửa sản phẩm cần ba giá trị khác nhau,
# mà `None` đã mang nghĩa "xóa giá vốn" rồi. Sentinel này là trạng thái thứ ba:
# form không gửi field -> giữ nguyên giá vốn đang có.
_KHONG_DOI_GIA_VON = object()


def _so_tien_tu_form(raw: Optional[str], ten_truong: str) -> Optional[int]:
    """Chuỗi thô từ form -> số tiền. Rỗng = None (xóa), chữ rác = 400.

    Không dùng kiểu số của FastAPI vì cần giữ được sự khác biệt giữa "không
    gửi" và "gửi rỗng" (bẫy #3 trong KIEN_TRUC.md).
    """
    if raw is None:
        return None
    chuoi = str(raw).strip()
    if not chuoi:
        return None
    try:
        return exact_vnd(chuoi)
    except ExactMoneyError:
        raise HTTPException(
            status_code=400,
            detail=tr("{field} phải là số VND nguyên, không có phần lẻ", field=ten_truong),
        )


def _mo_ta_gia_von(gia_von: Optional[int]) -> str:
    """Giá vốn cho dòng log. NULL phải đọc ra được là "chưa khai", không phải 0."""
    if gia_von is None:
        return "chưa khai"
    return f"{gia_von:,.0f}đ"


def _kiem_gia_von(gia_von: Optional[int]) -> Optional[int]:
    """Kiểm giá vốn nhận từ client. `None` đi thẳng qua - caller tự hiểu.

    Cho phép 0: hàng khuyến mãi/hàng tặng có giá vốn bằng 0 thật. Chỉ chặn số
    âm, thứ không có nghĩa gì trong kế toán kho.
    """
    if gia_von is None:
        return None
    try:
        return exact_vnd(gia_von)
    except ExactMoneyError:
        raise HTTPException(
            status_code=400,
            detail=tr("Giá vốn phải là số VND nguyên trong giới hạn"),
        )


# --- Categories ---
def create_category(
    db: Session, current_user: models.User, name: str, shop_id: int
) -> models.Category:
    _require_shop_operator_403(db, shop_id, current_user)

    name_stripped = name.strip() if name else ""
    if not name_stripped:
        raise HTTPException(
            status_code=400,
            detail=tr("Tên danh mục không được để trống"),
        )

    cat = models.Category(name=name_stripped, shop_id=shop_id, is_active=True)
    db.add(cat)
    db.commit()
    log_system_action(
        db,
        current_user.id,
        "CREATE_CATEGORY",
        f"Tạo danh mục '{name_stripped}' cho shop #{shop_id}",
    )
    db.refresh(cat)
    return cat


def update_category(
    db: Session, current_user: models.User, category_id: int, cat: CategoryUpdate
) -> models.Category:
    db_cat = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not db_cat:
        raise HTTPException(status_code=404, detail=tr("Danh mục không tồn tại"))

    shop = db.query(models.Shop).filter(models.Shop.id == db_cat.shop_id).first()
    if not shop or not has_shop_operator_access(shop, current_user):
        raise HTTPException(
            status_code=403,
            detail=tr("Không có quyền chỉnh sửa danh mục của cửa hàng này"),
        )
    require_staff_permission(current_user, PERMISSION_INVENTORY)

    name_stripped = cat.name.strip() if cat.name else ""
    if not name_stripped:
        raise HTTPException(
            status_code=400,
            detail=tr("Tên danh mục không được để trống"),
        )

    db_cat.name = name_stripped
    db_cat.is_active = cat.is_active
    db.commit()
    db.refresh(db_cat)
    log_system_action(
        db,
        current_user.id,
        "UPDATE_CATEGORY",
        f"Cập nhật danh mục: '{db_cat.name}' (ID: {db_cat.id}, Active: {db_cat.is_active})",
    )
    db.refresh(db_cat)
    return db_cat


def list_categories(
    db: Session, current_user: models.User, shop_id: int
) -> List[models.Category]:
    require_shop_access(db, shop_id, current_user)
    # POS cần đọc danh mục; kho cũng cần. Chỉ các thao tác ghi mới bắt buộc
    # quyền INVENTORY.
    require_any_staff_permission(
        current_user, PERMISSION_SALE, PERMISSION_INVENTORY
    )
    return db.query(models.Category).filter(models.Category.shop_id == shop_id).all()


# --- Products ---
def save_product_image(image: UploadFile) -> str:
    """Kiểm tra loại file, đuôi file, kích thước, magic bytes;
    tự sinh tên file bằng UUID (tránh path traversal). Trả về URL public."""
    if image.content_type not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=400,
            detail=tr("Loại file không hợp lệ. Chỉ chấp nhận JPG, PNG, WEBP"),
        )

    ext = pathlib.Path(image.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=tr("Đuôi file không hợp lệ"))

    contents = image.file.read()
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=tr("File quá lớn (tối đa 2MB)"),
        )
    if not contents:
        raise HTTPException(status_code=400, detail=tr("File rỗng"))

    if not is_valid_image(contents):
        raise HTTPException(
            status_code=400,
            detail=tr("Nội dung file không phải ảnh hợp lệ"),
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as buffer:
        buffer.write(contents)
    return f"/uploads/{filename}"


def lookup_by_barcode(
    db: Session, current_user: models.User, shop_id: int, barcode: str
) -> Dict[str, Any]:
    """Tra sản phẩm theo mã vạch cho màn hình quét. 404 nếu không có mã đó.

    Frontend đã giữ sẵn danh sách sản phẩm trong bộ nhớ nên phần lớn lượt quét
    khớp được ngay tại máy khách. Endpoint này để đối chiếu lại khi máy khách
    không tìm thấy: danh sách có thể đã cũ (nhân viên khác vừa thêm sản phẩm),
    lúc đó câu trả lời "không tìm thấy" phải do server quyết định.
    """
    _require_shop_operator_403(db, shop_id, current_user, permission=None)
    require_any_staff_permission(
        current_user, PERMISSION_SALE, PERMISSION_INVENTORY
    )
    prod = find_by_barcode(db, shop_id, barcode)
    if not prod:
        raise HTTPException(
            status_code=404,
            detail=tr(
                "Không tìm thấy sản phẩm có mã vạch '{barcode}'",
                barcode=barcode,
            ),
        )
    return {
        "id": prod.id,
        "code": prod.code,
        "barcode": prod.barcode,
        "name": prod.name,
        "price": prod.price,
        "stock": prod.stock,
        "image_url": prod.image_url,
        "is_active": prod.is_active,
        "category_id": prod.category_id,
        "shop_id": prod.shop_id,
        # Quét mã vạch của một biến thể phải ra đúng biến thể đó, và POS cần hai
        # trường này để dựng lại đúng ô hàng khi danh sách trong bộ nhớ đã cũ.
        "variant_group": prod.variant_group,
        "variant_name": prod.variant_name,
    }


def _kiem_danh_muc_thuoc_shop(db: Session, shop_id: int, category_id: int) -> None:
    """Danh mục phải tồn tại VÀ thuộc đúng shop đang thao tác.

    Hai điều kiện đi cùng một câu query, cùng lý do với `resolve_items` (bẫy
    11): tách ra kiểm tồn tại trước rồi mới kiểm chủ sở hữu là để lộ việc
    `category_id` nào có thật, và thông báo lỗi khác nhau giữa hai ca là đủ để
    dò danh mục của cửa hàng khác.
    """
    category = (
        db.query(models.Category)
        .filter(
            models.Category.id == category_id,
            models.Category.shop_id == shop_id,
        )
        .first()
    )
    if not category:
        raise HTTPException(
            status_code=400,
            detail=tr("Danh mục không thuộc cửa hàng này"),
        )


def _resolve_create_category_id(
    db: Session, shop_id: int, category_id: Optional[int]
) -> int:
    if category_id is not None:
        _kiem_danh_muc_thuoc_shop(db, shop_id, category_id)
        return category_id

    category = (
        db.query(models.Category)
        .filter(
            models.Category.shop_id == shop_id,
            models.Category.name == DEFAULT_CATEGORY_NAME,
        )
        .first()
    )
    if category is None:
        category = models.Category(
            shop_id=shop_id,
            name=DEFAULT_CATEGORY_NAME,
            is_active=True,
        )
        db.add(category)
        db.flush()
    elif not category.is_active:
        category.is_active = True
    return category.id


def create_product(
    db: Session,
    current_user: models.User,
    shop_id: int,
    name: str,
    price: int,
    stock: int,
    category_id: Optional[int],
    code: Optional[str] = None,
    barcode: Optional[str] = None,
    image: Optional[UploadFile] = None,
    cost_price: Optional[int] = None,
    track_batches: bool = False,
    variant_name: Optional[str] = None,
) -> models.Product:
    shop = _require_shop_operator_403(db, shop_id, current_user)
    try:
        price = exact_vnd(price)
    except ExactMoneyError:
        raise HTTPException(status_code=400, detail=tr("Giá sản phẩm phải là số VND nguyên"))
    if cost_price is not None:
        require_cost_visibility(shop, current_user)
        cost_price = _kiem_gia_von(cost_price)

    auth_session_service.fence_live_auth_session(db)
    resolved_category_id = _resolve_create_category_id(db, shop_id, category_id)

    # Khai biến thể thì `name` trở thành tên NHÓM và tên lưu vào DB là tên ghép.
    # Phải làm trước phép kiểm trùng bên dưới, nếu không "Áo thun" nhóm sẽ đụng
    # với sản phẩm đơn lẻ cùng tên trong khi hai cái đó không hề trùng nhau.
    variant_name = _chuan_hoa_ten_bien_the(variant_name)
    name, variant_group, variant_name = _ten_va_nhom(name, variant_name)

    existing_prod = (
        db.query(models.Product)
        .filter(models.Product.shop_id == shop_id, models.Product.name == name)
        .first()
    )
    if existing_prod:
        raise HTTPException(
            status_code=400,
            detail=tr("Sản phẩm với tên này đã tồn tại trong cửa hàng!"),
        )

    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail=tr("Giá sản phẩm phải lớn hơn 0"),
        )
    if stock < 0:
        raise HTTPException(
            status_code=400,
            detail=tr("Số lượng tồn kho không được âm"),
        )
    if stock > MAX_SAFE_QUANTITY:
        raise HTTPException(status_code=400, detail=tr("Số lượng tồn kho vượt giới hạn"))
    if track_batches and stock:
        raise HTTPException(
            status_code=400,
            detail=tr("Sản phẩm theo dõi lô phải nhập tồn ban đầu qua phiếu nhập có lô"),
        )

    barcode_value = normalize_barcode(barcode)
    _ensure_barcode_unique(db, shop_id, barcode_value)

    code_stripped = code.strip() if code else ""
    _ensure_code_unique(db, shop_id, code_stripped)

    image_url = DEFAULT_PRODUCT_IMAGE
    if image and image.filename:
        image_url = save_product_image(image)

    p = models.Product(
        code=code_stripped or None,
        barcode=barcode_value,
        name=name,
        variant_group=variant_group,
        variant_name=variant_name,
        price=price,
        track_batches=bool(track_batches),
        stock=stock,
        cost_known_qty=stock if cost_price is not None else 0,
        cost_unknown_qty=stock if cost_price is None else 0,
        cost_basis_vnd=checked_multiply(stock, cost_price or 0),
        cost_deficit_qty=0,
        cost_state_version=1 if stock else 0,
        image_url=image_url,
        category_id=resolved_category_id,
        shop_id=shop_id,
    )
    db.add(p)
    # Mã tự sinh lấy từ chính id của sản phẩm. Bản cũ dùng timestamp theo GIÂY
    # nên hai sản phẩm tạo cách nhau dưới một giây là trùng mã; id thì không bao
    # giờ đụng nhau, kể cả khi hai người tạo cùng lúc. flush() để có id trước
    # khi commit, vẫn nằm trong một transaction duy nhất.
    _flush_bat_trung(db)
    if not p.code:
        p.code = f"SP-{p.id}"
    code = p.code
    _commit_bat_trung(db)
    log_system_action(
        db,
        current_user.id,
        "CREATE_PRODUCT",
        f"Tạo SP: '{name}' ({code}) - Giá: {price:,.0f}đ, Kho: {stock}"
        f", Giá vốn: {_mo_ta_gia_von(cost_price)}",
    )
    db.refresh(p)
    return p


def list_products(
    db: Session, current_user: models.User, shop_id: int
) -> List[Dict]:
    """Danh sách sản phẩm của shop. Bắt buộc đăng nhập và phải thuộc shop đó.

    Trước F6 endpoint này mở cho mọi người: ai đoán được `shop_id` là đọc được
    trọn danh mục hàng và tồn kho của một cửa hàng lạ. Không có ai đang gọi nó
    trước lúc đăng nhập - POS và trang Kho đều gọi sau khi có token - nên chỗ mở
    đó không đổi lấy được gì cả.

    Dùng `PERMISSION_CATALOG_READ` vì đây là quyền đọc danh mục thuần túy, và cả
    ba vai trò nhân viên đều có nó: thu ngân vẫn thấy lưới hàng để bán, thủ kho
    vẫn thấy để nhập xuất.
    """
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_CATALOG_READ)
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    res: List[Dict] = []
    for p in products:
        cat_active = True
        if p.category:
            cat_active = p.category.is_active if p.category.is_active is not None else True
        res.append(
            {
                "id": p.id,
                "code": p.code,
                "barcode": p.barcode,
                "name": p.name,
                "price": p.price,
                "stock": p.stock,
                "image_url": p.image_url,
                "is_active": p.is_active,
                "category_id": p.category_id,
                "shop_id": p.shop_id,
                "category_is_active": cat_active,
                "track_batches": bool(p.track_batches),
                "fnb_station": p.fnb_station,
                # F6: cả hai NULL = sản phẩm đơn lẻ. Giao diện gom ô theo
                # `variant_group`; `name` đã là tên đầy đủ nên chỗ nào không
                # muốn gom thì cứ dùng `name` như trước, không phải sửa gì.
                "variant_group": p.variant_group,
                "variant_name": p.variant_name,
            }
        )
    return res


def list_product_costs(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    """Giá vốn của toàn bộ sản phẩm trong shop. CHỈ chủ shop và ADMIN.

    Vẫn tách hẳn khỏi `list_products` sau khi endpoint đó đã có xác thực (F6):
    hai vòng người xem khác nhau thật sự. Danh sách sản phẩm mở cho cả nhân
    viên, còn giá vốn chỉ chủ shop và ADMIN. Gộp lại là nhân viên đọc được giá
    vốn qua chính lưới hàng của POS.

    `chua_khai` đếm riêng số sản phẩm còn NULL để giao diện nhắc chủ shop khai
    nốt - không có nó thì lãi gộp im lặng thiếu một phần và không ai biết.
    """
    shop = require_shop_access(db, shop_id, current_user)
    require_cost_visibility(shop, current_user)
    products = (
        db.query(models.Product)
        .filter(models.Product.shop_id == shop_id)
        .all()
    )
    return {
        "costs": [
            {
                "product_id": p.id,
                "cost_price": p.cost_price,
                "known_qty": p.cost_known_qty,
                "unknown_qty": p.cost_unknown_qty,
                "cost_basis_vnd": p.cost_basis_vnd,
                "deficit_qty": p.cost_deficit_qty,
                "version": p.cost_state_version,
            }
            for p in products
        ],
        "chua_khai": sum(1 for p in products if p.cost_price is None),
    }


def danh_sach_lo(
    db: Session,
    current_user: models.User,
    shop_id: int,
    sap_het_han_trong: int = 30,
) -> Dict[str, Any]:
    """Các lô còn hàng của shop, kèm phân loại theo hạn sử dụng.

    `sap_het_han_trong` là số ngày coi là "sắp hết hạn". Giá trị tồn tính theo
    giá vốn của TỪNG lô - đó là số tiền thật sẽ mất nếu hàng hỏng trên kệ.
    """
    shop = require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)
    xem_gia_von = has_cost_visibility(shop, current_user)

    # Ngày nghiệp vụ Việt Nam, cùng nguồn với inventory_service (hàng còn bán
    # được) và write_off_service (hàng được phép hủy). Ba chỗ này lệch nhau là
    # có lô vừa không bán được vừa chưa hủy được.
    hom_nay = thoi_gian.hom_nay_vn()
    moc_canh_bao = (hom_nay + timedelta(days=max(sap_het_han_trong, 0))).strftime(
        "%Y-%m-%d"
    )
    hom_nay_str = hom_nay.strftime("%Y-%m-%d")

    lo = (
        db.query(models.ProductBatch)
        .filter(
            models.ProductBatch.shop_id == shop_id,
            models.ProductBatch.quantity > 0,
        )
        .all()
    )
    ten_sp = {
        p.id: p.name
        for p in db.query(models.Product)
        .filter(models.Product.shop_id == shop_id)
        .all()
    }

    da_het_han: List[Dict[str, Any]] = []
    sap_het_han: List[Dict[str, Any]] = []
    gia_tri_het_han = 0
    gia_tri_sap_het = 0

    for b in lo:
        if b.expiry_date is None:
            continue      # lô không hạn không bao giờ vào hai nhóm này
        ban_ghi: Dict[str, Any] = {
            "batch_id": b.id,
            "product_id": b.product_id,
            "product_name": ten_sp.get(b.product_id),
            "expiry_date": b.expiry_date,
            "quantity": b.quantity,
        }
        gia_tri = int(b.cost_basis_vnd) if int(b.cost_unknown_qty or 0) == 0 else None
        if xem_gia_von:
            ban_ghi["cost_price"] = b.cost_price
            ban_ghi["stock_value"] = gia_tri

        if b.expiry_date < hom_nay_str:
            da_het_han.append(ban_ghi)
            gia_tri_het_han += gia_tri or 0
        elif b.expiry_date <= moc_canh_bao:
            sap_het_han.append(ban_ghi)
            gia_tri_sap_het += gia_tri or 0

    da_het_han.sort(key=lambda r: r["expiry_date"])
    sap_het_han.sort(key=lambda r: r["expiry_date"])

    ket_qua: Dict[str, Any] = {
        "days": sap_het_han_trong,
        "expired": da_het_han,
        "expiring_soon": sap_het_han,
        "expired_quantity": sum(r["quantity"] for r in da_het_han),
        "expiring_soon_quantity": sum(r["quantity"] for r in sap_het_han),
    }
    if xem_gia_von:
        ket_qua["expired_value"] = gia_tri_het_han
        ket_qua["expiring_soon_value"] = gia_tri_sap_het
    return ket_qua


def update_product(
    db: Session,
    current_user: models.User,
    product_id: int,
    name: str,
    price: int,
    category_id: int,
    code: Optional[str] = None,
    barcode: Optional[str] = None,
    image: Optional[UploadFile] = None,
    cost_price: Optional[str] = None,
    variant_name: Optional[str] = None,
) -> models.Product:
    """Sửa thông tin sản phẩm: tên, giá, giá vốn, mã, mã vạch, danh mục, ảnh.

    `barcode` phân biệt hai trường hợp mà `code` không phân biệt:
    - `None` (form không gửi field) -> giữ nguyên mã vạch cũ.
    - `""` (form gửi field rỗng)    -> xóa mã vạch, đặt về NULL.
    Cần tách như vậy để sửa được lỗi gán nhầm mã vạch cho sản phẩm.

    `cost_price` nhận CHUỖI thô vì cần đúng ba trạng thái, mà kiểu số chỉ có
    hai: `None` = form không gửi (giữ nguyên), `""` = gửi rỗng (xóa giá vốn về
    NULL, dùng khi khai nhầm), chuỗi số = đặt giá vốn mới. Sửa tay ở đây là
    đường ghi đè bình quân gia quyền - dùng khi khai sai, không phải đường
    thường xuyên (nhập hàng thì đi qua `adjust_stock` kèm đơn giá).

    `variant_name` cũng có ba trạng thái, cùng lý do và cùng cách làm: `None` =
    form không gửi (giữ nguyên biến thể), `""` = gửi rỗng (gỡ biến thể, sản phẩm
    trở lại đơn lẻ), chuỗi = đặt tên biến thể mới. Gỡ biến thể KHÔNG xóa dữ liệu
    nào khác - tồn kho, lô hạn và lịch sử bán đều gắn theo `product_id` nên
    không bị ảnh hưởng, chỉ tên và cách gom nhóm trên giao diện đổi.

    CỐ Ý KHÔNG đụng vào `stock`. Ghi đè tồn kho từ form sửa gây mất hàng khi
    có bán song song (seller mở form thấy tồn 100, POS bán vài đơn, seller bấm
    Lưu -> tồn quay lại 100). Thay đổi tồn kho đi qua `adjust_stock` (nhập/xuất
    theo delta, cập nhật nguyên tử).
    """
    prod = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))
    shop = require_shop_access(db, prod.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)
    auth_session_service.fence_live_auth_session(db)
    try:
        price = exact_vnd(price)
    except ExactMoneyError:
        raise HTTPException(status_code=400, detail=tr("Giá sản phẩm phải là số VND nguyên"))

    gia_von_moi = _KHONG_DOI_GIA_VON
    if cost_price is not None:
        require_cost_visibility(shop, current_user)
        gia_von_moi = _kiem_gia_von(_so_tien_tu_form(cost_price, "Giá vốn"))

    name_stripped = name.strip() if name else ""
    if not name_stripped:
        raise HTTPException(
            status_code=400,
            detail=tr("Tên sản phẩm không được để trống"),
        )
    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail=tr("Giá sản phẩm phải lớn hơn 0"),
        )

    # `variant_name` là None khi form KHÔNG gửi field (client cũ, hoặc form chỉ
    # sửa vài trường): giữ nguyên biến thể đang có. Ghép lại tên dù giữ nguyên,
    # vì ô "Tên sản phẩm" lúc này mang tên NHÓM và người dùng có thể vừa đổi nó
    # - đổi tên nhóm phải kéo theo tên đầy đủ của biến thể.
    ten_bien_the = (
        prod.variant_name
        if variant_name is None
        else _chuan_hoa_ten_bien_the(variant_name)
    )
    name_stripped, nhom_moi, ten_bien_the = _ten_va_nhom(
        name_stripped, ten_bien_the
    )

    # Cùng một phép kiểm với `create_product`, gọi chung một hàm để hai đường
    # không lệch nhau khi sau này ai đó sửa một bên.
    _kiem_danh_muc_thuoc_shop(db, prod.shop_id, category_id)

    duplicate = (
        db.query(models.Product)
        .filter(
            models.Product.shop_id == prod.shop_id,
            models.Product.name == name_stripped,
            models.Product.id != product_id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=400,
            detail=tr("Sản phẩm với tên này đã tồn tại trong cửa hàng!"),
        )

    if barcode is not None:
        barcode_value = normalize_barcode(barcode)
        _ensure_barcode_unique(db, prod.shop_id, barcode_value, exclude_product_id=product_id)
        prod.barcode = barcode_value

    # Để trống ô mã thì giữ mã cũ (hành vi sẵn có, có test bảo vệ).
    code_stripped = code.strip() if code and code.strip() else None
    if code_stripped and code_stripped != prod.code:
        _ensure_code_unique(db, prod.shop_id, code_stripped, exclude_product_id=product_id)
        prod.code = code_stripped
    prod.name = name_stripped
    prod.variant_group = nhom_moi
    prod.variant_name = ten_bien_the
    prod.price = price
    prod.category_id = category_id
    ghi_chu_gia_von = ""
    if gia_von_moi is not _KHONG_DOI_GIA_VON:
        if prod.track_batches:
            raise HTTPException(
                status_code=400,
                detail=tr("Giá vốn sản phẩm theo lô chỉ được khai trên từng lô nhập"),
            )
        gia_von_cu = prod.cost_price
        ton = int(prod.stock or 0)
        if ton < 0:
            prod.cost_known_qty = 0
            prod.cost_unknown_qty = 0
            prod.cost_basis_vnd = 0
            prod.cost_deficit_qty = -ton
        elif gia_von_moi is None:
            prod.cost_known_qty = 0
            prod.cost_unknown_qty = ton
            prod.cost_basis_vnd = 0
            prod.cost_deficit_qty = 0
        else:
            prod.cost_known_qty = ton
            prod.cost_unknown_qty = 0
            prod.cost_basis_vnd = checked_multiply(ton, gia_von_moi)
            prod.cost_deficit_qty = 0
        prod.cost_state_version = int(prod.cost_state_version or 0) + 1
        if gia_von_cu != gia_von_moi:
            ghi_chu_gia_von = (
                f", Giá vốn: {_mo_ta_gia_von(gia_von_cu)}"
                f" -> {_mo_ta_gia_von(gia_von_moi)}"
            )
    if image and image.filename:
        prod.image_url = save_product_image(image)

    _commit_bat_trung(db)
    log_system_action(
        db,
        current_user.id,
        "UPDATE_PRODUCT",
        f"Cập nhật SP: '{prod.name}' ({prod.code}) - Giá: {price:,.0f}đ"
        f"{ghi_chu_gia_von}",
    )
    db.refresh(prod)
    return prod


def lo_de_kiem_ke(
    db: Session, current_user: models.User, shop_id: int
) -> Dict[str, Any]:
    """Mọi lô CÒN HÀNG của các sản phẩm theo dõi hạn, để dựng phiếu đếm.

    Khác `danh_sach_lo` (chỉ trả lô sắp/đã hết hạn, phục vụ màn cảnh báo): kiểm
    kê phải đếm được HẾT, kể cả lô còn hạn dài. Một request cho cả shop rồi đếm
    tại máy - hỏi từng sản phẩm lúc quét là mỗi lượt quét một vòng mạng, giữa
    lúc người ta đang cầm máy quét chạy dọc kệ hàng.

    Lô đã về 0 bị loại: nó là lịch sử, không phải hàng trên kệ để đếm.

    Sản phẩm KHÔNG theo lô cũng xuất hiện ở đây, nhưng chỉ khi nó còn evidence
    tồn âm offline chưa đóng: máy khách cần token snapshot để phiếu đếm không
    đóng nhầm một bằng chứng mới hơn. Sản phẩm không có evidence thì không có gì
    để echo nên vẫn đi đường cũ.

    KHÔNG trả `cost_price` - đếm hàng không cần biết giá vốn, và endpoint này mở
    cho cả thủ kho.
    """
    require_shop_access(db, shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)

    san_pham = (
        db.query(models.Product)
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.track_batches == True,  # noqa: E712
        )
        .all()
    )
    non_batch = (
        db.query(models.Product)
        .join(
            models.OfflineStockDeficit,
            models.OfflineStockDeficit.product_id == models.Product.id,
        )
        .filter(
            models.Product.shop_id == shop_id,
            models.Product.track_batches == False,  # noqa: E712
            models.OfflineStockDeficit.remaining_quantity > 0,
        )
        .distinct()
        .all()
    )
    lo_theo_sp: Dict[int, List[models.ProductBatch]] = {}
    if san_pham:
        for b in (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.product_id.in_([p.id for p in san_pham]),
                models.ProductBatch.quantity > 0,
            )
            .order_by(models.ProductBatch.expiry_date, models.ProductBatch.id)
            .all()
        ):
            lo_theo_sp.setdefault(b.product_id, []).append(b)
    deficit_states = {
        p.id: inventory_service.offline_batch_deficit_state(db, p.id)
        for p in san_pham
    }
    deficit_states.update({
        p.id: inventory_service.offline_stock_deficit_state(db, p.id)
        for p in non_batch
    })

    return {
        "products": [
            {
                "product_id": p.id,
                "name": p.name,
                "stock": p.stock,
                "track_batches": bool(p.track_batches),
                "offline_deficit_qty": deficit_states[p.id]["open_quantity"],
                "offline_deficit_snapshot": deficit_states[p.id]["snapshot"],
                "batches": [
                    {
                        "batch_id": b.id,
                        "expiry_date": b.expiry_date,
                        "quantity": b.quantity,
                    }
                    for b in lo_theo_sp.get(p.id, [])
                ],
            }
            for p in san_pham + non_batch
        ]
    }


def _kiem_dinh_dang_dong_lo(
    prod: models.Product,
    batches: List[Any],
    *,
    allow_empty_reconciliation: bool = False,
) -> None:
    """Kiểm dạng của phần đếm theo lô, trước khi ghi bất cứ gì."""
    if not batches and not allow_empty_reconciliation:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Sản phẩm '{name}' chưa có lô nào được đếm", name=prod.name
            ),
        )
    ids = [b.batch_id for b in batches]
    if len(set(ids)) != len(ids):
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Một lô của '{name}' xuất hiện nhiều lần trong phiếu kiểm kê",
                name=prod.name,
            ),
        )
    for b in batches:
        if (
            b.counted < 0
            or b.quantity_snapshot < 0
            or b.counted > MAX_SAFE_QUANTITY
            or b.quantity_snapshot > MAX_SAFE_QUANTITY
        ):
            raise HTTPException(
                status_code=400, detail=tr("Số đếm lô nằm ngoài giới hạn")
            )


def _kiem_ke_theo_lo(
    db: Session,
    prod: models.Product,
    batches: List[Any],
    offline_deficit_snapshot: Optional[str],
    *,
    actor_user_id: int,
    resolved_at: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Đặt số lượng của TỪNG LÔ bằng số đếm thực tế, rồi dựng lại `Product.stock`.

    Trả về (các dòng đã điều chỉnh, các dòng bị bỏ qua).

    Ba nguyên tắc của kiểm kê được giữ nguyên, chỉ hạ xuống mức lô: chỉ đụng vào
    lô có trong phiếu, lô nào đã đổi so với lúc bắt đầu đếm thì bỏ qua và báo
    lại, không nhận số âm.

    **Đếm ra hàng không thuộc lô nào đang có thì TỪ CHỐI**, không tự tạo lô. Mỗi
    hộp đều có hạn in trên bao bì nên hàng thừa luôn thuộc về một hạn cụ thể;
    hạn đó chưa có lô nghĩa là lần nhập hàng trước bị sót, và đường đúng để sửa
    là Nhập kho (khai đúng hạn) chứ không phải đoán. Tự tạo lô không hạn còn tệ
    hơn: lô không hạn xếp SAU CÙNG khi trừ FEFO nên số hàng đó nằm lại trên kệ
    lâu nhất - đúng thứ sẽ hỏng trước.

    `Product.stock` được tính lại bằng TỔNG của mọi lô (kể cả lô không có trong
    phiếu), không phải cộng dồn chênh lệch. Cộng dồn thì một lô bị bỏ qua vì đã
    đổi giữa chừng sẽ làm tổng lệch khỏi bảng lô - đúng thứ mà
    `inventory_service.doi_chieu_ton_kho()` sinh ra để bắt.
    """
    lo_cua_sp = {
        b.id: b
        for b in db.query(models.ProductBatch)
        .filter(models.ProductBatch.product_id == prod.id)
        .all()
    }
    batch_total_before = sum(int(batch.quantity or 0) for batch in lo_cua_sp.values())
    product_stock_before = int(prod.stock or 0)
    deficit_state = inventory_service.offline_batch_deficit_state(db, prod.id)
    offline_deficit_before = int(deficit_state["open_quantity"])
    if (
        offline_deficit_snapshot is not None
        and offline_deficit_snapshot != deficit_state["snapshot"]
    ):
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Evidence tồn âm offline đã đổi trong lúc kiểm kê; "
                "vui lòng bắt đầu lại dòng này"
            ),
        )
    if offline_deficit_before > 0 and offline_deficit_snapshot is None:
        raise HTTPException(
            status_code=409,
            detail=tr("Phiếu kiểm kê thiếu snapshot evidence tồn âm offline"),
        )
    if batch_total_before - product_stock_before != offline_deficit_before:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Chênh lệch tồn theo lô không khớp evidence offline; "
                "chưa thể kiểm kê tự động"
            ),
        )

    la = [b.batch_id for b in batches if b.batch_id not in lo_cua_sp]
    if la:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Lô #{ids} không thuộc sản phẩm '{name}'. Hàng đếm thừa mà chưa "
                "có lô thì nhập qua Nhập kho để khai đúng hạn sử dụng.",
                ids=", ".join(str(i) for i in la[:5]),
                name=prod.name,
            ),
        )

    dieu_chinh: List[Dict[str, Any]] = []
    bo_qua: List[Dict[str, Any]] = []
    for dem in batches:
        lo = lo_cua_sp[dem.batch_id]
        hien_tai = int(lo.quantity or 0)
        if hien_tai != dem.quantity_snapshot:
            bo_qua.append({
                "product_id": prod.id,
                "batch_id": lo.id,
                "name": prod.name,
                "ly_do": (
                    f"Lô HSD {lo.expiry_date or '-'} đã đổi từ "
                    f"{dem.quantity_snapshot} thành {hien_tai} trong lúc kiểm "
                    "kê. Vui lòng đếm lại lô này."
                ),
            })
            continue
        if hien_tai == dem.counted:
            continue
        lech = int(dem.counted) - hien_tai
        if lech > 0:
            inventory_service.add_cost_pool(lo, lech, None)
        else:
            inventory_service.consume_cost_pool(lo, -lech)
        lo.quantity = dem.counted
        dieu_chinh.append({
            "product_id": prod.id,
            "batch_id": lo.id,
            "name": prod.name,
            "expiry_date": lo.expiry_date,
            "truoc": hien_tai,
            "sau": dem.counted,
            "lech": dem.counted - hien_tai,
        })

    submitted_ids = {row.batch_id for row in batches}
    positive_ids_before = {
        batch_id
        for batch_id, batch in lo_cua_sp.items()
        if int(batch.quantity or 0) > 0
    }
    full_positive_coverage = positive_ids_before.issubset(submitted_ids)
    if offline_deficit_before > 0 and not full_positive_coverage:
        bo_qua.append({
            "product_id": prod.id,
            "batch_id": None,
            "name": prod.name,
            "ly_do": (
                "Danh sách lô dương đã đổi trong lúc kiểm kê. "
                "Vui lòng tải lại và đếm đủ các lô còn hàng."
            ),
        })
    batch_delta = sum(int(row["lech"]) for row in dieu_chinh)
    reconcile_offline = (
        offline_deficit_before > 0
        and not bo_qua
        and full_positive_coverage
    )
    if reconcile_offline:
        rebuilt_stock = sum(int(b.quantity or 0) for b in lo_cua_sp.values())
        if rebuilt_stock > MAX_SAFE_QUANTITY:
            raise HTTPException(
                status_code=409,
                detail=tr("Tổng tồn lô sau kiểm kê vượt giới hạn"),
            )
        prod.stock = rebuilt_stock
        if offline_deficit_before > 0:
            closed = inventory_service.close_offline_batch_deficits_after_stocktake(
                db, prod.id, actor_user_id=actor_user_id, resolved_at=resolved_at
            )
            if closed != offline_deficit_before:
                raise HTTPException(
                    status_code=409,
                    detail=tr("Evidence tồn âm offline đổi trong lúc kiểm kê"),
                )
            # Batch rows report their own count deltas. This synthetic row is
            # the independent Product.stock correction that closes the
            # deliberate offline deficit, so response/log conservation remains
            # exact even when every batch count itself was unchanged.
            dieu_chinh.append({
                "product_id": prod.id,
                "batch_id": None,
                "name": prod.name,
                "expiry_date": None,
                "truoc": product_stock_before + batch_delta,
                "sau": product_stock_before + batch_delta + offline_deficit_before,
                "lech": offline_deficit_before,
                "offline_deficit_reconciled": True,
            })
    elif batch_delta:
        new_stock = product_stock_before + batch_delta
        if new_stock < -MAX_SAFE_QUANTITY or new_stock > MAX_SAFE_QUANTITY:
            raise HTTPException(
                status_code=409,
                detail=tr("Tồn kho sau kiểm kê vượt giới hạn"),
            )
        # A partial/stale sheet may adjust accepted batches, but it must not
        # erase an unresolved offline mismatch. Move Product.stock by the same
        # delta so SUM(batch)-stock remains exactly the durable deficit.
        prod.stock = new_stock
    return dieu_chinh, bo_qua


def apply_stocktake(
    db: Session, current_user: models.User, shop_id: int, items: List[Any]
) -> Dict[str, Any]:
    """Áp dụng kết quả kiểm kê: đặt tồn kho bằng số đếm được thực tế.

    Ba nguyên tắc an toàn:

    1. CHỈ đụng vào sản phẩm có trong danh sách gửi lên. Sản phẩm không đếm tới
       được giữ nguyên, KHÔNG coi là tồn 0 - quên quét một kệ hàng mà bị xóa
       sạch tồn kho thì tai hại hơn nhiều so với việc kiểm kê thiếu.

    2. Dòng nào có tồn kho đã đổi so với lúc bắt đầu đếm thì BỎ QUA và báo lại.
       Bán hàng vẫn chạy song song khi đang kiểm kê; ghi đè lúc đó sẽ nuốt mất
       số hàng vừa bán. Không đoán - trả về để người dùng đếm lại đúng sản phẩm
       đó.

    3. Không cho số đếm âm.

    Sản phẩm có `track_batches` đếm THEO TỪNG LÔ (`it.batches`) - xem
    `_kiem_ke_theo_lo`. Gán thẳng một con số tổng cho hàng có lô là phá vỡ ràng
    buộc "tổng lô = tồn kho" mà không biết phải cộng trừ vào lô nào.
    """
    _require_shop_operator_403(db, shop_id, current_user)

    if not items:
        raise HTTPException(
            status_code=400,
            detail=tr("Chưa có sản phẩm nào được đếm"),
        )

    ids = [it.product_id for it in items]
    if len(set(ids)) != len(ids):
        raise HTTPException(
            status_code=400,
            detail=tr("Một sản phẩm xuất hiện nhiều lần trong phiếu kiểm kê"),
        )

    # Snapshot chỉ có nghĩa khi lần so sánh + lần gán cùng nằm sau write-lock.
    # Nếu phiếu nhập/bán chen giữa SELECT và commit, kiểm tra snapshot cũ vẫn
    # pass rồi phép gán tuyệt đối sẽ nuốt mất tồn vừa thay đổi.
    inventory_service.lock_shop_for_inventory(db, shop_id)
    db.expire_all()
    san_pham = {
        p.id: p
        for p in db.query(models.Product)
        .filter(models.Product.shop_id == shop_id, models.Product.id.in_(ids))
        .all()
    }

    # Kiểm dạng dòng TRƯỚC khi ghi bất cứ gì. Một dòng khai sai kiểu là cả phiếu
    # bị từ chối, không phải ghi được nửa phiếu rồi mới nổ - người dùng lúc đó
    # không biết phần nào đã vào.
    for it in items:
        prod = san_pham.get(it.product_id)
        if prod is None:
            continue          # dòng lạc, xử lý ở vòng dưới thành `bo_qua`
        if prod.track_batches:
            if it.batches is None:
                raise HTTPException(
                    status_code=400,
                    detail=tr(
                        "Sản phẩm '{name}' theo dõi hạn sử dụng; phải đếm theo "
                        "từng lô",
                        name=prod.name,
                    ),
                )
            if it.counted is not None or it.stock_snapshot is not None:
                raise HTTPException(
                    status_code=400,
                    detail=tr(
                        "Sản phẩm '{name}' đếm theo lô, không nhận số tổng",
                        name=prod.name,
                    ),
                )
            allow_empty_reconciliation = False
            if not it.batches:
                has_positive_batches = (
                    db.query(models.ProductBatch.id)
                    .filter(
                        models.ProductBatch.product_id == prod.id,
                        models.ProductBatch.quantity > 0,
                    )
                    .first()
                    is not None
                )
                allow_empty_reconciliation = (
                    not has_positive_batches
                    and inventory_service.open_offline_batch_deficit_qty(
                        db, prod.id
                    )
                    > 0
                )
            _kiem_dinh_dang_dong_lo(
                prod,
                it.batches,
                allow_empty_reconciliation=allow_empty_reconciliation,
            )
        else:
            if it.batches:
                raise HTTPException(
                    status_code=400,
                    detail=tr(
                        "Sản phẩm '{name}' không theo dõi lô, không đếm theo lô "
                        "được",
                        name=prod.name,
                    ),
                )
            if it.counted is None or it.stock_snapshot is None:
                raise HTTPException(
                    status_code=400,
                    detail=tr(
                        "Sản phẩm '{name}' thiếu số đếm", name=prod.name
                    ),
                )
            # `counted` là số món đang cầm trên tay nên không bao giờ âm, nhưng
            # `stock_snapshot` là TỒN ĐANG GHI, và bán offline lúc hết hàng cố ý
            # đẩy nó xuống âm (bẫy 28). Chặn snapshot âm là chặn luôn đúng cái
            # phiếu kiểm kê dùng để chữa khoản thiếu đó.
            if (
                it.counted < 0
                or it.counted > MAX_SAFE_QUANTITY
                or it.stock_snapshot < -MAX_SAFE_QUANTITY
                or it.stock_snapshot > MAX_SAFE_QUANTITY
            ):
                raise HTTPException(
                    status_code=400, detail=tr("Số đếm nằm ngoài giới hạn")
                )

    da_dieu_chinh: List[Dict[str, Any]] = []
    bo_qua: List[Dict[str, Any]] = []
    khong_doi = 0
    actor_user_id = int(current_user.id)
    # One canonical instant for the whole sheet: two rows of the same stocktake
    # must not be able to disagree about when the count happened.
    resolved_at = canonical_time_text(datetime.utcnow())

    try:
        for it in items:
            prod = san_pham.get(it.product_id)
            if prod is None:
                bo_qua.append({
                    "product_id": it.product_id,
                    "name": None,
                    "ly_do": "Sản phẩm không còn tồn tại trong cửa hàng",
                })
                continue

            if prod.track_batches:
                dieu_chinh, bo = _kiem_ke_theo_lo(
                    db,
                    prod,
                    it.batches,
                    it.offline_deficit_snapshot,
                    actor_user_id=actor_user_id,
                    resolved_at=resolved_at,
                )
                da_dieu_chinh.extend(dieu_chinh)
                bo_qua.extend(bo)
                if not dieu_chinh and not bo:
                    khong_doi += 1
                continue

            # Read the exact evidence BEFORE touching stock, so the token that
            # authorizes closing it describes the state this count was made
            # against - including an ABA round trip that leaves the open total
            # unchanged.
            deficit_state = inventory_service.offline_stock_deficit_state(
                db, prod.id
            )
            offline_deficit_before = int(deficit_state["open_quantity"])
            if (
                it.offline_deficit_snapshot is not None
                and it.offline_deficit_snapshot != deficit_state["snapshot"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OFFLINE_DEFICIT_SNAPSHOT_STALE",
                        "message": tr(
                            "Evidence tồn âm offline đã đổi trong lúc kiểm kê; "
                            "vui lòng bắt đầu lại dòng này"
                        ),
                    },
                )
            if offline_deficit_before > 0 and it.offline_deficit_snapshot is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OFFLINE_DEFICIT_SNAPSHOT_REQUIRED",
                        "message": tr(
                            "Phiếu kiểm kê thiếu snapshot evidence tồn âm offline"
                        ),
                    },
                )

            ton_hien_tai = prod.stock or 0
            if ton_hien_tai != it.stock_snapshot:
                bo_qua.append({
                    "product_id": prod.id,
                    "name": prod.name,
                    "ly_do": (
                        f"Tồn kho đã đổi từ {it.stock_snapshot} thành {ton_hien_tai} "
                        "trong lúc kiểm kê. Vui lòng đếm lại sản phẩm này."
                    ),
                })
                continue

            if ton_hien_tai == it.counted:
                khong_doi += 1
                continue

            lech = it.counted - ton_hien_tai
            if lech > 0:
                inventory_service.add_cost_pool(prod, lech, None)
            else:
                inventory_service.consume_cost_pool(prod, -lech)
            prod.stock = it.counted
            dong_ket_qua = {
                "product_id": prod.id,
                "name": prod.name,
                "truoc": ton_hien_tai,
                "sau": it.counted,
                "lech": lech,
            }
            # Only goods that actually reappeared may retire the evidence that
            # they were missing. A count that is unchanged or lower proves
            # nothing came back, so it leaves every deficit exactly as it was.
            if lech > 0 and offline_deficit_before > 0:
                reconcile_qty = min(lech, offline_deficit_before)
                dong_ket_qua["offline_deficit_reconciled"] = (
                    inventory_service.reconcile_offline_stock_deficits(
                        db,
                        prod.id,
                        reconcile_qty,
                        actor_user_id=actor_user_id,
                        resolved_at=resolved_at,
                    )
                )
            da_dieu_chinh.append(dong_ket_qua)

        if da_dieu_chinh:
            tong_lech = sum(d["lech"] for d in da_dieu_chinh)
            # Liệt kê tối đa 10 dòng để một phiếu kiểm kê lớn không sinh ra dòng log
            # dài vô hạn; con số tổng vẫn phản ánh đủ. Dòng theo lô nêu kèm hạn:
            # không có nó thì cùng một sản phẩm hiện mấy dòng giống hệt nhau.
            chi_tiet = ", ".join(
                f"{d['name']}"
                f"{' HSD ' + (d.get('expiry_date') or '-') if d.get('batch_id') else ''}"
                f": {d['truoc']}->{d['sau']}"
                for d in da_dieu_chinh[:10]
            )
            if len(da_dieu_chinh) > 10:
                chi_tiet += f" (và {len(da_dieu_chinh) - 10} dòng khác)"
            # Transaction-local, NOT `log_system_action()`: that helper commits
            # by itself and swallows the error, which would leave a closed
            # deficit and a resolved issue with no record of who closed them.
            db.add(
                models.SystemLog(
                    user_id=current_user.id,
                    # Ghi tường minh: ADMIN thao tác trên nhiều shop, suy từ
                    # user_id sẽ hoặc làm log vô hình ở màn "Ai làm gì" của chủ
                    # shop, hoặc lộ việc của shop khác.
                    shop_id=shop_id,
                    action="STOCKTAKE",
                    details=(
                        f"Kiểm kê shop {shop_id}: điều chỉnh "
                        f"{len(da_dieu_chinh)} dòng, lệch tổng {tong_lech:+d}. "
                        f"{chi_tiet}"
                    ),
                )
            )
        # Stock, cost pools, offline evidence, issue transitions and audit all
        # become durable together or not at all.
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "da_dieu_chinh": da_dieu_chinh,
        "bo_qua": bo_qua,
        "khong_doi": khong_doi,
        "tong_lech": sum(d["lech"] for d in da_dieu_chinh),
    }


_NGAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _kiem_han_su_dung(raw: Optional[str]) -> Optional[str]:
    """Chuỗi 'YYYY-MM-DD' hoặc None. Sai định dạng -> 400 chứ không lưu bừa.

    Lưu dạng chuỗi theo đúng định dạng này để so sánh chuỗi cũng là so sánh
    đúng thứ tự ngày, và tránh hẳn bài toán múi giờ.
    """
    if raw is None or not str(raw).strip():
        return None
    chuoi = str(raw).strip()
    if not _NGAY.match(chuoi):
        raise HTTPException(
            status_code=400,
            detail=tr("Hạn sử dụng phải theo định dạng YYYY-MM-DD"),
        )
    try:
        datetime.strptime(chuoi, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=tr("Hạn sử dụng không phải một ngày có thật"),
        )
    return chuoi


def add_purchase_stock(
    db: Session,
    prod: models.Product,
    quantity: int,
    unit_cost: int,
    expiry_date: Optional[str],
    *,
    batch_note: Optional[str] = None,
) -> Optional[models.ProductBatch]:
    """Cộng tồn từ phiếu nhập trong transaction của caller, KHÔNG commit.

    Không được gọi ``adjust_stock`` theo từng dòng phiếu vì hàm public đó tự
    commit. Phiếu nhiều dòng phải hoặc cùng ghi đủ kho+nợ, hoặc không ghi gì.
    """
    if quantity <= 0 or unit_cost < 0:
        raise HTTPException(status_code=400, detail=tr("Dòng phiếu nhập không hợp lệ"))
    current_stock = int(prod.stock or 0)
    if quantity > MAX_SAFE_QUANTITY or current_stock > MAX_SAFE_QUANTITY - quantity:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Tồn kho của '{name}' sau khi nhập vượt giới hạn {maximum}",
                name=prod.name,
                maximum=f"{MAX_SAFE_QUANTITY:,}",
            ),
        )
    if prod.track_batches:
        han = _kiem_han_su_dung(expiry_date)
        if han is None:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' có theo dõi hạn sử dụng; "
                    "phiếu nhập phải khai hạn của lô",
                    name=prod.name,
                ),
            )
        batch = models.ProductBatch(
            product_id=prod.id,
            shop_id=prod.shop_id,
            expiry_date=han,
            quantity=quantity,
            cost_known_qty=quantity,
            cost_unknown_qty=0,
            cost_basis_vnd=checked_multiply(quantity, unit_cost),
            cost_deficit_qty=0,
            cost_state_version=1,
            note=(batch_note or "").strip()[:200] or None,
        )
        db.add(batch)
        db.flush()
        prod.stock = current_stock + quantity
        return batch

    if expiry_date is not None and str(expiry_date).strip():
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Sản phẩm '{name}' không theo dõi lô nên không nhận hạn sử dụng",
                name=prod.name,
            ),
        )
    inventory_service.add_cost_pool(prod, quantity, unit_cost)
    prod.stock = current_stock + quantity
    return None


def _dieu_chinh_ton_theo_lo(
    db: Session,
    current_user: models.User,
    prod: models.Product,
    delta: int,
    unit_cost: Optional[int],
    expiry_date: Optional[str],
    reason: str,
) -> Dict[str, Any]:
    """Nhập/xuất kho cho sản phẩm có theo dõi lô.

    Nhập tạo LÔ MỚI; xuất trừ theo FEFO. `Product.stock` được ghi trong cùng
    transaction với lô - đó là bản sao của tổng lô, không phải một con số sống
    độc lập.
    """
    han = _kiem_han_su_dung(expiry_date)
    if delta > 0:
        if han is None:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' có theo dõi hạn sử dụng; "
                    "nhập hàng phải khai hạn của lô",
                    name=prod.name,
                ),
            )
        batch = models.ProductBatch(
                product_id=prod.id,
                shop_id=prod.shop_id,
                expiry_date=han,
                quantity=delta,
                cost_known_qty=delta if unit_cost is not None else 0,
                cost_unknown_qty=delta if unit_cost is None else 0,
                cost_basis_vnd=checked_multiply(delta, unit_cost or 0),
                cost_deficit_qty=0,
                cost_state_version=1,
            )
        db.add(batch)
        prod.stock = int(prod.stock or 0) + delta
        mo_ta = f"Nhập lô HSD {han} x{delta}"
    else:
        can_tru = -delta
        kha_dung = sum(
            b.quantity for b in inventory_service.lo_con_ban_duoc(db, prod.id)
        )
        if kha_dung < can_tru:
            raise HTTPException(
                status_code=400,
                detail=tr(
                    "Sản phẩm '{name}' chỉ còn {available} chưa hết hạn "
                    "(tổng tồn {total})",
                    name=prod.name,
                    available=kha_dung,
                    total=int(prod.stock or 0),
                ),
            )
        con_lai = can_tru
        for lo in inventory_service.lo_con_ban_duoc(db, prod.id):
            if con_lai <= 0:
                break
            lay = min(lo.quantity, con_lai)
            inventory_service.consume_cost_pool(lo, lay)
            lo.quantity -= lay
            con_lai -= lay
        prod.stock = int(prod.stock or 0) - can_tru
        mo_ta = f"Xuất theo FEFO x{can_tru}"

    db.commit()
    db.refresh(prod)
    log_system_action(
        db,
        current_user.id,
        "ADJUST_STOCK",
        f"{mo_ta} - SP '{prod.name}' ({prod.code}) -> tồn {prod.stock}. "
        f"Lý do: {reason}",
    )
    db.refresh(prod)
    ket_qua: Dict[str, Any] = {
        "id": prod.id,
        "stock": prod.stock,
        "delta": delta,
        "available_stock": inventory_service.ton_kha_dung(db, prod),
    }
    if has_cost_visibility(prod.shop, current_user):
        ket_qua["cost_price"] = prod.cost_price
    return ket_qua


def adjust_stock(
    db: Session,
    current_user: models.User,
    product_id: int,
    delta: int,
    unit_cost: Optional[int] = None,
    expiry_date: Optional[str] = None,
    reason: str = "",
) -> Dict[str, Any]:
    """Nhập (delta > 0) hoặc xuất (delta < 0) kho theo số lượng thay đổi.

    Dùng UPDATE nguyên tử `stock = stock + delta` thay vì đọc-rồi-ghi, nên
    nhiều thao tác kho / bán hàng chạy song song không ghi đè lẫn nhau. Điều
    kiện `stock + delta >= 0` nằm ngay trong câu UPDATE -> tồn kho không bao
    giờ âm; nếu xuất quá số đang có, rowcount = 0 và ta báo lỗi.

    `unit_cost` là đơn giá của LÔ ĐANG NHẬP, không phải giá vốn mới. Gửi kèm
    thì giá vốn được tính lại theo bình quân gia quyền; không gửi thì giữ
    nguyên. Phân biệt "không gửi" với "gửi 0" là bắt buộc: 0 là giá thật của
    hàng tặng và phải kéo bình quân xuống, còn không gửi là không có thông tin.
    Chỗ này an toàn hơn ô trên form vì đi qua JSON body, nơi `None` và `0` là
    hai giá trị khác nhau thật sự.
    """
    prod = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))
    shop = require_shop_access(db, prod.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)

    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail=tr("Vui lòng nhập lý do điều chỉnh kho"))
    reason = reason[:500]

    if delta == 0:
        raise HTTPException(
            status_code=400,
            detail=tr("Số lượng thay đổi phải khác 0"),
        )

    if unit_cost is not None:
        require_cost_visibility(shop, current_user)
        unit_cost = _kiem_gia_von(unit_cost)
        # Từ chối thẳng thay vì im lặng bỏ qua: người dùng gõ đơn giá vào phiếu
        # xuất là đang hiểu sai công dụng của ô đó, và im lặng nuốt sẽ để họ tin
        # rằng giá vốn vừa được cập nhật.
        if delta < 0:
            raise HTTPException(
                status_code=400,
                detail=tr("Phiếu xuất kho không nhận đơn giá nhập"),
            )

    # Product.stock and its exact cost pool are a single state machine.  Use the
    # same shop write-lock for batch and non-batch inventory mutations, then
    # discard every object read before the lock.
    locked_shop_id = int(prod.shop_id)
    inventory_service.lock_shop_for_inventory(db, locked_shop_id)
    db.expire_all()
    prod = (
        db.query(models.Product)
        .filter(
            models.Product.id == product_id,
            models.Product.shop_id == locked_shop_id,
        )
        .first()
    )
    if prod is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))

    if prod.track_batches:
        return _dieu_chinh_ton_theo_lo(
            db, current_user, prod, delta, unit_cost, expiry_date, reason
        )

    gia_von_truoc = prod.cost_price
    current_stock = int(prod.stock or 0)
    if delta < 0 and current_stock < -delta:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Không đủ tồn kho để xuất {quantity} (hiện còn {stock})",
                quantity=abs(delta),
                stock=prod.stock,
            ),
        )
    if delta > 0:
        if current_stock > MAX_SAFE_QUANTITY - delta:
            db.rollback()
            raise HTTPException(status_code=400, detail=tr("Tồn kho sau điều chỉnh vượt giới hạn"))
        inventory_service.add_cost_pool(prod, delta, unit_cost)
    else:
        inventory_service.consume_cost_pool(prod, -delta)
    prod.stock = current_stock + delta
    db.commit()
    db.refresh(prod)
    gia_von_sau = prod.cost_price

    hanh_dong = "Nhập" if delta > 0 else "Xuất"
    ghi_chu_gia_von = ""
    if unit_cost is not None:
        ghi_chu_gia_von = (
            f", đơn giá {unit_cost:,.0f}đ"
            f" -> giá vốn BQ {_mo_ta_gia_von(gia_von_truoc)}"
            f" thành {_mo_ta_gia_von(gia_von_sau)}"
        )
    log_system_action(
        db,
        current_user.id,
        "ADJUST_STOCK",
        f"{hanh_dong} kho SP '{prod.name}' ({prod.code}): "
        f"{'+' if delta > 0 else ''}{delta} -> tồn {prod.stock}{ghi_chu_gia_von}. "
        f"Lý do: {reason}",
    )
    ket_qua: Dict[str, Any] = {"id": prod.id, "stock": prod.stock, "delta": delta}
    # Chỉ đính giá vốn cho người được xem. Nhân viên kho vẫn nhập/xuất được
    # bình thường, chỉ là phản hồi không kèm con số đó.
    if has_cost_visibility(shop, current_user):
        ket_qua["cost_price"] = gia_von_sau
    return ket_qua


def toggle_product_status(
    db: Session, current_user: models.User, product_id: int
) -> Dict[str, bool]:
    prod = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))
    require_shop_access(db, prod.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)
    prod.is_active = not prod.is_active
    db.commit()
    log_system_action(
        db,
        current_user.id,
        "TOGGLE_PRODUCT_STATUS",
        f"Đổi trạng thái SP '{prod.name}' ({prod.code}): {'Hiện' if prod.is_active else 'Ẩn'}",
    )
    return {"is_active": prod.is_active}


def delete_product(db: Session, current_user: models.User, product_id: int) -> Dict[str, str]:
    prod = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))
    require_shop_access(db, prod.shop_id, current_user)
    require_staff_permission(current_user, PERMISSION_INVENTORY)
    shop_id = int(prod.shop_id)
    # PurchaseReceiptItem giữ product_id để lúc confirm cộng đúng sản phẩm.
    # SQLite không bật FK và có thể tái dùng ID sau hard-delete: nếu xóa P rồi
    # tạo Q nhận lại cùng ID, phiếu nháp P sẽ âm thầm nhập hàng vào Q. Khóa cùng
    # các đường tồn, reload, rồi chặn xóa cứng khi đã có bất kỳ dòng phiếu nhập.
    inventory_service.lock_shop_for_inventory(db, shop_id)
    db.expire_all()
    prod = (
        db.query(models.Product)
        .filter(models.Product.id == product_id, models.Product.shop_id == shop_id)
        .first()
    )
    if prod is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=tr("Sản phẩm không tồn tại"))
    has_purchase_history = (
        db.query(models.PurchaseReceiptItem.id)
        .filter(models.PurchaseReceiptItem.product_id == product_id)
        .first()
        is not None
    ) or (
        db.query(models.PurchaseOrderItem.id)
        .filter(models.PurchaseOrderItem.product_id == product_id)
        .first()
        is not None
    )
    if has_purchase_history:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Sản phẩm đã nằm trong chứng từ nhập hàng nên không thể xóa. "
                "Hãy bấm Ẩn để giữ đúng lịch sử chứng từ."
            ),
        )
    has_fnb_history = (
        db.query(models.FnbSessionLine.id)
        .filter(models.FnbSessionLine.product_id == product_id)
        .first()
        is not None
    )
    if has_fnb_history:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=tr(
                "Sản phẩm đã nằm trong lịch sử bán tại bàn nên không thể xóa. "
                "Hãy bấm Ẩn để giữ đúng lịch sử."
            ),
        )
    name, code = prod.name, prod.code
    db.delete(prod)
    db.commit()
    log_system_action(db, current_user.id, "DELETE_PRODUCT", f"Xóa SP '{name}' ({code})")
    return {"msg": "Deleted"}
