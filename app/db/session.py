"""Database sessie management voor Wissellijst V3."""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "")

engine = None
SessionLocal = None


def init_db():
    """Initialiseer de database engine en maak tabellen aan."""
    global engine, SessionLocal

    if not DATABASE_URL:
        return False

    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # Maak tabellen aan als ze nog niet bestaan
    Base.metadata.create_all(bind=engine)
    return True


def db_available():
    """Check of de database beschikbaar is."""
    return engine is not None and SessionLocal is not None


@contextmanager
def get_session():
    """Context manager voor database sessies.

    Gebruik:
        with get_session() as session:
            session.query(...)
    """
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
