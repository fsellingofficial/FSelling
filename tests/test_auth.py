"""Đăng ký, xác minh, đăng nhập, single-session, đổi mật khẩu."""
import uuid

from conftest import (
    ADMIN_PASSWORD,
    SELLER_PASSWORD,
    auth,
    login,
    new_seller,
    register_seller,
)

from fselling import models
from fselling.core.database import SessionLocal


def _email(u):
    return f"{u}@example.com"


def test_register_tu_choi_mat_khau_yeu(client):
    res = client.post(
        "/api/auth/register",
        json={"username": "weakpw", "password": "abcdefgh", "email": "weak@example.com"},
    )
    assert res.status_code == 400
    assert "Mật khẩu" in res.json()["detail"]


def test_register_luon_tao_role_seller_du_client_gui_admin(client):
    username = f"tryadmin_{uuid.uuid4().hex[:6]}"
    res = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": SELLER_PASSWORD,
            "email": _email(username),
            "role": "ADMIN",  # client cố nâng quyền
        },
    )
    assert res.status_code == 200

    session = SessionLocal()
    try:
        user = session.query(models.User).filter(models.User.username == username).first()
        assert user.role == "SELLER"
        assert user.is_verified is False
    finally:
        session.close()


def test_register_trung_username_va_email(client):
    username = register_seller(client)
    res = client.post(
        "/api/auth/register",
        json={"username": username, "password": SELLER_PASSWORD, "email": "khac@example.com"},
    )
    assert res.status_code == 400

    res = client.post(
        "/api/auth/register",
        json={"username": "khachang", "password": SELLER_PASSWORD, "email": _email(username)},
    )
    assert res.status_code == 400


def test_login_that_bai_khi_chua_xac_minh(client):
    username = f"chuaxacminh_{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/auth/register",
        json={"username": username, "password": SELLER_PASSWORD, "email": _email(username)},
    )
    res = client.post("/api/auth/login", json={"username": username, "password": SELLER_PASSWORD})
    assert res.status_code == 400


def test_login_sai_mat_khau_tra_401(client):
    username = register_seller(client)
    res = client.post("/api/auth/login", json={"username": username, "password": "Sai@12345"})
    assert res.status_code == 401


def test_login_tra_dung_contract(client):
    username = register_seller(client)
    res = client.post("/api/auth/login", json={"username": username, "password": SELLER_PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"access_token", "token_type", "role", "session"}
    assert body["token_type"] == "bearer"
    assert body["role"] == "SELLER"


def test_admin_dang_nhap_duoc_va_co_role_admin(client):
    res = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "ADMIN"


def test_khong_co_token_thi_401(client):
    assert client.get("/api/auth/session-check").status_code == 401
    assert client.get("/api/shops").status_code == 401


def test_token_qua_query_string_khong_duoc_chap_nhan(client):
    _, token = new_seller(client)
    res = client.get(f"/api/auth/session-check?token={token}")
    assert res.status_code == 401


def test_login_legacy_khong_vo_hieu_phien_thiet_bi_khac(client):
    username, token_cu = new_seller(client)
    assert client.get("/api/auth/session-check", headers=auth(token_cu)).status_code == 200

    token_moi = login(client, username)
    assert client.get("/api/auth/session-check", headers=auth(token_moi)).status_code == 200
    assert client.get("/api/auth/session-check", headers=auth(token_cu)).status_code == 200


def test_doi_mat_khau_vo_hieu_token_cu_va_tra_token_moi(client):
    username, token = new_seller(client)
    res = client.post(
        "/api/auth/change-password",
        json={"old_password": SELLER_PASSWORD, "new_password": "Moi@2026abc"},
        headers=auth(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"access_token", "token_type", "role", "session"}

    # token cũ mất hiệu lực, token mới dùng được
    assert client.get("/api/auth/session-check", headers=auth(token)).status_code == 401
    assert (
        client.get("/api/auth/session-check", headers=auth(body["access_token"])).status_code
        == 200
    )
    # mật khẩu mới có tác dụng
    assert login(client, username, "Moi@2026abc")


def test_doi_mat_khau_sai_mat_khau_cu(client):
    _, token = new_seller(client)
    res = client.post(
        "/api/auth/change-password",
        json={"old_password": "Sai@12345", "new_password": "Moi@2026abc"},
        headers=auth(token),
    )
    assert res.status_code == 400


def test_verify_email_bang_ma_sai(client, _no_real_email):
    username = f"verify_{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/auth/register",
        json={"username": username, "password": SELLER_PASSWORD, "email": _email(username)},
    )
    res = client.post(
        "/api/auth/verify-email", json={"email": _email(username), "code": "000000"}
    )
    assert res.status_code == 400


def test_verify_email_bang_ma_dung(client, _no_real_email):
    username = f"verify2_{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/auth/register",
        json={"username": username, "password": SELLER_PASSWORD, "email": _email(username)},
    )
    otp = _no_real_email[-1]["code"]
    res = client.post("/api/auth/verify-email", json={"email": _email(username), "code": otp})
    assert res.status_code == 200
    assert login(client, username)


def test_forgot_password_reset_dat_lai_mat_khau(client, _no_real_email):
    username = register_seller(client)
    res = client.post("/api/auth/forgot-password-request", json={"email": _email(username)})
    assert res.status_code == 200
    otp = _no_real_email[-1]["code"]

    res = client.post(
        "/api/auth/forgot-password-reset",
        json={"email": _email(username), "code": otp, "new_password": "Quen@2026abc"},
    )
    assert res.status_code == 200
    assert login(client, username, "Quen@2026abc")
