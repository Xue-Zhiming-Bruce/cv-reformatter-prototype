"""
Auth endpoint tests.

Each test that sets cookies uses its own TestClient instance so cookie jars
never bleed between tests.  DB rows created during a test are cleaned up by
the _cleanup fixture via a direct SessionLocal connection.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Subscription, User
from app.db.session import SessionLocal
from app.main import app


# ── helpers ───────────────────────────────────────────────────────────────

def _email() -> str:
    return f"test_{uuid.uuid4().hex[:10]}@example.com"


def _signup(client: TestClient, email: str, password: str = "validpass123") -> "requests.Response":  # type: ignore[name-defined]
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def _login(client: TestClient, email: str, password: str = "validpass123") -> "requests.Response":  # type: ignore[name-defined]
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ── cleanup fixture ───────────────────────────────────────────────────────

@pytest.fixture()
def tracked_emails() -> list[str]:
    """Collect emails created during a test; delete matching users after."""
    emails: list[str] = []
    yield emails
    if emails:
        db: Session = SessionLocal()
        try:
            db.query(User).filter(User.email.in_(emails)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


# ── tests ─────────────────────────────────────────────────────────────────

def test_signup_creates_user_and_free_subscription(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as client:
        res = _signup(client, email)

    assert res.status_code == 201
    body = res.json()
    assert body["email"] == email
    assert "id" in body
    assert "password_hash" not in body
    assert "access_token" in res.cookies

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None

        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        assert sub is not None, "Subscription row must be created on signup"
        assert sub.membership_tier == "free"
        assert sub.status == "active"
        assert sub.cycle_conversion_count == 0
    finally:
        db.close()


def test_signup_duplicate_email_returns_409(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as client:
        _signup(client, email)
        res = _signup(client, email)

    assert res.status_code == 409


def test_login_wrong_password_returns_401(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as c1:
        _signup(c1, email)

    with TestClient(app) as c2:
        res = _login(c2, email, "wrongpassword!!")

    assert res.status_code == 401


def test_login_unknown_email_returns_401() -> None:
    with TestClient(app) as client:
        res = _login(client, "nobody_known@example.com")
    assert res.status_code == 401


def test_me_without_cookie_returns_401() -> None:
    with TestClient(app) as client:
        res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_me_with_valid_cookie_returns_user(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as client:
        signup_res = _signup(client, email)
        assert signup_res.status_code == 201

        me_res = client.get("/api/auth/me")

    assert me_res.status_code == 200
    assert me_res.json()["email"] == email


def test_login_sets_cookie_and_me_works(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as signup_client:
        _signup(signup_client, email)

    with TestClient(app) as client:
        login_res = _login(client, email)
        assert login_res.status_code == 200
        assert "access_token" in login_res.cookies

        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        assert me_res.json()["email"] == email


def test_no_response_body_contains_password_hash(tracked_emails: list[str]) -> None:
    email = _email()
    tracked_emails.append(email)

    with TestClient(app) as client:
        signup_res = _signup(client, email)
        login_res = _login(client, email)
        me_res = client.get("/api/auth/me")

    for res in [signup_res, login_res, me_res]:
        text = res.text
        assert "password_hash" not in text, f"password_hash leaked in {res.request.url}"
        assert "$2b$" not in text, f"bcrypt hash leaked in {res.request.url}"
