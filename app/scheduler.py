"""APScheduler-service: geplande rotaties, plus de ene plek waar een rotatie
wordt uitgevoerd en vastgelegd (audit trail), ook bij handmatige rotaties."""
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from logging_config import get_logger

logger = get_logger(__name__)

_scheduler = None

# Tijdzone waarin rotatie-tijdstippen worden geïnterpreteerd (de container draait in UTC).
SCHEDULER_TZ = os.getenv("SCHEDULER_TZ") or os.getenv("TZ") or "Europe/Amsterdam"


class WissellijstScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler(
            timezone=SCHEDULER_TZ,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler gestart", extra={"timezone": SCHEDULER_TZ})

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler gestopt")

    def reload_jobs(self):
        """Verwijder alle wissellijst-jobs en maak ze opnieuw aan uit de database."""
        from db.session import db_available, get_session
        from db.models import Wissellijst

        if not db_available():
            logger.warning("Database niet beschikbaar, skip job reload")
            return
        for job in self.scheduler.get_jobs():
            if job.id.startswith("wl_"):
                job.remove()
        with get_session() as session:
            count = sum(1 for wl in session.query(Wissellijst).all() if self._add_job(wl))
        logger.info("Jobs herladen", extra={"jobs_count": count})

    def update_job(self, wissellijst):
        """Herplan (of verwijder) de job van één wissellijst (model of dict)."""
        wl_id = wissellijst.id if hasattr(wissellijst, "id") else wissellijst["id"]
        self.remove_job(wl_id)
        self._add_job(wissellijst)

    def remove_job(self, wl_id):
        existing = self.scheduler.get_job(f"wl_{wl_id}")
        if existing:
            existing.remove()

    def _add_job(self, wissellijst):
        if hasattr(wissellijst, "rotatie_schema"):
            wl_id, naam = wissellijst.id, wissellijst.naam
            schema = wissellijst.rotatie_schema or "uit"
            tijdstip = wissellijst.rotatie_tijdstip or "08:00"
            dag = wissellijst.rotatie_dag or 0
        else:
            wl_id = wissellijst["id"]
            naam = wissellijst.get("naam", wl_id)
            schema = wissellijst.get("rotatie_schema", "uit")
            tijdstip = wissellijst.get("rotatie_tijdstip", "08:00")
            dag = wissellijst.get("rotatie_dag", 0)

        if schema == "uit":
            return False
        trigger = self._make_trigger(schema, tijdstip, dag)
        if not trigger:
            logger.warning("Ongeldige trigger", extra={"wissellijst_id": wl_id, "schema": schema})
            return False

        self.scheduler.add_job(
            func=_execute_rotation, trigger=trigger, id=f"wl_{wl_id}",
            name=f"Rotatie: {naam}", args=[wl_id], replace_existing=True,
        )
        logger.info("Job aangemaakt", extra={"wissellijst_id": wl_id, "schema": schema, "naam": naam})
        return True

    @staticmethod
    def _make_trigger(schema, tijdstip, dag):
        try:
            uur, minuut = map(int, tijdstip.split(":"))
        except (ValueError, AttributeError):
            uur, minuut = 8, 0
        tz = SCHEDULER_TZ
        if schema == "elk_uur":
            return CronTrigger(minute=0, timezone=tz)
        if schema == "elke_3_uur":
            return CronTrigger(hour="11,14,17,20", minute=0, timezone=tz)
        if schema == "dagelijks":
            return CronTrigger(hour=uur, minute=minuut, timezone=tz)
        if schema == "wekelijks":
            return CronTrigger(day_of_week=dag, hour=uur, minute=minuut, timezone=tz)
        return None

    def get_jobs(self):
        return [{
            "id": job.id,
            "naam": job.name,
            "wissellijst_id": job.id.replace("wl_", ""),
            "volgende_run": job.next_run_time.isoformat() if job.next_run_time else None,
        } for job in self.scheduler.get_jobs() if job.id.startswith("wl_")]


def _execute_rotation(wissellijst_id, triggered_by="scheduler"):
    """Voer een rotatie uit en leg hem vast (RotatieRun + wijzigingen).

    Wordt aangeroepen door APScheduler én door de handmatige rotatie in web.py.
    Returns het resultaat-dict van rotate_and_regenerate; raist bij een fout
    (na vastleggen en alarm), zodat de aanroeper de fout ook ziet.
    """
    from db.session import get_session
    from db.models import Wissellijst, RotatieRun, RotatieWijziging, utcnow
    from alerts import notify_failure, clear_failures

    logger.info("Rotatie starten", extra={"wissellijst_id": wissellijst_id,
                                          "triggered_by": triggered_by})

    with get_session() as session:
        wl = session.get(Wissellijst, wissellijst_id)
        if not wl:
            raise ValueError(f"Wissellijst niet gevonden: {wissellijst_id}")
        wl_dict = wl.to_dict()

    with get_session() as session:
        run = RotatieRun(wissellijst_id=wissellijst_id, triggered_by=triggered_by, status="gestart")
        session.add(run)
        session.flush()
        run_id = run.id

    try:
        # Eerst het token controleren: een ingetrokken token is dan direct een
        # spotify_auth-fout in plaats van een vage fout halverwege.
        from spotify_auth import ensure_token
        ensure_token()

        from automation import rotate_and_regenerate
        result = rotate_and_regenerate(wl_dict)

        with get_session() as session:
            run = session.get(RotatieRun, run_id)
            run.status = "mislukt" if result.get("status") == "fout" else "voltooid"
            run.completed_at = utcnow()
            run.tracks_verwijderd = result.get("verwijderd", 0)
            run.tracks_toegevoegd = result.get("toegevoegd", 0)
            run.error_message = result.get("tekst") if result.get("status") == "fout" else None
            for soort, key in (("verwijderd", "verwijderd_detail"), ("toegevoegd", "toegevoegd_detail")):
                for track in result.get(key, []):
                    session.add(RotatieWijziging(run_id=run_id, type=soort,
                                                 artiest=track.get("artiest", ""),
                                                 titel=track.get("titel", ""),
                                                 uri=track.get("uri", ""),
                                                 categorie=track.get("categorie", ""),
                                                 jaar=track.get("jaar")))
            wl = session.get(Wissellijst, wissellijst_id)
            if result.get("status") == "ok":
                wl.laatste_rotatie = utcnow()

        logger.info("Rotatie voltooid",
                    extra={"wissellijst_id": wissellijst_id, "run_id": run_id,
                           "status": result.get("status"),
                           "verwijderd": result.get("verwijderd", 0),
                           "toegevoegd": result.get("toegevoegd", 0)})

        if result.get("status") == "fout":
            notify_failure(wl_dict.get("naam", wissellijst_id), result.get("tekst", "onbekend"),
                           triggered_by)
        else:
            clear_failures()
            mail = None
            if (wl_dict.get("mail_na_rotatie") and wl_dict.get("mail_adres")
                    and result.get("status") == "ok"):
                mail = (wl_dict["mail_adres"], wl_dict["naam"],
                        result.get("verwijderd_detail", []), result.get("toegevoegd_detail", []))
            if wl_dict.get("instagram_plaatjes") and result.get("status") == "ok":
                # De mail wacht op de plaatjes, zodat ze als bijlage mee kunnen.
                _plaatjes_op_achtergrond(run_id, mail)
            elif mail:
                _rotatie_mail(*mail)
        return result

    except Exception as e:
        with get_session() as session:
            run = session.get(RotatieRun, run_id)
            run.status = "mislukt"
            run.completed_at = utcnow()
            run.error_message = str(e)[:2000]
        logger.error("Rotatie mislukt", extra={"wissellijst_id": wissellijst_id,
                                               "run_id": run_id, "error": str(e)})
        notify_failure(wl_dict.get("naam", wissellijst_id), e, triggered_by)
        raise


def _rotatie_mail(adres, naam, verwijderd, toegevoegd, plaatjes=None, plaatjes_fout=None):
    try:
        from mail import send_rotation_mail
        send_rotation_mail(adres, naam, verwijderd, toegevoegd,
                           plaatjes=plaatjes, plaatjes_fout=plaatjes_fout)
    except Exception as mail_err:
        logger.error("Fout bij rotatie-mail", extra={"error": str(mail_err)})


def _plaatjes_op_achtergrond(run_id, mail=None):
    """Instagram-plaatjes maken zonder de rotatie op te houden, en daarna de rotatie-mail
    (mail = (adres, naam, verwijderd, toegevoegd)) met de plaatjes als bijlage. Een fout
    bij de plaatjes laat de rotatie geslaagd: hij komt in de log en in de mail, en de
    plaatjes zijn opnieuw te maken via de UI."""
    import threading

    def werk():
        plaatjes, fout = [], None
        try:
            from instagram import maak_voor_run, plaatjes_voor_run
            maak_voor_run(run_id)
            plaatjes = plaatjes_voor_run(run_id)
        except Exception as e:
            fout = str(e)
            logger.error("Instagram-plaatjes maken mislukt", extra={"run_id": run_id, "error": fout})
        if mail:
            _rotatie_mail(*mail, plaatjes=plaatjes, plaatjes_fout=fout)

    threading.Thread(target=werk, daemon=True).start()


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = WissellijstScheduler()
    return _scheduler
