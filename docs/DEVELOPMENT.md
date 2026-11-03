# Development guide

## Prerequisites
Python 3.12+, Node 22, and PostgreSQL 16 (Docker is easiest). No system packages are needed for the
media stack: PyAV bundles FFmpeg, OpenCV is the headless build, and every model runs on ONNX Runtime or
CTranslate2.

## Backend

```bash
cd backend
```
```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
```
```bash
pip install -r requirements-dev.txt
```
```bash
docker run -d --name mma-db -e POSTGRES_USER=mma -e POSTGRES_PASSWORD=mma -e POSTGRES_DB=mma -p 127.0.0.1:5432:5432 postgres:16-alpine
```

Create `backend/.env` (git-ignored):

```ini
MMA_DATABASE_URL=postgresql+psycopg://mma:mma@127.0.0.1:5432/mma
MMA_JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
MMA_COOKIE_SECURE=false
MMA_LOG_JSON=false
MMA_DEMO_PASSWORD=<12+ characters>
MMA_MODEL_CACHE_DIR=~/.cache/mma-models
```

Then run each step:

```bash
python -m app.cli migrate
```
```bash
python -m app.cli warm-models          # downloads Whisper, the Piper voice, the embedder and the reranker (~400 MB)
```
```bash
python -m app.cli load-demo            # alex@example.com (member), admin@example.com (admin)
```
```bash
uvicorn app.main:app --reload --port 8000
```
```bash
python -m app.cli worker               # in a second terminal: processes uploads
```

## Frontend

```bash
cd frontend && npm ci && npm run dev    # http://localhost:5173, proxies /api to :8000
```

`npm test` runs the unit tests, `npm run lint` and `npm run typecheck` the static checks, and
`npm run build` the production build.

## Tests

The backend tests run against a real PostgreSQL database, `mma_test`. Its schema is rebuilt from the
migration at the start of every session. The media tests use the real local models (cached in
`MMA_MODEL_CACHE_DIR`). They never call a paid API: Claude-provider tests use the scripted clients in
`app/evaluation/fake_claude.py`.

```bash
createdb -h 127.0.0.1 -U mma mma_test     # once
```
```bash
MMA_DATABASE_URL=postgresql+psycopg://mma:mma@127.0.0.1:5432/mma_test pytest
```

| File | Covers |
|---|---|
| `tests/test_units.py` | Calculator safety, unit conversion, intent rules and due dates, file-type detection, file-store traversal, PDF generation, retrieval helpers, error-rate metrics |
| `tests/test_chat.py` | Upload → OCR / parsing / transcription → cited answers for every file type; abstention; voice in and speech out; tool confirmation, decline, double-approval and deny; memory and the memory switch; privacy between users; roles; deletion and export; the Claude provider (citations, cost, image blocks), prompt injection with an obeying model, and API-failure fallback |
| `tests/test_auth.py` | Login, lockout, token rotation and reuse detection, roles, user administration |

## Evaluation

```bash
MMA_DATABASE_URL=postgresql+psycopg://mma:mma@127.0.0.1:5432/mma_test python -m app.cli evaluate --reset --gate --out ../eval/reports
```

`--reset` is required because the run **deletes all data** in the configured database. `--gate` exits
non-zero when a metric misses its threshold (`THRESHOLDS` in `app/evaluation/run.py`). The run takes
about 10–15 minutes on a laptop CPU, most of it speech recognition.

To add a test file, add a `Sample` with labelled `QA` items to `app/synthetic/samples.py`. A `QA` with an
empty `expect` list is an unanswerable question; the assistant must decline it.

## Project layout

```
backend/app/
  assistant/   engine, providers (local, claude), retrieval, intents, tools, the permission gate
  media/       file detection and storage, OCR / document / audio processing, model loaders
  modules/     API routers: auth, users, chat, memory (memories + tasks), settings, admin, audit
  jobs/        PostgreSQL job queue, worker, handlers (process_attachment)
  evaluation/  evaluation suite and scripted Claude clients
  synthetic/   generated sample files with labelled questions
frontend/src/
  pages/       Chat, Memory & tasks, Settings, Administration, Login
  components/  chat (rich text, citations, tool cards, attachments, recorder, speech), ui
infra/terraform/  AWS ECS + RDS + EFS (not applied)
```

## Conventions
- Migrations are explicit SQL in Alembic. Never autogenerate a migration without reading it.
- Every new tool needs a JSON schema, a safe default permission, `changes_data` set correctly, and a
  sentence in `base.describe`.
- Run `ruff check`, `ruff format`, `mypy app` and `npm run lint` before pushing; CI runs the same checks.
