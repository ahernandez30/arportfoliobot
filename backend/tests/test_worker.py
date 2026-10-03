import threading

from app import worker
from app.db import last_heartbeat


def test_worker_writes_heartbeat_and_stops(engine):
    stop = threading.Event()
    t = threading.Thread(target=worker.run, args=(stop,))
    t.start()
    try:
        for _ in range(50):
            if last_heartbeat(engine, "worker") is not None:
                break
            stop.wait(0.1)
    finally:
        stop.set()
        t.join(timeout=5)
    assert not t.is_alive()
    assert last_heartbeat(engine, "worker") is not None
