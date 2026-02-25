"""Structured logging configuratie voor Wissellijst V3."""
import logging
import sys

from pythonjsonlogger import jsonlogger


def setup_logging(level=logging.INFO):
    """Configureer structured JSON logging.

    Alle print() statements worden vervangen door logger calls.
    Output gaat naar stdout in JSON formaat.
    """
    # JSON formatter
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Root handler naar stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Root logger configureren
    root = logging.getLogger()
    root.setLevel(level)
    # Verwijder bestaande handlers
    root.handlers.clear()
    root.addHandler(handler)

    # Dempt noisy loggers
    for name in ("apscheduler", "urllib3", "spotipy", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return root


def get_logger(name):
    """Maak een logger voor een module.

    Gebruik:
        logger = get_logger(__name__)
        logger.info("Rotatie gestart", extra={"wissellijst_id": "abc123"})
    """
    return logging.getLogger(name)
