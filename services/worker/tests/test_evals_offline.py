"""evals/compare.py and evals/tune_weights.py on synthetic cached results (W5 D3, D4)."""

import importlib.util
from pathlib import Path

import evalkit
from agent.assessment import RiskAssessment
from agent.scoring import WEIGHTS_PATH, DiffStats, Weights, load_weights
from agent.service import Verdict
from agent.trace import Trace

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"evals/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = _load("compare")
tune_weights = _load("tune_weights")


def _record(
    n: int, label: int, kinds: list[str], *, level: str = "low", model="groq:m", prompt="v1"
):
    factors = [{"kind": k, "severity": 3, "evidence": "x"} for k in kinds]
    assessment = RiskAssessment(factors=factors, summary="s", confidence=0.9)
    return evalkit.EvalRecord(
        repo="o/r",
        number=n,
        label=label,
        model=model,
        prompt_version=prompt,
        stats=DiffStats(additions=10, deletions=0, changed_files=1),
        verdict=Verdict(
            repo="o/r", pr_number=n, head_sha="a", level=level, score=0, assessment=assessment
        ),  # fmt: skip
        trace=Trace(repo="o/r", pr_number=n, provider="groq", model="m"),
    )


def _save(cache: Path, record: evalkit.EvalRecord) -> None:
    path = evalkit.agent_cache_path(
        cache, record.model, record.prompt_version, "o/r", record.number
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json())


def test_compare_uses_only_prs_every_pair_finished(tmp_path):
    rows = [{"repo": "o/r", "number": n, "label": n % 2} for n in (1, 2, 3)]
    for n in (1, 2, 3):
        _save(tmp_path, _record(n, n % 2, [], level="high" if n % 2 else "low"))
    for n in (1, 2):  # v2 hasn't run #3 yet
        _save(tmp_path, _record(n, n % 2, [], level="low", prompt="v2"))

    table, results = compare.compare(rows, ["v1", "v2"], ["groq:m"], tmp_path)

    assert "Compared on the 2 PRs (of 3)" in table
    by_prompt = {r["prompt"]: r for r in results}
    assert (by_prompt["v1"]["done"], by_prompt["v2"]["done"]) == (3, 2)
    assert by_prompt["v1"]["metrics"]["thresholds"]["high"]["f1"] == 1.0  # #1 risky, #2 clean
    assert by_prompt["v2"]["metrics"]["thresholds"]["high"]["f1"] == 0.0
    assert "| v1 | `m` | 3/3 | 1.00 |" in table


def test_split_is_stratified_and_seeded():
    records = [_record(n, int(n < 10), []) for n in range(20)]

    train, test = tune_weights.split(records, seed=0)

    assert (len(train), len(test)) == (14, 6)
    assert sum(r.label for r in test) == 3
    assert [r.key for r in tune_weights.split(records, seed=0)[1]] == [r.key for r in test]


def test_tuning_raises_a_weight_that_separates_the_classes(tmp_path):
    # Every risky PR has missing_tests at severity 3 (5 x 3 = 15 points: low); clean PRs have none.
    records = [_record(n, 1, ["missing_tests"]) for n in range(10)]
    records += [_record(n, 0, []) for n in range(10, 20)]

    result = tune_weights.tune(records, "medium")

    assert result["before"]["train_f1"] == 0.0
    assert result["after"]["train_f1"] == result["after"]["test_f1"] == 1.0
    assert result["keep"] is True
    assert "missing_tests" in result["changes"] or "levels.medium" in result["changes"]


def test_apply_rewrites_only_the_numbers(tmp_path):
    path = tmp_path / "weights.yaml"
    path.write_text(WEIGHTS_PATH.read_text())
    data = load_weights().model_dump()
    data["factor_weights"]["risky_paths"] = 12
    data["levels"]["medium"] = 20

    tune_weights.apply(path, Weights.model_validate(data))

    text = path.read_text()
    assert "  risky_paths: 12\n" in text and "  medium: 20\n" in text
    assert text.splitlines()[0] == WEIGHTS_PATH.read_text().splitlines()[0]  # comments kept
    reloaded = Weights.model_validate(__import__("yaml").safe_load(text))
    assert reloaded.factor_weights["risky_paths"] == 12 and reloaded.levels.medium == 20
    assert reloaded.factor_weights["migration"] == load_weights().factor_weights["migration"]
