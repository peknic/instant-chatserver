import time

from fastapi.testclient import TestClient

from app.main import app
from app.ws import manager


def test_two_person_chat_broadcast(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={alice.token}") as wa, client.websocket_connect(
            f"/ws?token={bob.token}"
        ) as wb:
            wa.send_json({"type": "message", "chat_id": chat_id, "content": "你好", "message_type": "text"})

            received_a = wa.receive_json()
            received_b = wb.receive_json()

            assert received_a["type"] == "message"
            assert received_a["content"] == "你好"
            assert received_a["chat_id"] == chat_id
            assert received_a["sender_id"] == alice
            assert received_b["id"] == received_a["id"]

            wb.send_json({"type": "message", "chat_id": chat_id, "content": "嗨"})
            assert wa.receive_json()["content"] == "嗨"
            assert wb.receive_json()["sender_id"] == bob


def test_group_chat_broadcast_to_all_members(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        carol = register_user(client, "carol")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob, carol]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={alice.token}") as wa, client.websocket_connect(
            f"/ws?token={bob.token}"
        ) as wb, client.websocket_connect(f"/ws?token={carol.token}") as wc:
            wc.send_json({"type": "message", "chat_id": chat_id, "content": "大家好"})

            for ws in (wa, wb, wc):
                message = ws.receive_json()
                assert message["content"] == "大家好"
                assert message["sender_id"] == carol


def test_invite_notifies_invited_user_over_websocket(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        carol = register_user(client, "carol")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={carol.token}") as wc:
            resp = client.post(
                f"/chats/{chat_id}/invite",
                json={"members": [carol]},
                headers=auth(alice),
            )
            assert resp.status_code == 200, resp.text

            event = wc.receive_json()
            assert event["type"] == "chat_invite"
            assert event["chat_id"] == chat_id
            assert event["invited_by"] == alice
            assert carol in event["chat"]["members"]


def test_remove_notifies_removed_user_over_websocket(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={bob.token}") as wb:
            resp = client.delete(
                f"/chats/{chat_id}/members/{bob}",
                headers=auth(alice),
            )
            assert resp.status_code == 200, resp.text

            event = wb.receive_json()
            assert event["type"] == "chat_removed"
            assert event["chat_id"] == chat_id
            assert event["removed_by"] == alice


def test_delete_notifies_members_over_websocket(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={bob.token}") as wb:
            resp = client.delete(f"/chats/{chat_id}", headers=auth(alice))
            assert resp.status_code == 200, resp.text

            event = wb.receive_json()
            assert event["type"] == "chat_deleted"
            assert event["chat_id"] == chat_id
            assert event["deleted_by"] == alice


def test_non_member_cannot_send(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")
        bob = register_user(client, "bob")
        outsider = register_user(client, "dave")
        chat_id = client.post(
            "/chats", json={"members": [alice, bob]}, headers=auth(alice)
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={outsider.token}") as ws:
            ws.send_json({"type": "message", "chat_id": chat_id, "content": "hi"})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert "member" in error["detail"]


def test_websocket_rejects_bad_token(register_user, auth):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?token=garbage") as ws:
            error = ws.receive_json()
            assert error["type"] == "error"
            assert "token" in error["detail"]


def test_disconnect_cleans_up_connection(register_user, auth):
    with TestClient(app) as client:
        alice = register_user(client, "alice")

        with client.websocket_connect(f"/ws?token={alice.token}") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
            assert manager.online_users() == [alice]

        time.sleep(0.05)
        assert manager.online_users() == []
