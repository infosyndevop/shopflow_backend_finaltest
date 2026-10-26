
import re
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
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
    VegetableBillCreate,
    VegetableResponse,
)

router = APIRouter(prefix="/vegetables", tags=["Vegetables"])

CENT = Decimal("0.01")
MILLI = Decimal("0.001")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )


def effective_price(row: VegetableItem) -> Decimal:
    offer = row.offer_price_per_kg

    if offer is not None:
        return money(offer)

    return money(row.price_per_kg)


async def vegetable_response(
    db: AsyncSession,
    row: VegetableItem,
) -> dict:
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
        "status": row.status,
        "created_at": row.created_at,
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=VegetableResponse,
)
async def create_vegetable(
    data: VegetableCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER"])
    ),
):
    """Create a canonical ShopFlow product and its vegetable record."""

    tenant_id = current_user["tenant_id"]
    name = data.name.strip()

    try:
        duplicate_result = await db.execute(
            select(Product.id)
            .join(
                VegetableItem,
                VegetableItem.product_id == Product.id,
            )
            .where(
                Product.tenant_id == tenant_id,
                VegetableItem.tenant_id == tenant_id,
                func.lower(Product.name) == name.lower(),
            )
            .limit(1)
        )

        if duplicate_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail="This vegetable already exists in your shop.",
            )

        product = Product(
            tenant_id=tenant_id,
            category_id=None,
            name=name,
            description="Weight-based vegetable product",
            status="ACTIVE",
        )

        db.add(product)
        await db.flush()

        # Keep the existing rule that weight-based vegetables do not use
        # packaged product variants.
        variant_result = await db.execute(
            select(func.count(ProductVariant.id)).where(
                ProductVariant.product_id == product.id,
                ProductVariant.tenant_id == tenant_id,
            )
        )

        if variant_result.scalar_one() > 0:
            raise HTTPException(
                status_code=409,
                detail="A vegetable cannot use package variants.",
            )

        row = VegetableItem(
            tenant_id=tenant_id,
            product_id=product.id,
            unit=data.unit,
            price_per_kg=data.price_per_kg,
            offer_price_per_kg=data.offer_price_per_kg,
            tax_rate=data.tax_rate,
            stock_kg=data.stock_kg,
            status="ACTIVE",
        )

        db.add(row)
        await db.commit()
        await db.refresh(row)

        return await vegetable_response(db, row)

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("", response_model=list[VegetableResponse])
async def list_vegetables(
    search: str | None = Query(default=None, max_length=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List active vegetables or search them by name."""

    tenant_id = current_user["tenant_id"]

    query = (
        select(VegetableItem)
        .join(
            Product,
            Product.id == VegetableItem.product_id,
        )
        .where(
            VegetableItem.tenant_id == tenant_id,
            Product.tenant_id == tenant_id,
            VegetableItem.status == "ACTIVE",
            Product.status == "ACTIVE",
        )
    )

    if search and search.strip():
        query = query.where(
            Product.name.ilike(f"%{search.strip()}%")
        )

    query = query.order_by(Product.name.asc())

    result = await db.execute(query)
    rows = result.scalars().all()

    return [
        await vegetable_response(db, row)
        for row in rows
    ]


@router.patch(
    "/{vegetable_id}",
    response_model=VegetableResponse,
)
async def update_vegetable(
    vegetable_id: UUID,
    data: VegetableUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(
        require_roles(["OWNER", "MANAGER"])
    ),
):
    """Update pricing, offer price, stock, tax or status."""

    result = await db.execute(
        select(VegetableItem)
        .where(
            VegetableItem.id == vegetable_id,
            VegetableItem.tenant_id == current_user["tenant_id"],
        )
        .with_for_update()
    )

    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(404, "Vegetable not found.")

    changes = data.model_dump(exclude_unset=True)

    regular = changes.get("price_per_kg", row.price_per_kg)

    # Explicit null clears the offer price.
    offer = changes.get(
        "offer_price_per_kg",
        row.offer_price_per_kg,
    )

    if offer is not None and offer > regular:
        raise HTTPException(
            422,
            "Offer price cannot exceed regular price.",
        )

    try:
        for key, value in changes.items():
            setattr(row, key, value)

        await db.commit()
        await db.refresh(row)

        return await vegetable_response(db, row)

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/bills",
    status_code=status.HTTP_201_CREATED,
)
async def create_vegetable_bill(
    data: VegetableBillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a ShopFlow bill using weight-based vegetable items."""

    tenant_id = current_user["tenant_id"]
    user_id = current_user["user_id"]

    method = data.payment_method.upper()
    paid = money(data.paid_amount)
    discount = money(data.discount_amount)

    customer_name = (data.customer_name or "").strip() or None
    phone = re.sub(
        r"[\s()-]",
        "",
        (data.customer_phone or "").strip(),
    ) or None

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

    if discount > ZERO and not (customer_name and phone):
        raise HTTPException(
            400,
            "Customer name and phone are required when a discount is applied.",
        )

    ids = [line.vegetable_id for line in data.items]

    if len(ids) != len(set(ids)):
        raise HTTPException(
            400,
            "Duplicate vegetable lines are not allowed. Combine their weights.",
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

            raw_weight = Decimal(str(line.weight_kg))
            weight = raw_weight.quantize(MILLI)

            if raw_weight != weight or weight <= 0:
                raise HTTPException(
                    400,
                    "Weight must be positive with at most three decimal places.",
                )

            stock = Decimal(str(vegetable.stock_kg))

            if stock < weight:
                raise HTTPException(
                    400,
                    f"Insufficient stock for {product.name}. "
                    f"Available: {stock} kg.",
                )

            unit_price = effective_price(vegetable)
            gross = money(unit_price * weight)

            tax_rate = Decimal(str(vegetable.tax_rate or 0))
            tax = money(
                gross * tax_rate / Decimal("100")
            )

            line_total = money(gross + tax)

            subtotal += gross
            tax_total += tax

            prepared.append(
                {
                    "vegetable": vegetable,
                    "product": product,
                    "weight": weight,
                    "unit_price": unit_price,
                    "gross": gross,
                    "tax_rate": tax_rate,
                    "tax": tax,
                    "line_total": line_total,
                }
            )

        subtotal = money(subtotal)
        tax_total = money(tax_total)

        if discount > subtotal:
            raise HTTPException(
                400,
                "Discount cannot exceed the vegetable subtotal.",
            )

        total = money(subtotal + tax_total - discount)

        if paid > total:
            raise HTTPException(
                400,
                "Paid amount cannot exceed the bill total.",
            )

        if method == "KADAN" and paid != ZERO:
            raise HTTPException(
                400,
                "KADAN payment requires paid_amount to be zero.",
            )

        if method in {"CASH", "UPI"} and total > ZERO and paid <= ZERO:
            raise HTTPException(
                400,
                f"{method} payment requires a positive paid amount.",
            )

        outstanding = money(max(total - paid, ZERO))

        if outstanding > ZERO and not (customer_name and phone):
            raise HTTPException(
                400,
                "Customer name and phone are required for an unpaid balance.",
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

        for item in prepared:
            vegetable = item["vegetable"]

            bill_item = BillItem(
                bill_id=bill.id,
                product_id=item["product"].id,
                product_variant_id=None,
                item_name=item["product"].name,
                quantity=item["weight"],
                unit_price=item["unit_price"],
                offer_price=(
                    vegetable.offer_price_per_kg
                ),
                discount_amount=ZERO,
                tax_rate=item["tax_rate"],
                tax_amount=item["tax"],
                line_total=item["line_total"],
            )

            db.add(bill_item)

            vegetable.stock_kg = (
                Decimal(str(vegetable.stock_kg))
                - item["weight"]
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

            db.add(
                KadanTransaction(
                    tenant_id=tenant_id,
                    kadan_account_id=account.id,
                    bill_id=bill.id,
                    transaction_type="CREDIT",
                    amount=outstanding,
                    balance_after=new_balance,
                    created_by=user_id,
                )
            )

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
                        "vegetable_id": str(item["vegetable"].id),
                        "product_id": str(item["product"].id),
                        "name": item["product"].name,
                        "weight_kg": str(item["weight"]),
                        "regular_price_per_kg": str(
                            item["vegetable"].price_per_kg
                        ),
                        "offer_price_per_kg": (
                            str(item["vegetable"].offer_price_per_kg)
                            if item["vegetable"].offer_price_per_kg is not None
                            else None
                        ),
                        "effective_price_per_kg": str(
                            item["unit_price"]
                        ),
                        "tax_rate": str(item["tax_rate"]),
                        "tax_amount": str(item["tax"]),
                        "line_total": str(item["line_total"]),
                    }
                    for item in prepared
                ],
            },
        }

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
