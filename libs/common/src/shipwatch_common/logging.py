"""JSON logging for every service: structlog for our code, stdlib loggers routed through it.

Values bound with `structlog.contextvars.bind_contextvars(...)` (e.g. delivery_id) appear on
every line logged in that context, including lines from stdlib loggers (uvicorn, sqlalchemy).
"""

import logging
import sys
from collections.abc import Iterable, Mapping
from typing import Any

import structlog

_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def _add_static(fields: Mapping[str, Any]) -> structlog.typing.Processor:
    """Processor setting `fields` on every line, without overriding a value the line already has."""

    def add(_: Any, __: str, event_dict: structlog.typing.EventDict) -> structlog.typing.EventDict:
        for key, value in fields.items():
            event_dict.setdefault(key, value)
        return event_dict

    return add


def json_formatter(static_fields: Mapping[str, Any] | None = None) -> logging.Formatter:
    """Formatter rendering both structlog and plain stdlib records as one JSON object per line.

    `static_fields` (e.g. service and sha) are added to every line, structlog or stdlib.
    """
    static = [_add_static(static_fields)] if static_fields else []
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*_SHARED_PROCESSORS, structlog.stdlib.ExtraAdder()],
        processors=[
            *static,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
    )


def configure_logging(
    level: str = "INFO",
    *,
    static_fields: Mapping[str, Any] | None = None,
    extra_handlers: Iterable[logging.Handler] = (),
) -> None:
    """Route everything to JSON on stdout, and to `extra_handlers` (e.g. a Kafka shipper) too."""
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = json_formatter(static_fields)
    handlers = [logging.StreamHandler(sys.stdout), *extra_handlers]
    for handler in handlers:
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)

    # Uvicorn installs its own plain-text handlers; send its records through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # Services log one "request" line per request themselves (with delivery_id), so uvicorn's
    # access log would only duplicate it.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
