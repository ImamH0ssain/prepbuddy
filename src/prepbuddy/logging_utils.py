"""Structured JSON logging helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER_NAME = "prepbuddy"


def configure_logging(logs_dir: Path) -> None:
    """Configure a JSONL file handler once per process."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logs_dir / "prepbuddy.jsonl", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(event: str, **fields: Any) -> None:
    """Write one structured log event."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logging.getLogger(LOGGER_NAME).info(json.dumps(payload, default=str, sort_keys=True))

