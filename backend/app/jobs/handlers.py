"""Job handlers run by the worker."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableData
from app.core.logging import log_extra
from app.db.models import Attachment, Chunk
from app.media import models, process
from app.media.files import store

logger = logging.getLogger(__name__)


def process_attachment(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Extract text (OCR / document parsing / transcription), chunk it and embed the chunks.

    Processing is deterministic, so failures are recorded on the attachment rather than retried.
    """
    att = db.get(Attachment, uuid.UUID(payload["attachment_id"]))
    if att is None:
        return {"skipped": "attachment deleted"}
    att.status = "processing"
    db.commit()
    started = time.perf_counter()
    try:
        extracted = process.process(att.kind, store().get(att.file_key), att.mime)
        chunks = [(loc, t) for loc, t in extracted.chunks if t.strip()]
        vectors = models.embed_passages([t for _, t in chunks]) if chunks else []
    except UnprocessableData as exc:
        att.status, att.error = "failed", exc.detail
        return {"status": "failed", "error": exc.detail}
    except Exception as exc:
        logger.exception("attachment processing failed", extra=log_extra(attachment_id=str(att.id)))
        att.status, att.error = "failed", f"Processing failed ({type(exc).__name__})"
        return {"status": "failed", "error": type(exc).__name__}
    db.execute(delete(Chunk).where(Chunk.attachment_id == att.id))
    for i, ((loc, t), v) in enumerate(zip(chunks, vectors, strict=True)):
        db.add(Chunk(attachment_id=att.id, ordinal=i, locator=loc[:60], text=t, embedding=v))
    att.text = extracted.text
    att.meta = extracted.meta
    att.processor = extracted.processor
    att.processing_ms = int((time.perf_counter() - started) * 1000)
    att.status = "ready"
    att.error = None
    return {"status": "ready", "chunks": len(chunks), "ms": att.processing_ms}


HANDLERS: dict[str, Callable[[Session, dict[str, Any]], dict[str, Any] | None]] = {
    "process_attachment": process_attachment,
}
