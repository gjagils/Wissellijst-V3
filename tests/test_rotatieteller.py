"""Rotatieteller: alleen een geslaagde rotatie telt op; herstarten zet hem op 0."""
import automation
import scheduler
import spotify_auth
import web


class LegeSp:
    def playlist_items(self, playlist_id, fields=None, limit=100):
        return {"items": [], "next": None}


def _roteer(wl_id, monkeypatch, status="ok"):
    monkeypatch.setattr(automation, "rotate_and_regenerate", lambda wl: {
        "status": status, "tekst": "ok" if status == "ok" else "mislukt",
        "verwijderd": 0, "toegevoegd": 0})
    try:
        scheduler._execute_rotation(wl_id, triggered_by="user")
    except Exception:
        pass


def _nummers(client, wl_id):
    """Rotatienummers van nieuw naar oud (None voor mislukte runs)."""
    return [r["rotatienummer"] for r in client.get(f"/api/wissellijsten/{wl_id}/rotaties").get_json()]


def test_teller_en_herstarten(client, wissellijst, monkeypatch):
    import alerts
    monkeypatch.setattr(spotify_auth, "ensure_token", lambda: None)
    monkeypatch.setattr(alerts, "notify_failure", lambda *a, **k: None)
    wl_id = wissellijst["id"]

    _roteer(wl_id, monkeypatch)
    _roteer(wl_id, monkeypatch, status="fout")       # mislukt: telt niet
    _roteer(wl_id, monkeypatch)
    assert _nummers(client, wl_id) == [2, None, 1]

    monkeypatch.setattr(web, "get_spotify_client", lambda: LegeSp())
    r = client.post(f"/api/wissellijsten/{wl_id}/herstarten")
    assert r.status_code == 200 and "rotatieteller op 0" in r.get_json()["tekst"]
    assert _nummers(client, wl_id) == [None, None, None]   # oude runs blijven in de audit trail

    _roteer(wl_id, monkeypatch)
    assert _nummers(client, wl_id)[0] == 1              # eerste rotatie na herstart
    assert len(_nummers(client, wl_id)) == 4
