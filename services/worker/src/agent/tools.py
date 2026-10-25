"""The PR risk agent's tools: plain async functions over the GitHub REST API (docs/SPEC_W3.md).

Each returns a pydantic model the agent loop serialises for the LLM. Whenever data is cut, `notes`
says so, so a partial view is never mistaken for the whole picture.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from agent.github import GitHubClient

MAX_DIFF_PAGES = 3  # x100 files
MAX_PATCH_CHARS = 4_000
MAX_TOTAL_PATCH_CHARS = 20_000
MAX_CHURN_PATHS = 20
RECENT_MERGED = 10
MAX_CHECK_PAGES = 3
FAILING_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})


class DiffFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None
    patch_truncated: bool = False


class Diff(BaseModel):
    repo: str
    pr_number: int
    title: str
    author: str
    head_sha: str
    base_ref: str
    created_at: str | None  # when the PR was opened (ISO 8601); file_churn's window ends here
    changed_files: int
    additions: int
    deletions: int
    files: list[DiffFile]
    truncated: bool
    notes: list[str]


async def get_diff(gh: GitHubClient, repo: str, pr_number: int) -> Diff:
    """Files changed by a PR, with exact totals and patches cut to fit an LLM prompt."""
    pr = await gh.get_json(f"/repos/{repo}/pulls/{pr_number}")
    raw_files, more = await gh.get_pages(
        f"/repos/{repo}/pulls/{pr_number}/files", {"per_page": 100}, max_pages=MAX_DIFF_PAGES
    )
    notes: list[str] = []
    if more or len(raw_files) < pr["changed_files"]:
        notes.append(
            f"{pr['changed_files']} files changed; only the first {len(raw_files)} are listed"
        )

    files: list[DiffFile] = []
    budget = MAX_TOTAL_PATCH_CHARS
    no_patch: list[str] = []
    dropped = 0
    for f in raw_files:
        patch: str | None = f.get("patch")
        cut = False
        if patch is None:
            no_patch.append(f["filename"])
        elif budget <= 0:
            patch, dropped = None, dropped + 1
        else:
            limit = min(MAX_PATCH_CHARS, budget)
            if len(patch) > limit:
                notes.append(
                    f"patch for {f['filename']} truncated to {limit} of {len(patch)} chars"
                )
                patch, cut = patch[:limit], True
            budget -= len(patch)
        files.append(
            DiffFile(
                filename=f["filename"],
                status=f["status"],
                additions=f["additions"],
                deletions=f["deletions"],
                patch=patch,
                patch_truncated=cut,
            )
        )
    if dropped:
        notes.append(
            f"patches omitted for {dropped} more files: {MAX_TOTAL_PATCH_CHARS} char budget spent"
        )
    if no_patch:
        notes.append("GitHub sent no patch (binary or too large) for: " + ", ".join(no_patch))

    return Diff(
        repo=repo,
        pr_number=pr_number,
        title=pr["title"],
        author=pr["user"]["login"],
        head_sha=pr["head"]["sha"],
        base_ref=pr["base"]["ref"],
        created_at=pr.get("created_at"),
        changed_files=pr["changed_files"],
        additions=pr["additions"],
        deletions=pr["deletions"],
        files=files,
        truncated=bool(notes),
        notes=notes,
    )


class PathChurn(BaseModel):
    commits: int
    capped: bool  # at least 100 commits: the count is a lower bound


class FileChurn(BaseModel):
    repo: str
    ref: str | None  # branch counted on; None = the repo's default branch
    days: int
    since: str
    until: str
    files: dict[str, PathChurn]
    notes: list[str]


async def file_churn(
    gh: GitHubClient,
    repo: str,
    paths: list[str],
    days: int = 90,
    now: datetime | None = None,
    ref: str | None = None,
) -> FileChurn:
    """Commits touching each path in the `days` days before `now` (hot files change often).

    `now` is the end of the window: the PR's creation time when assessing a PR, so an old PR's
    churn is measured as it was then, not as of today. `ref` is the branch to count on (the PR's
    base branch; GitHub defaults to the repo's default branch, which may not have the files).
    """
    end = now or datetime.now(UTC)
    since = (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    notes: list[str] = []
    if len(paths) > MAX_CHURN_PATHS:
        notes.append(f"only the first {MAX_CHURN_PATHS} of {len(paths)} paths were checked")
    files: dict[str, PathChurn] = {}
    for path in paths[:MAX_CHURN_PATHS]:
        commits = await gh.get_json(
            f"/repos/{repo}/commits",
            {"path": path, "since": since, "until": until, "per_page": 100}
            | ({"sha": ref} if ref else {}),
        )
        files[path] = PathChurn(commits=len(commits), capped=len(commits) >= 100)
    return FileChurn(
        repo=repo, ref=ref, days=days, since=since, until=until, files=files, notes=notes
    )


class PrRef(BaseModel):
    number: int
    title: str
    merged_at: str | None


class Revert(BaseModel):
    pr: int  # the author's PR that was reverted
    revert_pr: int


class AuthorHistory(BaseModel):
    repo: str
    author: str
    merged_count: int
    recent_merged: list[PrRef]
    reverted: list[Revert]
    is_new_author: bool
    notes: list[str]


# `Revert "<title>"`, where a squash-merged title may end in ` (#123)`.
_REVERT_TITLE = re.compile(r'^Revert "(?P<title>.+?)(?: \(#(?P<number>\d+)\))?"$')


async def author_history(gh: GitHubClient, repo: str, author: str) -> AuthorHistory:
    """How many PRs the author has merged here, and which of them were later reverted."""
    search = {"sort": "created", "order": "desc", "per_page": 100}
    merged = await gh.get_json(
        "/search/issues", {**search, "q": f"repo:{repo} is:pr is:merged author:{author}"}
    )
    reverts = await gh.get_json(
        "/search/issues", {**search, "q": f"repo:{repo} is:pr is:merged in:title Revert"}
    )
    mine = merged["items"]
    by_number = {pr["number"]: pr for pr in mine}
    by_title = {pr["title"]: pr["number"] for pr in mine}
    body_ref = re.compile(rf"\bReverts {re.escape(repo)}#(\d+)\b", re.IGNORECASE)

    reverted: list[Revert] = []
    for rv in reverts["items"]:
        target = None
        if (m := body_ref.search(rv.get("body") or "")) and int(m[1]) in by_number:
            target = int(m[1])
        elif m := _REVERT_TITLE.match(rv["title"]):
            if m["number"] and int(m["number"]) in by_number:
                target = int(m["number"])
            else:
                target = by_title.get(m["title"])
        if target is not None and target != rv["number"]:
            reverted.append(Revert(pr=target, revert_pr=rv["number"]))

    notes: list[str] = []
    if merged["total_count"] > len(mine):
        notes.append(f"reverts checked against the newest {len(mine)} merged PRs only")
    return AuthorHistory(
        repo=repo,
        author=author,
        merged_count=merged["total_count"],
        recent_merged=[
            PrRef(
                number=pr["number"],
                title=pr["title"],
                merged_at=(pr.get("pull_request") or {}).get("merged_at"),
            )
            for pr in mine[:RECENT_MERGED]
        ],
        reverted=reverted,
        is_new_author=merged["total_count"] == 0,
        notes=notes,
    )


class CiStatus(BaseModel):
    repo: str
    sha: str
    total: int
    state: Literal["failing", "pending", "passing", "none"]
    counts: dict[str, int]
    failing: list[str]
    notes: list[str]


async def ci_status(gh: GitHubClient, repo: str, sha: str) -> CiStatus:
    """Summary of the check runs on a commit."""
    runs, more = await gh.get_pages(
        f"/repos/{repo}/commits/{sha}/check-runs",
        {"per_page": 100},
        max_pages=MAX_CHECK_PAGES,
        items_key="check_runs",
    )
    counts: dict[str, int] = {}
    failing: list[str] = []
    for run in runs:
        outcome = run["conclusion"] if run["status"] == "completed" else "pending"
        outcome = outcome or "pending"
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome in FAILING_CONCLUSIONS:
            failing.append(run["name"])

    if failing:
        state = "failing"
    elif counts.get("pending"):
        state = "pending"
    elif not runs:
        state = "none"
    else:
        state = "passing"
    notes = [f"only the first {len(runs)} check runs were read"] if more else []
    return CiStatus(
        repo=repo,
        sha=sha,
        total=len(runs),
        state=state,
        counts=counts,
        failing=failing,
        notes=notes,
    )
