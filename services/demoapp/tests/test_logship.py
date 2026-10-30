import json
import logging

from demoapp.logship import KafkaLogHandler
from shipwatch_common.kafka import RDKAFKA_LOGGER
from shipwatch_common.logging import json_formatter
from shipwatch_common.settings import ServiceSettings

KAFKA = ServiceSettings().kafka_bootstrap_servers


def _record(msg: str, name: str = "demoapp.main", level: int = logging.WARNING):
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_ships_formatted_lines_keyed_by_service(kafka_topic, consume_all):
    handler = KafkaLogHandler(KAFKA, kafka_topic, key="demoapp")
    handler.setFormatter(json_formatter({"service": "demoapp", "sha": "abc123"}))
    handler.handle(_record("out of stock"))
    handler.handle(_record("order not found"))
    handler.close()

    messages = consume_all(kafka_topic, 2)
    assert [m.key() for m in messages] == [b"demoapp", b"demoapp"]
    lines = [json.loads(m.value()) for m in messages]
    assert [line["event"] for line in lines] == ["out of stock", "order not found"]
    assert all(line["sha"] == "abc123" and line["level"] == "warning" for line in lines)
    assert handler.dropped == 0


def test_rdkafka_records_are_not_shipped(kafka_topic, consume_all):
    handler = KafkaLogHandler(KAFKA, kafka_topic, key="demoapp")
    handler.setFormatter(json_formatter())
    handler.handle(_record("broker down", name=RDKAFKA_LOGGER.name))
    handler.handle(_record("kept"))
    handler.close()

    (message,) = consume_all(kafka_topic, 1)
    assert json.loads(message.value())["event"] == "kept"


def test_full_queue_drops_instead_of_raising():
    # Nothing listens here and the local queue holds a single message.
    handler = KafkaLogHandler(
        "127.0.0.1:1", "logs", key="demoapp", **{"queue.buffering.max.messages": 1}
    )
    handler.setFormatter(json_formatter())
    for i in range(5):
        handler.handle(_record(f"line {i}"))
    assert handler.dropped >= 4
    handler._producer._producer.purge()  # don't wait for a broker that will never answer
    handler.close()
