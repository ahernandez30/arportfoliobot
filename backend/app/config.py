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


@lru_cache
def get_settings() -> Settings:
    return Settings()
