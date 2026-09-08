from typing import Literal

from pydantic import BaseModel

StaffRole = Literal["CASHIER", "SERVICE", "WAREHOUSE", "MANAGER", "KITCHEN", "BAR"]


class StaffCreate(BaseModel):
    username: str
    password: str
    # MANAGER giữ nguyên tập quyền STAFF trước khi có RBAC.
    staff_role: StaffRole = "MANAGER"


class StaffRoleUpdate(BaseModel):
    staff_role: StaffRole


class StaffOut(BaseModel):
    id: int
    username: str
    shop_id: int
    is_active: bool
    staff_role: StaffRole
