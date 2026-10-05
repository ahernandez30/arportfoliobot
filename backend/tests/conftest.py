"""Tests run against a separate PostgreSQL database, never the live one."""
import os
import tempfile

os.environ.setdefault(
    "ARPB_DATABASE_URL", "postgresql+psycopg:///arportfoliobot_test?host=/var/run/postgresql"
)
# A throwaway master key, so encryption is exercised for real.
_key_dir = tempfile.mkdtemp(prefix="arpb-test-key-")
_key_file = os.path.join(_key_dir, "master_key")
with open(_key_file, "wb") as fh:
    from cryptography.fernet import Fernet

    fh.write(Fernet.generate_key())
os.environ["ARPB_MASTER_KEY_FILE"] = _key_file

from pathlib import Path

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import passwords, user_settings
from app.db import get_engine, get_sessionmaker
from app.main import app
from app.models import User

BACKEND = Path(__file__).resolve().parent.parent
TABLES = "backtest_runs, strategy_trades, parity_checks, master_chart_state, strategy_presets, trading_controls, paper_events, paper_orders, paper_positions, paper_accounts, closed_trades, capital_snapshots, long_term_trades, long_term_positions, capital_flows, market_feed_status, market_watch, chart_layouts, dashboard_layouts, worker_heartbeats, audit_log, api_keys, user_settings, login_attempts, invites, sessions, users"
PASSWORD = "correct horse battery"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    assert "arportfoliobot_test" in os.environ["ARPB_DATABASE_URL"], "refusing to test against a non-test database"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine():
    return get_engine()


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


def make_user(email: str, *, role: str = "user", password: str = PASSWORD, active: bool = True) -> User:
    with get_sessionmaker()() as s:
        u = User(email=email, display_name=email.split("@")[0], password_hash=passwords.hash_password(password),
                 role=role, is_active=active)
        s.add(u)
        s.flush()
        user_settings.save(s, u.id, user_settings.SettingsModel())
        s.commit()
        return u


def client(ip: str = "203.0.113.10") -> TestClient:
    # https so the Secure session cookie is kept, like in a real browser.
    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


def signed_in(email: str, password: str = PASSWORD) -> TestClient:
    c = client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}
    return c


def enable_two_step(c: TestClient, password: str = PASSWORD) -> str:
    """Turns on two-step sign-in through the API and returns the secret."""
    r = c.post("/api/me/two-step/setup", json={"password": password})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = c.post("/api/me/two-step/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret


@pytest.fixture
def admin():
    return make_user("rafa@example.com", role="admin")


@pytest.fixture
def admin_client(admin):
    return signed_in(admin.email)
