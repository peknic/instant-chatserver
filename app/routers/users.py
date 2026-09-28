from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user
from ..database import get_db
from ..models import User
from ..schemas import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user
