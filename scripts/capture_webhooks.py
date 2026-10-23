"""Record real GitHub pull_request deliveries as test fixtures, and relay them to the ingester.

    make capture                      # terminal 1: listens on :8001
    make forward FORWARD_PORT=8001    # terminal 2: gh webhook forward -> this relay
    make sandbox-pr                   # terminal 3: generates the deliveries

Each delivery is saved verbatim as services/ingester/tests/fixtures/<name>.json with shape
{"headers": {"X-GitHub-Event", "X-GitHub-Delivery"}, "body": <payload>}. <name> is the PR action,
or "merged" for a closed PR with merged=true. Existing files are never overwritten. The signature
header is not saved: tests re-sign with the test secret.
"""

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "services/ingester/tests/fixtures"
KEPT_HEADERS = ("X-GitHub-Event", "X-GitHub-Delivery")
RELAYED_HEADERS = (*KEPT_HEADERS, "X-Hub-Signature-256", "Content-Type", "User-Agent")


def fixture_name(event: str | None, payload: dict[str, Any]) -> str:
    """File stem for a delivery: the action, or "merged" for a merged close."""
    action = payload.get("action") or "unknown"
    if event != "pull_request":
        return f"{event}_{action}"
    if action == "closed" and payload.get("pull_request", {}).get("merged"):
        return "merged"
    return action


def make_handler(target: str, out_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._save(body)
            status, reply = self._relay(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(reply)

        def _save(self, body: bytes) -> None:
            try:
                payload = json.loads(body)
            except ValueError:
                print("not JSON; not saved")
                return
            path = out_dir / f"{fixture_name(self.headers.get('X-GitHub-Event'), payload)}.json"
            if path.exists():
                print(f"exists, not overwritten: {path.name}")
                return
            fixture = {"headers": {h: self.headers.get(h) for h in KEPT_HEADERS}, "body": payload}
            path.write_text(json.dumps(fixture, indent=2) + "\n")
            print(f"saved {path.name}")

        def _relay(self, body: bytes) -> tuple[int, bytes]:
            headers = {h: v for h in RELAYED_HEADERS if (v := self.headers.get(h))}
            req = urllib.request.Request(target, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as err:
                return err.code, err.read()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--target", default="http://localhost:8000/webhooks/github")
    args = parser.parse_args()

    FIXTURES.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.target, FIXTURES))
    print(f"capturing on :{args.port} -> {args.target}, saving to {FIXTURES}")
    server.serve_forever()


if __name__ == "__main__":
    main()
