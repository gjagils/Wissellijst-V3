"""Instagram-plaatjes volgens de stijlgids: regels, render en de koppeling aan rotaties."""
import base64
import datetime
import io
import time

from PIL import Image

import instagram as ig
import scheduler
from db.models import RotatieRun, RotatieWijziging, RotatieAfbeelding
from db.session import get_session

DATUM = datetime.date(2026, 10, 16)
TRACKS = [("20s", "Beautiful Things", "Benson Boone"), ("80s", "Take On Me", "a-ha"),
          ("90s", "Wonderwall", "Oasis"), ("00s", "Back to Black", "Amy Winehouse"),
          ("10s", "Blinding Lights", "The Weeknd")]


def _tracks():
    return [{"decade": d, "title": t, "artist": a, "year": 1980 + 10 * ig.DECADE_ORDER.index(d) + 5,
             "artwork_url": ""} for d, t, a in TRACKS]


def test_asset_volgorde_highlight_en_device():
    a_in = ig.maak_asset(_tracks(), 27, DATUM, "IN")
    a_out = ig.maak_asset(_tracks(), 27, DATUM, "OUT")
    assert [t["decade"] for t in a_in["tracks"]] == ig.DECADE_ORDER        # altijd 80s -> 20s
    assert ig.valideer(a_in) == [] and ig.valideer(a_out) == []
    assert a_in["highlight_decade"] == "00s" and a_out["highlight_decade"] == "20s"   # 27%5, (27+2)%5
    assert a_in["device"] == "featurephone_headset"                          # 27 % 2 = variant B
    assert ig.maak_asset(_tracks(), 28, DATUM, "IN")["device"] == "smartphone_streaming"
    assert sum(t["highlighted"] for t in a_in["tracks"]) == 1
    assert ig.bestandsnaam(a_in) == "wissellijst-r027-2026-10-16-in-square.png"
    assert "Nu op de Wissellijst:\nBack to Black — Amy Winehouse (2005)" in ig.caption(a_in)
    assert "📼 80’s — Take On Me · a-ha (1985)" in ig.caption(a_in)
    assert ig.artiest_regel(a_in["tracks"][0]) == "a-ha · 1985"
    assert "Uitgelicht is Back to Black van Amy Winehouse" in ig.alt_tekst(a_in)
    # Eruit: geen "nu op", maar "helaas van"
    assert ig.sticker_tekst(a_in) == "NU OP DE WISSELLIJST"
    assert ig.sticker_tekst(a_out) == "HELAAS VAN DE WISSELLIJST"
    assert "💔 Helaas van de Wissellijst:" in ig.caption(a_out)
    assert "Nu op de Wissellijst" not in ig.caption(a_out)


def test_validatie_en_groeperen():
    vier = ig.maak_asset(_tracks()[:4], 1, DATUM, "IN")
    assert any("4 tracks" in f for f in ig.valideer(vier))
    dubbel = _tracks() + [{"decade": "80s", "title": "Smoorverliefd", "artist": "Doe Maar"}]
    groepen = ig.groepeer(dubbel + [{"decade": "70s", "title": "x", "artist": "y"}])
    assert [len(g) for g in groepen] == [5, 1]     # 70s valt af, tweede 80s opent groep 2
    assert ig.decade_van("80s heeft in de Top 2000 gestaan") == "80s"
    assert ig.decade_van("cabarock") is None


def test_schone_titel():
    assert ig.schone_titel("Take On Me - 2015 Remaster") == "Take On Me"
    assert ig.schone_titel("Radar Love (Remastered 2001)") == "Radar Love"
    assert ig.schone_titel("Live and Let Die") == "Live and Let Die"


def test_render_zonder_ai(monkeypatch):
    png = ig.render(ig.maak_asset(_tracks(), 3, DATUM, "OUT"), None)
    assert Image.open(io.BytesIO(png)).size == (1080, 1080)


def test_ai_achtergrond(monkeypatch):
    asset = ig.maak_asset(_tracks(), 3, DATUM, "IN")
    prompt = ig.ai_prompt(asset)
    assert "Do not render any text" in prompt and "discman" not in prompt
    assert "highlighted-track information" not in prompt   # geen aparte kolom meer
    assert "Main audio device: modern over-ear headphones" in prompt   # 3%5 = 10s, 3%2 = variant B

    monkeypatch.setattr(ig, "INSTAGRAM_AI_ILLUSTRATIE", False)
    assert ig.ai_achtergrond(asset) is None

    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), "red").save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    class FakeImages:
        def generate(self, **kw):
            assert kw["size"] == "1024x1024"
            return type("R", (), {"data": [type("D", (), {"b64_json": b64})()]})()

    class FakeOpenAI:
        def __init__(self, api_key):
            self.images = FakeImages()

    import openai
    monkeypatch.setattr(ig, "INSTAGRAM_AI_ILLUSTRATIE", True)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    achtergrond = ig.ai_achtergrond(asset)
    assert achtergrond.size == (1024, 1024)
    assert Image.open(io.BytesIO(ig.render(asset, achtergrond))).size == (1080, 1080)


def _run_met_wijzigingen(wl_id):
    with get_session() as s:
        run = RotatieRun(wissellijst_id=wl_id, triggered_by="scheduler", status="voltooid",
                         started_at=datetime.datetime(2026, 10, 16, 6, 0),
                         completed_at=datetime.datetime(2026, 10, 16, 6, 1))
        s.add(run)
        s.flush()
        for d, t, a in TRACKS:
            s.add(RotatieWijziging(run_id=run.id, type="toegevoegd", artiest=a, titel=t,
                                   categorie=f"{d} met een omschrijving", uri=f"spotify:track:{d}"))
            s.add(RotatieWijziging(run_id=run.id, type="verwijderd", artiest=a + " oud",
                                   titel=t + " - 2011 Remaster", categorie=d))
        return run.id


def test_maak_voor_run_en_routes(client, wissellijst, monkeypatch):
    monkeypatch.setattr(ig, "INSTAGRAM_RICHTINGEN", ("IN", "OUT"))
    monkeypatch.setattr(ig, "INSTAGRAM_AI_ILLUSTRATIE", False)
    monkeypatch.setattr(ig, "_hoes_urls", lambda uris: {})
    run_id = _run_met_wijzigingen(wissellijst["id"])

    assert ig.maak_voor_run(run_id) == 2
    with get_session() as s:
        plaatjes = s.query(RotatieAfbeelding).filter_by(run_id=run_id).all()
        assert sorted(p.richting for p in plaatjes) == ["in", "out"]
        uit = next(p for p in plaatjes if p.richting == "out")
        assert "Take On Me · a-ha oud" in uit.caption and "Remaster" not in uit.caption
        pid = plaatjes[0].id

    r = client.get(f"/rotaties/{run_id}/plaatje/{pid}.png?download=1")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert "attachment" in r.headers["Content-Disposition"]
    assert client.get(f"/rotaties/{run_id + 1}/plaatje/{pid}.png").status_code == 404
    html = client.get(f"/rotaties/{run_id}/post").get_data(as_text=True)
    assert f"/rotaties/{run_id}/plaatje/{pid}.png" in html and "Opnieuw maken" in html

    # Opnieuw maken via de API vervangt de plaatjes
    task_id = client.post(f"/api/rotaties/{run_id}/plaatjes").get_json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/vullen/{task_id}").get_json()
        if t["status"] != "bezig":
            break
        time.sleep(0.05)
    assert t["status"] == "klaar", t
    with get_session() as s:
        assert s.query(RotatieAfbeelding).filter_by(run_id=run_id).count() == 2


def test_rotatie_start_plaatjes_alleen_als_aan(client, wissellijst, monkeypatch):
    import automation
    import spotify_auth
    gestart = []
    monkeypatch.setattr(scheduler, "_plaatjes_op_achtergrond", lambda run_id, mail=None: gestart.append(run_id))
    monkeypatch.setattr(spotify_auth, "ensure_token", lambda: None)
    monkeypatch.setattr(automation, "rotate_and_regenerate", lambda wl: {
        "status": "ok", "tekst": "ok", "verwijderd": 0, "toegevoegd": 0})

    scheduler._execute_rotation(wissellijst["id"])
    assert gestart == []

    client.post("/api/wissellijsten", json={**wissellijst, "instagram_plaatjes": True})
    scheduler._execute_rotation(wissellijst["id"])
    assert len(gestart) == 1


def test_geen_plaatje_geeft_reden(wissellijst, monkeypatch):
    import pytest
    monkeypatch.setattr(ig, "INSTAGRAM_RICHTINGEN", ("IN", "OUT"))
    monkeypatch.setattr(ig, "_hoes_urls", lambda uris: {})
    with get_session() as s:
        run = RotatieRun(wissellijst_id=wissellijst["id"], status="voltooid",
                         started_at=datetime.datetime(2026, 10, 16, 6, 0))
        s.add(run)
        s.flush()
        s.add(RotatieWijziging(run_id=run.id, type="toegevoegd", artiest="A", titel="T", categorie=""))
        s.add(RotatieWijziging(run_id=run.id, type="verwijderd", artiest="B", titel="U", categorie="80s"))
        run_id = run.id
    with pytest.raises(ValueError) as e:
        ig.maak_voor_run(run_id)
    assert "IN: geen nummers met een decennium" in str(e.value)
    assert "OUT: 1 tracks in plaats van 5" in str(e.value)


def test_standaard_alleen_in(wissellijst, monkeypatch):
    """Alleen het IN-plaatje wordt gepost; OUT wordt standaard niet meer gemaakt."""
    assert ig.INSTAGRAM_RICHTINGEN == ("IN",)
    monkeypatch.setattr(ig, "_hoes_urls", lambda uris: {})
    run_id = _run_met_wijzigingen(wissellijst["id"])
    assert ig.maak_voor_run(run_id) == 1
    assert [p["richting"] for p in ig.plaatjes_voor_run(run_id)] == ["in"]
