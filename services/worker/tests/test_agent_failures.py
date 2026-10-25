"""assess_pr fails safely: every agent failure becomes needs_review with a reason (SPEC_W3 D6)."""

import asyncio

import httpx
import openai
import pytest
from agent.run import format_run, main
from agent.service import AgentRateLimited, assess_pr
from worker.settings import WorkerSettings

REPO = "encode/httpx"
GOOD = {
    "factors": [{"kind": "failing_ci", "severity": 3, "evidence": "Python 3.13: failure"}],
    "summary": "CI is failing.",
    "confidence": 0.9,
}
_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _settings(**overrides) -> WorkerSettings:
    return WorkerSettings(_env_file=None, llm_model="fake-model", **overrides)


@pytest.fixture
def pr_3783(gh_api, load_github):
    gh_api.get(f"/repos/{REPO}/pulls/3783").respond(json=load_github("pr_3783.json"))
    gh_api.get(f"/repos/{REPO}/pulls/3783/files").respond(json=load_github("pr_3783_files.json"))
    sha = load_github("pr_3783.json")["head"]["sha"]
    return gh_api.get(f"/repos/{REPO}/commits/{sha}/check-runs")


async def test_success_is_scored_deterministically(
    github, pr_3783, fake_llm, llm_reply, load_github
):
    pr_3783.respond(json=load_github("check_runs_failing.json"))
    llm = fake_llm(llm_reply.tools(("ci_status", {})), llm_reply.text("red"), llm_reply.text(GOOD))

    run = await assess_pr(llm, github, REPO, 3783, _settings())

    v = run.verdict
    assert (v.level, v.score, v.rules) == ("medium", 30, ["serious_failing_ci"])
    assert v.head_sha == load_github("pr_3783.json")["head"]["sha"]
    assert v.reason is None and v.assessment.summary == "CI is failing."
    assert "verdict: medium  score 30" in format_run(run)


async def _needs_review(llm, github, settings=None, **kwargs):
    run = await assess_pr(llm, github, REPO, 3783, settings or _settings(), **kwargs)
    assert run.verdict.level == "needs_review" and run.verdict.score is None
    return run


async def test_overall_timeout(github, pr_3783, fake_llm, llm_reply):
    async def hang():
        await asyncio.sleep(5)

    run = await _needs_review(fake_llm(hang), github, _settings(agent_timeout_seconds=0.1))

    assert run.verdict.reason == "agent timed out after 0.1s"
    assert run.verdict.head_sha is not None  # known from the deterministic diff fetch


async def test_llm_timeout(github, pr_3783, fake_llm):
    run = await _needs_review(fake_llm(openai.APITimeoutError(request=_REQ)), github)

    assert run.verdict.reason.startswith("LLM unavailable: APITimeoutError")


async def test_llm_connection_error(github, pr_3783, fake_llm):
    run = await _needs_review(fake_llm(openai.APIConnectionError(request=_REQ)), github)

    assert run.verdict.reason.startswith("LLM unavailable: APIConnectionError")


def _rate_limit() -> openai.RateLimitError:
    return openai.RateLimitError(
        "Rate limit reached", response=httpx.Response(429, request=_REQ), body=None
    )


async def test_llm_rate_limit_is_retryable_while_attempts_remain(github, pr_3783, fake_llm):
    with pytest.raises(AgentRateLimited):
        await assess_pr(fake_llm(_rate_limit()), github, REPO, 3783, _settings(), can_retry=True)


async def test_llm_rate_limit_on_last_attempt_needs_review(github, pr_3783, fake_llm):
    run = await _needs_review(fake_llm(_rate_limit()), github, can_retry=False, attempt=3)

    assert run.verdict.reason.startswith("rate limited after 3 attempt(s)")


async def test_github_rate_limit_counts_as_a_rate_limit(github, pr_3783, fake_llm, llm_reply):
    pr_3783.respond(403, json={"message": "API rate limit exceeded"},
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})  # fmt: skip
    llm = fake_llm(llm_reply.tools(("ci_status", {})))

    with pytest.raises(AgentRateLimited):
        await assess_pr(llm, github, REPO, 3783, _settings(), can_retry=True)


async def test_tool_error(github, pr_3783, fake_llm, llm_reply):
    pr_3783.respond(502, json={"message": "Bad Gateway"})
    llm = fake_llm(llm_reply.tools(("ci_status", {})))

    run = await _needs_review(llm, github)

    assert run.verdict.reason == "GitHub tool error: GitHub API 502: Bad Gateway"
    [tool] = [s for s in run.trace.steps if s.kind == "tool"]
    assert tool.name == "ci_status" and "502" in tool.error


async def test_pr_not_found(github, gh_api, fake_llm):
    gh_api.get(f"/repos/{REPO}/pulls/3783").respond(404, json={"message": "Not Found"})

    run = await _needs_review(fake_llm(), github)

    assert run.verdict.reason == "GitHub tool error: GitHub API 404: Not Found"
    assert run.verdict.head_sha is None and run.trace.steps == []


async def test_malformed_output_twice(github, pr_3783, fake_llm, llm_reply):
    llm = fake_llm(llm_reply.text("done"), llm_reply.text("{"), llm_reply.text("still not json"))

    run = await _needs_review(llm, github)

    assert run.verdict.reason.startswith("model output failed validation twice")


async def test_token_budget(github, pr_3783, fake_llm, llm_reply):
    # Each scripted reply costs 120 tokens; the budget allows one.
    llm = fake_llm(llm_reply.tools(("get_diff", {})), llm_reply.tools(("get_diff", {})))

    run = await _needs_review(llm, github, _settings(llm_token_budget=150))

    assert run.verdict.reason == "token budget exceeded: 240 > 150"
    assert [s.name for s in run.trace.steps] == ["turn 1", "get_diff", "turn 2"]


async def test_unexpected_error(github, pr_3783, fake_llm):
    run = await _needs_review(fake_llm(KeyError("choices")), github)

    assert run.verdict.reason == "unexpected error: KeyError: 'choices'"


def test_cli_refuses_to_run_without_a_model(monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL", "")

    assert main(["encode/httpx", "1"]) == 2
    assert "set LLM_MODEL" in capsys.readouterr().err


async def test_rate_limit_carries_the_providers_retry_after(github, pr_3783, fake_llm):
    limited = openai.RateLimitError(
        "slow down",
        response=httpx.Response(429, request=_REQ, headers={"retry-after": "15.2"}),
        body=None,
    )

    with pytest.raises(AgentRateLimited) as err:
        await assess_pr(fake_llm(limited), github, REPO, 3783, _settings(), can_retry=True)

    assert err.value.retry_after == 15.2


def test_llm_max_retries_setting(monkeypatch):
    from agent.llm import make_chat_model
    from pydantic import SecretStr

    settings = _settings(llm_provider="groq", groq_api_key=SecretStr("k"), llm_max_retries=6)
    llm = make_chat_model(settings)

    assert llm.max_retries == 6


async def test_verdict_log_keeps_the_verdict_level(github, pr_3783, fake_llm, llm_reply, capsys):
    # `level` is structlog's log-level key; logging the verdict's level under it got overwritten
    # with "info" in the JSON output. It goes under verdict_level instead.
    import json as _json
    import logging

    from shipwatch_common.logging import configure_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    configure_logging("INFO")  # JSON lines on stdout, as in the containers
    try:
        llm = fake_llm(llm_reply.text("ok"), llm_reply.text(GOOD))
        await assess_pr(llm, github, REPO, 3783, _settings())
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)

    out = capsys.readouterr().out.splitlines()
    lines = [_json.loads(x) for x in out if x.startswith("{")]
    [entry] = [x for x in lines if x.get("event") == "agent verdict"]
    assert (entry["level"], entry["verdict_level"], entry["score"]) == ("info", "medium", 30)
