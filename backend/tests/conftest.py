"""Tests run against a separate PostgreSQL database, never the live one."""
import os

os.environ.setdefault(
    "ARPB_DATABASE_URL", "postgresql+psycopg:///arportfoliobot_test?host=/var/run/postgresql"
)

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import get_engine

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    assert "arportfoliobot_test" in os.environ["ARPB_DATABASE_URL"], "refusing to test against a non-test database"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def engine():
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE worker_heartbeats"))
    return eng
