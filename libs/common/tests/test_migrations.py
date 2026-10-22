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
