"""What the LLM returns (SPEC_W3 D3). It proposes factors with evidence; it never sets the score."""

from typing import Literal

from pydantic import BaseModel, Field

FactorKind = Literal[
    "large_diff",
    "hot_files",
    "risky_paths",
    "new_author",
    "failing_ci",
    "missing_tests",
    "migration",
    "config_change",
]


class RiskFactor(BaseModel):
    kind: FactorKind
    severity: int = Field(ge=1, le=3, description="1 minor, 2 notable, 3 serious")
    evidence: str = Field(min_length=1, description="Quote the tool output this is based on.")


class RiskAssessment(BaseModel):
    factors: list[RiskFactor]
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
