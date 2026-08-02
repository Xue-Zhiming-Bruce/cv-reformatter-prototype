import os
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(subject: str) -> str:
    secret = os.environ["JWT_SECRET"]
    minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    return jwt.encode({"sub": subject, "exp": expire}, secret, algorithm="HS256")


def decode_token(token: str) -> str:
    secret = os.environ["JWT_SECRET"]
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return str(payload["sub"])
