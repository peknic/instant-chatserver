import json
import os
import random
import time
from pathlib import Path

CODE_VALIDITY = 300
RESEND_COOLDOWN = 30

_DEFAULT_FILE = Path(__file__).resolve().parent / "verify_codes.json"


def _path() -> Path:
    override = os.getenv("CHAT_VERIFY_CODE_FILE")
    return Path(override) if override else _DEFAULT_FILE


def _load() -> dict:
    path = _path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(codes: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(codes, f, indent=2, ensure_ascii=False)


def issue_code(email: str):
    """生成并保存验证码，返回 (ok, message, code|None)。"""
    codes = _load()

    entry = codes.get(email)
    if entry and time.time() - entry.get("send_time", 0) < RESEND_COOLDOWN:
        remaining = int(RESEND_COOLDOWN - (time.time() - entry["send_time"]))
        return False, f"请等待 {remaining} 秒后再发送", None

    code = str(random.randint(100000, 999999))
    codes[email] = {"code": code, "send_time": time.time(), "used": False}
    _save(codes)
    return True, "验证码已发送", code


def verify_code(email: str, user_code: str):
    """校验验证码，返回 (ok, message)。"""
    codes = _load()
    entry = codes.get(email)

    if not entry:
        return False, "未找到验证码记录，请先发送验证码"

    if entry.get("used"):
        del codes[email]
        _save(codes)
        return False, "验证码已过期，请重新发送"

    elapsed = time.time() - entry.get("send_time", 0)
    if elapsed > CODE_VALIDITY:
        del codes[email]
        _save(codes)
        return False, "验证码已过期（超过5分钟），请重新发送"

    if entry.get("code") != str(user_code).strip():
        return False, "验证码错误"

    del codes[email]
    _save(codes)
    return True, "验证成功"
