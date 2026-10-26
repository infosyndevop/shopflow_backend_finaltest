
"""Add vegetable offer price and low-stock threshold."""

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b4e1f2a9c3d7"
branch_labels = None
depends_on = None


def upgrade():
    # offer_price_per_kg is added by migration c8d9e0f1a2b3.
    # Add only the missing low-stock threshold column.
    op.add_column(
        "vegetable_items",
        sa.Column(
            "low_stock_threshold_kg",
            sa.Numeric(12, 3),
            nullable=False,
            server_default="5.000",
        ),
    )


def downgrade():
    op.drop_column(
        "vegetable_items",
        "low_stock_threshold_kg",
    )
