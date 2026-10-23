import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from shipwatch_common.db import make_engine, make_sessionmaker
from shipwatch_common.logging import configure_logging, get_logger
from shipwatch_common.settings import get_settings

from ingester import webhooks

log = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="shipwatch-ingester", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Bind delivery_id to every log line of this request, log the request, and turn any
        unhandled exception into a generic 500 (traceback goes to the log, never the client)."""
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            delivery_id=request.headers.get("x-github-delivery"),
            github_event=request.headers.get("x-github-event"),
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", method=request.method, path=request.url.path)
            response = JSONResponse(
                {"detail": "internal server error"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(webhooks.router)
    return app


app = create_app()
