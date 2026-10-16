"""Rotatie-mail: met de Instagram-plaatjes als bijlage, pas nadat ze gemaakt zijn."""
import threading

import pytest

import automation
import instagram as ig
import mail
import scheduler
import spotify_auth

PLAATJES = [
    {"bestandsnaam": "wissellijst-r001-2026-10-16-in-square.png", "png": b"\x89PNG-in",
     "caption": "🔄 Wissellijst — rotatie #1\n\n5 IN.", "alt": "Alt IN", "richting": "in"},
    {"bestandsnaam": "wissellijst-r001-2026-10-16-out-square.png", "png": b"\x89PNG-out",
     "caption": "5 OUT.", "alt": "Alt OUT", "richting": "out"},
]
ERIN = [{"artiest": "Doe Maar", "titel": "Smoorverliefd", "jaar": 1982}]
ERUIT = [{"artiest": "Simon & Garfunkel", "titel": "<The Boxer>", "jaar": 1969}]


@pytest.fixture
def verstuurd(monkeypatch):
    berichten = []
    monkeypatch.setattr(mail, "mail_configured", lambda: True)
    monkeypatch.setattr(mail, "_send", lambda msg, to: berichten.append(msg))
    return berichten


def _onderdelen(msg):
    return [(p.get_content_type(), p.get_filename()) for p in msg.walk()]


def test_mail_met_plaatjes(verstuurd):
    mail.send_rotation_mail("ik@example.org", "Door de tijd heen", ERUIT, ERIN, plaatjes=PLAATJES)
    msg = verstuurd[0]
    assert msg["Subject"] == "Rotatie voltooid: Door de tijd heen (2 Instagram-plaatjes)"
    assert msg["To"] == "ik@example.org"
    bijlagen = [(t, f) for t, f in _onderdelen(msg) if f]
    assert bijlagen == [("image/png", PLAATJES[0]["bestandsnaam"]), ("image/png", PLAATJES[1]["bestandsnaam"])]
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert "Simon &amp; Garfunkel &mdash; &lt;The Boxer&gt; (1969)" in html
    assert "5 IN." in html and "Alt OUT" in html
    tekst = next(p for p in msg.walk() if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    assert "+ Doe Maar - Smoorverliefd (1982)" in tekst and "Alt-tekst: Alt IN" in tekst


def test_mail_met_reden_zonder_plaatjes(verstuurd):
    mail.send_rotation_mail("ik@example.org", "Lijst", ERUIT, ERIN, plaatjes_fout="IN: 4 tracks in plaats van 5")
    msg = verstuurd[0]
    assert msg["Subject"] == "Rotatie voltooid: Lijst"
    assert not [f for _, f in _onderdelen(msg) if f]
    tekst = next(p for p in msg.walk() if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    assert "Geen Instagram-plaatje: IN: 4 tracks in plaats van 5" in tekst


@pytest.fixture
def synchroon(monkeypatch):
    """Draai de achtergrond-thread direct, zodat de test het resultaat ziet."""
    class DirecteThread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(threading, "Thread", DirecteThread)


def test_mail_wacht_op_plaatjes(verstuurd, synchroon, monkeypatch):
    monkeypatch.setattr(ig, "maak_voor_run", lambda run_id: 2)
    monkeypatch.setattr(ig, "plaatjes_voor_run", lambda run_id: PLAATJES)
    scheduler._plaatjes_op_achtergrond(1, ("ik@example.org", "Lijst", ERUIT, ERIN))
    assert len(verstuurd) == 1 and "2 Instagram-plaatjes" in verstuurd[0]["Subject"]


def test_mail_ook_als_plaatjes_mislukken(verstuurd, synchroon, monkeypatch):
    def mislukt(run_id):
        raise ValueError("Geen plaatje gemaakt. OUT: 0 tracks uit de 10s")
    monkeypatch.setattr(ig, "maak_voor_run", mislukt)
    scheduler._plaatjes_op_achtergrond(1, ("ik@example.org", "Lijst", ERUIT, ERIN))
    tekst = next(p for p in verstuurd[0].walk() if p.get_content_type() == "text/plain")
    assert "0 tracks uit de 10s" in tekst.get_payload(decode=True).decode()


def test_volgorde_bij_rotatie(client, wissellijst, verstuurd, monkeypatch):
    achtergrond = []
    monkeypatch.setattr(scheduler, "_plaatjes_op_achtergrond", lambda run_id, mail=None: achtergrond.append(mail))
    monkeypatch.setattr(spotify_auth, "ensure_token", lambda: None)
    monkeypatch.setattr(automation, "rotate_and_regenerate", lambda wl: {
        "status": "ok", "tekst": "ok", "verwijderd": 1, "toegevoegd": 1,
        "verwijderd_detail": ERUIT, "toegevoegd_detail": ERIN})
    basis = {**wissellijst, "mail_na_rotatie": True, "mail_adres": "ik@example.org"}

    # Plaatjes uit: de mail gaat direct
    client.post("/api/wissellijsten", json={**basis, "instagram_plaatjes": False})
    scheduler._execute_rotation(wissellijst["id"])
    assert len(verstuurd) == 1 and achtergrond == []

    # Plaatjes aan: geen directe mail, de achtergrond-taak krijgt het adres mee
    client.post("/api/wissellijsten", json={**basis, "instagram_plaatjes": True})
    scheduler._execute_rotation(wissellijst["id"])
    assert len(verstuurd) == 1
    assert achtergrond[0][0] == "ik@example.org" and achtergrond[0][3] == ERIN
