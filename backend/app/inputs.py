"""Checks for what is typed into the Capital Tracking and Account Manager forms."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.user_settings import SYMBOL_RE

MAX_QTY = Decimal("10000000")
MAX_PRICE = Decimal("10000000")
MAX_FEES = Decimal("1000000")
EARLIEST = date(1970, 1, 1)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contract(Strict):
    """A stock, or one option contract on it."""

    kind: Literal["option", "stock"]
    symbol: str = Field(max_length=12)
    option_type: Literal["call", "put"] | None = None
    strike: Decimal | None = Field(None, gt=0, le=Decimal("1000000"), decimal_places=3)
    expiration: date | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def _symbol(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().upper()
            if not SYMBOL_RE.match(v):
                raise ValueError("not a valid symbol")
        return v

    @model_validator(mode="after")
    def _contract(self):
        if self.kind == "stock":
            self.option_type = self.strike = self.expiration = None
        elif self.option_type is None or self.strike is None or self.expiration is None:
            raise ValueError("an option needs call or put, a strike and an expiration")
        elif not EARLIEST <= self.expiration <= date.today() + timedelta(days=366 * 5):
            raise ValueError("the expiration date is not right")
        return self


def check_day(d: date, label: str) -> date:
    if d < EARLIEST or d > date.today() + timedelta(days=1):
        raise ValueError(f"{label} cannot be in the future")
    return d


class Purchase(Strict):
    quantity: Decimal = Field(gt=0, le=MAX_QTY, decimal_places=4)
    price: Decimal = Field(ge=0, le=MAX_PRICE, decimal_places=4)
    fees: Decimal = Field(Decimal("0"), ge=0, le=MAX_FEES, decimal_places=2)
    day: date
    note: str = Field("", max_length=200)

    @field_validator("day")
    @classmethod
    def _day(cls, v: date) -> date:
        return check_day(v, "the date")


class NewPosition(Contract, Purchase):
    """Add-position form: what was bought, how many, at what price, and when."""


class PositionTrade(Purchase):
    side: Literal["buy", "sell"]


class PositionEdit(Strict):
    note: str = Field(max_length=200)


class FlowIn(Strict):
    account: Literal["long_term", "short_term"]
    kind: Literal["deposit", "withdrawal"]
    amount: Decimal = Field(gt=0, le=Decimal("1000000000"), decimal_places=2)
    day: date
    note: str = Field("", max_length=200)

    @field_validator("day")
    @classmethod
    def _day(cls, v: date) -> date:
        return check_day(v, "the date")


CloseReason = Literal["take_profit", "stop_loss", "signal", "manual", "time", "expired"]


class ManualTrade(Contract):
    """A finished short-term trade typed in by hand. Times are in the user's own time zone."""

    direction: Literal["long", "short"] = "long"
    quantity: Decimal = Field(gt=0, le=MAX_QTY, decimal_places=4)
    entry_price: Decimal = Field(ge=0, le=MAX_PRICE, decimal_places=4)
    exit_price: Decimal = Field(ge=0, le=MAX_PRICE, decimal_places=4)
    fees: Decimal = Field(Decimal("0"), ge=0, le=MAX_FEES, decimal_places=2)
    opened_at: datetime
    closed_at: datetime
    close_reason: CloseReason = "manual"
    notes: str = Field("", max_length=5000)

    @model_validator(mode="after")
    def _times(self):
        if self.closed_at < self.opened_at:
            raise ValueError("the close time is before the open time")
        return self


class NotesEdit(Strict):
    notes: str = Field(max_length=5000)
