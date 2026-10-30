"""assess_pr: run the agent on one PR and always come back with a Verdict (SPEC_W3 D4, D6).

Agent failures never raise. A timeout, an LLM outage, a GitHub error, invalid model output or a
blown token budget becomes level "needs_review" with the reason. The one exception is a rate
limit while the caller still has retries left: that raises AgentRateLimited, so the worker's
backoff and attempt budget apply instead of a second retry loop in here.
"""

from typing import Literal

import httpx
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from shipwatch_common.logging import get_logger
from worker.settings import WorkerSettings

from agent.assessment import RiskAssessment
from agent.github import GitHubClient, GitHubError, GitHubRateLimited
from agent.loop import Agent, AssessmentParseError, Limits, TokenBudgetExceeded
from agent.prompts import load_prompt
from agent.scoring import DiffStats, score
from agent.toolbox import make_toolbox
from agent.tools import get_diff
from agent.trace import Trace

log = get_logger(__name__)


class Verdict(BaseModel):
    repo: str
    pr_number: int
    head_sha: str | None
    level: Literal["low", "medium", "high", "needs_review"]
    score: int | None  # None when level is needs_review
    reason: str | None = None  # why it needs review
    assessment: RiskAssessment | None = None
    breakdown: dict[str, int] = {}
    rules: list[str] = []


class AgentRun(BaseModel):
    verdict: Verdict
    trace: Trace


class AgentRateLimited(Exception):
    """Retryable: the LLM provider or GitHub rate-limited us and the caller can try again.

    `retry_after` is how long the provider asked us to wait (seconds), if it said.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(exc: BaseException) -> float | None:
    if isinstance(exc, GitHubRateLimited):
        return exc.retry_after
    response = getattr(exc, "response", None)  # openai / anthropic / groq SDK errors
    value = getattr(response, "headers", {}).get("retry-after") if response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _is_rate_limit(exc: BaseException) -> bool:
    return isinstance(exc, GitHubRateLimited) or getattr(exc, "status_code", None) == 429


def _is_llm_unavailable(exc: BaseException) -> bool:
    # openai / anthropic SDK errors (both define these names), or a raw httpx failure.
    names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(names & {"APITimeoutError", "APIConnectionError", "InternalServerError"}) or (
        isinstance(exc, httpx.TransportError)
    )


def _reason(exc: BaseException, limits: Limits) -> str:
    if isinstance(exc, TimeoutError):
        return f"agent timed out after {limits.timeout_seconds:g}s"
    if isinstance(exc, TokenBudgetExceeded):
        return str(exc)
    if isinstance(exc, AssessmentParseError):
        return f"model output failed validation twice: {exc}"
    if isinstance(exc, GitHubError):
        return f"GitHub tool error: {exc}"
    if _is_llm_unavailable(exc):
        return f"LLM unavailable: {type(exc).__name__}: {exc}"
    return f"unexpected error: {type(exc).__name__}: {exc}"


def limits_from(settings: WorkerSettings) -> Limits:
    return Limits(
        max_tool_calls=settings.agent_max_tool_calls,
        timeout_seconds=settings.agent_timeout_seconds,
        token_budget=settings.llm_token_budget,
    )


async def assess_pr(
    llm: BaseChatModel,
    gh: GitHubClient,
    repo: str,
    pr_number: int,
    settings: WorkerSettings,
    *,
    can_retry: bool = False,
    attempt: int = 1,
    point_in_time: bool = False,
) -> AgentRun:
    """`point_in_time` is for evals: see make_toolbox."""
    limits = limits_from(settings)
    prompt = load_prompt(settings.prompt_version)
    trace = Trace(
        repo=repo,
        pr_number=pr_number,
        provider=settings.llm_provider,
        model=settings.llm_model or type(llm).__name__,
        prompt_version=prompt.version,
        input_cost_per_mtok=settings.llm_input_cost_per_mtok,
        output_cost_per_mtok=settings.llm_output_cost_per_mtok,
    )
    head_sha: str | None = None
    try:
        # Size comes from GitHub, not from the model, so the LLM can't hide a big diff (cached:
        # the agent's own get_diff call costs nothing extra).
        diff = await get_diff(gh, repo, pr_number)
        head_sha = diff.head_sha
        stats = DiffStats(
            additions=diff.additions, deletions=diff.deletions, changed_files=diff.changed_files
        )
        toolbox = make_toolbox(gh, repo, pr_number, point_in_time=point_in_time)
        agent = Agent(llm, toolbox, trace, limits, prompt)
        assessment = await agent.run()
    except Exception as exc:
        if _is_rate_limit(exc):
            if can_retry:
                raise AgentRateLimited(
                    f"rate limited on attempt {attempt}: {exc}", _retry_after(exc)
                ) from exc
            reason = f"rate limited after {attempt} attempt(s): {exc}"
        else:
            reason = _reason(exc, limits)
        log.warning("agent needs review", repo=repo, pr=pr_number, reason=reason[:300])
        verdict = Verdict(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            level="needs_review",
            score=None,
            reason=reason[:1000],
        )
        return AgentRun(verdict=verdict, trace=trace)

    result = score(assessment, stats)
    verdict = Verdict(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        level=result.level,
        score=result.score,
        assessment=assessment,
        breakdown=result.breakdown,
        rules=result.rules,
    )
    log.info(
        "agent verdict",
        repo=repo,
        pr=pr_number,
        verdict_level=verdict.level,
        score=verdict.score,
        tokens=trace.input_tokens + trace.output_tokens,
        latency_ms=trace.latency_ms,
    )
    return AgentRun(verdict=verdict, trace=trace)
