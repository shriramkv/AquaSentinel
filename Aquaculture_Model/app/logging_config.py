"""
Logging setup shared by the API and the training scripts, so every part of the project
writes log lines in the same format.

Level comes from the LOG_LEVEL environment variable (DEBUG / INFO / WARNING / ERROR, default INFO):
  INFO   one line per prediction, model load, ThingSpeak fetches, pond status changes
  DEBUG  adds per-request detail: cleaning reports, buffer sizes, data-quality warnings
"""
from __future__ import annotations

import logging
import os

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Attach a stderr handler to the root logger. Safe to call more than once.

    `logging.basicConfig` does nothing when the root logger already has handlers, so a host
    application (or pytest) that configured logging itself keeps its own setup.
    """
    name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    resolved = logging.getLevelName(name)
    bad_level = not isinstance(resolved, int)      # unknown names come back as a string
    logging.basicConfig(level=logging.INFO if bad_level else resolved, format=LOG_FORMAT)
    if bad_level:
        logging.getLogger(__name__).warning("Unknown LOG_LEVEL %r - falling back to INFO", name)

    # httpx logs every request URL at INFO. The ThingSpeak URL carries the channel's read key as a
    # query parameter, so keep these loggers quiet to stop the key from ending up in the log files.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
