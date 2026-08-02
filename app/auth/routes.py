import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.schemas import LoginRequest, SignupRequest, UserOut
from app.auth.security import create_access_token, hash_password, verify_password
from app.db.models import Subscription, User
from app.db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE = "access_token"


def _set_auth_cookie(response: Response, token: str) -> None:
    max_age = int(os.getenv("JWT_EXPIRE_MINUTES", "10080")) * 60
    response.set_cookie(
        key=_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=max_age,
    )


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)) -> UserOut:
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()

    now = datetime.now(UTC)
    db.add(Subscription(
        user_id=user.id,
        membership_tier="free",
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cycle_conversion_count=0,
    ))
    db.commit()
    db.refresh(user)

    _set_auth_cookie(response, create_access_token(str(user.id)))
    return UserOut.model_validate(user)


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> UserOut:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    _set_auth_cookie(response, create_access_token(str(user.id)))
    return UserOut.model_validate(user)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(key=_COOKIE, httponly=True, samesite="lax")
    return {"detail": "Logged out."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
