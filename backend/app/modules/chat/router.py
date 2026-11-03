"""Conversations, attachments, messages (typed or spoken), spoken replies and tool confirmations."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Member
from app.assistant import engine
from app.core.config import get_settings
from app.core.errors import NotFound, TooManyRequests, UnprocessableData
from app.core.ratelimit import SlidingWindowLimiter
from app.db.models import Attachment, Conversation, Message, ToolRequest
from app.jobs import queue
from app.media import files, models, process
from app.modules import audit
from app.modules.chat.schemas import (
    AttachmentDetail,
    AttachmentOut,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    Exchange,
    MessageIn,
    MessageOut,
    ToolRequestOut,
)

router = APIRouter(tags=["chat"])
_limiter = SlidingWindowLimiter(limit=30)
MAX_VOICE_SECONDS = 120


def _conversation(db: DB, user: CurrentUser, conversation_id: uuid.UUID) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFound("Conversation not found")
    return conv


def _attachment(db: DB, user: CurrentUser, attachment_id: uuid.UUID) -> Attachment:
    att = db.get(Attachment, attachment_id)
    if att is None or att.user_id != user.id:
        raise NotFound("Attachment not found")
    return att


def _pending(db: DB, conversation_id: uuid.UUID) -> list[ToolRequest]:
    return list(
        db.scalars(
            select(ToolRequest)
            .where(ToolRequest.conversation_id == conversation_id, ToolRequest.status == "pending")
            .order_by(ToolRequest.created_at)
        )
    )


def _rate_limit(user: CurrentUser) -> None:
    if not _limiter.allow(str(user.id)):
        raise TooManyRequests("Too many messages — wait a moment and try again")


def _read_upload(upload: UploadFile, limit_mb: int) -> bytes:
    # Endpoints that take uploads are plain ``def``: FastAPI runs them in its thread pool, so OCR and
    # transcription never block the event loop.
    data = upload.file.read(limit_mb * 1024 * 1024 + 1)
    if len(data) > limit_mb * 1024 * 1024:
        raise UnprocessableData(f"Files are limited to {limit_mb} MB")
    if not data:
        raise UnprocessableData("The file is empty")
    return data


# ---------------------------------------------------------------------- conversations
@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: DB, user: CurrentUser) -> list[Conversation]:
    return list(
        db.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()))
    )


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, db: DB, user: Member) -> Conversation:
    conv = Conversation(user_id=user.id, title=body.title or "New conversation")
    db.add(conv)
    db.commit()
    return conv


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, db: DB, user: CurrentUser) -> dict[str, object]:
    conv = _conversation(db, user, conversation_id)
    messages = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))
    atts = db.scalars(select(Attachment).where(Attachment.conversation_id == conv.id).order_by(Attachment.created_at))
    return {
        **ConversationOut.model_validate(conv).model_dump(),
        "messages": list(messages),
        "attachments": list(atts),
        "pending_requests": _pending(db, conv.id),
    }


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def rename_conversation(conversation_id: uuid.UUID, body: ConversationUpdate, db: DB, user: Member) -> Conversation:
    conv = _conversation(db, user, conversation_id)
    conv.title = body.title
    db.commit()
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, db: DB, user: Member) -> Response:
    """Delete the conversation, its messages, attachments, extracted text and stored files."""
    conv = _conversation(db, user, conversation_id)
    keys = [a.file_key for a in db.scalars(select(Attachment).where(Attachment.conversation_id == conv.id))]
    keys += [k for k in db.scalars(select(Message.speech_key).where(Message.conversation_id == conv.id)) if k]
    db.delete(conv)
    audit.record(db, user.id, "conversation.deleted", "conversation", str(conversation_id), files=len(keys))
    db.commit()
    store = files.store()
    for k in keys:
        store.delete(k)
    return Response(status_code=204)


# ---------------------------------------------------------------------- attachments
@router.post("/conversations/{conversation_id}/attachments", response_model=AttachmentOut, status_code=202)
def upload_attachment(
    conversation_id: uuid.UUID, db: DB, user: Member, file: Annotated[UploadFile, File()]
) -> Attachment:
    """Upload an image, document or recording. Processing (OCR / parsing / transcription) runs in the
    worker; poll ``GET /attachments/{id}`` until ``status`` is ``ready`` or ``failed``."""
    conv = _conversation(db, user, conversation_id)
    data = _read_upload(file, get_settings().max_upload_mb)
    filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:255] or "upload"
    detected = files.detect(data, filename)
    key, sha = files.store().put(data)
    att = Attachment(
        user_id=user.id,
        conversation_id=conv.id,
        kind=detected.kind,
        filename=filename,
        mime=detected.mime,
        size_bytes=len(data),
        sha256=sha,
        file_key=key,
    )
    db.add(att)
    db.flush()
    queue.enqueue(db, "process_attachment", {"attachment_id": str(att.id)}, max_attempts=1)
    db.commit()
    return att


@router.get("/attachments/{attachment_id}", response_model=AttachmentDetail)
def get_attachment(attachment_id: uuid.UUID, db: DB, user: CurrentUser) -> Attachment:
    return _attachment(db, user, attachment_id)


@router.get("/attachments/{attachment_id}/file")
def download_attachment(attachment_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    att = _attachment(db, user, attachment_id)
    try:
        data = files.store().get(att.file_key)
    except OSError as exc:
        raise NotFound("The file is no longer stored") from exc
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", att.filename)
    disposition = "inline" if att.kind in ("image", "audio") else "attachment"
    return Response(
        data,
        media_type=att.mime,
        headers={"Content-Disposition": f'{disposition}; filename="{safe}"', "Cache-Control": "private, max-age=3600"},
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: uuid.UUID, db: DB, user: Member) -> Response:
    att = _attachment(db, user, attachment_id)
    key = att.file_key
    db.delete(att)
    audit.record(db, user.id, "attachment.deleted", "attachment", str(attachment_id))
    db.commit()
    files.store().delete(key)
    return Response(status_code=204)


# ---------------------------------------------------------------------- messages
@router.post("/conversations/{conversation_id}/messages", response_model=Exchange)
def send_message(conversation_id: uuid.UUID, body: MessageIn, db: DB, user: Member) -> dict[str, object]:
    _rate_limit(user)
    conv = _conversation(db, user, conversation_id)
    for aid in body.attachment_ids:
        if _attachment(db, user, aid).conversation_id != conv.id:
            raise UnprocessableData("Attachments must belong to this conversation")
    reply = engine.respond(db, user.id, conv, body.text.strip(), attachment_ids=body.attachment_ids)
    db.commit()
    return _exchange(db, conv, reply)


@router.post("/conversations/{conversation_id}/voice", response_model=Exchange)
def send_voice(
    conversation_id: uuid.UUID, db: DB, user: Member, audio: Annotated[UploadFile, File()]
) -> dict[str, object]:
    """A spoken message: the recording is transcribed with Whisper and answered like typed text."""
    _rate_limit(user)
    conv = _conversation(db, user, conversation_id)
    data = _read_upload(audio, 10)
    if files.detect(data, audio.filename or "voice.webm").kind != "audio":
        raise UnprocessableData("Voice messages must be audio")
    extracted = process.process_audio(data, max_seconds=MAX_VOICE_SECONDS)
    text = extracted.text.strip()
    if not text:
        raise UnprocessableData("I couldn't hear any speech in that recording")
    meta = {k: extracted.meta[k] for k in ("duration_seconds", "language", "language_probability")}
    reply = engine.respond(db, user.id, conv, text, input_mode="voice", transcript_meta=meta)
    db.commit()
    return _exchange(db, conv, reply)


def _exchange(db: DB, conv: Conversation, reply: Message) -> dict[str, object]:
    user_msg = db.scalar(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return {"user_message": user_msg, "reply": reply, "pending_requests": _pending(db, conv.id)}


def speech_text(markdown: str) -> str:
    """What to say for a reply: no citation markers, emphasis or list bullets."""
    t = re.sub(r"\s*\[\d{1,3}\]", "", markdown)
    t = re.sub(r"[*_`#>]+", "", t)
    t = re.sub(r"^\s*-\s+", "", t, flags=re.MULTILINE)
    return re.sub(r"\n{2,}", "\n", t).strip()[:3000]


@router.get("/messages/{message_id}/speech", responses={200: {"content": {"audio/wav": {}}}})
def message_speech(message_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    """The reply read aloud (Piper text-to-speech, WAV). Generated once, then cached."""
    msg = db.get(Message, message_id)
    if msg is None or msg.role != "assistant":
        raise NotFound("Message not found")
    _conversation(db, user, msg.conversation_id)
    store = files.store()
    if msg.speech_key:
        try:
            return Response(store.get(msg.speech_key), media_type="audio/wav")
        except OSError:
            pass
    text = speech_text(msg.content)
    if not text:
        raise UnprocessableData("Nothing to read aloud")
    wav = models.synthesize(text)
    msg.speech_key, _ = store.put(wav, ".wav")
    db.commit()
    return Response(wav, media_type="audio/wav")


# ---------------------------------------------------------------------- tool confirmations
@router.post("/tool-requests/{request_id}/approve", response_model=MessageOut)
def approve(request_id: uuid.UUID, db: DB, user: Member) -> Message:
    msg = engine.decide(db, user.id, request_id, approve=True)
    db.commit()
    return msg


@router.post("/tool-requests/{request_id}/decline", response_model=MessageOut)
def decline(request_id: uuid.UUID, db: DB, user: Member) -> Message:
    msg = engine.decide(db, user.id, request_id, approve=False)
    db.commit()
    return msg


@router.get("/tool-requests", response_model=list[ToolRequestOut])
def list_requests(db: DB, user: CurrentUser, status: str = "pending") -> list[ToolRequest]:
    stmt = select(ToolRequest).where(ToolRequest.user_id == user.id)
    if status != "all":
        stmt = stmt.where(ToolRequest.status == status)
    return list(db.scalars(stmt.order_by(ToolRequest.created_at.desc()).limit(200)))
