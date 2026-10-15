"""Spotify-token in de database (vervangt het bestand .cache).

Revision ID: 002
Revises: 001
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spotify_token",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_info", sa.JSON, nullable=False),
        sa.Column("spotify_user", sa.String(255), server_default=""),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("spotify_token")
