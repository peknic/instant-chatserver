from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .security import decode_token


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    user_id = decode_token(token)
    if user_id is None:
        return None
    return db.get(User, user_id)


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    user = user_from_token(db, authorization[7:].strip())
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user
