"""Login sessions, the request dependencies that check them, rate limits and the audit log."""
import hashlib
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AuditLog, LoginAttempt, LoginSession, User

# __Host- prefix: the browser only accepts it over HTTPS, for this exact host, path /.
COOKIE_NAME = "__Host-arpb_session"
# last_seen_at is written at most this often, to avoid a database write per request.
TOUCH_EVERY = timedelta(minutes=5)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def client_ip(request: Request) -> str | None:
    # uvicorn runs with --proxy-headers trusting only nginx on 127.0.0.1,
    # so request.client is the visitor's real address.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def audit(
    db: Session,
    event: str,
    *,
    user_id: int | None = None,
    actor_id: int | None = None,
    detail: str = "",
    request: Request | None = None,
) -> None:
    db.add(
        AuditLog(
            event=event,
            user_id=user_id,
            actor_id=actor_id,
            detail=detail,
            ip=client_ip(request) if request else None,
        )
    )


# ---------- sessions ----------


def create_session(db: Session, user: User, request: Request, *, mfa_pending: bool) -> str:
    s = get_settings()
    token, token_hash = new_token()
    now = now_utc()
    lifetime = timedelta(minutes=s.mfa_pending_minutes) if mfa_pending else timedelta(days=s.session_max_days)
    db.add(
        LoginSession(
            token_hash=token_hash,
            user_id=user.id,
            mfa_pending=mfa_pending,
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
            user_agent=(request.headers.get("user-agent") or "")[:300],
            ip=client_ip(request),
        )
    )
    return token


def set_session_cookie(response: Response, token: str, *, mfa_pending: bool) -> None:
    s = get_settings()
    max_age = s.mfa_pending_minutes * 60 if mfa_pending else s.session_max_days * 86400
    response.set_cookie(
        COOKIE_NAME, token, max_age=max_age, path="/", secure=True, httponly=True, samesite="strict"
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")


def session_is_live(sess: LoginSession, now: datetime) -> bool:
    if now >= sess.expires_at:
        return False
    if sess.mfa_pending:
        return True
    return now - sess.last_seen_at < timedelta(hours=get_settings().session_idle_hours)


def find_session(db: Session, request: Request) -> tuple[LoginSession, User] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    sess = db.get(LoginSession, hash_token(token))
    if sess is None:
        return None
    now = now_utc()
    if not session_is_live(sess, now):
        db.delete(sess)
        db.commit()
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if not sess.mfa_pending and now - sess.last_seen_at >= TOUCH_EVERY:
        sess.last_seen_at = now
        db.commit()
    return sess, user


def current_session(request: Request, db: Session = Depends(get_db)) -> tuple[LoginSession, User]:
    found = find_session(db, request)
    if found is None or found[0].mfa_pending:
        raise HTTPException(401, "Please sign in.")
    return found


def current_user(found: tuple[LoginSession, User] = Depends(current_session)) -> User:
    return found[1]


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Only an admin can do that.")
    return user


def end_sessions(db: Session, user_id: int, *, keep: bytes | None = None) -> int:
    stmt = delete(LoginSession).where(LoginSession.user_id == user_id)
    if keep is not None:
        stmt = stmt.where(LoginSession.token_hash != keep)
    return db.execute(stmt).rowcount


def prune_expired(db: Session) -> None:
    """Drop expired sessions and old login-failure records."""
    now = now_utc()
    s = get_settings()
    db.execute(delete(LoginSession).where(LoginSession.expires_at < now))
    db.execute(
        delete(LoginSession).where(
            LoginSession.mfa_pending.is_(False),
            LoginSession.last_seen_at < now - timedelta(hours=s.session_idle_hours),
        )
    )
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=1)))


# ---------- rate limiting ----------


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_looks_valid(email: str) -> bool:
    local, _, domain = email.partition("@")
    return (
        bool(local) and "." in domain.strip(".") and "@" not in domain
        and not any(ch.isspace() for ch in email) and len(email) <= 254
    )


def login_blocked(db: Session, email: str, ip: str | None) -> bool:
    s = get_settings()
    since = now_utc() - timedelta(minutes=s.login_window_minutes)
    by_email = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.at >= since)
    )
    if by_email >= s.login_max_failures_per_email:
        return True
    if ip is None:
        return False
    by_ip = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.at >= since)
    )
    return by_ip >= s.login_max_failures_per_ip


def record_failure(db: Session, email: str, ip: str | None) -> None:
    db.add(LoginAttempt(email=email, ip=ip, at=now_utc()))
