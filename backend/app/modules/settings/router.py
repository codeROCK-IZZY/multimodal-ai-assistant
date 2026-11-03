"""Per-user settings: memory on/off, spoken replies, and a permission for every tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Member
from app.assistant import tools
from app.core.errors import UnprocessableData
from app.db.models import Attachment, Conversation, Memory, Message, Task, User, UserSettings
from app.modules import audit

router = APIRouter(tags=["settings"])

Permission = Literal["allow", "ask", "deny"]


class SettingsOut(BaseModel):
    memory_enabled: bool
    speak_replies: bool
    tool_permissions: dict[str, Permission]


class SettingsUpdate(BaseModel):
    memory_enabled: bool | None = None
    speak_replies: bool | None = None
    tool_permissions: dict[str, Permission] | None = None


def _get(db: DB, user: User) -> UserSettings:
    s = db.get(UserSettings, user.id)
    if s is None:
        s = UserSettings(user_id=user.id, memory_enabled=True, speak_replies=False, tool_permissions={})
        db.add(s)
        db.flush()
    return s


def _out(db: DB, user: User, s: UserSettings) -> dict[str, Any]:
    return {
        "memory_enabled": s.memory_enabled,
        "speak_replies": s.speak_replies,
        "tool_permissions": tools.permissions(db, user.id),
    }


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: DB, user: CurrentUser) -> dict[str, Any]:
    s = _get(db, user)
    db.commit()
    return _out(db, user, s)


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: SettingsUpdate, db: DB, user: Member) -> dict[str, Any]:
    s = _get(db, user)
    changes: dict[str, Any] = {}
    if body.memory_enabled is not None and body.memory_enabled != s.memory_enabled:
        s.memory_enabled = changes["memory_enabled"] = body.memory_enabled
    if body.speak_replies is not None:
        s.speak_replies = body.speak_replies
    if body.tool_permissions is not None:
        unknown = set(body.tool_permissions) - set(tools.TOOLS)
        if unknown:
            raise UnprocessableData(f"Unknown tools: {', '.join(sorted(unknown))}")
        current = tools.permissions(db, user.id)
        diff = {k: v for k, v in body.tool_permissions.items() if current[k] != v}
        s.tool_permissions = {**s.tool_permissions, **body.tool_permissions}
        if diff:
            changes["tool_permissions"] = diff
    s.updated_at = datetime.now(UTC)
    if changes:
        audit.record(db, user.id, "settings.updated", "settings", str(user.id), **changes)
    db.commit()
    return _out(db, user, s)


@router.get("/tools")
def tool_catalogue(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "default": t.default, "changes_data": t.changes_data}
        for t in tools.TOOLS.values()
    ]


@router.get("/me/export")
def export_my_data(db: DB, user: CurrentUser) -> dict[str, Any]:
    """Everything stored about the signed-in user, as JSON (files are listed, not inlined)."""
    conversations = []
    for c in db.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.created_at)):
        msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at))
        conversations.append(
            {
                "id": str(c.id),
                "title": c.title,
                "created_at": c.created_at.isoformat(),
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "input_mode": m.input_mode,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in msgs
                ],
            }
        )
    atts = db.scalars(select(Attachment).where(Attachment.user_id == user.id))
    tasks = db.scalars(select(Task).where(Task.user_id == user.id))
    return {
        "user": {"email": user.email, "full_name": user.full_name, "role": user.role},
        "settings": _out(db, user, _get(db, user)),
        "memories": [m.text for m in db.scalars(select(Memory).where(Memory.user_id == user.id))],
        "tasks": [{"title": t.title, "due": t.due.isoformat() if t.due else None, "done": t.done} for t in tasks],
        "attachments": [
            {"filename": a.filename, "kind": a.kind, "size_bytes": a.size_bytes, "sha256": a.sha256} for a in atts
        ],
        "conversations": conversations,
    }
