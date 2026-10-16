"""Instagram-post van een rotatie: wat eruit ging en wat erin kwam."""
import datetime
from zoneinfo import ZoneInfo

from scheduler import SCHEDULER_TZ
from logging_config import get_logger

logger = get_logger(__name__)


def _hoezen(uris):
    """uri -> albumhoes-URL. Leeg als Spotify niet bereikbaar is (de post werkt ook zonder)."""
    uris = [u for u in uris if u and u.startswith("spotify:track:")]
    if not uris:
        return {}
    try:
        from spotify_auth import get_spotify_client
        sp = get_spotify_client()
        hoezen = {}
        for i in range(0, len(uris), 50):
            for t in sp.tracks(uris[i:i + 50])["tracks"]:
                images = (t or {}).get("album", {}).get("images") or []
                if images:
                    hoezen[t["uri"]] = images[-2]["url"] if len(images) > 1 else images[0]["url"]
        return hoezen
    except Exception as e:
        logger.warning("Albumhoezen ophalen mislukt", extra={"error": str(e)})
        return {}


def _datum(dt):
    if not dt:
        return ""
    lokaal = dt.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(SCHEDULER_TZ))
    maanden = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
               "augustus", "september", "oktober", "november", "december"]
    return f"{lokaal.day} {maanden[lokaal.month - 1]} {lokaal.year}"


def _jaar(t):
    return f" ({t['jaar']})" if t.get("jaar") else ""


def caption(naam, datum, eruit, erin):
    regels = [f"🔄 {naam}: de wissel van {datum}", "", "Erin:"]
    regels += [f"🟢 {t['artiest']} – {t['titel']}{_jaar(t)}" for t in erin]
    regels += ["", "Eruit:"]
    regels += [f"🔴 {t['artiest']} – {t['titel']}{_jaar(t)}" for t in eruit]
    regels += ["", "#spotify #playlist #nieuwemuziek #wissellijst"]
    return "\n".join(regels)


def post_data(run_id):
    """Alles voor de post van één rotatie-run, of None als de run niet bestaat."""
    from db.session import get_session
    from db.models import RotatieRun, RotatieWijziging, RotatieAfbeelding, Wissellijst

    with get_session() as s:
        run = s.get(RotatieRun, run_id)
        if not run:
            return None
        wl = s.get(Wissellijst, run.wissellijst_id)
        wijzigingen = (s.query(RotatieWijziging).filter_by(run_id=run_id)
                       .order_by(RotatieWijziging.id).all())
        tracks = [{"type": w.type, "artiest": w.artiest, "titel": w.titel, "uri": w.uri or "",
                   "jaar": w.jaar}
                  for w in wijzigingen]
        naam = wl.naam if wl else "Wissellijst"
        datum = _datum(run.completed_at or run.started_at)
        plaatjes = [{"id": a.id, "richting": a.richting, "deel": a.deel,
                     "bestandsnaam": a.bestandsnaam, "caption": a.caption, "alt": a.alt_tekst}
                    for a in s.query(RotatieAfbeelding).filter_by(run_id=run_id)
                    .order_by(RotatieAfbeelding.deel, RotatieAfbeelding.richting).all()]

    hoezen = _hoezen([t["uri"] for t in tracks])
    for t in tracks:
        t["hoes"] = hoezen.get(t["uri"], "")
    eruit = [t for t in tracks if t["type"] == "verwijderd"]
    erin = [t for t in tracks if t["type"] == "toegevoegd"]
    return {"naam": naam, "datum": datum, "eruit": eruit, "erin": erin, "plaatjes": plaatjes,
            "caption": caption(naam, datum, eruit, erin)}
