"""Khởi tạo FastAPI app: middleware, routers, static mount, scheduler."""
from __future__ import annotations

import zoneinfo
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core import bootstrap
from .core.config import (
    ORDER_PENDING_TIMEOUT_MINUTES,
    STATIC_DIR,
    UPLOAD_DIR,
    get_allowed_origins,
)
from .core.i18n import LocaleMiddleware, tr
from .core.database import db_path
from .migration.coordinator import verify_database_for_startup
from .routers import (
    admin,
    assistant,
    auth,
    categories,
    clearance,
    cron,
    customers,
    expenses,
    fnb,
    forecast,
    loyalty,
    offline_leases,
    offline_capability,
    offline_recovery,
    orders,
    pages,
    products,
    purchase_orders,
    purchase_receipts,
    qr_reconciliation,
    reports,
    shifts,
    shops,
    staff,
    subscriptions,
    suppliers,
    tts,
    vouchers,
    webhooks,
)
from .services.maintenance_service import (
    cancel_expired_pending_orders,
    cleanup_expired_unverified_users,
)

CLEANUP_INTERVAL_MINUTES = 1
AUTO_CANCEL_INTERVAL_MINUTES = 5

def _validation_message(error: dict) -> str:
    """Biến lỗi kỹ thuật của Pydantic thành câu ngắn theo ngôn ngữ request."""
    error_type = error.get("type", "")
    context = error.get("ctx") or {}
    if error_type == "missing":
        return tr("Trường này là bắt buộc")
    if error_type == "greater_than":
        return tr("Giá trị phải lớn hơn {minimum}", minimum=context.get("gt", ""))
    if error_type == "greater_than_equal":
        minimum = context.get("ge", "")
        return tr("Giá trị phải lớn hơn hoặc bằng {minimum}", minimum=minimum)
    if error_type == "less_than":
        return tr("Giá trị phải nhỏ hơn {maximum}", maximum=context.get("lt", ""))
    if error_type == "less_than_equal":
        maximum = context.get("le", "")
        return tr("Giá trị phải nhỏ hơn hoặc bằng {maximum}", maximum=maximum)
    if error_type == "string_too_short":
        return tr(
            "Nội dung phải có ít nhất {minimum} ký tự",
            minimum=context.get("min_length", ""),
        )
    if error_type == "string_too_long":
        return tr(
            "Nội dung chỉ được tối đa {maximum} ký tự",
            maximum=context.get("max_length", ""),
        )
    if error_type in {"enum", "literal_error"}:
        return tr("Giá trị không nằm trong danh sách được phép")
    return tr("Dữ liệu không hợp lệ. Vui lòng kiểm tra lại")


async def localized_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Financial inbox/reconciliation requests must never echo hostile body or
    # query values through Pydantic's default ``input`` field.  Keep a stable,
    # non-reflective error contract for every I10-C route.
    if request.url.path.startswith(("/api/qr-payments", "/api/qr-reconciliation")):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "QR_RECONCILIATION_REQUEST_INVALID",
                    "message": "QR reconciliation request is invalid",
                }
            },
        )
    errors = jsonable_encoder(exc.errors())
    sensitive_path = request.url.path.endswith(
        ("/manager-pin", "/manager-approvals", "/returns", "/returns/approval")
    )
    for error in errors:
        error["msg"] = _validation_message(error)
        if sensitive_path:
            error.pop("input", None)
    return JSONResponse(status_code=422, content={"detail": errors})


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.schema_ready = False
    report = verify_database_for_startup(db_path)
    app.state.schema_verification = report.as_dict()
    app.state.schema_revision = report.current_revision
    bootstrap.initialize_application_data()

    scheduler = BackgroundScheduler(timezone=zoneinfo.ZoneInfo("UTC"))
    scheduler.add_job(
        cleanup_expired_unverified_users, "interval", minutes=CLEANUP_INTERVAL_MINUTES
    )
    print("[SCHEDULER] Background cleanup task started - runs every 1 minute")

    if ORDER_PENDING_TIMEOUT_MINUTES > 0:
        scheduler.add_job(
            cancel_expired_pending_orders, "interval", minutes=AUTO_CANCEL_INTERVAL_MINUTES
        )
        print(
            f"[SCHEDULER] Auto-cancel of stale PENDING orders is ON "
            f"(timeout {ORDER_PENDING_TIMEOUT_MINUTES} minutes)"
        )
    else:
        print(
            "[SCHEDULER] Auto-cancel of stale PENDING orders is OFF "
            "(set ORDER_PENDING_TIMEOUT_MINUTES to enable)"
        )

    scheduler.start()
    app.state.schema_ready = True

    try:
        yield
    finally:
        app.state.schema_ready = False
        scheduler.shutdown()
        print("[SCHEDULER] Background cleanup task stopped")


def create_app(lifespan_handler=lifespan) -> FastAPI:
    application = FastAPI(title="F-Selling Backend", lifespan=lifespan_handler)
    application.state.schema_ready = False
    application.add_exception_handler(
        RequestValidationError,
        localized_validation_error_handler,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept-Language",
            "X-Offline-Lease-Token",
        ],
    )
    application.add_middleware(LocaleMiddleware)

    @application.get("/api/health/ready", include_in_schema=False)
    async def readiness():
        if not application.state.schema_ready:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "schema_unverified"},
            )
        return {
            "ready": True,
            "revision": application.state.schema_revision,
        }

    @application.middleware("http")
    async def chan_nghiep_vu_khi_schema_chua_xac_minh(request: Request, call_next):
        if request.url.path != "/api/health/ready" and not application.state.schema_ready:
            return JSONResponse(
                status_code=503,
                content={"detail": "Schema chưa được xác minh; nghiệp vụ đang bị khóa"},
            )
        return await call_next(request)

    @application.middleware("http")
    async def khong_giu_cache_html(request, call_next):
        """Bắt trình duyệt hỏi lại server mỗi lần mở trang HTML.

        Vì sao cần: file HTML là nơi chứa mọi dấu `?v=` trỏ tới CSS/JS. FastAPI
        không gửi `Cache-Control` cho file tĩnh, nên trình duyệt tự suy ra thời
        hạn từ `Last-Modified` và có thể phục vụ HTML CŨ mà không hỏi lại —
        khi đó bump `?v=` không có tác dụng gì cả, vì bản HTML cũ vẫn trỏ tới
        số cũ.

        Đây KHÔNG phải lo xa: đã đo được thật khi thêm nút thu nợ. Server phục
        vụ `?v=20260806-thu-no` còn trình duyệt vẫn chạy `?v=20260806-offline-pos`,
        và service worker cũng không cứu được — `fetch()` bên trong nó vẫn đi
        qua HTTP cache của trình duyệt (xem LUẬT 3, bẫy 27).

        `no-cache` KHÔNG phải `no-store`: trình duyệt vẫn giữ bản sao, chỉ phải
        hỏi lại xem có mới hơn không. Có `ETag` nên câu trả lời thường là 304
        rỗng — gần như không tốn băng thông, mà không bao giờ chạy bản cũ.
        """
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @application.middleware("http")
    async def khong_giu_cache_api(request: Request, call_next):
        """API responses are never a source of offline truth or policy bypass."""
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        # I10-B render is authenticated binary output.  Apply nosniff to both
        # success and sanitized error responses; router-local headers alone do
        # not run when a service raises HTTPException.
        if request.url.path.startswith("/api/orders/") and request.url.path.endswith(
            "/qr/render"
        ):
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    application.include_router(auth.router)
    application.include_router(shops.router)
    application.include_router(categories.router)
    application.include_router(products.router)
    application.include_router(suppliers.router)
    application.include_router(purchase_orders.router)
    application.include_router(purchase_receipts.router)
    # webhooks PHẢI đứng trước orders: /api/orders/webhook vs /api/orders/{shop_id}
    application.include_router(webhooks.router)
    application.include_router(qr_reconciliation.router)
    application.include_router(orders.router)
    application.include_router(offline_leases.router)
    application.include_router(offline_capability.router)
    application.include_router(offline_recovery.router)
    application.include_router(shifts.router)
    application.include_router(staff.router)
    application.include_router(subscriptions.router)
    application.include_router(customers.router)
    application.include_router(loyalty.router)
    application.include_router(vouchers.router)
    application.include_router(expenses.router)
    application.include_router(reports.router)
    application.include_router(forecast.router)
    application.include_router(clearance.router)
    application.include_router(assistant.router)
    application.include_router(admin.router)
    application.include_router(tts.router)
    application.include_router(cron.router)
    application.include_router(fnb.router)
    application.include_router(pages.router)

    # Phục vụ ảnh upload từ UPLOAD_DIR (volume) — phải mount trước mount "/"
    application.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
    application.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return application


app = create_app()
