"""Invites, password-reset links, and the admin user list."""
from datetime import timedelta

from sqlalchemy import select, update

from app import cli
from app.auth import now_utc
from app.models import Invite, User
from tests.conftest import PASSWORD, client, enable_two_step, make_user, signed_in

NEW_PW = "a brand new password"


def token_of(link: str) -> str:
    assert link.startswith("https://arportfoliobot.com/invite/")
    return link.rsplit("/", 1)[1]


def invite(admin_client, **body) -> str:
    r = admin_client.post("/api/admin/invites", json=body)
    assert r.status_code == 200, r.text
    return token_of(r.json()["link"])


def test_invite_and_accept(admin_client, db):
    token = invite(admin_client, note="second user")
    c = client()
    info = c.get(f"/api/auth/invite/{token}").json()
    assert info["kind"] == "signup" and info["role"] == "user" and info["email"] is None
    r = c.post(f"/api/auth/invite/{token}", json={"email": "Ana@Example.com", "display_name": "Ana", "password": NEW_PW})
    assert r.status_code == 200, r.text
    me = c.get("/api/me").json()
    assert me["email"] == "ana@example.com" and me["role"] == "user"
    # Signs in normally afterwards.
    signed_in("ana@example.com", NEW_PW)
    # The admin sees the invite as used.
    rows = admin_client.get("/api/admin/invites").json()
    assert rows[0]["status"] == "used"


def test_invite_link_works_once(admin_client):
    token = invite(admin_client)
    body = {"email": "ana@example.com", "display_name": "Ana", "password": NEW_PW}
    assert client().post(f"/api/auth/invite/{token}", json=body).status_code == 200
    body["email"] = "someone@example.com"
    r = client().post(f"/api/auth/invite/{token}", json=body)
    assert r.status_code == 410
    assert client().get(f"/api/auth/invite/{token}").status_code == 410


def test_invite_with_fixed_email_ignores_typed_email(admin_client):
    token = invite(admin_client, email="Ana@example.com")
    assert client().get(f"/api/auth/invite/{token}").json()["email"] == "ana@example.com"
    c = client()
    c.post(f"/api/auth/invite/{token}", json={"email": "other@example.com", "display_name": "Ana", "password": NEW_PW})
    assert c.get("/api/me").json()["email"] == "ana@example.com"


def test_expired_and_revoked_invites(admin_client, db):
    t1 = invite(admin_client)
    db.execute(update(Invite).values(expires_at=now_utc() - timedelta(seconds=1)))
    db.commit()
    assert client().get(f"/api/auth/invite/{t1}").status_code == 410

    r = admin_client.post("/api/admin/invites", json={})
    t2, invite_id = token_of(r.json()["link"]), r.json()["invite"]["id"]
    assert admin_client.delete(f"/api/admin/invites/{invite_id}").json()["status"] == "revoked"
    body = {"email": "ana@example.com", "display_name": "Ana", "password": NEW_PW}
    assert client().post(f"/api/auth/invite/{t2}", json=body).status_code == 410


def test_bad_invite_token():
    assert client().get("/api/auth/invite/not-a-real-token").status_code == 404


def test_invite_token_stored_only_as_hash(admin_client, db):
    token = invite(admin_client)
    assert token.encode() not in db.scalar(select(Invite.token_hash))


def test_accept_checks_password_and_duplicates(admin_client):
    token = invite(admin_client)
    r = client().post(f"/api/auth/invite/{token}", json={"email": "ana@example.com", "display_name": "Ana", "password": "short"})
    assert r.status_code == 422 and "12" in r.json()["detail"]
    r = client().post(f"/api/auth/invite/{token}", json={"email": "rafa@example.com", "display_name": "x", "password": NEW_PW})
    assert r.status_code == 409
    r = client().post(f"/api/auth/invite/{token}", json={"email": "ana@example.com", "display_name": " ", "password": NEW_PW})
    assert r.status_code == 422
    # Still usable after those failures.
    assert client().get(f"/api/auth/invite/{token}").status_code == 200


def test_invite_options_checked(admin_client):
    assert admin_client.post("/api/admin/invites", json={"valid_hours": 5}).status_code == 422
    assert admin_client.post("/api/admin/invites", json={"role": "owner"}).status_code == 422
    assert admin_client.post("/api/admin/invites", json={"email": "rafa@example.com"}).status_code == 409


def test_admin_invite_creates_admin(admin_client):
    token = invite(admin_client, role="admin")
    c = client()
    c.post(f"/api/auth/invite/{token}", json={"email": "ana@example.com", "display_name": "Ana", "password": NEW_PW})
    assert c.get("/api/admin/users").status_code == 200


def test_regular_user_cannot_use_admin_endpoints(admin):
    make_user("ana@example.com")
    c = signed_in("ana@example.com")
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/invites").status_code == 403
    assert c.post("/api/admin/invites", json={}).status_code == 403
    assert c.patch(f"/api/admin/users/{admin.id}", json={"is_active": False}).status_code == 403
    assert c.post(f"/api/admin/users/{admin.id}/reset-password").status_code == 403
    assert client().get("/api/admin/users").status_code == 401


def test_disable_user_signs_them_out(admin_client):
    ana = make_user("ana@example.com")
    c = signed_in("ana@example.com")
    r = admin_client.patch(f"/api/admin/users/{ana.id}", json={"is_active": False})
    assert r.json()["is_active"] is False
    assert c.get("/api/me").status_code == 401
    assert client().post("/api/auth/login", json={"email": "ana@example.com", "password": PASSWORD}).status_code == 401
    admin_client.patch(f"/api/admin/users/{ana.id}", json={"is_active": True})
    signed_in("ana@example.com")


def test_admin_cannot_lock_themself_out(admin, admin_client):
    assert admin_client.patch(f"/api/admin/users/{admin.id}", json={"is_active": False}).status_code == 409
    assert admin_client.patch(f"/api/admin/users/{admin.id}", json={"role": "user"}).status_code == 409


def test_change_role(admin_client):
    ana = make_user("ana@example.com")
    assert admin_client.patch(f"/api/admin/users/{ana.id}", json={"role": "admin"}).json()["role"] == "admin"
    assert signed_in("ana@example.com").get("/api/admin/users").status_code == 200


def test_user_list(admin_client):
    make_user("ana@example.com")
    rows = admin_client.get("/api/admin/users").json()
    assert [r["email"] for r in rows] == ["rafa@example.com", "ana@example.com"]
    assert rows[0]["last_seen_at"] is not None and rows[1]["last_seen_at"] is None
    assert all("password_hash" not in r for r in rows)


def test_password_reset_link(admin_client):
    ana = make_user("ana@example.com")
    old = signed_in("ana@example.com")
    r = admin_client.post(f"/api/admin/users/{ana.id}/reset-password")
    token = token_of(r.json()["link"])
    assert client().get(f"/api/auth/invite/{token}").json() == {
        "kind": "reset", "email": "ana@example.com", "role": "user",
        "expires_at": client().get(f"/api/auth/invite/{token}").json()["expires_at"],
    }
    c = client()
    assert c.post(f"/api/auth/invite/{token}", json={"password": NEW_PW}).json() == {"status": "ok"}
    assert c.get("/api/me").json()["email"] == "ana@example.com"
    assert old.get("/api/me").status_code == 401
    assert client().post("/api/auth/login", json={"email": "ana@example.com", "password": PASSWORD}).status_code == 401
    signed_in("ana@example.com", NEW_PW)
    # Used once only.
    assert client().post(f"/api/auth/invite/{token}", json={"password": "yet another password"}).status_code == 410


def test_new_reset_link_cancels_older_one(admin_client):
    ana = make_user("ana@example.com")
    t1 = token_of(admin_client.post(f"/api/admin/users/{ana.id}/reset-password").json()["link"])
    admin_client.post(f"/api/admin/users/{ana.id}/reset-password")
    assert client().get(f"/api/auth/invite/{t1}").status_code == 410


def test_password_reset_keeps_two_step(admin_client):
    ana = make_user("ana@example.com")
    enable_two_step(signed_in("ana@example.com"))
    token = token_of(admin_client.post(f"/api/admin/users/{ana.id}/reset-password").json()["link"])
    c = client()
    assert c.post(f"/api/auth/invite/{token}", json={"password": NEW_PW}).json() == {"status": "code_required"}
    assert c.get("/api/me").status_code == 401


def test_admin_reset_two_step(admin_client):
    ana = make_user("ana@example.com")
    enable_two_step(signed_in("ana@example.com"))
    assert admin_client.post(f"/api/admin/users/{ana.id}/reset-two-step").json()["two_step"] is False
    signed_in("ana@example.com")  # password alone works again


# ---------- server command line ----------


def test_cli_first_admin_invite(capsys, db):
    assert cli.main(["invite", "--role", "admin"]) == 0
    token = token_of(capsys.readouterr().out.strip().splitlines()[-1])
    c = client()
    r = c.post(f"/api/auth/invite/{token}", json={"email": "rafa@example.com", "display_name": "Rafa", "password": NEW_PW})
    assert r.status_code == 200
    assert c.get("/api/me").json()["role"] == "admin"


def test_cli_reset_password_and_two_step(capsys, db):
    make_user("rafa@example.com", role="admin")
    enable_two_step(signed_in("rafa@example.com"))
    assert cli.main(["reset-two-step", "rafa@example.com"]) == 0
    assert db.scalar(select(User.totp_enabled)) is False
    assert cli.main(["reset-password", "rafa@example.com"]) == 0
    token = token_of(capsys.readouterr().out.strip().splitlines()[-1])
    assert client().post(f"/api/auth/invite/{token}", json={"password": NEW_PW}).json() == {"status": "ok"}
    assert cli.main(["reset-password", "nobody@example.com"]) == 1
    assert cli.main(["users"]) == 0
    assert "rafa@example.com" in capsys.readouterr().out
