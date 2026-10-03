import io

from fastapi.testclient import TestClient

from app.main import app


def _upload(client, chat_id, user, auth, name="note.txt", data=b"hello"):
    return client.post(
        f"/chats/{chat_id}/files",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        headers=auth(user),
    )


def test_upload_saves_file_and_creates_file_message(register_user, auth, tmp_path, monkeypatch):
    root = tmp_path / "chatfiles"
    monkeypatch.setenv("CHAT_FILE_DIR", str(root))

    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat = client.post(
            "/chats", json={"name": "项目群", "members": [alice, bob]}, headers=auth(alice)
        ).json()

        resp = _upload(client, chat["id"], alice, auth, name="报告.pdf", data=b"%PDF-1.4")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["message_type"] == "file"
        assert body["sender_id"] == alice
        assert body["content"] == "chatfiles/项目群/报告.pdf"
        assert (root / "项目群" / "报告.pdf").read_bytes() == b"%PDF-1.4"

        listed = client.get(
            f"/chats/{chat['id']}/messages", headers=auth(bob)
        ).json()["items"]
        assert [m["message_type"] for m in listed] == ["file"]
        assert listed[0]["content"] == body["content"]


def test_upload_requires_membership(register_user, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_FILE_DIR", str(tmp_path / "chatfiles"))

    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        outsider = register_user(client, "dave")
        chat = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()

        assert _upload(client, chat["id"], outsider, auth).status_code == 403
        assert _upload(client, chat["id"], alice, auth, data=b"").status_code == 400


def test_duplicate_filenames_get_unique_paths(register_user, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_FILE_DIR", str(tmp_path / "chatfiles"))

    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat = client.post(
            "/chats", json={"name": "群", "members": [alice, bob]}, headers=auth(alice)
        ).json()

        first = _upload(client, chat["id"], alice, auth, name="a.txt").json()["content"]
        second = _upload(client, chat["id"], alice, auth, name="a.txt").json()["content"]
        assert first != second
        assert first == "chatfiles/群/a.txt"
        assert second == "chatfiles/群/a-1.txt"


def test_download_requires_membership_and_token(register_user, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_FILE_DIR", str(tmp_path / "chatfiles"))

    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        outsider = register_user(client, "dave")
        chat = client.post(
            "/chats", json={"name": "群", "members": [alice, bob]}, headers=auth(alice)
        ).json()
        _upload(client, chat["id"], alice, auth, name="a.txt", data=b"content-here")

        url = f"/chats/{chat['id']}/files/a.txt"

        # member via Authorization header
        ok = client.get(url, headers=auth(bob))
        assert ok.status_code == 200
        assert ok.content == b"content-here"

        # member via token query param (browser link style)
        assert client.get(url, params={"token": bob.token}).status_code == 200

        # no token at all
        assert client.get(url).status_code == 401
        # non-member
        assert client.get(url, headers=auth(outsider)).status_code == 403
        # missing file
        assert client.get(
            f"/chats/{chat['id']}/files/nope.txt", headers=auth(bob)
        ).status_code == 404


def test_upload_broadcasts_file_message(register_user, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_FILE_DIR", str(tmp_path / "chatfiles"))

    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat = client.post(
            "/chats", json={"name": "群", "members": [alice, bob]}, headers=auth(alice)
        ).json()

        with client.websocket_connect(f"/ws?token={bob.token}") as wb:
            resp = _upload(client, chat["id"], alice, auth, name="b.txt", data=b"x")
            event = wb.receive_json()
            assert event["type"] == "message"
            assert event["chat_id"] == chat["id"]
            assert event["sender_id"] == alice
            assert event["message_type"] == "file"
            assert event["content"] == resp.json()["content"]
