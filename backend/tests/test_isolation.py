"""One user can never see or change another user's data (plan section 9)."""
from sqlalchemy import select

from app import keys
from app.models import ApiKey
from tests.conftest import make_user, signed_in

RAFA_KEY = "rafa-secret-token-AAAA1111"
ANA_KEY = "ana-secret-token-BBBB2222"


def two_users():
    make_user("rafa@example.com", role="admin")
    make_user("ana@example.com")
    rafa, ana = signed_in("rafa@example.com"), signed_in("ana@example.com")
    rafa.patch("/api/me/settings", json={"display": {"theme": "light"}, "watchlist": {"symbols": ["NVDA"]},
                                         "trading": {"max_order_usd": 777}})
    rafa.put("/api/me/keys/tradier", json={"secret": RAFA_KEY, "account_id": "RAFA-ACCT"})
    ana.put("/api/me/keys/tradier", json={"secret": ANA_KEY, "account_id": "ANA-ACCT"})
    return rafa, ana


def test_me_shows_only_own_account():
    rafa, ana = two_users()
    assert rafa.get("/api/me").json()["email"] == "rafa@example.com"
    assert ana.get("/api/me").json()["email"] == "ana@example.com"


def test_settings_are_separate():
    rafa, ana = two_users()
    a = ana.get("/api/me/settings").json()
    assert a["display"]["theme"] == "dark"
    assert a["watchlist"]["symbols"] == ["TSLA", "QQQ", "SPY"]
    assert a["trading"]["max_order_usd"] != 777
    ana.patch("/api/me/settings", json={"display": {"theme": "dark"}, "trading": {"max_order_usd": 5}})
    assert rafa.get("/api/me/settings").json()["trading"]["max_order_usd"] == 777


def test_keys_are_separate():
    rafa, ana = two_users()
    ana_view = ana.get("/api/me/keys").text
    assert "1111" not in ana_view and "RAFA-ACCT" not in ana_view
    rafa_view = rafa.get("/api/me/keys").text
    assert "2222" not in rafa_view and "ANA-ACCT" not in rafa_view
    # Ana deleting "her" tradier key leaves Rafa's alone.
    ana.delete("/api/me/keys/tradier")
    saved = {p["id"]: p["saved"] for p in rafa.get("/api/me/keys").json()["providers"]}
    assert saved["tradier"]["last4"] == "1111"


def test_server_side_key_lookup_is_per_user(db):
    two_users()
    rafa_id, ana_id = sorted(db.scalars(select(ApiKey.user_id)))
    assert keys.get_secret(db, rafa_id, "tradier") == RAFA_KEY
    assert keys.get_secret(db, ana_id, "tradier") == ANA_KEY
    assert keys.get_secret(db, ana_id, "databento") is None


def test_sessions_are_separate():
    rafa, ana = two_users()
    assert len(ana.get("/api/me/sessions").json()) == 1
    # Ana signing out everywhere does not sign Rafa out.
    ana.post("/api/me/sessions/end-all")
    assert rafa.get("/api/me").status_code == 200


def test_password_change_does_not_touch_other_user():
    rafa, ana = two_users()
    from tests.conftest import PASSWORD

    ana.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "ana's new password"})
    assert rafa.get("/api/me").status_code == 200
    signed_in("rafa@example.com")


def test_no_endpoint_accepts_another_users_id():
    """Own-data endpoints ignore any user id sent by the browser."""
    rafa, ana = two_users()
    r = ana.patch("/api/me/settings", json={"user_id": 1, "display": {"theme": "light"}})
    assert r.status_code == 422  # unknown field refused outright
    assert rafa.get("/api/me/settings").json()["trading"]["max_order_usd"] == 777


def test_capital_and_trades_are_separate(monkeypatch):
    from app import routes_market
    from app.marketdata import service
    from tests.fakes import FakeMarketData

    # Never call Tradier from tests.
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: FakeMarketData())
    rafa, ana = two_users()
    pos = {"kind": "stock", "symbol": "SPY", "quantity": 1, "price": 500, "day": "2026-09-01"}
    pid = rafa.post("/api/capital/positions", json=pos).json()["open"][0]["id"]
    tid = rafa.get("/api/capital").json()["open"][0]["trades"][0]["id"]
    fid = rafa.post("/api/flows", json={"account": "long_term", "kind": "deposit", "amount": 1000,
                                        "day": "2026-09-01"}).json()["id"]
    trade = {"kind": "stock", "symbol": "SPY", "quantity": 1, "entry_price": 1, "exit_price": 2,
             "opened_at": "2026-09-01T10:00", "closed_at": "2026-09-01T11:00"}
    xid = rafa.post("/api/trades", json=trade).json()["id"]

    view = ana.get("/api/capital").json()
    assert view["open"] == [] and view["flows"] == [] and view["totals"]["total"] == 0
    assert ana.get("/api/flows?account=long_term").json() == []
    assert ana.get("/api/trades?period=all").json()["trades"] == []
    assert ana.get("/api/capital/history").json() == {"snapshots": [], "flows": []}

    # Every way of reaching Rafa's records by id answers "not found" for Ana.
    sale = {"side": "sell", "quantity": 1, "price": 1, "day": "2026-09-02"}
    assert ana.post(f"/api/capital/positions/{pid}/trades", json=sale).status_code == 404
    assert ana.patch(f"/api/capital/positions/{pid}", json={"note": "x"}).status_code == 404
    assert ana.delete(f"/api/capital/positions/{pid}").status_code == 404
    assert ana.put(f"/api/capital/trades/{tid}", json={**sale, "side": "buy"}).status_code == 404
    assert ana.delete(f"/api/capital/trades/{tid}").status_code == 404
    assert ana.delete(f"/api/flows/{fid}").status_code == 404
    assert ana.patch(f"/api/trades/{xid}", json={"notes": "x"}).status_code == 404
    assert ana.put(f"/api/trades/{xid}", json=trade).status_code == 404
    assert ana.delete(f"/api/trades/{xid}").status_code == 404
    assert "1000" not in ana.get("/api/trades.csv?period=all").text

    # Ana buying the same stock makes her own position, not an addition to Rafa's.
    ana.post("/api/capital/positions", json=pos)
    assert rafa.get("/api/capital").json()["open"][0]["quantity"] == 1
    assert len(rafa.get("/api/capital").json()["open"]) == 1
    assert rafa.get("/api/trades?period=all").json()["trades"][0]["id"] == xid
