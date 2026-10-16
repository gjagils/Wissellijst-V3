"""Populariteitsverdeling per wissellijst aan/uit.

Revision ID: 004
Revises: 003
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wissellijsten",
                  sa.Column("populariteit_verdeling", sa.Boolean, nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("wissellijsten") as batch:
        batch.drop_column("populariteit_verdeling")
