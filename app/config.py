"""Instellingen (uit de omgeving) en de data-laag van Wissellijst V3.

Alle data staat in PostgreSQL; de tekstbestanden van V2 worden alleen nog
gelezen door db/migrate_data.py bij een eerste start met een lege database.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Spotify ---
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_SCOPE = "playlist-read-private playlist-modify-public playlist-modify-private user-top-read"

# --- OpenAI ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")              # suggesties per categorie
OPENAI_SCORE_MODEL = os.getenv("OPENAI_SCORE_MODEL", "gpt-4o-mini")  # discovery-scoring

# --- Gedrag van de generatie ---
SUGGESTIES_PER_CATEGORIE = int(os.getenv("SUGGESTIES_PER_CATEGORIE", "5"))
MAX_REASKS = int(os.getenv("MAX_REASKS", "2"))
BLOK_ACCEPT_RATIO = float(os.getenv("BLOK_ACCEPT_RATIO", "0.8"))  # deel van het blok dat gevuld moet zijn
DISCOVERY_MAX_MAANDEN = int(os.getenv("DISCOVERY_MAX_MAANDEN", "3"))
DISCOVERY_EXPIRE_DAGEN = int(os.getenv("DISCOVERY_EXPIRE_DAGEN", "30"))
# Populariteitsvakken (Spotify popularity 0-100) voor lijsten met populariteitsverdeling aan
POPULARITEIT_VAKKEN = os.getenv("POPULARITEIT_VAKKEN", "30-50,50-70,70-80,80-90,90-100")
# NL-radiolijsten: plek 1 krijgt score 100, de laatste plek RADIO_SCORE_MIN
RADIO_SCORE_MIN = int(os.getenv("RADIO_SCORE_MIN", "30"))
# Hoeveel nummers uit de radiolijsten GPT per categorie als keuzemenu krijgt
RADIO_KANDIDATEN_PER_CATEGORIE = int(os.getenv("RADIO_KANDIDATEN_PER_CATEGORIE", "25"))

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- E-mail (optioneel) ---
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")

# --- Paden: alleen nog voor de eenmalige migratie van V2-bestanden ---
DATA_DIR = os.getenv("DATA_DIR", "/app/data")


def _session():
    from db.session import get_session
    return get_session()


# --- Wissellijsten ---

def load_wissellijsten():
    from db.models import Wissellijst
    with _session() as session:
        wls = session.query(Wissellijst).all()
        return {"wissellijsten": [wl.to_dict() for wl in wls]}


_WL_VELDEN = ("naam", "playlist_id", "type", "categorieen", "bron_playlists",
              "aantal_blokken", "blok_grootte", "blokken_per_rotatie", "max_per_artiest",
              "populariteit_verdeling", "radiolijsten",
              "rotatie_schema", "rotatie_tijdstip", "rotatie_dag",
              "mail_na_rotatie", "mail_adres", "smaakprofiel")


def _parse_iso(value):
    import datetime
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def save_wissellijst(wl_data):
    """Maak een wissellijst aan of werk een bestaande bij. Returns de dict."""
    from db.models import Wissellijst, utcnow

    wl_id = wl_data.get("id")
    with _session() as session:
        wl = session.get(Wissellijst, wl_id) if wl_id else None
        if wl:
            for key in _WL_VELDEN:
                if key in wl_data:
                    setattr(wl, key, wl_data[key])
            lr = _parse_iso(wl_data.get("laatste_rotatie"))
            if lr:
                wl.laatste_rotatie = lr
            wl.updated_at = utcnow()
        else:
            wl = Wissellijst(
                id=wl_data["id"],
                naam=wl_data.get("naam", ""),
                playlist_id=wl_data.get("playlist_id", ""),
                type=wl_data.get("type", "categorie"),
                categorieen=wl_data.get("categorieen", []),
                bron_playlists=wl_data.get("bron_playlists", []),
                aantal_blokken=wl_data.get("aantal_blokken", 10),
                blok_grootte=wl_data.get("blok_grootte", 5),
                blokken_per_rotatie=wl_data.get("blokken_per_rotatie", 1),
                populariteit_verdeling=wl_data.get("populariteit_verdeling", False),
                radiolijsten=wl_data.get("radiolijsten", False),
                max_per_artiest=wl_data.get("max_per_artiest", 0),
                rotatie_schema=wl_data.get("rotatie_schema", "uit"),
                rotatie_tijdstip=wl_data.get("rotatie_tijdstip", "08:00"),
                rotatie_dag=wl_data.get("rotatie_dag", 0),
                mail_na_rotatie=wl_data.get("mail_na_rotatie", False),
                mail_adres=wl_data.get("mail_adres", ""),
                smaakprofiel=wl_data.get("smaakprofiel", ""),
                laatste_rotatie=_parse_iso(wl_data.get("laatste_rotatie")),
            )
            session.add(wl)
        session.flush()
        return wl.to_dict()


def get_wissellijst(lijst_id):
    from db.models import Wissellijst
    with _session() as session:
        wl = session.get(Wissellijst, lijst_id)
        return wl.to_dict() if wl else None


def delete_wissellijst(lijst_id):
    from db.models import Wissellijst
    with _session() as session:
        wl = session.get(Wissellijst, lijst_id)
        if wl:
            session.delete(wl)


def set_laatste_rotatie(lijst_id):
    from db.models import Wissellijst, utcnow
    with _session() as session:
        wl = session.get(Wissellijst, lijst_id)
        if wl:
            wl.laatste_rotatie = utcnow()


# --- Historie ---

def _entry_dict(e):
    return {"categorie": e.categorie, "artiest": e.artiest,
            "titel": e.titel, "uri": e.uri}


def get_historie(lijst_id):
    from db.models import HistorieEntry
    with _session() as session:
        entries = (session.query(HistorieEntry)
                   .filter_by(wissellijst_id=lijst_id)
                   .order_by(HistorieEntry.id)
                   .all())
        return [_entry_dict(e) for e in entries]


def add_historie_bulk(lijst_id, entries):
    from db.models import HistorieEntry
    with _session() as session:
        for entry in entries:
            session.add(HistorieEntry(
                wissellijst_id=lijst_id,
                categorie=entry.get("categorie", ""),
                artiest=entry.get("artiest", ""),
                titel=entry.get("titel", ""),
                uri=entry.get("uri", ""),
            ))


def delete_historie_entry(lijst_id, entry_index):
    """Verwijder een historie-entry op basis van index (volgorde van get_historie)."""
    from db.models import HistorieEntry
    with _session() as session:
        entries = (session.query(HistorieEntry)
                   .filter_by(wissellijst_id=lijst_id)
                   .order_by(HistorieEntry.id)
                   .all())
        if 0 <= entry_index < len(entries):
            session.delete(entries[entry_index])
            return True
    return False


def clear_historie(lijst_id):
    from db.models import HistorieEntry
    with _session() as session:
        session.query(HistorieEntry).filter_by(wissellijst_id=lijst_id).delete()


def get_historie_uris(lijst_id):
    from db.models import HistorieEntry
    with _session() as session:
        rows = (session.query(HistorieEntry.uri)
                .filter_by(wissellijst_id=lijst_id).all())
        return {r.uri for r in rows}


def import_historie(lijst_id, tekst):
    """Importeer historie-regels ("categorie - artiest - titel - spotify:track:...").

    Returns: dict met toegevoegd, dubbel, ongeldig.
    """
    from suggest import _parse_history_line

    bestaand = get_historie_uris(lijst_id)
    nieuw, dubbel, ongeldig = [], 0, 0
    for line in (tekst or "").splitlines():
        if not line.strip():
            continue
        parsed = _parse_history_line(line)
        if not parsed:
            ongeldig += 1
        elif parsed["uri"] in bestaand:
            dubbel += 1
        else:
            bestaand.add(parsed["uri"])
            nieuw.append(parsed)
    if nieuw:
        add_historie_bulk(lijst_id, nieuw)
    return {"toegevoegd": len(nieuw), "dubbel": dubbel, "ongeldig": ongeldig}


# --- Wachtrij ---

def get_wachtrij(lijst_id):
    from db.models import WachtrijEntry
    with _session() as session:
        entries = (session.query(WachtrijEntry)
                   .filter_by(wissellijst_id=lijst_id)
                   .order_by(WachtrijEntry.positie)
                   .all())
        return [_entry_dict(e) for e in entries]


def save_wachtrij(lijst_id, entries):
    """Vervang de wachtrij."""
    from db.models import WachtrijEntry
    with _session() as session:
        session.query(WachtrijEntry).filter_by(wissellijst_id=lijst_id).delete()
        for pos, entry in enumerate(entries):
            session.add(WachtrijEntry(
                wissellijst_id=lijst_id,
                categorie=entry.get("categorie", ""),
                artiest=entry.get("artiest", ""),
                titel=entry.get("titel", ""),
                uri=entry.get("uri", ""),
                positie=pos,
            ))


def clear_wachtrij(lijst_id):
    save_wachtrij(lijst_id, [])


def get_wachtrij_uris(lijst_id):
    from db.models import WachtrijEntry
    with _session() as session:
        rows = (session.query(WachtrijEntry.uri)
                .filter_by(wissellijst_id=lijst_id).all())
        return {r.uri for r in rows}


# --- Smaakprofiel ---

def get_smaakprofiel(lijst_id):
    from db.models import Smaakprofiel
    with _session() as session:
        sp = session.get(Smaakprofiel, lijst_id)
        return sp.profiel if sp else ""


def save_smaakprofiel(lijst_id, profiel_tekst):
    from db.models import Smaakprofiel
    with _session() as session:
        sp = session.get(Smaakprofiel, lijst_id)
        if sp:
            sp.profiel = profiel_tekst
        else:
            session.add(Smaakprofiel(wissellijst_id=lijst_id, profiel=profiel_tekst))
