import json
import os
import uuid
from typing import Any

# Must be set before the app (and its Settings) is imported. Always overrides any real secret
# exported in the shell, so tests only ever sign with this throwaway value.
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret-not-for-production"

import httpx  # noqa: E402
import pytest  # noqa: E402
from ingester.main import app  # noqa: E402
from ingester.signature import sign  # noqa: E402
from shipwatch_common.db import make_engine, make_sessionmaker  # noqa: E402
from shipwatch_common.models import Base  # noqa: E402
from shipwatch_common.settings import get_settings  # noqa: E402
from sqlalchemy import text  # noqa: E402

SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()


@pytest.fixture
async def client():
    # ASGITransport doesn't run the lifespan, so DB-backed tests wire app.state via `db`.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def db_engine():
    """Engine bound to a fresh schema in the real Postgres; the schema is dropped afterwards."""
    url = get_settings().database_url
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = make_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = make_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine

    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


@pytest.fixture
async def db(db_engine):
    """Session factory on the test schema, wired into the app; tables emptied after each test."""
    sessionmaker = make_sessionmaker(db_engine)
    app.state.sessionmaker = sessionmaker
    yield sessionmaker
    async with db_engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def make_pr_payload(
    *,
    action: str = "opened",
    number: int = 7,
    pr_id: int = 1001,
    title: str = "Add retries",
    state: str = "open",
    head_sha: str = "a" * 40,
    merged_at: str | None = None,
    updated_at: str = "2026-10-26T10:00:00Z",
) -> dict[str, Any]:
    """Minimal pull_request payload with the fields the ingester reads (shape matches GitHub)."""
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "id": pr_id,
            "number": number,
            "title": title,
            "state": state,
            "user": {"login": "octocat"},
            "head": {"sha": head_sha, "ref": "feature"},
            "base": {"ref": "main"},
            "created_at": "2026-10-26T10:00:00Z",
            "merged_at": merged_at,
            "updated_at": updated_at,
        },
        "repository": {"id": 501, "full_name": "octo/sandbox"},
    }


@pytest.fixture
def pr_payload():
    return make_pr_payload


@pytest.fixture
def send(client):
    """POST a correctly signed delivery to the webhook endpoint."""

    async def _send(
        payload: dict[str, Any], *, event: str = "pull_request", delivery: str | None = None
    ) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": sign(SECRET, body),
            "Content-Type": "application/json",
        }
        return await client.post("/webhooks/github", content=body, headers=headers)

    return _send
