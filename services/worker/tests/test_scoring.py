"""Table-driven tests for the deterministic score (SPEC_W3 D4), against the shipped weights.yaml."""

import pytest
from agent.assessment import RiskAssessment, RiskFactor
from agent.scoring import DiffStats, load_weights, score

SMALL = DiffStats(additions=8, deletions=2, changed_files=1)


def _assess(*factors: tuple[str, int], confidence: float = 0.8) -> RiskAssessment:
    return RiskAssessment(
        factors=[RiskFactor(kind=k, severity=s, evidence="from tool output") for k, s in factors],
        summary="test",
        confidence=confidence,
    )


CASES = [
    # (id, factors, diff, expected score, level, rules fired)
    ("empty-small-diff", [], SMALL, 0, "low", []),
    ("one-minor-factor", [("missing_tests", 1)], SMALL, 5, "low", []),
    ("failing-ci-notable", [("failing_ci", 2)], SMALL, 20, "low", []),
    ("failing-ci-serious", [("failing_ci", 3)], SMALL, 30, "medium", ["serious_failing_ci"]),
    ("just-below-medium", [("risky_paths", 3)], SMALL, 24, "low", []),
    ("exactly-medium", [("missing_tests", 2), ("hot_files", 3)], SMALL, 25, "medium", []),
    ("two-serious-medium", [("risky_paths", 3), ("migration", 3)], SMALL, 51, "medium", []),
    (
        "weights-reach-high",
        [("risky_paths", 3), ("migration", 3), ("failing_ci", 1)],
        SMALL,
        61,
        "high",
        ["migration_with_failing_ci"],
    ),
    (
        "migration-plus-failing-ci-always-high",
        [("migration", 1), ("failing_ci", 1)],
        SMALL,
        55,
        "high",
        ["migration_with_failing_ci"],
    ),
    ("duplicate-kind-counts-once", [("hot_files", 1), ("hot_files", 3)], SMALL, 15, "low", []),
    (
        "huge-diff-without-any-factor",
        [],
        DiffStats(additions=1100, deletions=100, changed_files=3),
        25,
        "medium",
        ["huge_diff"],
    ),
    (
        "size-points-below-rule",
        [],
        DiffStats(additions=400, deletions=200, changed_files=60),
        20,
        "low",
        [],
    ),
    (
        "size-adds-to-factors",
        [("failing_ci", 2)],
        DiffStats(additions=300, deletions=250, changed_files=4),
        30,
        "medium",
        [],
    ),
    ("new-author-and-config", [("new_author", 3), ("config_change", 2)], SMALL, 24, "low", []),
    (
        "everything-serious-clamps-to-100",
        [(k, 3) for k in load_weights().factor_weights],
        DiffStats(additions=5000, deletions=0, changed_files=80),
        100,
        "high",
        ["migration_with_failing_ci", "serious_failing_ci", "huge_diff"],
    ),
]


@pytest.mark.parametrize(("factors", "diff", "expected", "level", "rules"),
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])  # fmt: skip
def test_score(factors, diff, expected, level, rules):
    result = score(_assess(*factors), diff)

    assert (result.score, result.level, result.rules) == (expected, level, rules)


def test_confidence_never_moves_the_score():
    factors = [("risky_paths", 2), ("hot_files", 2)]

    low, high = (score(_assess(*factors, confidence=c), SMALL) for c in (0.05, 0.95))

    assert low == high


def test_breakdown_explains_the_score():
    result = score(
        _assess(("migration", 2), ("missing_tests", 1)),
        DiffStats(additions=150, deletions=60, changed_files=25),
    )

    assert result.breakdown == {
        "migration": 18,
        "missing_tests": 5,
        "size_lines": 5,
        "size_files": 5,
    }
    assert result.score == sum(result.breakdown.values()) == 33


def test_same_input_same_output():
    assessment = _assess(("failing_ci", 3), ("large_diff", 2))

    assert len({score(assessment, SMALL).model_dump_json() for _ in range(5)}) == 1
