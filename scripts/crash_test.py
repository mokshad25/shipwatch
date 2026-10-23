"""Crash test: produce events, SIGKILL the worker mid-stream, restart it, count what happened.

    make up && make migrate
    uv run python scripts/crash_test.py                    # 1,000 events, kill after 2-5 s
    uv run python scripts/crash_test.py --fail-rate 0.3    # also exercise retries and the DLQ

Prints a table and appends it to docs/results/crash_test.md. Needs the compose stack running and
talks to it from the host (Postgres :5432, Redpanda :19092, `docker compose` for the worker).
"""

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition
from shipwatch_common.db import make_engine
from shipwatch_common.kafka import EventEnvelope, Producer
from shipwatch_common.settings import ServiceSettings
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs/results/crash_test.md"


@dataclass
class Result:
    produced: int = 0
    processed: int = 0  # distinct event_ids with a processed_events row
    duplicates: int = 0  # event_ids whose processing reached the database more than once
    redelivered_skipped: int = 0  # redeliveries stopped by the Redis `done` key
    lost: int = 0  # neither processed nor dead-lettered
    in_dlq: int = 0


def compose(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=True,
        env={**os.environ, **(env or {})},
        capture_output=True,
    )


def produce(settings: ServiceSettings, run_id: str, n: int, rate: float, ids: list[str]) -> None:
    """Publish `n` events at about `rate` per second (steady traffic, so the kill lands mid-stream),
    appending each event_id to `ids`."""
    producer = Producer(settings.kafka_bootstrap_servers)
    start = time.monotonic()
    for i in range(n):
        time.sleep(max(0.0, start + i / rate - time.monotonic()))
        envelope = EventEnvelope(
            delivery_id=f"crash-{run_id}-{i}",
            type="crash_test.event",
            payload={"run_id": run_id, "seq": i},
        )
        # Spread over several repos (keys) so all three partitions are busy.
        producer.publish_envelope(settings.pr_events_topic, f"crash/repo-{i % 12}", envelope)
        ids.append(str(envelope.event_id))
        producer.poll(0)
    results = producer.flush_results(60)
    failed = [r for r in results if not r.ok]
    if failed:
        raise SystemExit(f"{len(failed)} produce failures, e.g. {failed[0].error}")


async def db_counts(database_url: str, ids: list[str]) -> tuple[int, int]:
    engine = make_engine(database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT count(*), count(*) FILTER (WHERE deliveries > 1) "
                        "FROM processed_events WHERE event_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": ids},
                )
            ).one()
    finally:
        await engine.dispose()
    return row[0], row[1]


def dlq_event_ids(settings: ServiceSettings, run_id: str) -> set[str]:
    topic = settings.pr_events_dlq_topic
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"crash-test-{uuid.uuid4().hex}",
            "enable.auto.commit": False,
        }
    )
    found: set[str] = set()
    try:
        partitions = consumer.list_topics(topic, timeout=10).topics[topic].partitions
        ends, assignment = {}, []
        for p in partitions:
            low, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            if high > low:
                ends[p] = high
                assignment.append(TopicPartition(topic, p, low))
        consumer.assign(assignment)
        while ends:
            msg = consumer.poll(10)
            if msg is None:
                break
            if msg.error() is not None:
                continue
            value = json.loads(msg.value())["original"]["value"]
            if isinstance(value, dict) and value.get("payload", {}).get("run_id") == run_id:
                found.add(value["event_id"])
            if msg.offset() + 1 >= ends.get(msg.partition(), 0):
                ends.pop(msg.partition(), None)
    finally:
        consumer.close()
    return found


def redis_skips(since: str, ids: set[str]) -> int:
    """Count "already processed, skipping" log lines for our events in the worker's logs."""
    out = subprocess.run(
        ["docker", "compose", "logs", "--no-log-prefix", "--since", since, "worker"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    skips: Counter[str] = Counter()
    for line in out.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if (
            record.get("event") == "event already processed, skipping"
            and record.get("event_id") in ids
        ):
            skips[record["event_id"]] += 1
    return sum(skips.values())


def run(args: argparse.Namespace) -> Result:
    settings = ServiceSettings()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC).isoformat()
    compose("up", "-d", "--wait", "worker", env={"FAIL_RATE": str(args.fail_rate)})

    print(f"run {run_id}: producing {args.events} events at {args.rate:.0f}/s")
    t0 = time.monotonic()
    ids: list[str] = []
    producer = threading.Thread(
        target=produce, args=(settings, run_id, args.events, args.rate, ids), daemon=True
    )
    producer.start()
    kill_after = random.uniform(args.kill_after_min, args.kill_after_max)
    time.sleep(kill_after)
    processed_at_kill, _ = asyncio.run(db_counts(settings.database_url, list(ids)))
    print(f"  {kill_after:.1f}s: docker kill worker ({processed_at_kill} processed so far)")
    compose("kill", "worker")  # SIGKILL: no graceful shutdown, no final commit
    time.sleep(args.down_seconds)
    print(f"  restarting worker after {args.down_seconds:.0f}s down")
    compose("start", "worker")
    producer.join()

    result = Result(produced=len(ids))
    deadline = time.monotonic() + args.timeout
    while True:
        result.processed, result.duplicates = asyncio.run(db_counts(settings.database_url, ids))
        dead = dlq_event_ids(settings, run_id) if args.fail_rate else set()
        result.in_dlq = len(dead)
        if result.processed + result.in_dlq >= result.produced or time.monotonic() > deadline:
            break
        time.sleep(1)
    result.lost = result.produced - result.processed - result.in_dlq
    result.redelivered_skipped = redis_skips(started, set(ids))
    elapsed = time.monotonic() - t0
    print(f"  done in {elapsed:.0f}s")
    record(result, run_id, kill_after, processed_at_kill, args.fail_rate, elapsed)
    return result


def record(
    result: Result, run_id: str, kill_after: float, at_kill: int, fail_rate: float, elapsed: float
) -> None:
    names = [f.name for f in fields(Result)]
    print()
    print("| " + " | ".join(names) + " |")
    print("|" + "---|" * len(names))
    print("| " + " | ".join(str(getattr(result, n)) for n in names) + " |")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS.exists():
        RESULTS.write_text(
            "# Crash test results\n\n"
            "Generated by `scripts/crash_test.py`: produce events, `docker kill` the worker\n"
            "after a random 2–5 s, restart it, count. Column meanings: docs/SPEC_W2.md,\n"
            "Delivery guarantees.\n\n"
            "| when (UTC) | run | fail rate | killed at | processed at kill | "
            + " | ".join(names)
            + " | took |\n|"
            + "---|" * (len(names) + 6)
            + "\n"
        )
    with RESULTS.open("a") as f:
        when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        values = " | ".join(str(getattr(result, n)) for n in names)
        f.write(
            f"| {when} | {run_id} | {fail_rate} | {kill_after:.1f}s | {at_kill} | {values} "
            f"| {elapsed:.0f}s |\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--rate", type=float, default=100.0, help="events per second")
    parser.add_argument("--kill-after-min", type=float, default=2.0)
    parser.add_argument("--kill-after-max", type=float, default=5.0)
    parser.add_argument("--down-seconds", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=180.0, help="max wait for the backlog")
    parser.add_argument("--fail-rate", type=float, default=0.0, help="FAIL_RATE for the worker")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    finally:
        # Leave the worker running with the default (no injected failures).
        if args.fail_rate:
            compose("up", "-d", "worker", env={"FAIL_RATE": "0"})
    return 0 if result.lost == 0 and result.duplicates == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
