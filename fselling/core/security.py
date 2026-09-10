"""Hash mật khẩu, chính sách mật khẩu và JWT.

Giữ nguyên thuật toán cũ: bcrypt cho mật khẩu, HS256 cho JWT,
payload gồm {sub, exp, sid}; trạng thái phiên nằm trong ``auth_sessions``.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import bcrypt
import jwt

from .config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY

# Mật khẩu phải có: chữ hoa, chữ thường, số và ký tự đặc biệt.
_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"\d")
_SPECIAL = re.compile(r"[!@#$%^&*(),.?\":{}|<>]")


def is_strong_password(password: str) -> bool:
    if not password:
        return False
    return bool(
        _UPPER.search(password)
        and _LOWER.search(password)
        and _DIGIT.search(password)
        and _SPECIAL.search(password)
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# Băm sẵn một lần lúc nạp module để `burn_password_time()` không phải trả giá
# gensalt ở mỗi request. Giá trị cụ thể không quan trọng - không ai đăng nhập
# bằng nó, vì nó không gắn với tài khoản nào cả.
_DUMMY_HASH = hash_password("khong-phai-mat-khau-cua-ai-ca")


def burn_password_time() -> None:
    """Tiêu đúng lượng thời gian của một lần kiểm mật khẩu, rồi bỏ kết quả.

    Gọi khi tên đăng nhập KHÔNG tồn tại. Không có nó thì tài khoản không tồn tại
    trả lời gần như tức thì, còn tài khoản có thật phải chờ bcrypt - chênh lệch
    đó đủ để dò xem username nào có trong hệ thống mà không cần đoán đúng mật
    khẩu lần nào.
    """
    bcrypt.checkpw(b"khong-phai-mat-khau-cua-ai-ca", _DUMMY_HASH.encode("utf-8"))


def new_session_id() -> str:
    """Sinh định danh phiên ngẫu nhiên phía server."""
    return uuid.uuid4().hex


def create_access_token(
    username: str, session_id: str, *, expires_at: datetime | None = None
) -> str:
    expire = expires_at or (
        datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode: Dict[str, Any] = {"sub": username, "exp": expire, "sid": session_id}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Raise jwt.PyJWTError nếu token sai/hết hạn."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def generate_otp() -> str:
    """OTP 6 chữ số, dùng nguồn ngẫu nhiên an toàn (thay cho random.randint)."""
    return f"{secrets.randbelow(900000) + 100000}"


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Chỉ chấp nhận token qua Authorization header, KHÔNG nhận qua query string
    để tránh lộ token qua browser history / server log / screenshot."""
    if authorization and authorization.startswith("Bearer "):
        parts = authorization.split(" ")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    return None


def compare_secret(candidate: Optional[str], expected: Optional[str]) -> bool:
    """So sánh chống timing attack cho webhook secret."""
    if not candidate or not expected:
        return False
    return secrets.compare_digest(str(candidate), str(expected))
