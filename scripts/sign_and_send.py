"""Send a correctly signed webhook to the ingester, the way GitHub would.

    uv run python scripts/sign_and_send.py services/ingester/tests/fixtures/opened.json
    uv run python scripts/sign_and_send.py payload.json --event pull_request
    printf '%s' "$BODY" | uv run python scripts/sign_and_send.py - --signature   # just print it

FILE is either a captured fixture ({"headers": {...}, "body": {...}}) or a raw payload. Raw payloads
are signed byte-for-byte; fixture bodies are re-serialised compactly first. The secret comes from
GITHUB_WEBHOOK_SECRET (environment or .env). Each send uses a fresh X-GitHub-Delivery unless
--delivery is given, so repeated sends aren't treated as duplicates.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/ingester/src"))
from ingester.signature import sign  # noqa: E402
from shipwatch_common.settings import get_settings  # noqa: E402


class Delivery(NamedTuple):
    event: str
    body: bytes


def load_delivery(raw: bytes, default_event: str) -> Delivery:
    """Turn a fixture file or a raw payload into (event, exact body bytes to sign and send)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return Delivery(default_event, raw)  # not JSON: send as-is (useful for testing 400s)
    if isinstance(data, dict) and set(data) == {"headers", "body"}:
        event = data["headers"].get("X-GitHub-Event") or default_event
        return Delivery(event, json.dumps(data["body"], separators=(",", ":")).encode())
    return Delivery(default_event, raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="fixture or payload JSON file, or - for stdin")
    parser.add_argument("--url", default="http://localhost:8000/webhooks/github")
    parser.add_argument("--event", default="pull_request", help="if the file doesn't say")
    parser.add_argument("--delivery", help="X-GitHub-Delivery (default: a new UUID)")
    parser.add_argument("--signature", action="store_true", help="only print the signature")
    args = parser.parse_args()

    raw = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
    delivery = load_delivery(raw, args.event)
    signature = sign(
        get_settings().github_webhook_secret.get_secret_value().encode(), delivery.body
    )
    if args.signature:
        print(signature)
        return

    request = urllib.request.Request(
        args.url,
        data=delivery.body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": delivery.event,
            "X-GitHub-Delivery": args.delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": signature,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            status, reply = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, reply = err.code, err.read()
    print(status, reply.decode())


if __name__ == "__main__":
    main()
