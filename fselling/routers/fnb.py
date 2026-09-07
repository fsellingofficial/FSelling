from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models
from ..dependencies import get_current_user, get_db
from ..schemas.fnb import (
    FnbAreaCreate,
    FnbAreaUpdate,
    FnbCheckAdjustments,
    FnbCheckPay,
    FnbCheckSplit,
    FnbCheckSplitPreview,
    FnbLineCancel,
    FnbLineCreate,
    FnbLineUpdate,
    FnbMergeTable,
    FnbManagerApprovalCreate,
    FnbManagerPinSet,
    FnbMoveTable,
    FnbSessionCancel,
    FnbSessionClose,
    FnbSessionOpen,
    FnbSessionSend,
    FnbSettingsUpdate,
    FnbStationUpdate,
    FnbTicketTransition,
    FnbTableCreate,
    FnbTableUpdate,
)
from ..services import fnb_service

router = APIRouter(prefix="/api/fnb", tags=["fnb"])


@router.patch("/shops/{shop_id}/settings")
def patch_settings(
    shop_id: int,
    request: FnbSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_fnb_settings(db, current_user, shop_id, request)


@router.patch("/shops/{shop_id}/manager-pin")
def patch_manager_pin(
    shop_id: int,
    request: FnbManagerPinSet,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.set_manager_pin(db, current_user, shop_id, request)


@router.post("/manager-approvals")
def post_manager_approval(
    request: FnbManagerApprovalCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.create_manager_approval(db, current_user, request)


@router.get("/floor")
def floor(
    shop_id: int,
    after_revision: int | None = Query(None, ge=0),
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.get_floor(
        db, current_user, shop_id, after_revision, include_inactive
    )


@router.post("/areas")
def post_area(
    request: FnbAreaCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.create_area(db, current_user, request)


@router.patch("/areas/{area_id}")
def patch_area(
    area_id: int,
    request: FnbAreaUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_area(db, current_user, area_id, request)


@router.post("/tables")
def post_table(
    request: FnbTableCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.create_table(db, current_user, request)


@router.patch("/tables/{table_id}")
def patch_table(
    table_id: int,
    request: FnbTableUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_table(db, current_user, table_id, request)


@router.post("/sessions")
def post_session(
    request: FnbSessionOpen,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.open_session(db, current_user, request)


@router.patch("/menu-items/{product_id}/station")
def patch_station(
    product_id: int,
    request: FnbStationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_product_station(db, current_user, product_id, request)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.get_session(db, current_user, session_id)


@router.get("/sessions/{session_id}/checks")
def get_checks(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.get_checks(db, current_user, session_id)


@router.post("/checks/{check_id}/split-preview")
def post_split_preview(
    check_id: int,
    request: FnbCheckSplitPreview,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.preview_split(db, current_user, check_id, request)


@router.post("/checks/{check_id}/split")
def post_split(
    check_id: int,
    request: FnbCheckSplit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.split_check(db, current_user, check_id, request)


@router.patch("/checks/{check_id}/adjustments")
def patch_check_adjustments(
    check_id: int,
    request: FnbCheckAdjustments,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_check_adjustments(db, current_user, check_id, request)


@router.get("/checks/{check_id}/provisional-receipt")
def get_provisional_receipt(
    check_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.get_provisional_receipt(db, current_user, check_id)


@router.post("/checks/{check_id}/pay")
def post_check_pay(
    check_id: int,
    request: FnbCheckPay,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.pay_check(db, current_user, check_id, request)


@router.post("/sessions/{session_id}/close")
def post_session_close(
    session_id: int,
    request: FnbSessionClose,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.close_session(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/lines")
def post_line(
    session_id: int,
    request: FnbLineCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.add_line(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/send")
def post_send(
    session_id: int,
    request: FnbSessionSend,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.send_session(db, current_user, session_id, request)


@router.get("/stations/{station}/tickets")
def get_station_tickets(
    station: str,
    shop_id: int,
    after_revision: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.get_station_tickets(
        db, current_user, shop_id, station, after_revision
    )


@router.post("/tickets/{ticket_id}/start")
def post_ticket_start(
    ticket_id: int,
    request: FnbTicketTransition,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.transition_ticket(
        db, current_user, ticket_id, "start", request
    )


@router.post("/tickets/{ticket_id}/done")
def post_ticket_done(
    ticket_id: int,
    request: FnbTicketTransition,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.transition_ticket(
        db, current_user, ticket_id, "done", request
    )


@router.post("/tickets/{ticket_id}/out-of-stock")
def post_ticket_out_of_stock(
    ticket_id: int,
    request: FnbTicketTransition,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.transition_ticket(
        db, current_user, ticket_id, "out-of-stock", request
    )


@router.post("/tickets/{ticket_id}/resume")
def post_ticket_resume(
    ticket_id: int,
    request: FnbTicketTransition,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.transition_ticket(
        db, current_user, ticket_id, "resume", request
    )


@router.post("/tickets/{ticket_id}/serve")
def post_ticket_serve(
    ticket_id: int,
    request: FnbTicketTransition,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.serve_ticket(db, current_user, ticket_id, request)


@router.patch("/lines/{line_id}")
def patch_line(
    line_id: int,
    request: FnbLineUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.update_line(db, current_user, line_id, request)


@router.post("/sessions/{session_id}/cancel-line")
def post_cancel_line(
    session_id: int,
    request: FnbLineCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.cancel_line(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/move-table")
def post_move_table(
    session_id: int,
    request: FnbMoveTable,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.move_table(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/merge-table")
def post_merge_table(
    session_id: int,
    request: FnbMergeTable,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.merge_table(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/cancel")
def post_cancel_session(
    session_id: int,
    request: FnbSessionCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return fnb_service.cancel_session(db, current_user, session_id, request)
