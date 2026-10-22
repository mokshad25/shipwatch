import asyncio
from logging.config import fileConfig

from alembic import context
from shipwatch_common.db import make_engine
from shipwatch_common.models import Base
from shipwatch_common.settings import DatabaseSettings
from sqlalchemy.engine import Connection

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tests pass a schema via `config.attributes["schema"]` to migrate an isolated, throwaway schema.
schema: str | None = config.attributes.get("schema")


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connect_args = {"server_settings": {"search_path": schema}} if schema else {}
    engine = make_engine(DatabaseSettings().database_url, connect_args=connect_args)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run against a database.")

asyncio.run(run_async_migrations())
