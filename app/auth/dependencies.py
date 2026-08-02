from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.db.models import User
from app.db.session import get_db

_401 = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not access_token:
        raise _401
    try:
        user_id = int(decode_token(access_token))
    except (JWTError, ValueError, KeyError):
        raise _401
    user = db.get(User, user_id)
    if user is None:
        raise _401
    return user
