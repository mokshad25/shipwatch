"""evals/evalkit.py: caching, pending runs, metrics, reports and the CI gate (W5 D2, D5)."""

import json

import evalkit
import httpx
import openai
import pytest
from agent.cache import FileResponseCache
from agent.github import GitHubError
from worker.settings import WorkerSettings

REPO = "encode/httpx"
GOOD = {
    "factors": [{"kind": "failing_ci", "severity": 3, "evidence": "Python 3.13: failure"}],
    "summary": "CI is failing.",
    "confidence": 0.9,
}
ROW = {"repo": REPO, "number": 3783, "label": 1}
_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _settings() -> WorkerSettings:
    base = WorkerSettings(_env_file=None, llm_provider="groq", llm_model="fake")
    return evalkit.eval_settings(base, "groq:fake", "v1")


def _rate_limit() -> openai.RateLimitError:
    return openai.RateLimitError(
        "Rate limit reached", response=httpx.Response(429, request=_REQ), body=None
    )


@pytest.fixture
def recorded(gh_api, load_github):
    for n in (3783, 3174):
        gh_api.get(f"/repos/{REPO}/pulls/{n}").respond(json=load_github(f"pr_{n}.json"))
        gh_api.get(f"/repos/{REPO}/pulls/{n}/files").respond(json=load_github(f"pr_{n}_files.json"))
    return gh_api


async def test_results_are_cached_per_pr_prompt_and_model(
    github, recorded, fake_llm, llm_reply, tmp_path
):
    llm = fake_llm(llm_reply.text("ok"), llm_reply.text(GOOD))

    first = await evalkit.run_eval([ROW], llm, github, _settings(), cache_root=tmp_path)
    again = await evalkit.run_eval([ROW], fake_llm(), github, _settings(), cache_root=tmp_path)

    assert (first.fresh, again.fresh, again.pending) == (1, 0, [])
    record = again.records[0]
    assert record == first.records[0]
    assert (record.verdict.level, record.verdict.score, record.label) == ("medium", 30, 1)
    assert (record.prompt_version, record.model) == ("v1", "groq:fake")
    assert (record.stats.changed_files, record.stats.lines) == (2, 61)  # pr_3783.json
    assert evalkit.agent_cache_path(tmp_path, "groq:fake", "v1", REPO, 3783).is_file()


async def test_transient_failures_stay_pending_and_stop_the_run(
    github, recorded, fake_llm, tmp_path
):
    rows = [ROW, {"repo": REPO, "number": 3174, "label": 0}]
    llm = fake_llm(_rate_limit())  # a second PR would exhaust the script: an "unexpected error"

    result = await evalkit.run_eval(
        rows, llm, github, _settings(), cache_root=tmp_path, concurrency=1, stop_after=1
    )

    assert (result.records, result.pending) == ([], [f"{REPO}#3783", f"{REPO}#3174"])
    assert not list(tmp_path.rglob("*.json"))


async def test_a_rerun_only_runs_what_was_pending(github, recorded, fake_llm, llm_reply, tmp_path):
    # The CI gate's --resume: a run cut short by the daily cap, then a rerun of the same code.
    rows = [ROW, {"repo": REPO, "number": 3174, "label": 0}]
    first = await evalkit.run_eval(
        rows,
        fake_llm(llm_reply.text("ok"), llm_reply.text(GOOD), _rate_limit()),
        github,
        _settings(),
        cache_root=tmp_path,
        concurrency=1,
    )
    rerun = await evalkit.run_eval(
        rows,
        fake_llm(llm_reply.text("ok"), llm_reply.text(GOOD)),  # enough for one PR only
        github,
        _settings(),
        cache_root=tmp_path,
        concurrency=1,
    )

    assert (first.fresh, first.pending) == (2, [f"{REPO}#3174"])
    assert (rerun.fresh, rerun.pending) == (1, [])
    assert [r.number for r in rerun.records] == [3783, 3174]


def test_resume_results_are_kept_only_while_prs_are_pending(tmp_path):
    results = tmp_path / "eval-results"
    cached = evalkit.agent_cache_path(results, "groq:fake", "v1", REPO, 3783)
    cached.parent.mkdir(parents=True)
    cached.write_text("{}")

    evalkit.finish_resume(results, [f"{REPO}#3174"])
    assert cached.is_file()

    evalkit.finish_resume(results, [])  # complete: a rerun must sample the LLM again
    assert [p.name for p in results.rglob("*")] == ["COMPLETE"]


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_load_dataset_alternates_labels_and_picks_a_subset(tmp_path):
    rows = [{"repo": "o/r", "number": n, "label": int(n < 3)} for n in (1, 2, 3, 4, 5)]
    data = _jsonl(tmp_path / "prs.jsonl", rows)
    subset = tmp_path / "subset.txt"
    subset.write_text("# CI subset\no/r#5\no/r#2\n")

    assert [r["number"] for r in evalkit.load_dataset(data)] == [1, 3, 2, 4, 5]
    assert [r["number"] for r in evalkit.load_dataset(data, subset)] == [2, 5]
    subset.write_text("o/r#9\n")
    with pytest.raises(ValueError, match="o/r#9"):
        evalkit.load_dataset(data, subset)


def test_load_dataset_rotates_repos(tmp_path):
    rows = [{"repo": repo, "number": n, "label": 1} for repo, n in [("a", 1), ("a", 2), ("b", 3)]]

    loaded = evalkit.load_dataset(_jsonl(tmp_path / "prs.jsonl", rows))

    assert [r["number"] for r in loaded] == [1, 3, 2]


def test_classification():
    pairs = [(1, True), (1, True), (1, False), (0, True), (0, False), (0, False)]

    c = evalkit.classification(pairs)

    assert c["confusion"] == {"tp": 2, "fp": 1, "fn": 1, "tn": 2}
    assert (c["precision"], c["recall"], c["f1"]) == (0.667, 0.667, 0.667)
    assert evalkit.classification([(1, False), (0, False)])["f1"] == 0.0  # no positives predicted


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 21)]
    assert (evalkit.percentile(values, 50), evalkit.percentile(values, 95)) == (10.0, 19.0)
    assert evalkit.percentile([], 50) is None


async def test_report_metrics_leave_needs_review_out(
    github, recorded, fake_llm, llm_reply, tmp_path
):
    t = llm_reply.text
    llm = fake_llm(t("ok"), t(GOOD), t("ok"), t("not json"), t("still not json"))
    rows = [ROW, {"repo": REPO, "number": 3174, "label": 0}]
    result = await evalkit.run_eval(
        rows, llm, github, _settings(), cache_root=tmp_path, concurrency=1
    )

    report = evalkit.build_report(
        result, model="groq:fake", prompt="v1", dataset="prs.jsonl", n_rows=2, threshold="high"
    )
    markdown = evalkit.render_markdown(report)

    m = report["metrics"]
    assert (m["scored"], m["needs_review"]) == (1, 1)  # #3174's output never validated
    assert m["thresholds"]["high"]["confusion"] == {"tp": 0, "fp": 0, "fn": 1, "tn": 0}
    assert m["thresholds"]["medium"]["confusion"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 0}
    assert "| ≥ high (primary) | 0.00 | 0.00 | 0.00 | 0 | 0 | 1 | 0 |" in markdown
    assert "| [encode/httpx#3783](https://github.com/encode/httpx/pull/3783) | 1 | medium | 30" in (
        markdown
    )
    path = evalkit.write_report(report, markdown, tmp_path / "reports")
    assert json.loads(path.read_text())["metrics"] == m
    assert path.with_suffix(".md").read_text() == markdown


def _report(f1: float, pending: list[str] | None = None) -> dict:
    return {
        "pending": pending or [],
        "metrics": {"scored": 7, "thresholds": {"medium": {"f1": f1}, "high": {"f1": 0.0}}},
    }


@pytest.mark.parametrize(
    ("f1", "pending", "ok", "text"),
    [
        (0.62, None, True, "**Gate: PASS** — F1 at ≥ medium: 0.62 vs baseline 0.60 (+2.0 points"),
        (0.55, None, True, "(-5.0 points; fails below −5)"),
        (0.54, None, False, "**Gate: FAIL** — F1 at ≥ medium: 0.54"),
        (0.90, ["o/r#1"], False, "1 PRs didn't run"),
        # A partial run still fails, but shows what the PRs that ran say (the W5·D5 demo PR's
        # F1 fell to 0 while 3 PRs were rate-limited, and the old message only blamed the limits).
        (0.0, ["o/r#1", "o/r#2", "o/r#3"], False, "On the 7 scored: F1 at ≥ medium: 0.00 vs"),
    ],
)
def test_gate(f1, pending, ok, text):
    baseline = {"threshold": "medium", "f1": 0.60}

    passed, message = evalkit.check_gate(_report(f1, pending), baseline, 5)

    assert passed is ok and text in message


async def test_file_cache_replays_and_offline_mode_refuses_the_network(tmp_path):
    cache = FileResponseCache(tmp_path / "github")
    key = cache.key("/repos/o/r/pulls/1", None)
    assert await cache.get(key) is None
    await cache.set(key, {"body": {"number": 1}, "next": None})
    assert await cache.get(key) == {"body": {"number": 1}, "next": None}

    settings = WorkerSettings(_env_file=None)
    async with evalkit.make_github(settings, tmp_path, offline=True) as gh:
        assert await gh.get_json("/repos/o/r/pulls/1") == {"number": 1}
        with pytest.raises(GitHubError, match="not in the recorded GitHub cache"):
            await gh.get_json("/repos/o/r/pulls/2")


def test_eval_settings_pick_model_prompt_and_prices():
    base = WorkerSettings(_env_file=None, llm_provider="openai", llm_model="x")

    s = evalkit.eval_settings(base, "groq:openai/gpt-oss-20b", "v2")

    assert (s.llm_provider, s.llm_model, s.prompt_version) == ("groq", "openai/gpt-oss-20b", "v2")
    assert (s.llm_input_cost_per_mtok, s.llm_output_cost_per_mtok) == (0.075, 0.30)
    assert s.llm_max_retries >= 6
