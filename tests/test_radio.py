"""NL-radiolijsten: parsen, importeren, NL-score en het keuzemenu voor GPT."""
import pytest

import radio
import suggest
import web
from radio import RadioIndex


@pytest.fixture(autouse=True)
def _leeg(client):
    for rl in client.get("/api/radiolijsten").get_json():
        client.delete(f"/api/radiolijsten/{rl['id']}")


def test_parse_tekst():
    entries = radio.parse_tekst(
        "1. Queen - Bohemian Rhapsody\n"
        "2) Golden Earring – Radar Love (1973)\n"
        "\n"
        "3\tDoe Maar\tSmoorverliefd\n"
        "zomaar een regel\n")
    assert [(e["positie"], e["artiest"], e["titel"], e["jaar"]) for e in entries] == [
        (1, "Queen", "Bohemian Rhapsody", None),
        (2, "Golden Earring", "Radar Love", 1973),
        (3, "Doe Maar", "Smoorverliefd", None),
    ]
    zonder_nummers = radio.parse_tekst("A - x\nB - y")
    assert [e["positie"] for e in zonder_nummers] == [1, 2]


def test_playlist_id_uit():
    assert radio.playlist_id_uit("https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd?si=x") == "37i9dQZF1DX4o1oenSJRJd"
    assert radio.playlist_id_uit("spotify:playlist:37i9dQZF1DX4o1oenSJRJd") == "37i9dQZF1DX4o1oenSJRJd"
    assert radio.playlist_id_uit("onzin met spaties") is None


def test_sleutels_en_score():
    assert radio.titel_sleutel("Radar Love - 2001 Remaster") == "radar love"
    assert radio.titel_sleutel("Smoorverliefd (Live)") == "smoorverliefd"
    assert radio.artiest_sleutel("Nick & Simon feat. Iemand") == "nick simon"
    # 3 plekken: 100, 65, 30
    idx = RadioIndex([("80s", 3, p, f"A{p}", f"T{p}", "", None) for p in (1, 2, 3)])
    assert [idx.score("", f"A{p}", f"T{p}") for p in (1, 2, 3)] == [100, 65, 30]
    assert idx.score("", "Onbekend", "T1") is None


def test_hoogste_score_telt_en_uri_match():
    idx = RadioIndex([
        ("", 2000, 1500, "Golden Earring", "Radar Love", "spotify:track:RL", 1973),
        ("70s", 500, 1, "Golden Earring", "Radar Love - Remastered", "", None),
    ])
    assert idx.score("", "Golden Earring", "Radar Love") == 100
    assert idx.score("spotify:track:RL", "x", "y") == idx.per_uri["spotify:track:RL"]
    # De allertijdenlijst heeft een releasejaar, dus het nummer is ook een 70s-kandidaat
    assert [e["titel"] for e in idx.kandidaten("70s")] in (["Radar Love - Remastered"], ["Radar Love"])


def test_kandidaten_sluiten_uit_en_spreiden():
    rijen = [("90s", 100, p, f"Artiest{p}", f"Titel{p}", f"spotify:track:{p}", None) for p in range(1, 101)]
    idx = RadioIndex(rijen)
    menu = idx.kandidaten("90s", aantal=10, uitsluiten_uris={"spotify:track:1"},
                          uitsluiten_artiesten=["Artiest2"],
                          uitsluiten_tracks=[("Artiest3", "Titel3")])
    assert len(menu) == 10
    titels = {e["titel"] for e in menu}
    assert not titels & {"Titel1", "Titel2", "Titel3"}
    scores = sorted(e["score"] for e in menu)
    assert scores[0] < 45 and scores[-1] > 85          # gespreid over de lijst
    assert idx.kandidaten("60s") == []


def test_import_tekst_en_beheer(client, anon):
    assert anon.get("/api/radiolijsten").status_code == 401
    r = client.post("/api/radiolijsten", json={
        "naam": "Top 500 van de 80s", "zender": "Qmusic", "decennium": "80s",
        "tekst": "1. Doe Maar - Smoorverliefd\n2. Golden Earring - Radar Love"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["aantal"] == 2
    lijsten = client.get("/api/radiolijsten").get_json()
    assert [(rl["naam"], rl["zender"], rl["aantal"]) for rl in lijsten] == [("Top 500 van de 80s", "Qmusic", 2)]

    assert client.post("/api/radiolijsten", json={"naam": "x", "tekst": "a - b", "decennium": "tachtig"}).status_code == 400
    assert client.post("/api/radiolijsten", json={"naam": "x", "tekst": "geen nummers"}).status_code == 400
    # Geplakte lijst kan niet vernieuwd worden vanuit Spotify
    assert client.post(f"/api/radiolijsten/{lijsten[0]['id']}/vernieuwen").status_code == 400
    assert client.delete(f"/api/radiolijsten/{lijsten[0]['id']}").get_json()["ok"]
    assert client.get("/api/radiolijsten").get_json() == []


class FakeSpPlaylist:
    def __init__(self, tracks):
        self.tracks = tracks

    def playlist(self, pid, fields=None):
        return {"name": "Q-top 500 van de 90s | editie 2026"}

    def playlist_items(self, pid, limit=100, fields=None):
        return {"items": [{"track": t} for t in self.tracks[:2]], "next": "volgende" if len(self.tracks) > 2 else None}

    def next(self, results):
        return {"items": [{"track": t} for t in self.tracks[2:]], "next": None}


def _track(i, jaar="1995-01-01"):
    return {"uri": f"spotify:track:{i}", "name": f"Titel{i}", "artists": [{"name": f"Artiest{i}"}],
            "album": {"release_date": jaar}}


def test_import_playlist_en_vernieuwen(client, monkeypatch):
    sp = FakeSpPlaylist([_track(1), _track(2), None, _track(3)])
    monkeypatch.setattr(web, "get_spotify_client", lambda: sp)
    r = client.post("/api/radiolijsten", json={
        "zender": "Qmusic", "decennium": "90s",
        "playlist": "https://open.spotify.com/playlist/0vaHokUs2adOZVTzPAtXa9"})
    assert r.status_code == 200, r.get_json()
    rl = r.get_json()
    assert rl["naam"] == "Q-top 500 van de 90s | editie 2026" and rl["aantal"] == 3
    assert rl["spotify_playlist_id"] == "0vaHokUs2adOZVTzPAtXa9"

    idx = RadioIndex.laad()
    assert idx.score("spotify:track:1", "", "") == 100 and idx.score("spotify:track:3", "", "") == 30

    sp.tracks = [_track(9)]
    r = client.post(f"/api/radiolijsten/{rl['id']}/vernieuwen")
    assert r.status_code == 200 and r.get_json()["aantal"] == 1
    assert RadioIndex.laad().score("spotify:track:1", "", "") is None


def test_import_playlist_spotify_weigert(client, monkeypatch):
    class Weigert:
        def playlist(self, *a, **k):
            raise Exception("http status: 403")
    monkeypatch.setattr(web, "get_spotify_client", lambda: Weigert())
    r = client.post("/api/radiolijsten", json={"playlist": "0vaHokUs2adOZVTzPAtXa9"})
    assert r.status_code == 502 and "als tekst" in r.get_json()["error"]


class FakeSpZoek:
    """Zoeken op Spotify: elk nummer bestaat, Spotify geeft popularity 50."""
    def playlist_items(self, playlist_id):
        return {"items": []}

    def search(self, q, limit, type):
        titel = q.split("track:")[1].split(" artist:")[0]
        return {"tracks": {"items": [{"uri": f"spotify:track:{titel}", "popularity": 50,
                                      "album": {"release_date": "1985-01-01"},
                                      "artists": [{"name": "X"}]}]}}


def test_generate_block_met_radiolijsten(client, wissellijst, monkeypatch):
    client.post("/api/radiolijsten", json={
        "naam": "Top 80s", "zender": "NPO Radio 2", "decennium": "80s",
        "tekst": "1. Hit Artiest - hit\n2. Midden Artiest - midden\n3. Klein Artiest - klein"})
    cats = ["80s a", "80s b"]
    prompts = []

    def fake_ask(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            # 'hit' (NL 100) en 'midden' (NL 65); Spotify zou beide 50 geven (zelfde vak)
            return ["80s a | Hit Artiest | hit", "80s b | Midden Artiest | midden"]
        return []

    monkeypatch.setattr(suggest, "_ask", fake_ask)
    block = suggest.generate_block(FakeSpZoek(), "pl", cats, wissellijst["id"],
                                   populariteit=True, radio=True)
    assert [t["titel"] for t in block] == ["hit", "midden"]   # NL-score gaat voor Spotify
    assert "Keuzemenu uit Nederlandse radiolijsten" in prompts[0]
    assert "Hit Artiest – hit (100)" in prompts[0]


def test_generate_block_radio_zonder_lijsten(wissellijst, monkeypatch):
    prompts = []
    monkeypatch.setattr(suggest, "_ask", lambda p: prompts.append(p) or ["80s a | A | t1", "80s b | B | t2"])
    block = suggest.generate_block(FakeSpZoek(), "pl", ["80s a", "80s b"], wissellijst["id"], radio=True)
    assert len(block) == 2 and "Keuzemenu" not in prompts[0]
