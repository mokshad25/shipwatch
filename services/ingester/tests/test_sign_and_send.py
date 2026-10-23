import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("sign_and_send", ROOT / "scripts/sign_and_send.py")
sign_and_send = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sign_and_send)

FIXTURE = Path(__file__).parent / "fixtures/opened.json"


def test_fixture_file_uses_its_event_and_compact_body():
    delivery = sign_and_send.load_delivery(FIXTURE.read_bytes(), "ping")

    assert delivery.event == "pull_request"
    assert json.loads(delivery.body) == json.loads(FIXTURE.read_text())["body"]
    assert b"\n" not in delivery.body


@pytest.mark.parametrize("raw", [b'{"zen": "hi"}\n', b"{not json"])
def test_raw_payload_is_sent_byte_for_byte(raw):
    delivery = sign_and_send.load_delivery(raw, "ping")

    assert delivery == ("ping", raw)


async def test_what_the_script_sends_is_accepted(db, client):
    delivery = sign_and_send.load_delivery(FIXTURE.read_bytes(), "ping")
    secret = sign_and_send.get_settings().github_webhook_secret.get_secret_value().encode()

    resp = await client.post(
        "/webhooks/github",
        content=delivery.body,
        headers={
            "X-GitHub-Event": delivery.event,
            "X-GitHub-Delivery": "d-script",
            "X-Hub-Signature-256": sign_and_send.sign(secret, delivery.body),
        },
    )

    assert (resp.status_code, resp.json()) == (202, {"status": "accepted"})
