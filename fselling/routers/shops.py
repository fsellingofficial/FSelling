from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models
from ..dependencies import get_current_user, get_db
from ..schemas.shop import ManagerPinSet, ShopCreate
from ..services import report_service, shop_service

router = APIRouter(prefix="/api/shops", tags=["shops"])


@router.patch("/{shop_id}/manager-pin")
def patch_manager_pin(
    shop_id: int,
    request: ManagerPinSet,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.set_manager_pin(db, current_user, shop_id, request)


@router.post("")
def create_shop(
    shop: ShopCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.create_shop(db, current_user, shop)


@router.get("")
def get_shops(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.list_shops(db, current_user)


@router.put("/{shop_id}")
def update_shop(
    shop_id: int,
    shop: ShopCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.update_shop(db, current_user, shop_id, shop)


@router.put("/{shop_id}/status")
def toggle_shop_status(
    shop_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.toggle_shop_status(db, current_user, shop_id)


@router.delete("/{shop_id}")
def delete_shop(
    shop_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return shop_service.delete_shop(db, current_user, shop_id)


@router.get("/{shop_id}/stats")
def get_shop_stats(
    shop_id: int,
    tu_ngay: Optional[str] = Query(None, description="Lọc từ ngày (YYYY-MM-DD)"),
    den_ngay: Optional[str] = Query(None, description="Lọc đến ngày (YYYY-MM-DD)"),
    contract_version: int = Query(1, ge=1, le=2),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return report_service.shop_stats(
        db, current_user, shop_id, tu_ngay=tu_ngay, den_ngay=den_ngay,
        contract_version=contract_version,
    )
