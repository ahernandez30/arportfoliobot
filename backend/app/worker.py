"""Background worker. Runs whether or not anyone has the site open.

Stage 2: heartbeat, plus live price feeds for symbols users have on screen (app.feed).
Strategies and paper orders come in later stages.
"""
import asyncio
import logging
import signal
import threading

from app.config import get_settings
from app.db import get_engine, record_heartbeat
from app.feed import FeedManager
from app.health import WORKER_NAME

log = logging.getLogger("arpb.worker")


async def heartbeat(stop: asyncio.Event) -> None:
    settings = get_settings()
    engine = get_engine()
    while not stop.is_set():
        try:
            await asyncio.to_thread(record_heartbeat, engine, WORKER_NAME)
        except Exception:
            log.exception("heartbeat failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_heartbeat_seconds)
        except asyncio.TimeoutError:
            pass


async def run_async(stop: asyncio.Event, *, feeds: bool = True) -> None:
    log.info("worker started")
    tasks = [asyncio.create_task(heartbeat(stop))]
    if feeds:
        tasks.append(asyncio.create_task(FeedManager(get_engine()).run(stop)))
    await asyncio.gather(*tasks)
    log.info("worker stopped")


def run(stop: threading.Event, *, feeds: bool = True) -> None:
    """Runs until `stop` is set (from a signal handler or another thread)."""

    async def main() -> None:
        astop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def watch() -> None:
            stop.wait()
            loop.call_soon_threadsafe(astop.set)

        threading.Thread(target=watch, daemon=True).start()
        await run_async(astop, feeds=feeds)

    asyncio.run(main())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
