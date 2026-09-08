"""I05 end-to-end cost provenance, cumulative returns and concurrency."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from conftest import _TEST_MIGRATIONS, _unique, auth, create_product, seller_with_shop
from sqlalchemy import update

from fselling import models
from fselling.core import thoi_gian
from fselling.core.database import SessionLocal
from fselling.services import return_service


def _adjust(client, ctx, product_id: int, quantity: int, cost=None, expiry=None):
    payload = {"delta": quantity, "reason": "I05 provenance test"}
    if cost is not None:
        payload["unit_cost"] = cost
    if expiry is not None:
        payload["expiry_date"] = expiry
    response = client.post(
        f"/api/products/{product_id}/stock",
        json=payload,
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text


def _order(
    client,
    ctx,
    product: dict,
    quantity: int,
    *,
    paid: bool = False,
    customer_id: int | None = None,
) -> int:
    payload = {
        "items": [
            {
                "product_id": product["id"],
                "price": product["price"],
                "quantity": quantity,
            }
        ],
        "payment_method": "cash" if paid else "transfer",
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    response = client.post(
        f"/api/orders/{ctx['shop_id']}",
        json=payload,
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    order_id = response.json()["order_id"]
    if paid:
        payment = client.post(
            f"/api/orders/{order_id}/pay", headers=auth(ctx["token"])
        )
        assert payment.status_code == 200, payment.text
    return order_id


def _return(client, ctx, order_id: int, item_id: int, quantity: int, *, restock: bool, operation_id: str):
    return client.post(
        f"/api/orders/{order_id}/returns",
        json={
            "items": [
                {
                    "order_item_id": item_id,
                    "quantity": quantity,
                    "restock": restock,
                }
            ],
            "method": "transfer",
            "reason": "Khách trả hàng",
            "reference": "TEST-RETURN",
            "operation_id": operation_id,
        },
        headers=auth(ctx["token"]),
    )


def _return_atomic_state(order_id: int, item_id: int, product_id: int):
    session = SessionLocal()
    try:
        order = session.get(models.Order, order_id)
        line = session.get(models.OrderItem, item_id)
        product = session.get(models.Product, product_id)
        sources = (
            session.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == item_id)
            .order_by(models.OrderItemBatch.id)
            .all()
        )
        batches = (
            session.query(models.ProductBatch)
            .filter(models.ProductBatch.product_id == product_id)
            .order_by(models.ProductBatch.id)
            .all()
        )
        return_ids = [
            row[0]
            for row in session.query(models.OrderReturn.id)
            .filter(models.OrderReturn.order_id == order_id)
            .all()
        ]
        return_items = (
            session.query(models.OrderReturnItem)
            .filter(models.OrderReturnItem.order_item_id == item_id)
            .order_by(models.OrderReturnItem.id)
            .all()
        )
        return_item_ids = [row.id for row in return_items]
        return_batches = (
            session.query(models.OrderReturnItemBatch)
            .filter(
                models.OrderReturnItemBatch.return_item_id.in_(return_item_ids)
            )
            .order_by(models.OrderReturnItemBatch.id)
            .all()
            if return_item_ids
            else []
        )
        return {
            "line": (
                line.returned_total_qty,
                line.returned_known_qty,
                line.returned_unknown_qty,
                line.returned_cost_basis_vnd,
                line.returned_refund_vnd,
                line.cost_return_version,
            ),
            "product": (
                product.stock,
                product.cost_known_qty,
                product.cost_unknown_qty,
                product.cost_basis_vnd,
                product.cost_deficit_qty,
            ),
            "sources": [
                (
                    row.id,
                    row.returned_total_qty,
                    row.returned_known_qty,
                    row.returned_unknown_qty,
                    row.returned_cost_basis_vnd,
                    row.cost_return_version,
                )
                for row in sources
            ],
            "batches": [
                (
                    row.id,
                    row.quantity,
                    row.cost_known_qty,
                    row.cost_unknown_qty,
                    row.cost_basis_vnd,
                    row.cost_deficit_qty,
                )
                for row in batches
            ],
            "returns": return_ids,
            "return_items": [row.id for row in return_items],
            "return_batches": [row.id for row in return_batches],
            "payments": [
                    (row.id, row.entry_type, row.amount)
                for row in session.query(models.OrderPayment)
                .filter(models.OrderPayment.order_id == order_id)
                .order_by(models.OrderPayment.id)
                .all()
            ],
            "loyalty": [
                (row.id, row.points_delta)
                for row in session.query(models.LoyaltyPointEntry)
                .filter(models.LoyaltyPointEntry.order_id == order_id)
                .order_by(models.LoyaltyPointEntry.id)
                .all()
            ],
            "loyalty_balance": (
                sum(
                    row[0]
                    for row in session.query(models.LoyaltyPointEntry.points_delta)
                    .filter(
                        models.LoyaltyPointEntry.customer_id
                        == order.customer_id
                    )
                    .all()
                )
                if order.customer_id is not None
                else 0
            ),
        }
    finally:
        session.close()


def test_nonbatch_unknown_first_and_final_known_remainder(client):
    ctx = seller_with_shop(client)
    product = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        _unique("I05Pool"),
        100,
        1,
        ctx["category_id"],
    )
    # U=1, then K=2/B=2 and K=1/B=2 => U=1, K=3, B=4.
    _adjust(client, ctx, product["id"], 2, cost=1)
    _adjust(client, ctx, product["id"], 1, cost=2)

    order_ids = [_order(client, ctx, product, 1) for _ in range(4)]
    session = SessionLocal()
    try:
        allocations = [
            session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == order_id)
            .one()
            for order_id in order_ids
        ]
        assert [
            (row.cost_known_qty, row.cost_unknown_qty, row.cost_basis_vnd)
            for row in allocations
        ] == [
            (0, 1, 0),
            (1, 0, 1),
            (1, 0, 1),
            (1, 0, 2),
        ]
        source = session.get(models.Product, product["id"])
        assert (
            source.stock,
            source.cost_known_qty,
            source.cost_unknown_qty,
            source.cost_basis_vnd,
            source.cost_deficit_qty,
        ) == (0, 0, 0, 0, 0)
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_batch_partial_returns_known_first_retry_and_restock_false(client):
    ctx = seller_with_shop(client)
    response = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": _unique("I05Batch"),
            "price": 100,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    product = response.json()
    today = thoi_gian.hom_nay_vn()
    known_expiry = (today + timedelta(days=10)).isoformat()
    unknown_expiry = (today + timedelta(days=20)).isoformat()
    _adjust(client, ctx, product["id"], 2, cost=5, expiry=known_expiry)
    _adjust(client, ctx, product["id"], 2, expiry=unknown_expiry)

    order_id = _order(client, ctx, product, 4, paid=True)
    session = SessionLocal()
    try:
        line = (
            session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == order_id)
            .one()
        )
        item_id = line.id
        assert (line.cost_known_qty, line.cost_unknown_qty, line.cost_basis_vnd) == (2, 2, 10)
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    first_op = "i05-return-" + uuid.uuid4().hex
    first = _return(
        client, ctx, order_id, item_id, 1, restock=False, operation_id=first_op
    )
    retry = _return(
        client, ctx, order_id, item_id, 1, restock=False, operation_id=first_op
    )
    assert first.status_code == retry.status_code == 200
    assert retry.json()["return"]["id"] == first.json()["return"]["id"]

    second = _return(
        client,
        ctx,
        order_id,
        item_id,
        2,
        restock=True,
        operation_id="i05-return-" + uuid.uuid4().hex,
    )
    final = _return(
        client,
        ctx,
        order_id,
        item_id,
        1,
        restock=True,
        operation_id="i05-return-" + uuid.uuid4().hex,
    )
    assert second.status_code == final.status_code == 200

    session = SessionLocal()
    try:
        line = session.get(models.OrderItem, item_id)
        assert (
            line.returned_total_qty,
            line.returned_known_qty,
            line.returned_unknown_qty,
            line.returned_cost_basis_vnd,
            line.cost_return_version,
        ) == (4, 2, 2, 10, 3)
        batches = (
            session.query(models.ProductBatch)
            .filter(models.ProductBatch.product_id == product["id"])
            .order_by(models.ProductBatch.expiry_date)
            .all()
        )
        # First known unit was provenance-only (restock=false); later returns
        # restore one known and both unknown units to their original batches.
        assert [
            (b.quantity, b.cost_known_qty, b.cost_unknown_qty, b.cost_basis_vnd)
            for b in batches
        ] == [(1, 1, 0, 5), (2, 0, 2, 0)]
        assert session.get(models.Product, product["id"]).stock == 3
        source_rows = (
            session.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == item_id)
            .order_by(models.OrderItemBatch.id)
            .all()
        )
        assert [
            (
                row.returned_total_qty,
                row.returned_known_qty,
                row.returned_unknown_qty,
                row.returned_cost_basis_vnd,
            )
            for row in source_rows
        ] == [(2, 2, 0, 10), (2, 0, 2, 0)]
        provenance = session.query(models.OrderReturnItemBatch).all()
        assert sum(row.quantity for row in provenance) == 4
        assert sum(row.cost_basis_vnd for row in provenance) == 10
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_concurrent_partial_return_only_one_wins(client):
    ctx = seller_with_shop(client)
    product = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        _unique("I05ConcurrentReturn"),
        100,
        1,
        ctx["category_id"],
    )
    order_id = _order(client, ctx, product, 1, paid=True)
    session = SessionLocal()
    try:
        item_id = (
            session.query(models.OrderItem.id)
            .filter(models.OrderItem.order_id == order_id)
            .scalar()
        )
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()

    def submit(marker: str):
        return _return(
            client,
            ctx,
            order_id,
            item_id,
            1,
            restock=True,
            operation_id=f"i05-concurrent-{marker}-{uuid.uuid4().hex}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, ("a", "b")))
    assert sorted(response.status_code for response in responses) == [200, 400]

    session = SessionLocal()
    try:
        line = session.get(models.OrderItem, item_id)
        assert line.returned_total_qty == 1
        assert line.cost_return_version == 1
        assert (
            session.query(models.OrderReturn)
            .filter(models.OrderReturn.order_id == order_id)
            .count()
            == 1
        )
        assert session.get(models.Product, product["id"]).stock == 1
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()


def test_line_return_cas_stale_counter_rolls_back_entire_event(client, monkeypatch):
    ctx = seller_with_shop(client)
    program = client.put(
        f"/api/loyalty/{ctx['shop_id']}",
        json={
            "enabled": True,
            "earn_amount": 100,
            "earn_points": 1,
            "redeem_points": 1,
            "redeem_amount": 1,
            "min_redeem_points": 1,
            "max_redeem_percent": 100,
            "expiry_days": None,
        },
        headers=auth(ctx["token"]),
    )
    assert program.status_code == 200, program.text
    customer_response = client.post(
        f"/api/customers/{ctx['shop_id']}",
        json={
            "name": _unique("I05 CAS customer"),
            "phone": _unique("09")[:15],
        },
        headers=auth(ctx["token"]),
    )
    assert customer_response.status_code == 200, customer_response.text
    customer_id = customer_response.json()["id"]
    product = create_product(
        client,
        ctx["token"],
        ctx["shop_id"],
        _unique("I05LineCAS"),
        100,
        1,
        ctx["category_id"],
    )
    order_id = _order(
        client,
        ctx,
        product,
        1,
        paid=True,
        customer_id=customer_id,
    )
    session = SessionLocal()
    try:
        item_id = (
            session.query(models.OrderItem.id)
            .filter(models.OrderItem.order_id == order_id)
            .scalar()
        )
    finally:
        session.close()
    before = _return_atomic_state(order_id, item_id, product["id"])
    assert before["loyalty_balance"] == 1
    real_cas = return_service._conditional_advance_line_return

    def stale_counter(db, line, delta):
        result = db.execute(
            update(models.OrderItem)
            .where(models.OrderItem.id == line.id)
            .values(
                returned_refund_vnd=models.OrderItem.returned_refund_vnd + 1
            )
            .execution_options(synchronize_session=False)
        )
        assert result.rowcount == 1
        return real_cas(db, line, delta)

    monkeypatch.setattr(
        return_service, "_conditional_advance_line_return", stale_counter
    )
    operation_id = "i05-line-cas-" + uuid.uuid4().hex
    failed = _return(
        client,
        ctx,
        order_id,
        item_id,
        1,
        restock=True,
        operation_id=operation_id,
    )
    assert failed.status_code == 409, failed.text
    assert _return_atomic_state(order_id, item_id, product["id"]) == before

    monkeypatch.setattr(
        return_service, "_conditional_advance_line_return", real_cas
    )
    success = _return(
        client,
        ctx,
        order_id,
        item_id,
        1,
        restock=True,
        operation_id=operation_id,
    )
    retry = _return(
        client,
        ctx,
        order_id,
        item_id,
        1,
        restock=True,
        operation_id=operation_id,
    )
    assert success.status_code == retry.status_code == 200
    assert success.json()["return"]["id"] == retry.json()["return"]["id"]
    after = _return_atomic_state(order_id, item_id, product["id"])
    assert len(after["returns"]) == len(before["returns"]) + 1
    assert after["line"][-1] == 1
    assert len(after["loyalty"]) == len(before["loyalty"]) + 1
    assert after["loyalty_balance"] == 0
    _TEST_MIGRATIONS.verify()


def test_batch_return_cas_stale_second_source_rolls_back_all(client, monkeypatch):
    ctx = seller_with_shop(client)
    response = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": _unique("I05BatchCAS"),
            "price": 100,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    product = response.json()
    today = thoi_gian.hom_nay_vn()
    _adjust(
        client,
        ctx,
        product["id"],
        1,
        cost=3,
        expiry=(today + timedelta(days=10)).isoformat(),
    )
    _adjust(
        client,
        ctx,
        product["id"],
        1,
        expiry=(today + timedelta(days=20)).isoformat(),
    )
    order_id = _order(client, ctx, product, 2, paid=True)
    session = SessionLocal()
    try:
        item_id = (
            session.query(models.OrderItem.id)
            .filter(models.OrderItem.order_id == order_id)
            .scalar()
        )
    finally:
        session.close()
    before = _return_atomic_state(order_id, item_id, product["id"])
    real_cas = return_service._conditional_advance_batch_return
    calls = {"count": 0}

    def stale_second_source(db, source, delta):
        calls["count"] += 1
        if calls["count"] == 2:
            result = db.execute(
                update(models.OrderItemBatch)
                .where(models.OrderItemBatch.id == source.id)
                .values(
                    cost_return_version=(
                        models.OrderItemBatch.cost_return_version + 1
                    )
                )
                .execution_options(synchronize_session=False)
            )
            assert result.rowcount == 1
        return real_cas(db, source, delta)

    monkeypatch.setattr(
        return_service,
        "_conditional_advance_batch_return",
        stale_second_source,
    )
    operation_id = "i05-batch-cas-" + uuid.uuid4().hex
    failed = _return(
        client,
        ctx,
        order_id,
        item_id,
        2,
        restock=True,
        operation_id=operation_id,
    )
    assert failed.status_code == 409, failed.text
    assert calls["count"] == 2
    assert _return_atomic_state(order_id, item_id, product["id"]) == before

    monkeypatch.setattr(
        return_service, "_conditional_advance_batch_return", real_cas
    )
    success = _return(
        client,
        ctx,
        order_id,
        item_id,
        2,
        restock=True,
        operation_id=operation_id,
    )
    retry = _return(
        client,
        ctx,
        order_id,
        item_id,
        2,
        restock=True,
        operation_id=operation_id,
    )
    assert success.status_code == retry.status_code == 200
    assert success.json()["return"]["id"] == retry.json()["return"]["id"]
    after = _return_atomic_state(order_id, item_id, product["id"])
    assert len(after["returns"]) == len(before["returns"]) + 1
    assert after["line"][-1] == 1
    assert all(row[-1] == 1 for row in after["sources"])
    _TEST_MIGRATIONS.verify()


def test_batch_cancellation_reverses_original_allocations_once_without_return(client):
    ctx = seller_with_shop(client)
    response = client.post(
        "/api/products",
        params={"shop_id": ctx["shop_id"]},
        data={
            "name": _unique("I05CancelBatch"),
            "price": 100,
            "stock": 0,
            "category_id": ctx["category_id"],
            "track_batches": "true",
        },
        headers=auth(ctx["token"]),
    )
    assert response.status_code == 200, response.text
    product = response.json()
    today = thoi_gian.hom_nay_vn()
    _adjust(
        client,
        ctx,
        product["id"],
        2,
        cost=3,
        expiry=(today + timedelta(days=10)).isoformat(),
    )
    _adjust(
        client,
        ctx,
        product["id"],
        1,
        expiry=(today + timedelta(days=20)).isoformat(),
    )
    order_id = _order(client, ctx, product, 3)

    first = client.post(
        f"/api/orders/{order_id}/cancel", headers=auth(ctx["token"])
    )
    retry = client.post(
        f"/api/orders/{order_id}/cancel", headers=auth(ctx["token"])
    )
    assert first.status_code == retry.status_code == 200
    assert first.json()["restored_items"] == 1
    assert retry.json()["restored_items"] == 0

    session = SessionLocal()
    try:
        order = session.get(models.Order, order_id)
        assert (
            order.status,
            order.inventory_reversed,
            order.inventory_reversal_version,
        ) == ("CANCELLED", 1, 1)
        line = (
            session.query(models.OrderItem)
            .filter(models.OrderItem.order_id == order_id)
            .one()
        )
        assert (
            line.inventory_reversed,
            line.inventory_reversal_version,
            line.returned_total_qty,
            line.cost_return_version,
        ) == (1, 1, 0, 0)
        source_rows = (
            session.query(models.OrderItemBatch)
            .filter(models.OrderItemBatch.order_item_id == line.id)
            .order_by(models.OrderItemBatch.id)
            .all()
        )
        assert all(
            row.inventory_reversed == 1
            and row.inventory_reversal_version == 1
            and row.returned_total_qty == 0
            for row in source_rows
        )
        batches = (
            session.query(models.ProductBatch)
            .filter(models.ProductBatch.product_id == product["id"])
            .order_by(models.ProductBatch.expiry_date)
            .all()
        )
        assert [
            (b.quantity, b.cost_known_qty, b.cost_unknown_qty, b.cost_basis_vnd)
            for b in batches
        ] == [(2, 2, 0, 6), (1, 0, 1, 0)]
        assert session.get(models.Product, product["id"]).stock == 3
        assert (
            session.query(models.OrderReturn)
            .filter(models.OrderReturn.order_id == order_id)
            .count()
            == 0
        )
    finally:
        session.close()
    _TEST_MIGRATIONS.verify()
