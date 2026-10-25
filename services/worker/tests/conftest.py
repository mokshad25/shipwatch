import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import redis
import respx
from agent.github import GitHubClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field, SecretStr
from redis.asyncio import Redis
from shipwatch_common.db import make_sessionmaker
from shipwatch_common.kafka import EventEnvelope, Producer
from shipwatch_common.models import Base, ProcessedEvent
from sqlalchemy import func, select, text
from worker.consumer import Worker
from worker.settings import WorkerSettings


@pytest.fixture
async def db(db_engine):
    """Session factory on the test schema; tables emptied after each test."""
    yield make_sessionmaker(db_engine)
    async with db_engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def settings(make_topic) -> WorkerSettings:
    """Worker settings on throwaway topics, a fresh consumer group and Redis key prefix."""
    settings = WorkerSettings(
        _env_file=None,
        pr_events_topic=make_topic(3),
        pr_events_dlq_topic=make_topic(1),
        consumer_group=f"test-worker-{uuid.uuid4().hex[:8]}",
        poll_timeout=0.2,
        backoff_base=0.01,
        backoff_max=0.05,
        redis_key_prefix=f"test-{uuid.uuid4().hex[:8]}:",
        processing_lease_seconds=2,
    )
    yield settings
    client = redis.Redis.from_url(settings.redis_url)
    keys = list(client.scan_iter(f"{settings.redis_key_prefix}*"))
    if keys:
        client.delete(*keys)
    client.close()


def _make_envelope(i: int = 0, *, repo: str = "octo/sandbox") -> EventEnvelope:
    return EventEnvelope(
        delivery_id=f"d-{i}",
        type="pull_request.opened",
        payload={"action": "opened", "number": i, "repository": {"full_name": repo}},
    )


@pytest.fixture
def make_envelope():
    return _make_envelope


@pytest.fixture
def produce(settings) -> Callable[..., list[EventEnvelope]]:
    """Publish envelopes (or raw bytes) to the test pr-events topic and wait for the acks."""

    def _produce(*items: EventEnvelope | bytes, key: str = "octo/sandbox") -> list:
        producer = Producer(settings.kafka_bootstrap_servers)
        for item in items:
            value = item.to_bytes() if isinstance(item, EventEnvelope) else item
            producer.publish(settings.pr_events_topic, key, value)
        assert all(r.ok for r in producer.flush_results(10))
        return list(items)

    return _produce


async def _wait_until(check: Callable[[], Awaitable[bool]], seconds: float = 20.0) -> None:
    try:
        async with asyncio.timeout(seconds):
            while not await check():  # noqa: ASYNC110 (polling the database)
                await asyncio.sleep(0.1)
    except TimeoutError:
        raise AssertionError(f"condition not met within {seconds}s") from None


@pytest.fixture
def processed_count(db) -> Callable[[], Awaitable[int]]:
    async def _count() -> int:
        async with db() as session:
            return await session.scalar(select(func.count()).select_from(ProcessedEvent))

    return _count


@pytest.fixture
def wait_for_processed(processed_count) -> Callable[[int], Awaitable[None]]:
    """`await wait_for_processed(n)`: until processed_events holds n rows (or fail)."""

    async def _wait(n: int, seconds: float = 20.0) -> None:
        async def check() -> bool:
            return await processed_count() == n

        await _wait_until(check, seconds)

    return _wait


@pytest.fixture
def run_worker(settings, db):
    """Start a Worker task; the fixture stops every started worker at the end of the test."""
    started: list[tuple[Worker, asyncio.Task]] = []

    def _run(**kwargs) -> tuple[Worker, asyncio.Task]:
        worker = Worker(settings, db, **kwargs)
        task = asyncio.create_task(worker.run())
        started.append((worker, task))
        return worker, task

    yield _run
    for worker, _ in started:
        worker.stop()
    for _, task in started:
        if not task.done():
            # Reaped by the event loop; errors surface in the test that awaited it.
            task.cancel()


GITHUB_FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _load_github(name: str) -> Any:
    return json.loads((GITHUB_FIXTURES / name).read_text())


@pytest.fixture
def load_github() -> Callable[[str], Any]:
    """`load_github("pr_3174.json")`: a recorded (trimmed) GitHub response body."""
    return _load_github


@pytest.fixture
def gh_api():
    """respx router for api.github.com. Any request without a matching route fails the test."""
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        yield router


@pytest.fixture
async def github(gh_api) -> GitHubClient:
    """GitHubClient on the compose Redis with a throwaway key prefix; HTTP goes to `gh_api`."""
    settings = WorkerSettings(
        _env_file=None,
        github_token=SecretStr("test-token"),
        redis_key_prefix=f"test-{uuid.uuid4().hex[:8]}:",
    )
    client = Redis.from_url(settings.redis_url)
    gh = GitHubClient.from_settings(settings, client)
    yield gh
    await gh.aclose()
    keys = [key async for key in client.scan_iter(f"{settings.redis_key_prefix}*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


class FakeChatModel(BaseChatModel):
    """Scripted chat model: each call (tool turn or structured output) takes the next item.

    Items are AIMessages, exceptions to raise, or async callables (e.g. to sleep) returning one.
    Structured output parses the message's content as JSON, like the real json_schema mode.
    """

    script: list[Any] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError("the agent is async")

    async def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("FakeChatModel script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = await item()
        return item

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=await self._next(messages))])

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        async def run(value):
            raw = await self._next(value if isinstance(value, list) else value.to_messages())
            try:
                parsed, error = schema.model_validate_json(raw.content), None
            except ValueError as exc:  # pydantic's ValidationError is a ValueError
                parsed, error = None, exc
            if not include_raw:
                if error:
                    raise error
                return parsed
            return {"raw": raw, "parsed": parsed, "parsing_error": error}

        return RunnableLambda(run)


def _usage(input_tokens: int = 100, output_tokens: int = 20) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def ai_tools(*calls: tuple[str, dict[str, Any]], **usage: int) -> AIMessage:
    """An AIMessage asking for tool calls: `ai_tools(("get_diff", {}), ("ci_status", {}))`."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}
            for name, args in calls
        ],
        usage_metadata=_usage(**usage),
    )


def ai_text(text: str, **usage: int) -> AIMessage:
    """A plain reply (no tool calls), or the JSON body of a structured-output reply."""
    if not isinstance(text, str):
        text = json.dumps(text)
    return AIMessage(content=text, usage_metadata=_usage(**usage))


@pytest.fixture
def fake_llm() -> Callable[..., FakeChatModel]:
    """`fake_llm(item, item, ...)`: a FakeChatModel that answers with the given script."""
    return lambda *script: FakeChatModel(script=list(script))


@pytest.fixture
def llm_reply():
    """Builders for scripted replies: `llm_reply.tools(...)`, `llm_reply.text(...)`."""

    class Replies:
        tools = staticmethod(ai_tools)
        text = staticmethod(ai_text)

    return Replies
