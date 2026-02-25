import os
import json
from dotenv import load_dotenv

load_dotenv()

# Spotify
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_SCOPE = "playlist-read-private playlist-modify-public playlist-modify-private user-top-read"

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")

# E-mail (optioneel)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")

# Paden (file-based fallback)
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
QUEUE_FILE = os.path.join(DATA_DIR, "volgende_blokje.txt")
HISTORY_FILE = os.path.join(DATA_DIR, "historie.txt")
SUGGESTIONS_FILE = os.path.join(DATA_DIR, "aanbevelingen.txt")
CACHE_PATH = os.path.join(DATA_DIR, ".cache")
CONFIG_FILE = os.path.join(DATA_DIR, "wissellijsten.json")


# --- Database-backed functies ---

def _use_db():
    """Check of we de database moeten gebruiken."""
    try:
        from db.session import db_available
        return db_available()
    except ImportError:
        return False


def load_wissellijsten():
    """Laad alle wissellijst-configuraties. DB als beschikbaar, anders JSON."""
    if _use_db():
        from db.session import get_session
        from db.models import Wissellijst
        with get_session() as session:
            wls = session.query(Wissellijst).all()
            return {"wissellijsten": [wl.to_dict() for wl in wls]}

    # Fallback naar JSON
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"wissellijsten": []}


def save_wissellijsten(data):
    """Sla wissellijst-configuraties op. DB als beschikbaar, anders JSON."""
    if _use_db():
        from db.session import get_session
        from db.models import Wissellijst
        import datetime

        with get_session() as session:
            for wl_data in data.get("wissellijsten", []):
                wl = session.query(Wissellijst).get(wl_data.get("id"))
                if wl:
                    # Update bestaande
                    wl.naam = wl_data.get("naam", wl.naam)
                    wl.playlist_id = wl_data.get("playlist_id", wl.playlist_id)
                    wl.type = wl_data.get("type", wl.type)
                    wl.categorieen = wl_data.get("categorieen", wl.categorieen)
                    wl.bron_playlists = wl_data.get("bron_playlists", wl.bron_playlists)
                    wl.aantal_blokken = wl_data.get("aantal_blokken", wl.aantal_blokken)
                    wl.blok_grootte = wl_data.get("blok_grootte", wl.blok_grootte)
                    wl.max_per_artiest = wl_data.get("max_per_artiest", wl.max_per_artiest)
                    wl.rotatie_schema = wl_data.get("rotatie_schema", wl.rotatie_schema)
                    wl.rotatie_tijdstip = wl_data.get("rotatie_tijdstip", wl.rotatie_tijdstip)
                    wl.rotatie_dag = wl_data.get("rotatie_dag", wl.rotatie_dag)
                    wl.mail_na_rotatie = wl_data.get("mail_na_rotatie", wl.mail_na_rotatie)
                    wl.mail_adres = wl_data.get("mail_adres", wl.mail_adres)
                    wl.smaakprofiel = wl_data.get("smaakprofiel", wl.smaakprofiel)
                    lr = wl_data.get("laatste_rotatie", "")
                    if lr:
                        try:
                            wl.laatste_rotatie = datetime.datetime.fromisoformat(lr)
                        except (ValueError, TypeError):
                            pass
                    wl.updated_at = datetime.datetime.utcnow()
        return

    # Fallback naar JSON
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_wissellijst(wl_data):
    """Sla een enkele wissellijst op (create of update)."""
    if _use_db():
        from db.session import get_session
        from db.models import Wissellijst
        import datetime

        wl_id = wl_data.get("id")
        with get_session() as session:
            wl = session.query(Wissellijst).get(wl_id) if wl_id else None
            if wl:
                # Update
                for key in ("naam", "playlist_id", "type", "categorieen",
                            "bron_playlists", "aantal_blokken", "blok_grootte",
                            "max_per_artiest", "rotatie_schema", "rotatie_tijdstip",
                            "rotatie_dag", "mail_na_rotatie", "mail_adres", "smaakprofiel"):
                    if key in wl_data:
                        setattr(wl, key, wl_data[key])
                lr = wl_data.get("laatste_rotatie", "")
                if lr:
                    try:
                        wl.laatste_rotatie = datetime.datetime.fromisoformat(lr)
                    except (ValueError, TypeError):
                        pass
                wl.updated_at = datetime.datetime.utcnow()
            else:
                # Create
                laatste_rotatie = None
                lr = wl_data.get("laatste_rotatie", "")
                if lr:
                    try:
                        laatste_rotatie = datetime.datetime.fromisoformat(lr)
                    except (ValueError, TypeError):
                        pass

                wl = Wissellijst(
                    id=wl_data["id"],
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
            session.flush()
            return wl.to_dict()

    # Fallback: save via JSON
    data = load_wissellijsten()
    found = False
    for i, existing in enumerate(data["wissellijsten"]):
        if existing["id"] == wl_data.get("id"):
            data["wissellijsten"][i] = wl_data
            found = True
            break
    if not found:
        data["wissellijsten"].append(wl_data)
    save_wissellijsten(data)
    return wl_data


def get_wissellijst(lijst_id):
    """Haal een specifieke wissellijst op via ID."""
    if _use_db():
        from db.session import get_session
        from db.models import Wissellijst
        with get_session() as session:
            wl = session.query(Wissellijst).get(lijst_id)
            return wl.to_dict() if wl else None

    # Fallback
    data = load_wissellijsten()
    for wl in data["wissellijsten"]:
        if wl["id"] == lijst_id:
            return wl
    return None


def delete_wissellijst(lijst_id):
    """Verwijder een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import Wissellijst
        with get_session() as session:
            wl = session.query(Wissellijst).get(lijst_id)
            if wl:
                session.delete(wl)
        return

    # Fallback
    data = load_wissellijsten()
    data["wissellijsten"] = [wl for wl in data["wissellijsten"] if wl["id"] != lijst_id]
    save_wissellijsten(data)


# --- Historie functies ---

def get_historie(lijst_id):
    """Haal historie-entries op voor een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            entries = (session.query(HistorieEntry)
                       .filter_by(wissellijst_id=lijst_id)
                       .order_by(HistorieEntry.id)
                       .all())
            return [{"categorie": e.categorie, "artiest": e.artiest,
                     "titel": e.titel, "uri": e.uri} for e in entries]

    # Fallback: file
    entries = []
    hf = get_history_file(lijst_id)
    if os.path.exists(hf):
        from suggest import _parse_history_line
        with open(hf, "r", encoding="utf-8") as f:
            for line in f:
                parsed = _parse_history_line(line)
                if parsed:
                    entries.append(parsed)
    return entries


def add_historie(lijst_id, entry):
    """Voeg een historie-entry toe."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            session.add(HistorieEntry(
                wissellijst_id=lijst_id,
                categorie=entry.get("categorie", ""),
                artiest=entry.get("artiest", ""),
                titel=entry.get("titel", ""),
                uri=entry.get("uri", ""),
            ))
        return

    # Fallback: file
    hf = get_history_file(lijst_id)
    os.makedirs(os.path.dirname(hf), exist_ok=True)
    with open(hf, "a", encoding="utf-8") as f:
        f.write(f"{entry['categorie']} - {entry['artiest']} - "
                f"{entry['titel']} - {entry['uri']}\n")


def add_historie_bulk(lijst_id, entries):
    """Voeg meerdere historie-entries toe in één transactie."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            for entry in entries:
                session.add(HistorieEntry(
                    wissellijst_id=lijst_id,
                    categorie=entry.get("categorie", ""),
                    artiest=entry.get("artiest", ""),
                    titel=entry.get("titel", ""),
                    uri=entry.get("uri", ""),
                ))
        return

    # Fallback: file
    hf = get_history_file(lijst_id)
    os.makedirs(os.path.dirname(hf), exist_ok=True)
    with open(hf, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(f"{entry['categorie']} - {entry['artiest']} - "
                    f"{entry['titel']} - {entry['uri']}\n")


def delete_historie_entry(lijst_id, entry_index):
    """Verwijder een historie-entry op basis van index."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            entries = (session.query(HistorieEntry)
                       .filter_by(wissellijst_id=lijst_id)
                       .order_by(HistorieEntry.id)
                       .all())
            if 0 <= entry_index < len(entries):
                session.delete(entries[entry_index])
                return True
        return False

    # Fallback: handled in web.py (existing file logic)
    return False


def clear_historie(lijst_id):
    """Wis de volledige historie van een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            session.query(HistorieEntry).filter_by(
                wissellijst_id=lijst_id).delete()
        return

    # Fallback: file
    hf = get_history_file(lijst_id)
    if os.path.exists(hf):
        os.remove(hf)


def get_historie_uris(lijst_id):
    """Haal alle historie URIs op als set."""
    if _use_db():
        from db.session import get_session
        from db.models import HistorieEntry
        with get_session() as session:
            entries = (session.query(HistorieEntry.uri)
                       .filter_by(wissellijst_id=lijst_id)
                       .all())
            return {e.uri for e in entries}

    # Fallback: file
    uris = set()
    hf = get_history_file(lijst_id)
    if os.path.exists(hf):
        with open(hf, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().rsplit(" - ", 1)
                if len(parts) == 2 and parts[1].startswith("spotify:"):
                    uris.add(parts[1])
    return uris


# --- Wachtrij functies ---

def get_wachtrij(lijst_id):
    """Haal wachtrij-entries op voor een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import WachtrijEntry
        with get_session() as session:
            entries = (session.query(WachtrijEntry)
                       .filter_by(wissellijst_id=lijst_id)
                       .order_by(WachtrijEntry.positie)
                       .all())
            return [{"categorie": e.categorie, "artiest": e.artiest,
                     "titel": e.titel, "uri": e.uri} for e in entries]

    # Fallback: file
    entries = []
    qf = get_queue_file(lijst_id)
    if os.path.exists(qf):
        from suggest import _parse_history_line
        with open(qf, "r", encoding="utf-8") as f:
            for line in f:
                parsed = _parse_history_line(line)
                if parsed:
                    entries.append(parsed)
    return entries


def save_wachtrij(lijst_id, entries):
    """Sla wachtrij-entries op (vervangt bestaande)."""
    if _use_db():
        from db.session import get_session
        from db.models import WachtrijEntry
        with get_session() as session:
            # Verwijder bestaande wachtrij
            session.query(WachtrijEntry).filter_by(
                wissellijst_id=lijst_id).delete()
            # Voeg nieuwe entries toe
            for pos, entry in enumerate(entries):
                session.add(WachtrijEntry(
                    wissellijst_id=lijst_id,
                    categorie=entry.get("categorie", ""),
                    artiest=entry.get("artiest", ""),
                    titel=entry.get("titel", ""),
                    uri=entry.get("uri", ""),
                    positie=pos,
                ))
        return

    # Fallback: file
    qf = get_queue_file(lijst_id)
    os.makedirs(os.path.dirname(qf), exist_ok=True)
    with open(qf, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(f"{entry['categorie']} - {entry['artiest']} - "
                    f"{entry['titel']} - {entry['uri']}\n")


def clear_wachtrij(lijst_id):
    """Wis de wachtrij voor een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import WachtrijEntry
        with get_session() as session:
            session.query(WachtrijEntry).filter_by(
                wissellijst_id=lijst_id).delete()
        return

    # Fallback: file
    qf = get_queue_file(lijst_id)
    if os.path.exists(qf):
        with open(qf, "w") as f:
            f.write("")


def get_wachtrij_uris(lijst_id):
    """Haal alle wachtrij URIs op als set."""
    if _use_db():
        from db.session import get_session
        from db.models import WachtrijEntry
        with get_session() as session:
            entries = (session.query(WachtrijEntry.uri)
                       .filter_by(wissellijst_id=lijst_id)
                       .all())
            return {e.uri for e in entries}

    # Fallback: file
    uris = set()
    qf = get_queue_file(lijst_id)
    if os.path.exists(qf):
        with open(qf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(" - ", 1)
                if len(parts) == 2 and parts[1].startswith("spotify:"):
                    uris.add(parts[1])
                elif line.startswith("spotify:"):
                    uris.add(line)
    return uris


# --- Smaakprofiel functies ---

def get_smaakprofiel(lijst_id):
    """Haal het smaakprofiel op voor een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import Smaakprofiel
        with get_session() as session:
            sp = session.query(Smaakprofiel).get(lijst_id)
            return sp.profiel if sp else ""

    # Fallback: file
    pf = get_smaakprofiel_file(lijst_id)
    if os.path.exists(pf):
        with open(pf, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_smaakprofiel(lijst_id, profiel_tekst):
    """Sla het smaakprofiel op voor een wissellijst."""
    if _use_db():
        from db.session import get_session
        from db.models import Smaakprofiel
        with get_session() as session:
            sp = session.query(Smaakprofiel).get(lijst_id)
            if sp:
                sp.profiel = profiel_tekst
            else:
                session.add(Smaakprofiel(
                    wissellijst_id=lijst_id,
                    profiel=profiel_tekst,
                ))
        return

    # Fallback: file
    pf = get_smaakprofiel_file(lijst_id)
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    with open(pf, "w", encoding="utf-8") as f:
        f.write(profiel_tekst)


# --- File pad helpers (voor backward compatibility) ---

def get_history_file(lijst_id):
    """Geef het pad naar het historie-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"historie_{lijst_id}.txt")


def get_queue_file(lijst_id):
    """Geef het pad naar het wachtrij-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"wachtrij_{lijst_id}.txt")


def get_smaakprofiel_file(lijst_id):
    """Geef het pad naar het smaakprofiel-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"smaakprofiel_{lijst_id}.txt")
