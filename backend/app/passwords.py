"""Password hashing (Argon2) and the password rules."""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 256

_hasher = PasswordHasher()
# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("not a real password, only used for timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str, email: str = "") -> str | None:
    """A plain-English reason the password is not allowed, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if not password.strip():
        return "The password cannot be only spaces."
    if len(set(password)) < 4:
        return "Use a less repetitive password."
    if email and password.strip().lower() == email.strip().lower():
        return "The password cannot be your email address."
    return None
