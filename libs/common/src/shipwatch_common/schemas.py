"""Messages on the week-4 topics (docs/SPEC_W4.md)."""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Deploy(BaseModel):
    """What `deploys` carries: one service now runs one commit."""

    deploy_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    service: str
    repo: str
    sha: str
    pr_number: int | None = None
    deployed_at: datetime = Field(default_factory=_now)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Deploy":
        return cls.model_validate_json(data)
