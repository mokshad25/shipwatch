"""Build the eval dataset: PRs later reverted or hotfixed (risky=1) and clean ones (risky=0).

    uv run python evals/build_dataset.py                            # default repos, 20 + 20
    uv run python evals/build_dataset.py --repos a/b c/d --as-of 2026-10-26

Lists every PR merged in each repo since --since, labels from that listing (and each sampled
negative's timeline), and writes evals/data/prs.jsonl. Labeling rules and their biases:
docs/EVALS.md. Needs GITHUB_TOKEN in .env (thousands of GETs); no LLM, costs nothing.
"""

import argparse
import asyncio
import json
import random
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
from agent.github import GitHubClient, GitHubRateLimited
from agent.tools import revert_target
from redis.asyncio import Redis
from shipwatch_common.logging import configure_logging
from worker.settings import WorkerSettings

OUT = Path(__file__).resolve().parent / "data/prs.jsonl"
DEFAULT_REPOS = ["astral-sh/ruff", "pydantic/pydantic", "astral-sh/uv"]

REVERT_WINDOW = timedelta(days=30)
HOTFIX_WINDOW = timedelta(days=7)
CLEAN_WINDOW = timedelta(days=30)  # a negative needs this much quiet after its merge

FIX_TITLE = re.compile(r"\b(hot-?fix|fix|fixes|fixed|regression)\b", re.IGNORECASE)
# Words that make a later issue/PR that mentions a PR look like trouble caused by it.
TROUBLE = re.compile(
    r"\b(revert|reverts|hot-?fix|fix|fixes|fixed|regression|broke|breaks|broken|bug|crash)\b",
    re.IGNORECASE,
)
PR_REF = re.compile(r"(?:#|/pull/)(\d+)\b")


@dataclass(frozen=True)
class Label:
    kind: str  # "revert" | "hotfix" | "clean"
    by_pr: int | None = None  # the revert or hotfix PR
    days_after: float | None = None

    @property
    def reason(self) -> str:
        if self.kind == "revert":
            return f"reverted by #{self.by_pr} {self.days_after:.1f} days after merge"
        if self.kind == "hotfix":
            return f"fixed by #{self.by_pr} {self.days_after:.1f} days after merge"
        return f"no revert, hotfix or trouble reference within {CLEAN_WINDOW.days} days"


def merged_at(pr: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(pr["merged_at"])


def is_bot(pr: dict[str, Any]) -> bool:
    user = pr.get("user") or {}
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


def is_revert(pr: dict[str, Any]) -> bool:
    return pr["title"].startswith("Revert ")


def _days(later: datetime, earlier: datetime) -> float:
    return round((later - earlier).total_seconds() / 86400, 1)


async def _retrying[T](call: Callable[[], Awaitable[T]], attempts: int = 5) -> T:
    """Wait out GitHub rate limits and flaky connections (pages already read stay cached)."""
    for attempt in range(attempts):
        try:
            return await call()
        except GitHubRateLimited as exc:
            if attempt == attempts - 1:
                raise
            wait = min(exc.retry_after if exc.retry_after is not None else 60, 900)
            print(f"rate limited; waiting {wait:.0f} s", file=sys.stderr)
            await asyncio.sleep(wait + 1)
        except httpx.TransportError as exc:
            if attempt == attempts - 1:
                raise
            wait = 2**attempt
            print(f"{type(exc).__name__}; retrying in {wait} s", file=sys.stderr)
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


async def list_merged(
    gh: GitHubClient, repo: str, since: datetime, as_of: datetime, *, max_pages: int = 200
) -> list[dict[str, Any]]:
    """Every PR merged in [since, as_of], oldest first, read newest-updated first.

    A PR's updated_at is never before its merged_at, so the first page whose last PR was updated
    before `since` is the last one with anything left to find.
    """
    prs: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
            "page": page,
        }
        body = await _retrying(lambda p=params: gh.get_json(f"/repos/{repo}/pulls", p))
        prs += [p for p in body if p.get("merged_at") and since <= merged_at(p) <= as_of]
        if len(body) < 100 or datetime.fromisoformat(body[-1]["updated_at"]) < since:
            break
    unique = {p["number"]: p for p in prs}  # a PR updated mid-listing can show up twice
    return sorted(unique.values(), key=merged_at)


def find_reverts(repo: str, prs: list[dict[str, Any]]) -> dict[int, Label]:
    """PR number -> its first revert merged within REVERT_WINDOW of it."""
    by_number = {p["number"]: p for p in prs}
    by_title = {p["title"]: p["number"] for p in prs}
    found: dict[int, Label] = {}
    for rv in prs:  # oldest first, so the first revert found for a PR is the earliest
        if not is_revert(rv) or rv["title"].startswith('Revert "Revert'):
            continue  # re-landing a reverted change says nothing about the change
        target = revert_target(repo, rv, by_number, by_title)
        if target is None or target in found or is_revert(by_number[target]):
            continue
        delay = merged_at(rv) - merged_at(by_number[target])
        if timedelta(0) <= delay <= REVERT_WINDOW:
            found[target] = Label(
                "revert", rv["number"], _days(merged_at(rv), merged_at(by_number[target]))
            )
    return found


def referenced(pr: dict[str, Any]) -> set[int]:
    """PR/issue numbers a PR's title or body points at (`#N` or `.../pull/N`)."""
    text = f"{pr['title']}\n{pr.get('body') or ''}"
    return {int(n) for n in PR_REF.findall(text)} - {pr["number"]}


def find_hotfixes(prs: list[dict[str, Any]]) -> dict[int, Label]:
    """PR number -> the first fix-titled PR that references it and merged within HOTFIX_WINDOW."""
    by_number = {p["number"]: p for p in prs}
    found: dict[int, Label] = {}
    for fix in prs:
        if is_revert(fix) or not FIX_TITLE.search(fix["title"]):
            continue
        for target in sorted(referenced(fix) & by_number.keys()):
            if is_revert(by_number[target]):
                continue
            delay = merged_at(fix) - merged_at(by_number[target])
            if target not in found and timedelta(0) <= delay <= HOTFIX_WINDOW:
                found[target] = Label(
                    "hotfix", fix["number"], _days(merged_at(fix), merged_at(by_number[target]))
                )
    return found


def troubled(prs: list[dict[str, Any]]) -> set[int]:
    """PRs that a trouble-sounding PR in the listing references within CLEAN_WINDOW."""
    by_number = {p["number"]: p for p in prs}
    out: set[int] = set()
    for later in prs:
        if not TROUBLE.search(later["title"]):
            continue
        for n in referenced(later) & by_number.keys():
            if timedelta(0) <= merged_at(later) - merged_at(by_number[n]) <= CLEAN_WINDOW:
                out.add(n)
    return out


async def has_trouble_reference(gh: GitHubClient, repo: str, pr: dict[str, Any]) -> bool:
    """Whether an issue or PR (anywhere) mentioned this PR within CLEAN_WINDOW and sounds bad."""
    events, _ = await _retrying(
        lambda: gh.get_pages(
            f"/repos/{repo}/issues/{pr['number']}/timeline", {"per_page": 100}, max_pages=3
        )
    )
    deadline = merged_at(pr) + CLEAN_WINDOW
    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = (event.get("source") or {}).get("issue") or {}
        labels = " ".join(label.get("name", "") for label in source.get("labels") or [])
        if datetime.fromisoformat(event["created_at"]) <= deadline and TROUBLE.search(
            f"{source.get('title', '')} {labels}"
        ):
            return True
    return False


def pick_positives(
    candidates: dict[str, list[tuple[dict[str, Any], Label]]], target: int, per_repo_max: int
) -> list[tuple[str, dict[str, Any], Label]]:
    """Round-robin over repos (each already ordered: reverts, then hotfixes) up to `target`."""
    queues = {repo: list(items[:per_repo_max]) for repo, items in candidates.items()}
    picked: list[tuple[str, dict[str, Any], Label]] = []
    while len(picked) < target and any(queues.values()):
        for repo, queue in queues.items():
            if queue and len(picked) < target:
                pr, label = queue.pop(0)
                picked.append((repo, pr, label))
    return picked


def _month(d: datetime) -> int:
    return d.year * 12 + d.month


async def pick_negatives(
    gh: GitHubClient,
    repo: str,
    positives: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """One clean PR per positive, from the closest merge month, checked against its timeline."""
    pool = list(pool)
    rng.shuffle(pool)
    chosen: list[dict[str, Any]] = []
    rejected: set[int] = set()
    for pos in positives:
        month = _month(merged_at(pos))
        # Stable sort keeps the shuffled order within a month distance: random but reproducible.
        for pr in sorted(pool, key=lambda p: abs(_month(merged_at(p)) - month)):
            if pr["number"] in rejected or pr in chosen:
                continue
            if await has_trouble_reference(gh, repo, pr):
                rejected.add(pr["number"])
                continue
            chosen.append(pr)
            break
    return chosen


def to_row(repo: str, pr: dict[str, Any], label: Label) -> dict[str, Any]:
    return {
        "repo": repo,
        "number": pr["number"],
        "label": 0 if label.kind == "clean" else 1,
        "reason": label.reason,
        "title": pr["title"],
        "author": (pr.get("user") or {}).get("login"),
        "merged_at": pr["merged_at"],
        "evidence": {"kind": label.kind, "by_pr": label.by_pr, "days_after": label.days_after},
    }


async def build(
    gh: GitHubClient,
    repos: list[str],
    *,
    since: datetime,
    as_of: datetime,
    target: int = 20,
    per_repo_max: int = 10,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Dataset rows, positives first, then by repo and PR number."""
    rng = random.Random(seed)
    cutoff = as_of - CLEAN_WINDOW  # candidates merged later can't show a full quiet window yet
    listings: dict[str, list[dict[str, Any]]] = {}
    labelled: dict[str, dict[int, Label]] = {}
    candidates: dict[str, list[tuple[dict[str, Any], Label]]] = {}
    for repo in repos:
        prs = listings[repo] = await list_merged(gh, repo, since, as_of)
        by_number = {p["number"]: p for p in prs}
        reverts, hotfixes = find_reverts(repo, prs), find_hotfixes(prs)
        labelled[repo] = hotfixes | reverts  # a PR both reverted and hotfixed counts as reverted
        print(f"{repo}: {len(prs)} merged PRs, {len(reverts)} reverted, {len(hotfixes)} hotfixed")
        ordered: list[tuple[dict[str, Any], Label]] = []
        for found in (reverts, hotfixes):  # reverts are the stronger signal, so they go first
            group = [
                (by_number[n], label)
                for n, label in sorted(found.items())
                if n not in {p["number"] for p, _ in ordered}
                and merged_at(by_number[n]) <= cutoff
                and not is_bot(by_number[n])
            ]
            rng.shuffle(group)
            ordered += group
        candidates[repo] = ordered

    rows: list[dict[str, Any]] = []
    positives = pick_positives(candidates, target, per_repo_max)
    for repo in repos:
        prs = listings[repo]
        mine = [pr for r, pr, _ in positives if r == repo]
        rows += [to_row(r, pr, label) for r, pr, label in positives if r == repo]
        # Neither a labelled PR nor the revert/fix that labelled it is a clean example.
        excluded = (
            troubled(prs)
            | labelled[repo].keys()
            | {label.by_pr for label in labelled[repo].values()}
        )
        pool = [
            p
            for p in prs
            if p["number"] not in excluded
            and merged_at(p) <= cutoff
            and not is_bot(p)
            and not is_revert(p)
        ]
        for pr in await pick_negatives(gh, repo, mine, pool, rng):
            rows.append(to_row(repo, pr, Label("clean")))
    rows.sort(key=lambda r: (-r["label"], r["repo"], r["number"]))
    return rows


def write_jsonl(rows: list[dict[str, Any]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time(), tzinfo=UTC)


async def _main(args: argparse.Namespace) -> int:
    settings = WorkerSettings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1)
    try:
        async with GitHubClient.from_settings(settings, redis) as gh:
            rows = await build(
                gh,
                args.repos,
                since=_day(args.since),
                # The whole as-of day counts, so a rerun on the same day sees the same PRs.
                as_of=_day(args.as_of) + timedelta(days=1) - timedelta(microseconds=1),
                target=args.target,
                per_repo_max=args.per_repo_max,
                seed=args.seed,
            )
    finally:
        await redis.aclose()
    await asyncio.to_thread(write_jsonl, rows, args.out)
    counts = {label: sum(r["label"] == label for r in rows) for label in (1, 0)}
    for repo in args.repos:
        kinds = [r["evidence"]["kind"] for r in rows if r["repo"] == repo]
        print(
            f"{repo}: " + ", ".join(f"{k}={kinds.count(k)}" for k in ("revert", "hotfix", "clean"))
        )
    print(f"wrote {len(rows)} rows ({counts[1]} risky, {counts[0]} clean) to {args.out}")
    return 0 if min(counts.values()) >= args.target else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repos", nargs="+", default=DEFAULT_REPOS, help="owner/name ...")
    parser.add_argument("--since", default="2025-01-01", help="earliest merge date (YYYY-MM-DD)")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="pretend today is")
    parser.add_argument("--target", type=int, default=20, help="PRs per class")
    parser.add_argument("--per-repo-max", type=int, default=10, help="positives per repo, at most")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    configure_logging("WARNING")
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
