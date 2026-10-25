"""scripts/run_samples.py: picks recently merged PRs and writes results + summary (SPEC_W3 D5)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("run_samples", ROOT / "scripts/run_samples.py")
run_samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_samples)

from agent.service import assess_pr  # noqa: E402
from worker.settings import WorkerSettings  # noqa: E402

REPO = "encode/httpx"
GOOD = {
    "factors": [{"kind": "failing_ci", "severity": 3, "evidence": "Python 3.13: failure"}],
    "summary": "CI is failing.",
    "confidence": 0.9,
}


async def test_recent_merged_skips_unmerged_and_sorts_newest_first(github, gh_api):
    gh_api.get(f"/repos/{REPO}/pulls").respond(
        json=[
            {"number": 1, "title": "a", "merged_at": "2026-01-01T00:00:00Z"},
            {"number": 2, "title": "closed, not merged", "merged_at": None},
            {"number": 3, "title": "c", "merged_at": "2026-03-01T00:00:00Z"},
            {"number": 4, "title": "d", "merged_at": "2026-02-01T00:00:00Z"},
        ]
    )

    prs = await run_samples.recent_merged(github, REPO, 2)

    assert [p["number"] for p in prs] == [3, 4]


async def test_summary_table_from_saved_samples(
    github, gh_api, load_github, fake_llm, llm_reply, tmp_path
):
    gh_api.get(f"/repos/{REPO}/pulls/3783").respond(json=load_github("pr_3783.json"))
    gh_api.get(f"/repos/{REPO}/pulls/3783/files").respond(json=load_github("pr_3783_files.json"))
    settings = WorkerSettings(
        _env_file=None,
        llm_model="m",
        llm_input_cost_per_mtok=1.0,
        llm_output_cost_per_mtok=4.0,
    )
    run = await assess_pr(
        fake_llm(llm_reply.text("ok"), llm_reply.text(GOOD)), github, REPO, 3783, settings
    )
    for number, title, merged in ((3783, "a | b", "2026-03-01"), (3700, "older", "2026-01-01")):
        sample = run_samples.Sample(
            pr={"number": number, "title": title, "merged_at": merged},
            verdict=run.verdict,
            trace=run.trace,
        )
        (tmp_path / f"{number}.json").write_text(sample.model_dump_json())

    table = run_samples.summary_table(run_samples.load_samples(tmp_path), REPO)

    rows = [line for line in table.splitlines() if line.startswith("| [#")]
    assert [r.split("]")[0] for r in rows] == ["| [#3783", "| [#3700"]  # newest merge first
    assert "| a \\| b | medium | 30 | failing_ci (3) | 0 | 200/40 |" in rows[0]
    assert rows[0].endswith("| $0.0004 |")
    assert "2 of 2 scored; total cost $0.0007." in table
