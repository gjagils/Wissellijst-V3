"""Rotatieteller per wissellijst opnieuw laten beginnen na herstarten.

Revision ID: 009
Revises: 008
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wissellijsten",
                  sa.Column("rotatie_teller_vanaf", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("wissellijsten") as batch:
        batch.drop_column("rotatie_teller_vanaf")
