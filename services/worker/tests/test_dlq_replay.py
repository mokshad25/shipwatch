import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("dlq_replay", ROOT / "scripts/dlq_replay.py")
dlq_replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dlq_replay)
DlqEntry = dlq_replay.DlqEntry


def entry(offset: int, error_type: str, partition: int = 0) -> DlqEntry:
    return DlqEntry(partition, offset, {"error": {"type": error_type}, "original": {}})


ENTRIES = [
    entry(0, "TimeoutError"),
    entry(1, "ValidationError"),
    entry(2, "TimeoutError"),
    entry(3, "InjectedFailure"),
    entry(4, "TimeoutError"),
    entry(5, "ValidationError"),
]


def test_group_by_error_counts_most_frequent_first():
    assert dlq_replay.group_by_error(ENTRIES) == [
        ("TimeoutError", 3),
        ("ValidationError", 2),
        ("InjectedFailure", 1),
    ]


def test_group_by_error_breaks_ties_by_name_and_handles_empty():
    assert dlq_replay.group_by_error([entry(0, "b"), entry(1, "a")]) == [("a", 1), ("b", 1)]
    assert dlq_replay.group_by_error([]) == []


def test_record_without_error_info_is_grouped_as_unknown():
    assert dlq_replay.group_by_error([DlqEntry(0, 0, {})]) == [("unknown", 1)]


@pytest.mark.parametrize(
    ("error_type", "limit", "offsets"),
    [
        (None, None, [0, 1, 2, 3, 4, 5]),
        ("TimeoutError", None, [0, 2, 4]),
        ("TimeoutError", 2, [0, 2]),
        (None, 1, [0]),
        ("NoSuchError", None, []),
        ("TimeoutError", 0, []),
    ],
)
def test_select_for_replay_filters_then_limits_oldest_first(error_type, limit, offsets):
    chosen = dlq_replay.select_for_replay(list(reversed(ENTRIES)), error_type, limit)

    assert [e.offset for e in chosen] == offsets


def test_flags_other_than_replay_need_replay():
    with pytest.raises(SystemExit):
        dlq_replay.main(["--dry-run"])


async def test_replay_end_to_end(
    produce,
    run_worker,
    settings,
    make_envelope,
    consume_all,
    wait_for_processed,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PR_EVENTS_TOPIC", settings.pr_events_topic)
    monkeypatch.setenv("PR_EVENTS_DLQ_TOPIC", settings.pr_events_dlq_topic)
    settings.fail_rate = 1.0
    produce(make_envelope(1), make_envelope(2))
    produce(b"{not json")
    worker, task = run_worker()
    await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 3)
    worker.stop()
    await asyncio.wait_for(task, 10)

    assert await asyncio.to_thread(dlq_replay.main, []) == 0
    out = capsys.readouterr().out
    assert re.search(r"InjectedFailure\s+2\n", out)
    assert re.search(r"ValidationError\s+1\n", out)

    args = ["--replay", "--error-type", "InjectedFailure", "--dry-run"]
    assert await asyncio.to_thread(dlq_replay.main, args) == 0
    assert "would replay 2 message(s)" in capsys.readouterr().out

    args = ["--replay", "--error-type", "InjectedFailure", "--limit", "1"]
    assert await asyncio.to_thread(dlq_replay.main, args) == 0
    assert "replayed 1/1" in capsys.readouterr().out

    # The replayed event is processed once the failure is gone.
    settings.fail_rate = 0.0
    worker, task = run_worker()
    await wait_for_processed(1)
    worker.stop()
    await asyncio.wait_for(task, 10)
    replayed = consume_all(settings.pr_events_topic, 4)[-1]
    assert dict(replayed.headers())["x-replayed-from"].startswith(b"dlq/0/")
    assert json.loads(replayed.value())["delivery_id"] == "d-1"
