import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..core.database import Base


class SystemLog(Base):
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True, index=True)
    # Có thể null nếu action từ hệ thống hoặc chưa login
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Dòng mới nên ghi tường minh shop_id khi biết. Đặc biệt ADMIN thao tác trên
    # nhiều shop: chỉ suy từ user_id sẽ hoặc làm log vô hình, hoặc lộ việc của
    # shop khác. NULL giữ tương thích toàn bộ lịch sử cũ.
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True, index=True)
    auth_session_id = Column(
        String(128), ForeignKey("auth_sessions.session_id"), nullable=True, index=True
    )
    action = Column(String, index=True)
    details = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User")


class AssistantAiUsage(Base):
    """Đếm lượt gọi Gemini của trợ lý, theo shop theo NGÀY nghiệp vụ.

    Đếm trong DATABASE chứ không trong bộ nhớ tiến trình - cùng lý do với bộ đếm
    chống dò mật khẩu (mục 17): khởi động lại server là bộ đếm trong RAM về 0,
    mà restart thì ép được. Ở đây hậu quả không phải bảo mật mà là tiền: hạn mức
    miễn phí của Google cạn thì mọi shop cùng mất tính năng.

    Chỉ đếm lượt THỰC SỰ gọi ra Google. Câu hỏi mà bộ so khớp nội bộ tự hiểu
    không tiêu lượt nào, và đó là đại đa số.
    """

    __tablename__ = "assistant_ai_usage"
    __table_args__ = (
        Index("ux_assistant_ai_usage_shop_ngay", "shop_id", "ngay", unique=True),
    )

    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    # Ngày nghiệp vụ Việt Nam dạng 'YYYY-MM-DD' (core/thoi_gian). Dùng ngày UTC
    # ở đây là hạn mức reset lúc 7 giờ sáng, giữa buổi bán hàng.
    ngay = Column(String(10), nullable=False)
    so_luot = Column(Integer, nullable=False, default=0)
