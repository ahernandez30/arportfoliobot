"""One-time links for signing up or choosing a new password.

Used by the admin screen and by the server command line."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_token, new_token, now_utc
from app.config import get_settings
from app.models import Invite, User

VALID_HOURS = (24, 72, 168)


def invite_link(token: str) -> str:
    return f"{get_settings().public_url}/invite/{token}"


def create(
    db: Session,
    *,
    role: str,
    valid_hours: int,
    created_by: int | None,
    email: str | None = None,
    note: str = "",
) -> tuple[Invite, str]:
    """Returns the invite and its link. The link is shown once and never stored."""
    token, token_hash = new_token()
    invite = Invite(
        token_hash=token_hash,
        email=email or None,
        role=role,
        note=note,
        created_by=created_by,
        created_at=now_utc(),
        expires_at=now_utc() + timedelta(hours=valid_hours),
    )
    db.add(invite)
    db.flush()
    return invite, invite_link(token)


def create_password_reset(
    db: Session, user: User, *, valid_hours: int, created_by: int | None = None
) -> tuple[Invite, str]:
    """A one-time link for `user` to choose a new password. Earlier unused reset links stop working."""
    for old in db.scalars(
        select(Invite).where(Invite.for_user_id == user.id, Invite.used_at.is_(None), Invite.revoked_at.is_(None))
    ):
        old.revoked_at = now_utc()
    token, token_hash = new_token()
    invite = Invite(
        token_hash=token_hash,
        email=user.email,
        role=user.role,
        note="password reset",
        for_user_id=user.id,
        created_by=created_by,
        created_at=now_utc(),
        expires_at=now_utc() + timedelta(hours=valid_hours),
    )
    db.add(invite)
    db.flush()
    return invite, invite_link(token)


def status(invite: Invite) -> str:
    if invite.used_at is not None:
        return "used"
    if invite.revoked_at is not None:
        return "revoked"
    if now_utc() >= invite.expires_at:
        return "expired"
    return "open"


def find_open(db: Session, token: str, *, lock: bool = False) -> tuple[Invite | None, str]:
    """The invite for a link token, and its status ('missing' if there is none)."""
    stmt = select(Invite).where(Invite.token_hash == hash_token(token))
    if lock:
        stmt = stmt.with_for_update()
    invite = db.scalar(stmt)
    if invite is None:
        return None, "missing"
    return invite, status(invite)
