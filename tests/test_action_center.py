"""Action Center R1: owner-only computed read model and seller UI contract."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from conftest import auth, new_seller, new_staff, seller_with_shop
from fselling.core.database import Base, SessionLocal
from fselling.services import report_service


ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _function(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


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


def test_empty_action_center_is_read_only_and_has_stable_contract(client):
    ctx = seller_with_shop(client)
    before = _row_counts()

    response = client.get(
        f"/api/action-center/{ctx['shop_id']}", headers=auth(ctx["token"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shop_id"] == ctx["shop_id"]
    assert body["items"] == []
    assert body["summary"] == {
        "total": 0,
        "critical": 0,
        "attention": 0,
        "plan": 0,
    }
    assert body["generated_at"].endswith("+07:00")
    assert _row_counts() == before


def test_action_center_requires_owner_visibility_and_shop_scope(client):
    owner = seller_with_shop(client)
    _, staff_token = new_staff(client, owner, "MANAGER")
    _, other_token = new_seller(client)

    staff = client.get(
        f"/api/action-center/{owner['shop_id']}", headers=auth(staff_token)
    )
    wrong_shop = client.get(
        f"/api/action-center/{owner['shop_id']}", headers=auth(other_token)
    )
    anonymous = client.get(f"/api/action-center/{owner['shop_id']}")

    assert staff.status_code == 403
    assert wrong_shop.status_code == 403
    assert anonymous.status_code == 401


def test_action_center_aggregates_fixed_kinds_without_identity_or_urls(client, monkeypatch):
    ctx = seller_with_shop(client)
    monkeypatch.setattr(
        report_service,
        "seller_dashboard",
        lambda *args, **kwargs: {"reconciliation_count": 2},
    )
    monkeypatch.setattr(
        report_service,
        "_action_center_unapplied_event_count",
        lambda *args, **kwargs: 1,
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "_action_center_overdue_order_count",
        lambda *args, **kwargs: 2,
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "offline_service",
        SimpleNamespace(
            danh_sach_can_xu_ly=lambda *args, **kwargs: [
                {"issue_details": [{}, {}]},
                {"issue_details": [{}]},
            ]
        ),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "clearance_service",
        SimpleNamespace(
            de_xuat_xa_hang=lambda *args, **kwargs: {
                "danh_sach": [
                    {"so_luong_da_het_han": 4, "so_ngay_con_han": -1},
                    {"so_luong_da_het_han": 0, "so_ngay_con_han": 5},
                    {"so_luong_da_het_han": 0, "so_ngay_con_han": None},
                ]
            }
        ),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "forecast_service",
        SimpleNamespace(
            du_bao_nhap_hang=lambda *args, **kwargs: {
                "danh_sach": [
                    {"can_nhap": 5, "dang_ve": 3},
                    {"can_nhap": 2, "dang_ve": 0},
                    {"can_nhap": 0, "dang_ve": 9},
                ]
            }
        ),
        raising=False,
    )
    monkeypatch.setattr(
        report_service.expense_service,
        "reminders",
        lambda *args, **kwargs: {
            "items": [
                {"missing_amount": 500_000, "day_of_month": 1},
                {"missing_amount": 200_000, "day_of_month": 1},
            ],
            "total_missing": 700_000,
        },
    )
    monkeypatch.setattr(
        report_service,
        "supplier_service",
        SimpleNamespace(
            list_suppliers=lambda *args, **kwargs: {
                "suppliers": [
                    {"overdue_amount": 800_000},
                    {"overdue_amount": 100_000},
                    {"overdue_amount": 0},
                ]
            }
        ),
        raising=False,
    )
    monkeypatch.setattr(
        report_service,
        "customer_service",
        SimpleNamespace(
            list_customers=lambda *args, **kwargs: [
                {"debt_amount": 1_000_000},
                {"debt_amount": 200_000},
                {"debt_amount": 0},
            ]
        ),
        raising=False,
    )

    response = client.get(
        f"/api/action-center/{ctx['shop_id']}", headers=auth(ctx["token"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    by_kind = {item["kind"]: item for item in body["items"]}
    assert list(by_kind) == [
        "ORDER_RECONCILIATION",
        "UNAPPLIED_BANK_EVENTS",
        "OFFLINE_ISSUES",
        "STOCK_RISK",
        "REORDER",
        "OVERDUE_PURCHASE_ORDERS",
        "EXPENSE_REMINDERS",
        "SUPPLIER_OVERDUE",
        "CUSTOMER_DEBT",
    ]
    assert by_kind["OFFLINE_ISSUES"]["count"] == 2
    assert by_kind["OFFLINE_ISSUES"]["detail_count"] == 3
    assert by_kind["STOCK_RISK"]["quantity"] == 4
    assert by_kind["REORDER"]["quantity"] == 7
    assert by_kind["REORDER"]["incoming_quantity"] == 3
    assert by_kind["EXPENSE_REMINDERS"]["amount_vnd"] == 700_000
    assert by_kind["SUPPLIER_OVERDUE"]["amount_vnd"] == 900_000
    assert by_kind["CUSTOMER_DEBT"]["amount_vnd"] == 1_200_000
    assert body["summary"]["total"] == sum(item["count"] for item in body["items"])
    serialized = response.text.lower()
    for forbidden in ("customer_name", "supplier_name", "phone", "url", "cost_price"):
        assert forbidden not in serialized


def test_action_center_ui_has_fixed_navigation_and_safe_states():
    html = _read("static/seller.html")
    js = _read("static/js/seller.js")
    locale = _read("static/js/locales/seller.js")

    assert 'data-main-tab="action-center"' in html
    assert 'id="action-center"' in html
    for element_id in (
        "actionCenterBadge",
        "actionCenterLoading",
        "actionCenterError",
        "actionCenterEmpty",
        "actionCenterList",
    ):
        assert f'id="{element_id}"' in html
    assert "async function loadActionCenter()" in js
    assert "`/action-center/${shopId}`" in js
    assert "generation !== currentShopGeneration" in js
    assert "requestId !== actionCenterRequestId" in js
    assert "function openActionCenterItem(kind)" in js
    assert "const ACTION_CENTER_TARGETS = Object.freeze" in js
    assert "item.url" not in js
    load = _function(js, "async function loadActionCenter()", "function openActionCenterItem(")
    assert "method: 'POST'" not in load
    for key in (
        "seller.tabs.action_center",
        "seller.action_center.title",
        "seller.action_center.empty",
        "seller.action_center.kind.ORDER_RECONCILIATION.title",
        "seller.action_center.kind.CUSTOMER_DEBT.description",
    ):
        assert locale.count(f"'{key}'") == 2, key
    assert "20260911-r5-5" in html
