"""Populariteitsverdeling: vakken, fallback zonder popularity, en generate_block."""
import suggest
from populariteit import Verdeling, parse_vakken

VAKKEN = parse_vakken("30-50,50-70,70-80,80-90,90-100")


def test_parse_vakken():
    assert VAKKEN == [(30, 50), (50, 70), (70, 80), (80, 90), (90, 100)]
    assert parse_vakken("90-100, onzin, 50-30, 10-20") == [(10, 20), (90, 100)]


def test_elk_vak_een_keer():
    v = Verdeling(VAKKEN, 5)
    assert v.check(85) == (True, "")
    v.neem(85)
    ok, reden = v.check(88)
    assert not ok and "80-90" in reden
    assert v.check(20)[0] is False          # onder 30: buiten de vakken
    assert v.check(100)[0] is True          # 100 hoort bij 90-100
    assert v.check(50)[0] is True and v.vak(50) == (50, 70)
    assert (80, 90) not in v.open_vakken()
    assert "90-100 (wereldhit)" in v.prompt_hint()


def test_fallback_zonder_popularity():
    v = Verdeling(VAKKEN, 5)
    assert v.check(None) == (True, "")
    assert v.uit and v.prompt_hint() == ""
    assert v.check(10) == (True, "")        # daarna geen filter meer


class FakeSp:
    def __init__(self, pops):
        self.pops = pops

    def playlist_items(self, playlist_id):
        return {"items": []}

    def search(self, q, limit, type):
        titel = q.split("track:")[1].split(" artist:")[0] if "track:" in q else q.split()[-1]
        return {"tracks": {"items": [{
            "uri": f"spotify:track:{titel}", "album": {"release_date": "1985-01-01"},
            "popularity": self.pops.get(titel), "artists": [{"name": "X"}]}]}}


def _gpt(regels):
    return lambda *a, **k: list(regels)


def test_generate_block_spreidt_populariteit(monkeypatch, wissellijst):
    cats = ["80s a", "80s b", "80s c", "80s d", "80s e"]
    # Eerste ronde: twee nummers in hetzelfde vak (85, 86) -> de tweede wordt afgewezen
    pops = {"t1": 35, "t2": 55, "t3": 75, "t4": 85, "t5": 86, "t6": 95}
    eerste = [f"{c} | Artiest{i} | t{i}" for i, c in enumerate(cats, start=1)]
    tweede = ["80s e | Artiest6 | t6"]
    antwoorden = iter([eerste, tweede])
    hints = []

    def fake_ask(prompt):
        hints.append(prompt)
        return next(antwoorden, [])

    monkeypatch.setattr(suggest, "_ask", fake_ask)
    block = suggest.generate_block(FakeSp(pops), "pl", cats, wissellijst["id"], populariteit=True)
    assert [t["uri"] for t in block] == [f"spotify:track:t{i}" for i in (1, 2, 3, 4, 6)]
    nog_nodig = hints[1].split("Nog nodig")[1].splitlines()[0]
    assert "90-100" in nog_nodig and "80-90" not in nog_nodig   # re-ask vraagt alleen het open vak


def test_generate_block_zonder_popularity_valt_terug(monkeypatch, wissellijst):
    cats = ["80s a", "80s b"]
    monkeypatch.setattr(suggest, "_ask", _gpt(["80s a | A | t1", "80s b | B | t2"]))
    block = suggest.generate_block(FakeSp({}), "pl", cats, wissellijst["id"], populariteit=True)
    assert len(block) == 2


def test_veld_opslaan(client, wissellijst):
    r = client.post("/api/wissellijsten", json={**wissellijst, "populariteit_verdeling": True})
    assert r.status_code == 200 and r.get_json()["populariteit_verdeling"] is True

