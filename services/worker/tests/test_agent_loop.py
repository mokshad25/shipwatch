"""The agent loop with a scripted LLM and recorded GitHub responses (SPEC_W3 D2)."""

import asyncio

import pytest
from agent.loop import Agent, Limits
from agent.toolbox import make_toolbox
from agent.trace import SKIPPED, Trace

REPO = "encode/httpx"
GOOD = {
    "factors": [{"kind": "failing_ci", "severity": 2, "evidence": "Python 3.13 failed"}],
    "summary": "CI fails on 3.13.",
    "confidence": 0.8,
}


@pytest.fixture
def recorded_github(gh_api, load_github):
    """Every endpoint the tools hit for encode/httpx#3783, from the recorded fixtures."""
    gh_api.get(f"/repos/{REPO}/pulls/3783").respond(json=load_github("pr_3783.json"))
    gh_api.get(f"/repos/{REPO}/pulls/3783/files").respond(json=load_github("pr_3783_files.json"))
    gh_api.get(f"/repos/{REPO}/commits").respond(json=[{"sha": "a"}, {"sha": "b"}])
    gh_api.get("/search/issues").respond(json={"total_count": 0, "items": []})
    sha = load_github("pr_3783.json")["head"]["sha"]
    gh_api.get(f"/repos/{REPO}/commits/{sha}/check-runs").respond(
        json=load_github("check_runs_failing.json")
    )
    return gh_api


def _agent(llm, github, **limits) -> Agent:
    trace = Trace(
        repo=REPO,
        pr_number=3783,
        provider="fake",
        model="fake",
        input_cost_per_mtok=2.0,
        output_cost_per_mtok=10.0,
    )
    return Agent(llm, make_toolbox(github, REPO, 3783), trace, Limits(**limits))


async def test_full_run_traces_every_llm_and_tool_call(
    github, recorded_github, fake_llm, llm_reply
):
    llm = fake_llm(
        llm_reply.tools(("get_diff", {})),
        llm_reply.tools(
            ("file_churn", {"paths": ["httpx/_client.py"]}),
            ("author_history", {"author": "kajinamit"}),
            ("ci_status", {}),
        ),
        llm_reply.text("CI is red on 3.13; the author is new."),
        llm_reply.text(GOOD, input_tokens=500, output_tokens=60),
    )
    agent = _agent(llm, github)

    assessment = await agent.run()

    assert assessment.factors[0].kind == "failing_ci"
    trace = agent.trace
    assert [(s.kind, s.name) for s in trace.steps] == [
        ("llm", "turn 1"),
        ("tool", "get_diff"),
        ("llm", "turn 2"),
        ("tool", "file_churn"),
        ("tool", "author_history"),
        ("tool", "ci_status"),
        ("llm", "turn 3"),
        ("llm", "assessment"),
    ]
    tools = {s.name: s for s in trace.steps if s.kind == "tool"}
    assert tools["get_diff"].summary == "2 files +61/-0, truncated"
    assert tools["file_churn"].args == {"paths": ["httpx/_client.py"]}
    assert tools["file_churn"].summary == "1 paths over 90d; top: httpx/_client.py=2"
    assert tools["author_history"].summary == "kajinamit: 0 merged, 0 reverted"
    assert tools["ci_status"].summary == "failing: 5 check runs (Python 3.13)"
    assert all(s.error is None for s in trace.steps)
    assert (trace.input_tokens, trace.output_tokens, trace.tool_calls) == (800, 120, 4)
    assert trace.cost_usd == pytest.approx((800 * 2 + 120 * 10) / 1e6)
    assert trace.latency_ms >= 0

    assert [t["function"]["name"] for t in llm.bound_tools] == [
        "get_diff",
        "file_churn",
        "author_history",
        "ci_status",
    ]
    # The assessment prompt carries the tool output and the model's notes as plain text.
    prompt = llm.calls[-1][-1].content
    assert "### get_diff({})" in prompt and "patch for httpx/_client.py truncated" in prompt
    assert '"state":"failing"' in prompt
    assert "CI is red on 3.13" in prompt


async def test_tool_calls_are_capped(github, recorded_github, fake_llm, llm_reply):
    four = [("get_diff", {}), ("ci_status", {}), ("get_diff", {}), ("ci_status", {})]
    llm = fake_llm(llm_reply.tools(*four), llm_reply.tools(*four), llm_reply.text(GOOD))
    agent = _agent(llm, github, max_tool_calls=6)

    await agent.run()

    tool_steps = [s for s in agent.trace.steps if s.kind == "tool"]
    assert len(tool_steps) == 8
    assert [s.error for s in tool_steps[6:]] == [SKIPPED, SKIPPED]
    assert agent.trace.tool_calls == 6
    # After the cap the loop goes straight to the assessment; no third tool turn.
    assert [s.name for s in agent.trace.steps if s.kind == "llm"] == [
        "turn 1",
        "turn 2",
        "assessment",
    ]


async def test_bad_arguments_and_unknown_tools_go_back_to_the_model(
    github, recorded_github, fake_llm, llm_reply
):
    llm = fake_llm(
        llm_reply.tools(("file_churn", {"paths": "not-a-list"}), ("delete_repo", {})),
        llm_reply.text("Nothing usable."),
        llm_reply.text(GOOD),
    )
    agent = _agent(llm, github)

    await agent.run()

    churn, unknown = (s for s in agent.trace.steps if s.kind == "tool")
    assert churn.error.startswith("invalid arguments")
    assert unknown.error == "unknown tool 'delete_repo'"
    tool_replies = [m.content for m in llm.calls[1] if m.type == "tool"]
    assert all(r.startswith("error: ") for r in tool_replies)


async def test_overall_timeout(github, recorded_github, fake_llm, llm_reply):
    async def slow():
        await asyncio.sleep(5)
        return llm_reply.text("too late")

    agent = _agent(fake_llm(slow), github, timeout_seconds=0.1)

    with pytest.raises(TimeoutError):
        await agent.run()

    [step] = agent.trace.steps
    assert step.name == "turn 1" and "CancelledError" in step.error


async def test_churn_window_ends_when_the_pr_was_opened(github, recorded_github):
    # encode/httpx#3783 was opened 2026-02-26T14:35:03Z; a merged PR assessed months later must
    # see the churn as it was then, not as of today.
    tools = make_toolbox(github, REPO, 3783)

    churn = await tools["file_churn"].run(tools["file_churn"].args(paths=["httpx/_client.py"]))

    assert (churn.since, churn.until) == ("2025-11-28T14:35:03Z", "2026-02-26T14:35:03Z")
    sent = recorded_github.calls.last.request.url.params
    assert (sent["since"], sent["until"]) == (churn.since, churn.until)
    assert sent["sha"] == churn.ref == "master"  # the PR's base branch


async def test_strict_structured_output_where_the_provider_supports_it(fake_llm, llm_reply):
    seen = {}

    class StrictFake(type(fake_llm())):
        def with_structured_output(self, schema, *, include_raw=False, strict=None, **kwargs):
            seen["strict"] = strict
            return super().with_structured_output(schema, include_raw=include_raw, **kwargs)

    llm = StrictFake(script=[llm_reply.text("done"), llm_reply.text(GOOD)])
    agent = Agent(llm, {}, Trace(repo=REPO, pr_number=1, provider="fake", model="fake"))

    await agent.run()

    assert seen == {"strict": True}
