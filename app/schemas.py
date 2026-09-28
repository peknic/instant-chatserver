from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    username: str = Field(..., max_length=64)
    avatar: str | None = Field(default=None, max_length=255)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None = None
    created_at: datetime


class SendCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    username: str | None = Field(default=None, max_length=64)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    code: str = Field(..., min_length=1, max_length=10)


class LoginRequest(BaseModel):
    account: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    token: str
    user: UserRead


class ChatBase(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    avatar: str | None = Field(default=None, max_length=255)
    members: list[int] = Field(default_factory=list)


class ChatCreate(ChatBase):
    pass


class ChatRead(ChatBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    adminmembers: list[int] = Field(default_factory=list)
    created_at: datetime


class ChatMembersAdd(BaseModel):
    members: list[int] = Field(..., min_length=1)


class MessageBase(BaseModel):
    chat_id: int
    sender_id: int
    content: str
    message_type: str = Field(default="text", max_length=32)


class MessageCreate(MessageBase):
    pass


class MessageRead(MessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class MessagePage(BaseModel):
    items: list[MessageRead]
    total: int
    limit: int
    offset: int
