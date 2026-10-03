"""Settings read from environment variables (prefix ARPB_).

In production they come from /etc/arportfoliobot/arportfoliobot.env, which is
readable by root only and loaded by systemd. Nothing secret lives in the repo.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARPB_")

    env: str = "development"
    # Unix-socket connection with peer authentication: no password involved.
    database_url: str = "postgresql+psycopg:///arportfoliobot?host=/var/run/postgresql"
    # Seconds between worker heartbeats, and age after which the worker counts as stale.
    worker_heartbeat_seconds: int = 30
    worker_stale_seconds: int = 120

    # Master key for encrypting stored keys and two-step secrets. In production systemd
    # hands it over with LoadCredential (see crypto.py); this overrides the path.
    master_key_file: str = ""

    # Login sessions: signed out after this much inactivity, and after this long in any case.
    session_idle_hours: int = 24 * 7
    session_max_days: int = 30
    # A password accepted but two-step code not yet given: how long that half-login lasts.
    mfa_pending_minutes: int = 5

    # Login rate limits, counted over a sliding window.
    login_window_minutes: int = 15
    login_max_failures_per_email: int = 5
    login_max_failures_per_ip: int = 20

    # Public address, used to build invite links.
    public_url: str = "https://arportfoliobot.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
