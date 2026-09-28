from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user
from ..database import get_db
from ..models import Chat, Message, User
from ..schemas import (
    ChatCreate,
    ChatMembersAdd,
    ChatRead,
    MessagePage,
)
from ..ws import manager

router = APIRouter(prefix="/chats", tags=["chats"])


def _normalize_members(members: list[int]) -> list[int]:
    unique = list(dict.fromkeys(members))
    return sorted(unique)


def _validate_users(db: Session, member_ids: list[int]) -> None:
    if not member_ids:
        raise HTTPException(status_code=400, detail="members must not be empty")
    found = {u.id for u in db.query(User).filter(User.id.in_(member_ids)).all()}
    missing = [m for m in member_ids if m not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"users not found: {missing}")


def _get_chat(db: Session, chat_id: int) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _require_member(chat: Chat, user_id: int) -> None:
    if user_id not in (chat.members or []):
        raise HTTPException(status_code=403, detail="user is not a member of this chat")


def _require_admin(chat: Chat, user_id: int) -> None:
    if user_id not in (chat.adminmembers or []):
        raise HTTPException(status_code=403, detail="user is not an admin of this chat")


def _sync_admins(chat: Chat) -> None:
    members = list(chat.members or [])
    admins = [a for a in (chat.adminmembers or []) if a in members]
    if members and not admins:
        admins = [min(members)]
    chat.adminmembers = admins


@router.post("", response_model=ChatRead)
def create_chat(
    payload: ChatCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requester_id = current_user.id
    members = _normalize_members(payload.members)
    if len(members) < 2:
        raise HTTPException(status_code=400, detail="chat requires at least 2 members")
    _validate_users(db, members)
    if requester_id not in members:
        raise HTTPException(status_code=400, detail="requester must be a member of the chat")

    if len(members) == 2:
        for chat in db.query(Chat).all():
            if sorted(chat.members or []) == members:
                response.status_code = 200
                return chat

    chat = Chat(
        name=payload.name,
        avatar=payload.avatar,
        members=members,
        adminmembers=[requester_id],
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    response.status_code = 201
    return chat


@router.get("", response_model=list[ChatRead])
def list_chats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = current_user.id
    chats = db.query(Chat).order_by(Chat.id).all()
    return [chat for chat in chats if user_id in (chat.members or [])]


@router.get("/{chat_id}", response_model=ChatRead)
def get_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat = _get_chat(db, chat_id)
    _require_member(chat, current_user.id)
    return chat


@router.post("/{chat_id}/members", response_model=ChatRead)
def add_members(
    chat_id: int,
    payload: ChatMembersAdd,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat = _get_chat(db, chat_id)
    _require_member(chat, current_user.id)
    _validate_users(db, payload.members)
    chat.members = _normalize_members(list(chat.members or []) + payload.members)
    db.commit()
    db.refresh(chat)
    return chat


@router.post("/{chat_id}/invite", response_model=ChatRead)
async def invite_members(
    chat_id: int,
    payload: ChatMembersAdd,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requester_id = current_user.id
    chat = _get_chat(db, chat_id)
    _require_member(chat, requester_id)

    current = list(chat.members or [])
    candidates = _normalize_members(payload.members)
    _validate_users(db, candidates)

    new_members = [m for m in candidates if m not in current]
    if not new_members:
        return chat

    chat.members = _normalize_members(current + new_members)
    db.commit()
    db.refresh(chat)

    invited_chat = ChatRead.model_validate(chat).model_dump(mode="json")
    for user_id in new_members:
        await manager.send_to_user(
            user_id,
            {
                "type": "chat_invite",
                "chat_id": chat.id,
                "invited_by": requester_id,
                "chat": invited_chat,
            },
        )
    return chat


@router.delete("/{chat_id}/members/{user_id}", response_model=ChatRead)
async def remove_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requester_id = current_user.id
    chat = _get_chat(db, chat_id)
    _require_member(chat, requester_id)
    if user_id not in (chat.members or []):
        raise HTTPException(status_code=404, detail="user is not a member of this chat")
    if requester_id != user_id:
        _require_admin(chat, requester_id)

    chat.members = [m for m in (chat.members or []) if m != user_id]
    _sync_admins(chat)
    db.commit()
    db.refresh(chat)

    await manager.send_to_user(
        user_id,
        {
            "type": "chat_removed",
            "chat_id": chat.id,
            "removed_by": requester_id,
        },
    )
    return chat


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requester_id = current_user.id
    chat = _get_chat(db, chat_id)
    _require_member(chat, requester_id)
    _require_admin(chat, requester_id)

    members = list(chat.members or [])
    db.query(Message).filter(Message.chat_id == chat_id).delete(synchronize_session=False)
    db.delete(chat)
    db.commit()

    for user_id in members:
        await manager.send_to_user(
            user_id,
            {
                "type": "chat_deleted",
                "chat_id": chat_id,
                "deleted_by": requester_id,
            },
        )
    return {"chat_id": chat_id, "deleted": True}


@router.get("/{chat_id}/messages", response_model=MessagePage)
def list_messages(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    chat = _get_chat(db, chat_id)
    _require_member(chat, current_user.id)

    query = db.query(Message).filter(Message.chat_id == chat_id)
    total = query.count()
    items = query.order_by(Message.id).offset(offset).limit(limit).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}
