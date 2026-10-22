import json

from ingester.signature import sign, verify_signature

SECRET = b"test-secret"
BODY = json.dumps({"zen": "Keep it logically awesome."}).encode()


def headers(signature: str | None, event: str = "ping", delivery: str = "d-1") -> dict[str, str]:
    h = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery}
    if signature is not None:
        h["X-Hub-Signature-256"] = signature
    return h


async def test_valid_signature_is_accepted(client):
    resp = await client.post("/webhooks/github", content=BODY, headers=headers(sign(SECRET, BODY)))

    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}


async def test_wrong_signature_is_rejected(client):
    resp = await client.post(
        "/webhooks/github", content=BODY, headers=headers("sha256=" + "0" * 64)
    )

    assert resp.status_code == 401


async def test_missing_signature_header_is_rejected(client):
    resp = await client.post("/webhooks/github", content=BODY, headers=headers(None))

    assert resp.status_code == 401


async def test_signature_made_with_wrong_secret_is_rejected(client):
    resp = await client.post(
        "/webhooks/github", content=BODY, headers=headers(sign(b"other-secret", BODY))
    )

    assert resp.status_code == 401


async def test_signature_over_different_bytes_is_rejected(client):
    # Same JSON, different whitespace: the signature is over raw bytes, not parsed content.
    reformatted = json.dumps(json.loads(BODY), indent=2).encode()
    resp = await client.post(
        "/webhooks/github", content=reformatted, headers=headers(sign(SECRET, BODY))
    )

    assert resp.status_code == 401


def test_verify_signature_rejects_malformed_headers():
    assert verify_signature(SECRET, BODY, sign(SECRET, BODY))
    assert not verify_signature(SECRET, BODY, None)
    assert not verify_signature(SECRET, BODY, "")
    assert not verify_signature(SECRET, BODY, sign(SECRET, BODY).removeprefix("sha256="))
    assert not verify_signature(SECRET, BODY, "sha1=" + "0" * 40)
    assert not verify_signature(SECRET, BODY, "sha256=é")


async def test_missing_delivery_header_is_rejected(client):
    resp = await client.post(
        "/webhooks/github",
        content=BODY,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(SECRET, BODY)},
    )

    assert resp.status_code == 400
