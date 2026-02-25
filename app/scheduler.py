"""APScheduler service voor Wissellijst V3.

Vervangt de polling-based _check_schedules thread met echte cron/interval jobs.
"""
import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from logging_config import get_logger

logger = get_logger(__name__)

# Globale scheduler instance
_scheduler = None


class WissellijstScheduler:
    """Beheert APScheduler jobs voor wissellijst rotaties."""

    def __init__(self):
        self.scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            }
        )

    def start(self):
        """Start de scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler gestart")

    def shutdown(self):
        """Stop de scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler gestopt")

    def reload_jobs(self):
        """Herlaad alle jobs uit de database.

        Verwijdert bestaande jobs en maakt nieuwe aan op basis van
        de wissellijst configuraties in de database.
        """
        from db.session import db_available, get_session
        from db.models import Wissellijst

        if not db_available():
            logger.warning("Database niet beschikbaar, skip job reload")
            return

        # Verwijder alle bestaande wissellijst jobs
        existing_jobs = self.scheduler.get_jobs()
        for job in existing_jobs:
            if job.id.startswith("wl_"):
                job.remove()

        # Laad wissellijsten uit DB en maak jobs
        with get_session() as session:
            wissellijsten = session.query(Wissellijst).all()
            count = 0
            for wl in wissellijsten:
                if self._add_job(wl):
                    count += 1

        logger.info("Jobs herladen", extra={"jobs_count": count})

    def update_job(self, wissellijst):
        """Update of verwijder een job voor een specifieke wissellijst.

        Args:
            wissellijst: Wissellijst object of dict met configuratie
        """
        wl_id = wissellijst.id if hasattr(wissellijst, "id") else wissellijst["id"]
        job_id = f"wl_{wl_id}"

        # Verwijder bestaande job
        existing = self.scheduler.get_job(job_id)
        if existing:
            existing.remove()

        # Maak nieuwe job als schema niet 'uit' is
        self._add_job(wissellijst)

    def _add_job(self, wissellijst):
        """Voeg een job toe voor een wissellijst.

        Returns: True als job is aangemaakt, False als schema 'uit' is.
        """
        if hasattr(wissellijst, "rotatie_schema"):
            # SQLAlchemy model
            wl_id = wissellijst.id
            schema = wissellijst.rotatie_schema or "uit"
            tijdstip = wissellijst.rotatie_tijdstip or "08:00"
            dag = wissellijst.rotatie_dag or 0
            naam = wissellijst.naam
        else:
            # Dict
            wl_id = wissellijst["id"]
            schema = wissellijst.get("rotatie_schema", "uit")
            tijdstip = wissellijst.get("rotatie_tijdstip", "08:00")
            dag = wissellijst.get("rotatie_dag", 0)
            naam = wissellijst.get("naam", wl_id)

        if schema == "uit":
            return False

        trigger = self._make_trigger(schema, tijdstip, dag)
        if not trigger:
            logger.warning("Ongeldige trigger voor wissellijst",
                           extra={"wissellijst_id": wl_id, "schema": schema})
            return False

        job_id = f"wl_{wl_id}"
        self.scheduler.add_job(
            func=_execute_rotation,
            trigger=trigger,
            id=job_id,
            name=f"Rotatie: {naam}",
            args=[wl_id],
            replace_existing=True,
        )

        logger.info("Job aangemaakt",
                     extra={"wissellijst_id": wl_id, "schema": schema,
                            "naam": naam})
        return True

    def _make_trigger(self, schema, tijdstip, dag):
        """Maak een APScheduler trigger op basis van schema.

        Args:
            schema: 'elk_uur', 'elke_3_uur', 'dagelijks', 'wekelijks'
            tijdstip: 'HH:MM' string
            dag: 0-6 (ma-zo)

        Returns: trigger object of None
        """
        try:
            uur, minuut = map(int, tijdstip.split(":"))
        except (ValueError, AttributeError):
            uur, minuut = 8, 0

        if schema == "elk_uur":
            return CronTrigger(minute=0)
        elif schema == "elke_3_uur":
            return IntervalTrigger(hours=3)
        elif schema == "dagelijks":
            return CronTrigger(hour=uur, minute=minuut)
        elif schema == "wekelijks":
            # APScheduler: 0=ma, 6=zo
            return CronTrigger(day_of_week=dag, hour=uur, minute=minuut)
        return None

    def get_jobs(self):
        """Lijst van geplande jobs.

        Returns: lijst van dicts met job info
        """
        jobs = []
        for job in self.scheduler.get_jobs():
            if job.id.startswith("wl_"):
                jobs.append({
                    "id": job.id,
                    "naam": job.name,
                    "wissellijst_id": job.id.replace("wl_", ""),
                    "volgende_run": job.next_run_time.isoformat()
                    if job.next_run_time else None,
                })
        return jobs

    def trigger_manual(self, wissellijst_id):
        """Trigger een rotatie handmatig (buiten het schema).

        Args:
            wissellijst_id: ID van de wissellijst
        """
        _execute_rotation(wissellijst_id, triggered_by="user")


def _execute_rotation(wissellijst_id, triggered_by="scheduler"):
    """Voer een rotatie uit voor een wissellijst.

    Wordt aangeroepen door APScheduler of handmatig.
    Maakt RotatieRun en RotatieWijziging records aan.
    """
    from db.session import db_available, get_session
    from db.models import Wissellijst, RotatieRun, RotatieWijziging

    logger.info("Rotatie starten",
                extra={"wissellijst_id": wissellijst_id,
                       "triggered_by": triggered_by})

    if not db_available():
        logger.error("Database niet beschikbaar voor rotatie")
        return

    # Haal wissellijst op
    with get_session() as session:
        wl = session.query(Wissellijst).get(wissellijst_id)
        if not wl:
            logger.error("Wissellijst niet gevonden",
                         extra={"wissellijst_id": wissellijst_id})
            return
        wl_dict = wl.to_dict()

    # Maak rotatie run record
    with get_session() as session:
        run = RotatieRun(
            wissellijst_id=wissellijst_id,
            triggered_by=triggered_by,
            status="gestart",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    try:
        from automation import rotate_and_regenerate
        result = rotate_and_regenerate(wl_dict)

        # Update run record
        with get_session() as session:
            run = session.query(RotatieRun).get(run_id)
            run.status = "voltooid"
            run.completed_at = datetime.datetime.utcnow()
            run.tracks_verwijderd = result.get("verwijderd", 0)
            run.tracks_toegevoegd = result.get("toegevoegd", 0)

            # Sla individuele wijzigingen op
            for track in result.get("verwijderd_detail", []):
                session.add(RotatieWijziging(
                    run_id=run_id,
                    type="verwijderd",
                    artiest=track.get("artiest", ""),
                    titel=track.get("titel", ""),
                ))
            for track in result.get("toegevoegd_detail", []):
                session.add(RotatieWijziging(
                    run_id=run_id,
                    type="toegevoegd",
                    artiest=track.get("artiest", ""),
                    titel=track.get("titel", ""),
                ))

        # Update laatste rotatie
        with get_session() as session:
            wl = session.query(Wissellijst).get(wissellijst_id)
            wl.laatste_rotatie = datetime.datetime.utcnow()

        logger.info("Rotatie voltooid",
                     extra={"wissellijst_id": wissellijst_id,
                            "run_id": run_id,
                            "verwijderd": result.get("verwijderd", 0),
                            "toegevoegd": result.get("toegevoegd", 0)})

        # Stuur e-mail als ingeschakeld
        if (wl_dict.get("mail_na_rotatie") and wl_dict.get("mail_adres")
                and result.get("status") == "ok"):
            try:
                from mail import send_rotation_mail
                send_rotation_mail(
                    wl_dict["mail_adres"], wl_dict["naam"],
                    result.get("verwijderd_detail", []),
                    result.get("toegevoegd_detail", []),
                )
            except Exception as mail_err:
                logger.error("Fout bij rotatie-mail",
                             extra={"error": str(mail_err)})

    except Exception as e:
        with get_session() as session:
            run = session.query(RotatieRun).get(run_id)
            run.status = "mislukt"
            run.completed_at = datetime.datetime.utcnow()
            run.error_message = str(e)

        logger.error("Rotatie mislukt",
                     extra={"wissellijst_id": wissellijst_id,
                            "run_id": run_id, "error": str(e)})


def get_scheduler():
    """Haal de globale scheduler instance op (lazy init)."""
    global _scheduler
    if _scheduler is None:
        _scheduler = WissellijstScheduler()
    return _scheduler
