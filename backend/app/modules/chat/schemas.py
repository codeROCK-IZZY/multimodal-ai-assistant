import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AttachmentOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    kind: str
    filename: str
    mime: str
    size_bytes: int
    status: str
    error: str | None
    meta: dict[str, Any]
    processor: str | None
    processing_ms: int | None
    created_at: datetime


class AttachmentDetail(AttachmentOut):
    text: str


class MessageOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    input_mode: str
    attachment_ids: list[str]
    citations: list[dict[str, Any]]
    tool_events: list[dict[str, Any]]
    status: str | None
    provider: str | None
    usage: dict[str, Any]
    latency_ms: int | None
    created_at: datetime


class ToolRequestOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    tool: str
    args: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    created_at: datetime


class ConversationOut(Out):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    attachments: list[AttachmentOut]
    pending_requests: list[ToolRequestOut]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class Exchange(BaseModel):
    user_message: MessageOut
    reply: MessageOut
    pending_requests: list[ToolRequestOut]
