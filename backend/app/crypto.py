"""Encryption of stored secrets with the master key.

The master key lives in a root-only file outside the repo and outside the
database (/etc/arportfoliobot/master.key). systemd reads it as root and hands it
to the services through LoadCredential, so the arpb user never needs read access
to the file itself. Database backups therefore never contain the master key.
"""
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

CREDENTIAL_NAME = "master_key"


class KeyStoreUnavailable(RuntimeError):
    """The master key is missing or unreadable, so secrets cannot be stored or read."""


def _key_path() -> Path | None:
    explicit = get_settings().master_key_file
    if explicit:
        return Path(explicit)
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        return Path(cred_dir) / CREDENTIAL_NAME
    return None


@lru_cache
def _fernet() -> Fernet:
    path = _key_path()
    if path is None:
        raise KeyStoreUnavailable("no master key configured")
    try:
        return Fernet(path.read_bytes().strip())
    except (OSError, ValueError) as exc:
        raise KeyStoreUnavailable("master key could not be loaded") from exc


def available() -> bool:
    try:
        _fernet()
        return True
    except KeyStoreUnavailable:
        return False


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise KeyStoreUnavailable("stored secret does not match the master key") from exc


def generate_key() -> bytes:
    return Fernet.generate_key()


def reset_cache() -> None:
    _fernet.cache_clear()


if __name__ == "__main__":
    # Used by deploy.sh to create the master key once. Prints the key to stdout,
    # which deploy.sh pipes straight into the root-only file.
    os.write(1, generate_key())
