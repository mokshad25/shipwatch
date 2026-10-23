"""Inspect pr-events.dlq and replay dead-lettered events to pr-events.

    uv run python scripts/dlq_replay.py                                  # counts per error type
    uv run python scripts/dlq_replay.py --replay --dry-run               # what would be replayed
    uv run python scripts/dlq_replay.py --replay --error-type TimeoutError --limit 10

Reads the whole DLQ every time without committing offsets, so listing is read-only and a replay can
be repeated. Replaying an event twice is harmless: the worker skips event_ids it has processed.
"""

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from confluent_kafka import Consumer, TopicPartition
from shipwatch_common.kafka import Producer
from shipwatch_common.settings import ServiceSettings
from worker.dlq import original_value


@dataclass(frozen=True)
class DlqEntry:
    partition: int
    offset: int
    record: dict[str, Any]

    @property
    def error_type(self) -> str:
        return self.record.get("error", {}).get("type", "unknown")

    @property
    def event_id(self) -> str | None:
        value = self.record.get("original", {}).get("value")
        return value.get("event_id") if isinstance(value, dict) else None


def read_dlq(bootstrap_servers: str, topic: str, timeout: float = 10.0) -> list[DlqEntry]:
    """Every message currently in `topic`, oldest first (per partition)."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"dlq-replay-{uuid.uuid4().hex}",  # throwaway: we never commit
            "enable.auto.commit": False,
        }
    )
    entries: list[DlqEntry] = []
    try:
        metadata = consumer.list_topics(topic, timeout=timeout).topics[topic]
        if metadata.error is not None:
            raise SystemExit(f"cannot read {topic}: {metadata.error}")
        ends: dict[int, int] = {}
        assignment = []
        for p in metadata.partitions:
            low, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=timeout)
            if high > low:
                ends[p] = high
                assignment.append(TopicPartition(topic, p, low))
        consumer.assign(assignment)
        while ends:
            msg = consumer.poll(timeout)
            if msg is None:
                raise SystemExit(f"timed out reading {topic}")
            if msg.error() is not None:
                continue
            try:
                record = json.loads(msg.value())
            except ValueError:
                record = {"error": {"type": "unreadable DLQ record"}}
            entries.append(DlqEntry(msg.partition(), msg.offset(), record))
            if msg.offset() + 1 >= ends.get(msg.partition(), 0):
                ends.pop(msg.partition(), None)
    finally:
        consumer.close()
    return entries


def group_by_error(entries: Iterable[DlqEntry]) -> list[tuple[str, int]]:
    """(error type, count), most frequent first, ties by name."""
    counts = Counter(e.error_type for e in entries)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def select_for_replay(
    entries: Iterable[DlqEntry], error_type: str | None = None, limit: int | None = None
) -> list[DlqEntry]:
    """Entries matching `error_type` (all if None), oldest first, at most `limit`."""
    chosen = [e for e in entries if error_type is None or e.error_type == error_type]
    chosen.sort(key=lambda e: (e.partition, e.offset))
    return chosen if limit is None else chosen[:limit]


def replay(producer: Producer, entries: list[DlqEntry], topic: str) -> int:
    """Republish each entry's original bytes to `topic` with its original key. Returns how many
    were acknowledged."""
    for e in entries:
        key = e.record["original"].get("key")
        producer.publish(
            topic,
            key,
            original_value(e.record),
            headers={"x-replayed-from": f"dlq/{e.partition}/{e.offset}"},
        )
    results = producer.flush_results(30)
    for e, r in zip(entries, results, strict=True):
        if not r.ok:
            print(f"  failed: dlq/{e.partition}/{e.offset}: {r.error}", file=sys.stderr)
    return sum(r.ok for r in results)


def print_groups(groups: list[tuple[str, int]], topic: str) -> None:
    total = sum(n for _, n in groups)
    print(f"{topic}: {total} message(s)")
    if groups:
        width = max(len("error type"), *(len(t) for t, _ in groups))
        print(f"  {'error type':<{width}}  count")
        for error_type, n in groups:
            print(f"  {error_type:<{width}}  {n:>5}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--replay", action="store_true", help="republish to the main topic")
    parser.add_argument("--error-type", help="only messages whose error.type is exactly this")
    parser.add_argument("--limit", type=int, help="replay at most N messages (oldest first)")
    parser.add_argument("--dry-run", action="store_true", help="show what --replay would do")
    args = parser.parse_args(argv)
    if (args.error_type or args.limit is not None or args.dry_run) and not args.replay:
        parser.error("--error-type, --limit and --dry-run go with --replay")

    settings = ServiceSettings()
    entries = read_dlq(settings.kafka_bootstrap_servers, settings.pr_events_dlq_topic)
    if not args.replay:
        print_groups(group_by_error(entries), settings.pr_events_dlq_topic)
        return 0

    chosen = select_for_replay(entries, args.error_type, args.limit)
    verb = "would replay" if args.dry_run else "replaying"
    print(f"{verb} {len(chosen)} message(s) to {settings.pr_events_topic}")
    for e in chosen:
        print(f"  dlq/{e.partition}/{e.offset}  {e.error_type:<20}  event_id={e.event_id}")
    if args.dry_run or not chosen:
        return 0
    ok = replay(Producer(settings.kafka_bootstrap_servers), chosen, settings.pr_events_topic)
    print(f"replayed {ok}/{len(chosen)}")
    return 0 if ok == len(chosen) else 1


if __name__ == "__main__":
    sys.exit(main())
