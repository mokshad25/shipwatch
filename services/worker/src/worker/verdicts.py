"""PR events -> agent -> verdict in Postgres, trace in MongoDB, summary on pr-verdicts (SPEC_W3 D7).

One verdict per (pr_id, head_sha):
1. If a final verdict exists, republish it and skip the agent (no second LLM bill).
2. Otherwise run the agent, upsert the trace (keyed by the same pair, so a retry overwrites it),
   then store the verdict and the processed_events row in one transaction.
3. Publish the summary and wait for the broker's ack. If the worker dies between the commit and
   the publish, the redelivery takes path 1 and publishes it then: at-least-once, and consumers
   dedupe on (pr_id, head_sha).

A needs_review verdict isn't final: a later delivery for the same commit (a DLQ replay, say)
re-runs the agent and replaces it.
"""

import asyncio
import json
from typing import Any

from agent.github import GitHubClient
from agent.llm import LLMConfigError, make_chat_model
from agent.service import AgentRun, Verdict, assess_pr
from agent.trace import Trace
from langchain_core.language_models import BaseChatModel
from pymongo import AsyncMongoClient
from redis.asyncio import Redis
from shipwatch_common.kafka import EventEnvelope, Producer
from shipwatch_common.logging import get_logger
from shipwatch_common.models import VerdictRecord
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from worker.processing import Source, record_processed
from worker.settings import WorkerSettings

log = get_logger(__name__)

ASSESSED_EVENTS = frozenset({"pull_request.opened", "pull_request.synchronize"})


class VerdictPublishError(ConnectionError):
    """The pr-verdicts publish wasn't acked. Retryable: the next attempt republishes."""


def summary_message(record: VerdictRecord, repo: str, pr_number: int) -> dict[str, Any]:
    factors = (record.assessment or {}).get("factors", [])
    top = sorted(factors, key=lambda f: -f["severity"])[:3]
    return {
        "verdict_id": record.id,
        "pr_id": record.pr_id,
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": record.head_sha,
        "level": record.level,
        "score": record.score,
        "reason": record.reason,
        "summary": (record.assessment or {}).get("summary"),
        "top_factors": [{"kind": f["kind"], "severity": f["severity"]} for f in top],
        "trace_id": record.trace_id,
        "created_at": record.created_at.isoformat(),
    }


class PrVerdicts:
    def __init__(
        self,
        settings: WorkerSettings,
        gh: GitHubClient,
        llm: BaseChatModel | None,
        mongo: AsyncMongoClient,
        producer: Producer,
    ) -> None:
        self.settings = settings
        self.gh = gh
        self.llm = llm  # None: LLM not configured, every PR gets needs_review
        self.mongo = mongo
        self.traces = mongo[settings.mongo_db]["traces"]
        self.producer = producer

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> "PrVerdicts":
        try:
            llm = make_chat_model(settings)
        except LLMConfigError as exc:
            log.error("PR risk agent disabled", error=str(exc))
            llm = None
        redis = Redis.from_url(settings.redis_url)
        return cls(
            settings,
            GitHubClient.from_settings(settings, redis),
            llm,
            AsyncMongoClient(settings.mongo_url, tz_aware=True),
            Producer(settings.kafka_bootstrap_servers),
        )

    async def aclose(self) -> None:
        await self.gh.aclose()
        await self.gh.cache.redis.aclose()
        await self.mongo.close()

    async def process(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        envelope: EventEnvelope,
        source: Source,
    ) -> None:
        if envelope.type not in ASSESSED_EVENTS:
            async with sessionmaker.begin() as session:
                await record_processed(session, envelope, source)
            return

        payload = envelope.payload
        pr, repo = payload["pull_request"], payload["repository"]["full_name"]
        pr_id, number, head_sha = pr["id"], pr["number"], pr["head"]["sha"]

        async with sessionmaker() as session:
            existing = await session.scalar(
                select(VerdictRecord).where(
                    VerdictRecord.pr_id == pr_id, VerdictRecord.head_sha == head_sha
                )
            )
        if existing is not None and existing.level != "needs_review":
            log.info("verdict exists, republishing", pr_id=pr_id, head_sha=head_sha)
            async with sessionmaker.begin() as session:
                await record_processed(session, envelope, source)
            await self.publish(existing, repo, number)
            return

        run = await self.assess(repo, number, source)
        trace_id = f"{pr_id}:{head_sha}"
        await self.traces.replace_one(
            {"_id": trace_id},
            {
                "_id": trace_id,
                "pr_id": pr_id,
                "head_sha": head_sha,
                "event_id": str(envelope.event_id),
                **run.model_dump(mode="json"),
            },
            upsert=True,
        )
        record = await self.store(sessionmaker, envelope, source, run, pr_id, head_sha, trace_id)
        await self.publish(record, repo, number)

    async def assess(self, repo: str, number: int, source: Source) -> AgentRun:
        if self.llm is None:
            verdict = Verdict(
                repo=repo,
                pr_number=number,
                head_sha=None,
                level="needs_review",
                score=None,
                reason="LLM not configured (set LLM_PROVIDER and LLM_MODEL)",
            )
            trace = Trace(repo=repo, pr_number=number, provider="none", model="none")
            return AgentRun(verdict=verdict, trace=trace)
        # Raises AgentRateLimited (retryable) only while the worker has attempts left.
        return await assess_pr(
            self.llm,
            self.gh,
            repo,
            number,
            self.settings,
            can_retry=source.can_retry,
            attempt=source.attempt,
        )

    async def store(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        envelope: EventEnvelope,
        source: Source,
        run: AgentRun,
        pr_id: int,
        head_sha: str,
        trace_id: str,
    ) -> VerdictRecord:
        v, t = run.verdict, run.trace
        values = {
            "pr_id": pr_id,
            "head_sha": head_sha,
            "level": v.level,
            "score": v.score,
            "reason": v.reason,
            "assessment": (
                {**v.assessment.model_dump(), "breakdown": v.breakdown, "rules": v.rules}
                if v.assessment
                else None
            ),
            "tokens": t.input_tokens + t.output_tokens,
            "cost_usd": t.cost_usd,
            "trace_id": trace_id,
        }
        stmt = insert(VerdictRecord).values(**values)
        # Concurrent duplicates keep the first final verdict; only needs_review gets replaced.
        stmt = stmt.on_conflict_do_update(
            index_elements=[VerdictRecord.pr_id, VerdictRecord.head_sha],
            set_={k: stmt.excluded[k] for k in values if k not in ("pr_id", "head_sha")},
            where=VerdictRecord.level == "needs_review",
        )
        async with sessionmaker.begin() as session:
            await session.execute(stmt)
            await record_processed(session, envelope, source)
        async with sessionmaker() as session:
            return await session.scalar(
                select(VerdictRecord).where(
                    VerdictRecord.pr_id == pr_id, VerdictRecord.head_sha == head_sha
                )
            )

    async def publish(self, record: VerdictRecord, repo: str, number: int) -> None:
        message = summary_message(record, repo, number)
        self.producer.publish(self.settings.pr_verdicts_topic, repo, json.dumps(message).encode())
        [result] = await asyncio.to_thread(self.producer.flush_results, 10.0)
        if not result.ok:
            raise VerdictPublishError(result.error)
        log.info("verdict published", verdict_level=record.level, score=record.score)
