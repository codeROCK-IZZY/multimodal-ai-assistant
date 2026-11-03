"""The assistant's tools and their permissions.

Each tool has a JSON schema (used by the Claude provider), a handler, and a **default permission**:

* ``allow`` — run without asking (read-only or harmless: calculator, unit conversion, the date).
* ``ask``   — the assistant must get the user's confirmation first (it changes the user's data).
* ``deny``  — never run.

Users can change every permission in Settings. Permissions are enforced in ``execute`` — the
single place that runs tools — so neither provider (rules or Claude) can bypass them.
"""

from __future__ import annotations

import ast
import operator
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Memory, Task, UserSettings

Permission = str  # allow | ask | deny


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    default: Permission
    handler: Callable[[Session, uuid.UUID, dict[str, Any]], dict[str, Any]]
    changes_data: bool = False


# ---------------------------------------------------------------------- calculator (no eval)
_OPS: dict[type, Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expression: str) -> float:
    """Evaluate +, -, *, /, //, %, ** and parentheses on numbers — nothing else (no names, calls or attributes)."""
    expr = expression.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolError("exponent too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ToolError("only arithmetic is supported")

    try:
        value = ev(ast.parse(expr, mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ToolError(f"cannot calculate: {exc}") from exc
    return float(value)


def _calculator(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    value = safe_eval(str(a["expression"]))
    shown = int(value) if value.is_integer() else round(value, 6)
    return {"expression": a["expression"], "result": shown}


# ---------------------------------------------------------------------- unit conversion
UNITS: dict[str, tuple[str, float]] = {  # unit -> (dimension, factor to the base unit)
    "mm": ("length", 0.001),
    "cm": ("length", 0.01),
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "in": ("length", 0.0254),
    "inch": ("length", 0.0254),
    "inches": ("length", 0.0254),
    "ft": ("length", 0.3048),
    "foot": ("length", 0.3048),
    "feet": ("length", 0.3048),
    "yd": ("length", 0.9144),
    "mile": ("length", 1609.344),
    "miles": ("length", 1609.344),
    "mi": ("length", 1609.344),
    "g": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "lb": ("mass", 0.45359237),
    "lbs": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237),
    "pounds": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523),
    "ml": ("volume", 0.001),
    "l": ("volume", 1.0),
    "liter": ("volume", 1.0),
    "litre": ("volume", 1.0),
    "liters": ("volume", 1.0),
    "litres": ("volume", 1.0),
    "gallon": ("volume", 3.785411784),
    "gallons": ("volume", 3.785411784),
    "cup": ("volume", 0.2365882365),
    "cups": ("volume", 0.2365882365),
    "c": ("temperature", 1.0),
    "celsius": ("temperature", 1.0),
    "f": ("temperature", 1.0),
    "fahrenheit": ("temperature", 1.0),
    "k": ("temperature", 1.0),
    "kelvin": ("temperature", 1.0),
}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    fu, tu = from_unit.lower().strip("°. "), to_unit.lower().strip("°. ")
    if fu not in UNITS or tu not in UNITS:
        raise ToolError(f"unknown unit: {fu if fu not in UNITS else tu}")
    (dim_f, factor_f), (dim_t, factor_t) = UNITS[fu], UNITS[tu]
    if dim_f != dim_t:
        raise ToolError(f"cannot convert {dim_f} to {dim_t}")
    if dim_f == "temperature":
        c = {
            "c": value,
            "celsius": value,
            "f": (value - 32) * 5 / 9,
            "fahrenheit": (value - 32) * 5 / 9,
            "k": value - 273.15,
            "kelvin": value - 273.15,
        }[fu]
        return {
            "c": c,
            "celsius": c,
            "f": c * 9 / 5 + 32,
            "fahrenheit": c * 9 / 5 + 32,
            "k": c + 273.15,
            "kelvin": c + 273.15,
        }[tu]
    return value * factor_f / factor_t


def _convert_units(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    result = convert(float(a["value"]), str(a["from_unit"]), str(a["to_unit"]))
    return {"value": a["value"], "from_unit": a["from_unit"], "to_unit": a["to_unit"], "result": round(result, 4)}


# ---------------------------------------------------------------------- date, tasks, memory
def _now(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {"utc": now.isoformat(timespec="seconds"), "date": now.date().isoformat(), "weekday": now.strftime("%A")}


def _create_task(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    title = str(a["title"]).strip()[:300]
    if not title:
        raise ToolError("a task needs a title")
    due = date.fromisoformat(a["due"]) if a.get("due") else None
    task = Task(user_id=user_id, title=title, due=due)
    db.add(task)
    db.flush()
    return {"task_id": str(task.id), "title": title, "due": due.isoformat() if due else None}


def _list_tasks(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    tasks = db.scalars(
        select(Task).where(Task.user_id == user_id, ~Task.done).order_by(Task.due.nulls_last(), Task.created_at)
    )
    return {"tasks": [{"title": t.title, "due": t.due.isoformat() if t.due else None} for t in tasks]}


def _save_memory(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    settings = db.get(UserSettings, user_id)
    if settings is not None and not settings.memory_enabled:
        raise ToolError("memory is turned off in your settings")
    text = str(a["fact"]).strip()[:500]
    if not text:
        raise ToolError("nothing to remember")
    m = Memory(user_id=user_id, text=text, source="chat")
    db.add(m)
    db.flush()
    return {"memory_id": str(m.id), "fact": text}


def _forget_memory(db: Session, user_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    about = str(a.get("about") or "").strip().lower()
    stmt = delete(Memory).where(Memory.user_id == user_id)
    if about and about not in ("everything", "all"):
        stmt = stmt.where(Memory.text.ilike(f"%{about.replace('%', '').replace('_', '')}%"))
    removed = db.execute(stmt).rowcount  # type: ignore[attr-defined]
    return {"removed": int(removed or 0), "about": about or "everything"}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "calculator",
            "Evaluate an arithmetic expression exactly.",
            _obj({"expression": {"type": "string"}}, ["expression"]),
            "allow",
            _calculator,
        ),
        Tool(
            "convert_units",
            "Convert a value between units of length, mass, volume or temperature.",
            _obj(
                {"value": {"type": "number"}, "from_unit": {"type": "string"}, "to_unit": {"type": "string"}},
                ["value", "from_unit", "to_unit"],
            ),
            "allow",
            _convert_units,
        ),
        Tool("current_datetime", "Get today's date and the current time (UTC).", _obj({}, []), "allow", _now),
        Tool(
            "create_task",
            "Add a task to the user's task list, optionally with a due date (YYYY-MM-DD).",
            _obj({"title": {"type": "string"}, "due": {"type": ["string", "null"]}}, ["title", "due"]),
            "ask",
            _create_task,
            changes_data=True,
        ),
        Tool("list_tasks", "List the user's open tasks.", _obj({}, []), "allow", _list_tasks),
        Tool(
            "save_memory",
            "Remember a fact the user explicitly asked to be remembered.",
            _obj({"fact": {"type": "string"}}, ["fact"]),
            "allow",
            _save_memory,
            changes_data=True,
        ),
        Tool(
            "forget_memory",
            "Delete remembered facts about a topic, or everything.",
            _obj({"about": {"type": "string"}}, ["about"]),
            "ask",
            _forget_memory,
            changes_data=True,
        ),
    ]
}


def permissions(db: Session, user_id: uuid.UUID) -> dict[str, Permission]:
    settings = db.get(UserSettings, user_id)
    stored = settings.tool_permissions if settings else {}
    return {name: stored.get(name, t.default) for name, t in TOOLS.items()}


def execute(
    db: Session, user_id: uuid.UUID, name: str, args: dict[str, Any], *, confirmed: bool = False
) -> dict[str, Any]:
    """Run a tool if the user's permissions allow it. ``confirmed`` = the user approved this call."""
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    perm = permissions(db, user_id)[name]
    if perm == "deny":
        raise PermissionError(f"{name} is disabled in your settings")
    if perm == "ask" and not confirmed:
        raise PermissionError(f"{name} needs your confirmation")
    return TOOLS[name].handler(db, user_id, args)
