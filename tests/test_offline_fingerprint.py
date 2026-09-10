"""Pure contract tests for the server-owned offline receipt v0 fingerprint."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from fselling import models
from fselling.services.offline_fingerprint import fingerprint_offline_receipt_v0


def _vector(**changes):
    values = {
        "shop_id": 7,
        "offline_uuid": "  off-vector-001  ",
        "sold_at": datetime(
            2026,
            8,
            11,
            15,
            4,
            5,
            123456,
            tzinfo=timezone(timedelta(hours=7)),
        ),
        "items": [
            {
                "product_id": 2,
                "product_name": "  Cà phê\u00a0sữa ",
                "unit_price_vnd": 12_500,
                "quantity": 2,
            },
            {
                "product_id": 1,
                "product_name": "A\u0301o  xanh",
                "unit_price_vnd": 100_000,
                "quantity": 1,
            },
        ],
        "cash_tendered_vnd": 125_000,
        "device_label": "\u3000Máy\u00a0  A\u3000",
    }
    values.update(changes)
    return values


def test_orm_maps_exact_three_released_tables_without_owning_new_schema():
    lease = models.OfflineLease.__table__
    registry = models.OfflineReceiptRegistry.__table__
    receipt = models.OfflineReceipt.__table__
    assert (lease.name, registry.name, receipt.name) == (
        "offline_leases",
        "offline_receipt_registry",
        "offline_receipts",
    )
    assert set(lease.columns.keys()) == {
        "lease_id", "shop_id", "user_id", "device_id", "contract_version",
        "catalog_version", "catalog_snapshot_digest", "secret_sha256",
        "server_anchor_id", "anchor_server_time_utc", "issued_at", "expires_at",
        "state_version", "revoked_at", "revoke_reason", "revoked_by_user_id",
        "issued_by_auth_session_id",
    }
    assert set(registry.columns.keys()) == {
        "offline_uuid", "shop_id", "order_id", "server_fingerprint",
        "contract_version", "state", "superseded_by_offline_uuid", "created_at",
        "updated_at", "state_version",
    }
    assert set(receipt.columns.keys()) == {
        "id", "order_id", "offline_uuid", "contract_version", "lease_id",
        "device_id", "offline_session_id", "sequence", "server_fingerprint",
        "client_fingerprint", "client_fingerprint_mismatch",
        "sold_by_claimed_user_id", "synced_by_user_id", "attribution_kind",
        "sold_at_effective", "sold_at_client_utc", "sold_at_upper_bound",
        "time_confidence", "client_monotonic_ms", "server_anchor_id", "ingested_at",
    }
    for column in (
        lease.c.anchor_server_time_utc,
        lease.c.issued_at,
        lease.c.expires_at,
        lease.c.revoked_at,
        registry.c.created_at,
        registry.c.updated_at,
        receipt.c.sold_at_effective,
        receipt.c.sold_at_client_utc,
        receipt.c.sold_at_upper_bound,
        receipt.c.ingested_at,
    ):
        assert column.type.length == 26
    assert lease.c.revoked_at.nullable is True
    assert registry.c.order_id.nullable is True
    assert receipt.c.order_id.nullable is False
    assert receipt.c.client_fingerprint_mismatch.nullable is False


def test_known_vector_is_locked_independently():
    result = fingerprint_offline_receipt_v0(**_vector())

    # Literal expected bytes/digest are reviewable constants, not calculated
    # through the helper under test.
    expected_json = (
        '{"cash_tendered_vnd":125000,"device_label":"Máy A","items":['
        '{"product_id":1,"product_name":"Áo xanh","quantity":1,'
        '"unit_price_vnd":100000},'
        '{"product_id":2,"product_name":"Cà phê sữa","quantity":2,'
        '"unit_price_vnd":12500}],"offline_uuid":"off-vector-001",'
        '"shop_id":7,"sold_at_utc":"2026-08-11 08:04:05.123456"}'
    )
    assert result.canonical_json == expected_json
    assert result.fingerprint == (
        "fsofr0:8c593b93522e11e191385073b327a4fb"
        "398866403766c1fdc9fe14d4bcb0a324"
    )
    assert len(result.sold_at_text) == 26


def test_item_reordering_does_not_change_the_digest():
    original = _vector()
    reordered = deepcopy(original)
    reordered["items"].reverse()
    assert (
        fingerprint_offline_receipt_v0(**original).fingerprint
        == fingerprint_offline_receipt_v0(**reordered).fingerprint
    )


def test_duplicate_line_multiplicity_is_not_grouped_away():
    shared = {
        "shop_id": 1,
        "offline_uuid": "off-duplicate-lines",
        "sold_at": datetime(2026, 8, 11, 1, 2, 3),
        "cash_tendered_vnd": 20_000,
        "device_label": None,
    }
    duplicate_lines = [
        {
            "product_id": 4,
            "product_name": "Bánh",
            "unit_price_vnd": 10_000,
            "quantity": 1,
        },
        {
            "product_id": 4,
            "product_name": "Bánh",
            "unit_price_vnd": 10_000,
            "quantity": 1,
        },
    ]
    grouped_line = [
        {
            "product_id": 4,
            "product_name": "Bánh",
            "unit_price_vnd": 10_000,
            "quantity": 2,
        }
    ]
    assert fingerprint_offline_receipt_v0(
        **shared, items=duplicate_lines
    ).fingerprint != fingerprint_offline_receipt_v0(
        **shared, items=grouped_line
    ).fingerprint


def test_unicode_equivalence_whitespace_and_timezone_equivalence_are_canonical():
    first = _vector()
    equivalent = _vector(
        sold_at=datetime(2026, 8, 11, 8, 4, 5, 123456, tzinfo=timezone.utc),
        device_label="Máy A",
        items=[
            {
                "product_id": 1,
                "product_name": "Áo\u2003xanh",
                "unit_price_vnd": 100_000,
                "quantity": 1,
            },
            {
                "product_id": 2,
                "product_name": "Cà phê sữa",
                "unit_price_vnd": 12_500,
                "quantity": 2,
            },
        ],
    )
    assert (
        fingerprint_offline_receipt_v0(**first).fingerprint
        == fingerprint_offline_receipt_v0(**equivalent).fingerprint
    )


@pytest.mark.parametrize(
    "field,mutated",
    [
        ("shop_id", 8),
        ("offline_uuid", "off-vector-002"),
        ("sold_at", datetime(2026, 8, 11, 15, 4, 6, tzinfo=timezone(timedelta(hours=7)))),
        ("cash_tendered_vnd", 125_001),
        ("device_label", "Máy B"),
    ],
)
def test_each_top_level_material_field_changes_the_digest(field, mutated):
    baseline = fingerprint_offline_receipt_v0(**_vector()).fingerprint
    assert fingerprint_offline_receipt_v0(
        **_vector(**{field: mutated})
    ).fingerprint != baseline


def test_each_item_material_field_changes_the_digest():
    baseline_payload = _vector()
    baseline = fingerprint_offline_receipt_v0(**baseline_payload).fingerprint
    for field, value in (
        ("product_id", 99),
        ("product_name", "Áo đỏ"),
        ("unit_price_vnd", 100_001),
        ("quantity", 2),
    ):
        changed = deepcopy(baseline_payload)
        changed["items"][1][field] = value
        assert fingerprint_offline_receipt_v0(**changed).fingerprint != baseline


@pytest.mark.parametrize(
    "forbidden",
    ["\x00", "\t", "\x1e", "\x1f", "\x7f", "\x85", "\u200b", "\u200c", "\u200d", "\ufeff"],
)
def test_forbidden_control_and_invisible_text_is_rejected(forbidden):
    payload = _vector(device_label=f"Máy{forbidden}A")
    with pytest.raises(ValueError, match="forbidden"):
        fingerprint_offline_receipt_v0(**payload)


@pytest.mark.parametrize(
    "blank",
    ["   ", "\u00a0", "\u3000\u3000", "\u2003 \u00a0"],
)
def test_required_product_name_must_not_canonicalize_to_empty(blank):
    """Tên hàng là bằng chứng bắt buộc; `min_length=1` không chặn được chuỗi chỉ
    gồm khoảng trắng Unicode."""
    payload = _vector()
    payload["items"][0]["product_name"] = blank
    with pytest.raises(ValueError, match="product_name"):
        fingerprint_offline_receipt_v0(**payload)


@pytest.mark.parametrize("blank", ["   ", "\u3000\u3000", "\u00a0 \u2003"])
def test_nullable_device_label_still_canonicalizes_to_null(blank):
    assert fingerprint_offline_receipt_v0(**_vector(device_label=blank)).device_label is None


@pytest.mark.parametrize(
    "boundary",
    [
        datetime(1, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=14))),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=-14))),
    ],
)
def test_out_of_range_utc_conversion_raises_value_error_not_overflow(boundary):
    """`astimezone` ném `OverflowError` ở biên; callers chỉ dịch `ValueError`
    thành HTTP 400 nên phải quy về đúng loại đó."""
    with pytest.raises(ValueError, match="representable UTC range"):
        fingerprint_offline_receipt_v0(**_vector(sold_at=boundary))


@pytest.mark.parametrize(
    "change",
    [
        {"cash_tendered_vnd": 125_000.0},
        {
            "items": [
                {
                    "product_id": 1,
                    "product_name": "Hàng",
                    "unit_price_vnd": 1.0,
                    "quantity": 1,
                }
            ]
        },
    ],
)
def test_fingerprint_never_accepts_float_money(change):
    with pytest.raises(ValueError, match="integer"):
        fingerprint_offline_receipt_v0(**_vector(**change))
