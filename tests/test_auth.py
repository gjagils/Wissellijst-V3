def test_zonder_login_is_alles_dicht_behalve_health(anon):
    r = anon.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")
    r = anon.get("/api/wissellijsten")
    assert r.status_code == 401 and r.get_json()["error"] == "login_required"
    assert anon.get("/api/status").status_code == 401
    assert anon.get("/health").status_code == 200


def test_fout_wachtwoord(anon):
    r = anon.post("/auth/login", data={"wachtwoord": "fout"})
    assert r.status_code == 401 and b"Onjuist wachtwoord" in r.data


def test_login_en_logout(client):
    assert client.get("/api/wissellijsten").status_code == 200
    r = client.get("/")
    assert r.status_code == 200 and b"status-banner" in r.data
    client.get("/auth/logout")
    assert client.get("/api/wissellijsten").status_code == 401


def test_status_endpoint(client, monkeypatch):
    import alerts
    monkeypatch.setattr(alerts, "spotify_status",
                        lambda max_age=60: {"connected": False, "user": None, "error": "auth_required"})
    s = client.get("/api/status").get_json()
    assert s["auth_enabled"] is True
    assert s["spotify"]["connected"] is False
    assert s["spotify"]["token"]["present"] in (True, False)
    assert "version" in s
