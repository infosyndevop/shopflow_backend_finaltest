
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.vegetable_item import VegetableItem
from app.models.bill import Bill
from app.models.bill_item import BillItem
from app.models.payment import Payment
from app.models.kadan_account import KadanAccount
from app.models.kadan_transaction import KadanTransaction
from app.repositories.bill_repository import bill_repository
from app.schemas.vegetable import (
    VegetableCreate,
    VegetableUpdate,
    VegetableQuickUpdate,
    VegetableBillCreate,
    VegetableResponse,
)

router = APIRouter(prefix="/vegetables", tags=["Vegetables"])

CENT = Decimal("0.01")
THREE_DP = Decimal("0.001")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def effective_price(vegetable: VegetableItem) -> Decimal:
    """Use the offer price when present; otherwise use regular price."""
    if vegetable.offer_price_per_kg is not None:
        return Decimal(str(vegetable.offer_price_per_kg))
    return Decimal(str(vegetable.price_per_kg))


async def vegetable_response(db, row: VegetableItem) -> dict:
    product = await db.get(Product, row.product_id)

    return {
        "id": row.id,
        "product_id": row.product_id,
        "name": product.name if product else "Unknown product",
        "unit": row.unit,
        "price_per_kg": row.price_per_kg,
        "offer_price_per_kg": row.offer_price_per_kg,
        "effective_price_per_kg": effective_price(row),
        "tax_rate": row.tax_rate,
        "stock_kg": row.stock_kg,
        "low_stock_threshold_kg": row.low_stock_threshold_kg,
        "is_low_stock": (
            Decimal(str(row.stock_kg))
            <= Decimal(str(row.low_stock_threshold_kg))
        ),
        "status": row.status,
        "created_at": row.created_at,
    }


async def get_tenant_vegetable(
    db: AsyncSession,
    tenant_id,
    vegetable_id: UUID,
    *,
    lock: bool = False,
):
    query = select(VegetableItem).where(
        VegetableItem.id == vegetable_id,
        VegetableItem.tenant_id == tenant_id,
    )

    if lock:
        query = query.with_for_update()

    result = await db.execute(query)
    return result.scalar_one_or_none()


# ---------------------------------------------------------
# CREATE VEGETABLE
# POST /api/v1/vegetables
# ---------------------------------------------------------

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=VegetableResponse,
)
async def create_vegetable(
    data: VegetableCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER","EMPLOYEE"])
    ),
):
    tenant_id = current_user["tenant_id"]
    name = data.name.strip()

    if not name:
        raise HTTPException(422, "Vegetable name cannot be empty.")

    if (
        data.offer_price_per_kg is not None
        and data.offer_price_per_kg > data.price_per_kg
    ):
        raise HTTPException(
            422,
            "Offer price cannot exceed regular price.",
        )

    try:
        # Prevent duplicate vegetable names within this shop.
        existing_result = await db.execute(
            select(Product)
            .join(
                VegetableItem,
                VegetableItem.product_id == Product.id,
            )
            .where(
                VegetableItem.tenant_id == tenant_id,
                Product.tenant_id == tenant_id,
                func.lower(Product.name) == name.lower(),
            )
        )

        if existing_result.scalars().first() is not None:
            raise HTTPException(
                409,
                "A vegetable with this name already exists.",
            )

        # Do not reuse a standard product or a product with variants.
        # Vegetables have their own weight-based stock and pricing.
        product = Product(
            tenant_id=tenant_id,
            name=name,
            description="Weight-based vegetable",
            status="ACTIVE",
        )
        db.add(product)
        await db.flush()

        vegetable = VegetableItem(
            tenant_id=tenant_id,
            product_id=product.id,
            unit=data.unit,
            price_per_kg=data.price_per_kg,
            offer_price_per_kg=data.offer_price_per_kg,
            tax_rate=data.tax_rate,
            stock_kg=data.stock_kg,
            low_stock_threshold_kg=data.low_stock_threshold_kg,
            status="ACTIVE",
        )

        db.add(vegetable)
        await db.commit()
        await db.refresh(vegetable)

        return await vegetable_response(db, vegetable)

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# LIST / SEARCH VEGETABLES
# GET /api/v1/vegetables
# GET /api/v1/vegetables?search=tomato
# ---------------------------------------------------------

@router.get(
    "",
    response_model=list[VegetableResponse],
)
async def list_vegetables(
    search: str | None = Query(default=None, max_length=200),
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = (
        select(VegetableItem)
        .join(
            Product,
            Product.id == VegetableItem.product_id,
        )
        .where(
            VegetableItem.tenant_id == current_user["tenant_id"],
            Product.tenant_id == current_user["tenant_id"],
        )
    )

    if active_only:
        query = query.where(
            VegetableItem.status == "ACTIVE",
            Product.status == "ACTIVE",
        )

    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(Product.name.ilike(pattern))

    query = query.order_by(VegetableItem.created_at.desc())

    result = await db.execute(query)
    rows = result.scalars().all()

    return [
        await vegetable_response(db, row)
        for row in rows
    ]


# ---------------------------------------------------------
# GET ONE VEGETABLE
# GET /api/v1/vegetables/{vegetable_id}
# ---------------------------------------------------------

@router.get(
    "/{vegetable_id}",
    response_model=VegetableResponse,
)
async def get_vegetable(
    vegetable_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = await get_tenant_vegetable(
        db,
        current_user["tenant_id"],
        vegetable_id,
    )

    if row is None:
        raise HTTPException(404, "Vegetable not found.")

    return await vegetable_response(db, row)


# ---------------------------------------------------------
# LOW-STOCK VEGETABLES
# GET /api/v1/vegetables/low-stock
# ---------------------------------------------------------

@router.get(
    "/low-stock",
    response_model=list[VegetableResponse],
)
async def get_low_stock_vegetables(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(VegetableItem)
        .join(
            Product,
            Product.id == VegetableItem.product_id,
        )
        .where(
            VegetableItem.tenant_id == current_user["tenant_id"],
            Product.tenant_id == current_user["tenant_id"],
            VegetableItem.status == "ACTIVE",
            Product.status == "ACTIVE",
            VegetableItem.stock_kg
            <= VegetableItem.low_stock_threshold_kg,
        )
        .order_by(VegetableItem.stock_kg.asc())
    )

    rows = result.scalars().all()

    return [
        await vegetable_response(db, row)
        for row in rows
    ]


# ---------------------------------------------------------
# FULL-DETAIL EDIT
# PUT /api/v1/vegetables/{vegetable_id}
# ---------------------------------------------------------

@router.put(
    "/{vegetable_id}",
    response_model=VegetableResponse,
)
async def edit_vegetable(
    vegetable_id: UUID,
    data: VegetableUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER"])
    ),
):
    row = await get_tenant_vegetable(
        db,
        current_user["tenant_id"],
        vegetable_id,
        lock=True,
    )

    if row is None:
        raise HTTPException(404, "Vegetable not found.")

    product = await db.get(Product, row.product_id)

    if product is None or product.tenant_id != current_user["tenant_id"]:
        raise HTTPException(404, "Linked product not found.")

    changes = data.model_dump(exclude_unset=True)

    # Explicit null removes an offer price.
    if "offer_price_per_kg" in changes:
        row.offer_price_per_kg = changes.pop(
            "offer_price_per_kg"
        )

    if "name" in changes:
        name = changes.pop("name")
        if name is not None:
            name = name.strip()

            duplicate_result = await db.execute(
                select(Product.id)
                .join(
                    VegetableItem,
                    VegetableItem.product_id == Product.id,
                )
                .where(
                    VegetableItem.tenant_id == current_user["tenant_id"],
                    Product.tenant_id == current_user["tenant_id"],
                    Product.id != product.id,
                    func.lower(Product.name) == name.lower(),
                )
                .limit(1)
            )

            if duplicate_result.scalar_one_or_none() is not None:
                raise HTTPException(
                    409,
                    "Another vegetable already uses this name.",
                )

            product.name = name

    for field, value in changes.items():
        if value is not None:
            setattr(row, field, value)

    if (
        row.offer_price_per_kg is not None
        and row.offer_price_per_kg > row.price_per_kg
    ):
        await db.rollback()
        raise HTTPException(
            422,
            "Offer price cannot exceed regular price.",
        )

    try:
        await db.commit()
        await db.refresh(row)
        return await vegetable_response(db, row)
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# QUICK UPDATE
# PATCH /api/v1/vegetables/{vegetable_id}/quick-update
# ---------------------------------------------------------

@router.patch(
    "/{vegetable_id}/quick-update",
    response_model=VegetableResponse,
)
async def quick_update_vegetable(
    vegetable_id: UUID,
    data: VegetableQuickUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER","EMPLOYEE"])
    ),
):
    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            422,
            "Provide at least one field to update.",
        )

    row = await get_tenant_vegetable(
        db,
        current_user["tenant_id"],
        vegetable_id,
        lock=True,
    )

    if row is None:
        raise HTTPException(404, "Vegetable not found.")

    for field, value in changes.items():
        # null is allowed to clear an offer price only.
        if field == "offer_price_per_kg":
            row.offer_price_per_kg = value
        elif value is not None:
            setattr(row, field, value)

    if (
        row.offer_price_per_kg is not None
        and row.offer_price_per_kg > row.price_per_kg
    ):
        await db.rollback()
        raise HTTPException(
            422,
            "Offer price cannot exceed regular price.",
        )

    try:
        await db.commit()
        await db.refresh(row)
        return await vegetable_response(db, row)
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# PARTIAL UPDATE (BACKWARD COMPATIBILITY)
# PATCH /api/v1/vegetables/{vegetable_id}
# ---------------------------------------------------------

@router.patch(
    "/{vegetable_id}",
    response_model=VegetableResponse,
)
async def update_vegetable(
    vegetable_id: UUID,
    data: VegetableUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER","EMPLOYEE"])
    ),
):
    row = await get_tenant_vegetable(
        db,
        current_user["tenant_id"],
        vegetable_id,
        lock=True,
    )

    if row is None:
        raise HTTPException(404, "Vegetable not found.")

    changes = data.model_dump(exclude_unset=True)

    if "name" in changes:
        # Product name lives on the linked canonical product.
        name = changes.pop("name")
        if name is not None:
            product = await db.get(Product, row.product_id)
            if product is None:
                raise HTTPException(404, "Linked product not found.")
            product.name = name.strip()

    for field, value in changes.items():
        if field == "offer_price_per_kg":
            row.offer_price_per_kg = value
        elif value is not None:
            setattr(row, field, value)

    if (
        row.offer_price_per_kg is not None
        and row.offer_price_per_kg > row.price_per_kg
    ):
        await db.rollback()
        raise HTTPException(
            422,
            "Offer price cannot exceed regular price.",
        )

    try:
        await db.commit()
        await db.refresh(row)
        return await vegetable_response(db, row)
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# CREATE VEGETABLE BILL
# POST /api/v1/vegetables/bills
#
# Uses the existing ShopFlow Bill, BillItem, Payment, and
# Kadan tables/repository.
# ---------------------------------------------------------

@router.post(
    "/bills",
    status_code=status.HTTP_201_CREATED,
)
async def create_vegetable_bill(
    data: VegetableBillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    tenant_id = current_user["tenant_id"]
    user_id = current_user["user_id"]

    method = data.payment_method.strip().upper()
    paid = money(data.paid_amount)
    discount = money(data.discount_amount)

    customer_name = (data.customer_name or "").strip() or None
    phone = (
        (data.customer_phone or "")
        .strip()
        .replace(" ", "")
        .replace("-", "")
        or None
    )

    if phone and phone.startswith("+91"):
        phone = phone[3:]

    if bool(customer_name) != bool(phone):
        raise HTTPException(
            400,
            "Customer name and phone must be provided together.",
        )

    if phone and not re.fullmatch(r"[6-9][0-9]{9}", phone):
        raise HTTPException(
            400,
            "A valid 10-digit Indian phone number is required.",
        )

    if discount > ZERO and (not customer_name or not phone):
        raise HTTPException(
            400,
            "Customer name and phone are required when a discount is applied.",
        )

    if method not in {"CASH", "UPI", "KADAN"}:
        raise HTTPException(
            400,
            "Payment method must be CASH, UPI, or KADAN.",
        )

    ids = [line.vegetable_id for line in data.items]

    if len(ids) != len(set(ids)):
        raise HTTPException(
            400,
            "Duplicate vegetable lines are not allowed; combine their weight.",
        )

    try:
        result = await db.execute(
            select(VegetableItem, Product)
            .join(
                Product,
                Product.id == VegetableItem.product_id,
            )
            .where(
                VegetableItem.id.in_(ids),
                VegetableItem.tenant_id == tenant_id,
                VegetableItem.status == "ACTIVE",
                Product.tenant_id == tenant_id,
                Product.status == "ACTIVE",
            )
            .with_for_update()
        )

        records = result.all()

        if len(records) != len(ids):
            raise HTTPException(
                400,
                "One or more vegetables were not found or are inactive.",
            )

        by_id = {
            vegetable.id: (vegetable, product)
            for vegetable, product in records
        }

        subtotal = ZERO
        tax_total = ZERO
        prepared = []

        for line in data.items:
            vegetable, product = by_id[line.vegetable_id]

            weight = Decimal(str(line.weight_kg)).quantize(
                THREE_DP,
                rounding=ROUND_HALF_UP,
            )

            if weight <= 0:
                raise HTTPException(
                    400,
                    "Vegetable weight must be greater than zero.",
                )

            stock = Decimal(str(vegetable.stock_kg))

            if stock < weight:
                raise HTTPException(
                    400,
                    f"Insufficient stock for {product.name}. "
                    f"Available: {stock} kg.",
                )

            # IMPORTANT: charge the offer price when configured.
            price = effective_price(vegetable)
            gross = money(price * weight)

            rate = Decimal(str(vegetable.tax_rate or 0))
            tax = money(gross * rate / Decimal("100"))
            line_total = money(gross + tax)

            subtotal += gross
            tax_total += tax

            prepared.append(
                (
                    vegetable,
                    product,
                    weight,
                    price,
                    gross,
                    rate,
                    tax,
                    line_total,
                )
            )

        subtotal = money(subtotal)
        tax_total = money(tax_total)

        if discount > subtotal:
            raise HTTPException(
                400,
                "Discount cannot exceed vegetable subtotal.",
            )

        total = money(subtotal + tax_total - discount)

        if paid > total:
            raise HTTPException(
                400,
                "Paid amount cannot exceed final bill total.",
            )

        if method == "KADAN" and paid != ZERO:
            raise HTTPException(
                400,
                "KADAN payment requires paid_amount to be 0.",
            )

        if method in {"CASH", "UPI"} and total > ZERO and paid <= ZERO:
            raise HTTPException(
                400,
                f"{method} payment requires a positive paid amount.",
            )

        outstanding = money(max(total - paid, ZERO))

        if outstanding > ZERO and (not customer_name or not phone):
            raise HTTPException(
                400,
                f"Unpaid balance ₹{outstanding:.2f}; "
                "customer name and phone are required for Kadan.",
            )

        customer = None

        if phone:
            customer = await bill_repository.get_customer_by_phone(
                db,
                tenant_id,
                phone,
            )

            if customer is None:
                customer = await bill_repository.create_customer(
                    db,
                    tenant_id,
                    customer_name,
                    phone,
                )

        latest = await bill_repository.get_latest_bill_number(
            db,
            tenant_id,
        )

        bill_number = f"BILL-{(latest or 0) + 1:06d}"

        bill = Bill(
            tenant_id=tenant_id,
            customer_id=customer.id if customer else None,
            bill_number=bill_number,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax_total,
            total_amount=total,
            status="COMPLETED",
            created_by=user_id,
        )

        await bill_repository.create_bill(db, bill)

        for (
            vegetable,
            product,
            weight,
            price,
            gross,
            rate,
            tax,
            line_total,
        ) in prepared:
            db.add(
                BillItem(
                    bill_id=bill.id,
                    product_id=product.id,
                    product_variant_id=None,
                    item_name=product.name,
                    quantity=weight,
                    unit_price=vegetable.price_per_kg,
                    offer_price=(
                        vegetable.offer_price_per_kg
                        if vegetable.offer_price_per_kg is not None
                        else None
                    ),
                    discount_amount=ZERO,
                    tax_rate=rate,
                    tax_amount=tax,
                    line_total=line_total,
                )
            )

            # Reduce the weight-based stock inside this transaction.
            vegetable.stock_kg = (
                Decimal(str(vegetable.stock_kg)) - weight
            )

        if paid > ZERO:
            await bill_repository.create_payment(
                db,
                Payment(
                    tenant_id=tenant_id,
                    bill_id=bill.id,
                    amount=paid,
                    payment_method=method,
                    status="COMPLETED",
                    created_by=user_id,
                ),
            )

        kadan_summary = None

        if outstanding > ZERO:
            account = await bill_repository.get_kadan_account(
                db,
                tenant_id,
                customer.id,
                for_update=True,
            )

            if account is None:
                account = KadanAccount(
                    tenant_id=tenant_id,
                    customer_id=customer.id,
                    outstanding_amount=ZERO,
                    status="ACTIVE",
                )
                db.add(account)
                await db.flush()

            new_balance = money(
                Decimal(str(account.outstanding_amount or ZERO))
                + outstanding
            )

            account.outstanding_amount = new_balance
            account.status = "ACTIVE"

            transaction = KadanTransaction(
                tenant_id=tenant_id,
                kadan_account_id=account.id,
                bill_id=bill.id,
                transaction_type="CREDIT",
                amount=outstanding,
                balance_after=new_balance,
                created_by=user_id,
            )

            db.add(transaction)

            kadan_summary = {
                "kadan_amount": str(outstanding),
                "outstanding_amount": str(new_balance),
            }

        await db.commit()
        await db.refresh(bill)

        return {
            "type": "success",
            "message": "Vegetable bill created successfully",
            "data": {
                "bill_id": str(bill.id),
                "bill_number": bill.bill_number,
                "subtotal": str(subtotal),
                "tax_amount": str(tax_total),
                "discount_amount": str(discount),
                "total_amount": str(total),
                "paid_amount": str(paid),
                "kadan_amount": str(outstanding),
                "payment_method": method,
                "kadan": kadan_summary,
                "items": [
                    {
                        "vegetable_id": str(vegetable.id),
                        "product_id": str(product.id),
                        "name": product.name,
                        "weight_kg": str(weight),
                        "regular_price_per_kg": str(
                            vegetable.price_per_kg
                        ),
                        "offer_price_per_kg": (
                            str(vegetable.offer_price_per_kg)
                            if vegetable.offer_price_per_kg is not None
                            else None
                        ),
                        "charged_price_per_kg": str(price),
                        "tax_rate": str(rate),
                        "tax_amount": str(tax),
                        "line_total": str(line_total),
                    }
                    for (
                        vegetable,
                        product,
                        weight,
                        price,
                        gross,
                        rate,
                        tax,
                        line_total,
                    ) in prepared
                ],
            },
        }

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
