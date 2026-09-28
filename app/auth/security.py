import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PBKDF2_ITERATIONS = 200_000
TOKEN_TTL = int(os.getenv("CHAT_TOKEN_TTL", 7 * 24 * 3600))
_DEV_SECRET = "chatser-dev-secret-change-me"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _secret() -> bytes:
    return os.getenv("CHAT_SECRET_KEY", _DEV_SECRET).encode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_token(user_id: int) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def decode_token(token: str) -> int | None:
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64e(expected), sig):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return int(payload.get("uid"))
