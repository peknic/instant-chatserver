import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    SendCodeRequest,
    TokenResponse,
    UserRead,
)
from ..auth.deps import get_current_user
from ..auth.email_whitelist import is_allowed_email, is_dotted_email, normalize_email
from ..auth.mailer import send_code
from ..auth.security import create_token, hash_password, verify_password
from ..auth.verifycode import verify_code

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
RESERVED_MSG = "邮箱或用户名不可用"


def _username_exists(db: Session, username: str) -> bool:
    return db.query(User).filter(User.username == username).first() is not None


def _email_taken(db: Session, email: str) -> bool:
    norm = normalize_email(email)
    for (stored,) in db.query(User.email).filter(User.email.isnot(None)).all():
        if stored == email or normalize_email(stored) == norm:
            return True
    return False


def _validate_email(email: str) -> str:
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    email = normalize_email(email)
    if not is_allowed_email(email) or is_dotted_email(email):
        raise HTTPException(status_code=400, detail="邮箱状态异常")
    return email


@router.post("/send_code")
def api_send_code(payload: SendCodeRequest, db: Session = Depends(get_db)):
    email = _validate_email(payload.email.strip())
    username = (payload.username or "").strip()
    if username and _username_exists(db, username):
        raise HTTPException(status_code=400, detail=RESERVED_MSG)
    if _email_taken(db, email):
        raise HTTPException(status_code=400, detail=RESERVED_MSG)

    ok, msg, _ = send_code(email)
    if not ok:
        raise HTTPException(status_code=429, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/register", response_model=TokenResponse, status_code=201)
def api_register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = _validate_email(payload.email.strip())
    username = payload.username.strip()
    if _username_exists(db, username):
        raise HTTPException(status_code=409, detail="用户名已被占用")
    if _email_taken(db, email):
        raise HTTPException(status_code=409, detail="邮箱已被占用")

    ok, msg = verify_code(email, payload.code)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    user = User(username=username, email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(token=create_token(user.id), user=UserRead.model_validate(user))


@router.post("/login", response_model=TokenResponse)
def api_login(payload: LoginRequest, db: Session = Depends(get_db)):
    account = payload.account.strip()
    user = None
    if "@" in account:
        email = normalize_email(account)
        for candidate in db.query(User).filter(User.email.isnot(None)).all():
            if normalize_email(candidate.email) == email:
                user = candidate
                break
    if user is None:
        user = db.query(User).filter(User.username == account).first()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return TokenResponse(token=create_token(user.id), user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
def api_me(user: User = Depends(get_current_user)):
    return user
