# -*- coding: utf-8 -*-
import os
import uuid
import threading
import datetime
import time
from flask import Flask, render_template, request, jsonify, redirect
from spotipy.oauth2 import SpotifyOAuth

from config import (
    load_wissellijsten, save_wissellijsten, get_wissellijst,
    get_history_file, get_queue_file, get_smaakprofiel_file,
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

app = Flask(__name__)

# Voortgang bijhouden per taak
_tasks = {}


@app.route("/health")
def health():
    """Health check endpoint voor deployment verificatie."""
    return jsonify({"status": "ok", "app": "wissellijst", "version": "3.0"})


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
    profiel_file = get_smaakprofiel_file(lijst_id)
    if os.path.exists(profiel_file):
        with open(profiel_file, 'r', encoding='utf-8') as f:
            profiel = f.read()
        return jsonify({"profiel": profiel})
    return jsonify({"profiel": ""})


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel/ophalen", methods=["POST"])
def api_smaakprofiel_ophalen(lijst_id):
    """Haal smaakprofiel op van Spotify en sla op (behoudt eigen toevoegingen)."""
    try:
        sp = get_spotify_client()
        spotify_profiel = build_taste_profile(sp)

        # Lees bestaand profiel om eigen toevoegingen te behouden
        profiel_file = get_smaakprofiel_file(lijst_id)
        eigen_sectie = ""
        if os.path.exists(profiel_file):
            with open(profiel_file, 'r', encoding='utf-8') as f:
                bestaand = f.read()
            # Eigen toevoegingen staan na de marker
            marker = "=== EIGEN TOEVOEGINGEN ==="
            if marker in bestaand:
                eigen_sectie = bestaand[bestaand.index(marker):]

        # Combineer Spotify + eigen
        volledig = spotify_profiel
        if eigen_sectie:
            volledig += "\n\n" + eigen_sectie

        with open(profiel_file, 'w', encoding='utf-8') as f:
            f.write(volledig)

        # Update ook in wissellijst config
        data = load_wissellijsten()
        for i, w in enumerate(data["wissellijsten"]):
            if w["id"] == lijst_id:
                data["wissellijsten"][i]["smaakprofiel"] = volledig
                break
        save_wissellijsten(data)

        return jsonify({"profiel": volledig})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wissellijsten/<lijst_id>/smaakprofiel", methods=["POST"])
def api_smaakprofiel_opslaan(lijst_id):
    """Sla het (bewerkte) smaakprofiel op voor een wissellijst."""
    try:
        body = request.get_json()
        profiel = body.get("profiel", "")

        profiel_file = get_smaakprofiel_file(lijst_id)
        with open(profiel_file, 'w', encoding='utf-8') as f:
            f.write(profiel)

        # Update ook in wissellijst config
        data = load_wissellijsten()
        for i, w in enumerate(data["wissellijsten"]):
            if w["id"] == lijst_id:
                data["wissellijsten"][i]["smaakprofiel"] = profiel
                break
        save_wissellijsten(data)

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
    data = load_wissellijsten()

    lijst_id = body.get("id")
    if lijst_id:
        # Update bestaande
        for i, wl in enumerate(data["wissellijsten"]):
            if wl["id"] == lijst_id:
                data["wissellijsten"][i] = body
                break
    else:
        # Nieuwe aanmaken
        body["id"] = str(uuid.uuid4())[:8]
        data["wissellijsten"].append(body)

    # Sla smaakprofiel ook op in apart bestand als het er is
    if body.get("smaakprofiel"):
        profiel_file = get_smaakprofiel_file(body["id"])
        with open(profiel_file, 'w', encoding='utf-8') as f:
            f.write(body["smaakprofiel"])

    save_wissellijsten(data)
    return jsonify(body)


@app.route("/api/wissellijsten/<lijst_id>", methods=["DELETE"])
def api_wissellijst_verwijderen(lijst_id):
    """Verwijder een wissellijst-configuratie."""
    data = load_wissellijsten()
    data["wissellijsten"] = [wl for wl in data["wissellijsten"] if wl["id"] != lijst_id]
    save_wissellijsten(data)
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
            # Spotify max 100 per keer
            for i in range(0, len(uris), 100):
                sp.playlist_remove_all_occurrences_of_items(
                    playlist_id, uris[i:i + 100])
    except Exception as e:
        return jsonify({"error": f"Kon playlist niet leeghalen: {e}"}), 500

    # Historie leegmaken
    history_file = get_history_file(lijst_id)
    if os.path.exists(history_file):
        with open(history_file, "w") as f:
            f.write("")

    # Wachtrij leegmaken
    queue_file = get_queue_file(lijst_id)
    if os.path.exists(queue_file):
        with open(queue_file, "w") as f:
            f.write("")

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

    history_file = get_history_file(lijst_id)

    entries = []
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                parsed = _parse_history_line(line)
                if parsed:
                    entries.append(parsed)

    return jsonify(entries)


@app.route("/api/wissellijsten/<lijst_id>/historie/<int:entry_index>", methods=["DELETE"])
def api_historie_verwijderen(lijst_id, entry_index):
    """Verwijder een historie-entry op basis van index."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    history_file = get_history_file(lijst_id)
    if not os.path.exists(history_file):
        return jsonify({"error": "Geen historie gevonden"}), 404

    # Lees alle regels
    with open(history_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Parse en filter: vind de N-de geldige entry
    valid_index = 0
    new_lines = []
    removed = False
    for line in lines:
        parsed = _parse_history_line(line)
        if parsed:
            if valid_index == entry_index:
                removed = True  # Skip deze regel
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

    history_file = get_history_file(lijst_id)
    if os.path.exists(history_file):
        os.remove(history_file)

    return jsonify({"ok": True, "tekst": f"Historie van '{wl['naam']}' gewist."})


# --- Wachtrij ---

def _read_queue(lijst_id):
    """Lees wachtrij-bestand en return lijst van dicts."""
    queue_file = get_queue_file(lijst_id)
    entries = []
    if os.path.exists(queue_file):
        with open(queue_file, "r", encoding="utf-8") as f:
            for line in f:
                parsed = _parse_history_line(line)
                if parsed:
                    entries.append(parsed)
    return entries


def _write_queue(lijst_id, entries):
    """Schrijf wachtrij-entries naar bestand."""
    queue_file = get_queue_file(lijst_id)
    with open(queue_file, "w", encoding="utf-8") as f:
        for t in entries:
            f.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")


@app.route("/api/wissellijsten/<lijst_id>/wachtrij")
def api_wachtrij(lijst_id):
    """Haal de wachtrij op van een wissellijst."""
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404
    return jsonify(_read_queue(lijst_id))


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

    entries = _read_queue(lijst_id)
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

    _write_queue(lijst_id, entries)
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
                )
            else:
                max_retries = 3
                for attempt in range(max_retries):
                    _tasks[task_id]["voortgang"] = 20 + (attempt * 25)
                    block = generate_block(
                        sp, wl["playlist_id"], wl.get("categorieen", []),
                        history_file=history_file,
                        max_per_artiest=wl.get("max_per_artiest", 0),
                    )
                    if block:
                        break

            if block:
                _write_queue(lijst_id, block)
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
    """Start een rotatie voor een wissellijst (async).

    Discovery: eerst analyseren (nieuw blok), dan roteren.
    Categorie: roteren, dan nieuw blok genereren.
    """
    wl = get_wissellijst(lijst_id)
    if not wl:
        return jsonify({"error": "Wissellijst niet gevonden"}), 404

    is_discovery = wl.get("type") == "discovery"
    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "bezig", "voortgang": 0,
                       "tekst": "Starten...", "resultaat": None}

    def run():
        try:
            if is_discovery:
                # Discovery: eerst analyseren, dan roteren
                _tasks[task_id]["voortgang"] = 10
                _tasks[task_id]["tekst"] = "Bronlijsten scannen & analyseren..."

                result = rotate_and_regenerate(wl)

                _tasks[task_id]["voortgang"] = 90
                _tasks[task_id]["tekst"] = "Rotatie voltooien..."
            else:
                # Categorie: roteer + genereer nieuw
                _tasks[task_id]["voortgang"] = 30
                _tasks[task_id]["tekst"] = "Oudste blok verwijderen en wachtrij toevoegen..."

                result = rotate_and_regenerate(wl)

            _tasks[task_id]["voortgang"] = 100
            _tasks[task_id]["status"] = "klaar"
            _tasks[task_id]["tekst"] = result["tekst"]
            _tasks[task_id]["resultaat"] = result

            # Update laatste rotatie in config
            data = load_wissellijsten()
            for i, w in enumerate(data["wissellijsten"]):
                if w["id"] == lijst_id:
                    data["wissellijsten"][i]["laatste_rotatie"] = datetime.datetime.now().isoformat()
                    break
            save_wissellijsten(data)

            # Stuur e-mail notificatie als ingeschakeld
            if wl.get("mail_na_rotatie") and wl.get("mail_adres") and result.get("status") == "ok":
                print(f"[Mail] Rotatie-mail versturen naar {wl['mail_adres']} voor '{wl['naam']}'...", flush=True)
                send_rotation_mail(
                    wl["mail_adres"], wl["naam"],
                    result.get("verwijderd_detail", []),
                    result.get("toegevoegd_detail", []),
                )
            else:
                reason = []
                if not wl.get("mail_na_rotatie"):
                    reason.append("mail_na_rotatie uit")
                if not wl.get("mail_adres"):
                    reason.append("geen mail_adres")
                if result.get("status") != "ok":
                    reason.append(f"status={result.get('status')}")
                print(f"[Mail] Geen mail verstuurd voor '{wl.get('naam')}': {', '.join(reason)}", flush=True)

        except Exception as e:
            _tasks[task_id]["status"] = "fout"
            _tasks[task_id]["tekst"] = str(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


# --- Rotatie Scheduler ---

def _check_schedules():
    """Background thread die elke 60 seconden controleert of er geroteerd moet worden."""
    while True:
        time.sleep(60)
        try:
            now = datetime.datetime.now()
            data = load_wissellijsten()

            for wl in data["wissellijsten"]:
                schema = wl.get("rotatie_schema", "uit")
                if schema == "uit":
                    continue

                laatste = wl.get("laatste_rotatie", "")

                if schema == "elk_uur":
                    # Elk uur roteren, op minuut 0
                    if now.minute != 0:
                        continue

                    # Niet dubbel roteren in hetzelfde uur
                    if laatste:
                        try:
                            laatste_dt = datetime.datetime.fromisoformat(laatste)
                            if (laatste_dt.date() == now.date()
                                    and laatste_dt.hour == now.hour):
                                continue
                        except ValueError:
                            pass

                elif schema == "elke_3_uur":
                    # Elke 3 uur roteren, op minuut 0
                    if now.minute != 0:
                        continue

                    # Check of er minstens 3 uur verstreken zijn
                    if laatste:
                        try:
                            laatste_dt = datetime.datetime.fromisoformat(laatste)
                            if (now - laatste_dt).total_seconds() < 3 * 3600:
                                continue
                        except ValueError:
                            pass
                else:
                    tijdstip = wl.get("rotatie_tijdstip", "08:00")
                    try:
                        uur, minuut = map(int, tijdstip.split(":"))
                    except ValueError:
                        continue

                    # Alleen uitvoeren op het juiste tijdstip (binnen de minuut)
                    if now.hour != uur or now.minute != minuut:
                        continue

                    # Niet dubbel roteren op dezelfde dag
                    if laatste:
                        try:
                            laatste_dt = datetime.datetime.fromisoformat(laatste)
                            if laatste_dt.date() == now.date():
                                continue
                        except ValueError:
                            pass

                    # Bij wekelijks: check de dag
                    if schema == "wekelijks":
                        dag = wl.get("rotatie_dag", 0)
                        if now.weekday() != dag:
                            continue

                # Roteer!
                print(f"[Scheduler] Rotatie starten voor: {wl['naam']}")
                try:
                    result = rotate_and_regenerate(wl)

                    # Update laatste rotatie
                    wl["laatste_rotatie"] = now.isoformat()
                    for i, w in enumerate(data["wissellijsten"]):
                        if w["id"] == wl["id"]:
                            data["wissellijsten"][i] = wl
                            break
                    save_wissellijsten(data)

                    print(f"[Scheduler] Rotatie klaar: {wl['naam']} - {result['tekst']}")

                    # Stuur e-mail notificatie als ingeschakeld
                    if wl.get("mail_na_rotatie") and wl.get("mail_adres") and result.get("status") == "ok":
                        print(f"[Scheduler] Rotatie-mail versturen naar {wl['mail_adres']} voor '{wl['naam']}'...", flush=True)
                        send_rotation_mail(
                            wl["mail_adres"], wl["naam"],
                            result.get("verwijderd_detail", []),
                            result.get("toegevoegd_detail", []),
                        )
                    else:
                        reason = []
                        if not wl.get("mail_na_rotatie"):
                            reason.append("mail_na_rotatie uit")
                        if not wl.get("mail_adres"):
                            reason.append("geen mail_adres")
                        if result.get("status") != "ok":
                            reason.append(f"status={result.get('status')}")
                        print(f"[Scheduler] Geen mail verstuurd voor '{wl['naam']}': {', '.join(reason)}", flush=True)

                except Exception as e:
                    print(f"[Scheduler] Rotatie fout voor {wl['naam']}: {e}")

        except Exception as e:
            print(f"[Scheduler] Fout: {e}")


# Start scheduler als daemon thread
_scheduler = threading.Thread(target=_check_schedules, daemon=True)
_scheduler.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
