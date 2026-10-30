"""evals/build_dataset.py: revert/hotfix labels, clean negatives, the jsonl rows (W5 D1)."""

import importlib.util
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("build_dataset", ROOT / "evals/build_dataset.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

REPO = "o/r"
SINCE = datetime(2026, 1, 1, tzinfo=UTC)
AS_OF = datetime(2026, 6, 30, tzinfo=UTC)


def _pr(number, title, merged, *, body="", login="dev", updated=None):
    return {
        "number": number,
        "title": title,
        "body": body,
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "merged_at": f"{merged}T12:00:00Z" if merged else None,
        "updated_at": f"{updated or merged or '2026-01-02'}T12:00:00Z",
    }


def _cross_ref(title, created, labels=()):
    return {
        "event": "cross-referenced",
        "created_at": f"{created}T00:00:00Z",
        "source": {"issue": {"title": title, "labels": [{"name": n} for n in labels]}},
    }


def test_reverts_matched_by_body_title_number_and_exact_title():
    prs = [
        _pr(1, "Add A", "2026-02-01"),
        _pr(2, "Add B", "2026-02-01"),
        _pr(3, "Add C", "2026-02-01"),
        _pr(4, 'Revert "Add A"', "2026-02-03", body="Reverts o/r#1"),
        _pr(5, 'Revert "Add B (#2)"', "2026-02-04"),
        _pr(6, 'Revert "Add C"', "2026-02-11"),
    ]

    found = bd.find_reverts(REPO, prs)

    assert {n: (lb.by_pr, lb.days_after) for n, lb in found.items()} == {
        1: (4, 2.0),
        2: (5, 3.0),
        3: (6, 10.0),
    }
    assert found[1].reason == "reverted by #4 2.0 days after merge"


def test_reverts_skip_relands_late_reverts_and_keep_the_first():
    prs = [
        _pr(1, "Add A", "2026-02-01"),
        _pr(2, 'Revert "Add A"', "2026-02-02", body="Reverts o/r#1"),
        _pr(3, 'Revert "Revert "Add A""', "2026-02-03", body="Reverts o/r#2"),
        _pr(4, 'Revert "Add A"', "2026-02-05", body="Reverts o/r#1"),
        _pr(5, "Add B", "2026-02-01"),
        _pr(6, 'Revert "Add B"', "2026-03-15", body="Reverts o/r#5"),  # 42 days later
    ]

    found = bd.find_reverts(REPO, prs)

    assert {n: lb.by_pr for n, lb in found.items()} == {1: 2}


def test_hotfix_needs_a_fix_title_a_reference_and_seven_days():
    prs = [
        _pr(1, "Speed up parser", "2026-03-01"),
        _pr(2, "Fix crash in parser", "2026-03-03", body="Follow-up to #1"),
        _pr(3, "Add cache", "2026-03-01"),
        _pr(4, "Tidy cache code", "2026-03-02", body="After #3"),  # not fix-like
        _pr(5, "Add flags", "2026-03-01"),
        _pr(6, "Fix flags", "2026-03-09", body="Broken by https://github.com/o/r/pull/5"),  # 8 days
        _pr(7, "Add docs", "2026-03-01"),
        _pr(8, "Fix typo in docs", "2026-03-02"),  # no reference
    ]

    found = bd.find_hotfixes(prs)

    assert {n: (lb.kind, lb.by_pr, lb.days_after) for n, lb in found.items()} == {
        1: ("hotfix", 2, 2.0)
    }


async def test_list_merged_keeps_merged_in_window_and_stops_paging(github, gh_api):
    page = [_pr(1000 - i, f"pr {i}", "2026-03-01", updated="2026-03-02") for i in range(98)]
    page.append(_pr(2, "closed unmerged", None, updated="2026-02-01"))
    page.append(_pr(1, "old", "2025-12-20", updated="2025-12-30"))  # updated before SINCE
    route = gh_api.get(f"/repos/{REPO}/pulls").respond(json=page)

    prs = await bd.list_merged(github, REPO, SINCE, AS_OF)

    assert len(prs) == 98 and route.call_count == 1  # page 2 never fetched
    assert route.calls[0].request.url.params["sort"] == "updated"


async def test_trouble_reference_within_thirty_days(github, gh_api):
    timelines = {
        1: [_cross_ref("Docs: mention the new API", "2026-02-05")],
        2: [_cross_ref("Parser panics on empty input", "2026-02-05", labels=["bug"])],
        3: [_cross_ref("Regression in X", "2026-04-01")],  # too late to count
    }
    gh_api.get(url__regex=rf"/repos/{REPO}/issues/(?P<n>\d+)/timeline").mock(
        side_effect=lambda request, n: httpx.Response(200, json=timelines[int(n)])
    )

    results = [
        await bd.has_trouble_reference(github, REPO, _pr(n, "x", "2026-02-01")) for n in (1, 2, 3)
    ]

    assert results == [False, True, False]


async def test_negatives_from_the_closest_month_skip_troubled_ones(github, gh_api):
    timelines = {7: [_cross_ref("Y broke the build", "2026-04-05")], 12: []}
    gh_api.get(url__regex=rf"/repos/{REPO}/issues/(?P<n>\d+)/timeline").mock(
        side_effect=lambda request, n: httpx.Response(200, json=timelines[int(n)])
    )
    pool = [_pr(12, "Add Z", "2026-05-01"), _pr(7, "Add Y", "2026-04-01")]

    chosen = await bd.pick_negatives(
        github, REPO, [_pr(1, "p", "2026-04-02")], pool, random.Random(0)
    )

    assert [p["number"] for p in chosen] == [12]


LISTING = [
    _pr(1, "Add feature A", "2026-02-01"),
    _pr(2, 'Revert "Add feature A"', "2026-02-03", body="Reverts o/r#1"),
    _pr(3, "Parser speedup", "2026-03-01"),
    _pr(4, "Fix crash from #3", "2026-03-03"),
    _pr(5, "Docs tweak", "2026-02-10"),
    _pr(6, "Refactor X", "2026-03-05"),
    _pr(8, "Bump deps", "2026-02-02", login="dependabot[bot]"),
    _pr(9, "Late PR", "2026-06-20"),  # within 30 days of AS_OF: no full quiet window yet
    _pr(10, "Tweak Z", "2026-04-12"),
    _pr(11, "Regression after #10", "2026-04-25"),  # 13 days: not a hotfix, but trouble
]


async def test_build_balanced_rows(github, gh_api, tmp_path):
    gh_api.get(f"/repos/{REPO}/pulls").respond(json=LISTING)
    gh_api.get(url__regex=rf"/repos/{REPO}/issues/\d+/timeline").respond(json=[])

    rows = await bd.build(github, [REPO], since=SINCE, as_of=AS_OF, target=2, seed=0)
    again = await bd.build(github, [REPO], since=SINCE, as_of=AS_OF, target=2, seed=0)

    assert rows == again
    assert [(r["number"], r["label"], r["evidence"]["kind"]) for r in rows] == [
        (1, 1, "revert"),
        (3, 1, "hotfix"),
        (5, 0, "clean"),  # Feb, like #1 (#8 is a bot)
        (6, 0, "clean"),  # Mar, like #3
    ]
    assert rows[0] == {
        "repo": REPO,
        "number": 1,
        "label": 1,
        "reason": "reverted by #2 2.0 days after merge",
        "title": "Add feature A",
        "author": "dev",
        "merged_at": "2026-02-01T12:00:00Z",
        "evidence": {"kind": "revert", "by_pr": 2, "days_after": 2.0},
    }
    assert rows[2]["evidence"] == {"kind": "clean", "by_pr": None, "days_after": None}

    bd.write_jsonl(rows, tmp_path / "prs.jsonl")
    lines = (tmp_path / "prs.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == rows


def test_positives_round_robin_across_repos_with_a_cap():
    lb = bd.Label("revert", 99, 1.0)
    candidates = {
        "a/a": [(_pr(n, "x", "2026-02-01"), lb) for n in (1, 2, 3, 4)],
        "b/b": [(_pr(n, "x", "2026-02-01"), lb) for n in (10,)],
    }

    picked = bd.pick_positives(candidates, target=4, per_repo_max=2)

    assert [(repo, pr["number"]) for repo, pr, _ in picked] == [("a/a", 1), ("b/b", 10), ("a/a", 2)]


def test_troubled_marks_prs_a_bad_sounding_pr_references_within_thirty_days():
    found = bd.troubled(LISTING + [_pr(12, "Bug: crash since #6", "2026-05-20")])  # 76 days

    assert found == {1, 3, 10}  # by the revert (#2), the hotfix (#4) and the regression (#11)


async def test_retrying_survives_a_transport_error(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(bd.asyncio, "sleep", no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return "ok"

    assert await bd._retrying(flaky) == "ok" and len(calls) == 2
