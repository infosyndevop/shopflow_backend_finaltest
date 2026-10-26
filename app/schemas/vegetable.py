from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class VegetableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    price_per_kg: Decimal = Field(
        gt=0, max_digits=12, decimal_places=2
    )

    offer_price_per_kg: Optional[Decimal] = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )

    tax_rate: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
    )

    stock_kg: Decimal = Field(
        default=Decimal("0.000"),
        ge=0,
        max_digits=12,
        decimal_places=3,
    )

    low_stock_threshold_kg: Decimal = Field(
        default=Decimal("5.000"),
        ge=0,
        max_digits=12,
        decimal_places=3,
    )

    unit: str = Field(default="kg", min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Vegetable name cannot be empty")
        return value

    @field_validator("unit")
    @classmethod
    def clean_unit(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"kg", "g"}:
            raise ValueError("unit must be kg or g")
        return value

    @model_validator(mode="after")
    def validate_offer(self):
        if (
            self.offer_price_per_kg is not None
            and self.offer_price_per_kg > self.price_per_kg
        ):
            raise ValueError(
                "Offer price cannot exceed regular price"
            )
        return self


class VegetableUpdate(BaseModel):
    """Full-detail update. Omitted fields retain their current values."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    price_per_kg: Optional[Decimal] = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    offer_price_per_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    tax_rate: Optional[Decimal] = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    stock_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=3
    )
    low_stock_threshold_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=3
    )
    unit: Optional[str] = Field(default=None, min_length=1, max_length=20)
    status: Optional[str] = Field(
        default=None, pattern="^(ACTIVE|INACTIVE)$"
    )

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Vegetable name cannot be empty")
        return value

    @field_validator("unit")
    @classmethod
    def clean_unit(cls, value):
        if value is None:
            return value
        value = value.strip().lower()
        if value not in {"kg", "g"}:
            raise ValueError("unit must be kg or g")
        return value


class VegetableQuickUpdate(BaseModel):
    """Only the supplied price and stock fields are updated."""

    price_per_kg: Optional[Decimal] = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    offer_price_per_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    stock_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=3
    )
    low_stock_threshold_kg: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=12, decimal_places=3
    )


class VegetableResponse(BaseModel):
    id: UUID
    product_id: UUID
    name: str
    unit: str
    price_per_kg: Decimal
    offer_price_per_kg: Optional[Decimal] = None
    effective_price_per_kg: Decimal
    tax_rate: Decimal
    stock_kg: Decimal
    low_stock_threshold_kg: Decimal
    is_low_stock: bool
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# Keep these schemas if your existing vegetable billing endpoint uses them.

class VegetableBillLine(BaseModel):
    vegetable_id: UUID
    weight_kg: Decimal = Field(
        gt=0, max_digits=12, decimal_places=3
    )


class VegetableBillCreate(BaseModel):
    items: list[VegetableBillLine] = Field(min_length=1)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    paid_amount: Decimal = Field(ge=0)
    payment_method: str = Field(min_length=1, max_length=20)
    customer_name: Optional[str] = Field(default=None, max_length=150)
    customer_phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("payment_method")
    @classmethod
    def normalize_payment_method(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"CASH", "UPI", "KADAN"}:
            raise ValueError(
                "payment_method must be CASH, UPI, or KADAN"
            )
        return value