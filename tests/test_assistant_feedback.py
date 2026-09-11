"""Assistant Feedback R1: fixed metadata only, one rating per signed reply."""
import jwt
from pathlib import Path

from conftest import auth, new_staff, seller_with_shop

from fselling import models
from fselling.core.config import ALGORITHM, SECRET_KEY
from fselling.core.database import SessionLocal

ROOT = Path(__file__).resolve().parent.parent


def _ask(client, ctx, question="Hôm nay bán được bao nhiêu?"):
    response = client.post(
        f"/api/assistant/{ctx['shop_id']}",
        json={"cau_hoi": question},
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rate(client, ctx, token, rating="HELPFUL", reason=None):
    body = {"feedback_token": token, "rating": rating}
    if reason is not None:
        body["reason"] = reason
    return client.post(
        f"/api/assistant/{ctx['shop_id']}/feedback",
        json=body,
        headers=auth(ctx["token"]),
    )


def test_reply_issues_privacy_safe_signed_feedback_token(client):
    ctx = seller_with_shop(client)
    marker = "PII-0900000000-NGUYEN-VAN-A"
    reply = _ask(client, ctx, marker)

    token = reply["feedback_token"]
    claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

    assert claims["typ"] == "assistant_feedback"
    assert claims["shop_id"] == ctx["shop_id"]
    assert claims["intent"] == "UNKNOWN"
    assert claims["understood"] is False
    assert claims["used_ai"] is False
    assert marker not in str(claims)
    assert "question" not in claims
    assert "answer" not in claims


def test_helpful_feedback_is_idempotent_and_never_logs_content(client):
    ctx = seller_with_shop(client)
    marker = "Hôm nay bán được bao nhiêu PII-0988777666"
    reply = _ask(client, ctx, marker)

    first = _rate(client, ctx, reply["feedback_token"])
    second = _rate(client, ctx, reply["feedback_token"])

    assert first.status_code == 200, first.text
    assert first.json() == {"recorded": True}
    assert second.status_code == 200, second.text
    assert second.json() == {"recorded": False}

    session = SessionLocal()
    try:
        rows = session.query(models.SystemLog).filter(
            models.SystemLog.shop_id == ctx["shop_id"],
            models.SystemLog.action == "ASSISTANT_FEEDBACK",
        ).all()
        assert len(rows) == 1
        assert "rating=HELPFUL" in rows[0].details
        assert "reason=NONE" in rows[0].details
        assert marker not in rows[0].details
        assert reply["tra_loi"] not in rows[0].details
    finally:
        session.close()


def test_not_helpful_requires_one_fixed_reason(client):
    ctx = seller_with_shop(client)
    token = _ask(client, ctx)["feedback_token"]

    missing = _rate(client, ctx, token, "NOT_HELPFUL")
    invalid = _rate(client, ctx, token, "NOT_HELPFUL", "FREE TEXT")
    valid = _rate(client, ctx, token, "NOT_HELPFUL", "WRONG_TIME_RANGE")

    assert missing.status_code == 400
    assert invalid.status_code == 422
    assert valid.status_code == 200, valid.text
    assert valid.json()["recorded"] is True


def test_helpful_rejects_a_wrong_answer_reason(client):
    ctx = seller_with_shop(client)
    response = _rate(
        client,
        ctx,
        _ask(client, ctx)["feedback_token"],
        "HELPFUL",
        "WRONG_NUMBERS",
    )
    assert response.status_code == 400


def test_token_cannot_be_tampered_reused_by_another_user_or_shop(client):
    owner = seller_with_shop(client)
    other = seller_with_shop(client)
    token = _ask(client, owner)["feedback_token"]

    header, payload, signature = token.split(".")
    middle = len(signature) // 2
    changed = "a" if signature[middle] != "a" else "b"
    signature = signature[:middle] + changed + signature[middle + 1:]
    tampered = ".".join((header, payload, signature))
    assert _rate(client, owner, tampered).status_code == 400
    assert _rate(client, other, token).status_code == 400


def test_staff_can_rate_own_reply_but_cannot_read_owner_summary(client):
    owner = seller_with_shop(client)
    _, staff_token = new_staff(client, owner)
    staff = {"shop_id": owner["shop_id"], "token": staff_token}

    token = _ask(client, staff)["feedback_token"]
    assert _rate(client, staff, token).status_code == 200

    denied = client.get(
        f"/api/assistant/{owner['shop_id']}/feedback/summary",
        headers=auth(staff_token),
    )
    assert denied.status_code == 403


def test_owner_summary_aggregates_fixed_reasons_and_never_auto_enables_ai(client):
    owner = seller_with_shop(client)
    helpful = _ask(client, owner)["feedback_token"]
    wrong = _ask(client, owner, "Tháng này lãi bao nhiêu?")["feedback_token"]
    assert _rate(client, owner, helpful).status_code == 200
    assert _rate(client, owner, wrong, "NOT_HELPFUL", "WRONG_NUMBERS").status_code == 200

    response = client.get(
        f"/api/assistant/{owner['shop_id']}/feedback/summary",
        headers=auth(owner["token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["helpful"] == 1
    assert body["not_helpful"] == 1
    assert body["helpful_percent"] == 50.0
    assert body["reasons"]["WRONG_NUMBERS"] == 1
    assert body["gate"] == {
        "minimum_samples": 30,
        "target_helpful_percent": 95,
        "ready_for_review": False,
    }
    assert "provider_enabled" not in body


def test_feedback_requires_authentication(client):
    ctx = seller_with_shop(client)
    token = _ask(client, ctx)["feedback_token"]
    response = client.post(
        f"/api/assistant/{ctx['shop_id']}/feedback",
        json={"feedback_token": token, "rating": "HELPFUL"},
    )
    assert response.status_code == 401


def test_seller_ui_offers_fixed_feedback_without_sending_content():
    html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")

    assert 'id="assistantFeedbackSummary"' in html
    assert "ganDanhGiaTroLy" in js
    assert "HELPFUL" in js
    assert "NOT_HELPFUL" in js
    for reason in (
        "NOT_UNDERSTOOD",
        "WRONG_REPORT",
        "WRONG_TIME_RANGE",
        "WRONG_NUMBERS",
        "OTHER",
    ):
        assert reason in js
    submit_block = js[js.index("async function guiDanhGiaTroLy"):]
    submit_block = submit_block[:submit_block.index("\n}")]
    assert "feedback_token" in submit_block
    assert "cau_hoi" not in submit_block
    assert "tra_loi" not in submit_block


def test_feedback_ui_is_localized_and_cache_busted():
    html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    locale = (ROOT / "static/js/locales/seller.js").read_text(encoding="utf-8")

    keys = (
        "helpful",
        "not_helpful",
        "reason_not_understood",
        "reason_wrong_report",
        "reason_wrong_time_range",
        "reason_wrong_numbers",
        "reason_other",
        "feedback_thanks",
        "quality_title",
        "quality_ai_stays_off",
    )
    for key in keys:
        assert locale.count(f"'seller.assistant.{key}'") == 2
    assert "/js/locales/seller.js?v=20260911-r5-5" in html
    assert "/js/seller.js?v=20260911-r5-5" in html
