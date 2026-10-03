"""Saved screen layouts: the dashboard tiles and the four charts. Both are per user."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.marketdata.base import TIMEFRAMES
from app.models import ChartLayout, DashboardLayout
from app.user_settings import SYMBOL_RE

Timeframe = Literal["1m", "5m", "15m", "1h", "1D", "1W"]
assert set(Timeframe.__args__) == set(TIMEFRAMES)

GRID_COLS = 12
TILE_KINDS = ("totals", "watchlist", "chart", "trades")


def _symbol(v: object) -> str:
    s = str(v).strip().upper()
    if not SYMBOL_RE.match(s):
        raise ValueError("not a valid symbol")
    return s


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartPane(_Strict):
    symbol: str
    timeframe: Timeframe = "1D"

    @field_validator("symbol", mode="before")
    @classmethod
    def _sym(cls, v: object) -> str:
        return _symbol(v)


class ChartsLayout(_Strict):
    panes: list[ChartPane] = Field(min_length=4, max_length=4)


class Tile(_Strict):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["totals", "watchlist", "chart", "trades"]
    x: int = Field(ge=0, lt=GRID_COLS)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=2, le=GRID_COLS)
    h: int = Field(ge=2, le=40)
    # Only for chart tiles.
    symbol: str | None = None
    timeframe: Timeframe | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def _sym(cls, v: object) -> object:
        return None if v in (None, "") else _symbol(v)

    @model_validator(mode="after")
    def _fit(self) -> "Tile":
        if self.x + self.w > GRID_COLS:
            raise ValueError("tile does not fit the grid")
        if self.kind == "chart" and (self.symbol is None or self.timeframe is None):
            raise ValueError("a chart tile needs a symbol and timeframe")
        return self


class DashboardModel(_Strict):
    tiles: list[Tile] = Field(max_length=20)

    @model_validator(mode="after")
    def _unique_ids(self) -> "DashboardModel":
        ids = [t.id for t in self.tiles]
        if len(ids) != len(set(ids)):
            raise ValueError("tile ids must be unique")
        return self


def default_charts(ticker: str) -> ChartsLayout:
    others = [s for s in ("TSLA", "QQQ", "SPY") if s != ticker]
    syms = [ticker, *others][:3]
    return ChartsLayout(panes=[
        ChartPane(symbol=syms[0], timeframe="1D"),
        ChartPane(symbol=syms[1] if len(syms) > 1 else ticker, timeframe="1D"),
        ChartPane(symbol=syms[2] if len(syms) > 2 else ticker, timeframe="1D"),
        ChartPane(symbol=ticker, timeframe="1h"),
    ])


def default_dashboard(ticker: str) -> DashboardModel:
    """The starting tiles from plan section 6: totals, watchlist, one chart, open trades."""
    return DashboardModel(tiles=[
        Tile(id="totals", kind="totals", x=0, y=0, w=12, h=3),
        Tile(id="watchlist", kind="watchlist", x=0, y=3, w=5, h=10),
        Tile(id="chart", kind="chart", x=5, y=3, w=7, h=10, symbol=ticker, timeframe="1D"),
        Tile(id="trades", kind="trades", x=0, y=13, w=12, h=5),
    ])


def load_charts(db: Session, user_id: int, ticker: str) -> ChartsLayout:
    row = db.get(ChartLayout, user_id)
    try:
        return ChartsLayout.model_validate(row.data) if row and row.data else default_charts(ticker)
    except ValidationError:
        return default_charts(ticker)


def save_charts(db: Session, user_id: int, layout: ChartsLayout) -> None:
    row = db.get(ChartLayout, user_id)
    if row is None:
        db.add(ChartLayout(user_id=user_id, data=layout.model_dump()))
    else:
        row.data = layout.model_dump()


def load_dashboard(db: Session, user_id: int, ticker: str) -> DashboardModel:
    row = db.get(DashboardLayout, user_id)
    try:
        return DashboardModel.model_validate(row.data) if row and row.data else default_dashboard(ticker)
    except ValidationError:
        return default_dashboard(ticker)


def save_dashboard(db: Session, user_id: int, layout: DashboardModel) -> None:
    row = db.get(DashboardLayout, user_id)
    if row is None:
        db.add(DashboardLayout(user_id=user_id, data=layout.model_dump()))
    else:
        row.data = layout.model_dump()
