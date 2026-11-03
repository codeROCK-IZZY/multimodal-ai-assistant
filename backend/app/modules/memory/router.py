"""What the assistant remembers about the user, and the user's task list. Both fully user-controlled."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.api.deps import DB, CurrentUser, Member
from app.core.errors import NotFound
from app.db.models import Memory, Task
from app.modules import audit

router = APIRouter(tags=["memory"])


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    text: str
    source: str
    created_at: datetime


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    due: date | None
    done: bool
    created_at: datetime


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due: date | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due: date | None = None
    done: bool | None = None


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(db: DB, user: CurrentUser) -> list[Memory]:
    return list(db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(Memory.created_at.desc())))


@router.post("/memories", response_model=MemoryOut, status_code=201)
def add_memory(body: MemoryIn, db: DB, user: Member) -> Memory:
    m = Memory(user_id=user.id, text=body.text.strip(), source="manual")
    db.add(m)
    db.commit()
    return m


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: uuid.UUID, db: DB, user: Member) -> Response:
    m = db.get(Memory, memory_id)
    if m is None or m.user_id != user.id:
        raise NotFound("Memory not found")
    db.delete(m)
    audit.record(db, user.id, "memory.deleted", "memory", str(memory_id))
    db.commit()
    return Response(status_code=204)


@router.delete("/memories", status_code=204)
def clear_memories(db: DB, user: Member) -> Response:
    n = db.execute(delete(Memory).where(Memory.user_id == user.id)).rowcount  # type: ignore[attr-defined]
    audit.record(db, user.id, "memory.cleared", "memory", None, removed=int(n or 0))
    db.commit()
    return Response(status_code=204)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: DB, user: CurrentUser, include_done: bool = False) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user.id)
    if not include_done:
        stmt = stmt.where(~Task.done)
    return list(db.scalars(stmt.order_by(Task.done, Task.due.nulls_last(), Task.created_at)))


@router.post("/tasks", response_model=TaskOut, status_code=201)
def add_task(body: TaskIn, db: DB, user: Member) -> Task:
    t = Task(user_id=user.id, title=body.title.strip(), due=body.due)
    db.add(t)
    db.commit()
    return t


def _task(db: DB, user: CurrentUser, task_id: uuid.UUID) -> Task:
    t = db.get(Task, task_id)
    if t is None or t.user_id != user.id:
        raise NotFound("Task not found")
    return t


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: uuid.UUID, body: TaskUpdate, db: DB, user: Member) -> Task:
    t = _task(db, user, task_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(t, field, value)
    db.commit()
    return t


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: uuid.UUID, db: DB, user: Member) -> Response:
    db.delete(_task(db, user, task_id))
    db.commit()
    return Response(status_code=204)
