"""Turn a RiskAssessment and raw diff stats into a score and level (SPEC_W3 D4).

A pure function of its inputs and weights.yaml: the LLM proposes factors, this decides the score.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from agent.assessment import FactorKind, RiskAssessment

Level = Literal["low", "medium", "high"]
WEIGHTS_PATH = Path(__file__).with_name("weights.yaml")


class Step(BaseModel):
    at_least: int
    points: int


class Size(BaseModel):
    lines: list[Step] = Field(default_factory=list)
    files: list[Step] = Field(default_factory=list)


class Rule(BaseModel):
    name: str
    kinds: list[FactorKind] = Field(default_factory=list)
    min_severity: int = Field(default=1, ge=1, le=3)
    min_lines: int = 0
    min_level: Literal["medium", "high"]


class Levels(BaseModel):
    medium: int
    high: int


class Weights(BaseModel):
    severity_multiplier: dict[int, float]
    factor_weights: dict[FactorKind, float]
    size: Size
    levels: Levels
    rules: list[Rule]


@lru_cache
def load_weights(path: Path = WEIGHTS_PATH) -> Weights:
    return Weights.model_validate(yaml.safe_load(path.read_text()))


class DiffStats(BaseModel):
    additions: int
    deletions: int
    changed_files: int

    @property
    def lines(self) -> int:
        return self.additions + self.deletions


class Score(BaseModel):
    score: int
    level: Level
    breakdown: dict[str, int]  # points per factor kind, plus size_lines / size_files
    rules: list[str]  # hard rules that fired


def _steps(value: int, steps: list[Step]) -> int:
    return max((s.points for s in steps if value >= s.at_least), default=0)


def _level(score: int, levels: Levels) -> Level:
    if score >= levels.high:
        return "high"
    return "medium" if score >= levels.medium else "low"


def score(assessment: RiskAssessment, diff: DiffStats, weights: Weights | None = None) -> Score:
    w = weights or load_weights()
    # One entry per kind, at its highest severity: listing a factor twice can't double its weight.
    severity: dict[str, int] = {}
    for f in assessment.factors:
        severity[f.kind] = max(severity.get(f.kind, 0), f.severity)

    breakdown = {
        kind: round(w.factor_weights[kind] * w.severity_multiplier[sev])
        for kind, sev in sorted(severity.items())
    }
    breakdown["size_lines"] = _steps(diff.lines, w.size.lines)
    breakdown["size_files"] = _steps(diff.changed_files, w.size.files)
    total = max(0, min(100, sum(breakdown.values())))

    fired: list[str] = []
    for rule in w.rules:
        kinds_ok = all(severity.get(k, 0) >= rule.min_severity for k in rule.kinds)
        if kinds_ok and diff.lines >= rule.min_lines:
            fired.append(rule.name)
            total = max(total, getattr(w.levels, rule.min_level))
    return Score(score=total, level=_level(total, w.levels), breakdown=breakdown, rules=fired)
