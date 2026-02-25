"""Initial schema - alle tabellen voor Wissellijst V3.

Revision ID: 001
Revises:
Create Date: 2026-02-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wissellijsten (hoofd-configuratie tabel)
    op.create_table(
        "wissellijsten",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("naam", sa.String(255), nullable=False),
        sa.Column("playlist_id", sa.String(64), nullable=False),
        sa.Column("type", sa.String(20), server_default="categorie"),
        sa.Column("categorieen", sa.JSON, server_default="[]"),
        sa.Column("bron_playlists", sa.JSON, server_default="[]"),
        sa.Column("aantal_blokken", sa.Integer, server_default="10"),
        sa.Column("blok_grootte", sa.Integer, server_default="5"),
        sa.Column("max_per_artiest", sa.Integer, server_default="0"),
        sa.Column("rotatie_schema", sa.String(20), server_default="uit"),
        sa.Column("rotatie_tijdstip", sa.String(5), server_default="08:00"),
        sa.Column("rotatie_dag", sa.Integer, server_default="0"),
        sa.Column("mail_na_rotatie", sa.Boolean, server_default="false"),
        sa.Column("mail_adres", sa.String(255), server_default=""),
        sa.Column("smaakprofiel", sa.Text, server_default=""),
        sa.Column("laatste_rotatie", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Smaakprofielen (apart voor backward compat)
    op.create_table(
        "smaakprofielen",
        sa.Column("wissellijst_id", sa.String(8),
                   sa.ForeignKey("wissellijsten.id"), primary_key=True),
        sa.Column("profiel", sa.Text, server_default=""),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Historie entries
    op.create_table(
        "historie",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("wissellijst_id", sa.String(8),
                   sa.ForeignKey("wissellijsten.id"), nullable=False),
        sa.Column("categorie", sa.String(100), server_default=""),
        sa.Column("artiest", sa.String(255), server_default=""),
        sa.Column("titel", sa.String(255), server_default=""),
        sa.Column("uri", sa.String(100), server_default=""),
        sa.Column("added_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_historie_wissellijst_id", "historie", ["wissellijst_id"])

    # Wachtrij entries
    op.create_table(
        "wachtrij",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("wissellijst_id", sa.String(8),
                   sa.ForeignKey("wissellijsten.id"), nullable=False),
        sa.Column("categorie", sa.String(100), server_default=""),
        sa.Column("artiest", sa.String(255), server_default=""),
        sa.Column("titel", sa.String(255), server_default=""),
        sa.Column("uri", sa.String(100), server_default=""),
        sa.Column("positie", sa.Integer, server_default="0"),
    )
    op.create_index("ix_wachtrij_wissellijst_id", "wachtrij", ["wissellijst_id"])

    # Rotatie runs (audit trail)
    op.create_table(
        "rotatie_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("wissellijst_id", sa.String(8),
                   sa.ForeignKey("wissellijsten.id"), nullable=False),
        sa.Column("triggered_by", sa.String(20), server_default="user"),
        sa.Column("status", sa.String(20), server_default="gestart"),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("tracks_verwijderd", sa.Integer, server_default="0"),
        sa.Column("tracks_toegevoegd", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_rotatie_runs_wissellijst_id", "rotatie_runs",
                     ["wissellijst_id"])

    # Rotatie wijzigingen (individuele track changes)
    op.create_table(
        "rotatie_wijzigingen",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer,
                   sa.ForeignKey("rotatie_runs.id"), nullable=False),
        sa.Column("type", sa.String(20)),
        sa.Column("artiest", sa.String(255), server_default=""),
        sa.Column("titel", sa.String(255), server_default=""),
        sa.Column("uri", sa.String(100), server_default=""),
        sa.Column("categorie", sa.String(100), server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_rotatie_wijzigingen_run_id", "rotatie_wijzigingen",
                     ["run_id"])


def downgrade() -> None:
    op.drop_table("rotatie_wijzigingen")
    op.drop_table("rotatie_runs")
    op.drop_table("wachtrij")
    op.drop_table("historie")
    op.drop_table("smaakprofielen")
    op.drop_table("wissellijsten")
