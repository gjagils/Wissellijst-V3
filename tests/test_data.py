import config

HISTORIE = """80s - Queen - Bohemian Rhapsody - spotify:track:AAA
90s - Nirvana - Smells Like Teen Spirit - spotify:track:BBB

dit is geen geldige regel
00s - Artiest - Met - Streepjes - spotify:track:CCC
90s - Nirvana - Smells Like Teen Spirit - spotify:track:BBB
"""


def test_import_historie_dedupe(wissellijst):
    wl_id = wissellijst["id"]
    r = config.import_historie(wl_id, HISTORIE)
    assert r == {"toegevoegd": 3, "dubbel": 1, "ongeldig": 1}
    entries = config.get_historie(wl_id)
    assert len(entries) == 3
    assert entries[2]["artiest"] == "Artiest" and entries[2]["titel"] == "Met - Streepjes"
    assert config.import_historie(wl_id, HISTORIE) == {"toegevoegd": 0, "dubbel": 4, "ongeldig": 1}


def test_import_endpoint(client, wissellijst):
    wl_id = wissellijst["id"]
    r = client.post(f"/api/wissellijsten/{wl_id}/historie/import",
                    json={"tekst": "10s - Adele - Hello - spotify:track:DDD"})
    assert r.status_code == 200 and r.get_json()["toegevoegd"] == 1
    assert client.post(f"/api/wissellijsten/{wl_id}/historie/import", json={"tekst": " "}).status_code == 400
    assert client.post("/api/wissellijsten/nope/historie/import", json={"tekst": "x"}).status_code == 404


def test_wissellijst_crud_en_validatie(client):
    r = client.post("/api/wissellijsten", json={"naam": "", "playlist_id": "x"})
    assert r.status_code == 400
    r = client.post("/api/wissellijsten", json={
        "naam": "Crud", "playlist_id": "pl-crud", "type": "categorie",
        "categorieen": ["90s"], "rotatie_schema": "dagelijks", "rotatie_tijdstip": "08:00",
    })
    wl = r.get_json()
    assert wl["id"] and wl["rotatie_schema"] == "dagelijks"
    ids = [w["id"] for w in client.get("/api/wissellijsten").get_json()]
    assert wl["id"] in ids
    assert client.delete(f"/api/wissellijsten/{wl['id']}").status_code == 200
    assert wl["id"] not in [w["id"] for w in client.get("/api/wissellijsten").get_json()]


def test_wachtrij_en_historie_helpers(wissellijst):
    wl_id = wissellijst["id"]
    config.save_wachtrij(wl_id, [{"categorie": "80s", "artiest": "A", "titel": "T", "uri": "spotify:track:Q1"}])
    assert config.get_wachtrij_uris(wl_id) == {"spotify:track:Q1"}
    config.clear_wachtrij(wl_id)
    assert config.get_wachtrij(wl_id) == []
    config.save_smaakprofiel(wl_id, "=== SMAAKPROFIEL ===\nx")
    assert config.get_smaakprofiel(wl_id).startswith("=== SMAAK")
