"""Two-step login codes from an authenticator app (TOTP, 6 digits, 30 seconds)."""
import time

import pyotp
import segno

ISSUER = "AR Portfolio Bot"
STEP_SECONDS = 30


def new_secret() -> str:
    return pyotp.random_base32()


def setup_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_svg_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#000", light="#fff")


def clean_code(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit())


def matching_step(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The time step the code belongs to, allowing one step of clock drift either way.

    Returns None if the code is wrong, or if it was already used (its step is not
    later than last_step), so a code seen over someone's shoulder cannot be replayed.
    """
    code = clean_code(code)
    if len(code) != 6:
        return None
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(now // STEP_SECONDS)
    for step in (current, current - 1, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if pyotp.utils.strings_equal(totp.at(step * STEP_SECONDS), code):
            return step
    return None
