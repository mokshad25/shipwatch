"""Shared eval code (docs/EVALS.md): dataset, caches, running the agent, metrics, reports.

Two caches make reruns cheap and repeatable:
- GitHub responses: evals/cache/github/<sha256>.json, recorded once, then replayed. `offline`
  replays only, so CI calls nothing but the LLM.
- Agent results: evals/cache/agent/<model>/<prompt>/<repo>-<pr>.json, keyed by
  (PR, prompt version, model). Transient failures (rate limits, outages, timeouts) are not
  cached, so the next run retries just those.
"""

import asyncio
import json
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from agent.cache import FileResponseCache
from agent.github import GitHubClient
from agent.scoring import DiffStats
from agent.service import AgentRun, Verdict, assess_pr
from agent.tools import get_diff
from agent.trace import Trace
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from worker.settings import WorkerSettings

EVALS = Path(__file__).resolve().parent
DATASET = EVALS / "data/prs.jsonl"
CACHE = EVALS / "cache"
REPORTS = EVALS / "reports"

LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}
THRESHOLDS = ("high", "medium")  # a PR is predicted risky at this level or above

# USD per million tokens (input, output), for cost per PR. Groq on-demand prices, 2026-10.
PRICES: dict[str, tuple[float, float]] = {
    "groq:openai/gpt-oss-120b": (0.15, 0.60),
    "groq:openai/gpt-oss-20b": (0.075, 0.30),
}

# Verdict reasons that say nothing about the PR: retry them next run instead of caching them.
TRANSIENT = ("rate limited", "LLM unavailable", "agent timed out", "GitHub tool error")


class EvalRecord(BaseModel):
    repo: str
    number: int
    label: int
    model: str  # provider:model
    prompt_version: str
    stats: DiffStats | None  # raw diff size, so weights can be re-scored without the LLM
    verdict: Verdict
    trace: Trace

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def scored(self) -> bool:
        return self.verdict.level != "needs_review"

    @property
    def transient(self) -> bool:
        return not self.scored and (self.verdict.reason or "").startswith(TRANSIENT)


def load_dataset(path: Path = DATASET, subset: Path | None = None) -> list[dict[str, Any]]:
    """Rows of prs.jsonl; with `subset`, only the `owner/repo#123` lines listed in that file."""
    rows = _interleave([json.loads(line) for line in path.read_text().splitlines() if line.strip()])
    if subset is None:
        return rows
    keep = {line.strip() for line in subset.read_text().splitlines() if line.strip()}
    keep = {k for k in keep if not k.startswith("#")}
    picked = [r for r in rows if f"{r['repo']}#{r['number']}" in keep]
    if len(picked) != len(keep):
        missing = keep - {f"{r['repo']}#{r['number']}" for r in picked}
        raise ValueError(f"subset PRs not in the dataset: {sorted(missing)}")
    return picked


def _round_robin(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per repo in turn: a ruff PR, a pydantic PR, a uv PR, a ruff PR, ..."""
    seen: dict[str, int] = {}
    ranked = []
    for r in rows:
        seen[r["repo"]] = seen.get(r["repo"], -1) + 1
        ranked.append((seen[r["repo"]], r["repo"], r))
    return [r for _, _, r in sorted(ranked, key=lambda t: t[:2])]


def _interleave(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alternate risky and clean rows, rotating through the repos, so a run cut short by a
    token cap (and the CI subset, its first 10 rows) is balanced in both."""
    risky = _round_robin([r for r in rows if r["label"] == 1])
    clean = _round_robin([r for r in rows if r["label"] == 0])
    out = []
    for i in range(max(len(risky), len(clean))):
        out += risky[i : i + 1] + clean[i : i + 1]
    return out


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def agent_cache_path(root: Path, model: str, prompt: str, repo: str, number: int) -> Path:
    return root / "agent" / slug(model) / prompt / f"{slug(repo)}-{number}.json"


def eval_settings(base: WorkerSettings, model: str, prompt: str) -> WorkerSettings:
    """`model` is provider:name. Batch runs wait out rate limits rather than give up."""
    provider, _, name = model.partition(":")
    prices = PRICES.get(model, (base.llm_input_cost_per_mtok, base.llm_output_cost_per_mtok))
    return base.model_copy(
        update={
            "llm_provider": provider,
            "llm_model": name,
            "prompt_version": prompt,
            "llm_input_cost_per_mtok": prices[0],
            "llm_output_cost_per_mtok": prices[1],
            "llm_max_retries": max(base.llm_max_retries, 6),
            "agent_timeout_seconds": max(base.agent_timeout_seconds, 600),
        }
    )


def _offline(request: httpx.Request) -> httpx.Response:
    return httpx.Response(599, json={"message": f"not in the recorded GitHub cache: {request.url}"})


def make_github(settings: WorkerSettings, cache_root: Path, *, offline: bool) -> GitHubClient:
    http = httpx.AsyncClient(
        base_url=settings.github_api_url,
        timeout=max(settings.github_timeout_seconds, 30),
        transport=httpx.MockTransport(_offline) if offline else None,
    )
    token = settings.github_token.get_secret_value() if settings.github_token else None
    return GitHubClient(http, FileResponseCache(cache_root / "github"), token)


async def evaluate_pr(
    llm: BaseChatModel, gh: GitHubClient, row: dict[str, Any], settings: WorkerSettings
) -> EvalRecord:
    run: AgentRun = await assess_pr(
        llm, gh, row["repo"], row["number"], settings, point_in_time=True
    )
    stats = None
    try:  # cached by the agent's own get_diff call, so free
        diff = await get_diff(gh, row["repo"], row["number"])
        stats = DiffStats(
            additions=diff.additions, deletions=diff.deletions, changed_files=diff.changed_files
        )
    except Exception:  # noqa: BLE001 (the verdict already says what went wrong)
        pass
    return EvalRecord(
        repo=row["repo"],
        number=row["number"],
        label=row["label"],
        model=f"{settings.llm_provider}:{settings.llm_model}",
        prompt_version=settings.prompt_version,
        stats=stats,
        verdict=run.verdict,
        trace=run.trace,
    )


class RunResult(BaseModel):
    records: list[EvalRecord]  # cached or completed, in dataset order
    fresh: int  # how many were run now (the rest came from the cache)
    pending: list[str]  # not run, or transient failures: retried next time


async def run_eval(
    rows: list[dict[str, Any]],
    llm: BaseChatModel,
    gh: GitHubClient,
    settings: WorkerSettings,
    *,
    cache_root: Path = CACHE,
    use_cache: bool = True,
    concurrency: int = 4,
    stop_after: int = 3,
    progress: bool = True,
) -> RunResult:
    """Run the agent over `rows`. After `stop_after` transient failures in a row (a daily token
    cap, typically) nothing new is started: the rest is `pending` for the next run."""
    model = f"{settings.llm_provider}:{settings.llm_model}"
    results: dict[str, EvalRecord] = {}
    pending: list[str] = []
    fresh = 0
    streak = 0
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict[str, Any]) -> None:
        nonlocal fresh, streak
        key = f"{row['repo']}#{row['number']}"
        path = agent_cache_path(cache_root, model, settings.prompt_version, *key.split("#"))
        if use_cache and path.is_file():
            results[key] = EvalRecord.model_validate_json(path.read_text())
            return
        async with sem:
            if streak >= stop_after:
                pending.append(key)
                return
            record = await evaluate_pr(llm, gh, row, settings)
        fresh += 1
        if record.transient:
            streak += 1
            pending.append(key)
            if progress:
                print(f"{key}: pending ({(record.verdict.reason or '')[:80]})")
            return
        streak = 0
        results[key] = record
        if use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record.model_dump_json(indent=1))
        if progress:
            v = record.verdict
            score = "" if v.score is None else v.score
            print(f"{key}: label {record.label}, {v.level} {score}")

    await asyncio.gather(*(one(r) for r in rows))
    order = [f"{r['repo']}#{r['number']}" for r in rows]
    records = [results[k] for k in order if k in results]
    return RunResult(records=records, fresh=fresh, pending=[k for k in order if k in pending])


def load_cached(
    rows: list[dict[str, Any]], model: str, prompt: str, cache_root: Path = CACHE
) -> list[EvalRecord]:
    """The cached results for (model, prompt), in dataset order; PRs not run yet are skipped."""
    out = []
    for r in rows:
        path = agent_cache_path(cache_root, model, prompt, r["repo"], r["number"])
        if path.is_file():
            out.append(EvalRecord.model_validate_json(path.read_text()))
    return out


# --- metrics ---------------------------------------------------------------------------------


def predicted(level: str, threshold: str) -> bool:
    return LEVEL_RANK[level] >= LEVEL_RANK[threshold]


def classification(pairs: Iterable[tuple[int, bool]]) -> dict[str, Any]:
    """Precision, recall, F1 (0 when undefined) and the confusion matrix from (label, predicted)."""
    tp = fp = fn = tn = 0
    for label, pred in pairs:
        if pred:
            tp, fp = (tp + 1, fp) if label else (tp, fp + 1)
        else:
            fn, tn = (fn + 1, tn) if label else (fn, tn + 1)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n": tp + fp + fn + tn,
    }


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def metrics(records: list[EvalRecord]) -> dict[str, Any]:
    """Classification at each threshold over scored PRs (needs_review is left out and counted),
    plus tokens, cost and latency per PR."""
    scored = [r for r in records if r.scored]
    latencies = [r.trace.latency_ms / 1000 for r in scored]
    tokens = [r.trace.input_tokens + r.trace.output_tokens for r in records]
    costs = [r.trace.cost_usd for r in records]
    return {
        "thresholds": {
            t: classification((r.label, predicted(r.verdict.level, t)) for r in scored)
            for t in THRESHOLDS
        },
        "scored": len(scored),
        "needs_review": len(records) - len(scored),
        "avg_tokens": round(sum(tokens) / len(tokens)) if tokens else 0,
        "cost_per_pr": round(sum(costs) / len(costs), 6) if costs else 0.0,
        "total_cost": round(sum(costs), 6),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
    }


# --- reports ---------------------------------------------------------------------------------


def top_factor(record: EvalRecord) -> str:
    a = record.verdict.assessment
    if not a or not a.factors:
        return "-"
    f = max(a.factors, key=lambda f: f.severity)
    return f"{f.kind} ({f.severity})"


def build_report(
    result: RunResult, *, model: str, prompt: str, dataset: str, n_rows: int, threshold: str
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "prompt_version": prompt,
        "dataset": dataset,
        "rows": n_rows,
        "threshold": threshold,
        "fresh_runs": result.fresh,
        "pending": result.pending,
        "metrics": metrics(result.records),
        "results": [
            {
                "pr": r.key,
                "label": r.label,
                "level": r.verdict.level,
                "score": r.verdict.score,
                "top_factor": top_factor(r),
                "reason": r.verdict.reason,
                "tokens": r.trace.input_tokens + r.trace.output_tokens,
                "cost_usd": r.trace.cost_usd,
                "latency_s": r.trace.latency_ms / 1000,
            }
            for r in result.records
        ],
    }


def _num(value: float | None, fmt: str = ".2f") -> str:
    return "-" if value is None else format(value, fmt)


def metrics_table(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        "| Threshold | Precision | Recall | F1 | TP | FP | FN | TN |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t, c in m["thresholds"].items():
        cm = c["confusion"]
        star = " (primary)" if t == report["threshold"] else ""
        lines.append(
            f"| ≥ {t}{star} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} "
            f"| {cm['tp']} | {cm['fp']} | {cm['fn']} | {cm['tn']} |"
        )
    lines += [
        "",
        f"Avg tokens/PR {m['avg_tokens']}, cost/PR ${m['cost_per_pr']:.4f} "
        f"(total ${m['total_cost']:.4f}), latency p50 {_num(m['latency_p50_s'], '.1f')} s, "
        f"p95 {_num(m['latency_p95_s'], '.1f')} s.",
    ]
    return "\n".join(lines)


def render_markdown(report: dict[str, Any], gate: str | None = None) -> str:
    m = report["metrics"]
    pending = len(report["pending"])
    lines = [
        f"# Eval: prompt {report['prompt_version']} · `{report['model']}`",
        "",
        f"{report['created_at']} · `{report['dataset']}`: {report['rows']} PRs, {m['scored']} "
        f"scored, {m['needs_review']} needs_review, {pending} pending "
        f"({report['fresh_runs']} run now, the rest from cache; "
        f"{report.get('github_requests_recorded', 0)} GitHub responses fetched live).",
        "",
        metrics_table(report),
    ]
    if gate:
        lines += ["", gate]
    lines += [
        "",
        "| PR | Label | Level | Score | Top factor | Tokens | Latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        repo, number = r["pr"].split("#")
        lines.append(
            f"| [{r['pr']}](https://github.com/{repo}/pull/{number}) | {r['label']} "
            f"| {r['level']} | {r['score'] if r['score'] is not None else '-'} "
            f"| {r['top_factor']} | {r['tokens']} | {r['latency_s']:.1f} s |"
        )
    if report["pending"]:
        lines += ["", "Pending (rerun to finish): " + ", ".join(report["pending"])]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], markdown: str, out: Path = REPORTS) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["created_at"].replace(":", "").replace("-", "")
    stem = f"{stamp}-{report['prompt_version']}-{slug(report['model'])}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / f"{stem}.md").write_text(markdown)
    return out / f"{stem}.json"


# --- regression gate (W5 D5) -----------------------------------------------------------------


def check_gate(
    report: dict[str, Any], baseline: dict[str, Any], max_drop_points: float
) -> tuple[bool, str]:
    """Fail if F1 at the baseline's threshold fell more than `max_drop_points` (0–100 scale), or
    if any PR is still pending (a partial run proves nothing)."""
    t = baseline["threshold"]
    f1 = report["metrics"]["thresholds"][t]["f1"]
    drop = round((baseline["f1"] - f1) * 100, 1)
    if report["pending"]:
        return False, f"**Gate: FAIL** — {len(report['pending'])} PRs didn't run (rate limits?)."
    verdict = "PASS" if drop <= max_drop_points else "FAIL"
    return verdict == "PASS", (
        f"**Gate: {verdict}** — F1 at ≥ {t}: {f1:.2f} vs baseline {baseline['f1']:.2f} "
        f"({-drop:+.1f} points; fails below −{max_drop_points:g})."
    )
