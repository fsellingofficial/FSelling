"""Daily Close R1: deterministic end-of-day checklist inside Action Center."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from conftest import auth, seller_with_shop
from fselling.core.database import Base, SessionLocal
from fselling.services import report_service


ROOT = Path(__file__).resolve().parent.parent


def _row_counts() -> dict[str, int]:
    session = SessionLocal()
    try:
        return {
            table.name: int(
                session.execute(select(func.count()).select_from(table)).scalar_one()
            )
            for table in Base.metadata.tables.values()
        }
    finally:
        session.close()


def test_daily_close_empty_contract_is_read_only(client):
    ctx = seller_with_shop(client)
    before = _row_counts()

    response = client.get(
        f"/api/action-center/{ctx['shop_id']}", headers=auth(ctx["token"])
    )

    assert response.status_code == 200, response.text
    close = response.json()["daily_close"]
    assert close["business_date"]
    assert close["generated_at"].endswith("+07:00")
    assert close["ready"] is True
    assert close["summary"] == {
        "revenue_vnd": 0,
        "order_count": 0,
        "blocking": 0,
        "attention": 0,
        "clear": 6,
    }
    assert [item["kind"] for item in close["checks"]] == [
        "OPEN_SHIFTS",
        "CASH_VARIANCE",
        "ORDER_RECONCILIATION",
        "UNAPPLIED_BANK_EVENTS",
        "OFFLINE_ISSUES",
        "EXPENSE_REMINDERS",
    ]
    assert {item["status"] for item in close["checks"]} == {"CLEAR"}
    assert _row_counts() == before


def test_daily_close_uses_fixed_aggregate_checks_without_identity_or_urls(
    client, monkeypatch
):
    ctx = seller_with_shop(client)
    monkeypatch.setattr(
        report_service,
        "seller_dashboard",
        lambda *args, **kwargs: {
            "reconciliation_count": 2,
            "total_revenue": 1_250_000,
            "total_orders": 7,
        },
    )
    monkeypatch.setattr(
        report_service,
        "_action_center_unapplied_event_count",
        lambda *args, **kwargs: 1,
    )
    monkeypatch.setattr(
        report_service,
        "_daily_close_shift_metrics",
        lambda *args, **kwargs: {
            "open_shift_count": 2,
            "variance_count": 1,
            "variance_amount_vnd": 80_000,
        },
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "offline_service",
        SimpleNamespace(danh_sach_can_xu_ly=lambda *args, **kwargs: [{}, {}, {}]),
        raising=False,
    )
    monkeypatch.setattr(
        report_service.expense_service,
        "reminders",
        lambda *args, **kwargs: {
            "items": [{"day_of_month": 1, "missing_amount": 400_000}],
            "total_missing": 400_000,
        },
    )
    monkeypatch.setattr(
        report_service,
        "clearance_service",
        SimpleNamespace(de_xuat_xa_hang=lambda *args, **kwargs: {"danh_sach": []}),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "forecast_service",
        SimpleNamespace(du_bao_nhap_hang=lambda *args, **kwargs: {"danh_sach": []}),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "_action_center_overdue_order_count",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        report_service,
        "supplier_service",
        SimpleNamespace(list_suppliers=lambda *args, **kwargs: {"suppliers": []}),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "customer_service",
        SimpleNamespace(list_customers=lambda *args, **kwargs: []),
        raising=False,
    )

    response = client.get(
        f"/api/action-center/{ctx['shop_id']}", headers=auth(ctx["token"])
    )

    assert response.status_code == 200, response.text
    close = response.json()["daily_close"]
    assert close["ready"] is False
    assert close["summary"] == {
        "revenue_vnd": 1_250_000,
        "order_count": 7,
        "blocking": 1,
        "attention": 5,
        "clear": 0,
    }
    checks = {item["kind"]: item for item in close["checks"]}
    assert checks["OPEN_SHIFTS"] == {
        "kind": "OPEN_SHIFTS",
        "status": "BLOCKING",
        "count": 2,
    }
    assert checks["CASH_VARIANCE"]["amount_vnd"] == 80_000
    assert checks["EXPENSE_REMINDERS"]["amount_vnd"] == 400_000
    serialized = response.text.lower()
    for forbidden in (
        "username",
        "customer_name",
        "supplier_name",
        "phone",
        "url",
        "cost_price",
    ):
        assert forbidden not in serialized


def test_daily_close_ui_is_part_of_action_center_and_only_navigates():
    html = (ROOT / "static/seller.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/seller.js").read_text(encoding="utf-8")
    locale = (ROOT / "static/js/locales/seller.js").read_text(encoding="utf-8")

    for element_id in (
        "dailyClosePanel",
        "dailyCloseRevenue",
        "dailyCloseOrders",
        "dailyCloseStatus",
        "dailyCloseList",
    ):
        assert f'id="{element_id}"' in html
    assert "function renderDailyClose(dailyClose)" in js
    assert "const DAILY_CLOSE_TARGETS = Object.freeze" in js
    assert "data-daily-close-kind" in js
    assert "dailyClose.url" not in js
    for key in (
        "seller.daily_close.title",
        "seller.daily_close.ready",
        "seller.daily_close.not_ready",
        "seller.daily_close.kind.OPEN_SHIFTS.title",
        "seller.daily_close.kind.EXPENSE_REMINDERS.description",
    ):
        assert locale.count(f"'{key}'") == 2, key
    assert "20260911-r5-5" in html
