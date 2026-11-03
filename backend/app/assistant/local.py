"""The local provider: everything on this machine, no API key, no network.

It is deliberately an **extractive** assistant: it routes the message with rules
(``intents.recognise``), runs tools, and answers questions by quoting the most relevant line or
sentence from the user's files or memories, with a citation. It never generates facts, so when the
files don't contain an answer it says so instead of guessing. Open-ended reasoning, general
knowledge and describing photos need the Claude provider.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.assistant import intents, retrieval
from app.assistant.base import Reply, Turn, call_tool, confirmation_prompt, describe
from app.core.config import get_settings
from app.media import models

HELP = (
    "I can work with files you attach — images (I read the text in them), PDFs, Word documents and "
    "audio recordings — and answer questions about them with citations. I can also do arithmetic, "
    "convert units, keep a task list and remember things you ask me to. You can type or use the "
    "microphone, and I can read replies aloud."
)


def _citation(n: int, p: retrieval.Passage, quote: str, score: float) -> dict[str, Any]:
    return {
        "n": n,
        "source": p.source,
        "attachment_id": p.attachment_id,
        "filename": p.filename,
        "locator": p.locator,
        "quote": quote[:400],
        "score": round(score, 2),
    }


class LocalProvider:
    name = "local"

    def answer(self, turn: Turn) -> Reply:
        intent = intents.recognise(turn.text)
        if intent.kind == "help":
            return Reply(HELP, "answered", self.name)
        if intent.kind == "tool" and intent.tool:
            return self._tool(turn, intent.tool, intent.args)
        if intent.kind == "list_memories":
            mems = retrieval.memories(turn.db, turn.user_id)
            if not mems:
                return Reply(
                    'I don\'t remember anything about you yet. Say "remember that …" to tell me something.',
                    "answered",
                    self.name,
                )
            return Reply("Here's what I remember:\n" + "\n".join(f"- {m.text}" for m in mems), "answered", self.name)
        if not turn.attachments and intent.kind in ("summarize", "read", "describe"):
            return Reply(
                "Attach a file first — an image, a document or a recording — and ask again.", "not_found", self.name
            )
        if intent.kind == "summarize":
            return self._summarize(turn)
        if intent.kind == "read":
            return self._read(turn)
        if intent.kind == "describe":
            return self._describe(turn)
        return self._question(turn)

    # ------------------------------------------------------------------ tools
    def _tool(self, turn: Turn, name: str, args: dict[str, Any]) -> Reply:
        call = call_tool(turn, name, args)  # intents come only from the user's own words: trusted
        if call.status == "executed":
            return Reply(describe(name, call.result or {}), "answered", self.name, tool_calls=[call])
        if call.status == "needs_permission":
            return Reply(confirmation_prompt(call), "needs_permission", self.name, tool_calls=[call])
        if call.status == "denied":
            return Reply(
                f"I can't do that: {call.error}. You can change this in Settings.",
                "answered",
                self.name,
                tool_calls=[call],
            )
        return Reply(f"That didn't work: {call.error}.", "error", self.name, tool_calls=[call])

    # ------------------------------------------------------------------ files
    def _latest(self, turn: Turn) -> Any:
        """The file the message is about: named by filename or kind, else the best search match, else the newest."""
        atts = turn.attachments
        if len(atts) == 1:
            return atts[0]
        words = retrieval.terms(turn.text)
        for att in reversed(atts):
            stem = att.filename.rsplit(".", 1)[0].lower()
            if any(w in stem.replace("-", " ").replace("_", " ").split() for w in words if len(w) > 3):
                return att
        low = turn.text.lower()
        for kind, cues in (
            ("image", ("image", "picture", "photo", "screenshot", "scan")),
            ("audio", ("recording", "audio", "memo", "voice", "call")),
            ("document", ("document", "pdf", "doc", "notes", "policy", "file")),
        ):
            matches = [a for a in atts if a.kind == kind]
            if matches and any(c in low for c in cues):
                return matches[-1]
        if words:
            hits = retrieval.search(turn.db, turn.user_id, turn.conversation_id, turn.text, k=1, include_memory=False)
            if hits:
                return next((a for a in atts if str(a.id) == hits[0].attachment_id), atts[-1])
        return atts[-1]

    def _summarize(self, turn: Turn) -> Reply:
        """Extractive summary: the sentences closest to the document's overall meaning, in original order."""
        att = self._latest(turn)
        units = retrieval.split_units(att.text)
        if not units:
            return Reply(f"I couldn't find any text in **{att.filename}** to summarise.", "not_found", self.name)
        if len(units) <= 4:
            picked = list(range(len(units)))
        else:
            vecs = np.asarray(models.embed_passages(units), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
            centre = vecs.mean(axis=0)
            scores = vecs @ centre + 0.02 * (np.arange(len(units)) == 0)  # slight preference for the opening line
            picked = sorted(np.argsort(-scores)[: min(5, max(3, len(units) // 8))].tolist())
        kind = {"audio": "recording", "image": "image", "document": "document"}[att.kind]
        body = "\n".join(f"- {units[i]}" for i in picked)
        extra = ""
        if att.kind == "audio" and att.meta.get("duration_seconds"):
            extra = f" ({att.meta['duration_seconds']:.0f} s)"
        elif att.kind == "document" and att.meta.get("pages"):
            extra = f" ({att.meta['pages']} pages)"
        cites = [
            {
                "n": 1,
                "source": "chunk",
                "attachment_id": str(att.id),
                "filename": att.filename,
                "locator": "whole file",
                "quote": "",
                "score": 1.0,
            }
        ]
        return Reply(f"Key points from the {kind} **{att.filename}**{extra} [1]:\n{body}", "answered", self.name, cites)

    def _read(self, turn: Turn) -> Reply:
        att = self._latest(turn)
        if not att.text.strip():
            return Reply(f"I didn't find any text in **{att.filename}**.", "not_found", self.name)
        label = "Transcript" if att.kind == "audio" else "Text"
        text = att.text if len(att.text) < 4000 else att.text[:4000] + "\n…"
        cites = [
            {
                "n": 1,
                "source": "chunk",
                "attachment_id": str(att.id),
                "filename": att.filename,
                "locator": "whole file",
                "quote": "",
                "score": 1.0,
            }
        ]
        return Reply(f"{label} of **{att.filename}** [1]:\n\n{text}", "answered", self.name, cites)

    def _describe(self, turn: Turn) -> Reply:
        images = [a for a in turn.attachments if a.kind == "image"]
        if not images:
            return self._summarize(turn)
        att = images[-1]
        m = att.meta
        parts = [
            f"**{att.filename}** is a {m.get('width')}×{m.get('height')} image, mostly "
            f"{' and '.join(m.get('dominant_colours', [])[:2]) or 'mixed colours'}."
        ]
        if att.text.strip():
            lines = att.text.split("\n")
            parts.append(
                f"It contains {len(lines)} line{'s' if len(lines) != 1 else ''} of text, starting with "
                f'"{lines[0][:120]}". Ask "what does it say" for all of it.'
            )
        else:
            parts.append("I didn't find any text in it.")
        parts.append("Recognising objects and scenes needs the Claude provider; locally I can read text in images.")
        return Reply(" ".join(parts), "answered", self.name)

    def _question(self, turn: Turn) -> Reply:
        passages = retrieval.search(turn.db, turn.user_id, turn.conversation_id, turn.text)
        answers = retrieval.best_answers(turn.text, passages)
        if not answers or answers[0].score < get_settings().answer_threshold:
            if turn.attachments:
                return Reply(
                    "I couldn't find that in your files. Try rephrasing, or ask me to summarise the file.",
                    "not_found",
                    self.name,
                )
            return Reply(
                "I can only answer from your files and what you've asked me to remember. Attach a file, "
                "or connect the Claude provider for general questions.",
                "not_found",
                self.name,
            )
        a = answers[0]
        p = a.passage
        quote = a.text.replace("\n", " / ")
        citations = [_citation(1, p, a.text, a.score)]
        if p.source == "memory":
            content = f"You told me: *{quote}* [1]"
        else:
            content = f'From **{p.filename}** ({p.locator}): "{quote}" [1]'
        return Reply(content, "answered", self.name, citations)
