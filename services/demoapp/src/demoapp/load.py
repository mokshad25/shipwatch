"""Open-loop load generator for demoapp (docs/SPEC_W4.md, "Load").

Request i goes out at start + i/rate whether or not earlier ones have answered, so a slow server
can't lower the offered rate.
"""

import argparse
import asyncio
import random
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

VALID_CODES = ["WELCOME10", "SPRING15", "VIP25"]
PRODUCT_IDS = list(range(1, 9))
MISSING_ORDER_ID = 10_000_000


@dataclass
class Stats:
    sent: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    kinds: Counter[str] = field(default_factory=Counter)
    elapsed: float = 0.0

    @property
    def rate(self) -> float:
        return self.sent / self.elapsed if self.elapsed else 0.0


def pick_request(rng: random.Random, order_ids: list[int]) -> tuple[str, str, str, dict | None]:
    """(kind, method, path, json body) for the next request, following the mix in the spec."""
    r = rng.random()
    if r < 0.60 or not order_ids:
        items = [
            {"product_id": rng.choice(PRODUCT_IDS), "qty": rng.randint(1, 3)}
            for _ in range(rng.randint(1, 3))
        ]
        body: dict = {"items": items}
        r = rng.random()
        if r < 0.10:
            body["discount_code"] = rng.choice(VALID_CODES)
        elif r < 0.13:
            body["discount_code"] = rng.choice(["SAVE99", "FREESHIP", "BLACKFRIDAY"])
        elif r < 0.15:
            items[0]["qty"] = 5_000_000
        return "create_order", "POST", "/orders", body
    if r < 0.85:
        return "get_order", "GET", f"/orders/{rng.choice(order_ids)}", None
    if r < 0.95:
        return "list_products", "GET", "/products", None
    return "missing_order", "GET", f"/orders/{MISSING_ORDER_ID}", None


async def run(
    client: httpx.AsyncClient, rate: float, duration: float, seed: int | None = None
) -> Stats:
    rng = random.Random(seed)
    stats = Stats()
    order_ids: list[int] = []
    tasks: set[asyncio.Task] = set()

    async def send(kind: str, method: str, path: str, body: dict | None) -> None:
        try:
            response = await client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            stats.statuses[type(exc).__name__] += 1
            return
        stats.statuses[str(response.status_code)] += 1
        if kind == "create_order" and response.status_code == 201:
            order_ids.append(response.json()["id"])

    loop = asyncio.get_running_loop()
    start = loop.time()
    total = int(rate * duration)
    for i in range(total):
        delay = start + i / rate - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        kind, method, path, body = pick_request(rng, order_ids)
        stats.sent += 1
        stats.kinds[kind] += 1
        task = asyncio.create_task(send(kind, method, path, body))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    # The last request is sent at (total-1)/rate; the run still lasts the full duration.
    await asyncio.sleep(max(0.0, start + duration - loop.time()))
    stats.elapsed = loop.time() - start
    if tasks:
        await asyncio.wait(tasks, timeout=30)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8100")
    parser.add_argument("--rate", type=float, default=20.0, help="requests per second")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    async def _main() -> Stats:
        limits = httpx.Limits(max_connections=100)
        async with httpx.AsyncClient(base_url=args.url, timeout=10, limits=limits) as client:
            return await run(client, args.rate, args.duration, args.seed)

    began = time.monotonic()
    print(f"load: {args.rate:g} req/s for {args.duration:g}s against {args.url}")
    stats = asyncio.run(_main())
    print(
        f"sent {stats.sent} in {stats.elapsed:.1f}s ({stats.rate:.1f} req/s), "
        f"done after {time.monotonic() - began:.1f}s"
    )
    print("statuses:", dict(sorted(stats.statuses.items())))
    print("requests:", dict(sorted(stats.kinds.items())))


if __name__ == "__main__":
    main()
