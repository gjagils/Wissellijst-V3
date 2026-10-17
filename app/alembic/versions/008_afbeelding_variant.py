"""Variant (strak of AI-afwerking) en opmerking bij Instagram-plaatjes.

Revision ID: 008
Revises: 007
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rotatie_afbeeldingen",
                  sa.Column("variant", sa.String(10), nullable=False, server_default="strak"))
    op.add_column("rotatie_afbeeldingen", sa.Column("opmerking", sa.Text, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("rotatie_afbeeldingen") as batch:
        batch.drop_column("opmerking")
        batch.drop_column("variant")
