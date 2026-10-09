"""The common shape every strategy has (plan section 7.8), so a new one plugs into Master Chart,
the worker and Backtest without changing those screens.

A strategy is: a name, input definitions (with defaults and allowed ranges), and one function
from candles plus inputs to signals and trades.
"""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from app.marketdata.base import Bar

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400, "1W": 604800}


class InputError(ValueError):
    """An input outside its allowed range, with a message fit for the screen."""


@dataclass(frozen=True)
class InputDef:
    key: str
    label: str
    kind: str  # "float", "int", "bool", "choice", "timeframe", "time"
    default: Any
    group: str
    min: float | None = None
    max: float | None = None
    options: tuple = ()
    help: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["options"] = list(self.options)
        return d

    def check(self, value: Any) -> Any:
        """The value in its proper type, or InputError."""
        name = self.label
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise InputError(f"{name}: must be on or off.")
            return value
        if self.kind in ("float", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InputError(f"{name}: enter a number.")
            if self.kind == "int":
                if float(value) != int(value):
                    raise InputError(f"{name}: enter a whole number.")
                value = int(value)
            else:
                value = float(value)
            if self.min is not None and value < self.min:
                raise InputError(f"{name}: must be at least {self.min:g}.")
            if self.max is not None and value > self.max:
                raise InputError(f"{name}: must be at most {self.max:g}.")
            return value
        if self.kind == "choice":
            if value not in self.options:
                raise InputError(f"{name}: choose one of {', '.join(self.options)}.")
            return value
        if self.kind == "timeframe":
            if value not in TIMEFRAME_SECONDS:
                raise InputError(f"{name}: not a timeframe.")
            return value
        if self.kind == "time":
            if value == "":
                return ""
            if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                raise InputError(f"{name}: use HH:MM, or leave empty.")
            try:
                h, m = int(value[:2]), int(value[3:])
            except ValueError:
                raise InputError(f"{name}: use HH:MM, or leave empty.") from None
            if not (0 <= h < 24 and 0 <= m < 60):
                raise InputError(f"{name}: use HH:MM, or leave empty.")
            return value
        raise InputError(f"{name}: unknown kind of input.")


def check_inputs(defs: list[InputDef], raw: dict | None) -> dict:
    """All inputs in their proper types: given values checked, missing ones at their defaults.
    Unknown keys are dropped, so settings saved for an older version still load."""
    raw = raw or {}
    return {d.key: d.check(raw[d.key]) if d.key in raw else d.default for d in defs}


@dataclass
class StrategyData:
    """Everything a strategy may read. `bars` are oldest first; bars from index `closed`
    on are still in progress and may only be previewed, never traded (plan section 7.5)."""

    symbol: str
    timeframe: str
    bars: list[Bar]
    closed: int
    # Higher-timeframe candles by timeframe, and lower-timeframe candles for the path inside a candle.
    other: dict[str, list[Bar]] = field(default_factory=dict)


class Strategy(ABC):
    id: str
    name: str
    version: str
    inputs: list[InputDef]

    def defaults(self) -> dict:
        return {d.key: d.default for d in self.inputs}

    @abstractmethod
    def needs(self, timeframe: str, inputs: dict) -> set[str]:
        """Other timeframes this run needs candles for."""

    @abstractmethod
    def run(self, data: StrategyData, inputs: dict, *, luck: bool = False, luck_from: int | None = None) -> dict:
        """Signals, trades and results for the candles. `luck_from` (Unix seconds) limits the luck
        test's shadow trades to candles from then on (Backtest date ranges)."""
