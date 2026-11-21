"""Structured logging.

LOG_FORMAT=json emits one JSON object per line in the shape Cloud Logging parses natively
(`severity`, `message`, `logging.googleapis.com/trace`), so request logs, agent spans and errors
can be correlated by request ID / trace in Logs Explorer. LOG_FORMAT=text is for local development.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

from ..config import settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("verity_request_id", default=None)
trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("verity_trace", default=None)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "logger": record.name,
        }
        rid = request_id_var.get()
        if rid:
            doc["request_id"] = rid
        trace = trace_var.get()
        if trace and settings.gcp_project:
            doc["logging.googleapis.com/trace"] = f"projects/{settings.gcp_project}/traces/{trace}"
        # structured extras: log.info("...", extra={"route": "/ask", "status": 200})
        for k, v in vars(record).items():
            if k not in _RESERVED and not k.startswith("_"):
                doc[k] = v
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        rid = request_id_var.get()
        return f"{line} [req={rid}]" if rid else line


_configured = False


def configure_logging(level: str | None = None, fmt: str | None = None, force: bool = False) -> None:
    """Idempotent root-logger setup; called by the API lifespan and every CLI entry point."""
    global _configured
    if _configured and not force:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if (fmt or settings.log_format) == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or settings.log_level).upper())
    for noisy in ("httpx", "httpcore", "pymongo", "urllib3", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
