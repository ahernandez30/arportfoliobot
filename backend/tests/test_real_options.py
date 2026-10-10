"""Real option prices (Backtest plan, step 3): reading stored downloads, finding what is missing,
and the download job: estimate, the user's yes, download within the agreed limit, run again."""
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import data_jobs
from app.backtest import real_options
from app.backtest.option_history import ModelOptionHistory
from app.backtest.real_options import Missing, StoredOptionHistory, occ
from app.db import get_engine
from app.marketdata.bars import NY
from app.marketdata.base import DailyClose
from sqlalchemy.orm import sessionmaker

from app.models import DataJob
from tests.conftest import make_user
from tests.test_backtest import body, md, rafa  # noqa: F401 (fixtures)


def test_occ_symbols():
    assert occ("TSLA", date(2026, 5, 15), "call", Decimal("400")) == "TSLA  260515C00400000"
    assert occ("TSLA", date(2023, 6, 16), "put", Decimal("156.67")) == "TSLA  230616P00156670"
    assert real_options.parse_occ("TSLA  260515C00400000") == ("TSLA", date(2026, 5, 15), "call", Decimal("400"))


def _seed(db, user_id):
    day = date(2024, 1, 10)
    real_options.save_chain(db, user_id, "TSLA", day, [
        ("TSLA", date(2024, 2, 16), "call", Decimal("240")), ("TSLA", date(2024, 2, 16), "call", Decimal("245")),
        ("TSLA", date(2024, 2, 16), "put", Decimal("240")), ("TSLA1", date(2024, 2, 16), "call", Decimal("80"))])
    at = int(datetime(2024, 1, 10, 16, 0, tzinfo=NY).timestamp())
    c = occ("TSLA", date(2024, 2, 16), "call", Decimal("240"))
    real_options.save_quotes(db, user_id, [c, occ("TSLA", date(2024, 2, 16), "call", Decimal("245"))], at - 900, at,
                             [(c, at - 120, Decimal("10.10"), Decimal("10.30")), (c, at - 60, Decimal("10.20"), Decimal("10.40"))])
    db.commit()
    return datetime(2024, 1, 10, 16, 0, tzinfo=NY)


def test_stored_prices_and_what_is_missing(db):
    u = make_user("rafa@example.com")
    at = _seed(db, u.id)
    h = StoredOptionHistory(db, u.id, "TSLA")
    assert h.expirations("TSLA", at) == [date(2024, 2, 16)]
    assert h.strikes("TSLA", at, 240.0) == [Decimal("240"), Decimal("245")]  # the adjusted TSLA1 contract is left out
    assert h.quote("TSLA", "call", Decimal("240"), date(2024, 2, 16), at, 240.0) == (Decimal("10.20"), Decimal("10.40"))
    # Fetched, but no quote in the window: no price, and nothing to fetch again.
    assert h.quote("TSLA", "call", Decimal("245"), date(2024, 2, 16), at, 240.0) == (None, None)
    assert h.quote("TSLA", "put", Decimal("245"), date(2024, 2, 16), at, 240.0) == (None, None)  # not listed
    assert h.missing.empty()
    # A listed contract at another moment, and a day without a chain, are written down as missing.
    later = at + timedelta(days=7)
    assert h.quote("TSLA", "call", Decimal("240"), date(2024, 2, 16), later, 240.0) == (None, None)
    assert h.expirations("TSLA", later) == []
    assert h.missing.days == {later.date()} and h.missing.moments == {int(later.timestamp()): {occ("TSLA", date(2024, 2, 16), "call", Decimal("240"))}}
    # Before Databento's options history begins there is nothing to fetch.
    old = datetime(2022, 6, 1, 16, 0, tzinfo=NY)
    assert h.expirations("TSLA", old) == [] and date(2022, 6, 1) not in h.missing.days
    assert Missing.from_dict(h.missing.as_dict()).moments == h.missing.moments


def test_the_estimate_fills_gaps_while_planning(db):
    u = make_user("rafa@example.com")
    at = _seed(db, u.id) + timedelta(days=7)
    model = ModelOptionHistory({"TSLA": [DailyClose(date(2023, 12, 1) + timedelta(days=i), 240 + i % 3) for i in range(60)]})
    h = StoredOptionHistory(db, u.id, "TSLA", fallback=model)
    assert h.expirations("TSLA", at) == model.expirations("TSLA", at)
    bid, ask = h.quote("TSLA", "call", Decimal("240"), date(2024, 2, 16), at, 240.0)
    assert bid is not None and ask > bid  # the estimate, while the real one is written down as missing
    assert h.missing.moments


class FakeDatabento:
    """Stands in for Databento: every Friday expiration for 20 weeks, strikes every 5 dollars, and a
    quote a minute before each moment. Each call costs one cent."""

    calls: list = []

    def __init__(self, key):
        assert key == "db-key-0000000000000000000000000"

    def chain_cost(self, underlying, day):
        return 0.01

    def minute_cost_all(self, underlying, at):
        return 40.0  # a cent per contract-minute

    def quotes_cost(self, contracts, start, end):
        return 0.01

    def chain(self, underlying, day):
        FakeDatabento.calls.append(("chain", day))
        first = day + timedelta(days=(4 - day.weekday()) % 7 or 7)
        return [(underlying, first + timedelta(weeks=w), kind, Decimal(k)) for w in range(20) for kind in ("call", "put")
                for k in range(50, 400, 5)]

    def quotes(self, contracts, start, end):
        """Puts are worth more at higher strikes, calls at lower ones."""
        FakeDatabento.calls.append(("quotes", len(contracts)))
        out = []
        for c in contracts:
            _, _, kind, k = real_options.parse_occ(c)
            bid = (k if kind == "put" else 500 - k) / 20
            out.append((c, end - 120, bid, bid + Decimal("0.10")))
        return out


def run_job(job_id):
    asyncio.run(data_jobs.Runner(sessionmaker(get_engine(), expire_on_commit=False), job_id).run())


def test_download_job_end_to_end(md, rafa, db, monkeypatch):  # noqa: F811 (fixtures)
    monkeypatch.setattr(data_jobs, "make_client", FakeDatabento)
    FakeDatabento.calls = []
    real = body(option={"structure": "credit_spread", "width_usd": 5}, option_prices="real", start="2023-06-01")
    r = rafa.post("/api/backtest/run", json=real)
    assert r.status_code == 409 and "Databento key" in r.json()["detail"]
    rafa.put("/api/me/keys/databento", json={"secret": "db-key-0000000000000000000000000"})
    r = rafa.post("/api/backtest/run", json=real)
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["status"] == "estimating" and rafa.get("/api/backtest/runs").json() == []
    run_job(job["id"])
    job = rafa.get(f"/api/backtest/data-jobs/{job['id']}").json()
    assert job["status"] == "confirm" and job["estimate_usd"] > 0 and job["limit_usd"] >= job["estimate_usd"]
    assert FakeDatabento.calls == []  # nothing downloaded before the yes
    assert rafa.post(f"/api/backtest/data-jobs/{job['id']}/confirm").json()["status"] == "queued"
    run_job(job["id"])
    job = rafa.get(f"/api/backtest/data-jobs/{job['id']}").json()
    assert job["status"] == "done", job
    assert 0 < job["spent_usd"] <= job["limit_usd"]
    assert {c[0] for c in FakeDatabento.calls} == {"chain", "quotes"}
    # Now the run has its real prices and is saved.
    r = rafa.post("/api/backtest/run", json=real)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["notes"]["options_source"] == "real (Databento OPRA)" and r.json()["setup"]["option_prices"] == "real"
    rows = [o for t in res["trades"] for opts in t["options"].values() for o in opts]
    priced = [o for o in rows if "problem" not in o]
    assert len(priced) > len(rows) / 2
    assert all(o["entry"] == pytest.approx(0.15) for o in priced)  # $5 apart: $0.25 less the $0.10 spread
    cov = rafa.get("/api/backtest/history").json()
    assert cov["databento_key"] and cov["options"]["chains"][0]["days"] > 0 and cov["options"]["spent_usd"] == job["spent_usd"]


def test_download_stops_at_the_limit_and_can_be_cancelled(md, rafa, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(data_jobs, "make_client", FakeDatabento)
    rafa.put("/api/me/keys/databento", json={"secret": "db-key-0000000000000000000000000"})
    job = rafa.post("/api/backtest/run", json=body(option={"structure": "credit_spread", "width_usd": 5}, option_prices="real", start="2023-06-01")).json()["job"]
    run_job(job["id"])
    j = db.get(DataJob, job["id"])
    j.plan = {**j.plan, "limit_usd": 0.02}  # room for two requests only
    j.status = "queued"
    db.commit()
    run_job(job["id"])
    db.expire_all()
    j = db.get(DataJob, job["id"])
    assert j.status == "failed" and "Stopped before passing" in j.error and float(j.spent_usd) <= 0.02
    # Cancelling: only while it has not finished, and only your own.
    job2 = rafa.post("/api/backtest/run", json=body(option={"structure": "credit_spread", "width_usd": 5}, option_prices="real", start="2023-06-01")).json()["job"]
    make_user("other@example.com")
    from tests.conftest import signed_in

    assert signed_in("other@example.com").post(f"/api/backtest/data-jobs/{job2['id']}/cancel").status_code == 404
    assert rafa.post(f"/api/backtest/data-jobs/{job2['id']}/cancel").json()["status"] == "cancelled"
    assert rafa.post(f"/api/backtest/data-jobs/{job2['id']}/confirm").status_code == 409


def test_estimate_without_known_chains_uses_the_positions():
    class C:
        def chain_cost(self, u, d):
            return 0.5

        def minute_cost_all(self, u, t):
            return 4.0

    m = Missing({date(2024, 1, 10), date(2024, 1, 11)})
    total, plan = data_jobs.estimate(C(), "TSLA", m, positions=10)
    # Chains: 2 x $0.50. Prices: 10 positions x 10 contracts x 16 minutes x $0.001.
    assert plan["chain_days"] == 2 and not plan["prices_known"]
    assert float(total) == pytest.approx(1.0 + 10 * 10 * 16 * 0.001)
    assert plan["limit_usd"] == float((Decimal(str(total)) * Decimal("1.5") + 1).to_integral_value(rounding="ROUND_UP"))



def test_estimate_check_compares_at_half_hour_marks():
    from app.backtest import estimate_check
    from app.marketdata.base import Bar

    t0 = int(datetime(2024, 1, 10, 9, 30, tzinfo=NY).timestamp())
    days = [Bar(t0 - 86400 * k, 240, 241, 239, 240 + (k % 5), 1) for k in range(40, 0, -1)]
    bars30 = days + [Bar(t0, 240, 241, 239, 240, 1)]
    c = occ("TSLA", date(2024, 3, 15), "call", Decimal("240"))
    rows = [(t0 + 1800, c, 20.0, 20.4), (t0 + 1860, c, 1.0, 1.1),  # the second is not on a half-hour mark
            (t0 + 1800, occ("TSLA", date(2024, 3, 15), "call", Decimal("400")), 0.01, 0.02)]  # under 10 cents
    got = estimate_check.samples(rows, bars30, "TSLA")
    assert len(got) == 1 and got[0].real_mid == pytest.approx(20.2) and got[0].dte == 65
    rep = estimate_check.report(got)
    assert rep["overall"]["n"] == 1 and "31-90 days" in rep["by_days_to_expiration"]
