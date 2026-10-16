"""Rotatie-logica met gemockte Spotify en GPT: lege wachtrij, audit trail, alarm."""
import pytest

import alerts
import automation
import config
import mail
import scheduler
import spotify_auth
import suggest


class FakeSp:
    def tracks(self, uris):
        return {"tracks": [None for _ in uris]}


@pytest.fixture
def rotatie_mocks(monkeypatch):
    calls = {"generate": 0, "rotate": 0}
    monkeypatch.setattr(automation, "get_spotify_client", lambda: FakeSp())
    monkeypatch.setattr(spotify_auth, "ensure_token", lambda: None)
    monkeypatch.setattr(alerts, "_alert_adres", lambda: "alarm@example.org")
    monkeypatch.setattr(mail, "mail_configured", lambda: True)
    sent = []
    monkeypatch.setattr(mail, "_send", lambda msg, to: sent.append((to, msg["Subject"])))

    def fake_generate_block(sp, playlist_id, categorieen, wl_id, max_per_artiest=0,
                            al_gekozen=None, populariteit=False):
        calls["generate"] += 1
        calls.setdefault("al_gekozen", []).append(len(al_gekozen or []))
        return [{"categorie": "80s", "artiest": "A", "titel": "T",
                 "uri": f"spotify:track:G{calls['generate']}"}]

    def fake_rotate_playlist(playlist_id, wl_id, sort_by_age=False):
        calls["rotate"] += 1
        q = config.get_wachtrij(wl_id)
        assert q, "rotate_playlist aangeroepen met lege wachtrij"
        config.clear_wachtrij(wl_id)
        return {"status": "ok", "tekst": f"{len(q)} nummers geroteerd.", "verwijderd": len(q),
                "toegevoegd": len(q),
                "verwijderd_detail": [{"artiest": "Oud", "titel": "Weg"}],
                "toegevoegd_detail": [{"artiest": e["artiest"], "titel": e["titel"]} for e in q]}

    monkeypatch.setattr(suggest, "generate_block", fake_generate_block)
    monkeypatch.setattr(automation, "rotate_playlist", fake_rotate_playlist)
    alerts.clear_failures()
    return calls, sent


def test_lege_wachtrij_genereert_eerst(wissellijst, rotatie_mocks):
    calls, _ = rotatie_mocks
    wl = config.get_wissellijst(wissellijst["id"])
    assert config.get_wachtrij(wl["id"]) == []
    result = automation.rotate_and_regenerate(wl)
    assert result["status"] == "ok" and result["nieuw_blok"] is True
    assert (calls["generate"], calls["rotate"]) == (2, 1)   # 1x vooraf, 1x nieuw blok
    assert len(config.get_wachtrij(wl["id"])) == 1


def test_meerdere_blokken_per_rotatie(client, wissellijst, rotatie_mocks):
    calls, _ = rotatie_mocks
    wl_id = wissellijst["id"]
    r = client.post("/api/wissellijsten", json={**wissellijst, "blokken_per_rotatie": 2})
    assert r.status_code == 200, r.get_json()
    wl = config.get_wissellijst(wl_id)
    assert wl["blokken_per_rotatie"] == 2

    result = automation.rotate_and_regenerate(wl)
    # Lege wachtrij: 2 blokken vooraf (roteert 2 nummers), daarna weer 2 blokken klaar
    assert result["status"] == "ok" and result["toegevoegd"] == 2
    assert calls["generate"] == 4
    assert calls["al_gekozen"] == [0, 1, 0, 1]   # tweede blok kent het eerste
    assert len(config.get_wachtrij(wl_id)) == 2


def test_blokken_per_rotatie_validatie(client, wissellijst):
    wl_id = wissellijst["id"]
    for waarde in (0, 3):   # aantal_blokken is 2
        r = client.post("/api/wissellijsten", json={**wissellijst, "blokken_per_rotatie": waarde})
        assert r.status_code == 400


def test_lege_wachtrij_en_generatie_mislukt(wissellijst, rotatie_mocks, monkeypatch):
    calls, _ = rotatie_mocks
    monkeypatch.setattr(suggest, "generate_block", lambda *a, **k: None)
    result = automation.rotate_and_regenerate(config.get_wissellijst(wissellijst["id"]))
    assert result["status"] == "fout" and calls["rotate"] == 0


def test_execute_rotation_legt_run_vast_en_mailt_bij_fout(client, wissellijst, rotatie_mocks, monkeypatch):
    calls, sent = rotatie_mocks
    wl_id = wissellijst["id"]

    # Geslaagde handmatige rotatie: run 'voltooid', triggered_by 'user', wijzigingen opgeslagen
    result = scheduler._execute_rotation(wl_id, triggered_by="user")
    assert result["status"] == "ok"
    runs = client.get(f"/api/wissellijsten/{wl_id}/rotaties").get_json()
    assert runs[0]["status"] == "voltooid" and runs[0]["triggered_by"] == "user"
    assert runs[0]["tracks_toegevoegd"] == 1
    assert {w["type"] for w in runs[0]["wijzigingen"]} == {"verwijderd", "toegevoegd"}
    assert config.get_wissellijst(wl_id)["laatste_rotatie"]

    # Ingetrokken token: run 'mislukt', één alarm-mail (spotify_auth), exception naar aanroeper
    def revoked():
        raise Exception("error: invalid_grant, error_description: Refresh token revoked")
    monkeypatch.setattr(spotify_auth, "ensure_token", revoked)
    with pytest.raises(Exception):
        scheduler._execute_rotation(wl_id)
    with pytest.raises(Exception):
        scheduler._execute_rotation(wl_id)
    runs = client.get(f"/api/wissellijsten/{wl_id}/rotaties").get_json()
    assert runs[0]["status"] == "mislukt" and "revoked" in runs[0]["error_message"]
    assert len(sent) == 1 and "spotify_auth" in sent[0][1]
    assert client.get("/api/status").get_json()["laatste_fout"]["soort"] == "spotify_auth"


def test_handmatige_rotatie_via_api(client, wissellijst, rotatie_mocks):
    import time
    wl_id = wissellijst["id"]
    task_id = client.post(f"/api/wissellijsten/{wl_id}/roteren").get_json()["task_id"]
    for _ in range(50):
        t = client.get(f"/api/vullen/{task_id}").get_json()
        if t["status"] != "bezig":
            break
        time.sleep(0.05)
    assert t["status"] == "klaar", t
    assert "geroteerd" in t["tekst"]
    assert client.get(f"/api/wissellijsten/{wl_id}/rotaties").get_json()[0]["triggered_by"] == "user"


def test_alarm_classificatie():
    assert alerts.classify_error("error: invalid_grant, error_description: Refresh token revoked") == "spotify_auth"
    assert alerts.classify_error(Exception("OpenAI suggesties mislukt: incorrect API key")) == "openai"
    assert alerts.classify_error("Wachtrij was leeg en nieuw blok genereren mislukt.") == "overig"
