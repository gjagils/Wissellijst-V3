# -*- coding: utf-8 -*-
"""Categorie-wissellijsten: GPT stelt voor, Spotify en de regels valideren."""
import re

from openai import OpenAI

from config import (
    OPENAI_API_KEY, OPENAI_MODEL, SUGGESTIES_PER_CATEGORIE, MAX_REASKS,
    BLOK_ACCEPT_RATIO,
    get_historie, get_historie_uris, add_historie_bulk, save_wachtrij,
)
from spotify_auth import get_spotify_client  # noqa: F401  (re-export voor callers)
from logging_config import get_logger
from validators import validate_artist_limit, validate_history, validate_decade

logger = get_logger(__name__)


def _openai():
    return OpenAI(api_key=OPENAI_API_KEY)


def search_spotify(sp, artist, title):
    """Zoek een track op Spotify; returns {"uri", "release_date"} of None."""
    def _extract(track):
        return {
            "uri": track["uri"],
            "release_date": track.get("album", {}).get("release_date", ""),
        }

    try:
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
    except Exception as e:
        logger.warning("Spotify search fout",
                       extra={"artiest": artist, "titel": title, "error": str(e)})
        return None


def _parse_history_line(line):
    """Parse "categorie - artiest - titel - spotify:track:...". URI is altijd het laatste deel."""
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


def load_history(wl_id):
    """Returns (artists, uris, artist_counts) uit de historie van een wissellijst."""
    artists, uris, artist_counts = [], [], {}
    for entry in get_historie(wl_id):
        artist = entry["artiest"]
        artists.append(artist)
        uris.append(entry["uri"])
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
    return artists, uris, artist_counts


def _prompt_tail():
    return (
        "Wees creatief en kies GEEN voor de hand liggende artiesten. "
        "Denk aan minder bekende maar geldige nummers.\n"
        "Zorg dat alle artiesten VERSCHILLEND zijn.\n"
        "Syntax per regel: categorie | artiest | titel\n"
        "Geef ALLEEN de regels, geen extra tekst."
    )


def _ask(prompt):
    try:
        response = _openai().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Je bent een muziekexpert. Geef alleen de gevraagde syntax regels."},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip().split("\n")
    except Exception as e:
        logger.error("OpenAI suggesties mislukt", extra={"error": str(e)})
        return []


def ask_gpt_for_suggestions(categorieen, exclude_artists, blocked_artists=None,
                            per_categorie=SUGGESTIES_PER_CATEGORIE):
    """Eerste ronde: suggesties per categorie."""
    cat_beschrijving = ", ".join(f"{i+1}. {c}" for i, c in enumerate(categorieen))
    totaal = len(categorieen) * per_categorie
    prompt = (
        f"Geef {per_categorie} muziek suggesties per categorie, dus {totaal} regels totaal.\n"
        f"Categorieën: {cat_beschrijving}\n"
    )
    if blocked_artists:
        prompt += (f"VERBODEN artiesten (max per artiest bereikt, ABSOLUUT NIET GEBRUIKEN): "
                   f"{', '.join(blocked_artists)}.\n")
    if exclude_artists:
        prompt += f"Liever niet (staan al in playlist): {', '.join(exclude_artists[:60])}.\n"
    return _ask(prompt + _prompt_tail())


def _ask_gpt_replacements(missing_cats, skipped_info, exclude_artists,
                          blocked_artists=None, per_categorie=SUGGESTIES_PER_CATEGORIE):
    """Re-ask voor missende categorieën, met context over wat eerder faalde."""
    cat_beschrijving = ", ".join(f"{i+1}. {c}" for i, c in enumerate(missing_cats))
    totaal = len(missing_cats) * per_categorie
    skip_context = ""
    for cat in missing_cats:
        reasons = skipped_info.get(cat, [])
        if reasons:
            skip_context += f"\n- {cat}: " + "; ".join(reasons[:5])

    prompt = (
        f"Geef {per_categorie} muziek suggesties per categorie, dus {totaal} regels totaal.\n"
        f"Categorieën: {cat_beschrijving}\n"
    )
    if skip_context:
        prompt += (f"\nEerder geprobeerde suggesties die NIET werkten:{skip_context}\n"
                   "Kies COMPLEET ANDERE artiesten dan hierboven.\n")
    if blocked_artists:
        prompt += f"VERBODEN artiesten (ABSOLUUT NIET GEBRUIKEN): {', '.join(blocked_artists)}.\n"
    if exclude_artists:
        prompt += f"Liever niet (staan al in playlist): {', '.join(exclude_artists[:60])}.\n"
    return _ask(prompt + _prompt_tail())


def _extract_decade(category):
    match = re.match(r'(\d{2}s)', category)
    return match.group(1) if match else None


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


def _process_suggestions(raw_suggestions, categorieen, filled, skipped,
                         used_uris, artist_counts, max_per_artiest, sp):
    """Valideer GPT-suggesties en vul het blok. Muteert filled/skipped/used_uris/artist_counts."""
    for line in raw_suggestions:
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        raw_cat, artist, title = parts[0].strip(), parts[1].strip(), parts[2].strip()

        matched_cat = _match_categorie(raw_cat, categorieen, filled)
        if not matched_cat:
            continue

        if not validate_artist_limit(artist, artist_counts, max_per_artiest):
            skipped.setdefault(matched_cat, []).append(f'"{artist} - {title}" (artiest op max)')
            continue

        result = search_spotify(sp, artist, title)
        if not result:
            skipped.setdefault(matched_cat, []).append(f'"{artist} - {title}" (niet op Spotify)')
            continue
        uri, release_date = result["uri"], result["release_date"]

        if not validate_history(uri, used_uris):
            skipped.setdefault(matched_cat, []).append(f'"{artist} - {title}" (al in historie)')
            continue

        expected_decade = _extract_decade(matched_cat)
        if expected_decade and not validate_decade(release_date, expected_decade):
            skipped.setdefault(matched_cat, []).append(
                f'"{artist} - {title}" (decade mismatch, verwacht {expected_decade})')
            continue

        filled[matched_cat] = {"categorie": matched_cat, "artiest": artist,
                               "titel": title, "uri": uri}
        used_uris.add(uri)
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        logger.info("Track gekozen", extra={"categorie": matched_cat,
                                            "artiest": artist, "titel": title})
        if len(filled) == len(categorieen):
            break


def generate_block(sp, playlist_id, categorieen, wl_id, max_per_artiest=0):
    """Genereer één blok (1 track per categorie), gevalideerd op Spotify.

    1. GPT: N suggesties per categorie.
    2. Valideer: artiest-limiet, Spotify, historie, decennium.
    3. Re-ask voor missende categorieën (max MAX_REASKS).
    4. Accepteer een gedeeltelijk blok vanaf BLOK_ACCEPT_RATIO.
    Returns: lijst van tracks of None.
    """
    try:
        current_tracks = sp.playlist_items(playlist_id)["items"]
    except Exception as e:
        logger.error("Kan playlist items niet ophalen",
                     extra={"playlist_id": playlist_id, "error": str(e)})
        return None

    active_artists = [t["track"]["artists"][0]["name"] for t in current_tracks if t.get("track")]
    history_artists, history_uris, artist_counts = load_history(wl_id)
    for a in active_artists:
        artist_counts[a] = artist_counts.get(a, 0) + 1

    def blocked():
        if max_per_artiest > 0:
            return [a for a, c in artist_counts.items() if c >= max_per_artiest]
        return []

    exclude = list(set(active_artists + history_artists[-50:]))
    filled, skipped, used_uris = {}, {}, set(history_uris)

    raw = ask_gpt_for_suggestions(categorieen, exclude, blocked_artists=blocked())
    _process_suggestions(raw, categorieen, filled, skipped, used_uris,
                         artist_counts, max_per_artiest, sp)

    reasks = 0
    for _ in range(MAX_REASKS):
        missing = [c for c in categorieen if c not in filled]
        if not missing:
            break
        reasks += 1
        logger.info("Re-ask voor missende categorieën",
                    extra={"poging": reasks, "missend": missing})
        extra = _ask_gpt_replacements(missing, skipped, exclude, blocked_artists=blocked())
        if not extra:
            continue
        _process_suggestions(extra, categorieen, filled, skipped, used_uris,
                             artist_counts, max_per_artiest, sp)

    total, filled_count = len(categorieen), len(filled)
    logger.info("Blok resultaat",
                extra={"gevuld": filled_count, "totaal": total, "reasks": reasks,
                       "skipped_per_categorie": {c: len(r) for c, r in skipped.items()}})

    threshold = max(1, int(total * BLOK_ACCEPT_RATIO))
    if filled_count >= threshold:
        if filled_count < total:
            logger.warning("Gedeeltelijk blok geaccepteerd",
                           extra={"gevuld": filled_count, "totaal": total,
                                  "missend": [c for c in categorieen if c not in filled]})
        return [filled[c] for c in categorieen if c in filled]

    logger.warning("Blok afgewezen na re-asks",
                   extra={"gevuld": filled_count, "totaal": total, "threshold": threshold})
    return None


def initial_fill(playlist_id, categorieen, wl_id, max_per_artiest=0,
                 aantal_blokken=10, on_progress=None):
    """Vul een playlist met N blokken plus één wachtrij-blok."""
    sp = get_spotify_client()
    block_size = len(categorieen)
    bestaande_tracks = sp.playlist_tracks(playlist_id, fields="total")["total"]
    bestaande_blokken = bestaande_tracks // block_size if block_size else 0
    nog_te_vullen = max(0, aantal_blokken - bestaande_blokken)
    if nog_te_vullen == 0:
        logger.info("Playlist al vol, alleen wachtrij", extra={"bestaand": bestaande_tracks})

    alle_tracks, mislukt = [], 0
    totaal = nog_te_vullen + 1

    for blok_nr in range(1, totaal + 1):
        is_wachtrij = blok_nr == totaal
        label = "volgend blokje" if is_wachtrij else f"blok {bestaande_blokken + blok_nr}/{aantal_blokken}"
        if on_progress:
            on_progress(blok_nr, totaal, f"Genereren {label}...")

        block = None
        for _ in range(3):
            block = generate_block(sp, playlist_id, categorieen, wl_id, max_per_artiest)
            if block:
                break
        if not block:
            mislukt += 1
            continue

        if is_wachtrij:
            save_wachtrij(wl_id, block)
        else:
            uris = [t["uri"] for t in block]
            try:
                sp.playlist_add_items(playlist_id, uris)
            except Exception as e:
                logger.error("Kon tracks niet toevoegen aan playlist",
                             extra={"playlist_id": playlist_id, "error": str(e)})
                mislukt += 1
                continue
            alle_tracks.extend(block)
            add_historie_bulk(wl_id, block)

    return {
        "toegevoegd": len(alle_tracks),
        "blokken": (bestaande_blokken + len(alle_tracks) // block_size) if block_size else 0,
        "mislukt": mislukt,
        "wachtrij_klaar": mislukt < 2,
    }
