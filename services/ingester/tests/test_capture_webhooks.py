import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/capture_webhooks.py"
spec = importlib.util.spec_from_file_location("capture_webhooks", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("pull_request", {"action": "opened", "pull_request": {"merged": False}}, "opened"),
        ("pull_request", {"action": "synchronize", "pull_request": {}}, "synchronize"),
        ("pull_request", {"action": "closed", "pull_request": {"merged": False}}, "closed"),
        ("pull_request", {"action": "closed", "pull_request": {"merged": True}}, "merged"),
        ("ping", {"zen": "hi"}, "ping_unknown"),
    ],
)
def test_fixture_name(event, payload, expected):
    assert capture.fixture_name(event, payload) == expected
