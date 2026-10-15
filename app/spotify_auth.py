"""Spotify-koppeling: OAuth met het token in de database.

- DbCacheHandler: spotipy's cache-haakje, leest/schrijft de tabel spotify_token.
- get_spotify_client(): client voor de rest van de app; raist "auth_required"
  als er geen bruikbaar token is.
- ensure_token(): snelle controle vóór een rotatie, zodat een ingetrokken
  token direct als spotify_auth-fout wordt gemeld in plaats van halverwege.
- import_cache_file(): eenmalige overname van het oude bestand .cache.
"""
import json
import os
import secrets

import spotipy
from spotipy.cache_handler import CacheHandler
from spotipy.oauth2 import SpotifyOAuth

from config import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE, DATA_DIR,
)
from logging_config import get_logger

logger = get_logger(__name__)

LEGACY_CACHE_PATH = os.path.join(DATA_DIR, ".cache")


class DbCacheHandler(CacheHandler):
    """Token in de tabel spotify_token (altijd rij 1)."""

    def get_cached_token(self):
        from db.session import db_available, get_session
        from db.models import SpotifyToken

        if not db_available():
            return None
        with get_session() as session:
            row = session.get(SpotifyToken, 1)
            return dict(row.token_info) if row else None

    def save_token_to_cache(self, token_info):
        from db.session import get_session
        from db.models import SpotifyToken, utcnow

        with get_session() as session:
            row = session.get(SpotifyToken, 1)
            if row:
                row.token_info = dict(token_info)
                row.updated_at = utcnow()
            else:
                session.add(SpotifyToken(id=1, token_info=dict(token_info)))


def make_auth_manager(state=None):
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        state=state,
        cache_handler=DbCacheHandler(),
        open_browser=False,
    )


def new_state():
    """Willekeurige state voor de OAuth-flow (CSRF-bescherming)."""
    return secrets.token_urlsafe(24)


def clear_token():
    from db.session import db_available, get_session
    from db.models import SpotifyToken

    if not db_available():
        return
    with get_session() as session:
        row = session.get(SpotifyToken, 1)
        if row:
            session.delete(row)


def set_spotify_user(naam):
    from db.session import get_session
    from db.models import SpotifyToken

    with get_session() as session:
        row = session.get(SpotifyToken, 1)
        if row:
            row.spotify_user = naam or ""


def token_status():
    """Is er een token, voor wie, en wanneer ververst? Geen API-call."""
    from db.session import db_available, get_session
    from db.models import SpotifyToken

    if not db_available():
        return {"present": False}
    with get_session() as session:
        row = session.get(SpotifyToken, 1)
        if not row:
            return {"present": False}
        return {
            "present": True,
            "user": row.spotify_user or None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "scope": (row.token_info or {}).get("scope", ""),
        }


def get_spotify_client():
    """Spotify-client met automatisch verversend token.

    Raises Exception("auth_required") als er geen token is of de scopes
    niet meer kloppen; dan moet de gebruiker opnieuw koppelen via /login.
    """
    auth_manager = make_auth_manager()
    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        raise Exception("auth_required")

    cached_scopes = set((token_info.get("scope") or "").split())
    if not set(SPOTIFY_SCOPE.split()).issubset(cached_scopes):
        logger.warning("Spotify-token mist scopes, opnieuw koppelen nodig")
        clear_token()
        raise Exception("auth_required")

    return spotipy.Spotify(auth_manager=auth_manager)


def ensure_token():
    """Ververs het token nu (één call naar Spotify). Raises bij een ingetrokken token."""
    auth_manager = make_auth_manager()
    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        raise Exception("auth_required")
    # validate_token ververst als het verlopen is en raist SpotifyOauthError
    # met "Refresh token revoked" als Spotify het token heeft ingetrokken.
    auth_manager.validate_token(token_info)


def import_cache_file():
    """Neem het oude bestand .cache eenmalig over in de database."""
    if not os.path.exists(LEGACY_CACHE_PATH):
        return False
    try:
        with open(LEGACY_CACHE_PATH, "r", encoding="utf-8") as f:
            token_info = json.load(f)
        if not token_info.get("refresh_token"):
            return False
        handler = DbCacheHandler()
        if handler.get_cached_token():
            return False  # DB heeft al een token, bestand is verouderd
        handler.save_token_to_cache(token_info)
        os.rename(LEGACY_CACHE_PATH, LEGACY_CACHE_PATH + ".overgenomen")
        logger.info("Spotify-token overgenomen uit .cache naar database")
        return True
    except Exception as e:
        logger.warning("Kon .cache niet overnemen", extra={"error": str(e)})
        return False
