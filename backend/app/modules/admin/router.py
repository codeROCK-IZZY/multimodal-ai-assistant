from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, Admin, CurrentUser
from app.core.config import get_settings
from app.db.models import Attachment, AuditEvent, Job, Message, ToolRequest, User
from app.modules import audit

router = APIRouter(tags=["admin"])


@router.get("/system/info")
def system_info(_: CurrentUser) -> dict[str, Any]:
    """What the UI needs to label AI output honestly (which provider is active, where data goes)."""
    s = get_settings()
    return {
        "llm_mode": s.llm_mode,
        "llm_model": s.claude_model if s.llm_mode == "claude" else None,
        "data_leaves_server": s.llm_mode == "claude",
        "speech_to_text": f"whisper {s.stt_model}",
        "text_to_speech": f"piper {s.tts_voice}",
        "ocr": "rapidocr (PP-OCR)",
        "embeddings": s.embedding_model,
        "max_upload_mb": s.max_upload_mb,
        "max_audio_seconds": s.max_audio_seconds,
    }


def _counts(db: DB, column: Any, *where: Any) -> dict[str, int]:
    return {str(k): int(v) for k, v in db.execute(select(column, func.count()).where(*where).group_by(column)).all()}


@router.get("/admin/stats")
def stats(db: DB, _: Admin) -> dict[str, Any]:
    latency = db.execute(
        select(
            func.percentile_cont(0.5).within_group(Message.latency_ms),
            func.percentile_cont(0.95).within_group(Message.latency_ms),
        ).where(Message.role == "assistant", Message.latency_ms.is_not(None))
    ).one()
    processing = db.execute(
        select(Attachment.kind, func.count(), func.avg(Attachment.processing_ms))
        .where(Attachment.status == "ready")
        .group_by(Attachment.kind)
    ).all()
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "replies_by_status": _counts(db, Message.status, Message.role == "assistant"),
        "messages_by_input": _counts(db, Message.input_mode, Message.role == "user"),
        "attachments_by_status": _counts(db, Attachment.status),
        "processing_ms_by_kind": {k: {"count": n, "avg_ms": round(float(a or 0))} for k, n, a in processing},
        "tool_requests_by_status": _counts(db, ToolRequest.status),
        "reply_latency_ms": {"p50": latency[0], "p95": latency[1]},
        "audit_events": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
    }


@router.get("/admin/jobs")
def jobs(db: DB, _: Admin, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500)))
    return [
        {
            "id": j.id,
            "type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "payload": j.payload,
            "result": j.result,
            "last_error": (j.last_error or "")[:500] or None,
            "created_at": j.created_at.isoformat(),
        }
        for j in rows
    ]


@router.get("/admin/audit")
def audit_events(db: DB, _: Admin, limit: int = 100) -> dict[str, Any]:
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 500)))
    emails = {f"user:{u.id}": u.email for u in db.scalars(select(User))}
    return {
        "chain": audit.verify_chain(db),
        "events": [
            {
                "id": e.id,
                "ts": e.ts.isoformat(),
                "actor": emails.get(e.actor, e.actor),
                "action": e.action,
                "entity": e.entity,
                "entity_id": e.entity_id,
                "data": e.data,
                "hash": e.hash[:12],
            }
            for e in rows
        ],
    }
