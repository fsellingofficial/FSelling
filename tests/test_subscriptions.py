"""Core gói Free/Pro: trial, checkout, tiền, quà ADMIN và migration."""
import datetime
import re
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from conftest import auth
from fselling import models
import legacy_bootstrap_support as bootstrap
from fselling.core.i18n import using_locale
from fselling.core.security import create_access_token, new_session_id
from fselling.core.translations_en import EN_MESSAGES
from fselling.schemas.shop import ShopCreate
from fselling.schemas.subscription import (
    SubscriptionCheckoutCreate,
    SubscriptionGiftCreate,
    SubscriptionGiftRevoke,
)
from fselling.services import log_service, shop_service, subscription_service


def _user_and_shop(db, *, with_subscription=True, now=None):
    suffix = uuid.uuid4().hex[:10]
    user = models.User(
        username=f"sub_owner_{suffix}",
        hashed_password="not-used",
        role="SELLER",
        email=f"sub_{suffix}@example.com",
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    shop = models.Shop(
        name=f"Shop sub {suffix}",
        business_address="1 Test",
        tax_code=suffix,
        phone="0900000000",
        email=f"shop_{suffix}@example.com",
        bank_account_no="111222333",
        bank_account_name="SHOP OWNER",
        bank_code="VCB",
        owner_id=user.id,
        is_active=True,
    )
    db.add(shop)
    db.flush()
    if with_subscription:
        subscription_service.create_trial_for_shop(db, shop.id, now=now)
    db.commit()
    db.refresh(user)
    db.refresh(shop)
    return user, shop


def _token_for(db, user):
    sid = new_session_id()
    now = datetime.datetime.utcnow()
    db.add(models.AuthSession(
        session_id=sid,
        user_id=user.id,
        device_id=f"test-{sid}",
        device_name="Subscription test",
        device_type="UNKNOWN",
        created_at=now,
        last_seen_at=now,
        expires_at=now + datetime.timedelta(days=1),
    ))
    db.commit()
    return create_access_token(user.username, sid)


def _bank_env(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_BANK_CODE", "MB")
    monkeypatch.setenv("SUBSCRIPTION_BANK_ACCOUNT_NO", "00123456789")
    monkeypatch.setenv("SUBSCRIPTION_BANK_ACCOUNT_NAME", "F SELLING")


def _transaction(reference, amount, txn_id):
    return SimpleNamespace(
        reference_code=reference,
        amount=float(amount),
        direction="in",
        txn_id=txn_id,
        account_no="00123456789",
        provider="sepay",
        payload_fingerprint=(txn_id.lower().replace("-", "") + "0" * 64)[:64],
    )


def _buy_pro(db, owner, shop, *, now, operation_id, txn_id, cycle="MONTHLY"):
    checkout_data = subscription_service.create_checkout(
        db,
        owner,
        shop.id,
        SubscriptionCheckoutCreate(cycle=cycle, operation_id=operation_id),
        now=now,
    )
    subscription_service.apply_subscription_transactions(
        db,
        [
            _transaction(
                checkout_data["reference_code"],
                subscription_service.PRICE_VND[cycle],
                txn_id,
            )
        ],
        now=now,
    )
    db.expire_all()
    return (
        db.query(models.SubscriptionCheckout)
        .filter_by(id=checkout_data["id"])
        .one()
    )


def test_tao_shop_tao_trial_cung_luc(db):
    suffix = uuid.uuid4().hex[:8]
    owner = models.User(
        username=f"trial_owner_{suffix}",
        hashed_password="not-used",
        role="SELLER",
        email=f"trial_{suffix}@example.com",
        is_verified=True,
        is_active=True,
    )
    db.add(owner)
    db.commit()
    before = datetime.datetime.utcnow()
    shop = shop_service.create_shop(
        db,
        owner,
        ShopCreate(
            name=f"Trial {suffix}",
            business_address="1 Test",
            tax_code=suffix,
            phone="0900000000",
            email=f"trial-shop-{suffix}@example.com",
            bank_account_no="123456789",
            bank_account_name="OWNER",
            bank_code="VCB",
        ),
    )
    after = datetime.datetime.utcnow()
    row = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    assert before <= row.trial_started_at <= after
    assert subscription_service.TRIAL_DAYS == 14
    assert row.trial_ends_at - row.trial_started_at == datetime.timedelta(days=14)


def test_backfill_shop_cu_chi_cap_trial_mot_lan(db):
    _owner, shop = _user_and_shop(db, with_subscription=False)
    assert db.query(models.ShopSubscription).filter_by(shop_id=shop.id).first() is None
    assert bootstrap.backfill_shop_subscriptions(db) >= 1
    first_end = (
        db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().trial_ends_at
    )
    assert bootstrap.backfill_shop_subscriptions(db) == 0
    assert (
        db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().trial_ends_at
        == first_end
    )


def test_backfill_checkout_paid_cu_khoi_phuc_segment_tu_snapshot(db):
    now = datetime.datetime(2026, 8, 7, 3, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    old_end = now + datetime.timedelta(days=60)
    checkout = models.SubscriptionCheckout(
        shop_id=shop.id,
        reference_code="SUBLEGACY000001",
        cycle="MONTHLY",
        amount_due_vnd=99_000,
        duration_days=30,
        status="PAID",
        received_amount_vnd=99_000,
        refund_due_amount_vnd=0,
        operation_id="legacy-paid-segment-operation-0001",
        operation_fingerprint="d" * 64,
        created_by_user_id=owner.id,
        created_at=now,
        expires_at=now + datetime.timedelta(hours=24),
        activated_at=now,
        paid_until_after=old_end,
    )
    db.add(checkout)
    db.commit()

    assert bootstrap.backfill_subscription_entitlements(db) == 1
    db.refresh(checkout)
    assert checkout.entitlement_starts_at == old_end - datetime.timedelta(days=30)
    assert checkout.entitlement_ends_at == old_end
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    assert subscription.paid_until == old_end
    assert bootstrap.backfill_subscription_entitlements(db) == 0


def test_backfill_trial_loi_sql_thi_rollback_va_fail_fast():
    fake_db = Mock()
    fake_db.execute.side_effect = SQLAlchemyError("DB test bị lỗi")

    with pytest.raises(RuntimeError, match="dừng khởi động") as exc:
        bootstrap.backfill_shop_subscriptions(fake_db)

    assert isinstance(exc.value.__cause__, SQLAlchemyError)
    fake_db.rollback.assert_called_once_with()
    fake_db.commit.assert_not_called()


def test_application_bootstrap_chi_seed_sau_khi_schema_da_verify(monkeypatch):
    from fselling.core import bootstrap as application_bootstrap

    fake_db = Mock()
    seed_admin = Mock()

    monkeypatch.setattr(application_bootstrap, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(application_bootstrap, "seed_admin", seed_admin)

    application_bootstrap.initialize_application_data()

    seed_admin.assert_called_once_with(fake_db)
    fake_db.close.assert_called_once_with()


def test_thieu_aggregate_khong_cap_trial_luc_mo_tab_hay_thanh_toan(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 4, 0, 0)
    owner, shop = _user_and_shop(db, with_subscription=False)
    checkout_data = subscription_service.create_checkout(
        db,
        owner,
        shop.id,
        SubscriptionCheckoutCreate(
            cycle="MONTHLY", operation_id="missing-aggregate-checkout-0001"
        ),
        now=now,
    )
    aggregate = (
        db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    )
    # Checkout được phép tự chữa aggregate để lấy lock, nhưng tuyệt đối không
    # biến lần mở tab thành một trial mới.
    assert aggregate.trial_started_at == now
    assert aggregate.trial_ends_at == now

    subscription_service.apply_subscription_transactions(
        db,
        [_transaction(checkout_data["reference_code"], 99_000, "TX-NO-LATE-TRIAL")],
        now=now,
    )
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    assert subscription.trial_started_at == now
    assert subscription.trial_ends_at == now
    assert subscription.paid_until == now + datetime.timedelta(days=30)


def test_trial_gift_khong_grace_nhung_paid_co_grace(db):
    now = datetime.datetime(2026, 8, 7, 5, 0, 0)
    _owner, shop = _user_and_shop(
        db,
        now=now - datetime.timedelta(days=subscription_service.TRIAL_DAYS),
    )
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()

    assert subscription_service.get_subscription_state(
        db, shop.id, now=now - datetime.timedelta(microseconds=1)
    )["phase"] == "TRIAL"
    # Trial hết đúng mốc là Free, không có grace.
    assert subscription_service.get_subscription_state(db, shop.id, now=now)["phase"] == "FREE"

    subscription.paid_until = now + datetime.timedelta(days=2)
    db.commit()
    assert subscription_service.get_subscription_state(
        db, shop.id, now=now + datetime.timedelta(days=3)
    )["phase"] == "GRACE"
    assert subscription_service.get_subscription_state(
        db, shop.id, now=now + datetime.timedelta(days=9)
    )["phase"] == "FREE"
    with pytest.raises(HTTPException) as exc:
        subscription_service.require_pro(
            db, shop.id, now=now + datetime.timedelta(days=9)
        )
    assert exc.value.status_code == 402


def test_checkout_chup_gia_24h_idempotent_va_contract_ui(db, monkeypatch):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 6, 0, 0)
    owner, shop = _user_and_shop(db, now=now)
    payload = SubscriptionCheckoutCreate(
        cycle="MONTHLY", operation_id="checkout-op-0001"
    )
    result = subscription_service.create_checkout(
        db, owner, shop.id, payload, now=now
    )
    assert result["amount_due_vnd"] == 99_000
    assert result["duration_days"] == 30
    assert result["expires_at"] - result["created_at"] == datetime.timedelta(hours=24)
    assert re.fullmatch(r"SUB[0-9A-F]{12}", result["reference_code"])
    assert result["reference_code"] in result["qr_url"]
    assert "amount=99000" in result["qr_url"]
    assert result["received_vnd"] == 0
    assert result["refund_due_vnd"] == 0
    assert result["remaining_vnd"] == 99_000

    retry = subscription_service.create_checkout(db, owner, shop.id, payload, now=now)
    assert retry["id"] == result["id"]
    assert db.query(models.SubscriptionCheckout).filter_by(
        operation_id="checkout-op-0001"
    ).count() == 1
    with pytest.raises(HTTPException) as open_exc:
        subscription_service.create_checkout(
            db,
            owner,
            shop.id,
            SubscriptionCheckoutCreate(
                cycle="MONTHLY", operation_id="checkout-op-other-0001"
            ),
            now=now,
        )
    assert open_exc.value.status_code == 409
    assert "một mã thanh toán" in open_exc.value.detail
    with using_locale("en"), pytest.raises(HTTPException) as english_exc:
        subscription_service.create_checkout(
            db,
            owner,
            shop.id,
            SubscriptionCheckoutCreate(
                cycle="MONTHLY", operation_id="checkout-op-english-0001"
            ),
            now=now,
        )
    assert "active payment code" in english_exc.value.detail
    assert (
        EN_MESSAGES[
            "Cửa hàng vừa tạo một mã thanh toán ở phiên khác. "
            "Hãy tải lại để dùng đúng mã đó."
        ]
        != ""
    )
    with pytest.raises(HTTPException) as exc:
        subscription_service.create_checkout(
            db,
            owner,
            shop.id,
            SubscriptionCheckoutCreate(
                cycle="YEARLY", operation_id="checkout-op-0001"
            ),
            now=now,
        )
    assert exc.value.status_code == 409

    overview = subscription_service.subscription_overview(
        db, shop.id, owner, now=now
    )
    assert overview["status"] == "TRIAL"
    assert overview["prices"] == {
        "monthly_vnd": 99_000,
        "yearly_vnd": 831_600,
    }
    assert overview["current_checkout"]["id"] == result["id"]
    assert overview["current_checkout"]["operation_id"] == "checkout-op-0001"
    assert "qr_url" in overview["current_checkout"]

    expired_overview = subscription_service.subscription_overview(
        db, shop.id, owner, now=now + datetime.timedelta(hours=24)
    )
    assert expired_overview["current_checkout"]["status"] == "EXPIRED"
    assert "qr_url" not in expired_overview["current_checkout"]

    replacement = subscription_service.create_checkout(
        db,
        owner,
        shop.id,
        SubscriptionCheckoutCreate(
            cycle="YEARLY", operation_id="checkout-after-expiry-0001"
        ),
        now=now + datetime.timedelta(hours=24),
    )
    assert replacement["id"] != result["id"]
    db.expire_all()
    assert (
        db.query(models.SubscriptionCheckout).filter_by(id=result["id"]).one().status
        == "EXPIRED"
    )
    assert (
        db.query(models.SubscriptionCheckout)
        .filter(
            models.SubscriptionCheckout.shop_id == shop.id,
            models.SubscriptionCheckout.status.in_({"PENDING", "UNDERPAID"}),
        )
        .count()
        == 1
    )


def test_unique_db_chan_hai_qr_mo_cung_shop_ke_ca_bo_qua_service(db):
    now = datetime.datetime(2026, 8, 7, 6, 30, 0)
    owner, shop = _user_and_shop(db, now=now)
    common = {
        "shop_id": shop.id,
        "cycle": "MONTHLY",
        "amount_due_vnd": 99_000,
        "duration_days": 30,
        "status": "PENDING",
        "received_amount_vnd": 0,
        "refund_due_amount_vnd": 0,
        "operation_fingerprint": "c" * 64,
        "created_by_user_id": owner.id,
        "created_at": now,
        "expires_at": now + datetime.timedelta(hours=24),
    }
    db.add(
        models.SubscriptionCheckout(
            **common,
            reference_code="SUB000000000001",
            operation_id="db-open-checkout-operation-0001",
        )
    )
    db.commit()
    db.add(
        models.SubscriptionCheckout(
            **common,
            reference_code="SUB000000000002",
            operation_id="db-open-checkout-operation-0002",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_tien_thieu_cong_don_du_moi_cap_mot_ky_va_retry_khong_cap_lai(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 7, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    checkout = models.SubscriptionCheckout(
        shop_id=shop.id,
        reference_code="SUBABCDEF123456",
        cycle="MONTHLY",
        amount_due_vnd=99_000,
        duration_days=30,
        status="PENDING",
        received_amount_vnd=0,
        refund_due_amount_vnd=0,
        operation_id="payment-checkout-0001",
        operation_fingerprint="a" * 64,
        created_by_user_id=owner.id,
        created_at=now,
        expires_at=now + datetime.timedelta(hours=24),
    )
    db.add(checkout)
    db.commit()
    db.refresh(checkout)

    first = subscription_service.apply_subscription_transactions(
        db, [_transaction(checkout.reference_code, 40_000, "TX-SUB-1")], now=now
    )
    assert first["underpaid_checkout_ids"] == [checkout.id]
    db.refresh(checkout)
    assert checkout.status == "UNDERPAID"
    assert checkout.received_amount_vnd == 40_000
    assert db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().paid_until is None
    underpaid_overview = subscription_service.subscription_overview(
        db, shop.id, owner, now=now
    )
    assert underpaid_overview["current_checkout"]["remaining_vnd"] == 59_000
    assert "amount=59000" in underpaid_overview["current_checkout"]["qr_url"]

    second_tx = _transaction(checkout.reference_code, 59_000, "TX-SUB-2")
    second = subscription_service.apply_subscription_transactions(
        db, [second_tx], now=now
    )
    assert second["activated_shop_ids"] == [shop.id]
    db.expire_all()
    saved = db.query(models.SubscriptionCheckout).filter_by(id=checkout.id).one()
    paid_until = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().paid_until
    assert saved.status == "PAID"
    assert paid_until == now + datetime.timedelta(days=30)
    assert db.query(models.SubscriptionPayment).filter_by(checkout_id=checkout.id).count() == 2
    paid_overview = subscription_service.subscription_overview(
        db, shop.id, owner, now=now
    )
    assert "qr_url" not in paid_overview["current_checkout"]

    retry = subscription_service.apply_subscription_transactions(db, [second_tx], now=now)
    assert retry["duplicate_payment_ids"]
    db.expire_all()
    assert db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().paid_until == paid_until
    assert db.query(models.SubscriptionPayment).filter_by(checkout_id=checkout.id).count() == 2


def test_mua_som_noi_tu_cuoi_trial_dang_con(db, monkeypatch):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 7, 30, 0)
    owner, shop = _user_and_shop(db, now=now)
    checkout = subscription_service.create_checkout(
        db,
        owner,
        shop.id,
        SubscriptionCheckoutCreate(
            cycle="MONTHLY", operation_id="early-renewal-checkout-0001"
        ),
        now=now,
    )
    subscription_service.apply_subscription_transactions(
        db,
        [_transaction(checkout["reference_code"], 99_000, "TX-EARLY-RENEWAL")],
        now=now,
    )
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    expected_trial_end = now + datetime.timedelta(days=subscription_service.TRIAL_DAYS)
    assert subscription.trial_ends_at == expected_trial_end
    assert subscription.paid_until == expected_trial_end + datetime.timedelta(
        days=subscription_service.DURATION_DAYS[subscription_service.CYCLE_MONTHLY]
    )


def test_review_tien_du_gom_mot_issue_checkout_va_tra_dung_so_can_hoan(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 7, 45, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    checkout_data = subscription_service.create_checkout(
        db,
        owner,
        shop.id,
        SubscriptionCheckoutCreate(
            cycle="MONTHLY", operation_id="overpaid-review-checkout-0001"
        ),
        now=now,
    )
    reference = checkout_data["reference_code"]
    subscription_service.apply_subscription_transactions(
        db,
        [_transaction(reference, 60_000, "TX-OVERPAID-PART-1")],
        now=now,
    )
    subscription_service.apply_subscription_transactions(
        db,
        [_transaction(reference, 50_000, "TX-OVERPAID-PART-2")],
        now=now,
    )
    issues = subscription_service.list_subscription_payments(
        db, needs_review=True
    )
    checkout_issues = [item for item in issues if item["checkout_id"] == checkout_data["id"]]
    assert len(checkout_issues) == 1
    issue = checkout_issues[0]
    assert issue["review_group"] == f"checkout:{checkout_data['id']}:OVERPAID"
    assert issue["is_aggregate_issue"] is True
    assert issue["latest_payment_amount_vnd"] == 50_000
    assert issue["amount_vnd"] == 110_000
    assert issue["checkout_received_vnd"] == 110_000
    assert issue["checkout_amount_due_vnd"] == 99_000
    assert issue["checkout_refund_due_vnd"] == 11_000

    # Thêm một lần chuyển nhầm nữa vẫn chỉ là MỘT issue, số hoàn là tổng mới.
    subscription_service.apply_subscription_transactions(
        db,
        [_transaction(reference, 1_000, "TX-OVERPAID-PART-3")],
        now=now,
    )
    issues = subscription_service.list_subscription_payments(
        db, needs_review=True
    )
    checkout_issues = [item for item in issues if item["checkout_id"] == checkout_data["id"]]
    assert len(checkout_issues) == 1
    issue = checkout_issues[0]
    assert issue["latest_payment_amount_vnd"] == 1_000
    assert issue["checkout_received_vnd"] == 111_000
    assert issue["checkout_refund_due_vnd"] == 12_000
    admin_row = next(
        item
        for item in subscription_service.admin_subscription_list(db, now=now)
        if item["shop_id"] == shop.id
    )
    assert admin_row["payments_needing_review"] == 1
    assert (
        db.query(models.SubscriptionPayment)
        .filter_by(
            checkout_id=checkout_data["id"],
            needs_review=True,
            review_reason="OVERPAID",
        )
        .count()
        == 2
    )


def test_tien_du_chi_cap_mot_ky_va_noi_tu_han_cu_trong_grace(db, monkeypatch):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 8, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    subscription.paid_until = now - datetime.timedelta(days=2)
    checkout = models.SubscriptionCheckout(
        shop_id=shop.id,
        reference_code="SUB123456ABCDEF",
        cycle="YEARLY",
        amount_due_vnd=831_600,
        duration_days=365,
        status="PENDING",
        received_amount_vnd=0,
        refund_due_amount_vnd=0,
        operation_id="payment-checkout-0002",
        operation_fingerprint="b" * 64,
        created_by_user_id=owner.id,
        created_at=now,
        expires_at=now + datetime.timedelta(hours=24),
    )
    db.add(checkout)
    db.commit()
    old_until = subscription.paid_until

    result = subscription_service.apply_subscription_transactions(
        db,
        [_transaction(checkout.reference_code, 831_700, "TX-SUB-OVER")],
        now=now,
    )
    assert result["activated_shop_ids"] == [shop.id]
    assert result["review_payment_ids"]
    db.expire_all()
    checkout = db.query(models.SubscriptionCheckout).filter_by(id=checkout.id).one()
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    assert checkout.status == "OVERPAID"
    assert checkout.refund_due_amount_vnd == 100
    assert subscription.paid_until == old_until + datetime.timedelta(days=365)

    extra = subscription_service.apply_subscription_transactions(
        db,
        [_transaction(checkout.reference_code, 831_600, "TX-SUB-EXTRA")],
        now=now,
    )
    assert extra["activated_shop_ids"] == []
    db.expire_all()
    assert db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one().paid_until == subscription.paid_until


def test_admin_tang_den_het_ngay_viet_nam_va_chi_thu_hoi_qua_tang(db):
    now = datetime.datetime(2026, 8, 7, 9, 0, 0)
    _owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    admin = models.User(
        username=f"sub_admin_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    gift_data = SubscriptionGiftCreate(
        expires_on=datetime.date(2026, 8, 31),
        reason="Tặng khách thử nghiệm",
        operation_id="gift-operation-0001",
    )
    result = subscription_service.create_admin_gift(
        db, admin, shop.id, gift_data, now=now
    )
    grant_id = result["grant"]["id"]
    # Hết 31/08 theo giờ Việt Nam = 17:00 UTC ngày 31/08.
    assert result["grant"]["ends_at"] == datetime.datetime(2026, 8, 31, 17, 0, 0)
    assert subscription_service.get_subscription_state(
        db, shop.id, now=datetime.datetime(2026, 8, 31, 16, 59, 59)
    )["phase"] == "GIFT"
    assert subscription_service.get_subscription_state(
        db, shop.id, now=datetime.datetime(2026, 8, 31, 16, 59, 59)
    )["active_grant_expires_on"] == datetime.date(2026, 8, 31)
    # Gift hết đúng mốc là Free, không có grace.
    assert subscription_service.get_subscription_state(
        db, shop.id, now=datetime.datetime(2026, 8, 31, 17, 0, 0)
    )["phase"] == "FREE"

    rows = subscription_service.admin_subscription_list(db, now=now)
    row = next(item for item in rows if item["shop_id"] == shop.id)
    assert row["active_grant_id"] == grant_id
    assert row["status"] == "GIFT"
    assert row["active_grant_expires_on"] == datetime.date(2026, 8, 31)

    revoked = subscription_service.revoke_admin_gift(
        db,
        admin,
        grant_id,
        SubscriptionGiftRevoke(
            reason="Kết thúc đợt thử",
            operation_id="revoke-operation-0001",
        ),
        now=now + datetime.timedelta(hours=1),
    )
    assert revoked["grant"]["revoked_at"] is not None
    assert revoked["subscription"]["phase"] == "FREE"
    assert db.query(models.SubscriptionPayment).filter_by(shop_id=shop.id).count() == 0


def test_admin_tang_ngay_vuot_gioi_han_tra_400_thay_vi_loi_500(db):
    now = datetime.datetime(2026, 8, 7, 9, 0, 0)
    _owner, shop = _user_and_shop(db, now=now)
    admin = models.User(
        username=f"sub_admin_max_date_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        subscription_service.create_admin_gift(
            db,
            admin,
            shop.id,
            SubscriptionGiftCreate(
                expires_on=datetime.date.max,
                reason="Kiểm tra ngày ngoài giới hạn",
                operation_id="gift-max-date-0001",
            ),
            now=now,
        )
    assert exc.value.status_code == 400


def test_admin_retry_qua_tang_cu_sau_ngay_het_han_van_idempotent(db):
    created_at = datetime.datetime(2026, 8, 7, 9, 0, 0)
    _owner, shop = _user_and_shop(db, now=created_at)
    admin = models.User(
        username=f"sub_admin_gift_retry_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    data = SubscriptionGiftCreate(
        expires_on=datetime.date(2026, 8, 31),
        reason="Kiểm tra retry sau khi hết hạn",
        operation_id="gift-expired-retry-operation-0001",
    )

    first = subscription_service.create_admin_gift(
        db, admin, shop.id, data, now=created_at
    )
    retried = subscription_service.create_admin_gift(
        db,
        admin,
        shop.id,
        data,
        now=datetime.datetime(2026, 9, 1, 0, 0, 0),
    )

    assert retried["grant"]["id"] == first["grant"]["id"]
    assert db.query(models.SubscriptionGrant).filter_by(shop_id=shop.id).count() == 1


def test_admin_tang_pro_hien_dung_ai_lam_gi_cua_shop_khong_lo_sang_shop_khac(db):
    now = datetime.datetime(2026, 8, 7, 9, 0, 0)
    _owner_a, shop_a = _user_and_shop(db, now=now)
    _owner_b, shop_b = _user_and_shop(db, now=now)
    admin = models.User(
        username=f"sub_admin_log_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()

    subscription_service.create_admin_gift(
        db,
        admin,
        shop_a.id,
        SubscriptionGiftCreate(
            expires_on=datetime.date(2026, 8, 31),
            reason="Tặng để kiểm nhật ký đúng shop",
            operation_id="gift-log-operation-0001",
        ),
        now=now,
    )

    log_row = (
        db.query(models.SystemLog)
        .filter(models.SystemLog.action == "SUBSCRIPTION_ADMIN_GIFT")
        .order_by(models.SystemLog.id.desc())
        .first()
    )
    assert log_row.shop_id == shop_a.id
    actions_a = {row["action"] for row in log_service.nhat_ky_cua_shop(db, shop_a.id)["logs"]}
    actions_b = {row["action"] for row in log_service.nhat_ky_cua_shop(db, shop_b.id)["logs"]}
    assert "SUBSCRIPTION_ADMIN_GIFT" in actions_a
    assert "SUBSCRIPTION_ADMIN_GIFT" not in actions_b


def test_thu_hoi_qua_admin_khong_cat_ngay_khach_da_tra(db):
    now = datetime.datetime(2026, 8, 7, 9, 30, 0)
    _owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    subscription = db.query(models.ShopSubscription).filter_by(shop_id=shop.id).one()
    paid_until = now + datetime.timedelta(days=20)
    subscription.paid_until = paid_until
    admin = models.User(
        username=f"paid_gift_admin_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()

    gift = subscription_service.create_admin_gift(
        db,
        admin,
        shop.id,
        SubscriptionGiftCreate(
            expires_on=datetime.date(2026, 9, 30),
            reason="Tặng thêm để kiểm tra",
            operation_id="gift-over-paid-operation-0001",
        ),
        now=now,
    )
    revoked = subscription_service.revoke_admin_gift(
        db,
        admin,
        gift["grant"]["id"],
        SubscriptionGiftRevoke(
            reason="Chỉ thu hồi phần quà",
            operation_id="revoke-over-paid-operation-0001",
        ),
        now=now + datetime.timedelta(hours=1),
    )
    db.refresh(subscription)
    assert subscription.paid_until == paid_until
    assert revoked["subscription"]["phase"] == "PAID"


def test_gift_mua_som_thu_hoi_keo_ky_paid_ve_som_nhung_du_30_ngay(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 10, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    admin = models.User(
        username=f"gift_rebase_admin_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    gift = subscription_service.create_admin_gift(
        db,
        admin,
        shop.id,
        SubscriptionGiftCreate(
            expires_on=datetime.date(2026, 8, 31),
            reason="Tặng trước khi khách mua",
            operation_id="gift-before-buy-operation-0001",
        ),
        now=now,
    )
    paid = _buy_pro(
        db,
        owner,
        shop,
        now=now,
        operation_id="gift-before-buy-checkout-0001",
        txn_id="TX-GIFT-BEFORE-BUY-1",
    )
    gift_end = datetime.datetime(2026, 8, 31, 17, 0, 0)
    assert paid.entitlement_starts_at == gift_end
    assert paid.entitlement_ends_at - paid.entitlement_starts_at == datetime.timedelta(
        days=30
    )
    before_revoke = subscription_service.get_subscription_state(db, shop.id, now=now)
    assert before_revoke["current_access_source"] == "GIFT"
    assert before_revoke["access_source"] == "PAID"
    assert before_revoke["phase"] == "PAID"
    assert before_revoke["access_until"] == paid.entitlement_ends_at

    revoke_moment = now + datetime.timedelta(days=1)
    revoke_payload = SubscriptionGiftRevoke(
        reason="Thu hồi đúng phần được tặng",
        operation_id="revoke-gift-before-buy-0001",
    )
    subscription_service.revoke_admin_gift(
        db,
        admin,
        gift["grant"]["id"],
        revoke_payload,
        now=revoke_moment,
    )
    db.expire_all()
    moved = db.query(models.SubscriptionCheckout).filter_by(id=paid.id).one()
    assert moved.entitlement_starts_at == revoke_moment
    assert moved.entitlement_ends_at == revoke_moment + datetime.timedelta(days=30)
    assert moved.paid_until_after == moved.entitlement_ends_at
    state = subscription_service.get_subscription_state(db, shop.id, now=revoke_moment)
    assert state["phase"] == "PAID"
    assert state["access_source"] == "PAID"
    assert state["active_grant_until"] is None
    assert state["paid_until"] == moved.entitlement_ends_at

    # Retry thu hồi không được kéo segment lần thứ hai.
    subscription_service.revoke_admin_gift(
        db,
        admin,
        gift["grant"]["id"],
        revoke_payload,
        now=revoke_moment + datetime.timedelta(days=1),
    )
    db.expire_all()
    retried = db.query(models.SubscriptionCheckout).filter_by(id=paid.id).one()
    assert retried.entitlement_starts_at == revoke_moment
    assert retried.entitlement_ends_at == revoke_moment + datetime.timedelta(days=30)


def test_paid_gift_nhieu_ky_thu_hoi_giu_du_tung_ky_va_chuoi_lien_tuc(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 11, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=40))
    admin = models.User(
        username=f"multi_rebase_admin_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    first = _buy_pro(
        db,
        owner,
        shop,
        now=now,
        operation_id="multi-paid-checkout-first-0001",
        txn_id="TX-MULTI-PAID-1",
    )
    first_start = first.entitlement_starts_at
    first_end = first.entitlement_ends_at

    gift_moment = now + datetime.timedelta(days=1)
    gift = subscription_service.create_admin_gift(
        db,
        admin,
        shop.id,
        SubscriptionGiftCreate(
            expires_on=datetime.date(2026, 9, 30),
            reason="Tặng chồng lên kỳ đã trả",
            operation_id="multi-paid-gift-operation-0001",
        ),
        now=gift_moment,
    )
    gift_state = subscription_service.get_subscription_state(
        db, shop.id, now=gift_moment
    )
    assert gift_state["phase"] == "GIFT"
    assert gift_state["access_source"] == "GIFT"

    second = _buy_pro(
        db,
        owner,
        shop,
        now=gift_moment + datetime.timedelta(hours=1),
        operation_id="multi-paid-checkout-second-0001",
        txn_id="TX-MULTI-PAID-2",
    )
    third = _buy_pro(
        db,
        owner,
        shop,
        now=gift_moment + datetime.timedelta(hours=2),
        operation_id="multi-paid-checkout-third-0001",
        txn_id="TX-MULTI-PAID-3",
        cycle="YEARLY",
    )
    assert second.entitlement_starts_at == datetime.datetime(2026, 9, 30, 17, 0)
    assert third.entitlement_starts_at == second.entitlement_ends_at

    revoke_moment = now + datetime.timedelta(days=2)
    subscription_service.revoke_admin_gift(
        db,
        admin,
        gift["grant"]["id"],
        SubscriptionGiftRevoke(
            reason="Gỡ quà nhưng giữ đủ các kỳ mua",
            operation_id="multi-paid-revoke-operation-0001",
        ),
        now=revoke_moment,
    )
    db.expire_all()
    first = db.query(models.SubscriptionCheckout).filter_by(id=first.id).one()
    second = db.query(models.SubscriptionCheckout).filter_by(id=second.id).one()
    third = db.query(models.SubscriptionCheckout).filter_by(id=third.id).one()
    assert (first.entitlement_starts_at, first.entitlement_ends_at) == (
        first_start,
        first_end,
    )
    assert second.entitlement_starts_at == first.entitlement_ends_at
    assert second.entitlement_ends_at - second.entitlement_starts_at == datetime.timedelta(
        days=30
    )
    assert third.entitlement_starts_at == second.entitlement_ends_at
    assert third.entitlement_ends_at - third.entitlement_starts_at == datetime.timedelta(
        days=365
    )
    state = subscription_service.get_subscription_state(db, shop.id, now=revoke_moment)
    assert state["access_source"] == "PAID"
    assert state["access_until"] == third.entitlement_ends_at
    assert state["paid_until"] == third.entitlement_ends_at


def test_grace_chi_tu_segment_paid_da_chay_va_renew_noi_tu_han_cu(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    now = datetime.datetime(2026, 8, 7, 12, 0, 0)
    owner, shop = _user_and_shop(db, now=now - datetime.timedelta(days=100))
    first = _buy_pro(
        db,
        owner,
        shop,
        now=now - datetime.timedelta(days=32),
        operation_id="real-segment-grace-first-0001",
        txn_id="TX-REAL-SEGMENT-GRACE-1",
    )
    assert first.entitlement_ends_at == now - datetime.timedelta(days=2)
    grace_state = subscription_service.get_subscription_state(db, shop.id, now=now)
    assert grace_state["phase"] == "GRACE"
    assert grace_state["access_source"] == "GRACE"
    assert grace_state["paid_grace_until"] == now + datetime.timedelta(days=5)

    renewed = _buy_pro(
        db,
        owner,
        shop,
        now=now,
        operation_id="real-segment-grace-renew-0001",
        txn_id="TX-REAL-SEGMENT-GRACE-2",
    )
    assert renewed.entitlement_starts_at == first.entitlement_ends_at
    assert renewed.entitlement_ends_at == now + datetime.timedelta(days=28)
    assert renewed.entitlement_ends_at - renewed.entitlement_starts_at == datetime.timedelta(
        days=30
    )
    renewed_state = subscription_service.get_subscription_state(db, shop.id, now=now)
    assert renewed_state["phase"] == "PAID"
    assert renewed_state["access_source"] == "PAID"


def test_gift_trong_grace_mua_roi_thu_hoi_van_noi_tu_han_paid_cu(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    first_start = datetime.datetime(2026, 8, 1, 10, 0, 0)
    owner, shop = _user_and_shop(
        db, now=first_start - datetime.timedelta(days=100)
    )
    admin = models.User(
        username=f"grace_gift_admin_{uuid.uuid4().hex[:8]}",
        hashed_password="not-used",
        role="ADMIN",
        is_verified=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    first = _buy_pro(
        db,
        owner,
        shop,
        now=first_start,
        operation_id="grace-gift-first-paid-0001",
        txn_id="TX-GRACE-GIFT-FIRST",
    )
    old_paid_end = first_start + datetime.timedelta(days=30)
    assert first.entitlement_ends_at == old_paid_end

    gift_moment = old_paid_end + datetime.timedelta(days=3)
    gift = subscription_service.create_admin_gift(
        db,
        admin,
        shop.id,
        SubscriptionGiftCreate(
            expires_on=datetime.date(2026, 9, 10),
            reason="Tặng khi khách đang trong grace",
            operation_id="grace-gift-grant-operation-0001",
        ),
        now=gift_moment,
    )
    second = _buy_pro(
        db,
        owner,
        shop,
        now=gift_moment,
        operation_id="grace-gift-second-paid-0001",
        txn_id="TX-GRACE-GIFT-SECOND",
    )
    assert second.entitlement_starts_at == datetime.datetime(2026, 9, 10, 17, 0)

    revoke_moment = old_paid_end + datetime.timedelta(days=4)
    subscription_service.revoke_admin_gift(
        db,
        admin,
        gift["grant"]["id"],
        SubscriptionGiftRevoke(
            reason="Gỡ quà, không cộng miễn ngày grace",
            operation_id="grace-gift-revoke-operation-0001",
        ),
        now=revoke_moment,
    )
    db.expire_all()
    second = db.query(models.SubscriptionCheckout).filter_by(id=second.id).one()
    assert second.entitlement_starts_at == old_paid_end
    assert second.entitlement_ends_at == old_paid_end + datetime.timedelta(days=30)
    assert second.entitlement_ends_at - second.entitlement_starts_at == datetime.timedelta(
        days=30
    )
    state = subscription_service.get_subscription_state(db, shop.id, now=revoke_moment)
    assert state["phase"] == "PAID"
    assert state["access_until"] == second.entitlement_ends_at


def test_shop_chi_co_trial_duoc_xoa_nhung_co_ma_thanh_toan_thi_giu_lai(
    db, monkeypatch
):
    _bank_env(monkeypatch)
    owner_empty, shop_empty = _user_and_shop(db)
    result = shop_service.delete_shop(db, owner_empty, shop_empty.id)
    assert result == {"msg": "Deleted"}
    assert db.query(models.Shop).filter_by(id=shop_empty.id).first() is None
    assert (
        db.query(models.ShopSubscription).filter_by(shop_id=shop_empty.id).first()
        is None
    )

    owner_checkout, shop_checkout = _user_and_shop(db)
    subscription_service.create_checkout(
        db,
        owner_checkout,
        shop_checkout.id,
        SubscriptionCheckoutCreate(
            cycle="MONTHLY", operation_id="delete-guard-checkout-0001"
        ),
    )
    with pytest.raises(HTTPException) as exc:
        shop_service.delete_shop(db, owner_checkout, shop_checkout.id)
    assert exc.value.status_code == 409
    assert db.query(models.Shop).filter_by(id=shop_checkout.id).one()


def test_api_quyen_va_unique_index_tien_fail_fast(client, db, monkeypatch):
    _bank_env(monkeypatch)
    owner, shop = _user_and_shop(db)
    owner_token = _token_for(db, owner)
    admin = db.query(models.User).filter(models.User.username == "admin").one()
    admin_token = _token_for(db, admin)

    status = client.get(
        f"/api/subscriptions/{shop.id}", headers=auth(owner_token)
    )
    assert status.status_code == 200
    assert status.json()["prices"]["yearly_vnd"] == 831_600
    assert client.get(
        "/api/admin/subscriptions", headers=auth(owner_token)
    ).status_code == 403
    assert client.get(
        "/api/admin/subscriptions", headers=auth(admin_token)
    ).status_code == 200
    assert client.post(
        f"/api/subscriptions/{shop.id}/checkouts",
        json={"cycle": "MONTHLY", "operation_id": "short"},
        headers=auth(owner_token),
    ).status_code == 422

    protected = {
        "ux_shop_subscriptions_shop_id",
        "ux_subscription_grants_operation_id",
        "ux_subscription_grants_revoke_operation_id",
        "ux_subscription_checkouts_reference_code",
        "ux_subscription_checkouts_operation_id",
        "ux_subscription_checkouts_one_open_per_shop",
        "ux_subscription_payments_idempotency_key",
    }
    assert protected.issubset(set(bootstrap._REQUIRED_INDEXES))
    assert protected.issubset(bootstrap._FINANCIAL_INDEXES)
    assert not protected.intersection(bootstrap.verify_required_indexes(db))


@pytest.mark.parametrize(
    "schema,payload",
    [
        (
            SubscriptionCheckoutCreate,
            {"cycle": "MONTHLY", "operation_id": "       a"},
        ),
        (
            SubscriptionGiftCreate,
            {
                "expires_on": "2026-08-31",
                "reason": "Lý do hợp lệ",
                "operation_id": "       a",
            },
        ),
        (
            SubscriptionGiftRevoke,
            {"reason": "Lý do hợp lệ", "operation_id": "       a"},
        ),
    ],
)
def test_operation_id_cat_khoang_trang_truoc_khi_kiem_do_dai(schema, payload):
    with pytest.raises(ValueError):
        schema(**payload)
