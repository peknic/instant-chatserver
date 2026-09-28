from fastapi.testclient import TestClient

from app.main import app


def _send_code(client: TestClient, email: str, username: str | None = None):
    return client.post("/auth/send_code", json={"email": email, "username": username})


def test_register_login_and_me(sent_codes):
    email = "alice@gmail.com"
    with TestClient(app) as client:
        assert _send_code(client, email).status_code == 200
        code = sent_codes[email]

        resp = client.post(
            "/auth/register",
            json={"username": "alice", "email": email, "password": "secret123", "code": code},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["user"]["username"] == "alice"
        assert body["user"]["email"] == email
        token = body["token"]

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "alice"

        # login by username
        assert (
            client.post("/auth/login", json={"account": "alice", "password": "secret123"}).status_code
            == 200
        )
        # login by email
        assert (
            client.post("/auth/login", json={"account": email, "password": "secret123"}).status_code
            == 200
        )
        # wrong password
        assert (
            client.post("/auth/login", json={"account": "alice", "password": "wrong"}).status_code
            == 401
        )


def test_send_code_validation(sent_codes):
    with TestClient(app) as client:
        assert _send_code(client, "not-an-email").status_code == 400
        assert _send_code(client, "alice@example.com").status_code == 400
        assert _send_code(client, "a.b@gmail.com").status_code == 400


def test_send_code_cooldown(sent_codes):
    with TestClient(app) as client:
        assert _send_code(client, "bob@gmail.com").status_code == 200
        assert _send_code(client, "bob@gmail.com").status_code == 429


def test_register_conflicts(sent_codes):
    email = "carol@gmail.com"
    with TestClient(app) as client:
        _send_code(client, email, username="carol")
        code = sent_codes[email]
        payload = {"username": "carol", "email": email, "password": "secret123", "code": code}
        assert client.post("/auth/register", json=payload).status_code == 201

        # duplicate username
        assert _send_code(client, "carol2@gmail.com", username="carol").status_code == 400
        # duplicate email
        assert _send_code(client, email).status_code == 400


def test_wrong_and_reused_code(sent_codes):
    email = "dave@gmail.com"
    with TestClient(app) as client:
        _send_code(client, email)
        code = sent_codes[email]
        payload = {"username": "dave", "email": email, "password": "secret123", "code": "000000"}

        # wrong code (unless the random code happens to be 000000)
        if code != "000000":
            assert client.post("/auth/register", json=payload).status_code == 400

        payload["code"] = code
        assert client.post("/auth/register", json=payload).status_code == 201

        # code consumed: a second registration with the same code fails
        second = {"username": "dave2", "email": "dave2@gmail.com", "password": "secret123", "code": code}
        assert client.post("/auth/register", json=second).status_code == 400


def test_plus_alias_is_same_email(sent_codes):
    with TestClient(app) as client:
        alias = "erin+tag@gmail.com"
        assert _send_code(client, alias).status_code == 200
        code = sent_codes["erin@gmail.com"]

        resp = client.post(
            "/auth/register",
            json={"username": "erin", "email": alias, "password": "secret123", "code": code},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["email"] == "erin@gmail.com"

        # another alias must be rejected as taken
        assert _send_code(client, "erin+other@gmail.com").status_code == 400
        # login with an alias still resolves to the same account
        assert (
            client.post(
                "/auth/login", json={"account": "erin+other@gmail.com", "password": "secret123"}
            ).status_code
            == 200
        )


def test_me_rejects_bad_token(sent_codes):
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert (
            client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
        )
