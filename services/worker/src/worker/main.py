import asyncio
import signal

from shipwatch_common.db import make_engine, make_sessionmaker
from shipwatch_common.logging import configure_logging

from worker.consumer import Worker
from worker.settings import get_settings


async def run() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    worker = Worker(settings, make_sessionmaker(engine))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
