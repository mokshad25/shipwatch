"""PR events -> verdict in Postgres, trace in MongoDB, summary on pr-verdicts (SPEC_W3 D7).

Real Postgres, MongoDB and Redpanda; the LLM is scripted and GitHub is replayed with respx.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import httpx
import openai
import pytest
from pymongo import AsyncMongoClient
from shipwatch_common.kafka import EventEnvelope, Producer
from shipwatch_common.models import ProcessedEvent, PullRequest, Repo, VerdictRecord
from sqlalchemy import func, select
from worker.processing import Source
from worker.verdicts import PrVerdicts

REPO = "encode/httpx"
PR_ID = 2_000_003_783
GOOD = {
    "factors": [{"kind": "failing_ci", "severity": 3, "evidence": "Python 3.13: failure"}],
    "summary": "CI is failing on 3.13.",
    "confidence": 0.9,
}


@pytest.fixture
def head_sha(load_github) -> str:
    return load_github("pr_3783.json")["head"]["sha"]


@pytest.fixture
def recorded_github(gh_api, load_github, head_sha):
    gh_api.get(f"/repos/{REPO}/pulls/3783").respond(json=load_github("pr_3783.json"))
    gh_api.get(f"/repos/{REPO}/pulls/3783/files").respond(json=load_github("pr_3783_files.json"))
    gh_api.get(f"/repos/{REPO}/commits/{head_sha}/check-runs").respond(
        json=load_github("check_runs_failing.json")
    )
    return gh_api


@pytest.fixture
async def pr_row(db, head_sha):
    """The repos / pull_requests rows the ingester would have written (verdicts reference them)."""
    now = datetime.now(UTC)
    async with db.begin() as session:
        session.add(Repo(id=1_000_001, full_name=REPO))
        await session.flush()
        session.add(
            PullRequest(
                id=PR_ID, repo_id=1_000_001, number=3783, title="t", author="kajinamit",
                state="open", head_sha=head_sha, base_branch="master", created_at=now,
                merged_at=None, updated_at=now,
            )
        )  # fmt: skip


@pytest.fixture
async def mongo():
    client = AsyncMongoClient("mongodb://localhost:27017", tz_aware=True)
    name = f"test_{uuid.uuid4().hex[:8]}"
    yield client, name
    await client.drop_database(name)
    await client.close()


@pytest.fixture
def make_pipeline(settings, github, mongo, make_topic, fake_llm):
    client, db_name = mongo
    verdict_settings = settings.model_copy(
        update={"pr_verdicts_topic": make_topic(1), "mongo_db": db_name, "llm_model": "fake"}
    )

    def _make(llm=None, **kw) -> PrVerdicts:
        producer = Producer(verdict_settings.kafka_bootstrap_servers)
        return PrVerdicts(verdict_settings, github, llm, client, producer)

    return _make


def _event(action: str, sha: str, i: int = 0) -> EventEnvelope:
    return EventEnvelope(
        delivery_id=f"d-{action}-{i}-{uuid.uuid4().hex[:6]}",
        type=f"pull_request.{action}",
        payload={
            "action": action,
            "number": 3783,
            "pull_request": {"id": PR_ID, "number": 3783, "head": {"sha": sha}},
            "repository": {"full_name": REPO},
        },
    )


SOURCE = Source("pr-events", 0, 0)


async def _verdicts(db) -> list[VerdictRecord]:
    async with db() as session:
        return list(await session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)))


async def _processed(db) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(ProcessedEvent))


def _scored_script(fake_llm, llm_reply):
    return fake_llm(llm_reply.tools(("ci_status", {})), llm_reply.text("red"), llm_reply.text(GOOD))


async def test_opened_stores_verdict_trace_and_publishes(
    db, pr_row, recorded_github, make_pipeline, mongo, consume_all, fake_llm, llm_reply, head_sha
):
    pipeline = make_pipeline(_scored_script(fake_llm, llm_reply))

    await pipeline.process(db, _event("opened", head_sha), SOURCE)

    [v] = await _verdicts(db)
    assert (v.pr_id, v.head_sha, v.level, v.score) == (PR_ID, head_sha, "medium", 30)
    assert v.assessment["factors"][0]["kind"] == "failing_ci"
    assert v.assessment["rules"] == ["serious_failing_ci"]
    assert v.tokens == 360 and v.trace_id == f"{PR_ID}:{head_sha}"
    assert await _processed(db) == 1

    client, db_name = mongo
    trace = await client[db_name]["traces"].find_one({"_id": v.trace_id})
    assert [s["name"] for s in trace["trace"]["steps"]] == [
        "turn 1",
        "ci_status",
        "turn 2",
        "assessment",
    ]
    assert trace["verdict"]["level"] == "medium"

    [msg] = consume_all(pipeline.settings.pr_verdicts_topic, 1)
    summary = json.loads(msg.value())
    assert msg.key() == REPO.encode()
    assert summary["verdict_id"] == v.id
    assert (summary["level"], summary["score"], summary["pr_number"]) == ("medium", 30, 3783)
    assert summary["top_factors"] == [{"kind": "failing_ci", "severity": 3}]


async def test_same_commit_again_republishes_without_running_the_agent(
    db, pr_row, recorded_github, make_pipeline, consume_all, fake_llm, llm_reply, head_sha
):
    llm = _scored_script(fake_llm, llm_reply)
    pipeline = make_pipeline(llm)
    await pipeline.process(db, _event("opened", head_sha, 1), SOURCE)
    calls = len(llm.calls)

    await pipeline.process(db, _event("opened", head_sha, 2), SOURCE)

    assert len(llm.calls) == calls  # no new LLM calls
    assert len(await _verdicts(db)) == 1
    assert await _processed(db) == 2
    first, again = consume_all(pipeline.settings.pr_verdicts_topic, 2)
    assert json.loads(first.value()) == json.loads(again.value())


async def test_synchronize_to_a_new_commit_adds_a_verdict(
    db, pr_row, recorded_github, make_pipeline, fake_llm, llm_reply, head_sha
):
    new_sha = "f" * 40
    script = [
        *_scored_script(fake_llm, llm_reply).script,
        *_scored_script(fake_llm, llm_reply).script,
    ]
    pipeline = make_pipeline(fake_llm(*script))

    await pipeline.process(db, _event("opened", head_sha), SOURCE)
    await pipeline.process(db, _event("synchronize", new_sha), SOURCE)

    assert [v.head_sha for v in await _verdicts(db)] == [head_sha, new_sha]


async def test_needs_review_is_stored_then_replaced_by_a_real_verdict(
    db, pr_row, recorded_github, make_pipeline, consume_all, fake_llm, llm_reply, head_sha
):
    await make_pipeline(None).process(db, _event("opened", head_sha, 1), SOURCE)

    [v] = await _verdicts(db)
    assert (v.level, v.score) == ("needs_review", None)
    assert v.reason == "LLM not configured (set LLM_PROVIDER and LLM_MODEL)"

    pipeline = make_pipeline(_scored_script(fake_llm, llm_reply))
    await pipeline.process(db, _event("opened", head_sha, 2), SOURCE)

    [v2] = await _verdicts(db)
    assert (v2.id, v2.level, v2.score, v2.reason) == (v.id, "medium", 30, None)
    levels = [
        json.loads(m.value())["level"] for m in consume_all(pipeline.settings.pr_verdicts_topic, 2)
    ]
    assert levels == ["needs_review", "medium"]


@pytest.mark.parametrize("action", ["closed", "edited", "labeled"])
async def test_other_actions_are_only_recorded(
    db, pr_row, make_pipeline, fake_llm, action, head_sha
):
    pipeline = make_pipeline(fake_llm())  # an empty script: any LLM call would fail the test

    await pipeline.process(db, _event(action, head_sha), SOURCE)

    assert await _verdicts(db) == []
    assert await _processed(db) == 1


async def test_worker_retries_a_rate_limit_then_stores_the_verdict(
    db, pr_row, recorded_github, make_pipeline, run_worker, produce, fake_llm, llm_reply, head_sha
):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    rate_limited = openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    llm = fake_llm(rate_limited, *_scored_script(fake_llm, llm_reply).script)
    pipeline = make_pipeline(llm)
    run_worker(process_fn=pipeline.process)

    produce(_event("opened", head_sha))

    async with asyncio.timeout(20):
        while not await _verdicts(db):  # noqa: ASYNC110 (polling the database)
            await asyncio.sleep(0.1)
    [v] = await _verdicts(db)
    assert (v.level, v.score) == ("medium", 30)
    assert len(llm.calls) == 4  # the rate-limited call, then the full run on attempt 2
