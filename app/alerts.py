"""Alarm bij falende rotaties en status van de Spotify-koppeling.

Eén mail per oorzaak: na een mislukte rotatie gaat er een mail uit, daarna
zwijgt het alarm voor die oorzaak tot een rotatie weer slaagt (of tot
ALERT_REPEAT_HOURS verstreken zijn).
"""
import datetime
import os
import threading
import time

from logging_config import get_logger

logger = get_logger(__name__)

ALERT_MAIL = os.getenv("ALERT_MAIL", "")
ALERT_REPEAT_HOURS = int(os.getenv("ALERT_REPEAT_HOURS", "24"))

_lock = threading.Lock()
# oorzaak -> tijdstip van de laatste mail
_gemeld = {}
# laatste fout, voor de status-API
_laatste_fout = None
# cache van de Spotify-status (duur ~1 API-call)
_spotify_cache = {"at": 0.0, "value": None}

SPOTIFY_AUTH_MARKERS = ("auth_required", "invalid_grant", "revoked",
                        "invalid_client", "token")


def classify_error(error):
    """Deel een fout in: spotify_auth, openai, spotify, overig."""
    tekst = str(error).lower()
    if any(m in tekst for m in SPOTIFY_AUTH_MARKERS) and "openai" not in tekst:
        return "spotify_auth"
    if "openai" in tekst or "api key" in tekst:
        return "openai"
    if "spotify" in tekst or "http status: 429" in tekst:
        return "spotify"
    return "overig"


def _uitleg(kind):
    return {
        "spotify_auth": ("De Spotify-koppeling is verlopen of ingetrokken. "
                         "Log opnieuw in via /login; tot die tijd faalt elke rotatie."),
        "openai": "OpenAI weigert de aanvraag. Controleer de API-key en het tegoed.",
        "spotify": "Spotify gaf een fout (mogelijk tijdelijk of een rate-limit).",
        "overig": "Onbekende fout; zie de logregels van de container.",
    }[kind]


def _alert_adres():
    """ALERT_MAIL, anders het eerste mailadres van een wissellijst met mail aan."""
    if ALERT_MAIL:
        return ALERT_MAIL
    try:
        from config import load_wissellijsten
        for wl in load_wissellijsten().get("wissellijsten", []):
            if wl.get("mail_na_rotatie") and wl.get("mail_adres"):
                return wl["mail_adres"]
    except Exception:
        pass
    return ""


def notify_failure(wissellijst_naam, error, triggered_by="scheduler"):
    """Registreer een mislukte rotatie en mail één keer per oorzaak."""
    global _laatste_fout
    kind = classify_error(error)
    now = datetime.datetime.now(datetime.timezone.utc)
    with _lock:
        _laatste_fout = {
            "wissellijst": wissellijst_naam,
            "soort": kind,
            "fout": str(error)[:300],
            "tijd": now.isoformat(),
            "triggered_by": triggered_by,
        }
        last = _gemeld.get(kind)
        if last and (now - last) < datetime.timedelta(hours=ALERT_REPEAT_HOURS):
            logger.info("Alarm al gemeld, geen nieuwe mail",
                        extra={"soort": kind, "wissellijst": wissellijst_naam})
            return False
        _gemeld[kind] = now

    adres = _alert_adres()
    onderwerp = f"Wissellijst: rotatie mislukt ({kind})"
    tekst = (
        f"Rotatie van '{wissellijst_naam}' is mislukt.\n\n"
        f"Oorzaak: {_uitleg(kind)}\n\n"
        f"Foutmelding: {str(error)[:500]}\n\n"
        f"Je krijgt deze mail één keer per oorzaak; de volgende komt pas na een "
        f"geslaagde rotatie of na {ALERT_REPEAT_HOURS} uur."
    )
    logger.error("ALARM: rotatie mislukt",
                 extra={"soort": kind, "wissellijst": wissellijst_naam,
                        "mail_naar": adres or "geen"})
    if not adres:
        return False
    try:
        from mail import send_alert_mail
        send_alert_mail(adres, onderwerp, tekst)
        return True
    except Exception as e:
        logger.error("Alarm-mail versturen mislukt", extra={"error": str(e)})
        return False


def clear_failures():
    """Een geslaagde rotatie: alarm resetten."""
    global _laatste_fout
    with _lock:
        _gemeld.clear()
        _laatste_fout = None


def laatste_fout():
    with _lock:
        return dict(_laatste_fout) if _laatste_fout else None


def spotify_status(max_age=60):
    """Werkt de Spotify-koppeling? Gecachet voor max_age seconden."""
    now = time.time()
    with _lock:
        cached = _spotify_cache["value"]
        if cached is not None and now - _spotify_cache["at"] < max_age:
            return cached
    status = {"connected": False, "user": None, "error": None}
    try:
        from spotify_auth import get_spotify_client
        sp = get_spotify_client()
        me = sp.current_user()
        status["connected"] = True
        status["user"] = me.get("display_name") or me.get("id")
    except Exception as e:
        status["error"] = str(e)[:200]
    with _lock:
        _spotify_cache["at"] = now
        _spotify_cache["value"] = status
    return status


def invalidate_spotify_status():
    with _lock:
        _spotify_cache["value"] = None
