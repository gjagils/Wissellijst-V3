# -*- coding: utf-8 -*-
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from openai import OpenAI

from config import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE, CACHE_PATH,
    OPENAI_API_KEY, HISTORY_FILE, SUGGESTIONS_FILE, QUEUE_FILE,
    get_historie_uris, add_historie_bulk, save_wachtrij,
)
import os
import re

from logging_config import get_logger
from validators import validate_artist_limit, validate_history, validate_decade

logger = get_logger(__name__)


def get_spotify_client():
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
    )
    token_info = auth_manager.get_cached_token()
    if not token_info:
        raise Exception("auth_required")

    cached_scopes = set((token_info.get('scope') or '').split())
    required_scopes = set(SPOTIFY_SCOPE.split())
    if not required_scopes.issubset(cached_scopes):
        if os.path.exists(CACHE_PATH):
            os.remove(CACHE_PATH)
        raise Exception("auth_required")

    return spotipy.Spotify(auth_manager=auth_manager)


def search_spotify(sp, artist, title):
    """Zoek een track op Spotify en geef info terug."""
    def _extract(track):
        return {
            "uri": track["uri"],
            "release_date": track.get("album", {}).get("release_date", ""),
        }

    results = sp.search(q=f"track:{title} artist:{artist}", limit=1, type="track")
    tracks = results.get("tracks", {}).get("items", [])
    if tracks:
        return _extract(tracks[0])

    results = sp.search(q=f"{artist} {title}", limit=5, type="track")
    tracks = results.get("tracks", {}).get("items", [])
    artist_lower = artist.lower()
    for t in tracks:
        if any(artist_lower in a["name"].lower() for a in t.get("artists", [])):
            return _extract(t)
    return None


def _parse_history_line(line):
    """Parse een historie-regel. URI is altijd het laatste deel, split van rechts."""
    line = line.strip()
    if not line:
        return None
    parts = line.rsplit(" - ", 1)
    if len(parts) < 2 or not parts[1].startswith("spotify:"):
        return None
    uri = parts[1].strip()
    left_parts = parts[0].split(" - ", 2)
    if len(left_parts) < 3:
        return None
    return {
        "categorie": left_parts[0].strip(),
        "artiest": left_parts[1].strip(),
        "titel": left_parts[2].strip(),
        "uri": uri,
    }


def load_history(history_file=None, wl_id=None):
    """Laad artiesten en URI's uit de historie.

    Returns: (artists, uris, artist_counts)
    """
    history_file = history_file or HISTORY_FILE
    artists = []
    uris = []
    artist_counts = {}

    if wl_id:
        from config import get_historie
        entries = get_historie(wl_id)
        for entry in entries:
            artist = entry["artiest"]
            artists.append(artist)
            uris.append(entry["uri"])
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
    elif os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                parsed = _parse_history_line(line)
                if not parsed:
                    continue
                artist = parsed["artiest"]
                artists.append(artist)
                uris.append(parsed["uri"])
                artist_counts[artist] = artist_counts.get(artist, 0) + 1

    return artists, uris, artist_counts


def ask_gpt_for_suggestions(categorieen, exclude_artists, blocked_artists=None, per_categorie=5):
    """Vraag GPT om suggesties op basis van vrije categorieën."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    cat_beschrijving = ", ".join(f"{i+1}. {c}" for i, c in enumerate(categorieen))
    totaal = len(categorieen) * per_categorie
    blocked_list = blocked_artists or []

    prompt = (
        f"Geef {per_categorie} muziek suggesties per categorie, dus {totaal} regels totaal.\n"
        f"Categorieën: {cat_beschrijving}\n"
    )
    if blocked_list:
        prompt += (
            f"VERBODEN artiesten (max per artiest bereikt, ABSOLUUT NIET GEBRUIKEN): "
            f"{', '.join(blocked_list)}.\n"
        )
    if exclude_artists:
        prompt += (
            f"Liever niet (staan al in playlist): {', '.join(exclude_artists[:60])}.\n"
        )
    prompt += (
        "Wees creatief en kies GEEN voor de hand liggende artiesten. "
        "Denk aan minder bekende maar geldige nummers.\n"
        "Zorg dat alle artiesten VERSCHILLEND zijn.\n"
        "Syntax per regel: categorie | artiest | titel\n"
        "Geef ALLEEN de regels, geen extra tekst."
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Je bent een muziekexpert. Geef alleen de gevraagde syntax regels."},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content.strip().split("\n")


def _extract_decade(category):
    """Haal decennium uit een categorienaam."""
    match = re.match(r'(\d{2}s)', category)
    return match.group(1) if match else None


def _get_decade(release_date):
    """Bepaal het decennium op basis van release datum."""
    try:
        year = int(release_date.split("-")[0])
        return f"{str((year // 10) * 10)[2:]}s"
    except Exception:
        return None


def _match_categorie(raw_cat, categorieen, filled):
    """Match een GPT-categorie aan de originele categorieën."""
    raw_lower = raw_cat.lower().strip()
    raw_clean = re.sub(r'^\d+[\.\)]\s*', '', raw_lower)

    for cat in categorieen:
        if cat in filled:
            continue
        cat_lower = cat.lower().strip()
        if cat_lower == raw_clean or cat_lower == raw_lower:
            return cat
        if cat_lower in raw_clean or raw_clean in cat_lower:
            return cat
    return None


def generate_block(sp, playlist_id, categorieen, history_file=None, wl_id=None,
                   max_per_artiest=0):
    """Genereer één blok suggesties (1 per categorie), gevalideerd op Spotify."""
    history_file = history_file or HISTORY_FILE

    current_tracks = sp.playlist_items(playlist_id)["items"]
    active_artists = [t["track"]["artists"][0]["name"] for t in current_tracks if t.get("track")]
    history_artists, history_uris, artist_counts = load_history(
        history_file, wl_id=wl_id)

    for a in active_artists:
        artist_counts[a] = artist_counts.get(a, 0) + 1

    if max_per_artiest > 0:
        blocked_artists = [a for a, c in artist_counts.items() if c >= max_per_artiest]
    else:
        blocked_artists = []

    exclude = list(set(active_artists + history_artists[-50:]))
    raw_suggestions = ask_gpt_for_suggestions(
        categorieen, exclude, blocked_artists=blocked_artists, per_categorie=5)

    filled = {}
    used_uris = set(history_uris)

    for line in raw_suggestions:
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue

        raw_cat = parts[0].strip()
        artist = parts[1].strip()
        title = parts[2].strip()

        matched_cat = _match_categorie(raw_cat, categorieen, filled)
        if not matched_cat:
            continue

        if not validate_artist_limit(artist, artist_counts, max_per_artiest):
            logger.debug("Skip: artiest op max",
                         extra={"artiest": artist, "titel": title})
            continue

        result = search_spotify(sp, artist, title)
        if not result:
            logger.debug("Skip: niet gevonden op Spotify",
                         extra={"artiest": artist, "titel": title})
            continue
        uri = result["uri"]
        release_date = result["release_date"]

        if not validate_history(uri, used_uris):
            logger.debug("Skip: al in historie",
                         extra={"artiest": artist, "titel": title})
            continue

        expected_decade = _extract_decade(matched_cat)
        if expected_decade and not validate_decade(release_date, expected_decade):
            logger.debug("Skip: decade mismatch",
                         extra={"artiest": artist, "titel": title,
                                "verwacht": expected_decade})
            continue

        filled[matched_cat] = {
            "categorie": matched_cat,
            "artiest": artist,
            "titel": title,
            "uri": uri,
        }
        used_uris.add(uri)
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        logger.info("Track gekozen",
                     extra={"categorie": matched_cat, "artiest": artist,
                            "titel": title})

        if len(filled) == len(categorieen):
            break

    if len(filled) < len(categorieen):
        missing = [c for c in categorieen if c not in filled]
        logger.warning("Onvolledig blok",
                       extra={"gevuld": len(filled), "totaal": len(categorieen),
                              "missend": missing})
        return None

    return [filled[c] for c in categorieen]


def initial_fill(playlist_id, categorieen, history_file=None, queue_file=None,
                  wl_id=None, max_per_artiest=0, aantal_blokken=10, on_progress=None):
    """Vul een playlist met N blokken + 1 volgend blokje."""
    history_file = history_file or HISTORY_FILE
    if not queue_file:
        queue_file = os.path.join(os.path.dirname(history_file), "volgende_blokje.txt")

    sp = get_spotify_client()
    block_size = len(categorieen)
    bestaande_tracks = sp.playlist_tracks(playlist_id, fields="total")["total"]
    bestaande_blokken = bestaande_tracks // block_size if block_size else 0

    if bestaande_blokken >= aantal_blokken:
        logger.info("Playlist al vol, alleen wachtrij",
                     extra={"bestaand": bestaande_tracks})
        nog_te_vullen = 0
    else:
        nog_te_vullen = aantal_blokken - bestaande_blokken

    alle_tracks = []
    mislukt = 0
    max_retries = 3
    totaal = nog_te_vullen + 1

    for blok_nr in range(1, totaal + 1):
        is_wachtrij = blok_nr == totaal
        actual_blok = bestaande_blokken + blok_nr
        label = "volgend blokje" if is_wachtrij else f"blok {actual_blok}/{aantal_blokken}"

        if on_progress:
            on_progress(blok_nr, totaal, f"Genereren {label}...")

        block = None
        for poging in range(max_retries):
            block = generate_block(sp, playlist_id, categorieen, history_file,
                                   wl_id=wl_id, max_per_artiest=max_per_artiest)
            if block:
                break

        if not block:
            mislukt += 1
            continue

        if is_wachtrij:
            if wl_id:
                save_wachtrij(wl_id, block)
            else:
                with open(queue_file, "w", encoding="utf-8") as f:
                    for t in block:
                        f.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")
        else:
            uris = [t["uri"] for t in block]
            sp.playlist_add_items(playlist_id, uris)
            alle_tracks.extend(block)

            if wl_id:
                add_historie_bulk(wl_id, block)
            else:
                with open(history_file, "a", encoding="utf-8") as hf:
                    for t in block:
                        hf.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")

    return {
        "toegevoegd": len(alle_tracks),
        "blokken": (bestaande_blokken + len(alle_tracks) // block_size) if block_size else 0,
        "mislukt": mislukt,
        "wachtrij_klaar": mislukt < 2,
    }


if __name__ == "__main__":
    from config import HISTORY_FILE as _hf
    DEFAULT_CATEGORIES = ["80s", "90s", "00s", "10s", "20s"]

    sp = get_spotify_client()
    playlist_id = os.environ.get("SPOTIFY_PLAYLIST_ID", "")
    if not playlist_id:
        logger.error("SPOTIFY_PLAYLIST_ID niet ingesteld.")
        exit(1)

    block = generate_block(sp, playlist_id, DEFAULT_CATEGORIES)
    if block and len(block) == 5:
        uris = [t["uri"] for t in block]
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(uris))
        logger.info("Suggesties gegenereerd en klaargezet.")
    else:
        logger.warning("Niet genoeg geldige suggesties gevonden.")
