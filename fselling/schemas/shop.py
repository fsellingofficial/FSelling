from typing import Optional

from pydantic import BaseModel, Field


class ShopCreate(BaseModel):
    name: str
    business_address: Optional[str] = None
    tax_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_code: Optional[str] = None


class ManagerPinSet(BaseModel):
    pin: str = Field(pattern=r"^\d{4,6}$")
