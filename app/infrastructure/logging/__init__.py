from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

REDACT_KEYS = frozenset(
    {
        "resume",
        "resume_text",
        "answer_book",
        "email",
        "phone",
        "ssn",
        "password",
        "description_text",
        "description_html",
    }
)

PII_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
)


def redact_value(key: str, value: Any) -> Any:
    if key.lower() in REDACT_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        text = value
        for pattern in PII_PATTERNS:
            text = pattern.sub("[redacted]", text)
        return text
    if isinstance(value, Mapping):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    return value


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "component": getattr(record, "component", record.name),
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", {})
        if isinstance(extra, Mapping):
            for key, value in extra.items():
                payload[key] = redact_value(str(key), value)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=False)


class StructuredLogger:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        self._logger = logger
        self._fields = dict(fields or {})

    def bind(self, **fields: Any) -> StructuredLogger:
        merged = {**self._fields, **fields}
        return StructuredLogger(self._logger, fields=merged)

    def _log(self, level: int, event: str, **fields: Any) -> None:
        extra = {**self._fields, **fields}
        component = extra.get("component", "worker")
        self._logger.log(level, event, extra={"fields": extra, "component": component})

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)


def configure_logging(
    *,
    level: str = "info",
    log_dir: Path | None = None,
    rotate_files: int = 10,
    rotate_mb: int = 5,
    stream: TextIO | None = None,
) -> StructuredLogger:
    logger = logging.getLogger("lodestar")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    formatter = JsonLogFormatter()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "worker.log",
            maxBytes=rotate_mb * 1024 * 1024,
            backupCount=rotate_files,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.propagate = False
    return StructuredLogger(logger, fields={"component": "worker"})
