# -*- coding: utf-8 -*-
"""Wissellijst V3: Flask-app (UI + API). Start via gunicorn (zie Dockerfile)."""
import atexit
import threading
import time
import uuid

from flask import Flask, render_template, request, jsonify, redirect, session
from markupsafe import escape

# Logging eerst, zodat de andere modules hem gebruiken.
from logging_config import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from config import (  # noqa: E402
    load_wissellijsten, get_wissellijst, save_wissellijst, delete_wissellijst,
    get_historie, delete_historie_entry, clear_historie, import_historie,
    get_wachtrij, save_wachtrij, clear_wachtrij,
    get_smaakprofiel, save_smaakprofiel,
)
import spotify_auth  # noqa: E402
from spotify_auth import get_spotify_client  # noqa: E402
from suggest import initial_fill, search_spotify, generate_blocks  # noqa: E402
from discovery import build_taste_profile, generate_discovery_block, initial_fill_discovery  # noqa: E402
from mail import mail_configured  # noqa: E402
from validators import validate_wissellijst_config  # noqa: E402
import auth  # noqa: E402
import alerts  # noqa: E402

app = Flask(__name__)
auth.setup(app)

VERSION = "3.2"

# Voortgang per achtergrondtaak; klaar/fout-taken worden na een uur opgeruimd.
_tasks = {}
_TASK_TTL = 3600


# --- Startup: database, migraties, scheduler ---

def _init_database():
    from db.session import init_db, migrate, get_session
    from db.migrate_data import needs_migration, migrate_all

    if not init_db():
        logger.error("DATABASE_URL niet gezet; de app kan niet werken zonder database")
        return False
    migrate()
    with get_session() as s:
        if needs_migration(s):
            logger.info("Data migratie starten (DB leeg, V2-bestanden gevonden)")
            migrate_all(s)
    spotify_auth.import_cache_file()
    logger.info("Database verbinding OK")
    return True


_wl_scheduler = None


def _init_scheduler():
    global _wl_scheduler
    try:
        from scheduler import get_scheduler
        _wl_scheduler = get_scheduler()
        _wl_scheduler.start()
        _wl_scheduler.reload_jobs()
    except Exception as e:
        logger.error("Scheduler start mislukt", extra={"error": str(e)})


db_ok = _init_database()
if db_ok:
    _init_scheduler()


@atexit.register
def _shutdown():
    if _wl_scheduler:
        _wl_scheduler.shutdown()


# --- Taken ---

def _new_task(tekst="Starten..."):
    now = time.time()
    for tid in [t for t, v in _tasks.items()
                if v["status"] != "bezig" and now - v.get("_t", now) > _TASK_TTL]:
        _tasks.pop(tid, None)
    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "bezig", "voortgang": 0, "tekst": tekst,
                       "resultaat": None, "_t": now}
    return task_id


def _run_task(task_id, fn):
    def run():
        try:
            fn(_tasks[task_id])
            _tasks[task_id]["status"] = "klaar"
            _tasks[task_id]["voortgang"] = 100
        except Exception as e:
            _tasks[task_id]["status"] = "fout"
            _tasks[task_id]["tekst"] = str(e)
        _tasks[task_id]["_t"] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id})


@app.route("/api/vullen/<task_id>")
def api_task_status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "Taak niet gevonden"}), 404
    return jsonify({k: v for k, v in task.items() if not k.startswith("_")})


# --- Basis ---

@app.route("/health")
def health():
    return jsonify({"status": "ok", "app": "wissellijst", "version": VERSION, "database": db_ok})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Status voor de UI: Spotify-koppeling, beveiliging, mail, laatste fout."""
    return jsonify({
        "spotify": {**alerts.spotify_status(), "token": spotify_auth.token_status()},
        "auth_enabled": auth.auth_enabled(),
        "mail_configured": mail_configured(),
        "laatste_fout": alerts.laatste_fout(),
        "version": VERSION,
    })


# --- Spotify koppelen (OAuth met state tegen CSRF) ---

@app.route("/login")
def login():
    state = spotify_auth.new_state()
    session["spotify_state"] = state
    return redirect(spotify_auth.make_auth_manager(state=state).get_authorize_url())


@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")
    state = request.args.get("state", "")

    def fout(tekst, status=400):
        return (f"<h2>Koppelen met Spotify mislukt</h2><p>{escape(tekst)}</p>"
                "<p><a href=\"/login\">Opnieuw proberen</a></p>", status)

    if error:
        return fout(error)
    if not code:
        return fout("Geen code ontvangen")
    if not state or state != session.pop("spotify_state", None):
        return fout("Ongeldige state (start de koppeling opnieuw via /login)")

    try:
        # check_cache=False: anders ververst spotipy eerst het oude token en
        # faalt dat op een ingetrokken refresh-token zonder de code te gebruiken.
        auth_manager = spotify_auth.make_auth_manager(state=state)
        auth_manager.get_access_token(code, as_dict=False, check_cache=False)
        me = get_spotify_client().current_user()
        spotify_auth.set_spotify_user(me.get("display_name") or me.get("id"))
    except Exception as e:
        logger.error("Spotify koppelen mislukt", extra={"error": str(e)})
        return fout(str(e), 500)

    logger.info("Spotify gekoppeld")
    alerts.invalidate_spotify_status()
    alerts.clear_failures()
    return redirect("/")


# --- Playlists ---

def _playlist_dict(p):
    return {
        "id": p["id"],
        "naam": p["name"],
        "tracks": p["tracks"]["total"],
        "image": p["images"][0]["url"] if p.get("images") else None,
    }


@app.route("/api/playlists")
def api_playlists():
    try:
        sp = get_spotify_client()
        results, offset = [], 0
        while True:
            batch = sp.current_user_playlists(limit=50, offset=offset)
            results.extend(batch["items"])
            if not batch["next"]:
                break
            offset += 50
        return jsonify([_playlist_dict(p) for p in results])
    except Exception as e:
        if "auth_required" in str(e):
            return jsonify({"error": "auth_required"}), 401
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists/<playlist_id>")
def api_playlist_info(playlist_id):
    try:
        p = get_spotify_client().playlist(playlist_id, fields="id,name,tracks(total),images")
        return jsonify(_playlist_dict(p))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists", methods=["POST"])
def api_playlist_aanmaken():
    try:
        naam = (request.get_json(silent=True) or {}).get("naam", "").strip()
        if not naam:
            return jsonify({"error": "Naam is verplicht"}), 400
        sp = get_spotify_client()
        playlist = sp.user_playlist_create(sp.current_user()["id"], naam, public=False)
        return jsonify({**_playlist_dict({**playlist, "tracks": {"total": 0}})})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Smaakprofiel ---

@app.route("/api/smaakprofiel", methods=["POST"])
def api_smaakprofiel_generiek():
    try:
        return jsonify({"profiel": build_taste_profile(get_spotify_client())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel", methods=["GET"])
def api_smaakprofiel_get(lijst_id):
    return jsonify({"profiel": get_smaakprofiel(lijst_id)})


def _sla_profiel_op(lijst_id, profiel):
    save_smaakprofiel(lijst_id, profiel)
    wl = get_wissellijst(lijst_id)
    if wl:
        wl["smaakprofiel"] = profiel
        save_wissellijst(wl)


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel/ophalen", methods=["POST"])
def api_smaakprofiel_ophalen(lijst_id):
    """Haal het profiel van Spotify en behoud de sectie 'eigen toevoegingen'."""
    try:
        spotify_profiel = build_taste_profile(get_spotify_client())
        bestaand = get_smaakprofiel(lijst_id)
        marker = "=== EIGEN TOEVOEGINGEN ==="
        volledig = spotify_profiel
        if marker in bestaand:
            volledig += "\n\n" + bestaand[bestaand.index(marker):]
        _sla_profiel_op(lijst_id, volledig)
        return jsonify({"profiel": volledig})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel", methods=["POST"])
def api_smaakprofiel_opslaan(lijst_id):
    try:
        _sla_profiel_op(lijst_id, (request.get_json(silent=True) or {}).get("profiel", ""))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Wissellijsten ---

@app.route("/api/wissellijsten")
def api_wissellijsten():
    return jsonify(load_wissellijsten()["wissellijsten"])


@app.route("/api/wissellijsten", methods=["POST"])
def api_wissellijst_opslaan():
    body = request.get_json(silent=True) or {}
    is_valid, errors = validate_wissellijst_config(body)
    if not is_valid:
        return jsonify({"error": "; ".join(errors)}), 400
    if not body.get("id"):
        body["id"] = str(uuid.uuid4())[:8]
    if body.get("smaakprofiel"):
        save_smaakprofiel(body["id"], body["smaakprofiel"])
    result = save_wissellijst(body)
    if _wl_scheduler:
        _wl_scheduler.update_job(body)
    return jsonify(result)


@app.route("/api/wissellijsten/<lijst_id>", methods=["DELETE"])
def api_wissellijst_verwijderen(lijst_id):
    delete_wissellijst(lijst_id)
    if _wl_scheduler:
        _wl_scheduler.remove_job(lijst_id)
    return jsonify({"ok": True})


@app.route("/api/wissellijsten/<lijst_id>/herstarten", methods=["POST"])
def api_wissellijst_herstarten(lijst_id):
    """Leeg de playlist, historie en wachtrij."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    try:
        sp = get_spotify_client()
    except Exception:
        return jsonify({"error": "auth_required"}), 401

    playlist_id = wl["playlist_id"]
    try:
        uris = []
        items = sp.playlist_items(playlist_id, fields="items(track(uri)),next", limit=100)
        while True:
            uris.extend(i["track"]["uri"] for i in items["items"]
                        if i.get("track") and i["track"].get("uri"))
            if not items.get("next"):
                break
            items = sp.next(items)
        for i in range(0, len(uris), 100):
            sp.playlist_remove_all_occurrences_of_items(playlist_id, uris[i:i + 100])
    except Exception as e:
        return jsonify({"error": f"Kon playlist niet leeghalen: {e}"}), 500

    clear_historie(lijst_id)
    clear_wachtrij(lijst_id)
    return jsonify({"ok": True, "verwijderd": len(uris),
                    "tekst": f"Playlist leeggemaakt ({len(uris)} tracks), historie en wachtrij gewist."})


@app.route("/api/vullen", methods=["POST"])
def api_vullen():
    """Initieel vullen (async)."""
    lijst_id = (request.get_json(silent=True) or {}).get("lijst_id")
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    def werk(task):
        def on_progress(blok_nr, totaal, tekst):
            task["voortgang"] = round(blok_nr / totaal * 100)
            task["tekst"] = tekst

        if wl.get("type") == "discovery":
            result = initial_fill_discovery(wl["playlist_id"], wl, on_progress=on_progress)
        else:
            result = initial_fill(wl["playlist_id"], wl.get("categorieen", []), lijst_id,
                                  max_per_artiest=wl.get("max_per_artiest", 0),
                                  aantal_blokken=wl.get("aantal_blokken", 10),
                                  blokken_per_rotatie=wl.get("blokken_per_rotatie", 1),
                                  populariteit=wl.get("populariteit_verdeling", False),
                                  radio=wl.get("radiolijsten", False),
                                  on_progress=on_progress)
        task["tekst"] = f"{result['toegevoegd']} nummers toegevoegd ({result['blokken']} blokken)"
        task["resultaat"] = result

    return _run_task(_new_task(), werk)


# --- Historie ---

@app.route("/api/wissellijsten/<lijst_id>/historie")
def api_historie(lijst_id):
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    return jsonify(get_historie(lijst_id))


@app.route("/api/wissellijsten/<lijst_id>/historie/<int:entry_index>", methods=["DELETE"])
def api_historie_verwijderen(lijst_id, entry_index):
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    if not delete_historie_entry(lijst_id, entry_index):
        return jsonify({"error": "Index niet gevonden"}), 404
    return jsonify({"ok": True})


@app.route("/api/wissellijsten/<lijst_id>/historie", methods=["DELETE"])
def api_historie_wissen(lijst_id):
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    clear_historie(lijst_id)
    return jsonify({"ok": True, "tekst": f"Historie van '{wl['naam']}' gewist."})


@app.route("/api/wissellijsten/<lijst_id>/historie/import", methods=["POST"])
def api_historie_importeren(lijst_id):
    """Importeer historie uit tekst (bijv. een oud historie.txt)."""
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    tekst = (request.get_json(silent=True) or {}).get("tekst", "")
    if not isinstance(tekst, str) or not tekst.strip():
        return jsonify({"error": "Geen tekst om te importeren"}), 400
    try:
        result = import_historie(lijst_id, tekst)
    except Exception as e:
        logger.error("Historie import mislukt", extra={"wissellijst_id": lijst_id, "error": str(e)})
        return jsonify({"error": str(e)}), 500
    logger.info("Historie geimporteerd", extra={"wissellijst_id": lijst_id, **result})
    return jsonify({**result, "ok": True,
                    "tekst": (f"{result['toegevoegd']} nummers toegevoegd, "
                              f"{result['dubbel']} stonden er al, "
                              f"{result['ongeldig']} regels overgeslagen.")})


# --- Wachtrij ---

@app.route("/api/wissellijsten/<lijst_id>/wachtrij")
def api_wachtrij(lijst_id):
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    return jsonify(get_wachtrij(lijst_id))


@app.route("/api/wissellijsten/<lijst_id>/wachtrij/vervang", methods=["POST"])
def api_wachtrij_vervang(lijst_id):
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    body = request.get_json(silent=True) or {}
    entry_index = body.get("index")
    artiest = (body.get("artiest") or "").strip()
    titel = (body.get("titel") or "").strip()
    if not artiest or not titel:
        return jsonify({"error": "Artiest en titel zijn verplicht"}), 400

    entries = get_wachtrij(lijst_id)
    if entry_index is None or not (0 <= entry_index < len(entries)):
        return jsonify({"error": "Ongeldige index"}), 400
    try:
        result = search_spotify(get_spotify_client(), artiest, titel)
        if not result:
            return jsonify({"error": f"Track '{artiest} - {titel}' niet gevonden op Spotify"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    entries[entry_index] = {"categorie": entries[entry_index]["categorie"],
                            "artiest": artiest, "titel": titel, "uri": result["uri"]}
    save_wachtrij(lijst_id, entries)
    return jsonify(entries)


@app.route("/api/wissellijsten/<lijst_id>/wachtrij/genereer", methods=["POST"])
def api_wachtrij_genereer(lijst_id):
    """Genereer een nieuw wachtrij-blok (async)."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    per_rotatie = wl.get("blokken_per_rotatie", 1)

    def werk(task):
        task["voortgang"] = 20
        sp = get_spotify_client()
        if wl.get("type") == "discovery":
            task["tekst"] = "Bronlijsten scannen en scoren..."
            block = generate_discovery_block(
                sp, wl, block_size=wl.get("blok_grootte", 10) * per_rotatie, wl_id=lijst_id)
        else:
            def on_progress(blok_nr, aantal):
                task["voortgang"] = 20 + round((blok_nr - 1) / aantal * 70)
                task["tekst"] = (f"Blokje {blok_nr}/{aantal} genereren..." if aantal > 1
                                 else "Nieuw blokje genereren...")
            block = generate_blocks(sp, wl["playlist_id"], wl.get("categorieen", []), lijst_id,
                                    wl.get("max_per_artiest", 0), aantal=per_rotatie,
                                    on_progress=on_progress,
                                    populariteit=wl.get("populariteit_verdeling", False),
                                    radio=wl.get("radiolijsten", False))
        if not block:
            raise RuntimeError("Kon geen blokje genereren")
        save_wachtrij(lijst_id, block)
        task["tekst"] = f"Wachtrij aangemaakt: {len(block)} tracks"
        task["resultaat"] = {"tracks": len(block)}

    return _run_task(_new_task("Wachtrij genereren..."), werk)


# --- Rotatie ---

@app.route("/api/wissellijsten/<lijst_id>/roteren", methods=["POST"])
def api_roteren(lijst_id):
    """Handmatige rotatie (async), via dezelfde code als de scheduler: met audit trail."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    def werk(task):
        from scheduler import _execute_rotation
        task["voortgang"] = 30
        task["tekst"] = ("Bronlijsten scannen en analyseren..." if wl.get("type") == "discovery"
                         else "Oudste blok verwijderen en wachtrij toevoegen...")
        result = _execute_rotation(lijst_id, triggered_by="user")
        task["tekst"] = result["tekst"]
        task["resultaat"] = result
        if result.get("status") == "fout":
            raise RuntimeError(result["tekst"])

    return _run_task(_new_task(), werk)


@app.route("/api/wissellijsten/<lijst_id>/rotaties")
def api_rotaties(lijst_id):
    """Rotatie-runs (audit trail), nieuwste eerst."""
    if not get_wissellijst(lijst_id):
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    from db.session import get_session
    from db.models import RotatieRun, RotatieWijziging

    with get_session() as s:
        runs = (s.query(RotatieRun).filter_by(wissellijst_id=lijst_id)
                .order_by(RotatieRun.started_at.desc()).limit(50).all())
        return jsonify([{
            "id": run.id,
            "triggered_by": run.triggered_by,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "tracks_verwijderd": run.tracks_verwijderd,
            "tracks_toegevoegd": run.tracks_toegevoegd,
            "error_message": run.error_message,
            "wijzigingen": [{"type": w.type, "artiest": w.artiest, "titel": w.titel}
                            for w in s.query(RotatieWijziging).filter_by(run_id=run.id).all()],
        } for run in runs])


@app.route("/rotaties/<int:run_id>/post")
def rotatie_post(run_id):
    """Instagram-post van een rotatie: wat eruit ging en wat erin kwam."""
    from post import post_data
    data = post_data(run_id)
    if not data:
        return "Rotatie niet gevonden", 404
    return render_template("post.html", **data)


# --- Radiolijsten (NL-populariteit) ---

@app.route("/api/radiolijsten")
def api_radiolijsten():
    import radio
    return jsonify(radio.lijsten())


@app.route("/api/radiolijsten", methods=["POST"])
def api_radiolijst_importeren():
    """Importeer een radiolijst uit een Spotify-playlist (playlist) of geplakte tekst (tekst)."""
    import re
    import radio
    body = request.get_json(silent=True) or {}
    naam = (body.get("naam") or "").strip()
    zender = (body.get("zender") or "").strip()
    decennium = (body.get("decennium") or "").strip().lower()
    if decennium and not re.fullmatch(r"\d0s", decennium):
        return jsonify({"error": "Decennium moet leeg zijn of zoals '80s'"}), 400

    playlist_id = ""
    if (body.get("playlist") or "").strip():
        playlist_id = radio.playlist_id_uit(body["playlist"])
        if not playlist_id:
            return jsonify({"error": "Geen geldige Spotify-playlist (URL of ID)"}), 400
        try:
            spotify_naam, entries = radio.lees_playlist(get_spotify_client(), playlist_id)
        except Exception as e:
            logger.error("Radiolijst lezen mislukt", extra={"playlist_id": playlist_id, "error": str(e)})
            return jsonify({"error": f"Kon de playlist niet lezen bij Spotify: {e}. "
                                     "Plak de lijst anders als tekst."}), 502
        naam = naam or spotify_naam
    else:
        entries = radio.parse_tekst(body.get("tekst", ""))
    if not naam:
        return jsonify({"error": "Naam is verplicht"}), 400
    if not entries:
        return jsonify({"error": "Geen nummers gevonden in de lijst"}), 400

    result = radio.sla_op(naam, zender, decennium, entries, spotify_playlist_id=playlist_id)
    logger.info("Radiolijst geïmporteerd", extra={"naam": naam, "nummers": len(entries)})
    return jsonify({**result, "tekst": f"{naam}: {len(entries)} nummers geïmporteerd."})


@app.route("/api/radiolijsten/<int:lijst_id>/vernieuwen", methods=["POST"])
def api_radiolijst_vernieuwen(lijst_id):
    """Lees een radiolijst opnieuw uit zijn Spotify-playlist."""
    import radio
    rl = radio.get_lijst(lijst_id)
    if not rl:
        return jsonify({"error": "Radiolijst niet gevonden"}), 404
    if not rl["spotify_playlist_id"]:
        return jsonify({"error": "Deze lijst is als tekst geplakt; importeer hem opnieuw"}), 400
    try:
        _, entries = radio.lees_playlist(get_spotify_client(), rl["spotify_playlist_id"])
    except Exception as e:
        return jsonify({"error": f"Kon de playlist niet lezen bij Spotify: {e}"}), 502
    if not entries:
        return jsonify({"error": "Playlist is leeg"}), 400
    result = radio.sla_op(rl["naam"], rl["zender"], rl["decennium"], entries, lijst_id=lijst_id)
    return jsonify({**result, "tekst": f"{rl['naam']}: {len(entries)} nummers bijgewerkt."})


@app.route("/api/radiolijsten/<int:lijst_id>", methods=["DELETE"])
def api_radiolijst_verwijderen(lijst_id):
    import radio
    if not radio.verwijder(lijst_id):
        return jsonify({"error": "Radiolijst niet gevonden"}), 404
    return jsonify({"ok": True})


@app.route("/api/scheduler/jobs")
def api_scheduler_jobs():
    return jsonify(_wl_scheduler.get_jobs() if _wl_scheduler else [])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
