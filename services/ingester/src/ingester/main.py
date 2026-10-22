import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from shipwatch_common.db import make_engine, make_sessionmaker
from shipwatch_common.settings import get_settings

from ingester import webhooks


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="shipwatch-ingester", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(webhooks.router)
    return app


app = create_app()
