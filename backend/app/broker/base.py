"""The Broker interface (plan section 4). Screens and strategies place orders through this,
never through a broker directly, so the paper broker and a real one (Stage 8) are interchangeable."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class OptionOrder:
    """Buy to open one option contract (a limit order). Stage 6 adds two-leg spreads."""

    symbol: str
    option_type: str
    strike: Decimal
    expiration: date
    quantity: int
    limit_price: Decimal
    take_profit_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    source: str = "manual"
    idempotency_key: str | None = None


class Broker(ABC):
    #: True only for a broker that moves real money (Stage 8).
    real_money: bool = False

    @abstractmethod
    def place_order(self, order: OptionOrder) -> int:
        """Places an opening order and returns its id."""

    @abstractmethod
    def close_position(self, position_id: int, quantity: int | None, limit_price: Decimal | None,
                       reason: str = "manual") -> int:
        """Sells all or part of a position; returns the order id."""

    @abstractmethod
    def cancel_order(self, order_id: int) -> None: ...

    @abstractmethod
    def positions(self) -> list: ...

    @abstractmethod
    def orders(self) -> list: ...

    @abstractmethod
    def balance(self) -> Decimal:
        """Cash in the account."""
