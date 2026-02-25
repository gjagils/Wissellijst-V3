"""Data migratie: van JSON/txt bestanden naar PostgreSQL.

Draait automatisch bij startup als de database leeg is en
er data bestanden bestaan in /app/data.
"""
import os
import json

from logging_config import get_logger

logger = get_logger(__name__)


def needs_migration(session):
    """Check of migratie nodig is (DB leeg, bestanden aanwezig)."""
    from db.models import Wissellijst

    # Als er al wissellijsten in de DB staan, geen migratie nodig
    count = session.query(Wissellijst).count()
    if count > 0:
        return False

    # Check of er data bestanden zijn
    data_dir = os.getenv("DATA_DIR", "/app/data")
    config_file = os.path.join(data_dir, "wissellijsten.json")
    return os.path.exists(config_file)


def migrate_all(session):
    """Migreer alle data van bestanden naar database.

    Args:
        session: SQLAlchemy sessie (al geopend, caller doet commit)
    """
    from db.models import (
        Wissellijst, Smaakprofiel, HistorieEntry, WachtrijEntry,
    )

    data_dir = os.getenv("DATA_DIR", "/app/data")
    config_file = os.path.join(data_dir, "wissellijsten.json")

    if not os.path.exists(config_file):
        logger.info("Geen wissellijsten.json gevonden, skip migratie")
        return

    # Stap 1: Wissellijsten migreren
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    wissellijsten = data.get("wissellijsten", [])
    logger.info("Migratie starten", extra={"wissellijsten": len(wissellijsten)})

    for wl_data in wissellijsten:
        wl_id = wl_data.get("id", "")
        if not wl_id:
            continue

        # Parse laatste_rotatie
        laatste_rotatie = None
        lr_str = wl_data.get("laatste_rotatie", "")
        if lr_str:
            try:
                from datetime import datetime
                laatste_rotatie = datetime.fromisoformat(lr_str)
            except (ValueError, TypeError):
                pass

        wl = Wissellijst(
            id=wl_id,
            naam=wl_data.get("naam", ""),
            playlist_id=wl_data.get("playlist_id", ""),
            type=wl_data.get("type", "categorie"),
            categorieen=wl_data.get("categorieen", []),
            bron_playlists=wl_data.get("bron_playlists", []),
            aantal_blokken=wl_data.get("aantal_blokken", 10),
            blok_grootte=wl_data.get("blok_grootte", 5),
            max_per_artiest=wl_data.get("max_per_artiest", 0),
            rotatie_schema=wl_data.get("rotatie_schema", "uit"),
            rotatie_tijdstip=wl_data.get("rotatie_tijdstip", "08:00"),
            rotatie_dag=wl_data.get("rotatie_dag", 0),
            mail_na_rotatie=wl_data.get("mail_na_rotatie", False),
            mail_adres=wl_data.get("mail_adres", ""),
            smaakprofiel=wl_data.get("smaakprofiel", ""),
            laatste_rotatie=laatste_rotatie,
        )
        session.add(wl)
        logger.info("Wissellijst gemigreerd",
                     extra={"id": wl_id, "naam": wl.naam})

        # Stap 2: Smaakprofiel migreren
        profiel_file = os.path.join(data_dir, f"smaakprofiel_{wl_id}.txt")
        if os.path.exists(profiel_file):
            with open(profiel_file, "r", encoding="utf-8") as f:
                profiel_tekst = f.read().strip()
            if profiel_tekst:
                session.add(Smaakprofiel(
                    wissellijst_id=wl_id,
                    profiel=profiel_tekst,
                ))
                logger.info("Smaakprofiel gemigreerd",
                             extra={"wissellijst_id": wl_id})

        # Stap 3: Historie migreren
        historie_file = os.path.join(data_dir, f"historie_{wl_id}.txt")
        if os.path.exists(historie_file):
            count = 0
            with open(historie_file, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_line(line)
                    if parsed:
                        session.add(HistorieEntry(
                            wissellijst_id=wl_id,
                            categorie=parsed["categorie"],
                            artiest=parsed["artiest"],
                            titel=parsed["titel"],
                            uri=parsed["uri"],
                        ))
                        count += 1
            logger.info("Historie gemigreerd",
                         extra={"wissellijst_id": wl_id, "entries": count})

        # Stap 4: Wachtrij migreren
        wachtrij_file = os.path.join(data_dir, f"wachtrij_{wl_id}.txt")
        if os.path.exists(wachtrij_file):
            count = 0
            with open(wachtrij_file, "r", encoding="utf-8") as f:
                for pos, line in enumerate(f):
                    parsed = _parse_line(line)
                    if parsed:
                        session.add(WachtrijEntry(
                            wissellijst_id=wl_id,
                            categorie=parsed["categorie"],
                            artiest=parsed["artiest"],
                            titel=parsed["titel"],
                            uri=parsed["uri"],
                            positie=pos,
                        ))
                        count += 1
            logger.info("Wachtrij gemigreerd",
                         extra={"wissellijst_id": wl_id, "entries": count})

    session.flush()
    logger.info("Data migratie voltooid",
                extra={"wissellijsten": len(wissellijsten)})


def _parse_line(line):
    """Parse een historie/wachtrij regel.

    Formaat: categorie - artiest - titel - spotify:track:xxx
    """
    line = line.strip()
    if not line:
        return None

    # URI is altijd het laatste deel
    parts = line.rsplit(" - ", 1)
    if len(parts) < 2 or not parts[1].startswith("spotify:"):
        return None
    uri = parts[1].strip()

    # Rest: categorie - artiest - titel
    left_parts = parts[0].split(" - ", 2)
    if len(left_parts) < 3:
        return None

    return {
        "categorie": left_parts[0].strip(),
        "artiest": left_parts[1].strip(),
        "titel": left_parts[2].strip(),
        "uri": uri,
    }
