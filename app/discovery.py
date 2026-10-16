# -*- coding: utf-8 -*-
"""Discovery-wissellijsten: bronlijsten scannen, scoren op smaakprofiel, selecteren."""
import json
import time
from datetime import datetime, timedelta

from openai import OpenAI

from config import (
    OPENAI_API_KEY, OPENAI_SCORE_MODEL, DISCOVERY_MAX_MAANDEN,
    get_historie_uris, get_wachtrij_uris, get_smaakprofiel,
    save_wachtrij, add_historie_bulk,
)
from logging_config import get_logger

logger = get_logger(__name__)


def _openai():
    return OpenAI(api_key=OPENAI_API_KEY)


def build_taste_profile(sp):
    """Smaakprofiel als tekst, uit Spotify's top-artiesten en top-tracks."""
    def safe(fn, **kw):
        try:
            return fn(**kw)["items"]
        except Exception:
            return []

    top_artists_medium = safe(sp.current_user_top_artists, limit=50, time_range="medium_term")
    top_artists_short = safe(sp.current_user_top_artists, limit=20, time_range="short_term")
    top_tracks = safe(sp.current_user_top_tracks, limit=50, time_range="medium_term")

    all_genres = {}
    for a in top_artists_medium:
        for g in a.get("genres", []):
            all_genres[g] = all_genres.get(g, 0) + 1
    top_genres = [g for g, _ in sorted(all_genres.items(), key=lambda x: -x[1])[:20]]

    parts = ["=== SMAAKPROFIEL ==="]
    if top_genres:
        parts.append(f"Favoriete genres: {', '.join(top_genres)}")
    if top_artists_medium:
        parts.append("Top artiesten (afgelopen 6 maanden): "
                     + ", ".join(a["name"] for a in top_artists_medium[:25]))
    if top_artists_short:
        parts.append("Recent veel geluisterd: "
                     + ", ".join(a["name"] for a in top_artists_short[:10]))
    if top_tracks:
        parts.append("Top nummers:")
        for t in top_tracks[:30]:
            parts.append(f"  - {', '.join(a['name'] for a in t['artists'])} - {t['name']}")
    return "\n".join(parts)


def scan_source_playlists(sp, playlist_ids):
    """Alle unieke tracks uit de bronlijsten, met overlap-telling."""
    tracks_map = {}
    for idx, pid in enumerate(playlist_ids, 1):
        try:
            results = sp.playlist_items(
                pid,
                fields="items(track(uri,name,artists(name),album(name,release_date))),next",
                limit=100,
            )
            items = list(results["items"])
            while results.get("next"):
                results = sp.next(results)
                items.extend(results["items"])
            playlist_name = sp.playlist(pid, fields="name")["name"]
            logger.info("Bronlijst gescand",
                        extra={"playlist": playlist_name, "tracks": len(items),
                               "index": idx, "totaal": len(playlist_ids)})

            for item in items:
                track = item.get("track")
                if not track or not track.get("uri"):
                    continue
                uri = track["uri"]
                album = track.get("album") or {}
                if uri in tracks_map:
                    tracks_map[uri]["overlap"] += 1
                    tracks_map[uri]["bronnen"].append(playlist_name)
                else:
                    tracks_map[uri] = {
                        "artiest": track["artists"][0]["name"] if track.get("artists") else "Onbekend",
                        "titel": track["name"],
                        "album": album.get("name", ""),
                        "release_date": album.get("release_date", ""),
                        "uri": uri,
                        "overlap": 1,
                        "bronnen": [playlist_name],
                    }
        except Exception as e:
            logger.error("Fout bij scannen bronlijst", extra={"playlist_id": pid, "error": str(e)})

    logger.info("Bronlijsten scan klaar",
                extra={"uniek": len(tracks_map), "bronlijsten": len(playlist_ids)})
    return tracks_map


def _is_recent_release(release_date, max_months=DISCOVERY_MAX_MAANDEN):
    if not release_date:
        return False
    try:
        parts = release_date.split("-")
        if len(parts) >= 2:
            release = datetime(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) >= 3 else 1)
        else:
            release = datetime(int(parts[0]), 1, 1)
        return release >= datetime.now() - timedelta(days=max_months * 30)
    except (ValueError, IndexError):
        return False


def _load_playlist_uris(sp, playlist_id):
    uris = set()
    try:
        results = sp.playlist_items(playlist_id, fields="items(track(uri)),next", limit=100)
        while True:
            for item in results["items"]:
                if item.get("track") and item["track"].get("uri"):
                    uris.add(item["track"]["uri"])
            if not results.get("next"):
                break
            results = sp.next(results)
    except Exception:
        pass
    return uris


def score_candidates(candidates, taste_profile):
    """Score tracks 1-10 met GPT op basis van het smaakprofiel. Returns {index: score}."""
    if not candidates:
        return {}
    track_lines = []
    for i, t in enumerate(candidates):
        overlap_text = f" [{t['overlap']}x in bronlijsten]" if t.get("overlap", 1) > 1 else ""
        track_lines.append(f"{i}. {t['artiest']} - {t['titel']} ({t.get('album', '')}){overlap_text}")

    prompt = f"""{taste_profile}

=== OPDRACHT ===
Beoordeel onderstaande tracks op basis van het smaakprofiel hierboven.
Geef elke track een score van 1-10 (10 = perfecte match met de smaak).

Let op:
- Focus op genre, stijl, en vergelijkbare artiesten
- Nummers van artiesten die in het profiel staan krijgen een hogere score
- Wees kritisch maar eerlijk

Tracks om te beoordelen:
{chr(10).join(track_lines)}

Antwoord ALLEEN met een JSON array, geen andere tekst:
[{{"i": 0, "s": 8}}, {{"i": 1, "s": 5}}, ...]"""

    try:
        t0 = time.time()
        response = _openai().chat.completions.create(
            model=OPENAI_SCORE_MODEL,
            messages=[
                {"role": "system",
                 "content": "Je bent een muziekexpert die tracks beoordeelt op basis van "
                            "iemands smaakprofiel. Antwoord alleen met JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        scores = {}
        for item in json.loads(content):
            idx = item.get("i", item.get("index", -1))
            if 0 <= idx < len(candidates):
                scores[idx] = item.get("s", item.get("score", 5))
        logger.info("GPT scoring klaar",
                    extra={"duur_sec": round(time.time() - t0, 1), "scores": len(scores)})
        return scores
    except Exception as e:
        logger.error("GPT scoring fout", extra={"error": str(e)})
        return {i: 5 for i in range(len(candidates))}


def rank_and_select(candidates, scores, count=10, max_per_artiest=0):
    """Combineer smaakscore (70%) en overlap (30%), selecteer top N met artiest-limiet."""
    ranked = []
    for i, track in enumerate(candidates):
        smaak = scores.get(i, 5)
        overlap = min(track.get("overlap", 1), 5) * 2
        ranked.append({**track, "smaak_score": smaak, "combined_score": smaak * 0.7 + overlap * 0.3})
    ranked.sort(key=lambda x: -x["combined_score"])

    selected, artiest_count = [], {}
    for track in ranked:
        artiest = track["artiest"]
        if max_per_artiest > 0 and artiest_count.get(artiest, 0) >= max_per_artiest:
            continue
        selected.append(track)
        artiest_count[artiest] = artiest_count.get(artiest, 0) + 1
        if len(selected) >= count:
            break
    return selected


def _taste_profile(wl, wl_id):
    return get_smaakprofiel(wl_id) or wl.get("smaakprofiel", "")


def _score_all(candidates, taste_profile, on_progress=None, totaal=1):
    all_scores = {}
    batch_size = 100
    n_batches = (len(candidates) + batch_size - 1) // batch_size
    for batch_start in range(0, len(candidates), batch_size):
        if on_progress:
            on_progress(0, totaal, f"Scoring batch {batch_start // batch_size + 1}/{n_batches}...")
        for local_idx, score in score_candidates(candidates[batch_start:batch_start + batch_size],
                                                 taste_profile).items():
            all_scores[batch_start + local_idx] = score
    return all_scores


def _to_block(tracks):
    def jaar(t):
        d = t.get("release_date") or ""
        return int(d[:4]) if d[:4].isdigit() else None
    return [{"categorie": "discovery", "artiest": t["artiest"],
             "titel": t["titel"], "uri": t["uri"], "jaar": jaar(t)} for t in tracks]


def generate_discovery_block(sp, wl, block_size=10, wl_id=None):
    """Scan -> filter -> score -> selecteer. Returns blok of None."""
    wl_id = wl_id or wl.get("id")
    source_ids = wl.get("bron_playlists", [])
    taste_profile = _taste_profile(wl, wl_id)
    if not source_ids:
        logger.warning("Geen bronlijsten geconfigureerd")
        return None
    if not taste_profile:
        logger.warning("Geen smaakprofiel beschikbaar")
        return None

    t_start = time.time()
    all_tracks = scan_source_playlists(sp, source_ids)
    used_uris = (get_historie_uris(wl_id) | get_wachtrij_uris(wl_id)
                 | _load_playlist_uris(sp, wl["playlist_id"]))
    candidates = [t for t in all_tracks.values()
                  if t["uri"] not in used_uris and _is_recent_release(t.get("release_date", ""))]
    logger.info("Discovery kandidaten", extra={"kandidaten": len(candidates)})
    if not candidates:
        logger.warning("Geen nieuwe tracks gevonden in bronlijsten")
        return None

    scores = _score_all(candidates, taste_profile)
    selected = rank_and_select(candidates, scores, count=block_size,
                               max_per_artiest=wl.get("max_per_artiest", 0))
    logger.info("Discovery klaar",
                extra={"duur_sec": round(time.time() - t_start, 1), "geselecteerd": len(selected)})
    return _to_block(selected) if selected else None


def initial_fill_discovery(playlist_id, wl, on_progress=None):
    """Initieel vullen van een discovery-wissellijst: N blokken plus een wachtrij-blok."""
    from spotify_auth import get_spotify_client

    sp = get_spotify_client()
    wl_id = wl.get("id")
    taste_profile = _taste_profile(wl, wl_id)
    aantal_blokken = wl.get("aantal_blokken", 5)
    block_size = wl.get("blok_grootte", 10)
    per_rotatie = wl.get("blokken_per_rotatie", 1)
    totaal = aantal_blokken + 1

    if on_progress:
        on_progress(0, totaal, "Bronlijsten scannen...")
    all_tracks = scan_source_playlists(sp, wl.get("bron_playlists", []))
    used_uris = get_historie_uris(wl_id) | _load_playlist_uris(sp, playlist_id)
    candidates = [t for t in all_tracks.values()
                  if t["uri"] not in used_uris and _is_recent_release(t.get("release_date", ""))]
    if not candidates:
        return {"toegevoegd": 0, "blokken": 0, "mislukt": totaal, "wachtrij_klaar": False}

    if on_progress:
        on_progress(0, totaal, f"{len(candidates)} unieke tracks gevonden, scoring...")
    scores = _score_all(candidates, taste_profile, on_progress, totaal)
    all_selected = rank_and_select(candidates, scores,
                                   count=(aantal_blokken + per_rotatie) * block_size,
                                   max_per_artiest=wl.get("max_per_artiest", 0))

    added, mislukt = [], 0
    for blok_nr in range(1, totaal + 1):
        is_wachtrij = blok_nr == totaal
        if on_progress:
            on_progress(blok_nr, totaal,
                        "Toevoegen volgend blokje..." if is_wachtrij
                        else f"Toevoegen blok {blok_nr}/{aantal_blokken}...")
        # Het laatste "blok" is de wachtrij: per_rotatie blokken in één keer.
        eind = len(all_selected) if is_wachtrij else blok_nr * block_size
        block_tracks = all_selected[(blok_nr - 1) * block_size: eind]
        if not block_tracks:
            mislukt += 1
            continue
        block = _to_block(block_tracks)
        if is_wachtrij:
            save_wachtrij(wl_id, block)
        else:
            sp.playlist_add_items(playlist_id, [t["uri"] for t in block])
            added.extend(block)
            add_historie_bulk(wl_id, block)

    return {
        "toegevoegd": len(added),
        "blokken": len(added) // block_size if block_size else 0,
        "mislukt": mislukt,
        "wachtrij_klaar": mislukt < 2,
    }
