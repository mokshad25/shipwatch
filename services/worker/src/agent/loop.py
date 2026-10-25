"""The agent loop (SPEC_W3 D2–D3): gather evidence with tools, then return a RiskAssessment.

1. Tool phase: the model, with the tools bound, calls tools until it stops or reaches the cap.
2. Assessment phase: one structured-output call on a condensed prompt holding every tool result
   as text. The prompt has no tool-call blocks, so it works the same on every provider, and the
   evidence the model scores is exactly what the trace recorded.
"""

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from agent.assessment import RiskAssessment
from agent.prompts import FINAL_INSTRUCTION, SYSTEM_PROMPT
from agent.toolbox import AgentTool
from agent.trace import SKIPPED, Trace, TraceStep


class AssessmentParseError(Exception):
    """The model's assessment failed validation twice (first try and one retry)."""


class TokenBudgetExceeded(Exception):
    def __init__(self, used: int, budget: int) -> None:
        super().__init__(f"token budget exceeded: {used} > {budget}")
        self.used = used
        self.budget = budget


@dataclass(frozen=True)
class Limits:
    max_tool_calls: int = 6
    timeout_seconds: float = 60.0
    token_budget: int = 50_000


@dataclass
class Evidence:
    """Tool results in call order, rendered into the assessment prompt."""

    results: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    notes: str = ""

    def render(self, repo: str, pr_number: int) -> str:
        parts = [f"Pull request {repo}#{pr_number}. Tool results, in the order you called them:"]
        for name, args, output in self.results:
            parts.append(f"### {name}({json.dumps(args, sort_keys=True)})\n{output}")
        if not self.results:
            parts.append("(no tool results)")
        if self.notes:
            parts.append(f"### Your analysis\n{self.notes}")
        parts.append(FINAL_INSTRUCTION)
        return "\n\n".join(parts)


def _usage(step: TraceStep, message: Any) -> None:
    usage = getattr(message, "usage_metadata", None) or {}
    step.input_tokens = usage.get("input_tokens", 0)
    step.output_tokens = usage.get("output_tokens", 0)


def _text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(
        b.get("text", "")
        for b in message.content
        if isinstance(b, dict) and b.get("type") == "text"
    )


class Agent:
    def __init__(
        self,
        llm: BaseChatModel,
        tools: dict[str, AgentTool],
        trace: Trace,
        limits: Limits = Limits(),  # noqa: B008 (frozen dataclass)
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.trace = trace
        self.limits = limits

    async def run(self) -> RiskAssessment:
        """Raises TimeoutError, TokenBudgetExceeded, AssessmentParseError, or whatever an LLM or
        tool call raised; the trace keeps every step up to the failure."""
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                evidence = await self._gather()
                return await self._assess(evidence)
        finally:
            self.trace.latency_ms = round((time.perf_counter() - start) * 1000)

    def _check_budget(self) -> None:
        used = self.trace.input_tokens + self.trace.output_tokens
        if used > self.limits.token_budget:
            raise TokenBudgetExceeded(used, self.limits.token_budget)

    async def _gather(self) -> Evidence:
        model = self.llm.bind_tools([t.schema() for t in self.tools.values()])
        messages: list[BaseMessage] = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                f"Review pull request {self.trace.repo}#{self.trace.pr_number} for deployment risk."
            ),
        ]
        evidence = Evidence()
        calls = 0
        turn = 0
        while True:
            turn += 1
            with self.trace.step("llm", f"turn {turn}") as step:
                ai = await model.ainvoke(messages)
                _usage(step, ai)
                step.summary = (
                    f"{len(ai.tool_calls)} tool call(s): "
                    + ", ".join(c["name"] for c in ai.tool_calls)
                    if ai.tool_calls
                    else "done gathering"
                )
            self._check_budget()
            messages.append(ai)
            if not ai.tool_calls:
                evidence.notes = _text(ai)
                return evidence
            for call in ai.tool_calls:
                if calls >= self.limits.max_tool_calls:
                    # Not executed. The tool phase ends after this turn, and the assessment prompt
                    # carries no tool-call blocks, so these ids never need an answer.
                    self.trace.steps.append(
                        TraceStep(kind="tool", name=call["name"], args=call["args"], error=SKIPPED)
                    )
                    continue
                calls += 1
                output = await self._call_tool(call)
                evidence.results.append((call["name"], call["args"], output))
                messages.append(ToolMessage(output, tool_call_id=call["id"]))
            if calls >= self.limits.max_tool_calls:
                return evidence

    async def _call_tool(self, call: dict[str, Any]) -> str:
        """Run one tool call. Bad arguments or an unknown tool go back to the model as an error
        (it can correct itself); errors from GitHub propagate (D6 turns them into needs_review)."""
        with self.trace.step("tool", call["name"], call["args"]) as step:
            tool = self.tools.get(call["name"])
            if tool is None:
                step.error = f"unknown tool {call['name']!r}"
                return f"error: {step.error}"
            try:
                args = tool.args.model_validate(call["args"])
            except ValidationError as exc:
                step.error = f"invalid arguments: {exc.errors(include_url=False)}"[:500]
                return f"error: {step.error}"
            result = await tool.run(args)
            step.summary = tool.summarize(result)
            return result.model_dump_json()

    async def _assess(self, evidence: Evidence) -> RiskAssessment:
        # Strict mode where the provider has it (OpenAI, Groq): without it, gpt-oss on Groq was
        # seen echoing the schema's shape ({"description": "", "properties": {...}}) instead of
        # the object. Anthropic's json_schema mode is constrained already and takes no flag.
        strict = (
            {"strict": True}
            if "strict" in inspect.signature(self.llm.with_structured_output).parameters
            else {}
        )
        structured = self.llm.with_structured_output(
            RiskAssessment, method="json_schema", include_raw=True, **strict
        )
        messages: list[BaseMessage] = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(evidence.render(self.trace.repo, self.trace.pr_number)),
        ]
        for attempt in (1, 2):
            name = "assessment" if attempt == 1 else "assessment retry"
            with self.trace.step("llm", name) as step:
                out = await structured.ainvoke(messages)
                _usage(step, out["raw"])
                error = out["parsing_error"]
                parsed = out["parsed"]
                if error is None and parsed is not None:
                    step.summary = (
                        f"{len(parsed.factors)} factor(s), confidence {parsed.confidence}"
                    )
                else:
                    step.error = f"invalid assessment: {error or 'no output'}"[:500]
            self._check_budget()
            if step.error is None:
                return parsed
            # Feed the failure back as a user turn (an assistant turn can't be empty on every
            # provider, and the raw output may be).
            previous = _text(out["raw"]) if isinstance(out["raw"], AIMessage) else ""
            messages.append(
                HumanMessage(
                    f"Your previous output:\n{previous or '(empty)'}\n\n"
                    f"It did not validate against the schema:\n{error}\n"
                    "Return the corrected JSON only."
                )
            )
        raise AssessmentParseError(step.error)
