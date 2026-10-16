"""Jaar van de originele uitgave bij historie, wachtrij en rotatie-wijzigingen.

Revision ID: 007
Revises: 006
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELLEN = ("historie", "wachtrij", "rotatie_wijzigingen")


def upgrade() -> None:
    for tabel in TABELLEN:
        op.add_column(tabel, sa.Column("jaar", sa.Integer, nullable=True))


def downgrade() -> None:
    for tabel in TABELLEN:
        with op.batch_alter_table(tabel) as batch:
            batch.drop_column("jaar")
