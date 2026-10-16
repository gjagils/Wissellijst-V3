"""Instagram-post van een rotatie."""
import datetime

import automation
import config
import post
from db.models import RotatieRun, RotatieWijziging
from db.session import get_session


def _maak_run(wl_id, wijzigingen):
    with get_session() as s:
        run = RotatieRun(wissellijst_id=wl_id, triggered_by="scheduler", status="voltooid",
                         started_at=datetime.datetime(2026, 10, 16, 6, 0),
                         completed_at=datetime.datetime(2026, 10, 16, 6, 1))
        s.add(run)
        s.flush()
        for soort, artiest, titel, uri in wijzigingen:
            s.add(RotatieWijziging(run_id=run.id, type=soort, artiest=artiest, titel=titel, uri=uri))
        return run.id


def test_post_pagina(client, anon, wissellijst, monkeypatch):
    monkeypatch.setattr(post, "_hoezen", lambda uris: {"spotify:track:N1": "https://i.scdn.co/image/abc"})
    run_id = _maak_run(wissellijst["id"], [
        ("verwijderd", "Oude Artiest", "Weg <script>", "spotify:track:O1"),
        ("toegevoegd", "Nieuwe Artiest", "Erbij", "spotify:track:N1"),
    ])

    assert anon.get(f"/rotaties/{run_id}/post").status_code == 302   # achter de login

    html = client.get(f"/rotaties/{run_id}/post").get_data(as_text=True)
    assert "Nieuwe Artiest" in html and "Erbij" in html and "Oude Artiest" in html
    assert "Weg &lt;script&gt;" in html and "Weg <script>" not in html.split("<textarea")[0]
    assert "https://i.scdn.co/image/abc" in html
    assert "16 oktober 2026" in html          # 06:01 UTC = 08:01 in Amsterdam, zelfde dag
    assert "🟢 Nieuwe Artiest – Erbij" in html and "🔴 Oude Artiest" in html

    assert client.get("/rotaties/999999/post").status_code == 404


def test_caption_volgorde():
    tekst = post.caption("Lijst", "1 januari 2026",
                         [{"artiest": "A", "titel": "x"}], [{"artiest": "B", "titel": "y"}])
    assert tekst.index("Erin:") < tekst.index("🟢 B – y") < tekst.index("Eruit:") < tekst.index("🔴 A – x")


class FakeSp:
    def __init__(self):
        self.verwijderd, self.toegevoegd = [], []

    def playlist_items(self, playlist_id, limit=100):
        return {"items": [{"track": {"uri": "spotify:track:OUD", "name": "Oud",
                                     "artists": [{"name": "Oude"}],
                                     "album": {"release_date": "1985-01-01"}}}]}

    def playlist_remove_all_occurrences_of_items(self, pl, uris):
        self.verwijderd += uris

    def playlist_add_items(self, pl, uris):
        self.toegevoegd += uris


def test_rotatie_detail_bevat_uri(wissellijst, monkeypatch):
    monkeypatch.setattr(automation, "get_spotify_client", lambda: FakeSp())
    config.save_wachtrij(wissellijst["id"], [
        {"categorie": "80s", "artiest": "Nieuw", "titel": "Erin", "uri": "spotify:track:NIEUW"}])
    result = automation.rotate_playlist("pl", wissellijst["id"])
    assert result["verwijderd_detail"][0]["uri"] == "spotify:track:OUD"
    assert result["toegevoegd_detail"][0]["uri"] == "spotify:track:NIEUW"


def test_verwijderd_decennium_uit_historie(wissellijst, monkeypatch):
    """Een remaster van 2011 die als 80s binnenkwam, gaat er ook als 80s uit."""
    class RemasterSp(FakeSp):
        def playlist_items(self, playlist_id, limit=100):
            items = super().playlist_items(playlist_id, limit)
            items["items"][0]["track"]["album"]["release_date"] = "2011-05-01"
            return items
    monkeypatch.setattr(automation, "get_spotify_client", lambda: RemasterSp())
    config.add_historie_bulk(wissellijst["id"], [
        {"categorie": "80s", "artiest": "Oude", "titel": "Oud", "uri": "spotify:track:OUD"}])
    config.save_wachtrij(wissellijst["id"], [
        {"categorie": "80s", "artiest": "Nieuw", "titel": "Erin", "uri": "spotify:track:NIEUW"}])
    result = automation.rotate_playlist("pl", wissellijst["id"])
    assert result["verwijderd_detail"][0]["categorie"] == "80s"
