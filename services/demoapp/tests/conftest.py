import json
import logging

import httpx
import pytest
from demoapp.main import create_app
from demoapp.settings import DemoSettings


class CaptureHandler(logging.Handler):
    """Keeps every line as the parsed JSON object the real handlers would write."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))

    def events(self, level: str | None = None) -> list[str]:
        return [line["event"] for line in self.lines if level in (None, line["level"])]


@pytest.fixture
def logs() -> CaptureHandler:
    return CaptureHandler()


@pytest.fixture
def settings() -> DemoSettings:
    return DemoSettings(_env_file=None, ship_logs=False, git_sha="abc123", jitter_rate=0.0)


@pytest.fixture
def app(settings, logs):
    yield create_app(settings, extra_handlers=(logs,))
    logging.getLogger().removeHandler(logs)


@pytest.fixture
async def client(app):
    # ASGITransport doesn't run the lifespan; nothing in the request path needs it.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
