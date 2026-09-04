-- Bare-RDS bootstrap for the core BitCadence data plane.
--
-- The normal migration set is additive and starts by altering these tables.
-- Supabase projects historically received this base schema from docs/SETUP.md;
-- RDS needs the same idempotent foundation before those migrations run.

create table if not exists agent_registry (
  instance_id     text primary key,
  role            text not null,
  status          text not null default 'offline',
  last_seen_at    timestamptz default now(),
  auth_token_hash text not null
);

create table if not exists agent_jobs (
  id                    uuid primary key default gen_random_uuid(),
  title                 text not null,
  description           text,
  source_agent_id       text,
  source_agent_role     text,
  target_agent_role     text not null,
  target_agent_id       text,
  status                text not null default 'pending',
  leased_by_instance_id text,
  depends_on            text[] default '{}',
  input_payload         jsonb default '{}'::jsonb,
  output_payload        jsonb default '{}'::jsonb,
  created_at            timestamptz default now(),
  started_at            timestamptz,
  completed_at          timestamptz,
  error_message         text
);

create index if not exists idx_agent_jobs_target
  on agent_jobs (target_agent_role, status);

-- Multi-tenancy is ordered before the governance migration that normally
-- creates this table, so a pristine database also needs its base shape here.
create table if not exists agent_job_events (
  id          bigint generated always as identity primary key,
  job_id      text not null,
  event       text not null,
  actor_id    text,
  actor_role  text,
  detail      jsonb default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

create or replace function lease_task(p_agent_instance_id text, p_task_id text)
returns boolean
language plpgsql
as $$
declare
  rows_affected int;
begin
  update agent_jobs
     set status = 'leased',
         leased_by_instance_id = p_agent_instance_id,
         started_at = now()
   where id = p_task_id::uuid
     and status = 'pending'
     and leased_by_instance_id is null;
  get diagnostics rows_affected = row_count;
  return rows_affected > 0;
end;
$$;
