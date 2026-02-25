import datetime
import os
import re

from config import (
    QUEUE_FILE, HISTORY_FILE,
    load_wissellijsten, get_history_file, get_queue_file,
    get_wachtrij, save_wachtrij, clear_wachtrij,
    get_historie_uris, add_historie_bulk,
)
from suggest import _parse_history_line, get_spotify_client
from logging_config import get_logger

logger = get_logger(__name__)


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


def rotate_playlist(playlist_id, wl_id=None, queue_file=None, history_file=None,
                    sort_by_age=False):
    """Verwijder de oudste nummers en voeg nieuwe toe uit de wachtrij.

    Args:
        wl_id: wissellijst ID (voor DB-backed operaties)
        sort_by_age: Als True, sorteer op added_at (oudste eerst).
    """
    # Lees wachtrij via DB of file
    if wl_id:
        queue_entries = get_wachtrij(wl_id)
    else:
        queue_file = queue_file or QUEUE_FILE
        queue_entries = []
        if os.path.exists(queue_file) and os.stat(queue_file).st_size > 0:
            with open(queue_file, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_history_line(line.strip())
                    if parsed:
                        queue_entries.append(parsed)

    if not queue_entries:
        logger.info("Wachtrij is leeg, geen update nodig")
        return {"status": "leeg", "tekst": "Wachtrij is leeg."}

    new_uris = [e["uri"] for e in queue_entries]
    new_tracks_detail = [{"artiest": e["artiest"], "titel": e["titel"]} for e in queue_entries]

    block_size = len(new_uris)
    sp = get_spotify_client()

    # Haal huidige playlist op
    if sort_by_age:
        current_items = _get_all_playlist_items(sp, playlist_id)
        current_items.sort(key=lambda x: x.get("added_at", "9999"))
    else:
        current_items = sp.playlist_items(playlist_id, limit=50)["items"]

    # Log de te verwijderen tracks naar historie
    tracks_to_remove = []
    removed_tracks_detail = []
    historie_entries = []

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
                logger.info("Discovery remove",
                            extra={"artiest": artist, "titel": name,
                                   "dagen_oud": days_ago})
            except (ValueError, TypeError):
                pass

        historie_entries.append({
            "categorie": decade, "artiest": artist,
            "titel": name, "uri": uri,
        })
        tracks_to_remove.append(uri)
        removed_tracks_detail.append({"artiest": artist, "titel": name})

    # Schrijf naar historie
    if historie_entries:
        if wl_id:
            add_historie_bulk(wl_id, historie_entries)
        else:
            history_file = history_file or HISTORY_FILE
            with open(history_file, "a", encoding="utf-8") as hf:
                for e in historie_entries:
                    hf.write(f"{e['categorie']} - {e['artiest']} - {e['titel']} - {e['uri']}\n")

    # Verwijder oud, voeg nieuw toe
    if tracks_to_remove:
        sp.playlist_remove_all_occurrences_of_items(playlist_id, tracks_to_remove)
        sp.playlist_add_items(playlist_id, new_uris)
        logger.info("Playlist geroteerd",
                     extra={"verwijderd": len(tracks_to_remove),
                            "toegevoegd": len(new_uris)})

    # Wachtrij leegmaken
    if wl_id:
        clear_wachtrij(wl_id)
    elif queue_file:
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


def _check_queue_decades(sp, wl_id=None, queue_file=None):
    """Check of tracks in wachtrij bij het juiste decennium horen. Alleen logging."""
    if wl_id:
        entries = get_wachtrij(wl_id)
    else:
        entries = []
        if queue_file and os.path.exists(queue_file) and os.stat(queue_file).st_size > 0:
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
        logger.warning("Kon tracks niet ophalen voor decade-check",
                       extra={"error": str(exc)})
        return

    for entry, track in zip(entries, tracks_info):
        if not track:
            continue

        release_date = track.get("album", {}).get("release_date", "")
        actual_decade = get_decade(release_date)

        match = re.match(r'(\d{2}s)', entry["categorie"])
        expected = match.group(1) if match else None

        artist = entry["artiest"]
        title = entry["titel"]

        if expected and actual_decade != "Unknown" and actual_decade != expected:
            logger.warning("Decade mismatch",
                           extra={"artiest": artist, "titel": title,
                                  "verwacht": expected, "werkelijk": actual_decade})
        elif expected:
            logger.debug("Decade OK",
                         extra={"artiest": artist, "titel": title,
                                "decade": actual_decade})


def rotate_and_regenerate(wl):
    """Roteer een wissellijst en genereer een nieuw wachtrij-blok."""
    from suggest import generate_block

    wl_id = wl["id"]
    queue_file = get_queue_file(wl_id)
    history_file = get_history_file(wl_id)
    is_discovery = wl.get("type") == "discovery"

    if is_discovery:
        return _rotate_discovery(wl, wl_id, queue_file, history_file)

    # --- Categorie flow ---
    sp = get_spotify_client()
    logger.info("Decade check starten",
                extra={"wissellijst": wl.get("naam", wl_id)})
    _check_queue_decades(sp, wl_id=wl_id, queue_file=queue_file)

    # Stap 1: Roteer
    result = rotate_playlist(wl["playlist_id"], wl_id=wl_id,
                             queue_file=queue_file, history_file=history_file)

    if result["status"] == "leeg":
        return result

    # Stap 2: Genereer nieuw blokje
    block = None
    max_retries = 3
    for _ in range(max_retries):
        block = generate_block(sp, wl["playlist_id"],
                               wl.get("categorieen", []),
                               history_file=history_file,
                               wl_id=wl_id,
                               max_per_artiest=wl.get("max_per_artiest", 0))
        if block:
            break

    if block:
        save_wachtrij(wl_id, block)
        result["nieuw_blok"] = True
    else:
        result["nieuw_blok"] = False
        result["tekst"] += " (Nieuw wachtrij-blok genereren mislukt)"

    return result


def _rotate_discovery(wl, wl_id, queue_file, history_file):
    """Discovery rotatie: eerst analyseren, dan roteren."""
    from discovery import generate_discovery_block

    sp = get_spotify_client()
    block_size = wl.get("blok_grootte", 10)

    expired_count = _count_expired_tracks(sp, wl["playlist_id"], max_days=30)
    effective_size = max(block_size, expired_count)

    if expired_count > block_size:
        logger.info("Expired tracks verhogen blokgrootte",
                     extra={"expired": expired_count, "blok_grootte": block_size,
                            "effective": effective_size})

    logger.info("Discovery analyseren",
                extra={"wissellijst": wl["naam"], "tracks": effective_size})
    block = generate_discovery_block(
        sp, wl, history_file,
        block_size=effective_size,
        wl_id=wl_id,
    )

    if not block:
        return {
            "status": "fout",
            "tekst": "Kon geen nieuw blok genereren uit bronlijsten.",
        }

    save_wachtrij(wl_id, block)
    logger.info("Wachtrij gevuld", extra={"tracks": len(block)})

    logger.info("Discovery roteren (oudste eerst)")
    result = rotate_playlist(wl["playlist_id"], wl_id=wl_id,
                             queue_file=queue_file, history_file=history_file,
                             sort_by_age=True)
    result["nieuw_blok"] = True
    return result


if __name__ == "__main__":
    data = load_wissellijsten()
    if not data["wissellijsten"]:
        playlist_id = os.environ.get("SPOTIFY_PLAYLIST_ID", "")
        if not playlist_id:
            logger.error("Geen wissellijsten gevonden en SPOTIFY_PLAYLIST_ID niet ingesteld.")
            exit(1)
        rotate_playlist(playlist_id)
    else:
        for wl in data["wissellijsten"]:
            logger.info("Roteer wissellijst", extra={"naam": wl["naam"]})
            rotate_and_regenerate(wl)
