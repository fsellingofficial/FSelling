"""Router xác thực. Chỉ xử lý HTTP, nghiệp vụ nằm trong auth_service."""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from .. import models
from ..dependencies import get_current_user, get_db
from ..schemas.auth import (
    AuthDeviceRevoke,
    AuthSessionRename,
    ChangePasswordRequest,
    EmailVerify,
    ForgotPasswordRequest,
    ForgotPasswordReset,
    Login,
    ResendCodeRequest,
    Token,
    UserCreate,
)
from ..services import auth_service, auth_session_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


# `background_tasks` được truyền xuống service để việc gửi email KHÔNG nằm
# trong thời gian trả lời request: nói chuyện với máy chủ mail mất hàng giây, mà
# mỗi giây đó giữ một luồng trong threadpool của FastAPI - hết luồng thì cả app
# đứng, kể cả POS đang bán hàng.
@router.post("/register")
def register(
    user: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    return auth_service.register(db, user, background_tasks)


@router.post("/verify-email")
def verify_email(data: EmailVerify, db: Session = Depends(get_db)):
    return auth_service.verify_email(db, data)


@router.post("/resend-code")
def resend_code(
    data: ResendCodeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    return auth_service.resend_code(db, data, background_tasks)


@router.post("/forgot-password-request")
def forgot_password_request(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    return auth_service.forgot_password_request(db, data, background_tasks)


@router.post("/forgot-password-reset")
def forgot_password_reset(data: ForgotPasswordReset, db: Session = Depends(get_db)):
    return auth_service.forgot_password_reset(db, data)


@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return auth_service.change_password(db, current_user, data)


@router.post("/login", response_model=Token, response_model_exclude_none=True)
def login(user: Login, db: Session = Depends(get_db)):
    return auth_service.login(db, user)


@router.get("/session-check")
def session_check(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    auth_session_service.require_live_session(
        db,
        current_user.id,
        db.info["auth_session_id"],
        touch=True,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/logout")
def logout(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    auth_session_service.revoke_session(
        db,
        current_user,
        current_user.id,
        db.info["auth_session_id"],
        "LOGOUT",
    )
    return {"msg": "Logged out"}


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return auth_session_service.list_user_sessions(
        db, current_user.id, db.info["auth_session_id"]
    )


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: str,
    data: AuthSessionRename,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return auth_session_service.rename_session(
        db, current_user, current_user.id, session_id, data.device_name
    )


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return auth_session_service.revoke_session(
        db, current_user, current_user.id, session_id, "SELF_REVOKE"
    )


@router.post("/devices/revoke")
def revoke_device(
    data: AuthDeviceRevoke,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return auth_session_service.revoke_device(
        db,
        actor=current_user,
        target_user_id=current_user.id,
        device_id=data.device_id,
    ).as_dict()
