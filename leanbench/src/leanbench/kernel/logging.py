"""Structured logging. Every record carries run_id / task_id / component."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_RESERVED = frozenset(vars(logging.makeLogRecord({})))
LOGGER_NAME = "leanbench"


class StructuredFormatter(logging.Formatter):
    """One JSON object per line: no ambiguity when a run is being triaged."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "task_id": getattr(record, "task_id", None),
            "component": getattr(record, "component", record.module),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


class ContextLogger:
    """A logger bound to a run/task/component triple."""

    __slots__ = ("_logger", "_context")

    def __init__(self, logger: logging.Logger, context: dict[str, Any]):
        self._logger = logger
        self._context = context

    def bind(self, **kwargs: Any) -> ContextLogger:
        merged = dict(self._context)
        merged.update({k: v for k, v in kwargs.items() if v is not None})
        return ContextLogger(self._logger, merged)

    def _log(self, level: int, message: str, **extra: Any) -> None:
        merged = dict(self._context)
        merged.update(extra)
        self._logger.log(level, message, extra=merged)

    def debug(self, message: str, **extra: Any) -> None:
        self._log(logging.DEBUG, message, **extra)

    def info(self, message: str, **extra: Any) -> None:
        self._log(logging.INFO, message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self._log(logging.WARNING, message, **extra)

    def error(self, message: str, **extra: Any) -> None:
        self._log(logging.ERROR, message, **extra)


def configure_logging(level: str = "INFO", *, stream: Any = None, structured: bool = True) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        StructuredFormatter() if structured else logging.Formatter("%(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def get_logger(
    component: str, *, run_id: str | None = None, task_id: str | None = None
) -> ContextLogger:
    return ContextLogger(
        logging.getLogger(LOGGER_NAME),
        {"component": component, "run_id": run_id, "task_id": task_id},
    )
