import itertools
import os
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("CHAT_DATABASE_URL", f"sqlite:///{_tmp.name}")

from app.auth.verifycode import issue_code  # noqa: E402
from app.database import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def sent_codes(tmp_path, monkeypatch):
    """隔离验证码文件并把发信替换为本地发码，记录验证码供断言。"""
    monkeypatch.setenv("CHAT_VERIFY_CODE_FILE", str(tmp_path / "codes.json"))
    monkeypatch.setenv("CHAT_SECRET_KEY", "test-secret")
    captured: dict[str, str] = {}

    def fake_send_code(email: str):
        ok, msg, code = issue_code(email)
        if ok and code:
            captured[email] = code
        return ok, msg, code

    monkeypatch.setattr("app.routers.auth.send_code", fake_send_code)
    return captured


@pytest.fixture
def register_user(sent_codes):
    """通过真实的注册接口（邮箱 + 验证码）创建用户，返回 AuthUser(id, token)。

    AuthUser 是 int 子类，可直接当作成员 ID 使用，同时携带 .token 供鉴权。
    """
    counter = itertools.count(1)

    def _register(client, username: str | None = None) -> "AuthUser":
        if username is None:
            username = f"user{next(counter)}"
        email = f"{username}@gmail.com"
        resp = client.post("/auth/send_code", json={"email": email, "username": username})
        assert resp.status_code == 200, resp.text
        code = sent_codes[email]
        resp = client.post(
            "/auth/register",
            json={"username": username, "email": email, "password": "secret123", "code": code},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return AuthUser(body["user"]["id"], body["token"])

    return _register


class AuthUser(int):
    def __new__(cls, user_id: int, token: str) -> "AuthUser":
        obj = super().__new__(cls, user_id)
        obj.token = token
        return obj


@pytest.fixture
def auth():
    """构造某个用户请求所需的 Authorization 头。"""

    def _auth(user: AuthUser) -> dict[str, str]:
        return {"Authorization": f"Bearer {user.token}"}

    return _auth
