"""Testopzet: SQLite in een tijdelijke map, wachtwoord aan, geen echte Spotify/OpenAI.

De omgeving moet staan vóór de app-modules importeren (ze lezen env bij import).
"""
import os
import sys
import tempfile
import warnings

import pytest

warnings.simplefilter("ignore", DeprecationWarning)

_TMP = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
    "DATA_DIR": _TMP,
    "OPENAI_API_KEY": "dummy",
    "SPOTIFY_CLIENT_ID": "id",
    "SPOTIFY_CLIENT_SECRET": "secret",
    "SPOTIFY_REDIRECT_URI": "http://localhost/callback",
    "APP_PASSWORD": "geheim123",
    "SESSION_COOKIE_SECURE": "0",
    "ALERT_MAIL": "alarm@example.org",
    # Geen echte beeld-aanroepen; tests die het nodig hebben zetten het zelf aan met een fake.
    "INSTAGRAM_AI_ILLUSTRATIE": "0",
    "INSTAGRAM_AI_AFWERKING": "0",
})
os.environ.pop("TZ", None)
os.environ.pop("SCHEDULER_TZ", None)

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP_DIR)

import web  # noqa: E402  (start DB, migraties en scheduler)


@pytest.fixture(scope="session", autouse=True)
def _shutdown_scheduler():
    yield
    if web._wl_scheduler:
        web._wl_scheduler.shutdown()


@pytest.fixture
def anon():
    """Test client zonder login."""
    return web.app.test_client()


@pytest.fixture
def client():
    """Ingelogde test client."""
    c = web.app.test_client()
    r = c.post("/auth/login", data={"wachtwoord": "geheim123"})
    assert r.status_code == 302
    return c


@pytest.fixture
def wissellijst(client):
    """Maak een categorie-wissellijst aan; returns de dict."""
    r = client.post("/api/wissellijsten", json={
        "naam": "Test", "playlist_id": "pl-test", "type": "categorie",
        "categorieen": ["80s", "90s"], "aantal_blokken": 2, "blok_grootte": 2,
        "rotatie_schema": "uit",
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()
