"""Replay real GitHub deliveries (captured with scripts/capture_webhooks.py) through the endpoint.

Bodies are re-signed with the test secret by the `send` fixture; no real secret is involved.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from shipwatch_common.models import PullRequest, Repo, WebhookEvent
from sqlalchemy import func, select

FIXTURES = Path(__file__).parent / "fixtures"
# The order GitHub sent them in during one `make sandbox-pr` run.
LIFECYCLE = ["opened", "synchronize", "edited", "closed", "reopened", "merged"]


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


async def replay(send, fixture: dict[str, Any]):
    headers = fixture["headers"]
    return await send(
        fixture["body"], event=headers["X-GitHub-Event"], delivery=headers["X-GitHub-Delivery"]
    )


async def count(db, model) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def get_pr(db, pr_id: int) -> PullRequest:
    async with db() as session:
        return await session.get(PullRequest, pr_id)


@pytest.mark.parametrize("name", LIFECYCLE)
def test_fixture_is_a_real_pull_request_delivery(name):
    fixture = load(name)

    assert fixture["headers"]["X-GitHub-Event"] == "pull_request"
    assert fixture["headers"]["X-GitHub-Delivery"]
    assert fixture["body"]["pull_request"]["base"]["ref"] == "sandbox"


async def test_lifecycle_replay_tracks_pull_request_state(db, send):
    fixtures = {name: load(name) for name in LIFECYCLE}
    opened_pr = fixtures["opened"]["body"]["pull_request"]
    pr_id = opened_pr["id"]

    resp = await replay(send, fixtures["opened"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    assert (pr.state, pr.merged_at) == ("open", None)
    assert pr.number == opened_pr["number"]
    assert pr.title == opened_pr["title"]
    assert pr.author == opened_pr["user"]["login"]
    assert pr.base_branch == "sandbox"
    assert pr.head_sha == opened_pr["head"]["sha"]
    assert pr.created_at == datetime.fromisoformat(opened_pr["created_at"])
    async with db() as session:
        repo = await session.get(Repo, fixtures["opened"]["body"]["repository"]["id"])
    assert repo.full_name == fixtures["opened"]["body"]["repository"]["full_name"]

    resp = await replay(send, fixtures["synchronize"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    assert pr.head_sha == fixtures["synchronize"]["body"]["pull_request"]["head"]["sha"]
    assert pr.head_sha != opened_pr["head"]["sha"]
    assert (pr.state, pr.merged_at) == ("open", None)

    resp = await replay(send, fixtures["edited"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    assert pr.title == fixtures["edited"]["body"]["pull_request"]["title"]
    assert pr.title != opened_pr["title"]
    assert (pr.state, pr.merged_at) == ("open", None)

    resp = await replay(send, fixtures["closed"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    assert (pr.state, pr.merged_at) == ("closed", None)  # closed without merging

    resp = await replay(send, fixtures["reopened"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    assert (pr.state, pr.merged_at) == ("open", None)

    resp = await replay(send, fixtures["merged"])
    assert resp.status_code == 202
    pr = await get_pr(db, pr_id)
    merged_at = fixtures["merged"]["body"]["pull_request"]["merged_at"]
    assert pr.state == "closed"
    assert pr.merged_at == datetime.fromisoformat(merged_at)

    # One row per delivery, one repo, one PR across the whole lifecycle.
    assert await count(db, WebhookEvent) == len(LIFECYCLE)
    assert await count(db, PullRequest) == 1
    assert await count(db, Repo) == 1
    async with db() as session:
        rows = (await session.execute(select(WebhookEvent).order_by(WebhookEvent.id))).scalars()
        stored = [(e.delivery_id, e.action) for e in rows]
    assert stored == [
        (fixtures[n]["headers"]["X-GitHub-Delivery"], fixtures[n]["body"]["action"])
        for n in LIFECYCLE
    ]


async def test_replaying_every_fixture_twice_keeps_one_row_each(db, send):
    for name in LIFECYCLE:
        assert (await replay(send, load(name))).status_code == 202
    before = await get_pr(db, load("merged")["body"]["pull_request"]["id"])

    for name in LIFECYCLE:
        resp = await replay(send, load(name))
        assert (resp.status_code, resp.json()) == (200, {"status": "duplicate"})

    assert await count(db, WebhookEvent) == len(LIFECYCLE)
    assert await count(db, PullRequest) == 1
    # Replaying old deliveries (e.g. "opened") must not roll the PR back to an earlier state.
    after = await get_pr(db, before.id)
    assert (after.state, after.merged_at, after.title, after.head_sha) == (
        before.state,
        before.merged_at,
        before.title,
        before.head_sha,
    )
