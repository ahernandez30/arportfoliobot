"""Server-side account commands, run by the admin on the server.

    sudo /opt/arportfoliobot/manage.sh invite --role admin      # first admin, or a lost password
    sudo /opt/arportfoliobot/manage.sh reset-two-step EMAIL
    sudo /opt/arportfoliobot/manage.sh users

No password is ever typed here: an invite link is printed, and the person opens it
and chooses their own password.
"""
import argparse
import sys

from sqlalchemy import select

from app import auth, invites
from app.auth import normalize_email
from app.db import get_sessionmaker
from app.models import User


def cmd_invite(args: argparse.Namespace) -> int:
    with get_sessionmaker()() as db:
        email = normalize_email(args.email) if args.email else None
        if email and db.scalar(select(User.id).where(User.email == email)) is not None:
            print(f"An account with {email} already exists. Use reset-password instead.", file=sys.stderr)
            return 1
        invite, link = invites.create(db, role=args.role, valid_hours=args.hours, created_by=None, email=email,
                                      note="created on the server")
        auth.audit(db, "invite_created", detail=f"invite {invite.id}, role {invite.role}, from server command line")
        db.commit()
    print(f"Invite link ({args.role}, works once, expires in {args.hours} hours):")
    print(link)
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    """Signs the user out everywhere and prints a one-time link to choose a new password."""
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.email == normalize_email(args.email)))
        if user is None:
            print("No account with that email.", file=sys.stderr)
            return 1
        _, link = invites.create_password_reset(db, user, valid_hours=args.hours)
        auth.end_sessions(db, user.id)
        auth.audit(db, "password_reset_link", user_id=user.id, detail="from server command line")
        db.commit()
    print(f"Password reset link for {user.email} (works once, expires in {args.hours} hours):")
    print(link)
    return 0


def cmd_reset_two_step(args: argparse.Namespace) -> int:
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.email == normalize_email(args.email)))
        if user is None:
            print("No account with that email.", file=sys.stderr)
            return 1
        user.totp_enabled = False
        user.totp_secret_enc = None
        user.totp_last_step = None
        auth.end_sessions(db, user.id)
        auth.audit(db, "two_step_reset", user_id=user.id, detail="from server command line")
        db.commit()
    print(f"Two-step sign-in is now off for {user.email}. They can sign in with their password and set it up again.")
    return 0


def cmd_users(_args: argparse.Namespace) -> int:
    with get_sessionmaker()() as db:
        for u in db.scalars(select(User).order_by(User.id)):
            flags = [u.role, "active" if u.is_active else "DISABLED", "two-step on" if u.totp_enabled else "two-step off"]
            print(f"{u.id:>4}  {u.email:<40} {', '.join(flags)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.sh", description="AR Portfolio Bot account commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("invite", help="print a one-time sign-up link")
    p.add_argument("--role", choices=["admin", "user"], default="user")
    p.add_argument("--email", default="")
    p.add_argument("--hours", type=int, choices=invites.VALID_HOURS, default=72)
    p.set_defaults(func=cmd_invite)

    p = sub.add_parser("reset-password", help="print a one-time link to choose a new password")
    p.add_argument("email")
    p.add_argument("--hours", type=int, choices=invites.VALID_HOURS, default=24)
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("reset-two-step", help="turn off two-step sign-in for a user who lost their phone")
    p.add_argument("email")
    p.set_defaults(func=cmd_reset_two_step)

    p = sub.add_parser("users", help="list accounts")
    p.set_defaults(func=cmd_users)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
