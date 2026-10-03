"""Sign in, two-step code, sign out, and accepting an invite."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, crypto, invites, passwords, totp, user_settings
from app.auth import audit, client_ip, normalize_email
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/api/auth")

TOO_MANY = "Too many failed attempts. Wait 15 minutes and try again."


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=passwords.MAX_LENGTH)


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


class AcceptIn(BaseModel):
    email: str = Field("", max_length=254)
    # Not needed for a password-reset link.
    display_name: str = Field("", max_length=80)
    password: str = Field(max_length=passwords.MAX_LENGTH)


def _start_session(db: Session, user: User, request: Request, response: Response, *, mfa_pending: bool) -> None:
    token = auth.create_session(db, user, request, mfa_pending=mfa_pending)
    db.commit()
    auth.set_session_cookie(response, token, mfa_pending=mfa_pending)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    email = normalize_email(body.email)
    ip = client_ip(request)
    if auth.login_blocked(db, email, ip):
        raise HTTPException(429, TOO_MANY)
    user = db.scalar(select(User).where(User.email == email))
    ok = passwords.verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        auth.record_failure(db, email, ip)
        audit(db, "login_failed", user_id=user.id if user else None, request=request,
              detail="account disabled" if ok and user and not user.is_active else "")
        db.commit()
        raise HTTPException(401, "Wrong email or password.")
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(body.password)
    auth.prune_expired(db)
    if user.totp_enabled:
        _start_session(db, user, request, response, mfa_pending=True)
        return {"status": "code_required"}
    audit(db, "login", user_id=user.id, actor_id=user.id, request=request)
    _start_session(db, user, request, response, mfa_pending=False)
    return {"status": "ok"}


@router.post("/code")
def login_code(body: CodeIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    found = auth.find_session(db, request)
    if found is None or not found[0].mfa_pending:
        raise HTTPException(401, "Your sign-in expired. Enter your email and password again.")
    pending, user = found
    ip = client_ip(request)
    if auth.login_blocked(db, user.email, ip):
        raise HTTPException(429, TOO_MANY)
    try:
        secret = crypto.decrypt(user.totp_secret_enc) if user.totp_secret_enc else None
    except crypto.KeyStoreUnavailable:
        raise HTTPException(503, "Two-step sign-in is unavailable on the server. Contact the admin.")
    step = totp.matching_step(secret, body.code, user.totp_last_step) if secret else None
    if step is None:
        auth.record_failure(db, user.email, ip)
        audit(db, "login_code_failed", user_id=user.id, request=request)
        db.commit()
        raise HTTPException(401, "That code is not right. Use the newest code from your app.")
    user.totp_last_step = step
    db.delete(pending)
    audit(db, "login", user_id=user.id, actor_id=user.id, request=request, detail="with two-step code")
    _start_session(db, user, request, response, mfa_pending=False)
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    found = auth.find_session(db, request)
    if found is not None:
        db.delete(found[0])
        db.commit()
    auth.clear_session_cookie(response)
    return {"status": "ok"}


_INVITE_PROBLEMS = {
    "missing": (404, "This invite link is not valid. Ask for a new one."),
    "used": (410, "This invite link has already been used."),
    "revoked": (410, "This invite link was cancelled. Ask for a new one."),
    "expired": (410, "This invite link has expired. Ask for a new one."),
}


@router.get("/invite/{token}")
def invite_info(token: str, db: Session = Depends(get_db)) -> dict:
    invite, state = invites.find_open(db, token)
    if state != "open":
        code, msg = _INVITE_PROBLEMS[state]
        raise HTTPException(code, msg)
    return {
        "kind": "reset" if invite.for_user_id else "signup",
        "email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
    }


@router.post("/invite/{token}")
def accept_invite(
    token: str, body: AcceptIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict:
    invite, state = invites.find_open(db, token, lock=True)
    if state != "open":
        code, msg = _INVITE_PROBLEMS[state]
        raise HTTPException(code, msg)
    if invite.for_user_id is not None:
        return _reset_password(db, invite, body, request, response)
    email = invite.email or normalize_email(body.email)
    if not auth.email_looks_valid(email):
        raise HTTPException(422, "That email address does not look right.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(409, "An account with that email already exists.")
    problem = passwords.password_problem(body.password, email)
    if problem:
        raise HTTPException(422, problem)
    if not body.display_name.strip():
        raise HTTPException(422, "Enter your name.")
    user = User(
        email=email,
        display_name=body.display_name.strip(),
        password_hash=passwords.hash_password(body.password),
        role=invite.role,
    )
    db.add(user)
    db.flush()
    user_settings.save(db, user.id, user_settings.SettingsModel())
    invite.used_at = auth.now_utc()
    invite.used_by = user.id
    audit(db, "invite_accepted", user_id=user.id, actor_id=user.id, request=request,
          detail=f"invite {invite.id}, role {invite.role}")
    _start_session(db, user, request, response, mfa_pending=False)
    return {"status": "ok"}



def _reset_password(db: Session, invite, body: AcceptIn, request: Request, response: Response) -> dict:
    user = db.get(User, invite.for_user_id)
    if user is None or not user.is_active:
        raise HTTPException(410, "This account is disabled. Contact the admin.")
    problem = passwords.password_problem(body.password, user.email)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = passwords.hash_password(body.password)
    user.password_changed_at = auth.now_utc()
    auth.end_sessions(db, user.id)
    invite.used_at = auth.now_utc()
    invite.used_by = user.id
    audit(db, "password_reset", user_id=user.id, actor_id=user.id, request=request, detail=f"link {invite.id}")
    # Two-step sign-in stays on: the new password alone does not sign them in.
    _start_session(db, user, request, response, mfa_pending=user.totp_enabled)
    return {"status": "code_required" if user.totp_enabled else "ok"}
