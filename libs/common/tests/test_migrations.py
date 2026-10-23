"""Migrations run cleanly against real Postgres and match the SQLAlchemy models."""

import asyncio
import uuid
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from shipwatch_common.db import make_engine
from shipwatch_common.models import Base
from shipwatch_common.settings import DatabaseSettings
from sqlalchemy import text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(schema: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["schema"] = schema
    cfg.attributes["configure_logger"] = False
    return cfg


async def run_sql(sql: str) -> None:
    engine = make_engine(DatabaseSettings().database_url)
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def schema_diff(schema: str) -> list:
    engine = make_engine(
        DatabaseSettings().database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    await engine.dispose()
    return diff


# Sync test on purpose: Alembic's env.py calls asyncio.run(), which can't nest in a running loop.
def test_upgrade_downgrade_roundtrip_matches_models():
    schema = f"mig_{uuid.uuid4().hex[:12]}"
    asyncio.run(run_sql(f'CREATE SCHEMA "{schema}"'))
    try:
        cfg = alembic_config(schema)
        command.upgrade(cfg, "head")
        assert asyncio.run(schema_diff(schema)) == []

        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        assert asyncio.run(schema_diff(schema)) == []
    finally:
        asyncio.run(run_sql(f'DROP SCHEMA "{schema}" CASCADE'))


def test_0003_backfills_updated_at_from_newest_stored_payload():
    schema = f"mig_{uuid.uuid4().hex[:12]}"
    asyncio.run(run_sql(f'CREATE SCHEMA "{schema}"'))
    try:
        cfg = alembic_config(schema)
        command.upgrade(cfg, "0002")
        for sql in (
            f"INSERT INTO \"{schema}\".repos VALUES (1, 'o/r')",
            f'INSERT INTO "{schema}".pull_requests VALUES '
            "(10, 1, 1, 't', 'a', 'closed', 'sha', 'sandbox', '2026-10-01T00:00:00Z', NULL)",
            f'INSERT INTO "{schema}".webhook_events (delivery_id, event_type, action, payload) '
            "VALUES ('d1', 'pull_request', 'opened', "
            '\'{"pull_request": {"id": 10, "updated_at": "2026-10-01T00:00:00Z"}}\'), '
            "('d2', 'pull_request', 'closed', "
            '\'{"pull_request": {"id": 10, "updated_at": "2026-10-02T00:00:00Z"}}\')',
        ):
            asyncio.run(run_sql(sql))
        command.upgrade(cfg, "0003")

        async def updated_at():
            engine = make_engine(DatabaseSettings().database_url)
            async with engine.connect() as conn:
                value = await conn.scalar(
                    text(f'SELECT updated_at::text FROM "{schema}".pull_requests WHERE id = 10')
                )
            await engine.dispose()
            return value

        assert asyncio.run(updated_at()).startswith("2026-10-02")
    finally:
        asyncio.run(run_sql(f'DROP SCHEMA "{schema}" CASCADE'))
