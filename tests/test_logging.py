"""De migraties bij de start mogen de logging van de app niet overschrijven."""
import logging

from pythonjsonlogger import jsonlogger

import web  # noqa: F401  (start draait de migraties)


def test_logging_na_migraties():
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert any(isinstance(h.formatter, jsonlogger.JsonFormatter) for h in root.handlers)
    for naam in ("automation", "suggest", "scheduler", "web", "radio"):
        assert not logging.getLogger(naam).disabled, naam
