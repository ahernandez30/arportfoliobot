"""Turn input-checking errors into one short plain-English message for the screen."""
from collections.abc import Sequence

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_LABELS = {
    "take_profit_pct": "Take profit %",
    "stop_loss_pct": "Stop loss %",
    "contracts_per_trade": "Contracts per trade",
    "max_order_usd": "Largest order allowed",
    "max_daily_loss_usd": "Largest loss per day",
    "auto_trading": "Automatic trading",
    "starting_balance": "Starting balance",
    "fill_rule": "Fill rule",
    "symbols": "Watchlist symbols",
    "default_ticker": "Default ticker",
    "timezone": "Time zone",
    "theme": "Theme",
    "display_name": "Name",
    "email": "Email",
    "secret": "Key",
    "account_id": "Account ID",
}


def first_error(errors: Sequence[dict]) -> str:
    if not errors:
        return "Something in the form is not right."
    err = errors[0]
    loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
    field = next((p for p in reversed(loc) if not p.isdigit()), "")
    label = _LABELS.get(field, field.replace("_", " ").capitalize())
    msg = str(err.get("msg", "is not valid")).removeprefix("Value error, ")
    if err.get("type") == "extra_forbidden":
        return f"Unknown setting: {field}."
    return f"{label}: {msg}." if label else f"{msg}."


async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": first_error(exc.errors())}, status_code=422)
