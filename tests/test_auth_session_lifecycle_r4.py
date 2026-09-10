"""Password, role, account and lost-device session lifecycle."""

from conftest import SELLER_PASSWORD, auth, register_seller, seller_with_shop

from fselling import models
from fselling.core.database import SessionLocal
from fselling.services import offline_lease_service


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


def test_change_password_revokes_all_old_sessions_and_replaces_current_device(client):
    username = register_seller(client)
    first = _login_device(client, username, SELLER_PASSWORD, "password-a")
    current = _login_device(client, username, SELLER_PASSWORD, "password-b")

    changed = client.post(
        "/api/auth/change-password",
        json={"old_password": SELLER_PASSWORD, "new_password": "Moi@2026abc"},
        headers=auth(current["access_token"]),
    )
    assert changed.status_code == 200, changed.text
    replacement = changed.json()
    assert replacement["session"]["device_id"] == "password-b"
    assert replacement["session"]["session_id"] != current["session"]["session_id"]
    assert client.get(
        "/api/auth/session-check", headers=auth(first["access_token"])
    ).status_code == 401
    assert client.get(
        "/api/auth/session-check", headers=auth(current["access_token"])
    ).status_code == 401
    assert client.get(
        "/api/auth/session-check", headers=auth(replacement["access_token"])
    ).status_code == 200


def test_device_revoke_revokes_linked_online_sessions_and_offline_leases(
    client, monkeypatch
):
    monkeypatch.setattr(
        offline_lease_service.config, "OFFLINE_LEASE_ISSUANCE_ENABLED", True
    )
    ctx = seller_with_shop(client)
    device = _login_device(client, ctx["username"], SELLER_PASSWORD, "lost-device")
    lease = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "offline-profile"},
        headers=auth(device["access_token"]),
    )
    assert lease.status_code == 200, lease.text

    revoked = client.post(
        "/api/auth/devices/revoke",
        json={"device_id": "lost-device"},
        headers=auth(device["access_token"]),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"sessions_revoked": 1, "offline_leases_revoked": 1}
    assert client.get(
        "/api/auth/session-check", headers=auth(device["access_token"])
    ).status_code == 401
    with SessionLocal() as db:
        stored = db.get(models.OfflineLease, lease.json()["lease_id"])
        assert stored.issued_by_auth_session_id == device["session"]["session_id"]
        assert stored.revoked_at is not None
        assert stored.revoke_reason == "DEVICE_REVOKED"


def test_device_revoke_also_revokes_lease_from_replaced_session(client, monkeypatch):
    monkeypatch.setattr(
        offline_lease_service.config, "OFFLINE_LEASE_ISSUANCE_ENABLED", True
    )
    ctx = seller_with_shop(client)
    first = _login_device(client, ctx["username"], SELLER_PASSWORD, "shared-device")
    lease = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "offline-profile"},
        headers=auth(first["access_token"]),
    ).json()
    current = _login_device(client, ctx["username"], SELLER_PASSWORD, "shared-device")

    revoked = client.post(
        "/api/auth/devices/revoke",
        json={"device_id": "shared-device"},
        headers=auth(current["access_token"]),
    )

    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"sessions_revoked": 1, "offline_leases_revoked": 1}
    with SessionLocal() as db:
        assert db.get(models.OfflineLease, lease["lease_id"]).revoked_at is not None


def test_legacy_unlinked_offline_lease_is_not_guessed_by_device_revoke(
    client, monkeypatch
):
    monkeypatch.setattr(
        offline_lease_service.config, "OFFLINE_LEASE_ISSUANCE_ENABLED", True
    )
    ctx = seller_with_shop(client)
    device = _login_device(client, ctx["username"], SELLER_PASSWORD, "legacy-safe")
    lease = client.post(
        "/api/offline/leases",
        json={"shop_id": ctx["shop_id"], "device_id": "same-visible-id"},
        headers=auth(device["access_token"]),
    ).json()
    with SessionLocal() as db:
        stored = db.get(models.OfflineLease, lease["lease_id"])
        stored.issued_by_auth_session_id = None
        db.commit()

    response = client.post(
        "/api/auth/devices/revoke",
        json={"device_id": "legacy-safe"},
        headers=auth(device["access_token"]),
    )
    assert response.status_code == 200
    assert response.json()["offline_leases_revoked"] == 0
    with SessionLocal() as db:
        assert db.get(models.OfflineLease, lease["lease_id"]).revoked_at is None
