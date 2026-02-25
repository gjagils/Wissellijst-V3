"""Policy validators voor Wissellijst V3.

Validatie van wissellijst configuratie en rotatie-regels.
"""
import re


def validate_wissellijst_config(data):
    """Valideer wissellijst configuratie bij opslaan.

    Args:
        data: dict met wissellijst configuratie

    Returns:
        (is_valid, errors) tuple

    Raises: niets, returns altijd een tuple.
    """
    errors = []

    # Verplichte velden
    if not data.get("naam", "").strip():
        errors.append("Naam is verplicht")

    if not data.get("playlist_id", "").strip():
        errors.append("Playlist ID is verplicht")

    # Type validatie
    wl_type = data.get("type", "categorie")
    if wl_type not in ("categorie", "discovery"):
        errors.append(f"Ongeldig type: {wl_type}")

    # Categorie-specifieke validatie
    if wl_type == "categorie":
        cats = data.get("categorieen", [])
        if not cats or len(cats) == 0:
            errors.append("Minimaal 1 categorie vereist voor categorie-type")

    # Discovery-specifieke validatie
    if wl_type == "discovery":
        bronnen = data.get("bron_playlists", [])
        if not bronnen or len(bronnen) == 0:
            errors.append("Minimaal 1 bronlijst vereist voor discovery-type")

    # Numerieke velden
    aantal_blokken = data.get("aantal_blokken", 10)
    if not isinstance(aantal_blokken, int) or aantal_blokken < 1:
        errors.append("aantal_blokken moet een positief geheel getal zijn")

    blok_grootte = data.get("blok_grootte", 5)
    if not isinstance(blok_grootte, int) or blok_grootte < 1:
        errors.append("blok_grootte moet een positief geheel getal zijn")

    max_per_artiest = data.get("max_per_artiest", 0)
    if not isinstance(max_per_artiest, int) or max_per_artiest < 0:
        errors.append("max_per_artiest moet 0 of een positief geheel getal zijn")

    # Rotatie schema validatie
    schema = data.get("rotatie_schema", "uit")
    geldige_schemas = ("uit", "elk_uur", "elke_3_uur", "dagelijks", "wekelijks")
    if schema not in geldige_schemas:
        errors.append(f"Ongeldig rotatie_schema: {schema}")

    # Tijdstip validatie (HH:MM)
    tijdstip = data.get("rotatie_tijdstip", "08:00")
    if tijdstip and not re.match(r'^\d{2}:\d{2}$', tijdstip):
        errors.append(f"Ongeldig tijdstip formaat: {tijdstip} (verwacht HH:MM)")

    # Dag validatie (0-6)
    dag = data.get("rotatie_dag", 0)
    if not isinstance(dag, int) or dag < 0 or dag > 6:
        errors.append(f"Ongeldige dag: {dag} (verwacht 0-6)")

    return (len(errors) == 0, errors)


def validate_artist_limit(artist, artist_counts, max_per_artiest):
    """Check of een artiest het maximum aantal keer heeft bereikt.

    Args:
        artist: artiestnaam
        artist_counts: dict van artiest -> count
        max_per_artiest: maximum (0 = onbeperkt)

    Returns:
        True als de artiest nog mag, False als max bereikt.
    """
    if max_per_artiest <= 0:
        return True
    return artist_counts.get(artist, 0) < max_per_artiest


def validate_history(candidate_uri, history_uris):
    """Check of een URI al in de historie staat.

    Args:
        candidate_uri: Spotify URI om te checken
        history_uris: set van URIs in historie

    Returns:
        True als de URI NIET in de historie staat (geldig).
    """
    return candidate_uri not in history_uris


def validate_decade(release_date, expected_decade):
    """Check of een release datum bij het verwachte decennium past.

    Args:
        release_date: Spotify release date string (YYYY-MM-DD, YYYY-MM, of YYYY)
        expected_decade: verwacht decennium (bijv. '80s', '90s')

    Returns:
        True als het klopt of niet te bepalen, False als het niet klopt.
    """
    if not expected_decade or not release_date:
        return True

    try:
        year = int(release_date.split("-")[0])
        actual_decade = f"{str((year // 10) * 10)[2:]}s"
        return actual_decade == expected_decade
    except (ValueError, IndexError):
        return True  # Bij twijfel: accepteren
