# -*- coding: utf-8 -*-
"""Discovery wissellijst: scan bronlijsten, bouw smaakprofiel, score met GPT."""
import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from config import (
    OPENAI_API_KEY,
    get_historie_uris, get_wachtrij_uris, get_smaakprofiel,
)
from logging_config import get_logger

logger = get_logger(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)


def build_taste_profile(sp):
    """Bouw een smaakprofiel op basis van Spotify luistergedrag."""
    try:
        top_artists_medium = sp.current_user_top_artists(
            limit=50, time_range='medium_term')['items']
    except Exception:
        top_artists_medium = []

    try:
        top_artists_short = sp.current_user_top_artists(
            limit=20, time_range='short_term')['items']
    except Exception:
        top_artists_short = []

    try:
        top_tracks = sp.current_user_top_tracks(
            limit=50, time_range='medium_term')['items']
    except Exception:
        top_tracks = []

    all_genres = {}
    for a in top_artists_medium:
        for g in a.get('genres', []):
            all_genres[g] = all_genres.get(g, 0) + 1
    sorted_genres = sorted(all_genres.items(), key=lambda x: -x[1])
    top_genres = [g for g, _ in sorted_genres[:20]]

    medium_artists = [a['name'] for a in top_artists_medium[:25]]
    recent_artists = [a['name'] for a in top_artists_short[:10]]

    track_lines = []
    for t in top_tracks[:30]:
        artists = ', '.join(a['name'] for a in t['artists'])
        track_lines.append(f"  - {artists} - {t['name']}")

    profile_parts = ["=== SMAAKPROFIEL ==="]
    if top_genres:
        profile_parts.append(f"Favoriete genres: {', '.join(top_genres)}")
    if medium_artists:
        profile_parts.append(
            f"Top artiesten (afgelopen 6 maanden): {', '.join(medium_artists)}")
    if recent_artists:
        profile_parts.append(
            f"Recent veel geluisterd: {', '.join(recent_artists)}")
    if track_lines:
        profile_parts.append("Top nummers:")
        profile_parts.extend(track_lines)

    return '\n'.join(profile_parts)


def scan_source_playlists(sp, playlist_ids):
    """Scan bronlijsten en tel overlap."""
    tracks_map = {}

    for idx, pid in enumerate(playlist_ids, 1):
        try:
            results = sp.playlist_items(
                pid,
                fields='items(track(uri,name,artists(name),album(name,release_date))),next',
                limit=100,
            )
            items = list(results['items'])
            while results.get('next'):
                results = sp.next(results)
                items.extend(results['items'])

            playlist_info = sp.playlist(pid, fields='name')
            playlist_name = playlist_info['name']

            logger.info("Bronlijst gescand",
                        extra={"playlist": playlist_name, "tracks": len(items),
                               "index": idx, "totaal": len(playlist_ids)})

            for item in items:
                track = item.get('track')
                if not track or not track.get('uri'):
                    continue

                uri = track['uri']
                artiest = (track['artists'][0]['name']
                           if track.get('artists') else 'Onbekend')
                titel = track['name']
                album = track['album']['name'] if track.get('album') else ''
                release_date = (track['album'].get('release_date', '')
                                if track.get('album') else '')

                if uri in tracks_map:
                    tracks_map[uri]['overlap'] += 1
                    tracks_map[uri]['bronnen'].append(playlist_name)
                else:
                    tracks_map[uri] = {
                        'artiest': artiest,
                        'titel': titel,
                        'album': album,
                        'release_date': release_date,
                        'uri': uri,
                        'overlap': 1,
                        'bronnen': [playlist_name],
                    }
        except Exception as e:
            logger.error("Fout bij scannen bronlijst",
                         extra={"playlist_id": pid, "error": str(e)})

    logger.info("Bronlijsten scan klaar",
                extra={"uniek": len(tracks_map), "bronlijsten": len(playlist_ids)})
    return tracks_map


def _is_recent_release(release_date, max_months=3):
    """Check of een track binnen de laatste max_months maanden is uitgebracht."""
    if not release_date:
        return False
    try:
        parts = release_date.split('-')
        if len(parts) >= 2:
            release = datetime(int(parts[0]), int(parts[1]),
                               int(parts[2]) if len(parts) >= 3 else 1)
        else:
            release = datetime(int(parts[0]), 1, 1)
        cutoff = datetime.now() - timedelta(days=max_months * 30)
        return release >= cutoff
    except (ValueError, IndexError):
        return False


def _load_playlist_uris(sp, playlist_id):
    """Haal alle URIs op uit een Spotify playlist."""
    uris = set()
    try:
        results = sp.playlist_items(
            playlist_id, fields='items(track(uri)),next', limit=100)
        for item in results['items']:
            if item.get('track') and item['track'].get('uri'):
                uris.add(item['track']['uri'])
        while results.get('next'):
            results = sp.next(results)
            for item in results['items']:
                if item.get('track') and item['track'].get('uri'):
                    uris.add(item['track']['uri'])
    except Exception:
        pass
    return uris


def score_candidates(candidates, taste_profile):
    """Score tracks met GPT op basis van smaakprofiel."""
    if not candidates:
        return {}

    track_lines = []
    for i, t in enumerate(candidates):
        overlap_text = (f" [{t['overlap']}x in bronlijsten]"
                        if t.get('overlap', 1) > 1 else "")
        track_lines.append(
            f"{i}. {t['artiest']} - {t['titel']} ({t.get('album', '')})"
            f"{overlap_text}")

    tracks_text = '\n'.join(track_lines)
    prompt = f"""{taste_profile}

=== OPDRACHT ===
Beoordeel onderstaande tracks op basis van het smaakprofiel hierboven.
Geef elke track een score van 1-10 (10 = perfecte match met de smaak).

Let op:
- Focus op genre, stijl, en vergelijkbare artiesten
- Nummers van artiesten die in het profiel staan krijgen een hogere score
- Wees kritisch maar eerlijk

Tracks om te beoordelen:
{tracks_text}

Antwoord ALLEEN met een JSON array, geen andere tekst:
[{{"i": 0, "s": 8}}, {{"i": 1, "s": 5}}, ...]"""

    try:
        import time
        t0 = time.time()
        logger.info("GPT scoring gestart", extra={"tracks": len(candidates)})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system",
                 "content": "Je bent een muziekexpert die tracks beoordeelt op "
                            "basis van iemands smaakprofiel. Antwoord alleen met JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        scores_list = json.loads(content)
        scores = {}
        for item in scores_list:
            idx = item.get('i', item.get('index', -1))
            score = item.get('s', item.get('score', 5))
            if 0 <= idx < len(candidates):
                scores[idx] = score

        elapsed = time.time() - t0
        avg = (sum(scores.values()) / len(scores)) if scores else 0
        logger.info("GPT scoring klaar",
                     extra={"duur_sec": round(elapsed, 1), "scores": len(scores),
                            "gemiddeld": round(avg, 1)})
        return scores

    except Exception as e:
        logger.error("GPT scoring fout", extra={"error": str(e)})
        return {i: 5 for i in range(len(candidates))}


def rank_and_select(candidates, scores, count=10, max_per_artiest=0):
    """Rank candidates op gecombineerde score en selecteer top N."""
    ranked = []
    for i, track in enumerate(candidates):
        smaak = scores.get(i, 5)
        overlap = min(track.get('overlap', 1), 5) * 2
        combined = smaak * 0.7 + overlap * 0.3
        ranked.append({**track, 'smaak_score': smaak, 'combined_score': combined})

    ranked.sort(key=lambda x: -x['combined_score'])

    logger.info("Discovery ranglijst",
                extra={"totaal": len(ranked), "selectie": count})

    selected = []
    artiest_count = {}
    for track in ranked:
        artiest = track['artiest']
        if max_per_artiest > 0:
            if artiest_count.get(artiest, 0) >= max_per_artiest:
                continue
        selected.append(track)
        artiest_count[artiest] = artiest_count.get(artiest, 0) + 1
        if len(selected) >= count:
            break

    return selected


def generate_discovery_block(sp, wl, history_file, block_size=10, wl_id=None):
    """Genereer een discovery blok: scan -> score -> rank -> selecteer."""
    source_ids = wl.get('bron_playlists', [])
    max_per_artiest = wl.get('max_per_artiest', 0)
    wl_id = wl_id or wl.get('id')

    import time

    # Smaakprofiel: DB of file
    taste_profile = ''
    if wl_id:
        taste_profile = get_smaakprofiel(wl_id)
    if not taste_profile:
        from config import get_smaakprofiel_file
        profiel_file = get_smaakprofiel_file(wl.get('id', ''))
        if os.path.exists(profiel_file):
            with open(profiel_file, 'r', encoding='utf-8') as f:
                taste_profile = f.read().strip()
    if not taste_profile:
        taste_profile = wl.get('smaakprofiel', '')

    if not source_ids:
        logger.warning("Geen bronlijsten geconfigureerd")
        return None
    if not taste_profile:
        logger.warning("Geen smaakprofiel beschikbaar")
        return None

    t_start = time.time()

    # Stap 1: Scan bronlijsten
    logger.info("Discovery stap 1: bronlijsten scannen",
                extra={"bronlijsten": len(source_ids)})
    all_tracks = scan_source_playlists(sp, source_ids)

    # Stap 2: Filter
    if wl_id:
        history_uris = get_historie_uris(wl_id)
        queue_uris = get_wachtrij_uris(wl_id)
    else:
        history_uris = set()
        queue_uris = set()

    playlist_uris = _load_playlist_uris(sp, wl['playlist_id'])
    used_uris = history_uris | playlist_uris | queue_uris

    candidates = [t for t in all_tracks.values() if t['uri'] not in used_uris]
    logger.info("Discovery stap 2: filter",
                extra={"kandidaten": len(candidates),
                       "historie": len(history_uris),
                       "playlist": len(playlist_uris),
                       "wachtrij": len(queue_uris)})

    pre_count = len(candidates)
    candidates = [t for t in candidates
                  if _is_recent_release(t.get('release_date', ''))]
    logger.info("Discovery stap 2b: release filter",
                extra={"kandidaten": len(candidates),
                       "gefilterd": pre_count - len(candidates)})

    if not candidates:
        logger.warning("Geen nieuwe tracks gevonden in bronlijsten")
        return None

    # Stap 3: Score met GPT
    all_scores = {}
    batch_size = 100
    n_batches = (len(candidates) + batch_size - 1) // batch_size
    logger.info("Discovery stap 3: GPT scoring", extra={"batches": n_batches})
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]
        batch_scores = score_candidates(batch, taste_profile)
        for local_idx, score in batch_scores.items():
            all_scores[batch_start + local_idx] = score

    # Stap 4: Rank en selecteer
    selected = rank_and_select(candidates, all_scores, count=block_size,
                               max_per_artiest=max_per_artiest)

    elapsed = time.time() - t_start
    logger.info("Discovery klaar",
                extra={"duur_sec": round(elapsed, 1),
                       "geselecteerd": len(selected)})

    if not selected:
        return None

    return [{'categorie': 'discovery', 'artiest': t['artiest'],
             'titel': t['titel'], 'uri': t['uri']} for t in selected]


def initial_fill_discovery(playlist_id, wl, history_file, queue_file,
                           on_progress=None):
    """Initieel vullen van een discovery wissellijst."""
    from suggest import get_spotify_client
    from config import save_wachtrij, add_historie_bulk

    import time
    t_start = time.time()

    sp = get_spotify_client()
    source_ids = wl.get('bron_playlists', [])
    max_per_artiest = wl.get('max_per_artiest', 0)
    wl_id = wl.get('id')

    taste_profile = ''
    if wl_id:
        taste_profile = get_smaakprofiel(wl_id)
    if not taste_profile:
        from config import get_smaakprofiel_file
        profiel_file = get_smaakprofiel_file(wl_id or '')
        if os.path.exists(profiel_file):
            with open(profiel_file, 'r', encoding='utf-8') as f:
                taste_profile = f.read().strip()
    if not taste_profile:
        taste_profile = wl.get('smaakprofiel', '')

    aantal_blokken = wl.get('aantal_blokken', 5)
    block_size = wl.get('blok_grootte', 10)
    totaal = aantal_blokken + 1

    logger.info("Discovery fill starten",
                extra={"bronlijsten": len(source_ids),
                       "blokken": aantal_blokken, "blok_grootte": block_size})

    if on_progress:
        on_progress(0, totaal, "Bronlijsten scannen...")

    all_tracks = scan_source_playlists(sp, source_ids)

    if wl_id:
        history_uris = get_historie_uris(wl_id)
    else:
        history_uris = set()

    playlist_uris = _load_playlist_uris(sp, playlist_id)
    used_uris = history_uris | playlist_uris

    candidates = [t for t in all_tracks.values() if t['uri'] not in used_uris]
    pre_count = len(candidates)
    candidates = [t for t in candidates
                  if _is_recent_release(t.get('release_date', ''))]

    if not candidates:
        return {"toegevoegd": 0, "blokken": 0,
                "mislukt": totaal, "wachtrij_klaar": False}

    if on_progress:
        on_progress(0, totaal, f"{len(candidates)} unieke tracks gevonden, scoring...")

    all_scores = {}
    batch_size = 100
    n_batches = (len(candidates) + batch_size - 1) // batch_size
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]
        batch_nr = batch_start // batch_size + 1
        if on_progress:
            on_progress(0, totaal, f"Scoring batch {batch_nr}/{n_batches}...")
        batch_scores = score_candidates(batch, taste_profile)
        for local_idx, score in batch_scores.items():
            all_scores[batch_start + local_idx] = score

    total_needed = totaal * block_size
    all_selected = rank_and_select(candidates, all_scores, count=total_needed,
                                   max_per_artiest=max_per_artiest)

    alle_tracks_added = []
    mislukt = 0

    for blok_nr in range(1, totaal + 1):
        is_wachtrij = blok_nr == totaal
        label = ("volgend blokje" if is_wachtrij
                 else f"blok {blok_nr}/{aantal_blokken}")
        if on_progress:
            on_progress(blok_nr, totaal, f"Toevoegen {label}...")

        start_idx = (blok_nr - 1) * block_size
        end_idx = start_idx + block_size
        block_tracks = all_selected[start_idx:end_idx]

        if not block_tracks:
            mislukt += 1
            continue

        block = [{'categorie': 'discovery', 'artiest': t['artiest'],
                  'titel': t['titel'], 'uri': t['uri']} for t in block_tracks]

        if is_wachtrij:
            if wl_id:
                save_wachtrij(wl_id, block)
            else:
                with open(queue_file, "w", encoding="utf-8") as f:
                    for t in block:
                        f.write(f"{t['categorie']} - {t['artiest']} - "
                                f"{t['titel']} - {t['uri']}\n")
        else:
            uris = [t['uri'] for t in block]
            sp.playlist_add_items(playlist_id, uris)
            alle_tracks_added.extend(block)
            if wl_id:
                add_historie_bulk(wl_id, block)
            else:
                with open(history_file, "a", encoding="utf-8") as hf:
                    for t in block:
                        hf.write(f"{t['categorie']} - {t['artiest']} - "
                                 f"{t['titel']} - {t['uri']}\n")

    elapsed = time.time() - t_start
    blokken_ok = len(alle_tracks_added) // block_size if block_size else 0
    logger.info("Discovery fill klaar",
                extra={"duur_sec": round(elapsed, 1),
                       "tracks": len(alle_tracks_added),
                       "blokken": blokken_ok, "mislukt": mislukt})

    return {
        "toegevoegd": len(alle_tracks_added),
        "blokken": blokken_ok,
        "mislukt": mislukt,
        "wachtrij_klaar": mislukt < 2,
    }
