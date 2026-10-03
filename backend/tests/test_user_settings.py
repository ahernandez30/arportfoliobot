import pytest
from pydantic import ValidationError

from app import user_settings
from app.user_settings import SettingsModel, apply_changes


def test_defaults():
    s = SettingsModel()
    assert s.display.timezone == "America/New_York"
    assert s.display.theme == "dark"
    assert s.trading.auto_trading == "off"
    assert s.paper.starting_balance == 100_000
    assert s.paper.fill_rule == "bid_ask"
    assert s.watchlist.symbols == ["TSLA", "QQQ", "SPY"]


def test_partial_change_keeps_everything_else():
    s = apply_changes(SettingsModel(), {"trading": {"stop_loss_pct": 12.5}})
    assert s.trading.stop_loss_pct == 12.5
    assert s.trading.take_profit_pct == SettingsModel().trading.take_profit_pct
    assert s.display == SettingsModel().display


@pytest.mark.parametrize(
    "changes",
    [
        {"trading": {"stop_loss_pct": 0}},
        {"trading": {"stop_loss_pct": 101}},
        {"trading": {"contracts_per_trade": 0}},
        {"trading": {"contracts_per_trade": 1.5}},
        {"trading": {"max_order_usd": -5}},
        {"trading": {"auto_trading": "paper_and_real"}},  # not before Stage 8
        {"paper": {"starting_balance": 10}},
        {"paper": {"fill_rule": "best"}},
        {"display": {"timezone": "Mars/Olympus"}},
        {"display": {"theme": "pink"}},
        {"watchlist": {"symbols": ["TSLA", "not a symbol"]}},
        {"watchlist": {"default_ticker": ""}},
        {"nonsense": 1},
        {"trading": {"unknown": 1}},
    ],
)
def test_rejects_out_of_range(changes):
    with pytest.raises(ValidationError):
        apply_changes(SettingsModel(), changes)


def test_symbols_are_cleaned():
    s = apply_changes(SettingsModel(), {"watchlist": {"symbols": [" tsla", "QQQ", "tsla", "brk.b", ""],
                                                      "default_ticker": " spy "}})
    assert s.watchlist.symbols == ["TSLA", "QQQ", "BRK.B"]
    assert s.watchlist.default_ticker == "SPY"


def test_stored_bad_value_falls_back_to_default_for_that_section_only():
    stored = {"display": {"theme": "light"}, "trading": {"stop_loss_pct": -3}}
    s = user_settings._parse_stored(stored)
    assert s.display.theme == "light"
    assert s.trading == SettingsModel().trading


def test_stored_unknown_old_setting_is_ignored_safely():
    s = user_settings._parse_stored({"display": {"theme": "light", "retired_option": True}})
    assert s.display.theme == "light"
