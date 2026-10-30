from demoapp import pricing


async def test_products(client):
    response = await client.get("/products")
    assert response.status_code == 200
    assert len(response.json()) == 8


async def test_create_and_get_order(client, logs):
    response = await client.post(
        "/orders",
        json={"items": [{"product_id": 1, "qty": 2}], "discount_code": "WELCOME10"},
    )
    assert response.status_code == 201
    order = response.json()
    assert (order["subtotal"], order["discount"], order["total"]) == ("49.80", "4.98", "44.82")

    fetched = await client.get(f"/orders/{order['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == order
    assert logs.events("warning") == []


async def test_unknown_discount_code_warns_but_places_the_order(client, logs):
    response = await client.post(
        "/orders", json={"items": [{"product_id": 2, "qty": 1}], "discount_code": "SAVE99"}
    )
    assert response.status_code == 201
    assert response.json()["discount"] == "0.00"
    (warning,) = [line for line in logs.lines if line["level"] == "warning"]
    assert warning["event"] == "unknown discount code"
    assert warning["code"] == "SAVE99"


async def test_out_of_stock_is_409_and_warns(client, logs):
    response = await client.post("/orders", json={"items": [{"product_id": 3, "qty": 5_000_000}]})
    assert response.status_code == 409
    assert logs.events("warning") == ["out of stock"]


async def test_missing_order_is_404_and_warns(client, logs):
    assert (await client.get("/orders/999")).status_code == 404
    assert logs.events("warning") == ["order not found"]


async def test_bad_input_is_422(client):
    assert (await client.post("/orders", json={"items": []})).status_code == 422
    unknown = await client.post("/orders", json={"items": [{"product_id": 99, "qty": 1}]})
    assert unknown.status_code == 422


async def test_every_line_carries_service_sha_and_request_id(client, logs):
    response = await client.get("/orders/999")
    request_id = response.headers["x-request-id"]
    # The test client's own httpx line is captured too; only demoapp's matter here.
    lines = [line for line in logs.lines if line["logger"].startswith("demoapp")]
    assert len(lines) == 2  # the warning and the request line
    for line in lines:
        assert line["service"] == "demoapp"
        assert line["sha"] == "abc123"
        assert line["request_id"] == request_id
    request_line = lines[-1]
    assert request_line["event"] == "request"
    assert (request_line["method"], request_line["path"], request_line["status"]) == (
        "GET",
        "/orders/999",
        404,
    )


async def test_slow_request_warns(settings, logs, client, app):
    settings.slow_ms = -1  # every request counts as slow
    await client.get("/products")
    assert logs.events("warning") == ["slow request"]


async def test_unhandled_error_is_500_with_a_structured_traceback(client, logs, monkeypatch):
    def broken(code):
        return code.strip()  # what a removed None check looks like

    monkeypatch.setattr(pricing, "discount_rate", broken)
    response = await client.post("/orders", json={"items": [{"product_id": 1, "qty": 1}]})

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    (error,) = [line for line in logs.lines if line["level"] == "error"]
    assert error["event"] == "unhandled error"
    exc = error["exception"][-1]
    assert exc["exc_type"] == "AttributeError"
    frames = [(f["filename"], f["name"]) for f in exc["frames"]]
    # Paths end in the repo path, which is what D4 matches against a PR's changed files.
    assert any(
        name.endswith("services/demoapp/src/demoapp/main.py") and fn == "create_order"
        for name, fn in frames
    )
    assert any(name.endswith("services/demoapp/src/demoapp/pricing.py") for name, _ in frames)
