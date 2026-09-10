"""Ghi nhật ký hành động vào bảng system_logs."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import and_, false, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import models


def log_system_action(
    db: Session,
    user_id: Optional[int],
    action: str,
    details: str = "",
    *,
    shop_id: Optional[int] = None,
) -> None:
    try:
        log_entry = models.SystemLog(
            user_id=user_id,
            shop_id=shop_id,
            auth_session_id=db.info.get("auth_session_id"),
            action=action,
            details=details,
        )
        db.add(log_entry)
        db.commit()
    except SQLAlchemyError as e:
        print(f"Error logging action: {e}")
        db.rollback()


# Hành động KHÔNG hiện ở màn "Ai làm gì" của chủ shop.
#
# CỐ Ý là danh sách LOẠI TRỪ, không phải danh sách cho phép. Thêm một hành động
# mới ở đâu đó trong code mà quên khai vào đây thì nó VẪN hiện ra — thừa một
# dòng còn hơn thiếu một dòng ở đúng cái màn hình dùng để soi. Danh sách cho
# phép thì ngược lại: quên khai là hành động đó vô hình vĩnh viễn và không ai
# biết màn hình đang thủng.
#
# Đo trên dữ liệu thật: 118 trên 232 dòng là LOGIN. Trộn vào thì phải cuộn hai
# màn hình mới thấy một lần hủy đơn, và màn hình nào phải cuộn mãi mới thấy
# điều quan trọng thì người ta thôi mở nó.
KHONG_HIEN_O_SHOP = frozenset({
    "LOGIN",
    # Thêm mới thì không rủi ro; SỬA và XÓA mới là thứ cần soi, và hai cái đó
    # không nằm trong danh sách này nên vẫn hiện.
    "CREATE_PRODUCT",
    "CREATE_CATEGORY",
    "UPDATE_CATEGORY",
    "CREATE_SHOP",
    "UPDATE_SHOP",
    "TOGGLE_SHOP_STATUS",
    "CREATE_CUSTOMER",
    "UPDATE_CUSTOMER",
})


def _nguoi_thao_tac_cua_shop(db: Session, shop_id: int) -> List[int]:
    """ID chủ shop + nhân viên để đọc những dòng lịch sử chưa có ``shop_id``.

    Dòng mới có thể ghi thẳng ``shop_id``. Dòng cũ chỉ ghi AI làm GÌ nên vẫn cần
    danh sách này để không bỏ đi lịch sử trước migration.

    Chấp nhận một điểm mờ: chủ shop có nhiều cửa hàng thì việc do chính họ làm
    không chỉ đích danh được cửa hàng nào. Đổi lại, câu hỏi thật sự cần trả lời
    ở màn này là "có ai hủy đơn hay hoàn tiền bất thường không" — với câu đó thì
    biết AI làm quan trọng hơn biết ở cửa hàng nào.

    KHÔNG gộp ADMIN vào nhánh legacy: tài khoản đó thao tác trên mọi shop, đưa
    vào là chủ shop này nhìn thấy việc liên quan tới shop của người khác. Log
    Admin mới chỉ hiện khi chính dòng đó có ``shop_id`` tường minh.
    """
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    ids = set()
    if shop and shop.owner_id:
        ids.add(shop.owner_id)
    for (uid,) in db.query(models.User.id).filter(
        models.User.staff_shop_id == shop_id
    ):
        ids.add(uid)
    return sorted(ids)


def nhat_ky_cua_shop(
    db: Session, shop_id: int, page: int = 1, per_page: int = 30
) -> dict:
    """Nhật ký thao tác của một cửa hàng, mới nhất trước."""
    page = max(1, int(page or 1))
    per_page = min(max(1, int(per_page or 30)), 100)

    nguoi = _nguoi_thao_tac_cua_shop(db, shop_id)
    legacy_actor = (
        models.SystemLog.user_id.in_(nguoi) if nguoi else false()
    )

    truy_van = (
        db.query(models.SystemLog)
        .filter(
            or_(
                models.SystemLog.shop_id == shop_id,
                and_(
                    models.SystemLog.shop_id.is_(None),
                    legacy_actor,
                ),
            ),
            models.SystemLog.action.notin_(KHONG_HIEN_O_SHOP),
        )
        .order_by(models.SystemLog.created_at.desc(), models.SystemLog.id.desc())
    )
    total = truy_van.count()
    dong = truy_van.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "logs": [
            {
                "id": l.id,
                "username": l.user.username if l.user else "Hệ thống",
                "action": l.action,
                "details": l.details,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in dong
        ],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page,
    }


def get_recent_logs(db: Session, limit: int = 100) -> List[dict]:
    logs = (
        db.query(models.SystemLog)
        .order_by(models.SystemLog.created_at.desc())
        .limit(limit)
        .all()
    )
    res = []
    for log in logs:
        username = log.user.username if log.user else "System"
        res.append(
            {
                "id": log.id,
                "username": username,
                "action": log.action,
                "details": log.details,
                "created_at": log.created_at.isoformat(),
            }
        )
    return res
