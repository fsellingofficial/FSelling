"""Router quản lý nhân viên. Chỉ chủ shop thao tác (service kiểm require_own_shop)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..dependencies import get_current_user, get_db
from ..schemas.staff import StaffCreate, StaffRoleUpdate
from ..schemas.auth import AuthDeviceRevoke
from ..services import staff_service

router = APIRouter(prefix="/api/staff", tags=["staff"])


class ResetPasswordBody(BaseModel):
    new_password: str


@router.post("/{shop_id}")
def create_staff(
    shop_id: int,
    data: StaffCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.create_staff(db, current_user, shop_id, data)


@router.get("/{shop_id}")
def list_staff(
    shop_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.list_staff(db, current_user, shop_id)


@router.delete("/member/{staff_id}")
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.delete_staff(db, current_user, staff_id)


@router.put("/member/{staff_id}/password")
def reset_staff_password(
    staff_id: int,
    body: ResetPasswordBody,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.reset_staff_password(db, current_user, staff_id, body.new_password)


@router.put("/member/{staff_id}/role")
def update_staff_role(
    staff_id: int,
    body: StaffRoleUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.update_staff_role(db, current_user, staff_id, body)


@router.get("/member/{staff_id}/sessions")
def list_staff_sessions(
    staff_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.list_staff_sessions(db, current_user, staff_id)


@router.delete("/member/{staff_id}/sessions/{session_id}")
def revoke_staff_session(
    staff_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.revoke_staff_session(
        db, current_user, staff_id, session_id
    )


@router.post("/member/{staff_id}/devices/revoke")
def revoke_staff_device(
    staff_id: int,
    data: AuthDeviceRevoke,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return staff_service.revoke_staff_device(
        db, current_user, staff_id, data.device_id
    )
