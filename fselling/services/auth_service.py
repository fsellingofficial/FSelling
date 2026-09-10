"""Đăng ký, xác minh email, quên/đổi mật khẩu, đăng nhập."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..core.config import (
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_ATTEMPTS,
    OTP_EXPIRE_MINUTES,
    OTP_MAX_ATTEMPTS,
    OTP_RESEND_COOLDOWN_SECONDS,
    log_to_file,
)
from ..core.i18n import tr
from ..core.security import (
    burn_password_time,
    generate_otp,
    hash_password,
    is_strong_password,
    verify_password,
)
from ..dependencies import effective_staff_role
from ..schemas.auth import (
    ChangePasswordRequest,
    EmailVerify,
    ForgotPasswordRequest,
    ForgotPasswordReset,
    Login,
    ResendCodeRequest,
    UserCreate,
)
from . import auth_session_service, email_service
from .log_service import log_system_action

PASSWORD_POLICY_MSG = "Mật khẩu phải bao gồm kí tự đặc biệt, chữ hoa, chữ thường và số"

# Câu trả lời DUY NHẤT của các endpoint xin mã, dùng cho MỌI trường hợp: email
# có thật, email không tồn tại, hay đang trong thời gian chờ. Trả lời khác nhau
# theo từng ca là nói cho người hỏi biết email nào đã đăng ký F-Selling - thứ
# dùng để nhắm mục tiêu lừa đảo.
MSG_DA_GUI_MA = (
    "Nếu email này có tài khoản, mã xác minh đã được gửi. "
    "Nhớ kiểm tra cả hộp thư rác."
)


def _gui_mail_nen(
    background_tasks: Optional[BackgroundTasks],
    email_to: str,
    otp_code: str,
    subject: str,
) -> None:
    """Đẩy việc gửi mail ra ngoài thời gian trả lời request.

    Gửi thẳng ở đây thì request phải chờ hết vòng nói chuyện với máy chủ mail -
    đo được 3,5 giây với Gmail - và mỗi lần chờ giữ một luồng trong threadpool
    của FastAPI. Vài chục request là hết luồng, lúc đó POS cũng không bán được
    hàng dù POS chẳng liên quan gì tới email.

    Tách ra nền còn khép luôn kênh đo thời gian: email có thật và email không
    tồn tại giờ trả lời nhanh như nhau.

    `background_tasks` để None được, cho các lời gọi từ script/test không đi qua
    HTTP; khi đó gửi thẳng như cũ.
    """
    if background_tasks is None:
        email_service.send_otp_email(email_to, otp_code, subject)
        return
    background_tasks.add_task(
        email_service.send_otp_email, email_to, otp_code, subject
    )


def _token_response(
    user: models.User,
    token: str,
    auth_session: models.AuthSession,
) -> Dict[str, object]:
    response = {"access_token": token, "token_type": "bearer", "role": user.role}
    if user.role == "STAFF":
        response["staff_role"] = effective_staff_role(user)
    response["session"] = auth_session_service.session_view(
        auth_session, current_session_id=auth_session.session_id
    )
    return response


def _otp_expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)


def _cap_ma_moi(user: models.User) -> str:
    """Phát mã OTP mới cho user và dọn sạch trạng thái của mã cũ.

    Bộ đếm sai PHẢI về 0 ở đây: mã mới là một bí mật mới, số lần đoán hụt mã cũ
    không nói lên điều gì về nó. Quên reset thì người dùng thật xin mã mới xong
    vẫn bị chặn ngay lần nhập đầu.
    """
    otp_code = generate_otp()
    user.verification_code = otp_code
    user.verification_code_expires = _otp_expiry()
    user.verification_attempts = 0
    user.verification_code_sent_at = datetime.utcnow()
    return otp_code


def _dem_lan_nhap_sai_ma(db: Session, user: models.User) -> None:
    """Ghi nhận một lần nhập sai mã OTP; chạm ngưỡng thì HỦY mã.

    CỐ Ý hủy mã chứ không khóa tài khoản. Mã OTP chỉ 6 chữ số nên bắt buộc phải
    giới hạn số lần đoán, nhưng nếu phạt bằng cách khóa tài khoản theo email thì
    bất kỳ ai biết email của bạn cũng khóa được tài khoản bạn, chỉ bằng cách gõ
    bừa vài lần. Hủy mã thì kẻ tấn công chỉ tự làm mất công của chính họ, còn
    chủ tài khoản bấm "gửi lại mã" là xong.
    """
    user.verification_attempts = (user.verification_attempts or 0) + 1
    het_luot = user.verification_attempts >= OTP_MAX_ATTEMPTS
    if het_luot:
        user.verification_code = None
        user.verification_code_expires = None
    db.commit()
    if het_luot:
        raise HTTPException(
            status_code=429,
            detail=tr(
                "Đã nhập sai mã quá nhiều lần. Mã này bị hủy, "
                "vui lòng bấm gửi lại để nhận mã mới."
            ),
        )
    raise HTTPException(status_code=400, detail=tr("Mã xác thực không hợp lệ"))


def register(
    db: Session,
    user: UserCreate,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, str]:
    if not is_strong_password(user.password):
        raise HTTPException(status_code=400, detail=tr(PASSWORD_POLICY_MSG))

    if db.query(models.User).filter(models.User.username == user.username).first():
        raise HTTPException(status_code=400, detail=tr("Tên đăng nhập đã tồn tại"))

    if db.query(models.User).filter(models.User.email == user.email).first():
        raise HTTPException(
            status_code=400,
            detail=tr("Email này đã được đăng ký tài khoản khác"),
        )

    otp_code = generate_otp()
    db_user = models.User(
        username=user.username,
        hashed_password=hash_password(user.password),
        role="SELLER",  # Ép cứng SELLER, không tin role từ client (tránh tự nâng quyền admin)
        email=user.email,
        is_verified=False,
        verification_code=otp_code,
        verification_code_expires=_otp_expiry(),
        verification_code_sent_at=datetime.utcnow(),
    )
    db.add(db_user)
    db.commit()

    _gui_mail_nen(
        background_tasks, user.email, otp_code, "F-Selling: Xác minh tài khoản mới"
    )
    return {
        "msg": tr(
            "Đăng ký thành công. Vui lòng kiểm tra email để nhận mã kích hoạt tài khoản."
        )
    }


def verify_email(db: Session, data: EmailVerify) -> Dict[str, str]:
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        # Cùng một câu với "mã sai": email lạ mà trả 404 riêng thì đây cũng
        # thành một cửa để quét email nào đã đăng ký, y như forgot-password.
        # Người dùng thật không mất gì - gõ nhầm email thì mã cũng sai thật.
        raise HTTPException(status_code=400, detail=tr("Mã xác thực không hợp lệ"))
    if user.is_verified:
        return {"msg": tr("Tài khoản đã được xác minh trước đó.")}

    if not user.verification_code or user.verification_code != data.code:
        _dem_lan_nhap_sai_ma(db, user)

    if user.verification_code_expires and datetime.utcnow() > user.verification_code_expires:
        # Xóa tài khoản hết hạn để người dùng có thể đăng ký lại
        print(f"[VERIFY] Verification expired for user {user.username}. Deleting account.")
        db.delete(user)
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Mã xác thực đã hết hạn. Vui lòng đăng ký lại để nhận mã mới."
            ),
        )

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires = None
    user.verification_attempts = 0
    db.commit()
    return {
        "msg": tr(
            "Xác minh tài khoản thành công! Bây giờ bạn đã có thể đăng nhập."
        )
    }


def _con_trong_thoi_gian_cho(user: models.User) -> bool:
    if OTP_RESEND_COOLDOWN_SECONDS <= 0 or user.verification_code_sent_at is None:
        return False
    da_qua = (datetime.utcnow() - user.verification_code_sent_at).total_seconds()
    return da_qua < OTP_RESEND_COOLDOWN_SECONDS


def resend_code(
    db: Session,
    data: ResendCodeRequest,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, str]:
    """Gửi lại mã xác minh. LUÔN trả cùng một câu, xem `MSG_DA_GUI_MA`."""
    user = db.query(models.User).filter(models.User.email == data.email).first()

    # Ba lý do không gửi, và cả ba đều phải im lặng như nhau:
    #   - email không có tài khoản  -> lộ email nào đã đăng ký
    #   - tài khoản đã xác minh rồi -> lộ thêm cả trạng thái tài khoản
    #   - đang trong thời gian chờ  -> lộ qua đúng cái 429 của lần bấm thứ hai
    if user and not user.is_verified and not _con_trong_thoi_gian_cho(user):
        otp_code = _cap_ma_moi(user)
        db.commit()
        _gui_mail_nen(
            background_tasks,
            user.email,
            otp_code,
            "F-Selling: Gửi lại mã xác minh tài khoản",
        )
    return {"msg": tr(MSG_DA_GUI_MA)}


def forgot_password_request(
    db: Session,
    data: ForgotPasswordRequest,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, str]:
    """Xin mã khôi phục mật khẩu. LUÔN trả cùng một câu, xem `MSG_DA_GUI_MA`.

    Đây là endpoint gọi được mà KHÔNG cần đăng nhập và chỉ cần một địa chỉ
    email, nên nó là chỗ thuận tiện nhất để quét xem email nào có tài khoản.
    """
    user = db.query(models.User).filter(models.User.email == data.email).first()

    if user and not _con_trong_thoi_gian_cho(user):
        otp_code = _cap_ma_moi(user)
        db.commit()
        _gui_mail_nen(
            background_tasks, user.email, otp_code, "F-Selling: Mã khôi phục mật khẩu"
        )
    return {"msg": tr(MSG_DA_GUI_MA)}


def forgot_password_reset(db: Session, data: ForgotPasswordReset) -> Dict[str, str]:
    if not is_strong_password(data.new_password):
        raise HTTPException(status_code=400, detail=tr(PASSWORD_POLICY_MSG))

    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        # PHẢI dùng đúng chuỗi mà `_dem_lan_nhap_sai_ma` trả về, không phải một
        # câu "tương tự". Lệch một chữ ("xác nhận" so với "xác thực") là kẻ dò
        # phân biệt được ngay email nào có thật - test bắt được đúng lỗi này.
        raise HTTPException(status_code=400, detail=tr("Mã xác thực không hợp lệ"))

    # Đây là đường NGUY HIỂM NHẤT trong cả app: đoán trúng 6 chữ số là đặt được
    # mật khẩu mới cho tài khoản người khác. Bắt buộc phải đếm số lần đoán.
    if not user.verification_code or user.verification_code != data.code:
        _dem_lan_nhap_sai_ma(db, user)

    if user.verification_code_expires and datetime.utcnow() > user.verification_code_expires:
        raise HTTPException(status_code=400, detail=tr("Mã xác nhận đã hết hạn"))

    user.hashed_password = hash_password(data.new_password)
    user.verification_code = None
    user.verification_code_expires = None
    user.verification_attempts = 0
    # Đổi được mật khẩu nghĩa là đã chứng minh sở hữu email; gỡ luôn khóa đăng
    # nhập nếu đang bị, để chủ tài khoản không phải chờ hết giờ mới vào được.
    user.failed_login_count = 0
    user.locked_until = None
    auth_session_service.revoke_all_user_sessions(
        db,
        target_user_id=user.id,
        actor_user_id=user.id,
        reason="PASSWORD_RESET",
    )
    db.commit()
    return {"msg": tr("Đặt lại mật khẩu thành công! Vui lòng đăng nhập lại.")}


def change_password(
    db: Session, current_user: models.User, data: ChangePasswordRequest
) -> Dict[str, str]:
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=400,
            detail=tr("Mật khẩu hiện tại không chính xác"),
        )

    if not is_strong_password(data.new_password):
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Mật khẩu mới phải bao gồm kí tự đặc biệt, chữ hoa, chữ thường và số"
            ),
        )

    auth_session_service.fence_live_auth_session(db)
    current_session = db.get(
        models.AuthSession, db.info.get("auth_session_id")
    )
    if current_session is None:
        raise HTTPException(status_code=401, detail=tr("Phiên đăng nhập không hợp lệ"))
    device = (
        current_session.device_id,
        current_session.device_name,
        current_session.device_type,
    )
    current_user.hashed_password = hash_password(data.new_password)
    affected = auth_session_service.revoke_all_user_sessions(
        db,
        target_user_id=current_user.id,
        actor_user_id=current_user.id,
        reason="PASSWORD_CHANGE",
    )
    auth_session, token = auth_session_service.create_session(
        db, current_user, *device
    )
    db.add(
        models.SystemLog(
            user_id=current_user.id,
            auth_session_id=auth_session.session_id,
            action="AUTH_SESSIONS_REVOKE_PASSWORD",
            details=f"sessions={affected}",
        )
    )
    db.commit()
    log_system_action(
        db, current_user.id, "CHANGE_PASSWORD", f"User {current_user.username} changed password"
    )
    db.refresh(current_user)
    return _token_response(current_user, token, auth_session)


SAI_THONG_TIN = "Tên đăng nhập hoặc mật khẩu không chính xác"


def _con_bi_khoa(user: models.User) -> int:
    """Số phút còn lại của lệnh khóa, 0 nếu không còn bị khóa."""
    if user.locked_until is None:
        return 0
    con_lai = (user.locked_until - datetime.utcnow()).total_seconds()
    if con_lai <= 0:
        return 0
    return int(con_lai // 60) + 1


def _ghi_nhan_dang_nhap_sai(db: Session, user: models.User) -> None:
    """Đếm số lần sai liên tiếp và khóa tạm khi chạm ngưỡng."""
    user.failed_login_count = (user.failed_login_count or 0) + 1
    vua_khoa = user.failed_login_count >= LOGIN_MAX_ATTEMPTS
    if vua_khoa:
        user.locked_until = datetime.utcnow() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        user.failed_login_count = 0     # đếm lại từ đầu cho chu kỳ khóa sau
    db.commit()
    if vua_khoa:
        log_to_file(
            f"Login locked: user='{user.username}' for {LOGIN_LOCKOUT_MINUTES} minutes"
        )
        raise HTTPException(
            status_code=429,
            detail=tr(
                "Sai mật khẩu quá nhiều lần. Tài khoản tạm khóa {minutes} phút.",
                minutes=LOGIN_LOCKOUT_MINUTES,
            ),
        )
    raise HTTPException(status_code=401, detail=tr(SAI_THONG_TIN))


def login(db: Session, user: Login) -> Dict[str, str]:
    db_user = db.query(models.User).filter(models.User.username == user.username).first()

    if not db_user:
        # Tài khoản không tồn tại vẫn phải tốn đúng chừng ấy thời gian. Trả lời
        # ngay lập tức là nói cho kẻ dò biết username nào KHÔNG có, và loại trừ
        # dần cũng chính là dò ra username nào CÓ.
        burn_password_time()
        raise HTTPException(status_code=401, detail=tr(SAI_THONG_TIN))

    con_khoa = _con_bi_khoa(db_user)
    if con_khoa:
        # Chặn TRƯỚC khi kiểm mật khẩu: kiểm rồi mới chặn thì mỗi request vẫn
        # tốn một lần bcrypt, và cửa khóa thành ra một đường làm nghẽn server.
        raise HTTPException(
            status_code=429,
            detail=tr(
                "Tài khoản đang tạm khóa. Vui lòng thử lại sau {minutes} phút.",
                minutes=con_khoa,
            ),
        )

    if not verify_password(user.password, db_user.hashed_password):
        _ghi_nhan_dang_nhap_sai(db, db_user)

    if db_user.is_active is False:
        raise HTTPException(status_code=403, detail=tr("Tài khoản đã ngừng hoạt động"))

    if db_user.email and not db_user.is_verified:
        raise HTTPException(
            status_code=400,
            detail=tr(
                "Tài khoản chưa được xác minh email. Vui lòng xác minh trước khi đăng nhập."
            ),
        )

    auth_session, token = auth_session_service.create_session(
        db,
        db_user,
        user.device_id,
        user.device_name,
        user.device_type,
    )
    # Đăng nhập đúng xóa sạch lịch sử sai: bộ đếm là "sai LIÊN TIẾP", không phải
    # tổng cộng cả đời. Không reset thì người hay gõ nhầm sẽ bị khóa oan sau vài
    # tuần dùng bình thường.
    db_user.failed_login_count = 0
    db_user.locked_until = None
    db.commit()

    log_system_action(db, db_user.id, "LOGIN", f"User {db_user.username} logged in")
    db.refresh(db_user)
    log_to_file(f"Login success: user='{user.username}' (ID={db_user.id})")
    return _token_response(db_user, token, auth_session)
