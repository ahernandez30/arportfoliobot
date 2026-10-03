"""A user's preferences: their shape, defaults, allowed ranges, and how they are saved.

Stored as one JSON document per user (table user_settings). Whatever is stored is
merged over the defaults and re-checked on every read, so adding a setting later
never breaks an older saved document.
"""
import re
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.models import UserSettings

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_TIMEZONES = frozenset(available_timezones())


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Display(_Section):
    timezone: str = "America/New_York"
    theme: Literal["dark", "light"] = "dark"

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        if v not in _TIMEZONES:
            raise ValueError("unknown time zone")
        return v


class Trading(_Section):
    # Take profit and stop loss for MANUAL trades, in percent of the option price.
    # Strategy trades exit by the strategy's own rule on the stock chart (plan 7.6).
    take_profit_pct: float = Field(30.0, gt=0, le=1000)
    stop_loss_pct: float = Field(20.0, gt=0, le=100)
    contracts_per_trade: int = Field(1, ge=1, le=1000)
    # Limits for manual and automatic orders (plan section 10).
    max_order_usd: float = Field(5000.0, gt=0, le=10_000_000)
    max_daily_loss_usd: float = Field(1000.0, gt=0, le=10_000_000)
    # "paper_and_real" is added in Stage 8, once a real broker exists.
    auto_trading: Literal["off", "paper"] = "off"


class Paper(_Section):
    starting_balance: float = Field(100_000.0, ge=1000, le=100_000_000)
    # bid_ask: buys fill at the ask, sells at the bid. mid: both at the middle price.
    fill_rule: Literal["bid_ask", "mid"] = "bid_ask"


class Watchlist(_Section):
    symbols: list[str] = Field(default_factory=lambda: ["TSLA", "QQQ", "SPY"], max_length=50)
    default_ticker: str = "TSLA"

    @field_validator("symbols", mode="before")
    @classmethod
    def _clean_symbols(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for s in v:
            s = str(s).strip().upper()
            if s and s not in out:
                out.append(s)
        return out

    @field_validator("symbols")
    @classmethod
    def _valid_symbols(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if not SYMBOL_RE.match(s)]
        if bad:
            raise ValueError(f"not a valid symbol: {', '.join(bad)}")
        return v

    @field_validator("default_ticker", mode="before")
    @classmethod
    def _clean_ticker(cls, v: object) -> object:
        return str(v).strip().upper() if isinstance(v, str) else v

    @field_validator("default_ticker")
    @classmethod
    def _valid_ticker(cls, v: str) -> str:
        if not SYMBOL_RE.match(v):
            raise ValueError("not a valid symbol")
        return v


class SettingsModel(_Section):
    display: Display = Field(default_factory=Display)
    trading: Trading = Field(default_factory=Trading)
    paper: Paper = Field(default_factory=Paper)
    watchlist: Watchlist = Field(default_factory=Watchlist)


def _merge(base: dict, changes: dict) -> dict:
    out = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _known_only(data: dict, shape: dict) -> dict:
    """Drop stored settings that no longer exist."""
    out = {}
    for key, value in data.items():
        if key not in shape:
            continue
        if isinstance(value, dict) and isinstance(shape[key], dict):
            out[key] = _known_only(value, shape[key])
        else:
            out[key] = value
    return out


def _parse_stored(data: dict | None) -> SettingsModel:
    """Stored settings over the defaults. A stored value that no longer passes the
    rules falls back to its default instead of locking the user out of Config."""
    defaults = SettingsModel().model_dump()
    merged = _merge(defaults, _known_only(data or {}, defaults))
    try:
        return SettingsModel.model_validate(merged)
    except ValidationError:
        result = {}
        for name, field in SettingsModel.model_fields.items():
            try:
                result[name] = field.annotation.model_validate(merged.get(name, {}))
            except ValidationError:
                result[name] = field.annotation()
        return SettingsModel(**result)


def load(db: Session, user_id: int) -> SettingsModel:
    row = db.get(UserSettings, user_id)
    return _parse_stored(row.data if row else None)


def apply_changes(current: SettingsModel, changes: dict) -> SettingsModel:
    """Settings with `changes` merged in. Raises ValidationError if anything breaks a rule."""
    return SettingsModel.model_validate(_merge(current.model_dump(), changes))


def save(db: Session, user_id: int, settings: SettingsModel) -> None:
    row = db.get(UserSettings, user_id)
    if row is None:
        db.add(UserSettings(user_id=user_id, data=settings.model_dump()))
    else:
        row.data = settings.model_dump()
