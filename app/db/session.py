"""Database-engine, sessies en migraties voor Wissellijst V3.

Het schema wordt uitsluitend door Alembic beheerd. Een database die nog uit
de tijd van create_all stamt (tabellen aanwezig, geen alembic_version) wordt
eenmalig gestempeld op revisie 001 en daarna normaal geüpgraded.
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from logging_config import get_logger

logger = get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

engine = None
SessionLocal = None


def init_db():
    """Maak de engine aan. Returns False als DATABASE_URL ontbreekt."""
    global engine, SessionLocal

    if not DATABASE_URL:
        return False

    kwargs = {"pool_pre_ping": True}
    if not DATABASE_URL.startswith("sqlite"):
        kwargs.update(pool_size=5, max_overflow=10)
    engine = create_engine(DATABASE_URL, **kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return True


def _alembic_config():
    from alembic.config import Config

    cfg = Config(os.path.join(APP_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(APP_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    return cfg


def migrate():
    """Breng het schema naar de nieuwste revisie.

    Raises bij een echte fout: een app met een half schema moet niet starten.
    """
    from alembic import command

    cfg = _alembic_config()
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    if "wissellijsten" in tables and "alembic_version" not in tables:
        # Oude situatie: create_all heeft 001 al aangemaakt, Alembic weet dat niet.
        logger.warning("Database zonder alembic_version: stempelen op 001")
        command.stamp(cfg, "001")

    command.upgrade(cfg, "head")
    logger.info("Alembic migraties uitgevoerd")


def db_available():
    return engine is not None and SessionLocal is not None


@contextmanager
def get_session():
    """Context manager voor database-sessies (commit bij succes, rollback bij fout)."""
    if not db_available():
        raise RuntimeError("Database niet beschikbaar")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
