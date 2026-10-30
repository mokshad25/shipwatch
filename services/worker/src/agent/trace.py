"""What the agent did on one PR: every LLM and tool call, with tokens and latency (SPEC_W3 D2)."""

import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


def _now() -> datetime:
    return datetime.now(UTC)


class TraceStep(BaseModel):
    kind: Literal["llm", "tool"]
    name: str  # tool name, or the LLM call's role ("turn 1", "assessment")
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None


class Trace(BaseModel):
    repo: str
    pr_number: int
    provider: str
    model: str
    prompt_version: str = "v1"
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    started_at: datetime = Field(default_factory=_now)
    latency_ms: int = 0
    steps: list[TraceStep] = Field(default_factory=list)

    @computed_field
    @property
    def input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @computed_field
    @property
    def output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @computed_field
    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.kind == "tool" and s.error != SKIPPED)

    @computed_field
    @property
    def cost_usd(self) -> float:
        cost = self.input_tokens * self.input_cost_per_mtok
        cost += self.output_tokens * self.output_cost_per_mtok
        return round(cost / 1_000_000, 6)

    @contextmanager
    def step(self, kind: Literal["llm", "tool"], name: str, args: dict[str, Any] | None = None):
        """Time a step and append it, even if the body raises (the error is recorded)."""
        step = TraceStep(kind=kind, name=name, args=args or {})
        start = time.perf_counter()
        try:
            yield step
        except BaseException as exc:
            step.error = step.error or f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            step.latency_ms = round((time.perf_counter() - start) * 1000)
            self.steps.append(step)


# Error marker for a tool call the model asked for after the cap was reached (never executed).
SKIPPED = "skipped: tool call limit reached"
