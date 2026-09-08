import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import models
from ..core.i18n import tr
from ..dependencies import get_current_user, get_db, require_shop_access
from ..schemas.order import (
    CashPayment,
    CashTopup,
    DebtPayment,
    OfflineIssueAcknowledge,
    OfflineOrderCreate,
    OfflineOrderCreateV1,
    OrderCreate,
    OrderReturnApprovalCreate,
    OrderReturnCreate,
    RefundComplete,
)
from ..services import (
    offline_service,
    order_service,
    qr_sales_service,
    return_service,
    subscription_service,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])

# Body cap: 64 KiB actual
BODY_CAP_BYTES = 64 * 1024

_V1_ONLY_FIELDS = frozenset(
    {
        "lease_id",
        "device_id",
        "offline_session_id",
        "sequence",
        "sold_at_client_utc",
        "client_monotonic_ms",
        "monotonic_valid",
        "server_anchor_id",
        "catalog_version",
        "catalog_snapshot_digest",
        "client_fingerprint",
    }
)


def _offline_http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": tr(message)},
    )


async def _read_offline_body_bounded(request: Request) -> bytes:
    """Read at most 64 KiB after FastAPI has completed JWT authentication."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared, 10)
        except ValueError:
            declared_size = -1
        if declared_size > BODY_CAP_BYTES:
            raise _offline_http_error(
                413,
                "OFFLINE_BODY_TOO_LARGE",
                "Body vượt giới hạn 64 KiB",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > BODY_CAP_BYTES:
            raise _offline_http_error(
                413,
                "OFFLINE_BODY_TOO_LARGE",
                "Body vượt giới hạn 64 KiB",
            )
        body.extend(chunk)
    return bytes(body)


def _malformed() -> HTTPException:
    return _offline_http_error(
        422,
        "OFFLINE_RECEIPT_MALFORMED",
        "Phiếu offline không hợp lệ",
    )


@router.post("/{shop_id}")
def create_order(
    shop_id: int,
    order: OrderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Free vẫn bán tiền mặt/VietQR bằng tài khoản chủ shop. Nhân viên là tính
    # năng Pro; bán ghi nợ cũng là nghiệp vụ Pro. Chỉ chặn lúc TẠO đơn mới —
    # các endpoint thu nợ/hoàn tiền/trả hàng/đồng bộ offline phía dưới luôn mở
    # để giải quyết tiền đã phát sinh.
    require_shop_access(db, shop_id, current_user)
    if current_user.role == "STAFF" or order.payment_method == "debt":
        subscription_service.require_pro(db, shop_id)
    return order_service.create_order(db, current_user, shop_id, order)


@router.get("/{shop_id}/history")
def get_sales_history(
    shop_id: int,
    scope: Literal["today", "7d"] = Query("today"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.list_sales_history(
        db, current_user, shop_id, scope=scope, q=q, page=page
    )


@router.post("/{shop_id}/offline")
async def dong_bo_don_offline(
    shop_id: int,
    request: Request,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nhận một phiếu đã bán khi mất mạng.

    Body cap 64 KiB. Content-Length early rejection. 200 items max.
    Contract v1 dùng X-Offline-Lease-Token header (không body/query/fingerprint).
    """
    # Because this endpoint accepts Request instead of a Pydantic body, FastAPI
    # resolves get_current_user first.  Only an authenticated request reaches
    # this bounded ASGI-stream reader.
    body = await _read_offline_body_bounded(request)
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _malformed()
    if not isinstance(raw, dict):
        raise _malformed()

    version = raw.get("offline_contract_version")
    is_exact_int = isinstance(version, int) and not isinstance(version, bool)
    if version is None or (is_exact_int and version == 0):
        # A v0 payload may explicitly say 0, but any v1-only tuple member makes
        # it malformed rather than silently fabricating/promoting evidence.
        if _V1_ONLY_FIELDS.intersection(raw):
            raise _malformed()
        try:
            phieu = OfflineOrderCreate.model_validate(raw)
        except ValidationError:
            raise _malformed()
        return offline_service.dong_bo_phieu(db, current_user, shop_id, phieu)

    if is_exact_int and version == 1:
        try:
            phieu = OfflineOrderCreateV1.model_validate(raw)
        except ValidationError:
            raise _malformed()
        # Token from X-Offline-Lease-Token header only — never from body/query
        lease_token = request.headers.get("X-Offline-Lease-Token", "")
        return offline_service.dong_bo_phieu_v1(
            db, current_user, shop_id, phieu, lease_token=lease_token
        )

    raise _malformed()


@router.get("/{shop_id}/offline-issues")
def don_offline_can_xu_ly(
    shop_id: int,
    state: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Đơn offline có vướng mắc (tồn âm, ca đã chốt, sản phẩm đã xóa...).

    Mặc định chỉ trả vướng mắc `OPEN`. Truyền `state=ACKNOWLEDGED|RESOLVED|ALL`
    để tra lịch sử những cái đã xử lý xong.
    """
    require_shop_access(db, shop_id, current_user)
    return offline_service.danh_sach_can_xu_ly(db, shop_id, state)


@router.post("/{shop_id}/offline-issues/{issue_id}/acknowledge")
def xac_nhan_van_de_offline(
    shop_id: int,
    issue_id: int,
    payload: OfflineIssueAcknowledge,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Chủ shop ghi nhận đã xem một vướng mắc không có bằng chứng để đóng.

    Không đụng tới kho, giá vốn hay tiền: `TON_AM` exact vẫn phải đợi kiểm kê,
    còn `SP_KHONG_CON` phải đi đường phục hồi.
    """
    return offline_service.xac_nhan_van_de(
        db, current_user, shop_id, issue_id, payload
    )


@router.get("/{order_id}/qr")
def get_order_qr_intent(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Sanitized metadata for one already-issued immutable sales intent."""
    return qr_sales_service.authorized_intent_metadata(
        db, current_user, order_id
    )


@router.get("/{order_id}/qr/render")
def render_order_qr(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Render existing intent bytes; this endpoint can never issue/regenerate."""
    rendered = qr_sales_service.render_authorized_intent(
        db, current_user, order_id
    )
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.get_order(db, current_user, order_id)


@router.get("/{order_id}/detail")
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    chi_tiet = order_service.get_order_detail(db, current_user, order_id)
    return return_service.bo_sung_thong_tin_tra_hang(db, chi_tiet)


@router.post("/{order_id}/debt-payment")
def debt_payment(
    order_id: int,
    payload: DebtPayment,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.debt_payment(db, current_user, order_id, payload)


@router.post("/{order_id}/returns")
def create_order_return(
    order_id: int,
    payload: OrderReturnCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return return_service.create_return(db, current_user, order_id, payload)


@router.post("/{order_id}/returns/approval")
def approve_return(
    order_id: int,
    request: OrderReturnApprovalCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return return_service.create_return_approval(
        db, current_user, order_id, request
    )


@router.post("/{order_id}/pay")
def pay_order(
    order_id: int,
    request: CashPayment | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.pay_order(db, current_user, order_id, request)


@router.post("/{order_id}/cash-topup")
def cash_topup(
    order_id: int,
    request: CashTopup,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.cash_topup(db, current_user, order_id, request)


@router.post("/{order_id}/refund-complete")
def refund_complete(
    order_id: int,
    request: RefundComplete,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.complete_refund(db, current_user, order_id, request)


@router.post("/{order_id}/cancel")
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return order_service.cancel_order(db, current_user, order_id)
