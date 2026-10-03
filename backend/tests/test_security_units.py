"""Password rules, two-step codes, encryption, and email checks."""
import pyotp
import pytest

from app import crypto, keys, passwords, totp
from app.auth import email_looks_valid, normalize_email


def test_password_hash_roundtrip():
    h = passwords.hash_password("a good long password")
    assert h.startswith("$argon2id$")
    assert "a good long password" not in h
    assert passwords.verify_password(h, "a good long password")
    assert not passwords.verify_password(h, "a good long passworD")


def test_verify_without_hash_is_always_false():
    assert not passwords.verify_password(None, "anything at all")


def test_verify_tolerates_garbage_hash():
    assert not passwords.verify_password("not-a-hash", "whatever password")


@pytest.mark.parametrize(
    "pw, email, ok",
    [
        ("short", "", False),
        ("x" * 12, "", False),  # too repetitive
        ("            ", "", False),
        ("rafa@example.com", "Rafa@Example.com", False),
        ("x" * 257, "", False),
        ("four words in a row", "rafa@example.com", True),
    ],
)
def test_password_rules(pw, email, ok):
    assert (passwords.password_problem(pw, email) is None) is ok


def test_totp_accepts_current_and_neighbouring_steps_once():
    secret = totp.new_secret()
    t = 1_800_000_000.0
    step = int(t // 30)
    code_now = pyotp.TOTP(secret).at(t)
    assert totp.matching_step(secret, code_now, None, now=t) == step
    # The same code cannot be used again once its step is recorded.
    assert totp.matching_step(secret, code_now, step, now=t) is None
    # A code from the previous 30 seconds still counts (phone clock a little behind).
    prev = pyotp.TOTP(secret).at(t - 30)
    assert totp.matching_step(secret, prev, None, now=t) == step - 1
    # Two steps away does not.
    old = pyotp.TOTP(secret).at(t - 90)
    assert totp.matching_step(secret, old, None, now=t) is None


def test_totp_ignores_spaces_and_rejects_wrong_length():
    secret = totp.new_secret()
    t = 1_800_000_000.0
    code = pyotp.TOTP(secret).at(t)
    assert totp.matching_step(secret, f"{code[:3]} {code[3:]}", None, now=t) is not None
    assert totp.matching_step(secret, code[:5], None, now=t) is None
    assert totp.matching_step(secret, "", None, now=t) is None


def test_totp_qr_is_inline_svg():
    uri = totp.setup_uri(totp.new_secret(), "rafa@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "AR%20Portfolio%20Bot" in uri
    assert totp.qr_svg_data_uri(uri).startswith("data:image/svg+xml")


def test_encrypt_roundtrip_and_no_plaintext():
    ct = crypto.encrypt("my-secret-token-1234")
    assert b"my-secret-token-1234" not in ct
    assert crypto.decrypt(ct) == "my-secret-token-1234"


def test_missing_master_key_is_reported_not_crashing(monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "master_key_file", str(tmp_path / "nope"))
    crypto.reset_cache()
    try:
        assert not crypto.available()
        with pytest.raises(crypto.KeyStoreUnavailable):
            crypto.encrypt("x")
    finally:
        monkeypatch.undo()
        crypto.reset_cache()
    assert crypto.available()


def test_decrypt_with_other_key_fails_cleanly():
    from cryptography.fernet import Fernet

    foreign = Fernet(Fernet.generate_key()).encrypt(b"x")
    with pytest.raises(crypto.KeyStoreUnavailable):
        crypto.decrypt(foreign)


def test_last4():
    assert keys.last4("abcdefgh1234") == "1234"
    # Too short to reveal anything safely.
    assert keys.last4("abc1234") == ""


@pytest.mark.parametrize(
    "email, ok",
    [("rafa@example.com", True), ("a@b.co", True), ("rafa", False), ("a@b", False),
     ("a b@c.com", False), ("@c.com", False), ("a@.com.", False), ("a@b@c.com", False)],
)
def test_email_check(email, ok):
    assert email_looks_valid(email) is ok


def test_normalize_email():
    assert normalize_email("  Rafa@Example.COM ") == "rafa@example.com"
