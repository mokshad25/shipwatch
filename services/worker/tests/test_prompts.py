"""Versioned prompts (W5 D3): every version parses, and the agent sends the chosen one."""

import pytest
from agent.loop import Agent
from agent.prompts import Prompt, UnknownPromptVersion, load_prompt, versions
from agent.toolbox import make_toolbox
from agent.trace import Trace

GOOD = {"factors": [], "summary": "Docs only.", "confidence": 0.9}


@pytest.mark.parametrize("version", versions())
def test_every_version_has_both_sections(version):
    prompt = load_prompt(version)

    assert prompt.system and prompt.final
    assert "# Final instruction" not in prompt.system
    assert "risky_paths" in prompt.final  # the factor guide


def test_versions_and_unknown_version():
    assert {"v1", "v2"} <= set(versions())
    with pytest.raises(UnknownPromptVersion, match="no prompt 'v9'"):
        load_prompt("v9")


async def test_agent_sends_the_chosen_prompt(github, fake_llm, llm_reply):
    prompt = Prompt(version="vtest", system="SYSTEM TEXT", final="FINAL TEXT")
    llm = fake_llm(llm_reply.text("nothing to check"), llm_reply.text(GOOD))
    trace = Trace(repo="o/r", pr_number=1, provider="fake", model="fake")

    await Agent(llm, make_toolbox(github, "o/r", 1), trace, prompt=prompt).run()

    gather, assess = llm.calls
    assert gather[0].content == "SYSTEM TEXT" and assess[0].content == "SYSTEM TEXT"
    assert assess[1].content.endswith("FINAL TEXT")
    assert trace.prompt_version == "vtest"
