"""Run the PR risk agent on one PR and print its trace and verdict.

    uv run python -m agent.run encode/httpx 3783
    uv run python -m agent.run encode/httpx 3783 --json            # everything as JSON
    uv run python -m agent.run encode/httpx 3783 --out docs/results/w3_samples

Needs LLM_PROVIDER, LLM_MODEL and that provider's API key (and ideally GITHUB_TOKEN) in .env.
GitHub responses are cached in Redis if it is running; without it the run still works.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from redis.asyncio import Redis
from shipwatch_common.logging import configure_logging
from worker.settings import WorkerSettings

from agent.github import GitHubClient
from agent.llm import LLMConfigError, make_chat_model
from agent.service import AgentRun, assess_pr


def format_run(run: AgentRun) -> str:
    t, v = run.trace, run.verdict
    rows = [("#", "kind", "name", "ms", "in", "out", "summary / error")]
    for i, s in enumerate(t.steps, 1):
        detail = s.error or s.summary
        if s.kind == "tool" and s.args:
            detail = f"{s.args} -> {detail}"
        rows.append(
            (str(i), s.kind, s.name, str(s.latency_ms), str(s.input_tokens or ""),
             str(s.output_tokens or ""), detail[:120])
        )  # fmt: skip
    widths = [max(len(r[c]) for r in rows) for c in range(6)]
    lines = [f"{t.repo}#{t.pr_number}  ({t.provider}:{t.model})", ""]
    lines += ["  ".join(r[c].ljust(widths[c]) for c in range(6)) + "  " + r[6] for r in rows]
    lines += [
        "",
        f"tool calls {t.tool_calls}  tokens {t.input_tokens} in / {t.output_tokens} out  "
        f"latency {t.latency_ms} ms  cost ${t.cost_usd:.4f}",
        "",
        f"verdict: {v.level}" + (f"  score {v.score}" if v.score is not None else ""),
    ]
    if v.reason:
        lines.append(f"reason: {v.reason}")
    if v.assessment:
        lines.append(f"summary: {v.assessment.summary}  (confidence {v.assessment.confidence})")
        for f in v.assessment.factors:
            lines.append(f"  - {f.kind} (severity {f.severity}): {f.evidence}")
        lines.append(f"breakdown: {v.breakdown}  rules: {v.rules or 'none'}")
    return "\n".join(lines)


async def run(repo: str, pr_number: int, settings: WorkerSettings) -> AgentRun:
    llm = make_chat_model(settings)
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1)
    try:
        async with GitHubClient.from_settings(settings, redis) as gh:
            return await assess_pr(llm, gh, repo, pr_number, settings)
    finally:
        await redis.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.run", description=__doc__.split("\n")[0])
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--json", action="store_true", help="print the run as JSON")
    parser.add_argument("--out", type=Path, help="also save the run as <out>/<pr_number>.json")
    args = parser.parse_args(argv)

    settings = WorkerSettings()
    configure_logging("WARNING")
    try:
        result = asyncio.run(run(args.repo, args.pr_number, settings))
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2) if args.json else format_run(result))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.pr_number}.json").write_text(result.model_dump_json(indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
