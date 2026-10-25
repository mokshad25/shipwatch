from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from shipwatch_common.settings import ServiceSettings


class WorkerSettings(ServiceSettings):
    consumer_group: str = "worker"
    poll_timeout: float = 1.0
    # Static group membership: a restarted worker with the same id gets its partitions straight
    # back instead of waiting for the dead member's session to time out. Must be unique per
    # running replica. None = dynamic membership.
    consumer_instance_id: str | None = None
    session_timeout_ms: int = 10_000
    max_attempts: int = 3
    backoff_base: float = 0.5  # seconds before the 2nd attempt (upper bound, jittered)
    backoff_max: float = 5.0
    retry_after_max: float = 30.0  # cap on honouring a rate limit's retry-after between attempts
    # Probability (0..1) that an attempt fails with an injected, retryable error. Testing only.
    fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # How long a "processing" key blocks redelivered copies of an event. Must exceed the slowest
    # normal processing time; after a crash, that event's partition waits up to this long.
    processing_lease_seconds: int = 20
    done_ttl_seconds: int = 7 * 24 * 3600
    # Agent tools (docs/SPEC_W3.md). Without a token GitHub allows 60 requests per hour.
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_timeout_seconds: float = 10.0
    tool_cache_ttl_seconds: int = 600
    # PR risk agent (docs/SPEC_W3.md). Prices are per million tokens, used only for reporting cost.
    llm_provider: Literal["openai", "anthropic", "groq"] = "openai"
    llm_model: str = ""
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    llm_input_cost_per_mtok: float = 0.0
    llm_output_cost_per_mtok: float = 0.0
    llm_timeout_seconds: float = 30.0  # one LLM request
    # SDK-level retries. 0 in the worker: retries go through its own attempt budget (D6). Batch
    # scripts raise it so the SDK waits out provider rate limits (it honours retry-after).
    llm_max_retries: int = 0
    agent_timeout_seconds: float = 60.0  # the whole run: every LLM and tool call
    agent_max_tool_calls: int = 6
    llm_token_budget: int = 50_000  # input + output tokens per PR
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "shipwatch"


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
