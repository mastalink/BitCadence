-- ═══════════════════════════════════════════════════════════════════════
-- BitCadence Drumline Agent Exchange Migration (September 2026)
-- Two append-only tables: agent_exchanges (non-authoritative discussion)
-- and agent_exchange_promotions (immutable receipts for explicit promotion
-- into agent_context). Existing tables and APIs are untouched. The feature
-- stays off until MCO_AGENT_EXCHANGE is enabled. Idempotent.
-- Rollback: disable MCO_AGENT_EXCHANGE; do not drop exchange data.
-- ═══════════════════════════════════════════════════════════════════════

create table if not exists agent_exchanges (
  id                     uuid primary key,
  org_id                 text not null default 'default',
  kind                   text not null check (kind in
    ('question','proposal','blocker','reply','decision','handoff','resolution','supersession')),
  body                   text not null check (char_length(body) <= 8000),
  body_sha256            text not null,
  author_instance_id     text not null,
  author_role            text,
  author_subject         text,
  created_at             timestamptz not null default now(),
  thread_id              uuid not null,
  reply_to_id            uuid,
  job_id                 uuid,
  workflow_name          text,
  workflow_run           text,
  workflow_step          text,
  resolves_exchange_id   uuid,
  supersedes_exchange_id uuid,
  provenance             jsonb not null default '{}'::jsonb,
  idempotency_key        text not null check (char_length(idempotency_key) <= 128),
  request_sha256         text not null,
  constraint agent_exchanges_linkage check (
    job_id is not null
    or (workflow_name is not null and workflow_run is not null and workflow_step is not null)),
  constraint agent_exchanges_idempotency unique (org_id, author_instance_id, idempotency_key)
);

create index if not exists idx_agent_exchanges_org_created
  on agent_exchanges (org_id, created_at desc, id desc);
create index if not exists idx_agent_exchanges_thread
  on agent_exchanges (org_id, thread_id, created_at, id);
create index if not exists idx_agent_exchanges_job
  on agent_exchanges (org_id, job_id, created_at desc);
create index if not exists idx_agent_exchanges_workflow
  on agent_exchanges (org_id, workflow_name, workflow_run, workflow_step, created_at desc);

create table if not exists agent_exchange_promotions (
  id                   uuid primary key,
  org_id               text not null default 'default',
  exchange_id          uuid not null,
  thread_id            uuid not null,
  exchange_sha256      text,
  target_kind          text not null check (target_kind in ('decision','lesson','handoff')),
  title_sha256         text not null,
  body_sha256          text not null,
  context_id           uuid not null,
  promoted_by          text not null,
  promoted_by_role     text,
  idempotency_key      text not null check (char_length(idempotency_key) <= 128),
  audit_correlation_id text not null,
  created_at           timestamptz not null default now(),
  constraint agent_exchange_promotions_once unique (org_id, exchange_id, target_kind)
);

create index if not exists idx_agent_exchange_promotions_thread
  on agent_exchange_promotions (org_id, thread_id);

-- Append-only: history is never rewritten; resolution/supersession are later rows.
create or replace function agent_exchange_append_only() returns trigger as $$
begin
  raise exception '% is append-only: % is not allowed', tg_table_name, tg_op;
end;
$$ language plpgsql;

drop trigger if exists trg_agent_exchanges_append_only on agent_exchanges;
create trigger trg_agent_exchanges_append_only
  before update or delete on agent_exchanges
  for each row execute function agent_exchange_append_only();

drop trigger if exists trg_agent_exchange_promotions_append_only on agent_exchange_promotions;
create trigger trg_agent_exchange_promotions_append_only
  before update or delete on agent_exchange_promotions
  for each row execute function agent_exchange_append_only();

