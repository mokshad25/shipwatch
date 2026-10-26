import asyncio
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import structlog
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from shipwatch_common.logging import configure_logging, get_logger

from demoapp import pricing
from demoapp.catalog import Store
from demoapp.logship import KafkaLogHandler
from demoapp.settings import DemoSettings

log = get_logger(__name__)


class OrderLine(BaseModel):
    product_id: int
    qty: int = Field(ge=1)


class OrderIn(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=20)
    discount_code: str | None = None


def create_app(
    settings: DemoSettings | None = None,
    extra_handlers: tuple[logging.Handler, ...] = (),
) -> FastAPI:
    settings = settings or DemoSettings()
    shipper = (
        KafkaLogHandler(
            settings.kafka_bootstrap_servers, settings.logs_topic, key=settings.service_name
        )
        if settings.ship_logs
        else None
    )
    configure_logging(
        settings.log_level,
        static_fields={"service": settings.service_name, "sha": settings.git_sha},
        extra_handlers=(*extra_handlers, *([shipper] if shipper else [])),
    )
    store = Store()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("startup", products=len(store.products))
        yield
        log.info("shutdown", orders=len(store.orders))
        if shipper is not None:
            logging.getLogger().removeHandler(shipper)
            shipper.close()

    app = FastAPI(title="demoapp", lifespan=lifespan)
    app.state.store = store
    app.state.shipper = shipper

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Bind request_id to every line of this request, log the request, and turn an unhandled
        exception into a generic 500 with its traceback in the log."""
        request_id = uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", method=request.method, path=request.url.path)
            response = JSONResponse(
                {"detail": "internal server error"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        if duration_ms > settings.slow_ms:
            log.warning("slow request", path=request.url.path, duration_ms=duration_ms)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    async def jitter() -> None:
        """Every so often a request is slow, like on a real service."""
        if random.random() < settings.jitter_rate:
            await asyncio.sleep(random.uniform(0, settings.max_jitter_ms) / 1000)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "sha": settings.git_sha}

    @app.get("/products")
    async def list_products() -> list[dict]:
        await jitter()
        return [
            {"id": p.id, "name": p.name, "price": str(p.price), "stock": p.stock}
            for p in store.products.values()
        ]

    @app.post("/orders", status_code=status.HTTP_201_CREATED)
    async def create_order(order: OrderIn) -> dict:
        await jitter()
        lines: list[tuple[Decimal, int]] = []
        for line in order.items:
            product = store.products.get(line.product_id)
            if product is None:
                raise HTTPException(422, f"unknown product {line.product_id}")
            if line.qty > product.stock:
                log.warning(
                    "out of stock", product_id=product.id, requested=line.qty, stock=product.stock
                )
                raise HTTPException(409, f"only {product.stock} of product {product.id} left")
            lines.append((product.price, line.qty))

        amount = pricing.subtotal(lines)
        code = order.discount_code
        try:
            discount = pricing.apply_discount(amount, code)
        except pricing.UnknownDiscountCode:
            log.warning("unknown discount code", code=code)
            code, discount = None, Decimal("0.00")

        for line in order.items:
            store.products[line.product_id].stock -= line.qty
        order_id = store.next_order_id()
        stored = {
            "id": order_id,
            "items": [line.model_dump() for line in order.items],
            "discount_code": code,
            "subtotal": str(amount),
            "discount": str(discount),
            "total": str(amount - discount),
        }
        store.orders[order_id] = stored
        log.info("order created", order_id=order_id, total=stored["total"])
        return stored

    @app.get("/orders/{order_id}")
    async def get_order(order_id: int) -> dict:
        await jitter()
        order = store.orders.get(order_id)
        if order is None:
            log.warning("order not found", order_id=order_id)
            raise HTTPException(404, "order not found")
        return order

    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory demoapp.main:app`."""
    return create_app()
