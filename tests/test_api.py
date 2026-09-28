from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Message


def test_user_get_and_not_found(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")

        resp = client.get(f"/users/{alice}", headers=auth(alice))
        assert resp.status_code == 200
        assert resp.json()["username"] == "alice"
        assert resp.json()["email"] == "alice@gmail.com"

        assert client.get("/users/999999", headers=auth(alice)).status_code == 404


def test_user_endpoints_require_token(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")

        assert client.get(f"/users/{alice}").status_code == 401
        assert client.get("/chats").status_code == 401
        assert client.get("/chats/1/messages").status_code == 401
        assert (
            client.get("/chats", headers={"Authorization": "Bearer garbage"}).status_code == 401
        )


def test_two_person_chat_is_not_duplicated(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")

        first = client.post("/chats", json={"members": [a, b]}, headers=auth(a))
        assert first.status_code == 201, first.text
        assert first.json()["adminmembers"] == [a]
        second = client.post("/chats", json={"members": [b, a]}, headers=auth(b))
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["members"] == [a, b]
        assert second.json()["adminmembers"] == [a]

        listed = client.get("/chats", headers=auth(a)).json()
        assert [c["id"] for c in listed] == [first.json()["id"]]


def test_group_chat_members_and_access_control(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        c = register_user(client, "carol")
        outsider = register_user(client, "dave")

        chat = client.post("/chats", json={"members": [a, b, c]}, headers=auth(a)).json()
        assert chat["members"] == [a, b, c]
        assert chat["adminmembers"] == [a]

        # non-member cannot read
        assert client.get(f"/chats/{chat['id']}", headers=auth(outsider)).status_code == 403

        # add then remove a member
        added = client.post(
            f"/chats/{chat['id']}/members",
            json={"members": [outsider]},
            headers=auth(a),
        )
        assert added.status_code == 200
        assert outsider in added.json()["members"]

        removed = client.delete(
            f"/chats/{chat['id']}/members/{outsider}",
            headers=auth(a),
        )
        assert removed.status_code == 200
        assert outsider not in removed.json()["members"]

        # non-member cannot manage members
        assert (
            client.post(
                f"/chats/{chat['id']}/members",
                json={"members": [outsider]},
                headers=auth(outsider),
            ).status_code
            == 403
        )


def test_invite_requires_membership_and_is_idempotent(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        c = register_user(client, "carol")
        outsider = register_user(client, "dave")

        chat = client.post("/chats", json={"members": [a, b]}, headers=auth(a)).json()
        chat_id = chat["id"]

        # non-member cannot invite
        denied = client.post(
            f"/chats/{chat_id}/invite",
            json={"members": [c]},
            headers=auth(outsider),
        )
        assert denied.status_code == 403

        # member can invite
        invited = client.post(
            f"/chats/{chat_id}/invite",
            json={"members": [c]},
            headers=auth(a),
        )
        assert invited.status_code == 200, invited.text
        assert c in invited.json()["members"]

        # invited user can now read the chat
        assert client.get(f"/chats/{chat_id}", headers=auth(c)).status_code == 200

        # inviting an existing member is idempotent
        again = client.post(
            f"/chats/{chat_id}/invite",
            json={"members": [c]},
            headers=auth(b),
        )
        assert again.status_code == 200
        assert again.json()["members"].count(c) == 1

        # unknown user is rejected
        missing = client.post(
            f"/chats/{chat_id}/invite",
            json={"members": [999999]},
            headers=auth(a),
        )
        assert missing.status_code == 400


def test_remove_member_requires_membership_and_returns_404(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        c = register_user(client, "carol")
        outsider = register_user(client, "dave")

        chat = client.post("/chats", json={"members": [a, b, c]}, headers=auth(a)).json()
        chat_id = chat["id"]

        # non-member cannot remove
        denied = client.delete(
            f"/chats/{chat_id}/members/{c}",
            headers=auth(outsider),
        )
        assert denied.status_code == 403

        # plain member (not admin) cannot remove another member
        assert (
            client.delete(
                f"/chats/{chat_id}/members/{c}",
                headers=auth(b),
            ).status_code
            == 403
        )

        # admin can remove another member
        removed = client.delete(
            f"/chats/{chat_id}/members/{c}",
            headers=auth(a),
        )
        assert removed.status_code == 200, removed.text
        assert c not in removed.json()["members"]
        assert client.get(f"/chats/{chat_id}", headers=auth(c)).status_code == 403

        # removing a non-member is 404, including the already removed user
        assert (
            client.delete(
                f"/chats/{chat_id}/members/{c}",
                headers=auth(a),
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/chats/{chat_id}/members/999999",
                headers=auth(a),
            ).status_code
            == 404
        )


def test_delete_chat_clears_chat_and_messages(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        outsider = register_user(client, "dave")
        chat = client.post("/chats", json={"members": [a, b]}, headers=auth(a)).json()
        chat_id = chat["id"]

        with client.websocket_connect(f"/ws?token={a.token}") as ws:
            for i in range(3):
                ws.send_json({"type": "message", "chat_id": chat_id, "content": f"msg-{i}"})
                ws.receive_json()

        # non-member cannot delete
        assert client.delete(f"/chats/{chat_id}", headers=auth(outsider)).status_code == 403

        # plain member (not admin) cannot delete either
        assert client.delete(f"/chats/{chat_id}", headers=auth(b)).status_code == 403

        # admin deletes the chat
        resp = client.delete(f"/chats/{chat_id}", headers=auth(a))
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] is True

        # chat entry is gone from the list and direct read
        assert client.get("/chats", headers=auth(b)).json() == []
        assert client.get(f"/chats/{chat_id}", headers=auth(b)).status_code == 404
        assert client.get(f"/chats/{chat_id}/messages", headers=auth(b)).status_code == 404

        # all messages of this chat are gone
        with SessionLocal() as db:
            assert db.query(Message).filter(Message.chat_id == chat_id).count() == 0

        # deleting again -> 404
        assert client.delete(f"/chats/{chat_id}", headers=auth(a)).status_code == 404


def test_messages_pagination_and_access_control(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        outsider = register_user(client, "dave")
        chat = client.post("/chats", json={"members": [a, b]}, headers=auth(a)).json()
        chat_id = chat["id"]

        # seed messages through the websocket-free path: send via a helper request is
        # not exposed over HTTP, so use the websocket to persist messages.
        with client.websocket_connect(f"/ws?token={a.token}") as ws:
            for i in range(5):
                ws.send_json({"type": "message", "chat_id": chat_id, "content": f"msg-{i}"})
                ws.receive_json()

        page1 = client.get(
            f"/chats/{chat_id}/messages",
            params={"limit": 2, "offset": 0},
            headers=auth(b),
        )
        assert page1.status_code == 200
        body = page1.json()
        assert body["total"] == 5
        assert [m["content"] for m in body["items"]] == ["msg-0", "msg-1"]

        page3 = client.get(
            f"/chats/{chat_id}/messages",
            params={"limit": 2, "offset": 4},
            headers=auth(b),
        )
        assert [m["content"] for m in page3.json()["items"]] == ["msg-4"]

        assert (
            client.get(f"/chats/{chat_id}/messages", headers=auth(outsider)).status_code == 403
        )


def test_create_chat_requires_requester_membership(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        outsider = register_user(client, "dave")

        resp = client.post("/chats", json={"members": [a, b]}, headers=auth(outsider))
        assert resp.status_code == 400


def test_last_admin_leaving_promotes_next_member(register_user, auth):
    with TestClient(app) as client:
        a = register_user(client, "alice")
        b = register_user(client, "bob")
        c = register_user(client, "carol")
        chat = client.post("/chats", json={"members": [a, b, c]}, headers=auth(a)).json()
        chat_id = chat["id"]
        assert chat["adminmembers"] == [a]

        # a plain member can leave on their own
        left = client.delete(f"/chats/{chat_id}/members/{b}", headers=auth(b))
        assert left.status_code == 200
        assert b not in left.json()["members"]
        assert left.json()["adminmembers"] == [a]

        # the only admin leaves -> the next member is promoted
        out = client.delete(f"/chats/{chat_id}/members/{a}", headers=auth(a))
        assert out.status_code == 200
        assert a not in out.json()["members"]
        assert out.json()["adminmembers"] == [c]

        # the promoted admin can now delete the chat
        assert client.delete(f"/chats/{chat_id}", headers=auth(c)).status_code == 200
