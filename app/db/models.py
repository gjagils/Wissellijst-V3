"""SQLAlchemy modellen voor Wissellijst V3."""
import datetime
from sqlalchemy import (
    Column, String, Integer, Text, Boolean, DateTime, ForeignKey, JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow():
    """Naive UTC-timestamp (kolommen zijn naive; datetime.utcnow is verouderd)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Wissellijst(Base):
    """Configuratie van een wissellijst."""
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
    blokken_per_rotatie = Column(Integer, default=1)
    populariteit_verdeling = Column(Boolean, default=False)
    radiolijsten = Column(Boolean, default=False)  # NL-radiolijsten voor kandidaten en populariteit
    max_per_artiest = Column(Integer, default=0)

    # Rotatie schema
    rotatie_schema = Column(String(20), default="uit")
    rotatie_tijdstip = Column(String(5), default="08:00")
    rotatie_dag = Column(Integer, default=0)

    # Mail notificatie
    mail_na_rotatie = Column(Boolean, default=False)
    mail_adres = Column(String(255), default="")

    # Smaakprofiel (kopie als tekst; de bron is de tabel smaakprofielen)
    smaakprofiel = Column(Text, default="")

    # Timestamps
    laatste_rotatie = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

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
        return {
            "id": self.id,
            "naam": self.naam,
            "playlist_id": self.playlist_id,
            "type": self.type,
            "categorieen": self.categorieen or [],
            "bron_playlists": self.bron_playlists or [],
            "aantal_blokken": self.aantal_blokken,
            "blok_grootte": self.blok_grootte,
            "blokken_per_rotatie": self.blokken_per_rotatie or 1,
            "populariteit_verdeling": bool(self.populariteit_verdeling),
            "radiolijsten": bool(self.radiolijsten),
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
    """Smaakprofiel per wissellijst."""
    __tablename__ = "smaakprofielen"

    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            primary_key=True)
    profiel = Column(Text, default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    wissellijst = relationship("Wissellijst", back_populates="smaakprofiel_rel")


class HistorieEntry(Base):
    """Historie-entry per wissellijst: elk nummer dat ooit in de playlist zat."""
    __tablename__ = "historie"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wissellijst_id = Column(String(8), ForeignKey("wissellijsten.id"),
                            nullable=False, index=True)
    categorie = Column(String(100), default="")
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="")
    added_at = Column(DateTime, default=utcnow)

    wissellijst = relationship("Wissellijst", back_populates="historie")


class WachtrijEntry(Base):
    """Wachtrij-entry per wissellijst: het volgende blok."""
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
    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    tracks_verwijderd = Column(Integer, default=0)
    tracks_toegevoegd = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    wissellijst = relationship("Wissellijst", back_populates="rotatie_runs")
    wijzigingen = relationship("RotatieWijziging", back_populates="run",
                               cascade="all, delete-orphan")


class RotatieWijziging(Base):
    """Individuele track-wijziging binnen een rotatie-run."""
    __tablename__ = "rotatie_wijzigingen"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("rotatie_runs.id"), nullable=False,
                    index=True)
    type = Column(String(20))  # toegevoegd of verwijderd
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="")
    categorie = Column(String(100), default="")
    created_at = Column(DateTime, default=utcnow)

    run = relationship("RotatieRun", back_populates="wijzigingen")


class SpotifyToken(Base):
    """Het Spotify OAuth-token (één rij). Vervangt het bestand .cache."""
    __tablename__ = "spotify_token"

    id = Column(Integer, primary_key=True)  # altijd 1
    token_info = Column(JSON, nullable=False)
    spotify_user = Column(String(255), default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Radiolijst(Base):
    """Een NL-radiolijst (bijv. Q-top 500 van de 90s, Top 2000) als bron voor populariteit."""
    __tablename__ = "radiolijsten"

    id = Column(Integer, primary_key=True, autoincrement=True)
    naam = Column(String(255), nullable=False)
    zender = Column(String(100), default="")
    decennium = Column(String(10), default="")  # bijv. '80s'; leeg = alle decennia
    spotify_playlist_id = Column(String(64), default="")  # leeg = geplakt als tekst
    geimporteerd_op = Column(DateTime, default=utcnow)

    entries = relationship("RadiolijstEntry", back_populates="radiolijst",
                           cascade="all, delete-orphan", order_by="RadiolijstEntry.positie")

    def to_dict(self, aantal=None):
        return {
            "id": self.id,
            "naam": self.naam,
            "zender": self.zender or "",
            "decennium": self.decennium or "",
            "spotify_playlist_id": self.spotify_playlist_id or "",
            "geimporteerd_op": self.geimporteerd_op.isoformat() if self.geimporteerd_op else "",
            "aantal": aantal,
        }


class RadiolijstEntry(Base):
    """Eén nummer op een plek in een radiolijst."""
    __tablename__ = "radiolijst_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    radiolijst_id = Column(Integer, ForeignKey("radiolijsten.id"), nullable=False, index=True)
    positie = Column(Integer, nullable=False)
    artiest = Column(String(255), default="")
    titel = Column(String(255), default="")
    uri = Column(String(100), default="", index=True)
    jaar = Column(Integer, nullable=True)  # releasejaar als bekend (uit Spotify)

    radiolijst = relationship("Radiolijst", back_populates="entries")
