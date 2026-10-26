import random
from collections import Counter

import httpx
from demoapp.load import pick_request, run


def test_mix_matches_the_spec():
    rng = random.Random(7)
    kinds = Counter(pick_request(rng, [1, 2, 3])[0] for _ in range(20_000))
    shares = {kind: n / 20_000 for kind, n in kinds.items()}
    assert abs(shares["create_order"] - 0.60) < 0.02
    assert abs(shares["get_order"] - 0.25) < 0.02
    assert abs(shares["list_products"] - 0.10) < 0.02
    assert abs(shares["missing_order"] - 0.05) < 0.01


def test_first_requests_create_orders_before_any_exist():
    assert pick_request(random.Random(0), [])[0] == "create_order"


async def test_holds_the_rate_against_the_app(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        stats = await run(client, rate=20, duration=1.0, seed=1)

    assert stats.sent == 20
    assert 0.95 < stats.elapsed < 1.3
    assert sum(stats.statuses.values()) == 20
    assert "500" not in stats.statuses
