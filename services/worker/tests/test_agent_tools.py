"""The agent's four GitHub tools, replayed from recorded encode/httpx responses (SPEC_W3)."""

from datetime import datetime

import pytest
from agent.tools import (
    MAX_PATCH_CHARS,
    author_history,
    ci_status,
    file_churn,
    get_diff,
)

REPO = "encode/httpx"


def _mock_pr(gh_api, load_github, n, *, pr=None, files=None):
    gh_api.get(f"/repos/{REPO}/pulls/{n}").respond(json=pr or load_github(f"pr_{n}.json"))
    return gh_api.get(f"/repos/{REPO}/pulls/{n}/files").respond(
        json=files if files is not None else load_github(f"pr_{n}_files.json")
    )


# get_diff


async def test_diff_of_small_pr_is_complete(github, gh_api, load_github):
    _mock_pr(gh_api, load_github, 3174)

    diff = await get_diff(github, REPO, 3174)

    assert (diff.author, diff.base_ref, diff.changed_files) == ("shinyzhu", "master", 5)
    assert (diff.additions, diff.deletions) == (20, 20)
    assert len(diff.files) == 5
    assert all(f.patch and not f.patch_truncated for f in diff.files)
    assert (diff.truncated, diff.notes) == (False, [])


async def test_diff_truncates_a_large_patch_and_says_so(github, gh_api, load_github):
    _mock_pr(gh_api, load_github, 3783)

    diff = await get_diff(github, REPO, 3783)

    client = diff.files[0]
    assert client.filename == "httpx/_client.py"
    assert client.patch_truncated and len(client.patch) == MAX_PATCH_CHARS
    assert not diff.files[1].patch_truncated
    assert diff.truncated
    assert diff.notes == ["patch for httpx/_client.py truncated to 4000 of 4863 chars"]


async def test_diff_drops_patches_once_the_total_budget_is_spent(github, gh_api, load_github):
    template = load_github("pr_3174_files.json")[0]
    files = [{**template, "filename": f"f{i}.py", "patch": "x" * 3_500} for i in range(8)]
    pr = {**load_github("pr_3174.json"), "changed_files": 8}
    _mock_pr(gh_api, load_github, 3174, pr=pr, files=files)

    diff = await get_diff(github, REPO, 3174)

    sizes = [len(f.patch) if f.patch else None for f in diff.files]
    assert sizes == [3_500] * 5 + [2_500, None, None]  # 5 x 3500 + 2500 = 20,000 budget
    assert all(f.additions == template["additions"] for f in diff.files)  # stats are kept
    assert diff.notes == [
        "patch for f5.py truncated to 2500 of 3500 chars",
        "patches omitted for 2 more files: 20000 char budget spent",
    ]


async def test_diff_notes_files_github_sent_no_patch_for(github, gh_api, load_github):
    files = load_github("pr_3174_files.json")
    files[1] = {**files[1], "filename": "docs/img/logo.png", "patch": None}
    _mock_pr(gh_api, load_github, 3174, files=files)

    diff = await get_diff(github, REPO, 3174)

    assert diff.files[1].patch is None and not diff.files[1].patch_truncated
    assert diff.notes == ["GitHub sent no patch (binary or too large) for: docs/img/logo.png"]


async def test_diff_lists_at_most_300_files_but_reports_real_totals(github, gh_api, load_github):
    template = load_github("pr_3174_files.json")[0]
    pr = {**load_github("pr_3174.json"), "changed_files": 350}
    gh_api.get(f"/repos/{REPO}/pulls/3174").respond(json=pr)
    nxt = "https://api.github.com/repositories/1/pulls/3174/files?per_page=100&page={}"
    first = gh_api.get(f"/repos/{REPO}/pulls/3174/files")
    pages = [first] + [gh_api.get(nxt.format(p)) for p in (2, 3, 4)]
    for p, route in enumerate(pages, start=1):
        batch = [{**template, "filename": f"p{p}_{i}.py"} for i in range(100 if p < 4 else 50)]
        headers = {"link": f'<{nxt.format(p + 1)}>; rel="next"'} if p < 4 else {}
        route.respond(json=batch, headers=headers)

    diff = await get_diff(github, REPO, 3174)

    assert len(diff.files) == 300 and diff.changed_files == 350
    assert pages[3].call_count == 0
    assert diff.notes[0] == "350 files changed; only the first 300 are listed"


# file_churn


def _mock_churn(gh_api, since, commits_by_path):
    return {
        path: gh_api.get(f"/repos/{REPO}/commits", params={"path": path, "since": since}).respond(
            json=commits
        )
        for path, commits in commits_by_path.items()
    }


async def test_churn_counts_commits_per_path_since_window_start(github, gh_api, load_github):
    recorded = load_github("churn.json")
    routes = _mock_churn(gh_api, "2024-02-05T00:00:00Z", recorded["commits"])
    now = datetime.fromisoformat(recorded["now"])

    churn = await file_churn(github, REPO, list(recorded["commits"]), days=90, now=now)

    assert churn.since == "2024-02-05T00:00:00Z"
    assert {p: c.commits for p, c in churn.files.items()} == {
        "README.md": 6,
        "docs/advanced/proxies.md": 2,
        "docs/contributing.md": 4,
        "docs/http2.md": 2,
        "docs/index.md": 5,
    }
    assert not any(c.capped for c in churn.files.values())
    assert all(r.call_count == 1 for r in routes.values())
    assert churn.notes == []


async def test_churn_flags_a_full_page_as_a_lower_bound(github, gh_api):
    now = datetime.fromisoformat("2024-05-05T00:00:00Z")
    _mock_churn(gh_api, "2024-04-05T00:00:00Z", {"hot.py": [{"sha": str(i)} for i in range(100)]})

    churn = await file_churn(github, REPO, ["hot.py"], days=30, now=now)

    assert churn.files["hot.py"].commits == 100 and churn.files["hot.py"].capped


async def test_churn_checks_at_most_20_paths(github, gh_api):
    now = datetime.fromisoformat("2024-05-05T00:00:00Z")
    route = gh_api.get(f"/repos/{REPO}/commits").respond(json=[])

    churn = await file_churn(github, REPO, [f"f{i}.py" for i in range(25)], now=now)

    assert len(churn.files) == 20 and route.call_count == 20
    assert churn.notes == ["only the first 20 of 25 paths were checked"]


# author_history


def _mock_search(gh_api, author, merged, reverts):
    gh_api.get(
        "/search/issues", params={"q": f"repo:{REPO} is:pr is:merged author:{author}"}
    ).respond(json=merged)
    gh_api.get(
        "/search/issues", params={"q": f"repo:{REPO} is:pr is:merged in:title Revert"}
    ).respond(json=reverts)


async def test_author_with_a_pr_reverted_via_the_revert_button(github, gh_api, load_github):
    _mock_search(
        gh_api,
        "shinyzhu",
        load_github("search_merged_author.json"),
        load_github("search_reverts.json"),
    )

    history = await author_history(github, REPO, "shinyzhu")

    assert history.merged_count == 1 and not history.is_new_author
    assert [p.number for p in history.recent_merged] == [3174]
    assert history.recent_merged[0].merged_at == "2024-05-05T16:24:16Z"
    assert [(r.pr, r.revert_pr) for r in history.reverted] == [(3174, 3192)]  # "Reverts …#3174"
    assert history.notes == []


@pytest.mark.parametrize(
    ("number", "title", "revert"),
    [
        # Real: #2539 is titled `Revert "Raise `TypeError` on invalid query params. (#2523)"`.
        (2523, "Raise `TypeError` on invalid query params.", 2539),
        # Real #898 with its "Reverts encode/httpx#866" body blanked: matched by title alone.
        (866, "Add `Content-Length` header for empty bytestream", 898),
    ],
)
async def test_revert_matched_by_title(github, gh_api, load_github, number, title, revert):
    reverts = load_github("search_reverts.json")
    for item in reverts["items"]:
        item["body"] = ""
    merged = {
        "total_count": 489,
        "items": [{"number": number, "title": title, "body": "", "pull_request": {}}],
    }
    _mock_search(gh_api, "someone", merged, reverts)

    history = await author_history(github, REPO, "someone")

    assert [(r.pr, r.revert_pr) for r in history.reverted] == [(number, revert)]
    assert history.notes == ["reverts checked against the newest 1 merged PRs only"]


async def test_new_author(github, gh_api, load_github):
    _mock_search(
        gh_api, "first-timer", {"total_count": 0, "items": []}, load_github("search_reverts.json")
    )

    history = await author_history(github, REPO, "first-timer")

    assert history.is_new_author and history.merged_count == 0
    assert (history.recent_merged, history.reverted) == ([], [])


# ci_status


def _mock_checks(gh_api, sha, body):
    gh_api.get(f"/repos/{REPO}/commits/{sha}/check-runs").respond(json=body)


async def test_ci_failing(github, gh_api, load_github):
    _mock_checks(gh_api, "abc", load_github("check_runs_failing.json"))

    ci = await ci_status(github, REPO, "abc")

    assert (ci.state, ci.total, ci.failing) == ("failing", 5, ["Python 3.13"])
    assert ci.counts == {"failure": 1, "cancelled": 4}


async def test_ci_passing_ignores_a_cancelled_run(github, gh_api, load_github):
    _mock_checks(gh_api, "abc", load_github("check_runs_passing.json"))

    ci = await ci_status(github, REPO, "abc")

    assert (ci.state, ci.total, ci.failing) == ("passing", 6, [])
    assert ci.counts == {"success": 5, "cancelled": 1}


async def test_ci_pending_while_runs_are_unfinished(github, gh_api, load_github):
    runs = load_github("check_runs_passing.json")["check_runs"]
    runs[0] = {**runs[0], "status": "in_progress", "conclusion": None}
    runs[1] = {**runs[1], "status": "queued", "conclusion": None}
    _mock_checks(gh_api, "abc", {"total_count": len(runs), "check_runs": runs})

    ci = await ci_status(github, REPO, "abc")

    assert ci.state == "pending"
    assert ci.counts == {"pending": 2, "success": 4}


async def test_ci_none_without_check_runs(github, gh_api):
    _mock_checks(gh_api, "abc", {"total_count": 0, "check_runs": []})

    ci = await ci_status(github, REPO, "abc")

    assert (ci.state, ci.total, ci.counts) == ("none", 0, {})
