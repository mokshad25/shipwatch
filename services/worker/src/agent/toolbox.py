"""The D1 tools as the LLM sees them: bound to the PR under review, with one-line summaries.

The repo and PR number are fixed by the caller, not chosen by the model, so a prompt-injected
diff can't send the agent off to read another repository.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent.github import GitHubClient
from agent.tools import AuthorHistory, CiStatus, Diff, FileChurn
from agent.tools import author_history as _author_history
from agent.tools import ci_status as _ci_status
from agent.tools import file_churn as _file_churn
from agent.tools import get_diff as _get_diff


class NoArgs(BaseModel):
    pass


class ChurnArgs(BaseModel):
    paths: list[str] = Field(description="File paths to check, e.g. from get_diff.")
    days: int = Field(default=90, ge=1, le=365, description="Look-back window in days.")


class AuthorArgs(BaseModel):
    author: str = Field(description="GitHub login, e.g. the PR author from get_diff.")


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[BaseModel], Awaitable[BaseModel]]
    summarize: Callable[[Any], str]

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function schema; both langchain-openai and langchain-anthropic take it."""
        parameters = self.args.model_json_schema()
        parameters.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def _diff_summary(d: Diff) -> str:
    cut = "truncated" if d.truncated else "not truncated"
    return f"{d.changed_files} files +{d.additions}/-{d.deletions}, {cut}"


def _churn_summary(c: FileChurn) -> str:
    hottest = sorted(c.files.items(), key=lambda kv: -kv[1].commits)[:3]
    top = ", ".join(f"{p}={v.commits}{'+' if v.capped else ''}" for p, v in hottest)
    return f"{len(c.files)} paths over {c.days}d; top: {top or 'none'}"


def _author_summary(a: AuthorHistory) -> str:
    return f"{a.author}: {a.merged_count} merged, {len(a.reverted)} reverted"


def _ci_summary(c: CiStatus) -> str:
    failing = f" ({', '.join(c.failing)})" if c.failing else ""
    return f"{c.state}: {c.total} check runs{failing}"


def make_toolbox(
    gh: GitHubClient, repo: str, pr_number: int, *, point_in_time: bool = False
) -> dict[str, AgentTool]:
    """`point_in_time`: author_history sees only PRs merged before this PR was opened. Evals set
    it, since they review PRs long after the fact (file_churn always works that way)."""

    async def get_diff(_: NoArgs) -> Diff:
        return await _get_diff(gh, repo, pr_number)

    async def file_churn(args: ChurnArgs) -> FileChurn:
        # On the PR's base branch, in the window before it was opened (get_diff is cached).
        diff = await _get_diff(gh, repo, pr_number)
        opened = datetime.fromisoformat(diff.created_at) if diff.created_at else None
        return await _file_churn(gh, repo, args.paths, args.days, now=opened, ref=diff.base_ref)

    async def author_history(args: AuthorArgs) -> AuthorHistory:
        before = None
        if point_in_time:
            diff = await _get_diff(gh, repo, pr_number)
            before = datetime.fromisoformat(diff.created_at) if diff.created_at else None
        return await _author_history(gh, repo, args.author, before)

    async def ci_status(_: NoArgs) -> CiStatus:
        # The PR's head commit; get_diff is cached, so this costs no extra request after it.
        diff = await _get_diff(gh, repo, pr_number)
        return await _ci_status(gh, repo, diff.head_sha)

    tools = [
        AgentTool(
            "get_diff",
            "Files changed by this PR: per-file stats and (possibly truncated) patches, exact "
            "totals, title, author, head SHA. Start here.",
            NoArgs,
            get_diff,
            _diff_summary,
        ),
        AgentTool(
            "file_churn",
            "How many commits touched each path in the days before this PR was opened. "
            "Frequently changed files are hot spots.",
            ChurnArgs,
            file_churn,
            _churn_summary,
        ),
        AgentTool(
            "author_history",
            "The author's merged PRs in this repo and which of them were later reverted.",
            AuthorArgs,
            author_history,
            _author_summary,
        ),
        AgentTool(
            "ci_status",
            "Check runs on the PR's head commit: passing, failing (with names) or pending.",
            NoArgs,
            ci_status,
            _ci_summary,
        ),
    ]
    return {t.name: t for t in tools}
