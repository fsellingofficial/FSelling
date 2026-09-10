"""Self-service and owner-scoped auth-session management."""

from datetime import datetime, timedelta

from conftest import STAFF_PASSWORD, auth, new_staff, seller_with_shop

from fselling import models
from fselling.core.database import SessionLocal


def _login_device(client, username, password, device_id):
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "device_id": device_id,
            "device_name": device_id,
            "device_type": "DESKTOP",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_self_list_rename_revoke_and_server_logout(client):
    ctx = seller_with_shop(client)
    first = _login_device(client, ctx["username"], "Seller@2026", "owner-first")
    second = _login_device(client, ctx["username"], "Seller@2026", "owner-second")
    older_seen = _login_device(client, ctx["username"], "Seller@2026", "owner-older-seen")
    expired = _login_device(client, ctx["username"], "Seller@2026", "owner-expired")
    with SessionLocal() as db:
        db.get(models.AuthSession, first["session"]["session_id"]).last_seen_at = datetime.utcnow()
        older_row = db.get(models.AuthSession, older_seen["session"]["session_id"])
        older_row.created_at = datetime.utcnow() - timedelta(hours=2)
        older_row.last_seen_at = datetime.utcnow() - timedelta(hours=1)
        expired_row = db.get(models.AuthSession, expired["session"]["session_id"])
        expired_row.created_at = datetime.utcnow() - timedelta(days=2)
        expired_row.last_seen_at = expired_row.created_at
        expired_row.expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()

    listed = client.get("/api/auth/sessions", headers=auth(second["access_token"]))
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["current"] is True
    listed_devices = [row["device_id"] for row in listed.json()]
    assert listed_devices[0] == "owner-second"
    assert listed_devices.index("owner-first") < listed_devices.index("owner-older-seen")
    assert "owner-expired" not in listed_devices
    assert {row["device_id"] for row in listed.json()} >= {
        "owner-first",
        "owner-second",
    }

    renamed = client.patch(
        f"/api/auth/sessions/{first['session']['session_id']}",
        json={"device_name": "Quầy chính"},
        headers=auth(second["access_token"]),
    )
    assert renamed.status_code == 200
    assert renamed.json()["device_name"] == "Quầy chính"

    revoked = client.delete(
        f"/api/auth/sessions/{first['session']['session_id']}",
        headers=auth(second["access_token"]),
    )
    assert revoked.status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(first["access_token"])
    ).status_code == 401

    repeated = client.delete(
        f"/api/auth/sessions/{first['session']['session_id']}",
        headers=auth(second["access_token"]),
    )
    assert repeated.status_code == 200
    with SessionLocal() as db:
        logs = db.query(models.SystemLog).filter(
            models.SystemLog.action == "AUTH_SESSION_REVOKE",
            models.SystemLog.user_id == db.query(models.User.id).filter_by(
                username=ctx["username"]
            ).scalar(),
        ).all()
        assert len(logs) == 1
        assert logs[0].auth_session_id == second["session"]["session_id"]

    logout = client.post("/api/auth/logout", headers=auth(second["access_token"]))
    assert logout.status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(second["access_token"])
    ).status_code == 401


def test_owner_can_manage_only_staff_sessions_in_own_shop(client):
    owner = seller_with_shop(client)
    staff_username, staff_token = new_staff(client, owner, "SERVICE")
    staff_login = _login_device(
        client, staff_username, STAFF_PASSWORD, "waiter-phone"
    )
    with SessionLocal() as db:
        staff_id = db.query(models.User.id).filter_by(username=staff_username).scalar()

    listed = client.get(
        f"/api/staff/member/{staff_id}/sessions", headers=auth(owner["token"])
    )
    assert listed.status_code == 200, listed.text
    assert any(row["device_id"] == "waiter-phone" for row in listed.json())

    revoked = client.delete(
        f"/api/staff/member/{staff_id}/sessions/{staff_login['session']['session_id']}",
        headers=auth(owner["token"]),
    )
    assert revoked.status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(staff_token)
    ).status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(staff_login["access_token"])
    ).status_code == 401

    foreign_owner = seller_with_shop(client)
    hidden = client.get(
        f"/api/staff/member/{staff_id}/sessions",
        headers=auth(foreign_owner["token"]),
    )
    assert hidden.status_code == 404
    assert "waiter-phone" not in hidden.text


def test_owner_can_revoke_a_staff_device_without_touching_other_staff_device(client):
    owner = seller_with_shop(client)
    staff_username, first_token = new_staff(client, owner, "SERVICE")
    lost = _login_device(client, staff_username, STAFF_PASSWORD, "staff-lost")
    kept = _login_device(client, staff_username, STAFF_PASSWORD, "staff-kept")
    with SessionLocal() as db:
        staff_id = db.query(models.User.id).filter_by(username=staff_username).scalar()

    response = client.post(
        f"/api/staff/member/{staff_id}/devices/revoke",
        json={"device_id": "staff-lost"},
        headers=auth(owner["token"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["sessions_revoked"] == 1
    assert client.get(
        "/api/auth/session-check", headers=auth(lost["access_token"])
    ).status_code == 401
    assert client.get(
        "/api/auth/session-check", headers=auth(kept["access_token"])
    ).status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(first_token)
    ).status_code == 200
