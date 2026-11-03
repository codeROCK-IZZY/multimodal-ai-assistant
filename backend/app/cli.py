"""Command-line tools: ``python -m app.cli <command>``.

migrate                          apply database migrations
create-user --email E --role R   create a user (password from MMA_USER_PASSWORD or prompt)
warm-models                      download every local model (speech, voice, OCR, embeddings)
load-demo                        demo users with sample conversations, files, memories and tasks
evaluate --reset --out DIR       RESETS the database, runs the evaluation suite, writes the report
worker                           run the background worker (processes uploads)
drain                            process all queued jobs, then exit
demo                             migrate + load-demo (one-command setup)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging

DEMO_USERS = {"alex": "member", "admin": "admin"}
# (conversation title, sample names to attach, messages to send once the files are processed).
# Messages starting with "voice:" are spoken by the local voice and sent through speech recognition.
DEMO_CONVERSATIONS = [
    (
        "Business trip expenses",
        ["receipt", "policy"],
        ["What was the total on the receipt?", "What is the daily meal limit?", "What is 23.85 + 41.20 + 18.90?"],
    ),
    (
        "Dentist reminder",
        ["memo"],
        [
            "voice:What should I bring to the dentist?",
            "When is the appointment?",
            "Remind me to book a taxi for the dentist on tuesday",
        ],
    ),
    (
        "Project Lighthouse",
        ["kickoff", "notice"],
        [
            "Who owns the design review?",
            "Which room is the offsite in?",
            "Summarize the kickoff notes",
            "Remember that the Lighthouse launch is my top priority this quarter",
        ],
    ),
]
DEMO_MEMORIES = ["My manager is Dana Whitfield.", "I prefer window seats on flights.", "My home city is Millbrook."]


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    print("migrations applied")


def _create_user(email: str, role: str, password: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = password or os.environ.get("MMA_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} exists — skipped")
            return
        db.add(
            User(
                email=email.lower(),
                full_name=email.split("@")[0].title(),
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"user {email} ({role}) created")


def load_demo() -> None:
    from sqlalchemy import select

    from app.assistant import engine
    from app.core.security import hash_password
    from app.db.models import Attachment, Conversation, Memory, Task, User
    from app.db.session import session_scope
    from app.jobs import queue, worker
    from app.media import files, models, process
    from app.synthetic import samples

    password = os.environ.get("MMA_DEMO_PASSWORD")
    if not password or len(password) < 12:
        sys.exit("set MMA_DEMO_PASSWORD (12+ characters) for the demo users")
    with session_scope() as db:
        users = {}
        for name, role in DEMO_USERS.items():
            email = f"{name}@example.com"
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                u = User(email=email, full_name=name.title(), password_hash=hash_password(password), role=role)
                db.add(u)
                db.flush()
            users[name] = u
        alex = users["alex"]
        if db.scalar(select(Conversation.id).where(Conversation.user_id == alex.id).limit(1)):
            print("demo data already loaded")
            return
        by_name = {s.name: s for s in samples.build()}
        store = files.store()
        planned = []
        for title, names, messages in DEMO_CONVERSATIONS:
            conv = Conversation(user_id=alex.id, title=title)
            db.add(conv)
            db.flush()
            for n in names:
                s = by_name[n]
                det = files.detect(s.data, s.filename)
                key, sha = store.put(s.data)
                att = Attachment(
                    user_id=alex.id,
                    conversation_id=conv.id,
                    kind=det.kind,
                    filename=s.filename,
                    mime=det.mime,
                    size_bytes=len(s.data),
                    sha256=sha,
                    file_key=key,
                )
                db.add(att)
                db.flush()
                queue.enqueue(db, "process_attachment", {"attachment_id": str(att.id)}, max_attempts=1)
            planned.append((conv.id, messages))
        for text in DEMO_MEMORIES:
            db.add(Memory(user_id=alex.id, text=text, source="manual"))
        db.add(Task(user_id=alex.id, title="Submit October expense claim"))
        alex_id = alex.id
    print(f"processed {worker.drain()} uploads")
    with session_scope() as db:
        for conv_id, messages in planned:
            demo_conv = db.get(Conversation, conv_id)
            assert demo_conv is not None
            title = demo_conv.title
            for text in messages:
                if text.startswith("voice:"):
                    heard = process.process_audio(models.synthesize(text.removeprefix("voice:")), max_seconds=120)
                    meta = {k: heard.meta[k] for k in ("duration_seconds", "language", "language_probability")}
                    engine.respond(db, alex_id, demo_conv, heard.text, input_mode="voice", transcript_meta=meta)
                else:
                    engine.respond(db, alex_id, demo_conv, text)
            demo_conv.title = title
    print("demo loaded: alex@example.com (member) and admin@example.com (admin)")


def _evaluate(out: Path, reset: bool, gate: bool) -> None:
    from app.evaluation.run import evaluate, gate_failures, write_report

    if not reset:
        sys.exit("evaluate deletes all data in the database; pass --reset to confirm (use a separate database)")
    report = evaluate()
    write_report(report, out)
    print(json.dumps(report["summary"], indent=1, default=str))
    if gate and (failures := gate_failures(report)):
        sys.exit("evaluation gate failed: " + "; ".join(failures))


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    sub.add_parser("warm-models")
    sub.add_parser("load-demo")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../eval/reports"))
    ev.add_argument("--reset", action="store_true", help="confirm that all data may be deleted")
    ev.add_argument("--gate", action="store_true", help="exit non-zero if any metric misses its threshold")
    sub.add_parser("worker")
    sub.add_parser("drain")
    sub.add_parser("demo")
    args = p.parse_args(argv)
    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-user":
        _create_user(args.email, args.role)
    elif args.cmd == "warm-models":
        from app.media import models

        models.warm_up()
        print("models ready")
    elif args.cmd == "load-demo":
        load_demo()
    elif args.cmd == "evaluate":
        _evaluate(args.out, args.reset, args.gate)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd == "drain":
        from app.jobs.worker import drain

        print(f"processed {drain()} jobs")
    elif args.cmd == "demo":
        _migrate()
        load_demo()


if __name__ == "__main__":
    main()
