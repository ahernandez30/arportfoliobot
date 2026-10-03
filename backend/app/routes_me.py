"""The signed-in user's own account: profile, settings, password, two-step code, sessions, keys.

Every handler here works only on the user from the session cookie. No handler
takes a user id from the request.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, crypto, keys, passwords, totp, user_settings
from app.auth import audit, current_session, current_user, normalize_email
from app.db import get_db
from app.errors import first_error
from app.models import LoginSession, User

router = APIRouter(prefix="/api/me")


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=passwords.MAX_LENGTH)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=passwords.MAX_LENGTH)
    new_password: str = Field(max_length=passwords.MAX_LENGTH)


class PasswordOnly(BaseModel):
    password: str = Field(max_length=passwords.MAX_LENGTH)


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


class DisableCodeIn(BaseModel):
    password: str = Field(max_length=passwords.MAX_LENGTH)
    code: str = Field(max_length=20)


class KeyIn(BaseModel):
    secret: str = Field(min_length=8, max_length=4096)
    account_id: str = Field("", max_length=64)


def _me(user: User, db: Session) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "two_step": user.totp_enabled,
        "settings": user_settings.load(db, user.id).model_dump(),
    }


def _check_password(db: Session, user: User, password: str, request: Request) -> None:
    """For sensitive changes: the current password, with the same lockout as sign-in."""
    ip = auth.client_ip(request)
    if auth.login_blocked(db, user.email, ip):
        raise HTTPException(429, "Too many failed attempts. Wait 15 minutes and try again.")
    if not passwords.verify_password(user.password_hash, password):
        auth.record_failure(db, user.email, ip)
        db.commit()
        raise HTTPException(403, "Your current password is not right.")


def _check_code(db: Session, user: User, code: str, request: Request) -> None:
    step = totp.matching_step(crypto.decrypt(user.totp_secret_enc), code, user.totp_last_step)
    if step is None:
        auth.record_failure(db, user.email, auth.client_ip(request))
        db.commit()
        raise HTTPException(403, "That code is not right. Use the newest code from your app.")
    user.totp_last_step = step


def _require_keystore() -> None:
    if not crypto.available():
        raise HTTPException(503, "Secure key storage is not set up on the server yet. Tell the admin.")


@router.get("")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _me(user, db)


@router.put("/profile")
def update_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    user.display_name = body.display_name.strip() or user.display_name
    db.commit()
    return _me(user, db)


@router.put("/email")
def change_email(
    body: EmailIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    _check_password(db, user, body.password, request)
    email = normalize_email(body.email)
    if not auth.email_looks_valid(email):
        raise HTTPException(422, "That email address does not look right.")
    if email != user.email:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise HTTPException(409, "Another account already uses that email.")
        audit(db, "email_changed", user_id=user.id, actor_id=user.id, request=request,
              detail=f"{user.email} -> {email}")
        user.email = email
    db.commit()
    return _me(user, db)


@router.get("/settings")
def get_settings_(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return user_settings.load(db, user.id).model_dump()


@router.patch("/settings")
def change_settings(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        updated = user_settings.apply_changes(user_settings.load(db, user.id), body)
    except ValidationError as exc:
        raise HTTPException(422, first_error(exc.errors()))
    user_settings.save(db, user.id, updated)
    db.commit()
    return updated.model_dump()


@router.put("/password")
def change_password(
    body: PasswordIn,
    request: Request,
    found: tuple[LoginSession, User] = Depends(current_session),
    db: Session = Depends(get_db),
) -> dict:
    sess, user = found
    _check_password(db, user, body.current_password, request)
    problem = passwords.password_problem(body.new_password, user.email)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = passwords.hash_password(body.new_password)
    user.password_changed_at = auth.now_utc()
    ended = auth.end_sessions(db, user.id, keep=sess.token_hash)
    audit(db, "password_changed", user_id=user.id, actor_id=user.id, request=request,
          detail=f"{ended} other session(s) signed out")
    db.commit()
    return {"status": "ok", "other_sessions_ended": ended}


# ---------- two-step code ----------


@router.post("/two-step/setup")
def two_step_setup(
    body: PasswordOnly, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """Step 1: a fresh secret to scan. Not switched on until a code from it is confirmed."""
    _require_keystore()
    if user.totp_enabled:
        raise HTTPException(409, "Two-step sign-in is already on.")
    _check_password(db, user, body.password, request)
    secret = totp.new_secret()
    user.totp_secret_enc = crypto.encrypt(secret)
    user.totp_last_step = None
    db.commit()
    uri = totp.setup_uri(secret, user.email)
    return {"qr": totp.qr_svg_data_uri(uri), "secret": secret}


@router.post("/two-step/enable")
def two_step_enable(
    body: CodeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    _require_keystore()
    if user.totp_enabled:
        raise HTTPException(409, "Two-step sign-in is already on.")
    if user.totp_secret_enc is None:
        raise HTTPException(409, "Start the setup first.")
    _check_code(db, user, body.code, request)
    user.totp_enabled = True
    audit(db, "two_step_enabled", user_id=user.id, actor_id=user.id, request=request)
    db.commit()
    return _me(user, db)


@router.post("/two-step/disable")
def two_step_disable(
    body: DisableCodeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    _require_keystore()
    if not user.totp_enabled:
        raise HTTPException(409, "Two-step sign-in is already off.")
    _check_password(db, user, body.password, request)
    _check_code(db, user, body.code, request)
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.totp_last_step = None
    audit(db, "two_step_disabled", user_id=user.id, actor_id=user.id, request=request)
    db.commit()
    return _me(user, db)


# ---------- sessions ----------


@router.get("/sessions")
def list_sessions(
    found: tuple[LoginSession, User] = Depends(current_session), db: Session = Depends(get_db)
) -> list[dict]:
    current, user = found
    rows = db.scalars(
        select(LoginSession)
        .where(LoginSession.user_id == user.id, LoginSession.mfa_pending.is_(False))
        .order_by(LoginSession.last_seen_at.desc())
    )
    now = auth.now_utc()
    return [
        {
            "current": s.token_hash == current.token_hash,
            "created_at": s.created_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat(),
            "user_agent": s.user_agent,
            "ip": str(s.ip) if s.ip else None,
        }
        for s in rows
        if auth.session_is_live(s, now)
    ]


@router.post("/sessions/end-all")
def sign_out_everywhere(
    request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    ended = auth.end_sessions(db, user.id)
    audit(db, "signed_out_everywhere", user_id=user.id, actor_id=user.id, request=request,
          detail=f"{ended} session(s)")
    db.commit()
    auth.clear_session_cookie(response)
    return {"status": "ok", "sessions_ended": ended}


# ---------- keys ----------


@router.get("/keys")
def list_keys(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    saved = {
        k.provider: {
            "last4": k.last4,
            "account_id": k.account_id,
            "updated_at": k.updated_at.isoformat(),
        }
        for k in keys.list_for(db, user.id)
    }
    return {
        "storage_ready": crypto.available(),
        "providers": [{"id": pid, "name": name, "saved": saved.get(pid)} for pid, name in keys.PROVIDERS.items()],
    }


@router.put("/keys/{provider}")
def save_key(
    provider: str, body: KeyIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    if provider not in keys.PROVIDERS:
        raise HTTPException(404, "Unknown service.")
    _require_keystore()
    secret = body.secret.strip()
    if len(secret) < 8 or any(ch.isspace() for ch in secret):
        raise HTTPException(422, "That key does not look right. Paste it without spaces.")
    keys.save(db, user.id, provider, secret, body.account_id.strip())
    audit(db, "key_saved", user_id=user.id, actor_id=user.id, request=request, detail=provider)
    db.commit()
    return list_keys(user, db)


@router.delete("/keys/{provider}")
def delete_key(
    provider: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    if not keys.delete(db, user.id, provider):
        raise HTTPException(404, "No saved key for that service.")
    audit(db, "key_deleted", user_id=user.id, actor_id=user.id, request=request, detail=provider)
    db.commit()
    return list_keys(user, db)
