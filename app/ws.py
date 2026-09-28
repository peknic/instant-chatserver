import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .auth.deps import user_from_token
from .database import SessionLocal
from .models import Chat, Message

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        connections = self.active.get(user_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active.pop(user_id, None)

    def online_users(self) -> list[int]:
        return sorted(self.active)

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        for websocket in list(self.active.get(user_id, set())):
            try:
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(user_id, websocket)

    async def broadcast(self, user_ids: list[int], payload: dict) -> None:
        for user_id in set(user_ids):
            await self.send_to_user(user_id, payload)


manager = ConnectionManager()


def _serialize_message(message: Message, chat_id: int) -> dict:
    return {
        "type": "message",
        "id": message.id,
        "chat_id": chat_id,
        "sender_id": message.sender_id,
        "content": message.content,
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


async def _handle_payload(db: Session, websocket: WebSocket, user_id: int, data: dict) -> None:
    message_type = data.get("type", "message")

    if message_type == "ping":
        await websocket.send_json({"type": "pong"})
        return

    if message_type != "message":
        await websocket.send_json({"type": "error", "detail": f"unknown message type: {message_type}"})
        return

    chat_id = data.get("chat_id")
    content = data.get("content")

    if not isinstance(chat_id, int) or not content:
        await websocket.send_json({"type": "error", "detail": "chat_id and content are required"})
        return

    chat = db.get(Chat, chat_id)
    if chat is None:
        await websocket.send_json({"type": "error", "detail": "chat not found"})
        return

    members = list(chat.members or [])
    if user_id not in members:
        await websocket.send_json({"type": "error", "detail": "user is not a member of this chat"})
        return

    message = Message(
        chat_id=chat_id,
        sender_id=user_id,
        content=content,
        message_type=data.get("message_type") or "text",
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    await manager.broadcast(members, _serialize_message(message, chat_id))


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    db = SessionLocal()
    user = user_from_token(db, websocket.query_params.get("token"))
    if user is None:
        # 先 accept 再关闭，避免 uvicorn 记录成 HTTP 403 并让客户端误判为可重试。
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": "invalid or expired token"})
        await websocket.close(code=1008)
        db.close()
        return

    user_id = user.id
    await manager.connect(user_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "invalid json"})
                continue
            if not isinstance(data, dict):
                await websocket.send_json({"type": "error", "detail": "payload must be a json object"})
                continue
            await _handle_payload(db, websocket, user_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        db.close()


@router.websocket("/ws/{path:path}")
async def websocket_legacy_endpoint(websocket: WebSocket, path: str):
    # 兜底旧客户端地址（如 /ws/5）：先 accept 再以 1008 关闭，
    # 旧版 app.js 收到 1008 会停止自动重连，避免反复请求刷 403。
    await websocket.accept()
    await websocket.send_json(
        {"type": "error", "detail": "outdated client, please use /ws?token=<token>"}
    )
    await websocket.close(code=1008)
