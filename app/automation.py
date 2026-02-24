import datetime
import os
import re

from config import (
    QUEUE_FILE, HISTORY_FILE,
    load_wissellijsten, get_history_file, get_queue_file,
)
from suggest import _parse_history_line, get_spotify_client


def get_decade(release_date):
    """Bepaal het decennium op basis van release datum, bijv. '90s'."""
    try:
        year = int(release_date.split("-")[0])
        return f"{str((year // 10) * 10)[2:]}s"
    except Exception:
        return "Unknown"


def _get_all_playlist_items(sp, playlist_id):
    """Haal alle items uit een playlist op (met paginering)."""
    items = []
    result = sp.playlist_items(playlist_id, limit=100)
    items.extend(result["items"])
    while result.get("next"):
        result = sp.next(result)
        items.extend(result["items"])
    return items


def _count_expired_tracks(sp, playlist_id, max_days=30):
    """Tel hoeveel tracks ouder zijn dan max_days in de playlist."""
    items = _get_all_playlist_items(sp, playlist_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    count = 0
    for item in items:
        added_at = item.get("added_at")
        if not added_at:
            continue
        try:
            added_dt = datetime.datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            if (now - added_dt).days >= max_days:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


def rotate_playlist(playlist_id, queue_file=None, history_file=None,
                    sort_by_age=False):
    """Verwijder de oudste nummers en voeg nieuwe toe uit de wachtrij.

    Args:
        sort_by_age: Als True, sorteer op added_at (oudste eerst) i.p.v.
                     playlist-positie. Gebruikt voor discovery.
    """
    queue_file = queue_file or QUEUE_FILE
    history_file = history_file or HISTORY_FILE

    if not os.path.exists(queue_file) or os.stat(queue_file).st_size == 0:
        print("Wachtrij is leeg. Geen update nodig.")
        return {"status": "leeg", "tekst": "Wachtrij is leeg."}

    # Lees wachtrij - ondersteunt zowel URI-only als volledig formaat
    new_uris = []
    new_tracks_detail = []
    with open(queue_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parsed = _parse_history_line(line)
            if parsed:
                new_uris.append(parsed["uri"])
                new_tracks_detail.append({"artiest": parsed["artiest"], "titel": parsed["titel"]})
            elif line.startswith("spotify:"):
                new_uris.append(line)

    if not new_uris:
        print("Wachtrij is leeg.")
        return {"status": "leeg", "tekst": "Wachtrij is leeg."}

    block_size = len(new_uris)
    sp = get_spotify_client()

    # Haal huidige playlist op
    if sort_by_age:
        current_items = _get_all_playlist_items(sp, playlist_id)
        # Sorteer op added_at (oudste eerst)
        current_items.sort(
            key=lambda x: x.get("added_at", "9999"),
        )
    else:
        current_items = sp.playlist_items(playlist_id, limit=50)["items"]

    # Log de te verwijderen tracks naar historie
    tracks_to_remove = []
    removed_tracks_detail = []

    with open(history_file, "a", encoding="utf-8") as hf:
        for item in current_items[:block_size]:
            track = item["track"]
            if not track:
                continue
            decade = get_decade(track["album"]["release_date"])
            artist = track["artists"][0]["name"]
            name = track["name"]
            uri = track["uri"]
            added_at = item.get("added_at", "")
            if sort_by_age and added_at:
                try:
                    added_dt = datetime.datetime.fromisoformat(
                        added_at.replace("Z", "+00:00"))
                    days_ago = (datetime.datetime.now(datetime.timezone.utc) - added_dt).days
                    print(f"  [discovery-remove] {artist} - {name} "
                          f"({days_ago} dagen oud)", flush=True)
                except (ValueError, TypeError):
                    pass
            hf.write(f"{decade} - {artist} - {name} - {uri}\n")
            tracks_to_remove.append(uri)
            removed_tracks_detail.append({"artiest": artist, "titel": name})

    # Verwijder oud, voeg nieuw toe
    if tracks_to_remove:
        sp.playlist_remove_all_occurrences_of_items(playlist_id, tracks_to_remove)
        sp.playlist_add_items(playlist_id, new_uris)
        print("Playlist succesvol geroteerd.")

    # Haal details op voor nieuwe tracks als die URI-only waren
    if new_uris and not new_tracks_detail:
        try:
            tracks_info = sp.tracks(new_uris)
            for t in tracks_info.get("tracks", []):
                if t:
                    new_tracks_detail.append({
                        "artiest": t["artists"][0]["name"],
                        "titel": t["name"],
                    })
        except Exception:
            pass

    # Wachtrij leegmaken
    with open(queue_file, "w") as f:
        f.write("")

    return {
        "status": "ok",
        "tekst": f"{len(tracks_to_remove)} nummers geroteerd.",
        "verwijderd": len(tracks_to_remove),
        "toegevoegd": len(new_uris),
        "verwijderd_detail": removed_tracks_detail,
        "toegevoegd_detail": new_tracks_detail,
    }


def _check_queue_decades(sp, queue_file):
    """Check of tracks in wachtrij bij het juiste decennium horen. Alleen logging."""
    if not os.path.exists(queue_file) or os.stat(queue_file).st_size == 0:
        return

    entries = []
    with open(queue_file, "r", encoding="utf-8") as f:
        for line in f:
            parsed = _parse_history_line(line)
            if parsed:
                entries.append(parsed)

    if not entries:
        return

    uris = [e["uri"] for e in entries]
    try:
        tracks_info = sp.tracks(uris)["tracks"]
    except Exception as exc:
        print(f"  [decade-check] Kon tracks niet ophalen: {exc}", flush=True)
        return

    for entry, track in zip(entries, tracks_info):
        if not track:
            continue

        release_date = track.get("album", {}).get("release_date", "")
        actual_decade = get_decade(release_date)

        # Haal verwacht decennium uit de categorienaam (bijv. "80s" uit "80s heeft in de...")
        match = re.match(r'(\d{2}s)', entry["categorie"])
        expected = match.group(1) if match else None

        artist = entry["artiest"]
        title = entry["titel"]

        if expected and actual_decade != "Unknown" and actual_decade != expected:
            print(f"  [decade-check] ✗ {artist} - {title}: "
                  f"categorie={expected} maar release={release_date} ({actual_decade})",
                  flush=True)
        elif expected:
            print(f"  [decade-check] ✓ {artist} - {title}: "
                  f"{actual_decade} past bij {expected}",
                  flush=True)


def rotate_and_regenerate(wl):
    """Roteer een wissellijst en genereer een nieuw wachtrij-blok.

    Voor discovery: eerst nieuw blok analyseren, dan pas roteren.
    Voor categorie: eerst roteren, dan nieuw blok genereren.

    Args:
        wl: wissellijst dict met alle configuratie
    Returns: dict met resultaten
    """
    from suggest import generate_block

    queue_file = get_queue_file(wl["id"])
    history_file = get_history_file(wl["id"])
    is_discovery = wl.get("type") == "discovery"

    if is_discovery:
        return _rotate_discovery(wl, queue_file, history_file)

    # --- Categorie flow: decade-check, roteer, genereer ---

    # Stap 0: Decade check op wachtrij (logging)
    sp = get_spotify_client()
    print(f"[decade-check] Controleer wachtrij voor {wl.get('naam', wl['id'])}...",
          flush=True)
    _check_queue_decades(sp, queue_file)

    # Stap 1: Roteer
    result = rotate_playlist(wl["playlist_id"], queue_file=queue_file,
                             history_file=history_file)

    if result["status"] == "leeg":
        return result

    # Stap 2: Genereer nieuw blokje voor de wachtrij
    block = None
    max_retries = 3
    for _ in range(max_retries):
        block = generate_block(sp, wl["playlist_id"],
                               wl.get("categorieen", []),
                               history_file=history_file,
                               max_per_artiest=wl.get("max_per_artiest", 0))
        if block:
            break

    if block:
        with open(queue_file, "w", encoding="utf-8") as f:
            for t in block:
                f.write(f"{t['categorie']} - {t['artiest']} - {t['titel']} - {t['uri']}\n")
        result["nieuw_blok"] = True
    else:
        result["nieuw_blok"] = False
        result["tekst"] += " (Nieuw wachtrij-blok genereren mislukt)"

    return result


def _rotate_discovery(wl, queue_file, history_file):
    """Discovery rotatie: eerst analyseren, dan roteren.

    Verwijdert de oudste tracks (op added_at). Tracks ouder dan 30 dagen
    worden sowieso verwijderd, ook als dat meer is dan blok_grootte.

    1. Bepaal effectieve blokgrootte (rekening houdend met >30 dagen)
    2. Genereer nieuw blok (scan bronlijsten + GPT scoring)
    3. Schrijf naar wachtrij
    4. Roteer playlist (oudste eruit, wachtrij erin)
    """
    from discovery import generate_discovery_block

    sp = get_spotify_client()
    block_size = wl.get("blok_grootte", 10)

    # Stap 0: Tel tracks ouder dan 30 dagen
    expired_count = _count_expired_tracks(sp, wl["playlist_id"], max_days=30)
    effective_size = max(block_size, expired_count)

    if expired_count > block_size:
        print(f"[discovery-rotate] {expired_count} tracks ouder dan 30 dagen "
              f"(blok_grootte={block_size}), verhoogd naar {effective_size}",
              flush=True)

    # Stap 1: Genereer nieuw blok
    print(f"[discovery-rotate] Stap 1: Analyseren voor {wl['naam']} "
          f"({effective_size} tracks)...", flush=True)
    block = generate_discovery_block(
        sp, wl, history_file,
        block_size=effective_size,
    )

    if not block:
        return {
            "status": "fout",
            "tekst": "Kon geen nieuw blok genereren uit bronlijsten.",
        }

    # Stap 2: Schrijf naar wachtrij
    with open(queue_file, "w", encoding="utf-8") as f:
        for t in block:
            f.write(f"{t['categorie']} - {t['artiest']} - "
                    f"{t['titel']} - {t['uri']}\n")
    print(f"[discovery-rotate] Stap 2: {len(block)} tracks in wachtrij",
          flush=True)

    # Stap 3: Roteer (sort_by_age=True: oudste op added_at eerst)
    print(f"[discovery-rotate] Stap 3: Roteren (oudste eerst)...", flush=True)
    result = rotate_playlist(wl["playlist_id"], queue_file=queue_file,
                             history_file=history_file, sort_by_age=True)
    result["nieuw_blok"] = True
    return result


if __name__ == "__main__":
    # Roteer alle wissellijsten
    data = load_wissellijsten()
    if not data["wissellijsten"]:
        # Fallback naar oude env-var manier
        playlist_id = os.environ.get("SPOTIFY_PLAYLIST_ID", "")
        if not playlist_id:
            print("Geen wissellijsten gevonden en SPOTIFY_PLAYLIST_ID niet ingesteld.")
            exit(1)
        rotate_playlist(playlist_id)
    else:
        for wl in data["wissellijsten"]:
            print(f"Roteer: {wl['naam']}...")
            queue_file = get_queue_file(wl["id"])
            history_file = get_history_file(wl["id"])
            rotate_playlist(wl["playlist_id"], queue_file=queue_file,
                            history_file=history_file)
