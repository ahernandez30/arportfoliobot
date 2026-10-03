"""Sign in, two-step code, sign out, session expiry, rate limits."""
from datetime import timedelta

import pyotp
from sqlalchemy import select, update

from app.auth import COOKIE_NAME, now_utc
from app.models import AuditLog, LoginSession, User
from tests.conftest import PASSWORD, client, enable_two_step, make_user, signed_in


def test_me_requires_sign_in():
    assert client().get("/api/me").status_code == 401


def test_sign_in_sets_secure_cookie_and_me_works():
    make_user("rafa@example.com")
    c = client()
    r = c.post("/api/auth/login", json={"email": " Rafa@Example.com ", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()
    me = c.get("/api/me").json()
    assert me["email"] == "rafa@example.com"
    assert "password_hash" not in me and "totp_secret_enc" not in me
    assert c.get("/api/me").headers["cache-control"] == "no-store"


def test_wrong_password_and_unknown_email_look_the_same():
    make_user("rafa@example.com")
    a = client().post("/api/auth/login", json={"email": "rafa@example.com", "password": "wrong password!"})
    b = client().post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong password!"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_session_token_is_stored_only_as_hash(db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    token = c.cookies.get(COOKIE_NAME)
    stored = db.scalars(select(LoginSession.token_hash)).all()
    assert len(stored) == 1 and token.encode() not in stored[0]


def test_logout_ends_the_session():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    stolen = dict(c.cookies)
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/me").status_code == 401
    # The old cookie value is dead on the server too, not just deleted in the browser.
    other = client()
    other.cookies.update(stolen)
    assert other.get("/api/me").status_code == 401


def test_disabled_account_cannot_sign_in():
    make_user("off@example.com", active=False)
    r = client().post("/api/auth/login", json={"email": "off@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_idle_session_expires(db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    db.execute(update(LoginSession).values(last_seen_at=now_utc() - timedelta(days=8)))
    db.commit()
    assert c.get("/api/me").status_code == 401
    assert db.scalar(select(LoginSession)) is None


def test_absolute_session_expiry(db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    db.execute(update(LoginSession).values(expires_at=now_utc() - timedelta(seconds=1)))
    db.commit()
    assert c.get("/api/me").status_code == 401


def test_rate_limit_per_email():
    make_user("rafa@example.com")
    c = client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "rafa@example.com", "password": "nope nope"}).status_code == 401
    # Locked now, even with the right password.
    r = c.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    assert r.status_code == 429
    # Another account is not affected.
    make_user("other@example.com")
    assert client().post("/api/auth/login", json={"email": "other@example.com", "password": PASSWORD}).status_code == 200


def test_rate_limit_per_ip():
    c = client()
    for i in range(20):
        c.post("/api/auth/login", json={"email": f"guess{i}@example.com", "password": "nope nope"})
    make_user("rafa@example.com")
    r = c.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    assert r.status_code == 429
    # The same person from another address is fine.
    r = client(ip="198.51.100.7").post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    assert r.status_code == 200


def test_failed_logins_are_audited(db):
    make_user("rafa@example.com")
    client().post("/api/auth/login", json={"email": "rafa@example.com", "password": "nope nope"})
    events = db.scalars(select(AuditLog.event)).all()
    assert events == ["login_failed"]


def test_cross_site_post_is_refused():
    make_user("rafa@example.com")
    r = client().post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD},
                      headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client().post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD},
                      headers={"Origin": "https://testserver"})
    assert r.status_code == 200


# ---------- two-step ----------


def test_two_step_flow(db):
    make_user("rafa@example.com")
    secret = enable_two_step(signed_in("rafa@example.com"))

    c = client()
    r = c.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    assert r.json() == {"status": "code_required"}
    # Half-signed-in: nothing visible yet.
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/auth/code", json={"code": "000000"}).status_code == 401
    # Wait for a fresh 30-second step that enabling did not already use.
    user = db.scalar(select(User))
    db.refresh(user)
    code_step = user.totp_last_step + 1
    code = pyotp.TOTP(secret).at(code_step * 30)
    import app.totp as totp_mod

    real = totp_mod.time.time
    totp_mod.time.time = lambda: code_step * 30 + 1
    try:
        assert c.post("/api/auth/code", json={"code": code}).status_code == 200
        assert c.get("/api/me").json()["two_step"] is True
        # The same code cannot be used for a second sign-in.
        c2 = client()
        c2.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
        assert c2.post("/api/auth/code", json={"code": code}).status_code == 401
    finally:
        totp_mod.time.time = real


def test_code_step_needs_password_first():
    assert client().post("/api/auth/code", json={"code": "123456"}).status_code == 401


def test_pending_two_step_expires(db):
    make_user("rafa@example.com")
    secret = enable_two_step(signed_in("rafa@example.com"))
    c = client()
    c.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    db.execute(update(LoginSession).where(LoginSession.mfa_pending.is_(True))
               .values(expires_at=now_utc() - timedelta(seconds=1)))
    db.commit()
    r = c.post("/api/auth/code", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 401


def test_two_step_codes_rate_limited():
    make_user("rafa@example.com")
    enable_two_step(signed_in("rafa@example.com"))
    c = client()
    c.post("/api/auth/login", json={"email": "rafa@example.com", "password": PASSWORD})
    for _ in range(5):
        c.post("/api/auth/code", json={"code": "000000"})
    assert c.post("/api/auth/code", json={"code": "000000"}).status_code == 429


def test_two_step_secret_is_encrypted(db):
    make_user("rafa@example.com")
    secret = enable_two_step(signed_in("rafa@example.com"))
    stored = db.scalar(select(User.totp_secret_enc))
    assert secret.encode() not in stored
