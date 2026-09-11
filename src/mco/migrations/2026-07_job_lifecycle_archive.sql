-- ═══════════════════════════════════════════════════════════════════════
-- MCOrchestr8 Job Lifecycle Migration (July 2026)
-- Adds: CANCELLED as a job status, archive/unarchive (soft-hide from the
-- default board view), and reassignment linkage (clone-and-supersede a
-- failed/rejected/cancelled job onto a new target, keeping both rows so
-- "did a replacement get done?" always has a direct answer).
-- Idempotent: safe to run more than once. Not required for the Local-Only
-- edition - LocalStore rows are schemaless JSON documents, so these fields
-- just start appearing the first time a route writes them.
-- ═══════════════════════════════════════════════════════════════════════

alter table agent_jobs add column if not exists archived              boolean not null default false;
alter table agent_jobs add column if not exists archived_at           timestamptz;
alter table agent_jobs add column if not exists archived_by           text;
alter table agent_jobs add column if not exists reassigned_from_job_id text;
alter table agent_jobs add column if not exists reassigned_to_job_id   text;

create index if not exists idx_agent_jobs_archived
  on agent_jobs (org_id, archived);
