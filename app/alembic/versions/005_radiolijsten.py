"""NL-radiolijsten als bron voor populariteit en kandidaten.

Revision ID: 005
Revises: 004
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "radiolijsten",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("naam", sa.String(255), nullable=False),
        sa.Column("zender", sa.String(100), server_default=""),
        sa.Column("decennium", sa.String(10), server_default=""),
        sa.Column("spotify_playlist_id", sa.String(64), server_default=""),
        sa.Column("geimporteerd_op", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_table(
        "radiolijst_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("radiolijst_id", sa.Integer, sa.ForeignKey("radiolijsten.id"), nullable=False),
        sa.Column("positie", sa.Integer, nullable=False),
        sa.Column("artiest", sa.String(255), server_default=""),
        sa.Column("titel", sa.String(255), server_default=""),
        sa.Column("uri", sa.String(100), server_default=""),
        sa.Column("jaar", sa.Integer, nullable=True),
    )
    op.create_index("ix_radiolijst_entries_radiolijst_id", "radiolijst_entries", ["radiolijst_id"])
    op.create_index("ix_radiolijst_entries_uri", "radiolijst_entries", ["uri"])
    op.add_column("wissellijsten",
                  sa.Column("radiolijsten", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("wissellijsten") as batch:
        batch.drop_column("radiolijsten")
    op.drop_index("ix_radiolijst_entries_uri", "radiolijst_entries")
    op.drop_index("ix_radiolijst_entries_radiolijst_id", "radiolijst_entries")
    op.drop_table("radiolijst_entries")
    op.drop_table("radiolijsten")
