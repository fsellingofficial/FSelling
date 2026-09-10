from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr
    # KHÔNG nhận 'role' từ client. Đăng ký công khai luôn tạo tài khoản SELLER.


class EmailVerify(BaseModel):
    email: str
    code: str


class ResendCodeRequest(BaseModel):
    email: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordReset(BaseModel):
    email: str
    code: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class Login(BaseModel):
    username: str
    password: str
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    device_type: Optional[str] = None


class AuthSessionView(BaseModel):
    session_id: str
    device_id: str
    device_name: str
    device_type: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    current: bool


class AuthSessionRename(BaseModel):
    device_name: str


class AuthDeviceRevoke(BaseModel):
    device_id: str


class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    staff_role: Optional[str] = None
    session: AuthSessionView
