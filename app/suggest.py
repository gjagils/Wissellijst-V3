# -*- coding: utf-8 -*-
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from openai import OpenAI

from config import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE, CACHE_PATH,
    OPENAI_API_KEY, HISTORY_FILE, SUGGESTIONS_FILE, QUEUE_FILE,
)
import os
import re


def get_spotify_client():
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
    )
    # Check of er een geldige (of refreshbare) token is
    token_info = auth_manager.get_cached_token()
    if not token_info:
        raise Exception("auth_required")

    # Check of de cached token alle benodigde scopes bevat
    cached_scopes = set((token_info.get('scope') or '').split())
    required_scopes = set(SPOTIFY_SCOPE.split())
    if not required_scopes.issubset(cached_scopes):
        # Scopes gewijzigd - verwijder oude token
        import os
        if os.path.exists(CACHE_PATH):
            os.remove(CACHE_PATH)
        raise Exception("auth_required")

    return spotipy.Spotify(auth_manager=auth_manager)


def search_spotify(sp, artist, title):
    """Zoek een track op Spotify en geef info terug.

    Probeert eerst strikt (track: + artist:), dan breder als fallback.

    Returns: dict met {uri, release_date} of None als niet gevonden.
    """
    def _extract(track):
        return {
            "uri": track["uri"],
            "release_date": track.get("album", {}).get("release_date", ""),
        }

    # Strikte zoekopdracht
    results = sp.search(q=f"track:{title} artist:{artist}", limit=1, type="track")
    tracks = results.get("tracks", {}).get("items", [])
    if tracks:
        return _extract(tracks[0])

    # Fallback: bredere zoekopdracht
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
    # URI is altijd het laatste deel (spotify:track:...)
    parts = line.rsplit(" - ", 1)
    if len(parts) < 2 or not parts[1].startswith("spotify:"):
        return None
    uri = parts[1].strip()
    # Rest: categorie - artiest - titel (titel kan " - " bevatten)
    left_parts = parts[0].split(" - ", 2)
    if len(left_parts) < 3:
        return None
    return {
        "categorie": left_parts[0].strip(),
        "artiest": left_parts[1].strip(),
        "titel": left_parts[2].strip(),
        "uri": uri,
    }


def load_history(history_file=None):
    """Laad artiesten en URI's uit de historie.

    Returns: (artists, uris, artist_counts) waar artist_counts een dict is
             met per artiest het aantal keer dat die voorkomt.
    """
    history_file = history_file or HISTORY_FILE
    artists = []
    uris = []
    artist_counts = {}
    if os.path.exists(history_file):
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
    """Vraag GPT om suggesties op basis van vrije categorieën.

    Args:
        blocked_artists: artiesten die absoluut niet mogen (max bereikt)
        per_categorie: aantal alternatieven per categorie (standaard 5)
    """
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
    """Haal decennium (bijv. '80s') uit een categorienaam.

    Werkt voor '80s', '80s heeft in de top 40', etc.
    Returns: decade string of None als geen decennium in de categorie zit.
    """
    match = re.match(r'(\d{2}s)', category)
    return match.group(1) if match else None


def _get_decade(release_date):
    """Bepaal het decennium op basis van release datum, bijv. '90s'."""
    try:
        year = int(release_date.split("-")[0])
        return f"{str((year // 10) * 10)[2:]}s"
    except Exception:
        return None


def _match_categorie(raw_cat, categorieen, filled):
    """Match een GPT-categorie aan de originele categorieën.

    Probeert exact, dan case-insensitive, dan substring matching.
    Skipt categorieën die al gevuld zijn.
    """
    raw_lower = raw_cat.lower().strip()
    # Verwijder eventuele nummering (bijv. "1. 80s" -> "80s")
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


def generate_block(sp, playlist_id, categorieen, history_file=None, max_per_artiest=0):
    """Genereer één blok suggesties (1 per categorie), gevalideerd op Spotify.

    Vraagt GPT om meerdere alternatieven per categorie en kiest de eerste
    die geldig is (gevonden op Spotify, niet in historie, niet geblokkeerd).

    Args:
        max_per_artiest: max nummers per artiest (0 = onbeperkt)

    Returns: lijst van dicts met {categorie, artiest, titel, uri} of None bij fout.
    """
    history_file = history_file or HISTORY_FILE

    # Verzamel artiesten om te vermijden
    current_tracks = sp.playlist_items(playlist_id)["items"]
    active_artists = [t["track"]["artists"][0]["name"] for t in current_tracks if t.get("track")]
    history_artists, history_uris, artist_counts = load_history(history_file)

    # Tel ook artiesten in huidige playlist mee
    for a in active_artists:
        artist_counts[a] = artist_counts.get(a, 0) + 1

    # Artiesten die het max bereikt hebben uitsluiten
    if max_per_artiest > 0:
        blocked_artists = [a for a, c in artist_counts.items() if c >= max_per_artiest]
    else:
        blocked_artists = []

    # Stuur geblokkeerde artiesten apart (strenger) en rest als gewone exclude
    exclude = list(set(active_artists + history_artists[-50:]))

    raw_suggestions = ask_gpt_for_suggestions(
        categorieen, exclude,
        blocked_artists=blocked_artists,
        per_categorie=5,
    )

    filled = {}  # categorie -> result dict
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

        # Match aan originele categorie (skip al gevulde)
        matched_cat = _match_categorie(raw_cat, categorieen, filled)
        if not matched_cat:
            continue

        # Check max per artiest
        if max_per_artiest > 0 and artist_counts.get(artist, 0) >= max_per_artiest:
            print(f"  [block] Skip {artist} - {title}: artiest op max ({max_per_artiest})",
                  flush=True)
            continue

        result = search_spotify(sp, artist, title)
        if not result:
            print(f"  [block] Skip {artist} - {title}: niet gevonden op Spotify",
                  flush=True)
            continue
        uri = result["uri"]
        release_date = result["release_date"]
        if uri in used_uris:
            print(f"  [block] Skip {artist} - {title}: al in historie",
                  flush=True)
            continue

        # Decade check: klopt het releasejaar bij de gevraagde categorie?
        expected_decade = _extract_decade(matched_cat)
        if expected_decade and release_date:
            actual_decade = _get_decade(release_date)
            if actual_decade and actual_decade != expected_decade:
                print(f"  [block] Skip {artist} - {title}: "
                      f"release {release_date} ({actual_decade}) past niet bij {expected_decade}",
                      flush=True)
                continue

        filled[matched_cat] = {
            "categorie": matched_cat,
            "artiest": artist,
            "titel": title,
            "uri": uri,
        }
        used_uris.add(uri)
        # Update count voor dit blok
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        print(f"  [block] {matched_cat}: {artist} - {title} ✓", flush=True)

        # Stop vroeg als alles gevuld is
        if len(filled) == len(categorieen):
            break

    if len(filled) < len(categorieen):
        missing = [c for c in categorieen if c not in filled]
        print(f"  [block] Onvolledig blok: {len(filled)}/{len(categorieen)}, "
              f"missend: {missing}", flush=True)
        return None

    # Geef resultaten terug in volgorde van categorieën
    return [filled[c] for c in categorieen]


def initial_fill(playlist_id, categorieen, history_file=None, queue_file=None,
                  max_per_artiest=0, aantal_blokken=10, on_progress=None):
    """Vul een playlist met N blokken + 1 volgend blokje.

    Houdt rekening met bestaande tracks: als de playlist al tracks bevat,
    worden alleen de resterende blokken aangevuld.

    Args:
        playlist_id: Spotify playlist ID
        categorieen: lijst van categorieën
        history_file: pad naar historie bestand (optioneel)
        queue_file: pad naar wachtrij bestand (optioneel)
        max_per_artiest: max nummers per artiest (0 = onbeperkt)
        aantal_blokken: aantal blokken voor de playlist (standaard 10)
        on_progress: callback(blok_nr, totaal, status_tekst) voor voortgang

    Returns: dict met resultaten
    """
    history_file = history_file or HISTORY_FILE
    if not queue_file:
        queue_file = os.path.join(os.path.dirname(history_file), "volgende_blokje.txt")

    sp = get_spotify_client()

    # Check hoeveel tracks al in de playlist zitten
    block_size = len(categorieen)
    bestaande_tracks = sp.playlist_tracks(playlist_id, fields="total")["total"]
    bestaande_blokken = bestaande_tracks // block_size if block_size else 0

    if bestaande_blokken >= aantal_blokken:
        print(f"[fill] Playlist heeft al {bestaande_tracks} tracks "
              f"({bestaande_blokken} blokken), doel is {aantal_blokken}. "
              f"Alleen wachtrij genereren.", flush=True)
        nog_te_vullen = 0
    else:
        nog_te_vullen = aantal_blokken - bestaande_blokken
        if bestaande_blokken > 0:
            print(f"[fill] Playlist heeft al {bestaande_tracks} tracks "
                  f"({bestaande_blokken} blokken), nog {nog_te_vullen} blokken "
                  f"nodig.", flush=True)

    alle_tracks = []
    mislukt = 0
    max_retries = 3
    totaal = nog_te_vullen + 1  # +1 voor wachtrij

    for blok_nr in range(1, totaal + 1):
        is_wachtrij = blok_nr == totaal
        actual_blok = bestaande_blokken + blok_nr
        label = "volgend blokje" if is_wachtrij else f"blok {actual_blok}/{aantal_blokken}"

        if on_progress:
            on_progress(blok_nr, totaal, f"Genereren {label}...")

        block = None
        for poging in range(max_retries):
            block = generate_block(sp, playlist_id, categorieen, history_file,
                                   max_per_artiest=max_per_artiest)
            if block:
                break

        if not block:
            mislukt += 1
            continue

        if is_wachtrij:
            # Laatste blok gaat naar de wachtrij (volledig formaat)
            with open(queue_file, "w", encoding="utf-8") as f:
                for t in block:
                    f.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")
        else:
            # Voeg toe aan playlist
            uris = [t["uri"] for t in block]
            sp.playlist_add_items(playlist_id, uris)
            alle_tracks.extend(block)

            # Schrijf naar historie
            with open(history_file, "a", encoding="utf-8") as hf:
                for t in block:
                    hf.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")

    return {
        "toegevoegd": len(alle_tracks),
        "blokken": (bestaande_blokken + len(alle_tracks) // block_size) if block_size else 0,
        "mislukt": mislukt,
        "wachtrij_klaar": mislukt < 2,
    }


# Standalone mode (backwards compatible)
if __name__ == "__main__":
    from config import HISTORY_FILE as _hf
    DEFAULT_CATEGORIES = ["80s", "90s", "00s", "10s", "20s"]

    sp = get_spotify_client()
    # Lees playlist ID uit env (oude manier)
    playlist_id = os.environ.get("SPOTIFY_PLAYLIST_ID", "")
    if not playlist_id:
        print("SPOTIFY_PLAYLIST_ID niet ingesteld.")
        exit(1)

    block = generate_block(sp, playlist_id, DEFAULT_CATEGORIES)
    if block and len(block) == 5:
        uris = [t["uri"] for t in block]
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(uris))

        with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
            for t in block:
                f.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")
            f.write("\n--- KOPIEER BLOK HIERONDER ---\n")
            f.write("\n".join(uris))

        print("Suggesties gegenereerd en klaargezet.")
    else:
        print("Niet genoeg geldige suggesties gevonden.")
