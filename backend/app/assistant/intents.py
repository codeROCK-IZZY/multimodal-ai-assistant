"""Offline intent recognition for the local provider.

Rules over the **user's own message** (typed, or transcribed from their voice) — never over
attachment text. That is what makes the local provider immune to prompt injection inside files:
nothing in an uploaded document can become an instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.assistant.tools import UNITS

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_UNIT_RE = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))


@dataclass
class Intent:
    kind: str  # tool | summarize | read | describe | list_memories | help | question
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


def parse_due(text: str, today: date) -> tuple[str, date | None]:
    """Strip a due-date phrase from the end of a task. Returns (remaining text, date)."""
    t = text.strip().rstrip(".!")
    patterns: list[tuple[str, Any]] = [
        (r"\s+(?:by|on|due)?\s*(\d{4}-\d{2}-\d{2})$", lambda m: date.fromisoformat(m.group(1))),
        (r"\s+(?:by\s+|on\s+|due\s+)?today$", lambda m: today),
        (r"\s+(?:by\s+|on\s+|due\s+)?tomorrow$", lambda m: today + timedelta(days=1)),
        (r"\s+in\s+(\d{1,3})\s+days?$", lambda m: today + timedelta(days=int(m.group(1)))),
        (r"\s+next\s+week$", lambda m: today + timedelta(days=7)),
        (
            r"\s+(?:by\s+|on\s+|due\s+|this\s+|next\s+)?(" + "|".join(WEEKDAYS) + r")$",
            lambda m: today + timedelta(days=(WEEKDAYS.index(m.group(1).lower()) - today.weekday() - 1) % 7 + 1),
        ),
    ]
    for pat, fn in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return t[: m.start()].strip(), fn(m)
    return t, None


def _number(s: str) -> float:
    return float(s.replace(",", ""))


def recognise(message: str, today: date | None = None) -> Intent:
    today = today or date.today()
    text = message.strip()
    low = re.sub(r"\s+", " ", text.lower()).strip(" ?!.")

    if not low or re.fullmatch(r"(hi|hello|hey|help|what can you do|good (morning|afternoon|evening))", low):
        return Intent("help")

    # --- memory ---
    if re.search(r"\bwhat do you (remember|know) about me\b|\bwhat have you remembered\b|\blist (my )?memories\b", low):
        return Intent("list_memories")
    m = re.match(r"(?:please )?(?:forget|delete|remove)\s+(?:about\s+|what you know about\s+|that\s+)?(.+)", low)
    if m and (
        m.group(1).startswith(("everything", "all", "my ", "the ", "about"))
        or "memor" in low
        or not re.search(r"\btask\b", low)
    ):
        about = re.sub(r"^(?:about|my|the)\s+", "", m.group(1)).strip()
        about = "everything" if about in ("everything", "all", "all memories", "everything you know") else about
        return Intent("tool", "forget_memory", {"about": about})
    m = re.match(r"(?:please )?(?:remember|note|keep in mind)\s+(?:that\s+)?(.+)", text.strip(), re.IGNORECASE)
    if m:
        fact = m.group(1).strip().rstrip(".")
        fact = re.sub(r"^\s*my\b", "My", fact)
        fact = re.sub(r"^\s*i\b", "I", fact)
        return Intent("tool", "save_memory", {"fact": fact[0].upper() + fact[1:] if fact else fact})

    # --- tasks ---
    m = re.match(
        r"(?:please )?(?:remind me to|add (?:a )?task(?: to)?:?|create (?:a )?task(?: to)?:?|"
        r"add to my (?:task |to-?do )?list:?|"
        r"put\b.+\bon my list)\s*(.*)",
        low,
    )
    if m:
        raw = text[len(text) - len(m.group(1)) :] if m.group(1) else ""
        title, due = parse_due(raw, today)
        title = title.strip().rstrip(".")
        return Intent(
            "tool", "create_task", {"title": title[:1].upper() + title[1:], "due": due.isoformat() if due else None}
        )
    if re.search(
        r"\b(list|show|what are|what's on|whats on|read)\b.*\b(tasks|to-?dos?|task list|my list)\b", low
    ) or low in ("my tasks", "tasks", "todo", "to do list"):
        return Intent("tool", "list_tasks", {})

    # --- conversions: "5 km to miles", "convert 72 f to c", "how many cups in 2 liters" ---
    m = re.search(rf"(-?[\d,]*\.?\d+)\s*°?\s*({_UNIT_RE})\b\s*(?:to|in|into|as)\s*°?\s*({_UNIT_RE})\b", low)
    if m:
        return Intent(
            "tool", "convert_units", {"value": _number(m.group(1)), "from_unit": m.group(2), "to_unit": m.group(3)}
        )
    m = re.search(rf"how many\s+({_UNIT_RE})\s+(?:are\s+)?in\s+(-?[\d,]*\.?\d+)\s*({_UNIT_RE})\b", low)
    if m:
        return Intent(
            "tool", "convert_units", {"value": _number(m.group(2)), "from_unit": m.group(3), "to_unit": m.group(1)}
        )

    # --- arithmetic: needs an operator between numbers; words like "plus" are translated ---
    expr = low
    for word, op in (
        (" plus ", "+"),
        (" minus ", "-"),
        (" times ", "*"),
        (" multiplied by ", "*"),
        (" divided by ", "/"),
        (" over ", "/"),
        (" to the power of ", "**"),
        (" x ", "*"),
    ):
        expr = expr.replace(word, f" {op} ")
    m = re.search(
        r"(?:what is|what's|whats|calculate|compute|evaluate|=)?\s*"
        r"([-+(]*\s*[\d.,]+(?:\s*[-+*/%^×÷()]+\s*[-+(]*\s*[\d.,]+\)*)+)",
        expr,
    )
    if m and re.search(r"\d\s*[-+*/%^×÷]+\s*[-+(]*\s*\d", m.group(1)) and not re.search(r"\d{4}-\d{2}-\d{2}", low):
        return Intent("tool", "calculator", {"expression": m.group(1).strip()})
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent) of\s+\$?([\d,]+(?:\.\d+)?)", low)
    if m:
        return Intent("tool", "calculator", {"expression": f"{m.group(1)} / 100 * {m.group(2).replace(',', '')}"})

    # --- date / time ---
    if re.search(
        r"\b(what(?:'s| is)? (?:the )?(?:date|time|day)|what day is it|today's date|current (?:date|time))\b", low
    ):
        return Intent("tool", "current_datetime", {})

    # --- attachments ---
    if re.search(r"\b(summari[sz]e|summary|tl;?dr|key points|main points|gist)\b", low):
        return Intent("summarize")
    if re.search(
        r"\b(transcribe|transcript|read (?:it|this|the \w+) (?:out|to me)|what does (?:it|this|the \w+) say|"
        r"extract (?:the )?text|full text)\b",
        low,
    ):
        return Intent("read")
    if re.search(r"\b(describe|what(?:'s| is) in (?:this|the) (?:image|picture|photo)|what do you see)\b", low):
        return Intent("describe")

    return Intent("question")
