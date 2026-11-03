"""One conversational turn, end to end.

``respond`` stores the user's message, asks the provider for a reply, stores the reply (with its
citations and tool events) and turns every tool call that needs confirmation into a pending
``ToolRequest`` the user can approve or decline. ``decide`` executes or declines such a request.

Data-changing tool runs, approvals and declines are written to the audit trail. Message contents
are not — the audit records *what was done*, not what was said.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import tools
from app.assistant.base import Provider, Reply, Turn, describe
from app.assistant.local import LocalProvider
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.db.models import Attachment, Conversation, Message, ToolRequest
from app.modules import audit

_claude_client: Any | None = None  # tests inject a fake client here


def use_claude_client(client: Any | None) -> None:
    global _claude_client
    _claude_client = client


def provider() -> Provider:
    if get_settings().llm_mode == "claude" or _claude_client is not None:
        from app.assistant.claude import ClaudeProvider

        return ClaudeProvider(client=_claude_client)
    return LocalProvider()


def _title(text: str) -> str:
    t = " ".join(text.split())
    return t[:60] + ("…" if len(t) > 60 else "") or "New conversation"


def respond(
    db: Session,
    user_id: uuid.UUID,
    conversation: Conversation,
    text: str,
    *,
    input_mode: str = "text",
    attachment_ids: list[uuid.UUID] | None = None,
    transcript_meta: dict[str, Any] | None = None,
) -> Message:
    """Handle a user message and return the stored assistant reply (caller commits)."""
    history = list(
        db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at))
    )
    atts = list(
        db.scalars(
            select(Attachment)
            .where(Attachment.conversation_id == conversation.id, Attachment.status == "ready")
            .order_by(Attachment.created_at)
        )
    )
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=text,
        input_mode=input_mode,
        attachment_ids=[str(a) for a in attachment_ids or []],
        usage={"transcription": transcript_meta} if transcript_meta else {},
    )
    db.add(user_msg)
    if not history:
        conversation.title = _title(text)
    db.flush()

    started = time.perf_counter()
    turn = Turn(db, user_id, conversation.id, text, input_mode, atts, history)
    try:
        reply = provider().answer(turn)
    except Exception as exc:  # a model failure must not lose the user's message
        reply = Reply(
            f"Something went wrong while answering ({type(exc).__name__}). Please try again.", "error", "none"
        )
    latency = int((time.perf_counter() - started) * 1000)

    msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply.content,
        status=reply.status,
        provider=reply.provider,
        citations=reply.citations,
        usage=reply.usage,
        latency_ms=latency,
        tool_events=[],
    )
    db.add(msg)
    db.flush()
    events: list[dict[str, Any]] = []
    for call in reply.tool_calls:
        if call.status == "needs_permission":
            req = ToolRequest(
                user_id=user_id, conversation_id=conversation.id, message_id=msg.id, tool=call.name, args=call.args
            )
            db.add(req)
            db.flush()
            call.request_id = str(req.id)
        elif call.status == "executed" and tools.TOOLS[call.name].changes_data:
            audit.record(
                db,
                user_id,
                f"tool.{call.name}",
                "tool",
                call.name,
                conversation_id=str(conversation.id),
                confirmed=False,
                provider=reply.provider,
            )
        events.append(call.as_event())
    msg.tool_events = events
    conversation.updated_at = datetime.now(UTC)
    db.flush()
    return msg


def decide(db: Session, user_id: uuid.UUID, request_id: uuid.UUID, approve: bool) -> Message:
    """Approve (run) or decline a pending tool request; posts the outcome as an assistant message."""
    req = db.get(ToolRequest, request_id, with_for_update=True)
    if req is None or req.user_id != user_id:
        raise NotFound("Request not found")
    if req.status != "pending":
        raise Conflict(f"This request was already {req.status}")
    req.decided_at = datetime.now(UTC)
    if not approve:
        req.status = "denied"
        content, status = f"Okay, I won't {req.tool.replace('_', ' ')}.", "answered"
        audit.record(db, user_id, "tool.declined", "tool_request", str(req.id), tool=req.tool)
    else:
        try:
            with db.begin_nested():
                result = tools.execute(db, user_id, req.tool, req.args, confirmed=True)
        except PermissionError as exc:  # the tool was set to "deny" after the request was made
            req.status = "failed"
            req.result = {"error": str(exc)}
            content, status = f"I can't do that: {exc}.", "error"
        except (tools.ToolError, ValueError, KeyError, TypeError) as exc:
            req.status = "failed"
            req.result = {"error": str(exc)}
            content, status = f"That didn't work: {exc}.", "error"
        else:
            req.status = "executed"
            req.result = result
            content, status = describe(req.tool, result), "answered"
            audit.record(db, user_id, f"tool.{req.tool}", "tool_request", str(req.id), confirmed=True)
    msg = Message(
        conversation_id=req.conversation_id,
        role="assistant",
        content=content,
        status=status,
        provider="tool",
        tool_events=[
            {"name": req.tool, "args": req.args, "status": req.status, "request_id": str(req.id), "result": req.result}
        ],
    )
    db.add(msg)
    db.flush()
    return msg
