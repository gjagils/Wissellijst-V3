"""Instagram-plaatjes per rotatie.

Revision ID: 006
Revises: 005
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rotatie_afbeeldingen",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("rotatie_runs.id"), nullable=False),
        sa.Column("richting", sa.String(3), nullable=False),
        sa.Column("deel", sa.Integer, server_default="1"),
        sa.Column("bestandsnaam", sa.String(120), server_default=""),
        sa.Column("png", sa.LargeBinary, nullable=False),
        sa.Column("caption", sa.Text, server_default=""),
        sa.Column("alt_tekst", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_rotatie_afbeeldingen_run_id", "rotatie_afbeeldingen", ["run_id"])
    op.add_column("wissellijsten",
                  sa.Column("instagram_plaatjes", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("wissellijsten") as batch:
        batch.drop_column("instagram_plaatjes")
    op.drop_index("ix_rotatie_afbeeldingen_run_id", "rotatie_afbeeldingen")
    op.drop_table("rotatie_afbeeldingen")
