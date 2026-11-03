# Operations runbook

## Processes

| Process | Command | Notes |
|---|---|---|
| API | `uvicorn app.main:app --workers 1` | Loads the models in a background thread at start (about 20–40 s); transcribes voice messages and synthesises speech in requests |
| Worker | `python -m app.cli worker` | Processes uploads (OCR, parsing, transcription, embeddings). Stops after the current job on SIGTERM; give it up to 120 s |
| Migrations | `python -m app.cli migrate` | Run once per deploy, before the new API version |

Both processes need the same **file store** (`MMA_FILE_DIR`): the API writes uploads and the worker reads
them. Locally this is a Docker volume; on AWS it is EFS.

## Configuration (`MMA_*` environment variables)

| Variable | Default | Effect |
|---|---|---|
| `MMA_LLM_PROVIDER` | `auto` | `auto` uses Claude when `ANTHROPIC_API_KEY` is set; `local` never calls an API |
| `MMA_CLAUDE_MODEL` / `MMA_CLAUDE_EFFORT` | `claude-opus-5-5` / `medium` | Claude model and effort |
| `MMA_STT_MODEL` | `base.en` | `tiny.en` is about 2× faster with higher error; `small.en` is more accurate and about 3× slower |
| `MMA_TTS_VOICE` | `en_US-ljspeech-medium` | Any Piper voice id; check the voice's dataset licence |
| `MMA_ANSWER_THRESHOLD` | `-3.0` | Cross-encoder score below which the assistant declines. Lower values answer more and decline less; re-run the evaluation after changing it |
| `MMA_MAX_UPLOAD_MB` / `MMA_MAX_PDF_PAGES` / `MMA_MAX_AUDIO_SECONDS` | 25 / 100 / 600 | Upload limits |
| `MMA_MODEL_CACHE_DIR` | none (Hugging Face default) | Where models are stored; `/models` in the image |
| `MMA_MODEL_THREADS` | all cores | CPU threads per model |

## Sizing

| | CPU | Memory | Why |
|---|---|---|---|
| API | 1 vCPU | 3 GB | About 1.5 GB of resident models; a 30-second voice message is transcribed in roughly 2–4 s |
| Worker | 2 vCPU | 4 GB | OCR of a photo takes roughly 2–8 s; a 10-minute recording transcribes in about 1–3 minutes |

Throughput scales with worker tasks: each takes jobs with `SKIP LOCKED`, so adding workers needs no
coordination.

## Health and monitoring
- `GET /api/health/live`: the process is up. `GET /api/health/ready`: the database is reachable.
- **Admin page:** reply latency (p50/p95), replies by status (the share of `not_found` and `error`
  matters), files by status, average processing time per file type, tool-request outcomes, and the audit
  chain's verification status.
- **Structured JSON logs** with a request id on every line. Worker jobs log their type, duration and
  outcome.
- **Alarms (Terraform):** API 5xx, unhealthy targets, p95 latency, RDS CPU and storage, failed jobs, and
  a monthly budget alert.

## Runbooks

**Uploads stay "processing".** Check that the worker is running and look at `/admin/jobs`. Jobs left in
`running` by a crashed worker are re-queued automatically after 30 minutes.

**An attachment failed.** The error is shown on the attachment and stored in `attachments.error`.
Predictable failures (unreadable image, undecodable audio, too many pages) are not retried; ask the user
for another format.

**Many `not_found` replies.** Check that the files are `ready` and contain text (open "Show extracted
text"). If users ask well-formed questions the files do answer, consider lowering
`MMA_ANSWER_THRESHOLD`, but run the evaluation first and compare the sweep.

**Claude errors.** Replies are labelled "local (Claude unavailable)" and keep working. Check the API key,
rate limits and Anthropic's status page. To turn Claude off, set `MMA_LLM_PROVIDER=local`; no data
migration is needed.

**Slow first request after a deploy.** The models load in the background after start, and the health-check
grace period is 120 s. If every request is slow, check CPU throttling and `MMA_MODEL_THREADS`.

**Audit chain broken.** `/admin/audit` names the first broken event. The table rejects UPDATE and DELETE,
so a break means someone with database-owner rights disabled the trigger. Treat it as a security
incident: snapshot the database and review access.

## Backups and data
- **Database:** RDS automated backups (7 days) and a final snapshot on deletion.
- **Files:** EFS backup policy enabled (AWS Backup); locally, back up the `files` volume.
- **Deleting a user's data:** deleting a conversation removes its files, extracted text, embeddings and
  cached speech. Deactivating a user blocks sign-in. For a full erasure, delete the user row (all of
  their data cascades), then remove their files from the store.
