"""Aantal blokken dat per rotatie wisselt.

Revision ID: 003
Revises: 002
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wissellijsten",
                  sa.Column("blokken_per_rotatie", sa.Integer, nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("wissellijsten") as batch:
        batch.drop_column("blokken_per_rotatie")
