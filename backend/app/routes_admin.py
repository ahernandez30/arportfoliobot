"""Admin only: the user list, roles, disabling accounts, and invites."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, invites
from app.auth import audit, normalize_email, require_admin
from app.db import get_db
from app.models import Invite, LoginSession, User

router = APIRouter(prefix="/api/admin")


class UserChange(BaseModel):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class InviteIn(BaseModel):
    email: str = Field("", max_length=254)
    role: Literal["admin", "user"] = "user"
    note: str = Field("", max_length=120)
    valid_hours: int = 72


def _user_row(db: Session, u: User) -> dict:
    last_seen = db.scalar(
        select(func.max(LoginSession.last_seen_at)).where(
            LoginSession.user_id == u.id, LoginSession.mfa_pending.is_(False)
        )
    )
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "two_step": u.totp_enabled,
        "created_at": u.created_at.isoformat(),
        "last_seen_at": last_seen.isoformat() if last_seen else None,
    }


def _invite_row(i: Invite) -> dict:
    return {
        "id": i.id,
        "email": i.email,
        "role": i.role,
        "note": i.note,
        "kind": "reset" if i.for_user_id else "signup",
        "status": invites.status(i),
        "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat(),
        "used_at": i.used_at.isoformat() if i.used_at else None,
    }


@router.get("/users")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [_user_row(db, u) for u in db.scalars(select(User).order_by(User.created_at))]


def _get_other_user(db: Session, admin: User, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such user.")
    if target.id == admin.id:
        raise HTTPException(409, "You cannot change your own role or access here. Ask another admin.")
    return target


@router.patch("/users/{user_id}")
def change_user(
    user_id: int,
    body: UserChange,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = _get_other_user(db, admin, user_id)
    if body.role is not None and body.role != target.role:
        audit(db, "role_changed", user_id=target.id, actor_id=admin.id, request=request,
              detail=f"{target.role} -> {body.role}")
        target.role = body.role
    if body.is_active is not None and body.is_active != target.is_active:
        target.is_active = body.is_active
        if not body.is_active:
            auth.end_sessions(db, target.id)
        audit(db, "user_enabled" if body.is_active else "user_disabled", user_id=target.id,
              actor_id=admin.id, request=request)
    db.commit()
    return _user_row(db, target)


@router.post("/users/{user_id}/reset-two-step")
def reset_two_step(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    """For a user who lost their phone. They can sign in with the password alone and set it up again."""
    target = _get_other_user(db, admin, user_id)
    target.totp_enabled = False
    target.totp_secret_enc = None
    target.totp_last_step = None
    auth.end_sessions(db, target.id)
    audit(db, "two_step_reset", user_id=target.id, actor_id=admin.id, request=request)
    db.commit()
    return _user_row(db, target)


@router.post("/users/{user_id}/reset-password")
def password_reset_link(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    """A one-time link for a user who forgot their password. The admin passes it on."""
    target = _get_other_user(db, admin, user_id)
    if not target.is_active:
        raise HTTPException(409, "Enable the account first.")
    invite, link = invites.create_password_reset(db, target, valid_hours=24, created_by=admin.id)
    audit(db, "password_reset_link", user_id=target.id, actor_id=admin.id, request=request,
          detail=f"link {invite.id}")
    db.commit()
    return {"invite": _invite_row(invite), "link": link}


@router.get("/invites")
def list_invites(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Invite).order_by(Invite.created_at.desc()).limit(50))
    return [_invite_row(i) for i in rows]


@router.post("/invites")
def create_invite(
    body: InviteIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    if body.valid_hours not in invites.VALID_HOURS:
        raise HTTPException(422, "Choose how long the link works: 1, 3 or 7 days.")
    email = normalize_email(body.email) or None
    if email is not None:
        if not auth.email_looks_valid(email):
            raise HTTPException(422, "That email address does not look right.")
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise HTTPException(409, "An account with that email already exists.")
    invite, link = invites.create(
        db, role=body.role, valid_hours=body.valid_hours, created_by=admin.id, email=email, note=body.note.strip()
    )
    audit(db, "invite_created", actor_id=admin.id, request=request,
          detail=f"invite {invite.id}, role {invite.role}")
    db.commit()
    return {"invite": _invite_row(invite), "link": link}


@router.delete("/invites/{invite_id}")
def revoke_invite(
    invite_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(404, "No such invite.")
    if invites.status(invite) == "open":
        invite.revoked_at = auth.now_utc()
        audit(db, "invite_revoked", actor_id=admin.id, request=request, detail=f"invite {invite.id}")
        db.commit()
    return _invite_row(invite)
