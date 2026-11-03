"""Test fixtures. Integration tests run against a real PostgreSQL database (``mma_test``); the schema
is rebuilt from the Alembic migration at session start. Media tests use the real local models
(downloaded once into ``MMA_MODEL_CACHE_DIR``)."""

import os
import tempfile
from collections.abc import Callable, Iterator

os.environ.setdefault("MMA_DATABASE_URL", "postgresql+psycopg://mma:mma@127.0.0.1:5437/mma_test")
os.environ["MMA_JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["MMA_ENV"] = "test"
os.environ["MMA_COOKIE_SECURE"] = "false"
os.environ["MMA_LOG_JSON"] = "false"
os.environ["MMA_LOG_LEVEL"] = "WARNING"
os.environ["MMA_LLM_PROVIDER"] = "local"  # tests never call a paid API
os.environ["MMA_FILE_DIR"] = tempfile.mkdtemp(prefix="mma-files-")
os.environ.pop("ANTHROPIC_API_KEY", None)

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.assistant import engine
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import get_engine, new_session
from app.main import app
from app.modules.auth.router import login_limiter
from app.modules.chat import router as chat_router

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-Battery-9"
TABLES = (
    "tool_requests, tasks, memories, messages, chunks, attachments, conversations, user_settings, jobs, "
    "audit_events, refresh_tokens, users"
)


def _alembic(direction: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    (command.downgrade if direction == "down" else command.upgrade)(cfg, "base" if direction == "down" else "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    _alembic("down")
    _alembic("up")
    yield


def reset_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    login_limiter.reset()
    chat_router._limiter.reset()
    engine.use_claude_client(None)


@pytest.fixture
def clean_db() -> Iterator[None]:
    reset_database()
    yield
    engine.use_claude_client(None)


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def create_user(email: str, role: str = "member", password: str = PASSWORD, active: bool = True) -> User:
    with new_session() as s:
        u = User(
            email=email,
            full_name=email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            is_active=active,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> Callable[..., dict[str, str]]:
    """``auth("alex")`` → headers for alex@example.com (a member, created on first use)."""
    made: dict[str, dict[str, str]] = {}

    def _auth(name: str, role: str = "member") -> dict[str, str]:
        if name not in made:
            create_user(f"{name}@example.com", role)
            made[name] = login(client, f"{name}@example.com")
        return made[name]

    return _auth


def drain_jobs() -> int:
    from app.jobs.worker import drain

    return drain()


def new_conversation(client: TestClient, headers: dict[str, str]) -> str:
    r = client.post("/api/v1/conversations", headers=headers, json={})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def upload(client: TestClient, headers: dict[str, str], conv: str, filename: str, data: bytes) -> dict:
    r = client.post(f"/api/v1/conversations/{conv}/attachments", headers=headers, files={"file": (filename, data)})
    assert r.status_code == 202, r.text
    drain_jobs()
    att = client.get(f"/api/v1/attachments/{r.json()['id']}", headers=headers).json()
    assert att["status"] == "ready", att
    return att


def say(client: TestClient, headers: dict[str, str], conv: str, text_: str) -> dict:
    r = client.post(f"/api/v1/conversations/{conv}/messages", headers=headers, json={"text": text_})
    assert r.status_code == 200, r.text
    return r.json()
