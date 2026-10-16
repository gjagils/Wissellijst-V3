"""Rotatie: oudste blok eruit, wachtrij erin, nieuw blok klaarzetten."""
import datetime
import re

from config import (
    DISCOVERY_EXPIRE_DAGEN,
    get_wachtrij, save_wachtrij, clear_wachtrij, add_historie_bulk,
)
from spotify_auth import get_spotify_client
from logging_config import get_logger

logger = get_logger(__name__)


def get_decade(release_date):
    """'1994-03-01' -> '90s'; onbekend -> 'Unknown'."""
    try:
        year = int(release_date.split("-")[0])
        return f"{str((year // 10) * 10)[2:]}s"
    except Exception:
        return "Unknown"


def _get_all_playlist_items(sp, playlist_id):
    items = []
    result = sp.playlist_items(playlist_id, limit=100)
    items.extend(result["items"])
    while result.get("next"):
        result = sp.next(result)
        items.extend(result["items"])
    return items


def _count_expired_tracks(sp, playlist_id, max_days):
    """Aantal tracks dat langer dan max_days in de playlist staat."""
    now = datetime.datetime.now(datetime.timezone.utc)
    count = 0
    for item in _get_all_playlist_items(sp, playlist_id):
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


def rotate_playlist(playlist_id, wl_id, sort_by_age=False):
    """Verwijder de oudste nummers (zoveel als de wachtrij lang is) en voeg de wachtrij toe.

    De wachtrij bevat blokken_per_rotatie blokken, dus zoveel blokken wisselen er.

    sort_by_age: sorteer op added_at (discovery); anders de playlist-volgorde.
    """
    queue_entries = get_wachtrij(wl_id)
    if not queue_entries:
        logger.info("Wachtrij is leeg, geen update nodig")
        return {"status": "leeg", "tekst": "Wachtrij is leeg."}

    new_uris = [e["uri"] for e in queue_entries]
    new_tracks_detail = [{"artiest": e["artiest"], "titel": e["titel"], "uri": e["uri"]}
                         for e in queue_entries]
    block_size = len(new_uris)
    sp = get_spotify_client()

    if sort_by_age:
        current_items = _get_all_playlist_items(sp, playlist_id)
        current_items.sort(key=lambda x: x.get("added_at", "9999"))
    elif block_size <= 100:
        current_items = sp.playlist_items(playlist_id, limit=block_size)["items"]
    else:
        current_items = _get_all_playlist_items(sp, playlist_id)

    tracks_to_remove, removed_tracks_detail, historie_entries = [], [], []
    for item in current_items[:block_size]:
        track = item["track"]
        if not track:
            continue
        historie_entries.append({
            "categorie": get_decade(track["album"]["release_date"]),
            "artiest": track["artists"][0]["name"],
            "titel": track["name"],
            "uri": track["uri"],
        })
        tracks_to_remove.append(track["uri"])
        removed_tracks_detail.append({"artiest": track["artists"][0]["name"],
                                      "titel": track["name"], "uri": track["uri"]})

    if historie_entries:
        add_historie_bulk(wl_id, historie_entries)

    if tracks_to_remove:
        sp.playlist_remove_all_occurrences_of_items(playlist_id, tracks_to_remove)
        sp.playlist_add_items(playlist_id, new_uris)
        logger.info("Playlist geroteerd",
                    extra={"verwijderd": len(tracks_to_remove), "toegevoegd": len(new_uris)})

    clear_wachtrij(wl_id)

    return {
        "status": "ok",
        "tekst": f"{len(tracks_to_remove)} nummers geroteerd.",
        "verwijderd": len(tracks_to_remove),
        "toegevoegd": len(new_uris),
        "verwijderd_detail": removed_tracks_detail,
        "toegevoegd_detail": new_tracks_detail,
    }


def _check_queue_decades(sp, wl_id):
    """Log of wachtrij-tracks bij hun decennium horen (alleen logging)."""
    entries = get_wachtrij(wl_id)
    if not entries:
        return
    try:
        tracks_info = sp.tracks([e["uri"] for e in entries])["tracks"]
    except Exception as exc:
        logger.warning("Kon tracks niet ophalen voor decade-check", extra={"error": str(exc)})
        return
    for entry, track in zip(entries, tracks_info):
        if not track:
            continue
        actual = get_decade(track.get("album", {}).get("release_date", ""))
        match = re.match(r'(\d{2}s)', entry["categorie"])
        expected = match.group(1) if match else None
        if expected and actual != "Unknown" and actual != expected:
            logger.warning("Decade mismatch",
                           extra={"artiest": entry["artiest"], "titel": entry["titel"],
                                  "verwacht": expected, "werkelijk": actual})


def rotate_and_regenerate(wl):
    """Roteer een wissellijst en zet een nieuwe wachtrij (blokken_per_rotatie blokken) klaar."""
    from suggest import generate_blocks

    wl_id = wl["id"]
    if wl.get("type") == "discovery":
        return _rotate_discovery(wl, wl_id)

    sp = get_spotify_client()
    _check_queue_decades(sp, wl_id)

    def nieuw_blok():
        return generate_blocks(sp, wl["playlist_id"], wl.get("categorieen", []), wl_id,
                               wl.get("max_per_artiest", 0),
                               aantal=wl.get("blokken_per_rotatie", 1),
                               populariteit=wl.get("populariteit_verdeling", False),
                               radio=wl.get("radiolijsten", False))

    # Lege wachtrij: eerst een blok genereren, anders blijft de lijst hangen.
    if not get_wachtrij(wl_id):
        logger.warning("Wachtrij leeg bij rotatie, eerst blok genereren",
                       extra={"wissellijst": wl.get("naam", wl_id)})
        block = nieuw_blok()
        if not block:
            return {"status": "fout",
                    "tekst": "Wachtrij was leeg en nieuw blok genereren mislukt."}
        save_wachtrij(wl_id, block)

    result = rotate_playlist(wl["playlist_id"], wl_id)
    if result["status"] == "leeg":
        return result

    block = nieuw_blok()
    if block:
        save_wachtrij(wl_id, block)
        result["nieuw_blok"] = True
    else:
        result["nieuw_blok"] = False
        result["tekst"] += " (Nieuw wachtrij-blok genereren mislukt)"
    return result


def _rotate_discovery(wl, wl_id):
    """Discovery: eerst analyseren en een blok maken, dan roteren op leeftijd."""
    from discovery import generate_discovery_block

    sp = get_spotify_client()
    block_size = wl.get("blok_grootte", 10) * wl.get("blokken_per_rotatie", 1)
    expired_count = _count_expired_tracks(sp, wl["playlist_id"], DISCOVERY_EXPIRE_DAGEN)
    effective_size = max(block_size, expired_count)
    if expired_count > block_size:
        logger.info("Expired tracks verhogen blokgrootte",
                    extra={"expired": expired_count, "blok_grootte": block_size})

    block = generate_discovery_block(sp, wl, block_size=effective_size, wl_id=wl_id)
    if not block:
        return {"status": "fout", "tekst": "Kon geen nieuw blok genereren uit bronlijsten."}

    save_wachtrij(wl_id, block)
    result = rotate_playlist(wl["playlist_id"], wl_id, sort_by_age=True)
    result["nieuw_blok"] = True
    return result
