
"""Add optional offer price to vegetable items.

Revision ID: c8d9e0f1a2b3
Revises: b4e1f2a9c3d7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b4e1f2a9c3d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vegetable_items",
        sa.Column(
            "offer_price_per_kg",
            sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "vegetable_items",
        "offer_price_per_kg",
    )
