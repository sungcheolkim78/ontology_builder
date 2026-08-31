import hashlib
import os
import secrets

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def issue_token(password: str) -> str | None:
    if not APP_PASSWORD or not secrets.compare_digest(password, APP_PASSWORD):
        return None
    return _expected_token()


def is_valid_token(token: str) -> bool:
    return bool(APP_PASSWORD) and secrets.compare_digest(token, _expected_token())


def _expected_token() -> str:
    return hashlib.sha256(APP_PASSWORD.encode()).hexdigest()
