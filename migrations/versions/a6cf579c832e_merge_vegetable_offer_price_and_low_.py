"""merge vegetable offer price and low stock migrations

Revision ID: a6cf579c832e
Revises: c8d9e0f1a2b3, c9d0e1f2a3b4
Create Date: 2026-10-26 21:03:59.928633
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision: str = 'a6cf579c832e'
down_revision: Union[str, Sequence[str], None] = ('c8d9e0f1a2b3', 'c9d0e1f2a3b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    pass


def downgrade() -> None:
    """Downgrade database schema."""
    pass