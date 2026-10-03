"""Broker and market-data keys: stored encrypted, shown only as their last 4 characters."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.models import ApiKey

# Which services a key can be for. Stage 2 and Stage 8 add the code that uses them.
PROVIDERS = {
    "tradier": "Tradier (live account)",
    "tradier_sandbox": "Tradier (sandbox / practice)",
    "databento": "Databento",
}


def last4(secret: str) -> str:
    return secret[-4:] if len(secret) >= 8 else ""


def save(db: Session, user_id: int, provider: str, secret: str, account_id: str = "") -> ApiKey:
    row = db.scalar(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
    encrypted = crypto.encrypt(secret)
    if row is None:
        row = ApiKey(user_id=user_id, provider=provider)
        db.add(row)
    row.secret_enc = encrypted
    row.last4 = last4(secret)
    row.account_id = account_id
    db.flush()
    return row


def list_for(db: Session, user_id: int) -> list[ApiKey]:
    return list(db.scalars(select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.provider)))


def delete(db: Session, user_id: int, provider: str) -> bool:
    row = db.scalar(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
    if row is None:
        return False
    db.delete(row)
    return True


def get_secret(db: Session, user_id: int, provider: str) -> str | None:
    """The decrypted key, for server-side use only (Stage 2 onward). Never send it to a browser."""
    row = db.scalar(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
    return crypto.decrypt(row.secret_enc) if row else None
