async def test_healthz_returns_ok(client):
    resp = await client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
