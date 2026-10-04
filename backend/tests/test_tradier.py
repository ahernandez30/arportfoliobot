"""The Tradier client, against recorded-style answers (no network)."""
import asyncio
import json
from datetime import date, datetime

import httpx
import pytest

from app.marketdata.bars import NY
from app.marketdata.base import MarketDataError
from app.marketdata.tradier import TradierMarketData, as_list, parse_stream_message

TOKEN = "secret-token-abcdef123456"


def client(handler, *, sandbox=False) -> TradierMarketData:
    return TradierMarketData(TOKEN, sandbox=sandbox, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_as_list():
    assert as_list(None) == [] and as_list("null") == []
    assert as_list({"a": 1}) == [{"a": 1}]
    assert as_list([1, 2]) == [1, 2]


def test_sends_bearer_token_to_the_right_server():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"clock": {"state": "closed", "description": "Market is closed"}})

    run(client(handler).clock())
    assert seen["url"].startswith("https://api.tradier.com/v1/markets/clock")
    assert seen["auth"] == f"Bearer {TOKEN}"
    run(client(handler, sandbox=True).clock())
    assert seen["url"].startswith("https://sandbox.tradier.com/")


def test_token_not_in_repr():
    assert TOKEN not in repr(client(lambda r: httpx.Response(200)))


def test_quotes_single_and_many_and_unknown():
    def handler(req):
        assert req.method == "POST"
        syms = httpx.QueryParams(req.content.decode())["symbols"].split(",")
        if syms == ["SPY"]:
            q = {"symbol": "SPY", "last": 600.5, "prevclose": 598.0, "change_percentage": 0.42, "bid": 600.4,
                 "ask": 600.6, "trade_date": 1790000000000, "description": "SPDR S&P 500"}
            return httpx.Response(200, json={"quotes": {"quote": q}})
        assert syms == ["BRK/B", "XXXX"]  # BRK.B is written BRK/B at Tradier
        return httpx.Response(200, json={"quotes": {"quote": [{"symbol": "BRK/B", "last": "480.1"}],
                                                    "unmatched_symbols": {"symbol": "XXXX"}}})

    one = run(client(handler).quotes(["SPY"]))
    assert one["SPY"].last == 600.5 and one["SPY"].prev_close == 598.0 and one["SPY"].trade_time == 1790000000000
    many = run(client(handler).quotes(["BRK.B", "XXXX"]))
    assert list(many) == ["BRK.B"] and many["BRK.B"].last == 480.1


def test_no_quotes_at_all():
    out = run(client(lambda r: httpx.Response(200, json={"quotes": {"unmatched_symbols": {"symbol": "ZZZZ"}}})).quotes(["ZZZZ"]))
    assert out == {}


def test_daily_candles():
    def handler(req):
        assert req.url.params["interval"] == "daily"
        return httpx.Response(200, json={"history": {"day": [
            {"date": "2026-10-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"date": "2026-10-02", "open": 1.5, "high": 3, "low": 1, "close": 2.5, "volume": 20},
        ]}})

    bars = run(client(handler).candles("TSLA", "1D"))
    assert [b.close for b in bars] == [1.5, 2.5]
    assert datetime.utcfromtimestamp(bars[0].time).date() == date(2026, 10, 1)


def test_empty_history_is_empty_list():
    assert run(client(lambda r: httpx.Response(200, json={"history": None})).candles("TSLA", "1W")) == []


def test_hourly_candles_built_from_15_minute_timesales():
    def handler(req):
        p = req.url.params
        assert p["interval"] == "15min" and p["session_filter"] == "open"
        rows = [{"time": f"2026-10-02T{t}:00", "open": 1, "high": h, "low": 1, "close": 1, "volume": 5}
                for t, h in [("09:30", 2), ("09:45", 3), ("10:00", 4), ("10:15", 1), ("10:30", 9)]]
        return httpx.Response(200, json={"series": {"data": rows}})

    bars = run(client(handler).candles("SPY", "1h"))
    assert len(bars) == 2
    assert bars[0].high == 4 and bars[0].volume == 20
    assert datetime.fromtimestamp(bars[1].time, NY).strftime("%H:%M") == "10:30"


def test_unknown_timeframe():
    with pytest.raises(MarketDataError):
        run(client(lambda r: httpx.Response(200, json={})).candles("SPY", "2h"))


@pytest.mark.parametrize("status, text", [(401, "did not accept the key"), (429, "limit"), (500, r"error \(500\)")])
def test_errors_become_plain_messages(status, text):
    with pytest.raises(MarketDataError, match=text):
        run(client(lambda r: httpx.Response(status)).clock())


def test_plain_text_answer():
    with pytest.raises(MarketDataError, match="unexpected"):
        run(client(lambda r: httpx.Response(200, text="Invalid Parameter: symbol")).clock())


def test_network_failure():
    def handler(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(MarketDataError, match="Cannot reach Tradier"):
        run(client(handler).clock())


def test_option_chain_and_expirations():
    def handler(req):
        if req.url.path.endswith("expirations"):
            return httpx.Response(200, json={"expirations": {"date": ["2026-10-09", "2026-10-16"]}})
        return httpx.Response(200, json={"options": {"option": {
            "symbol": "SPY261016C00600000", "underlying": "SPY", "option_type": "call", "strike": 600,
            "expiration_date": "2026-10-16", "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 10, "open_interest": 99}}})

    md = client(handler)
    assert run(md.option_expirations("SPY")) == [date(2026, 10, 9), date(2026, 10, 16)]
    chain = run(md.option_chain("SPY", date(2026, 10, 16)))
    assert chain[0].strike == 600 and chain[0].option_type == "call" and chain[0].ask == 5.3


def test_sandbox_cannot_stream():
    async def go():
        async for _ in client(lambda r: httpx.Response(200), sandbox=True).stream(["SPY"]):
            pass

    with pytest.raises(MarketDataError, match="sandbox"):
        run(go())


def test_parse_stream_message():
    msg = "\n".join(json.dumps(p) for p in [
        {"type": "trade", "symbol": "SPY", "price": "600.10", "size": "100", "cvol": "123", "date": "1790000000000"},
        {"type": "quote", "symbol": "BRK/B", "bid": 480.0, "ask": 480.2},
        {"type": "summary", "symbol": "SPY", "open": "598", "high": "601", "low": "597", "prevClose": "599"},
        {"type": "timesale", "symbol": "SPY"},
    ]) + "\nnot json\n"
    trade, q, summary = parse_stream_message(msg)
    assert trade.kind == "trade" and trade.fields == {"last": 600.1, "size": 100, "cvol": 123, "time": 1790000000000}
    assert q.symbol == "BRK.B" and q.fields == {"bid": 480.0, "ask": 480.2}
    assert summary.fields["prev_close"] == 599


def test_stream_error_payload():
    with pytest.raises(MarketDataError, match="not a valid symbol"):
        parse_stream_message('{"error":"1234 is not a valid symbol"}')


def test_stream_uses_the_websocket_address(monkeypatch):
    """Tradier's session answer carries the HTTP-streaming URL; the websocket must use wss://ws.tradier.com."""
    import app.marketdata.tradier as tradier_mod

    seen = {}

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        def __aiter__(self):
            async def gen():
                yield '{"type":"trade","symbol":"SPY","price":"1.5","size":"1","date":"1"}\n'
            return gen()

    def fake_connect(url, **kw):
        seen["url"] = url
        seen["ws"] = FakeWS()
        return seen["ws"]

    monkeypatch.setattr(tradier_mod.websockets, "connect", fake_connect)

    def handler(req):
        assert req.url.path == "/v1/markets/events/session"
        return httpx.Response(200, json={"stream": {"url": "https://stream.tradier.com/v1/markets/events", "sessionid": "S1"}})

    async def go():
        return [ev async for ev in client(handler).stream(["SPY", "BRK.B"])]

    events = run(go())
    assert seen["url"] == "wss://ws.tradier.com/v1/markets/events"
    assert seen["ws"].sent[0]["sessionid"] == "S1" and seen["ws"].sent[0]["symbols"] == ["SPY", "BRK/B"]
    assert [e.kind for e in events] == ["ready", "trade"]
