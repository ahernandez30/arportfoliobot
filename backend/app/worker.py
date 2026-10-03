"""Background worker. Runs whether or not anyone has the site open.

Stage 0: it only reports a heartbeat. Prices, strategies and paper orders come later.
"""
import logging
import signal
import threading

from app.config import get_settings
from app.db import get_engine, record_heartbeat
from app.health import WORKER_NAME

log = logging.getLogger("arpb.worker")


def run(stop: threading.Event) -> None:
    settings = get_settings()
    engine = get_engine()
    log.info("worker started")
    while not stop.is_set():
        try:
            record_heartbeat(engine, WORKER_NAME)
        except Exception:
            log.exception("heartbeat failed")
        stop.wait(settings.worker_heartbeat_seconds)
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
