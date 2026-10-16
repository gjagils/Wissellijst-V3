"""Jaar van de originele uitgave: zoeken, doorgeven en tonen; en geen blokken van 4."""
import automation
import config
import suggest
import scheduler
import spotify_auth


def _track(uri, naam, artiest, datum, pop):
    return {"uri": uri, "name": naam, "artists": [{"name": artiest}],
            "album": {"release_date": datum}, "popularity": pop}


class ZoekSp:
    def __init__(self, items):
        self.items = items

    def search(self, q, limit, type):
        return {"tracks": {"items": self.items}}


def test_bekendste_versie_en_vroegste_jaar():
    sp = ZoekSp([
        _track("spotify:track:REMASTER", "You Oughta Know - 2015 Remaster", "Alanis Morissette", "2021-03-05", 80),
        _track("spotify:track:ORIGINEEL", "You Oughta Know", "Alanis Morissette", "1995-06-13", 70),
        _track("spotify:track:ANDER", "You Oughta Know", "Iemand Anders", "1990-01-01", 99),
    ])
    r = suggest.search_spotify(sp, "Alanis Morissette", "You Oughta Know")
    assert r["uri"] == "spotify:track:REMASTER"     # bekendste versie
    assert r["jaar"] == 1995 and r["release_date"] == "1995-06-13"   # vroegste uitgave
    assert r["popularity"] == 80


def test_remaster_valt_niet_meer_in_verkeerd_decennium(wissellijst, monkeypatch):
    sp = ZoekSp([
        _track("spotify:track:R", "You Oughta Know - 2021 Remaster", "Alanis Morissette", "2021-01-01", 80),
        _track("spotify:track:O", "You Oughta Know", "Alanis Morissette", "1995-06-13", 70),
    ])
    sp.playlist_items = lambda pid: {"items": []}
    monkeypatch.setattr(suggest, "_ask", lambda p: ["20s | Alanis Morissette | You Oughta Know"])
    assert suggest.generate_block(sp, "pl", ["20s"], wissellijst["id"]) is None


class FakeSp:
    def __init__(self, pops):
        self.pops = pops

    def playlist_items(self, playlist_id):
        return {"items": []}

    def search(self, q, limit, type):
        titel = q.split("track:")[1].split(" artist:")[0]
        return {"tracks": {"items": [_track(f"spotify:track:{titel}", titel, "X", "1985-01-01",
                                            self.pops[titel])]}}


def test_laatste_ronde_zonder_vakken(wissellijst, monkeypatch):
    cats = ["80s a", "80s b"]
    # Beide nummers in hetzelfde vak: de tweede wordt telkens afgewezen, tot de laatste ronde
    antwoorden = iter([["80s a | A | t1", "80s b | B | t2"], [], [], ["80s b | C | t3"]])
    monkeypatch.setattr(suggest, "_ask", lambda p: next(antwoorden, []))
    block = suggest.generate_block(FakeSp({"t1": 85, "t2": 86, "t3": 87}), "pl", cats,
                                   wissellijst["id"], populariteit=True)
    assert [t["titel"] for t in block] == ["t1", "t3"]
    assert block[0]["jaar"] == 1985


def test_jaar_door_wachtrij_rotatie_en_log(client, wissellijst, monkeypatch):
    wl_id = wissellijst["id"]

    class RotSp:
        def playlist_items(self, playlist_id, limit=100):
            return {"items": [{"track": _track("spotify:track:OUD", "Oud", "Oude", "2011-01-01", 50)}]}

        def playlist_remove_all_occurrences_of_items(self, pl, uris):
            pass

        def playlist_add_items(self, pl, uris):
            pass

    config.add_historie_bulk(wl_id, [{"categorie": "80s", "artiest": "Oude", "titel": "Oud",
                                      "uri": "spotify:track:OUD", "jaar": 1984}])
    config.save_wachtrij(wl_id, [{"categorie": "80s", "artiest": "Nieuw", "titel": "Erin",
                                  "uri": "spotify:track:NIEUW", "jaar": 1986}])
    assert config.get_wachtrij(wl_id)[0]["jaar"] == 1986

    monkeypatch.setattr(automation, "get_spotify_client", lambda: RotSp())
    monkeypatch.setattr(spotify_auth, "ensure_token", lambda: None)
    monkeypatch.setattr(automation, "rotate_and_regenerate",
                        lambda wl: automation.rotate_playlist(wl["playlist_id"], wl["id"]))
    scheduler._execute_rotation(wl_id, triggered_by="user")

    run = client.get(f"/api/wissellijsten/{wl_id}/rotaties").get_json()[0]
    jaren = {(w["type"], w["titel"]): w["jaar"] for w in run["wijzigingen"]}
    assert jaren == {("toegevoegd", "Erin"): 1986, ("verwijderd", "Oud"): 1984}   # niet 2011
    html = client.get(f"/rotaties/{run['id']}/post").get_data(as_text=True)
    assert "Oude – Oud (1984)" in html and "Nieuw – Erin (1986)" in html
