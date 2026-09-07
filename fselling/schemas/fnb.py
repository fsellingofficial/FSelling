from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..core.numeric_limits import MAX_SAFE_QUANTITY, MAX_SAFE_VND
from .money import ExactVND

OperationId = str


class FnbRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FnbSettingsUpdate(FnbRequest):
    enabled: bool
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbAreaCreate(FnbRequest):
    shop_id: int
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=MAX_SAFE_QUANTITY)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbAreaUpdate(FnbRequest):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    sort_order: Optional[int] = Field(default=None, ge=0, le=MAX_SAFE_QUANTITY)
    active: Optional[bool] = None
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbTableCreate(FnbRequest):
    shop_id: int
    area_id: int
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=MAX_SAFE_QUANTITY)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbTableUpdate(FnbRequest):
    area_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    sort_order: Optional[int] = Field(default=None, ge=0, le=MAX_SAFE_QUANTITY)
    active: Optional[bool] = None
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_state_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbSessionOpen(FnbRequest):
    shop_id: int
    table_id: int
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_table_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbLineCreate(FnbRequest):
    product_id: int
    quantity: int = Field(gt=0, le=MAX_SAFE_QUANTITY)
    note: Optional[str] = Field(default=None, max_length=500)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbLineUpdate(FnbRequest):
    quantity: int = Field(gt=0, le=MAX_SAFE_QUANTITY)
    note: Optional[str] = Field(default=None, max_length=500)
    expected_line_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbStationUpdate(FnbRequest):
    station: Literal["KITCHEN", "BAR", "DIRECT"]
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbSessionSend(FnbRequest):
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbTicketTransition(FnbRequest):
    expected_state_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_session_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=500)


class FnbLineCancel(FnbRequest):
    line_id: int
    quantity: int = Field(gt=0, le=MAX_SAFE_QUANTITY)
    expected_line_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)
    resolution: Optional[Literal["RESTOCK", "WASTE"]] = None
    reason: Optional[str] = Field(default=None, max_length=500)
    approval_token: Optional[str] = Field(default=None, min_length=32, max_length=256)


class FnbManagerPinSet(FnbRequest):
    pin: str = Field(pattern=r"^\d{4,6}$")


class FnbManagerApprovalCreate(FnbRequest):
    shop_id: int
    approver_username: str = Field(min_length=1, max_length=100)
    pin: str = Field(pattern=r"^\d{4,6}$")
    action: Literal["CANCEL_SENT_LINE"]
    entity_type: Literal["SESSION"]
    entity_id: int
    revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)


class FnbMoveTable(FnbRequest):
    from_table_id: int
    to_table_id: int
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_from_state_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_to_state_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbMergeTable(FnbRequest):
    target_table_id: int
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_target_session_revision: Optional[int] = Field(
        default=None, ge=0, le=MAX_SAFE_QUANTITY
    )
    expected_target_table_version: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbSessionCancel(FnbRequest):
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    reason: Optional[str] = Field(default=None, max_length=500)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbCheckLineSelection(FnbRequest):
    line_id: int
    quantity: int = Field(gt=0, le=MAX_SAFE_QUANTITY)


class FnbCheckSplitPreview(FnbRequest):
    lines: list[FnbCheckLineSelection] = Field(min_length=1)


class FnbCheckSplit(FnbCheckSplitPreview):
    label: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_session_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbCheckAdjustments(FnbRequest):
    discount_kind: Literal["NONE", "FLAT", "PERCENT"] = "NONE"
    discount_value: int = Field(default=0, ge=0, le=MAX_SAFE_VND)
    service_charge_kind: Literal["NONE", "FLAT", "PERCENT"] = "NONE"
    service_charge_value: int = Field(default=0, ge=0, le=MAX_SAFE_VND)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_session_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbCheckPay(FnbRequest):
    payment_method: Literal["cash", "transfer", "debt"]
    customer_id: Optional[int] = None
    voucher_code: Optional[str] = Field(default=None, max_length=100)
    loyalty_points_to_use: int = Field(default=0, ge=0, le=MAX_SAFE_QUANTITY)
    cash_tendered_vnd: Optional[ExactVND] = Field(default=None, ge=0, le=MAX_SAFE_VND)
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    expected_session_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)


class FnbSessionClose(FnbRequest):
    expected_revision: int = Field(ge=0, le=MAX_SAFE_QUANTITY)
    operation_id: OperationId = Field(min_length=8, max_length=128)
