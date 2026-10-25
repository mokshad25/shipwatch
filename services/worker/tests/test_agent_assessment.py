"""Structured RiskAssessment: good, bad and partial model output, with one retry (SPEC_W3 D3)."""

import pytest
from agent.loop import Agent, AssessmentParseError
from agent.trace import Trace

GOOD = {
    "factors": [
        {"kind": "migration", "severity": 3, "evidence": "adds alembic/versions/0007.py"},
        {"kind": "missing_tests", "severity": 1, "evidence": "no files under tests/"},
    ],
    "summary": "Adds a migration without tests.",
    "confidence": 0.7,
}


def _agent(llm) -> Agent:
    # No tools: the scripted model answers turn 1 with text, so the run goes straight to the
    # assessment and only the structured-output behaviour is under test.
    return Agent(llm, {}, Trace(repo="octo/sandbox", pr_number=1, provider="fake", model="fake"))


def _assessment_steps(agent: Agent):
    return [s for s in agent.trace.steps if s.name.startswith("assessment")]


async def test_good_output(fake_llm, llm_reply):
    agent = _agent(fake_llm(llm_reply.text("done"), llm_reply.text(GOOD)))

    assessment = await agent.run()

    assert [f.kind for f in assessment.factors] == ["migration", "missing_tests"]
    assert assessment.confidence == 0.7
    [step] = _assessment_steps(agent)
    assert step.error is None and step.summary == "2 factor(s), confidence 0.7"


async def test_empty_factor_list_is_valid(fake_llm, llm_reply):
    low = {"factors": [], "summary": "Docs only.", "confidence": 0.9}
    agent = _agent(fake_llm(llm_reply.text("done"), llm_reply.text(low)))

    assert (await agent.run()).factors == []


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("Sure! The PR looks risky.", id="not-json"),
        pytest.param({**GOOD, "factors": [{**GOOD["factors"][0], "kind": "vibes"}]}, id="bad-kind"),
        pytest.param({**GOOD, "factors": [{**GOOD["factors"][0], "severity": 5}]}, id="severity-5"),
        pytest.param({**GOOD, "confidence": 1.5}, id="confidence>1"),
    ],
)
async def test_bad_output_is_retried_once_with_the_error(fake_llm, llm_reply, bad):
    llm = fake_llm(llm_reply.text("done"), llm_reply.text(bad), llm_reply.text(GOOD))
    agent = _agent(llm)

    assessment = await agent.run()

    assert assessment.summary == GOOD["summary"]
    first, retry = _assessment_steps(agent)
    assert first.error.startswith("invalid assessment") and retry.error is None
    feedback = llm.calls[-1][-1].content
    assert "did not validate" in feedback and "validation error" in feedback


@pytest.mark.parametrize(
    "partial",
    [
        pytest.param({"factors": GOOD["factors"], "summary": "x"}, id="no-confidence"),
        pytest.param({"factors": [{"kind": "migration", "severity": 3}], "summary": "x",
                      "confidence": 0.5}, id="factor-without-evidence"),
        pytest.param('{"factors": [], "summary": "cut off', id="truncated-json"),
    ],
)  # fmt: skip
async def test_partial_output_is_retried(fake_llm, llm_reply, partial):
    agent = _agent(fake_llm(llm_reply.text("done"), llm_reply.text(partial), llm_reply.text(GOOD)))

    assert (await agent.run()).confidence == 0.7
    assert len(_assessment_steps(agent)) == 2


async def test_bad_twice_raises_and_keeps_the_trace(fake_llm, llm_reply):
    agent = _agent(fake_llm(llm_reply.text("done"), llm_reply.text("nope"), llm_reply.text("no")))

    with pytest.raises(AssessmentParseError):
        await agent.run()

    assert [s.error is not None for s in _assessment_steps(agent)] == [True, True]
    assert agent.trace.input_tokens == 300  # all three calls counted
