"""Token in de database, de OAuth-callback met state, en de overname van .cache."""
import json
import os
import time

import requests

import spotify_auth
from config import SPOTIFY_SCOPE


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text, self.reason = json.dumps(body), "x"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


def _token(refresh="r1", expired=False):
    return {"access_token": "a", "refresh_token": refresh, "scope": SPOTIFY_SCOPE,
            "expires_at": int(time.time()) + (-100 if expired else 3600),
            "expires_in": 3600, "token_type": "Bearer"}


def test_db_cache_handler_roundtrip():
    h = spotify_auth.DbCacheHandler()
    spotify_auth.clear_token()
    assert h.get_cached_token() is None
    h.save_token_to_cache(_token("r1"))
    assert h.get_cached_token()["refresh_token"] == "r1"
    h.save_token_to_cache(_token("r2"))
    assert h.get_cached_token()["refresh_token"] == "r2"
    assert spotify_auth.token_status()["present"] is True


def test_get_client_eist_scopes():
    h = spotify_auth.DbCacheHandler()
    h.save_token_to_cache({**_token(), "scope": "playlist-read-private"})
    try:
        spotify_auth.get_spotify_client()
        assert False, "verwachtte auth_required"
    except Exception as e:
        assert "auth_required" in str(e)
    assert spotify_auth.token_status()["present"] is False  # token is gewist


def test_import_cache_file():
    spotify_auth.clear_token()
    with open(spotify_auth.LEGACY_CACHE_PATH, "w") as f:
        json.dump(_token("uit-bestand"), f)
    assert spotify_auth.import_cache_file() is True
    assert spotify_auth.DbCacheHandler().get_cached_token()["refresh_token"] == "uit-bestand"
    assert not os.path.exists(spotify_auth.LEGACY_CACHE_PATH)
    assert os.path.exists(spotify_auth.LEGACY_CACHE_PATH + ".overgenomen")
    os.remove(spotify_auth.LEGACY_CACHE_PATH + ".overgenomen")


def test_callback_met_ingetrokken_token_in_db(client, monkeypatch):
    """Oud token in de DB is ingetrokken; de callback moet de nieuwe code tóch inwisselen."""
    spotify_auth.DbCacheHandler().save_token_to_cache(_token("revoked", expired=True))
    posted = []

    def fake_post(url, data=None, headers=None, **kw):
        posted.append(data["grant_type"])
        if data["grant_type"] == "refresh_token":
            return FakeResp(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})
        return FakeResp(200, {"access_token": "new", "refresh_token": "new-r",
                              "scope": SPOTIFY_SCOPE, "expires_in": 3600, "token_type": "Bearer"})

    real_make = spotify_auth.make_auth_manager

    def patched_make(state=None):
        am = real_make(state=state)
        am._session.post = fake_post
        return am

    monkeypatch.setattr(spotify_auth, "make_auth_manager", patched_make)

    class FakeClient:
        def current_user(self):
            return {"display_name": "Tester", "id": "tester"}

    import web
    monkeypatch.setattr(web, "get_spotify_client", lambda: FakeClient())

    # Zonder geldige state: geweigerd
    r = client.get("/callback?code=CODE&state=nep")
    assert r.status_code == 400 and b"state" in r.data

    # Met state uit /login: code ingewisseld, token in DB, gebruiker opgeslagen
    r = client.get("/login")
    assert r.status_code == 302 and "state=" in r.headers["Location"]
    with client.session_transaction() as s:
        state = s["spotify_state"]
    r = client.get(f"/callback?code=CODE&state={state}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert posted == ["authorization_code"]
    st = spotify_auth.token_status()
    assert st["present"] and st["user"] == "Tester"
    assert spotify_auth.DbCacheHandler().get_cached_token()["refresh_token"] == "new-r"


def test_callback_toont_fout(client, monkeypatch):
    class Boom:
        def get_access_token(self, *a, **k):
            raise Exception("error: invalid_client, error_description: Invalid client secret")

    monkeypatch.setattr(spotify_auth, "make_auth_manager", lambda state=None: Boom())
    client.get("/login")
    with client.session_transaction() as s:
        state = s["spotify_state"]
    r = client.get(f"/callback?code=CODE&state={state}")
    assert r.status_code == 500 and b"Invalid client secret" in r.data and b"/login" in r.data
