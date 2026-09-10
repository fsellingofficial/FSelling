"""Plan 4 multi-device authentication-session contracts."""

from datetime import datetime, timedelta

from conftest import SELLER_PASSWORD, auth, register_seller

from fselling import models
from fselling.core.database import SessionLocal
from fselling.core.security import create_access_token, new_session_id


def _login_device(client, username, device_id, name="Máy test", kind="DESKTOP"):
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": SELLER_PASSWORD,
            "device_id": device_id,
            "device_name": name,
            "device_type": kind,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_two_devices_for_one_user_remain_live(client):
    username = register_seller(client)
    device_a = _login_device(client, username, "device-a", "Quầy A")
    device_b = _login_device(client, username, "device-b", "Máy B", "TABLET")

    assert client.get(
        "/api/auth/session-check", headers=auth(device_a["access_token"])
    ).status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(device_b["access_token"])
    ).status_code == 200
    assert device_a["session"]["device_id"] == "device-a"
    assert device_b["session"]["device_type"] == "TABLET"


def test_relogin_same_device_revokes_only_previous_device_session(client):
    username = register_seller(client)
    first_a = _login_device(client, username, "same-a")
    device_b = _login_device(client, username, "other-b")
    second_a = _login_device(client, username, "same-a")

    revoked = client.get(
        "/api/auth/session-check", headers=auth(first_a["access_token"])
    )
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"
    assert client.get(
        "/api/auth/session-check", headers=auth(second_a["access_token"])
    ).status_code == 200
    assert client.get(
        "/api/auth/session-check", headers=auth(device_b["access_token"])
    ).status_code == 200


def test_signed_token_requires_matching_live_registry_row(client):
    username = register_seller(client)
    missing = create_access_token(username, new_session_id())
    response = client.get("/api/auth/session-check", headers=auth(missing))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_SESSION_INVALID"

    live = _login_device(client, username, "expire-me")
    with SessionLocal() as db:
        row = db.get(models.AuthSession, live["session"]["session_id"])
        row.created_at = datetime.utcnow() - timedelta(days=2)
        row.last_seen_at = row.created_at
        row.expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()
    response = client.get(
        "/api/auth/session-check", headers=auth(live["access_token"])
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_SESSION_EXPIRED"


def test_legacy_login_without_device_metadata_uses_unknown_device(client):
    username = register_seller(client)
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": SELLER_PASSWORD},
    )
    assert response.status_code == 200
    session = response.json()["session"]
    assert session["device_type"] == "UNKNOWN"
    assert session["device_id"].startswith("legacy-")
    assert client.get(
        "/api/auth/session-check", headers=auth(response.json()["access_token"])
    ).status_code == 200
