"""PaperBroker: the Broker interface over the paper engine. The only order-placing broker until Stage 8."""
from decimal import Decimal

from sqlalchemy.orm import Session

from app import paper
from app.broker.base import Broker, OptionOrder


class PaperBroker(Broker):
    real_money = False

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def place_order(self, order: OptionOrder) -> int:
        c = paper.Contract(order.symbol, order.option_type, order.strike, order.expiration)
        return paper.place_open(self.db, self.user_id, c, order.quantity, order.limit_price, order.take_profit_pct,
                                order.stop_loss_pct, source=order.source,
                                idempotency_key=order.idempotency_key).id

    def close_position(self, position_id: int, quantity: int | None, limit_price: Decimal | None,
                       reason: str = "manual") -> int:
        return paper.place_close(self.db, self.user_id, position_id, quantity, limit_price, reason).id

    def cancel_order(self, order_id: int) -> None:
        paper.cancel(self.db, self.user_id, order_id)

    def positions(self) -> list:
        return paper.open_positions(self.db, self.user_id)

    def orders(self) -> list:
        return paper.working_orders(self.db, self.user_id)

    def balance(self) -> Decimal:
        return paper.account(self.db, self.user_id).cash
