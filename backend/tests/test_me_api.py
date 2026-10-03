"""The signed-in user's own profile, settings, password, two-step, sessions and keys."""
import pyotp
from sqlalchemy import select

from app.models import ApiKey
from tests.conftest import PASSWORD, client, enable_two_step, make_user, signed_in

KEY = "TrAdIeR-sEcReT-TOKEN-9876"


def test_profile_and_email(db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    assert c.put("/api/me/profile", json={"display_name": "Rafa H"}).json()["display_name"] == "Rafa H"
    r = c.put("/api/me/email", json={"email": "new@example.com", "password": "wrong password"})
    assert r.status_code == 403
    r = c.put("/api/me/email", json={"email": "New@Example.com", "password": PASSWORD})
    assert r.json()["email"] == "new@example.com"
    make_user("taken@example.com")
    assert c.put("/api/me/email", json={"email": "taken@example.com", "password": PASSWORD}).status_code == 409


def test_settings_change_and_plain_english_errors():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    r = c.patch("/api/me/settings", json={"display": {"theme": "light"}, "trading": {"stop_loss_pct": 18}})
    assert r.status_code == 200
    s = c.get("/api/me/settings").json()
    assert s["display"]["theme"] == "light" and s["trading"]["stop_loss_pct"] == 18
    r = c.patch("/api/me/settings", json={"trading": {"stop_loss_pct": 0}})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Stop loss %:")
    r = c.patch("/api/me/settings", json={"display": {"timezone": "Nowhere/Land"}})
    assert r.json()["detail"] == "Time zone: unknown time zone."
    # A rejected change saves nothing.
    assert c.get("/api/me/settings").json()["trading"]["stop_loss_pct"] == 18


def test_real_trading_cannot_be_switched_on_yet():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    assert c.patch("/api/me/settings", json={"trading": {"auto_trading": "paper_and_real"}}).status_code == 422
    assert c.patch("/api/me/settings", json={"trading": {"auto_trading": "paper"}}).status_code == 200


def test_password_change_signs_out_other_devices():
    make_user("rafa@example.com")
    phone = signed_in("rafa@example.com")
    laptop = signed_in("rafa@example.com")
    r = laptop.put("/api/me/password", json={"current_password": "wrong one!!", "new_password": "a new long password"})
    assert r.status_code == 403
    r = laptop.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 422
    r = laptop.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "a new long password"})
    assert r.json() == {"status": "ok", "other_sessions_ended": 1}
    assert laptop.get("/api/me").status_code == 200
    assert phone.get("/api/me").status_code == 401
    signed_in("rafa@example.com", "a new long password")


def test_sessions_list_and_sign_out_everywhere():
    make_user("rafa@example.com")
    phone = signed_in("rafa@example.com")
    laptop = signed_in("rafa@example.com")
    rows = laptop.get("/api/me/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    assert laptop.post("/api/me/sessions/end-all").json()["sessions_ended"] == 2
    assert laptop.get("/api/me").status_code == 401
    assert phone.get("/api/me").status_code == 401


def test_two_step_setup_needs_password_and_a_valid_code():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    assert c.post("/api/me/two-step/setup", json={"password": "wrong password"}).status_code == 403
    r = c.post("/api/me/two-step/setup", json={"password": PASSWORD})
    assert r.json()["qr"].startswith("data:image/svg+xml")
    assert c.post("/api/me/two-step/enable", json={"code": "000000"}).status_code == 403
    assert c.get("/api/me").json()["two_step"] is False
    c.post("/api/me/two-step/enable", json={"code": pyotp.TOTP(r.json()["secret"]).now()})
    assert c.get("/api/me").json()["two_step"] is True
    # Setting up again while on is refused (it would replace the phone's secret).
    assert c.post("/api/me/two-step/setup", json={"password": PASSWORD}).status_code == 409


def test_two_step_disable_needs_password_and_code(monkeypatch):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    secret = enable_two_step(c)
    import app.totp as totp_mod

    later = totp_mod.time.time() + 60  # a fresh step, not the one used to enable
    monkeypatch.setattr(totp_mod.time, "time", lambda: later)
    code = pyotp.TOTP(secret).at(later)
    assert c.post("/api/me/two-step/disable", json={"password": "wrong password", "code": code}).status_code == 403
    assert c.post("/api/me/two-step/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 403
    assert c.post("/api/me/two-step/disable", json={"password": PASSWORD, "code": code}).json()["two_step"] is False


def test_keys_saved_encrypted_and_never_returned(db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    listing = c.get("/api/me/keys").json()
    assert listing["storage_ready"] is True
    assert {p["id"] for p in listing["providers"]} >= {"tradier", "tradier_sandbox"}
    r = c.put("/api/me/keys/tradier", json={"secret": f"  {KEY} ", "account_id": "6YA1234"})
    assert r.status_code == 200
    assert KEY not in r.text
    saved = {p["id"]: p["saved"] for p in r.json()["providers"]}["tradier"]
    assert saved["last4"] == "9876" and saved["account_id"] == "6YA1234"
    for path in ("/api/me", "/api/me/keys", "/api/me/settings"):
        assert KEY not in c.get(path).text
    row = db.scalar(select(ApiKey))
    assert KEY.encode() not in row.secret_enc
    # Replacing keeps one row.
    c.put("/api/me/keys/tradier", json={"secret": "another-token-0000"})
    assert db.scalar(select(ApiKey.last4)) == "0000"
    assert c.delete("/api/me/keys/tradier").status_code == 200
    assert c.delete("/api/me/keys/tradier").status_code == 404


def test_keys_input_checked():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    assert c.put("/api/me/keys/robinhood", json={"secret": KEY}).status_code == 404
    assert c.put("/api/me/keys/tradier", json={"secret": "short"}).status_code == 422
    assert c.put("/api/me/keys/tradier", json={"secret": "has a space inside"}).status_code == 422


def test_secret_never_written_to_audit_log(db):
    from app.models import AuditLog

    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": KEY})
    details = " ".join(db.scalars(select(AuditLog.detail)))
    assert KEY not in details


def test_everything_here_needs_sign_in():
    c = client()
    for method, path in [("get", "/api/me"), ("get", "/api/me/settings"), ("patch", "/api/me/settings"),
                         ("get", "/api/me/keys"), ("put", "/api/me/keys/tradier"), ("get", "/api/me/sessions"),
                         ("put", "/api/me/password"), ("post", "/api/me/two-step/setup")]:
        assert getattr(c, method)(path, **({} if method in ("get",) else {"json": {}})).status_code in (401, 422)
