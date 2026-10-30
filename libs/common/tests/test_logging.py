import json
import logging

import structlog
from shipwatch_common.logging import configure_logging, get_logger


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


def test_static_fields_and_extra_handlers():
    capture = _Capture()
    configure_logging(
        "INFO", static_fields={"service": "svc", "sha": "abc"}, extra_handlers=[capture]
    )
    try:
        structlog.contextvars.clear_contextvars()
        get_logger("t").info("from structlog", sha="override")
        logging.getLogger("plain").warning("from stdlib")
    finally:
        logging.getLogger().removeHandler(capture)

    structlog_line, stdlib_line = capture.lines
    assert structlog_line["event"] == "from structlog"
    assert (structlog_line["service"], structlog_line["sha"]) == ("svc", "override")
    assert stdlib_line["event"] == "from stdlib"
    assert (stdlib_line["service"], stdlib_line["sha"], stdlib_line["level"]) == (
        "svc",
        "abc",
        "warning",
    )


def test_defaults_add_nothing():
    capture = _Capture()
    configure_logging("INFO", extra_handlers=[capture])
    try:
        get_logger("t").info("hello")
    finally:
        logging.getLogger().removeHandler(capture)
        configure_logging("INFO")
    assert "service" not in capture.lines[0]
