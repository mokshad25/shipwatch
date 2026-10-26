"""Ship log lines to Kafka from inside the process (docs/SPEC_W4.md, "Shipping logs")."""

import logging

from shipwatch_common.kafka import RDKAFKA_LOGGER, Producer


class KafkaLogHandler(logging.Handler):
    """Produces each formatted record to `topic`, keyed by service. Never blocks or raises.

    A line that can't be queued (librdkafka's buffer is full, e.g. because the broker has been down
    for a while) is dropped and counted in `dropped`; losing a log line beats slowing a request.
    """

    def __init__(self, bootstrap_servers: str, topic: str, key: str, **config: object) -> None:
        super().__init__()
        self.topic = topic
        self.key = key
        self.dropped = 0
        self._producer = Producer(
            bootstrap_servers,
            **{
                "acks": "1",
                "enable.idempotence": False,
                "linger.ms": 50,
                "delivery.timeout.ms": 30_000,
                **config,
            },
        )

    def emit(self, record: logging.LogRecord) -> None:
        # librdkafka's own complaints about the broker must not be shipped through that broker.
        if record.name == RDKAFKA_LOGGER.name:
            return
        try:
            line = self.format(record)
            if not self._producer.publish_nowait(
                self.topic, self.key, line.encode(), on_error=self._on_delivery_error
            ):
                self.dropped += 1
            # Serve delivery callbacks so librdkafka can free their slots.
            self._producer.poll(0)
        except Exception:
            self.dropped += 1

    def _on_delivery_error(self, _: str) -> None:
        self.dropped += 1

    def close(self) -> None:
        try:
            self._producer.close(5.0)
        finally:
            super().close()
