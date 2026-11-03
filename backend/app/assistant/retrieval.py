"""Find the passages that answer a question — in two stages.

**1. Retrieve** chunks of the conversation's attachments and (if memory is on) the user's
remembered facts:

    score = cosine(query, passage)  +  0.15 × lexical overlap

Lexical overlap is the share of the query's distinctive terms (numbers, identifiers such as
``INV-2041``, non-stop words) found in the passage. Embeddings handle paraphrase; the lexical term
rescues exact identifiers that embeddings blur.

**2. Rerank** the sentences/lines inside the best chunks — singly and as pairs of neighbours — with
a cross-encoder that reads question and candidate together. The top candidate is the quoted answer,
and its score decides whether the files answer the question at all: below ``MMA_ANSWER_THRESHOLD``
the assistant says it didn't find it. Embedding similarity alone separates answerable from
unanswerable questions poorly (see the threshold sweeps in the evaluation report); the
cross-encoder separates them far better.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Attachment, Chunk, Memory, UserSettings
from app.media import models

LEXICAL_WEIGHT = 0.15
RERANK_PASSAGES = 4
PASSAGE_PENALTY = 10.0
STOP = set(
    """a an and are as at be by can could did do does for from had has have how i in is it its me my of on or our
    please say says said should so tell than that the their them then there these they this to was we were what when
    where which who whom whose why will with would you your file document image picture photo recording audio
    attached attachment show give find""".split()
)


@dataclass
class Passage:
    source: str  # chunk | memory
    text: str
    locator: str
    cosine: float
    score: float
    attachment_id: str | None = None
    filename: str | None = None
    ordinal: int = 0


@dataclass
class Answer:
    passage: Passage
    text: str  # the quoted sentence/line (or two neighbouring ones)
    score: float  # cross-encoder relevance (logit; higher = more relevant)


def terms(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_.$/:]*[A-Za-z0-9]|[A-Za-z0-9]", text.lower())
    return {w for w in words if w not in STOP and (len(w) > 2 or w.isdigit())}


def lexical(query_terms: set[str], text: str) -> float:
    if not query_terms:
        return 0.0
    hay = text.lower()
    return sum(1 for t in query_terms if t in hay) / len(query_terms)


def _cosines(query: list[float], vectors: list[list[float]]) -> np.ndarray:
    if not vectors:
        return np.zeros(0)
    m = np.asarray(vectors, dtype=np.float32)
    q = np.asarray(query, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    q /= np.linalg.norm(q) + 1e-9
    return m @ q


def memories(db: Session, user_id: uuid.UUID) -> list[Memory]:
    settings = db.get(UserSettings, user_id)
    if settings is not None and not settings.memory_enabled:
        return []
    return list(db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at)))


def search(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    query: str,
    *,
    k: int | None = None,
    include_memory: bool = True,
) -> list[Passage]:
    """Stage 1: rank chunks and memories for ``query`` (best first)."""
    k = k or get_settings().retrieval_top_k
    qv = models.embed_query(query)
    qt = terms(query)
    rows = db.execute(
        select(Chunk, Attachment.filename)
        .join(Attachment, Attachment.id == Chunk.attachment_id)
        .where(Attachment.conversation_id == conversation_id, Attachment.status == "ready")
    ).all()
    out: list[Passage] = []
    cos = _cosines(qv, [c.embedding for c, _ in rows])
    for (chunk, filename), c in zip(rows, cos, strict=True):
        score = float(c) + LEXICAL_WEIGHT * lexical(qt, chunk.text)
        out.append(
            Passage(
                "chunk", chunk.text, chunk.locator, float(c), score, str(chunk.attachment_id), filename, chunk.ordinal
            )
        )
    if include_memory:
        mems = memories(db, user_id)
        if mems:
            mcos = _cosines(qv, models.embed_passages([m.text for m in mems]))
            for m, c in zip(mems, mcos, strict=True):
                score = float(c) + LEXICAL_WEIGHT * lexical(qt, m.text)
                out.append(Passage("memory", m.text, "memory", float(c), score))
    out.sort(key=lambda p: p.score, reverse=True)
    return out[:k]


def split_units(text: str) -> list[str]:
    """Sentences, or lines for line-oriented text (receipts, forms, OCR output)."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", line)
        units.extend(p.strip() for p in parts if len(p.strip()) > 1)
    return units


def candidates(passage: Passage) -> list[str]:
    """Single sentences/lines and pairs of neighbours (an answer often spans a heading and its value)."""
    units = split_units(passage.text)
    return units + [f"{units[i]}\n{units[i + 1]}" for i in range(len(units) - 1)]


def best_answers(query: str, passages: list[Passage], n: int = 1) -> list[Answer]:
    """Stage 2: rerank candidate sentences inside the top passages with the cross-encoder.

    The final score is the cross-encoder logit minus ``PASSAGE_PENALTY`` × how far the candidate's
    passage trails the best passage in stage 1. The cross-encoder only sees one sentence, so without
    this a well-phrased line from an unrelated file ("Support line: 0800 …" on a kettle warranty) can
    beat the file the question is actually about ("What is the dentist's phone number?").
    """
    cands: list[tuple[Passage, str]] = [(p, c) for p in passages[:RERANK_PASSAGES] for c in candidates(p)]
    if not cands:
        return []
    top = passages[0].score
    scores = models.rerank(query, [c for _, c in cands])
    ranked = sorted(
        (Answer(p, c, s - PASSAGE_PENALTY * (top - p.score)) for (p, c), s in zip(cands, scores, strict=True)),
        key=lambda a: -a.score,
    )
    best = [ranked[0]]
    for a in ranked[1:]:
        if len(best) >= n:
            break
        if not any(a.text in b.text or b.text in a.text for b in best):
            best.append(a)
    return best
