"""SQLAlchemy modellen voor Wissellijst V3."""
import datetime
from sqlalchemy import (
    Column, String, Integer, Text, Boolean, DateTime, ForeignKey, JSON, Float,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Wissellijst(Base):
    """Configuratie van een wissellijst (vervangt wissellijsten.json entries)."""
    __tablename__ = "wissellijsten"

    id = Column(String(8), primary_key=True)
    naam = Column(String(255), nullable=False)
    playlist_id = Column(String(64), nullable=False)
    type = Column(String(20), default="categorie")  # categorie of discovery

    # Configuratie
    categorieen = Column(JSON, default=list)
    bron_playlists = Column(JSON, default=list)
    aantal_blokken = Column(Integer, default=10)
    blok_grootte = Column(Integer, default=5)
    max_per_artiest = Column(Integer, default=0)

    # Rotatie schema
    rotatie_schema = Column(String(20), default="uit")
    rotatie_tijdstip = Column(String(5), default="08:00")
    rotatie_dag = Column(Integer, default=0)

    # Mail notificatie
    mail_na_rotatie = Column(Boolean, default=False)
    mail_adres = Column(String(255), default="")

    # Smaakprofiel (als tekst, voor backward compat)
    smaakprofiel = Column(Text, default="")

    # Timestamps
    laatste_rotatie = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)

    # Relaties
    smaakprofiel_rel = relationship("Smaakprofiel", back_populates="wissellijst",
                                     uselist=False, cascade="all, delete-orphan")
    historie = relationship("HistorieEntry", back_populates="wissellijst",
                            cascade="all, delete-orphan",
                            order_by="HistorieEntry.id")
    wachtrij = relationship("WachtrijEntry", back_populates="wissellijst",
                            cascade="all, delete-orphan",
                            order_by="WachtrijEntry.positie")
    rotatie_runs = relationship("RotatieRun", back_populates="wissellijst",
                                cascade="all, delete-orphan",
                                order_by="RotatieRun.started_at.desc()")

    def to_dict(self):
        """Converteer naar dict (compatible met het oude JSON formaat)."""
        return {
            "id": self.id,
            "naam": self.naam,
            "playlist_id": self.playlist_id,
            "type": self.type,
            "categorieen": self.categorieen or [],
            "bron_playlists": self.bron_playlists or [],
            "aantal_blokken": self.aantal_blokken,
            "blok_grootte": self.blok_grootte,
            "max_per_artiest": self.max_per_artiest,
            "rotatie_schema": self.rotatie_schema,
            "rotatie_tijdstip": self.rotatie_tijdstip,
            "rotatie_dag": self.rotatie_dag,
            "mail_na_rotatie": self.mail_na_rotatie,
            "mail_adres": self.mail_adres or "",
            "smaakprofiel": self.smaakprofiel or "",
            "laatste_rotatie": self.laatste_rotatie.isoformat() if self.laatste_rotatie else "",
        }


class Smaakprofiel(Base):
    """Smaakprofiel per wissellijst (vervangt smaakprofiel_*.txt)."""
    __tablename__ = "smaakprofielen"

    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            primary_key=True)
    profiel = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)

    wissellijst = relationship("Wissellijst", back_populates="smaakprofiel_rel")


class HistorieEntry(Base):
    """Historie-entry per wissellijst (vervangt historie_*.txt)."""
    __tablename__ = "historie"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            nullable=False, index=True)
    categorie = Column(String(100), default="")
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="")
    added_at = Column(DateTime, default=datetime.datetime.utcnow)

    wissellijst = relationship("Wissellijst", back_populates="historie")


class WachtrijEntry(Base):
    """Wachtrij-entry per wissellijst (vervangt wachtrij_*.txt)."""
    __tablename__ = "wachtrij"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            nullable=False, index=True)
    categorie = Column(String(100), default="")
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="")
    positie = Column(Integer, default=0)

    wissellijst = relationship("Wissellijst", back_populates="wachtrij")


class RotatieRun(Base):
    """Audit trail: elke keer dat een rotatie wordt uitgevoerd."""
    __tablename__ = "rotatie_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            nullable=False, index=True)
    triggered_by = Column(String(20), default="user")  # user of scheduler
    status = Column(String(20), default="gestart")  # gestart, voltooid, mislukt
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    tracks_verwijderd = Column(Integer, default=0)
    tracks_toegevoegd = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    wissellijst = relationship("Wissellijst", back_populates="rotatie_runs")
    wijzigingen = relationship("RotatieWijziging", back_populates="run",
                               cascade="all, delete-orphan")


class RotatieWijziging(Base):
    """Individuele track wijziging binnen een rotatie-run."""
    __tablename__ = "rotatie_wijzigingen"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("rotatie_runs.id"), nullable=False,
                    index=True)
    type = Column(String(20))  # toegevoegd of verwijderd
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="")
    categorie = Column(String(100), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    run = relationship("RotatieRun", back_populates="wijzigingen")
