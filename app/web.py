# -*- coding: utf-8 -*-
import os
import uuid
import threading
import datetime
import time
import atexit

from flask import Flask, render_template, request, jsonify, redirect
from spotipy.oauth2 import SpotifyOAuth

# Setup logging FIRST (before other imports that use it)
from logging_config import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from config import (
    load_wissellijsten, save_wissellijsten, get_wissellijst,
    save_wissellijst, delete_wissellijst,
    get_history_file, get_queue_file, get_smaakprofiel_file,
    get_historie, delete_historie_entry, clear_historie,
    get_wachtrij, save_wachtrij,
    get_smaakprofiel, save_smaakprofiel,
    DATA_DIR, HISTORY_FILE,
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE, CACHE_PATH,
)
from suggest import get_spotify_client, initial_fill, search_spotify, generate_block, _parse_history_line
from discovery import (
    build_taste_profile, generate_discovery_block, initial_fill_discovery,
)
from automation import rotate_and_regenerate
from mail import mail_configured, send_rotation_mail
from validators import validate_wissellijst_config

app = Flask(__name__)

# Voortgang bijhouden per taak
_tasks = {}


# --- Database & migratie init ---

def _run_alembic_migrations():
    """Draai Alembic migraties (upgrade to head)."""
    try:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_cfg.set_main_option(
            "script_location",
            os.path.join(os.path.dirname(__file__), "alembic"),
        )
        # DATABASE_URL uit environment
        db_url = os.getenv("DATABASE_URL", "")
        if db_url:
            alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migraties uitgevoerd")
    except Exception as e:
        logger.warning("Alembic migraties overgeslagen", extra={"error": str(e)})


def _init_database():
    """Initialiseer database en draai migratie als nodig."""
    try:
        from db.session import init_db, db_available, get_session
        from db.migrate_data import needs_migration, migrate_all

        success = init_db()
        if not success:
            logger.warning("Database niet geconfigureerd, fallback naar bestanden")
            return False

        logger.info("Database verbinding OK")

        # Alembic migraties draaien (schema updates)
        _run_alembic_migrations()

        # Auto-migratie als DB leeg is en bestanden bestaan
        with get_session() as session:
            if needs_migration(session):
                logger.info("Data migratie starten (DB leeg, bestanden gevonden)")
                migrate_all(session)
                logger.info("Data migratie voltooid")

        return True
    except Exception as e:
        logger.error("Database initialisatie mislukt", extra={"error": str(e)})
        return False


# --- Scheduler init ---

_wl_scheduler = None

def _init_scheduler():
    """Start APScheduler en laad jobs."""
    global _wl_scheduler
    try:
        from scheduler import get_scheduler
        _wl_scheduler = get_scheduler()
        _wl_scheduler.start()
        _wl_scheduler.reload_jobs()
        logger.info("APScheduler gestart")
    except Exception as e:
        logger.error("Scheduler start mislukt", extra={"error": str(e)})


# --- Startup ---

db_ok = _init_database()
_init_scheduler()


@app.route("/health")
def health():
    """Health check endpoint voor deployment verificatie."""
    return jsonify({
        "status": "ok",
        "app": "wissellijst",
        "version": "3.1",
        "database": db_ok,
    })


def _get_auth_manager():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    """Redirect naar Spotify login."""
    auth_manager = _get_auth_manager()
    auth_url = auth_manager.get_authorize_url()
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """Ontvang de auth code van Spotify en sla het token op."""
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return jsonify({"error": error}), 400
    if not code:
        return jsonify({"error": "Geen code ontvangen"}), 400

    auth_manager = _get_auth_manager()
    auth_manager.get_access_token(code)
    return redirect("/")


@app.route("/api/playlists")
def api_playlists():
    """Haal alle playlists van de Spotify gebruiker op."""
    try:
        sp = get_spotify_client()
        results = []
        offset = 0
        while True:
            batch = sp.current_user_playlists(limit=50, offset=offset)
            results.extend(batch["items"])
            if not batch["next"]:
                break
            offset += 50

        playlists = [
            {
                "id": p["id"],
                "naam": p["name"],
                "tracks": p["tracks"]["total"],
                "image": p["images"][0]["url"] if p.get("images") else None,
            }
            for p in results
        ]
        return jsonify(playlists)
    except Exception as e:
        if "auth_required" in str(e):
            return jsonify({"error": "auth_required"}), 401
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists/<playlist_id>")
def api_playlist_info(playlist_id):
    """Haal info op over een specifieke Spotify playlist."""
    try:
        sp = get_spotify_client()
        p = sp.playlist(playlist_id, fields='id,name,tracks(total),images')
        return jsonify({
            "id": p["id"],
            "naam": p["name"],
            "tracks": p["tracks"]["total"],
            "image": p["images"][0]["url"] if p.get("images") else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists", methods=["POST"])
def api_playlist_aanmaken():
    """Maak een nieuwe Spotify playlist aan."""
    try:
        body = request.json
        naam = body.get("naam", "").strip()
        if not naam:
            return jsonify({"error": "Naam is verplicht"}), 400

        sp = get_spotify_client()
        user_id = sp.current_user()["id"]
        playlist = sp.user_playlist_create(user_id, naam, public=False)
        return jsonify({
            "id": playlist["id"],
            "naam": playlist["name"],
            "tracks": 0,
            "image": playlist["images"][0]["url"] if playlist.get("images") else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/smaakprofiel", methods=["POST"])
def api_smaakprofiel_generiek():
    """Genereer smaakprofiel van Spotify (zonder aan lijst te koppelen)."""
    try:
        sp = get_spotify_client()
        profiel = build_taste_profile(sp)
        return jsonify({"profiel": profiel})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel", methods=["GET"])
def api_smaakprofiel_get(lijst_id):
    """Haal het opgeslagen smaakprofiel op voor een wissellijst."""
    profiel = get_smaakprofiel(lijst_id)
    return jsonify({"profiel": profiel})


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel/ophalen", methods=["POST"])
def api_smaakprofiel_ophalen(lijst_id):
    """Haal smaakprofiel op van Spotify en sla op (behoudt eigen toevoegingen)."""
    try:
        sp = get_spotify_client()
        spotify_profiel = build_taste_profile(sp)

        # Lees bestaand profiel om eigen toevoegingen te behouden
        bestaand = get_smaakprofiel(lijst_id)
        eigen_sectie = ""
        marker = "=== EIGEN TOEVOEGINGEN ==="
        if marker in bestaand:
            eigen_sectie = bestaand[bestaand.index(marker):]

        # Combineer Spotify + eigen
        volledig = spotify_profiel
        if eigen_sectie:
            volledig += "\n\n" + eigen_sectie

        save_smaakprofiel(lijst_id, volledig)

        # Update ook in wissellijst config
        wl = get_wissellijst(lijst_id)
        if wl:
            wl["smaakprofiel"] = volledig
            save_wissellijst(wl)

        return jsonify({"profiel": volledig})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel", methods=["POST"])
def api_smaakprofiel_opslaan(lijst_id):
    """Sla het (bewerkte) smaakprofiel op voor een wissellijst."""
    try:
        body = request.get_json()
        profiel = body.get("profiel", "")

        save_smaakprofiel(lijst_id, profiel)

        # Update ook in wissellijst config
        wl = get_wissellijst(lijst_id)
        if wl:
            wl["smaakprofiel"] = profiel
            save_wissellijst(wl)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten")
def api_wissellijsten():
    """Haal alle opgeslagen wissellijst-configuraties op."""
    data = load_wissellijsten()
    return jsonify(data["wissellijsten"])


@app.route("/api/wissellijsten", methods=["POST"])
def api_wissellijst_opslaan():
    """Maak een nieuwe wissellijst aan of update een bestaande."""
    body = request.json

    # Validatie
    is_valid, errors = validate_wissellijst_config(body)
    if not is_valid:
        return jsonify({"error": "; ".join(errors)}), 400

    lijst_id = body.get("id")
    if not lijst_id:
        body["id"] = str(uuid.uuid4())[:8]

    # Sla smaakprofiel ook apart op als het er is
    if body.get("smaakprofiel"):
        save_smaakprofiel(body["id"], body["smaakprofiel"])

    result = save_wissellijst(body)

    # Update scheduler job
    if _wl_scheduler:
        _wl_scheduler.update_job(body)

    return jsonify(result or body)


@app.route("/api/wissellijsten/<lijst_id>", methods=["DELETE"])
def api_wissellijst_verwijderen(lijst_id):
    """Verwijder een wissellijst-configuratie."""
    delete_wissellijst(lijst_id)

    # Verwijder scheduler job
    if _wl_scheduler:
        job_id = f"wl_{lijst_id}"
        existing = _wl_scheduler.scheduler.get_job(job_id)
        if existing:
            existing.remove()

    return jsonify({"ok": True})


@app.route("/api/wissellijsten/<lijst_id>/herstarten", methods=["POST"])
def api_wissellijst_herstarten(lijst_id):
    """Herstart een wissellijst: leeg de playlist, historie en wachtrij."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    try:
        sp = get_spotify_client()
    except Exception:
        return jsonify({"error": "auth_required"}), 401

    playlist_id = wl["playlist_id"]

    # Playlist leeghalen
    try:
        items = sp.playlist_items(playlist_id, fields="items(track(uri)),next",
                                  limit=100)
        uris = []
        for item in items["items"]:
            if item.get("track") and item["track"].get("uri"):
                uris.append(item["track"]["uri"])
        while items.get("next"):
            items = sp.next(items)
            for item in items["items"]:
                if item.get("track") and item["track"].get("uri"):
                    uris.append(item["track"]["uri"])

        if uris:
            for i in range(0, len(uris), 100):
                sp.playlist_remove_all_occurrences_of_items(
                    playlist_id, uris[i:i + 100])
    except Exception as e:
        return jsonify({"error": f"Kon playlist niet leeghalen: {e}"}), 500

    # Historie en wachtrij leegmaken
    clear_historie(lijst_id)
    from config import clear_wachtrij
    clear_wachtrij(lijst_id)

    return jsonify({
        "ok": True,
        "verwijderd": len(uris),
        "tekst": f"Playlist leeggemaakt ({len(uris)} tracks), historie en wachtrij gewist.",
    })


@app.route("/api/vullen", methods=["POST"])
def api_vullen():
    """Start het initieel vullen van een wissellijst (async)."""
    body = request.json
    lijst_id = body.get("lijst_id")

    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    task_id = str(uuid.uuid4())[:8]
    aantal_blokken = wl.get("aantal_blokken", 10)
    _tasks[task_id] = {"status": "bezig", "voortgang": 0, "tekst": "Starten...", "resultaat": None}

    is_discovery = wl.get("type") == "discovery"

    def run():
        def on_progress(blok_nr, totaal, tekst):
            _tasks[task_id]["voortgang"] = round(blok_nr / totaal * 100)
            _tasks[task_id]["tekst"] = tekst

        try:
            if is_discovery:
                result = initial_fill_discovery(
                    playlist_id=wl["playlist_id"],
                    wl=wl,
                    history_file=get_history_file(lijst_id),
                    queue_file=get_queue_file(lijst_id),
                    on_progress=on_progress,
                )
            else:
                result = initial_fill(
                    playlist_id=wl["playlist_id"],
                    categorieen=wl.get("categorieen", []),
                    history_file=get_history_file(lijst_id),
                    queue_file=get_queue_file(lijst_id),
                    wl_id=lijst_id,
                    max_per_artiest=wl.get("max_per_artiest", 0),
                    aantal_blokken=aantal_blokken,
                    on_progress=on_progress,
                )
            _tasks[task_id]["status"] = "klaar"
            _tasks[task_id]["voortgang"] = 100
            _tasks[task_id]["tekst"] = f"{result['toegevoegd']} nummers toegevoegd ({result['blokken']} blokken)"
            _tasks[task_id]["resultaat"] = result
        except Exception as e:
            _tasks[task_id]["status"] = "fout"
            _tasks[task_id]["tekst"] = str(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/api/vullen/<task_id>")
def api_vullen_status(task_id):
    """Check de voortgang van een vul-taak."""
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "Taak niet gevonden"}), 404
    return jsonify(task)


# --- Historie ---

@app.route("/api/wissellijsten/<lijst_id>/historie")
def api_historie(lijst_id):
    """Haal de historie op van een wissellijst."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    entries = get_historie(lijst_id)
    return jsonify(entries)


@app.route("/api/wissellijsten/<lijst_id>/historie/<int:entry_index>", methods=["DELETE"])
def api_historie_verwijderen(lijst_id, entry_index):
    """Verwijder een historie-entry op basis van index."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    success = delete_historie_entry(lijst_id, entry_index)
    if not success:
        # Fallback naar file-based verwijdering
        history_file = get_history_file(lijst_id)
        if not os.path.exists(history_file):
            return jsonify({"error": "Geen historie gevonden"}), 404

        with open(history_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        valid_index = 0
        new_lines = []
        removed = False
        for line in lines:
            parsed = _parse_history_line(line)
            if parsed:
                if valid_index == entry_index:
                    removed = True
                else:
                    new_lines.append(line)
                valid_index += 1
            else:
                new_lines.append(line)

        if not removed:
            return jsonify({"error": "Index niet gevonden"}), 404

        with open(history_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    return jsonify({"ok": True})


@app.route("/api/wissellijsten/<lijst_id>/historie", methods=["DELETE"])
def api_historie_wissen(lijst_id):
    """Wis de volledige historie van een wissellijst."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    clear_historie(lijst_id)
    return jsonify({"ok": True, "tekst": f"Historie van '{wl['naam']}' gewist."})


# --- Wachtrij ---

@app.route("/api/wissellijsten/<lijst_id>/wachtrij")
def api_wachtrij(lijst_id):
    """Haal de wachtrij op van een wissellijst."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    return jsonify(get_wachtrij(lijst_id))


@app.route("/api/wissellijsten/<lijst_id>/wachtrij/vervang", methods=["POST"])
def api_wachtrij_vervang(lijst_id):
    """Vervang een track in de wachtrij."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    body = request.json
    entry_index = body.get("index")
    artiest = body.get("artiest", "").strip()
    titel = body.get("titel", "").strip()

    if not artiest or not titel:
        return jsonify({"error": "Artiest en titel zijn verplicht"}), 400

    entries = get_wachtrij(lijst_id)
    if entry_index is None or entry_index < 0 or entry_index >= len(entries):
        return jsonify({"error": "Ongeldige index"}), 400

    # Zoek op Spotify
    try:
        sp = get_spotify_client()
        result = search_spotify(sp, artiest, titel)
        if not result:
            return jsonify({"error": f"Track '{artiest} - {titel}' niet gevonden op Spotify"}), 404
        uri = result["uri"]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Behoud de categorie, update de rest
    entries[entry_index] = {
        "categorie": entries[entry_index]["categorie"],
        "artiest": artiest,
        "titel": titel,
        "uri": uri,
    }

    save_wachtrij(lijst_id, entries)
    return jsonify(entries)


@app.route("/api/wissellijsten/<lijst_id>/wachtrij/genereer", methods=["POST"])
def api_wachtrij_genereer(lijst_id):
    """Genereer een nieuw wachtrij-blokje (async)."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "bezig", "voortgang": 0, "tekst": "Wachtrij genereren...", "resultaat": None}

    is_discovery = wl.get("type") == "discovery"

    def run():
        try:
            _tasks[task_id]["voortgang"] = 20
            _tasks[task_id]["tekst"] = ("Bronlijsten scannen..."
                                        if is_discovery
                                        else "Nieuw blokje genereren...")

            sp = get_spotify_client()
            history_file = get_history_file(lijst_id)
            block = None

            if is_discovery:
                _tasks[task_id]["voortgang"] = 40
                _tasks[task_id]["tekst"] = "Tracks scoren met smaakprofiel..."
                block = generate_discovery_block(
                    sp, wl, history_file,
                    block_size=wl.get("blok_grootte", 10),
                    wl_id=lijst_id,
                )
            else:
                max_retries = 3
                for attempt in range(max_retries):
                    _tasks[task_id]["voortgang"] = 20 + (attempt * 25)
                    block = generate_block(
                        sp, wl["playlist_id"], wl.get("categorieen", []),
                        history_file=history_file,
                        wl_id=lijst_id,
                        max_per_artiest=wl.get("max_per_artiest", 0),
                    )
                    if block:
                        break

            if block:
                save_wachtrij(lijst_id, block)
                _tasks[task_id]["status"] = "klaar"
                _tasks[task_id]["voortgang"] = 100
                _tasks[task_id]["tekst"] = f"Wachtrij aangemaakt: {len(block)} tracks"
                _tasks[task_id]["resultaat"] = {"tracks": len(block)}
            else:
                _tasks[task_id]["status"] = "fout"
                _tasks[task_id]["tekst"] = "Kon geen blokje genereren"

        except Exception as e:
            _tasks[task_id]["status"] = "fout"
            _tasks[task_id]["tekst"] = str(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


# --- Rotatie ---

@app.route("/api/wissellijsten/<lijst_id>/roteren", methods=["POST"])
def api_roteren(lijst_id):
    """Start een rotatie voor een wissellijst (async)."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    is_discovery = wl.get("type") == "discovery"
    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "bezig", "voortgang": 0,
                       "tekst": "Starten...", "resultaat": None}

    def run():
        try:
            _tasks[task_id]["voortgang"] = 30
            _tasks[task_id]["tekst"] = ("Bronlijsten scannen & analyseren..."
                                        if is_discovery
                                        else "Oudste blok verwijderen en wachtrij toevoegen...")

            result = rotate_and_regenerate(wl)

            _tasks[task_id]["voortgang"] = 100
            _tasks[task_id]["status"] = "klaar"
            _tasks[task_id]["tekst"] = result["tekst"]
            _tasks[task_id]["resultaat"] = result

            # Update laatste rotatie in config
            wl["laatste_rotatie"] = datetime.datetime.now().isoformat()
            save_wissellijst(wl)

            # Stuur e-mail notificatie als ingeschakeld
            if wl.get("mail_na_rotatie") and wl.get("mail_adres") and result.get("status") == "ok":
                logger.info("Rotatie-mail versturen",
                            extra={"naar": wl["mail_adres"], "wissellijst": wl["naam"]})
                send_rotation_mail(
                    wl["mail_adres"], wl["naam"],
                    result.get("verwijderd_detail", []),
                    result.get("toegevoegd_detail", []),
                )

        except Exception as e:
            _tasks[task_id]["status"] = "fout"
            _tasks[task_id]["tekst"] = str(e)
            logger.error("Rotatie mislukt", extra={"error": str(e),
                                                    "wissellijst_id": lijst_id})

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


# --- Rotatie historie (NIEUW) ---

@app.route("/api/wissellijsten/<lijst_id>/rotaties")
def api_rotaties(lijst_id):
    """Haal rotatie-runs op voor een wissellijst (audit trail)."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    try:
        from db.session import db_available, get_session
        from db.models import RotatieRun, RotatieWijziging

        if not db_available():
            return jsonify([])

        with get_session() as session:
            runs = (session.query(RotatieRun)
                    .filter_by(wissellijst_id=lijst_id)
                    .order_by(RotatieRun.started_at.desc())
                    .limit(50)
                    .all())

            result = []
            for run in runs:
                wijzigingen = (session.query(RotatieWijziging)
                               .filter_by(run_id=run.id)
                               .all())

                result.append({
                    "id": run.id,
                    "triggered_by": run.triggered_by,
                    "status": run.status,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "tracks_verwijderd": run.tracks_verwijderd,
                    "tracks_toegevoegd": run.tracks_toegevoegd,
                    "error_message": run.error_message,
                    "wijzigingen": [
                        {
                            "type": w.type,
                            "artiest": w.artiest,
                            "titel": w.titel,
                        }
                        for w in wijzigingen
                    ],
                })

            return jsonify(result)
    except ImportError:
        return jsonify([])


# --- Scheduler info ---

@app.route("/api/scheduler/jobs")
def api_scheduler_jobs():
    """Lijst van geplande scheduler jobs."""
    if _wl_scheduler:
        return jsonify(_wl_scheduler.get_jobs())
    return jsonify([])


# --- Shutdown ---

@atexit.register
def _shutdown():
    if _wl_scheduler:
        _wl_scheduler.shutdown()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
