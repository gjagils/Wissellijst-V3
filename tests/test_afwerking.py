"""AI-afwerking van de plaatjes: tekstcontrole, pogingen en terugval op het strakke plaatje."""
import base64
import datetime
import io
import json

from PIL import Image

import instagram as ig
from db.models import RotatieAfbeelding
from db.session import get_session
from test_instagram import _run_met_wijzigingen, _tracks

DATUM = datetime.date(2026, 10, 16)


def _asset(richting="IN"):
    return ig.maak_asset(_tracks(), 27, DATUM, richting)


def _goed_gelezen(asset):
    return {
        "tracks": [{"decennium": ig.label(t["decade"]).upper(), "titel": t["title"],
                    "artiest_regel": ig.artiest_regel(t)} for t in asset["tracks"]],
        "rotatie": "ROTATIE #27", "datum": "16 OKT 2026", "sticker": ig.sticker_tekst(asset),
        "banner": "5 IN" if asset["direction"] == "IN" else "5 OUT",
        "overige_tekst": ["Wissellijst", "Door de tijd heen"],
    }


def test_vergelijk_tekst_goed():
    asset = _asset()
    assert ig.vergelijk_tekst(asset, _goed_gelezen(asset)) == []


def test_vergelijk_tekst_vindt_fouten():
    asset = _asset("OUT")
    gelezen = _goed_gelezen(asset)
    gelezen["tracks"][1], gelezen["tracks"][2] = gelezen["tracks"][2], gelezen["tracks"][1]   # 00s voor 90s
    gelezen["tracks"][0]["titel"] = "Take On Mee"
    gelezen["sticker"] = "NU OP DE WISSELLIJST"
    gelezen["overige_tekst"] = ["SONY", "WALKMAN"]
    fouten = " | ".join(ig.vergelijk_tekst(asset, gelezen))
    assert "op de plek van 90’s" in fouten
    assert "Take On Mee" in fouten
    assert "sticker" in fouten
    assert "SONY" in fouten


def test_vergelijk_tekst_te_weinig_kaarten():
    asset = _asset()
    gelezen = _goed_gelezen(asset)
    gelezen["tracks"] = gelezen["tracks"][:4]
    assert any("4 trackkaarten" in f for f in ig.vergelijk_tekst(asset, gelezen))


def test_pogingen_en_terugval(monkeypatch):
    monkeypatch.setattr(ig, "INSTAGRAM_AI_AFWERKING", True)
    monkeypatch.setattr(ig, "ai_afwerking", lambda asset, png: b"ai-png")
    uitslagen = iter([["titel fout"], []])
    monkeypatch.setattr(ig, "controleer_tekst", lambda asset, png: next(uitslagen))
    png, opmerking = ig.afgewerkt_plaatje(_asset(), b"strak")
    assert png == b"ai-png" and "poging 2" in opmerking

    monkeypatch.setattr(ig, "controleer_tekst", lambda asset, png: ["titel 'X' in plaats van 'Y'"])
    png, opmerking = ig.afgewerkt_plaatje(_asset(), b"strak")
    assert png is None and opmerking.startswith("AI-afwerking afgekeurd: titel")

    def kapot(asset, png):
        raise RuntimeError("rate limit")
    monkeypatch.setattr(ig, "ai_afwerking", kapot)
    png, opmerking = ig.afgewerkt_plaatje(_asset(), b"strak")
    assert png is None and "rate limit" in opmerking

    monkeypatch.setattr(ig, "INSTAGRAM_AI_AFWERKING", False)
    assert ig.afgewerkt_plaatje(_asset(), b"strak") == (None, "AI-afwerking staat uit")


def _png(kleur="red", grootte=1024):
    buf = io.BytesIO()
    Image.new("RGB", (grootte, grootte), kleur).save(buf, "PNG")
    return buf.getvalue()


class FakeOpenAI:
    aanroepen = []

    def __init__(self, api_key, timeout=None):
        self.images = self
        self.chat = type("Chat", (), {"completions": self})()

    def edit(self, **kw):
        FakeOpenAI.aanroepen.append(("edit", kw))
        return type("R", (), {"data": [type("D", (), {"b64_json": base64.b64encode(_png()).decode()})()]})()

    def create(self, **kw):
        FakeOpenAI.aanroepen.append(("chat", kw))
        bericht = type("M", (), {"content": json.dumps(_goed_gelezen(_asset()))})()
        return type("R", (), {"choices": [type("C", (), {"message": bericht})()]})()


def test_openai_aanroepen(monkeypatch):
    import openai
    FakeOpenAI.aanroepen = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    asset = _asset()
    png = ig.ai_afwerking(asset, _png("blue", 1080))
    assert Image.open(io.BytesIO(png)).size == (1080, 1080)
    soort, kw = FakeOpenAI.aanroepen[0]
    assert soort == "edit" and kw["extra_body"] == {"quality": "high", "input_fidelity": "high"}
    assert "Take On Me" in kw["prompt"] and "letter for letter" in kw["prompt"]
    assert "brand names" in kw["prompt"]

    assert ig.controleer_tekst(asset, png) == []
    soort, kw = FakeOpenAI.aanroepen[1]
    assert soort == "chat" and kw["response_format"] == {"type": "json_object"}
    assert kw["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_maak_voor_run_bewaart_beide_varianten(client, wissellijst, monkeypatch):
    monkeypatch.setattr(ig, "_hoes_urls", lambda uris: {})
    monkeypatch.setattr(ig, "afgewerkt_plaatje",
                        lambda asset, strak: (b"AI-" + asset["direction"].encode(), "AI-afwerking goedgekeurd (poging 1)")
                        if asset["direction"] == "IN" else (None, "AI-afwerking afgekeurd: sticker 'X'"))
    run_id = _run_met_wijzigingen(wissellijst["id"])
    assert ig.maak_voor_run(run_id) == 2      # twee sets: IN en OUT

    with get_session() as s:
        rijen = {(a.richting, a.variant): a.bestandsnaam
                 for a in s.query(RotatieAfbeelding).filter_by(run_id=run_id).all()}
    assert rijen[("in", "ai")].endswith("-in-square.png")
    assert rijen[("in", "strak")].endswith("-in-square-strak.png")
    assert rijen[("out", "strak")].endswith("-out-square.png") and ("out", "ai") not in rijen

    beste = ig.plaatjes_voor_run(run_id)
    assert [(p["richting"], p["variant"]) for p in beste] == [("in", "ai"), ("out", "strak")]
    assert beste[0]["png"] == b"AI-IN"

    html = client.get(f"/rotaties/{run_id}/post").get_data(as_text=True)
    assert "AI-afwerking" in html and "AI-afwerking afgekeurd: sticker" in html
    assert html.index("AI-afwerking</span>") < html.index("Strak</span>")
