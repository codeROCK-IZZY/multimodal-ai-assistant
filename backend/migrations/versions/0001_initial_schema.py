"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
-- ============================================================ platform
CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email              varchar(320) NOT NULL,
    full_name          varchar(200) NOT NULL DEFAULT '',
    password_hash      varchar(255) NOT NULL,
    role               varchar(20)  NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    is_active          boolean      NOT NULL DEFAULT true,
    failed_login_count integer      NOT NULL DEFAULT 0,
    locked_until       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email))
);
ALTER TABLE users RENAME CONSTRAINT users_pkey TO pk_users;

CREATE TABLE refresh_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash  varchar(64) NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    job_type     varchar(50) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    finished_at  timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_jobs_status_run_after ON jobs (status, run_after);

-- Tamper-evident audit trail (hash chain; UPDATE/DELETE rejected by trigger).
CREATE TABLE audit_events (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    actor     varchar(100) NOT NULL,
    action    varchar(100) NOT NULL,
    entity    varchar(50) NOT NULL,
    entity_id varchar(100),
    run_id    uuid,
    data      jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash varchar(64) NOT NULL,
    hash      varchar(64) NOT NULL
);
CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END $$;
CREATE TRIGGER trg_audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

-- ============================================================ assistant
CREATE TABLE user_settings (
    user_id          uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    memory_enabled   boolean NOT NULL DEFAULT true,
    tool_permissions jsonb NOT NULL DEFAULT '{}'::jsonb,   -- tool -> allow | ask | deny (defaults in code)
    speak_replies    boolean NOT NULL DEFAULT false,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title      varchar(300) NOT NULL DEFAULT 'New conversation',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_conversations_user_id ON conversations (user_id, updated_at DESC);

CREATE TABLE attachments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id uuid REFERENCES conversations (id) ON DELETE CASCADE,
    kind            varchar(10) NOT NULL CHECK (kind IN ('image', 'document', 'audio')),
    filename        varchar(255) NOT NULL,
    mime            varchar(100) NOT NULL,
    size_bytes      integer NOT NULL,
    sha256          varchar(64) NOT NULL,
    file_key        varchar(500) NOT NULL,
    status          varchar(12) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    error           text,
    text            text NOT NULL DEFAULT '',          -- OCR / extracted / transcribed text
    meta            jsonb NOT NULL DEFAULT '{}'::jsonb, -- pages, OCR confidence, duration, language, size...
    processor       varchar(100),                       -- which model produced ``text``
    processing_ms   integer,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_attachments_conversation_id ON attachments (conversation_id);

-- Searchable pieces of attachment text (a page region, a paragraph, a stretch of a recording).
CREATE TABLE chunks (
    id            bigserial PRIMARY KEY,
    attachment_id uuid NOT NULL REFERENCES attachments (id) ON DELETE CASCADE,
    ordinal       integer NOT NULL,
    locator       varchar(60) NOT NULL,                 -- "page 2", "00:41-00:58", "line 3"
    text          text NOT NULL,
    embedding     real[] NOT NULL
);
CREATE INDEX ix_chunks_attachment_id ON chunks (attachment_id);

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            varchar(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text NOT NULL,
    input_mode      varchar(10) NOT NULL DEFAULT 'text' CHECK (input_mode IN ('text', 'voice')),
    attachment_ids  jsonb NOT NULL DEFAULT '[]'::jsonb,
    citations       jsonb NOT NULL DEFAULT '[]'::jsonb,
    tool_events     jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          varchar(20) CHECK (status IN ('answered', 'not_found', 'needs_permission', 'error')),
    provider        varchar(100),
    usage           jsonb NOT NULL DEFAULT '{}'::jsonb,
    latency_ms      integer,
    speech_key      varchar(500),                       -- cached text-to-speech audio
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_messages_conversation_id ON messages (conversation_id, created_at);

CREATE TABLE memories (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    text       varchar(500) NOT NULL,
    source     varchar(20) NOT NULL DEFAULT 'chat' CHECK (source IN ('chat', 'manual')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_memories_user_id ON memories (user_id);

CREATE TABLE tasks (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title      varchar(300) NOT NULL,
    due        date,
    done       boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_tasks_user_id ON tasks (user_id);

-- A tool call the assistant wants to make but the user's permissions say "ask first".
CREATE TABLE tool_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    message_id      uuid NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    tool            varchar(50) NOT NULL,
    args            jsonb NOT NULL,
    status          varchar(10) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied', 'executed', 'failed')),
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz
);
CREATE INDEX ix_tool_requests_user_id ON tool_requests (user_id, status);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS tool_requests, tasks, memories, messages, chunks, attachments, conversations, user_settings,
    audit_events, jobs, refresh_tokens, users CASCADE;
DROP FUNCTION IF EXISTS audit_events_immutable();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
