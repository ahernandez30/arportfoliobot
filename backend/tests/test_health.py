from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import health
from app.db import last_heartbeat, record_heartbeat
from app.main import app

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


def test_worker_status_never():
    assert health.worker_status(None, NOW, 120) == "never"


def test_worker_status_fresh_and_boundary():
    assert health.worker_status(NOW - timedelta(seconds=5), NOW, 120) == "ok"
    assert health.worker_status(NOW - timedelta(seconds=120), NOW, 120) == "ok"


def test_worker_status_stale():
    assert health.worker_status(NOW - timedelta(seconds=121), NOW, 120) == "stale"


def test_heartbeat_upserts(engine):
    record_heartbeat(engine, "worker", NOW - timedelta(minutes=10))
    record_heartbeat(engine, "worker", NOW)
    assert last_heartbeat(engine, "worker") == NOW


def test_check_degraded_without_worker(engine):
    assert health.check(engine, 120, NOW) == {"status": "degraded", "database": "ok", "worker": "never"}


def test_check_ok_with_recent_worker(engine):
    record_heartbeat(engine, "worker", NOW - timedelta(seconds=10))
    assert health.check(engine, 120, NOW)["status"] == "ok"


def test_api_health_endpoint(engine):
    record_heartbeat(engine, "worker")
    resp = TestClient(app).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok", "worker": "ok"}


def test_api_docs_are_not_exposed():
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
