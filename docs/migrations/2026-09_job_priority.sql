-- Job scheduling priority.
--
-- Workers are instructed to take the FIRST job in their inbox, so the order
-- /api/jobs/pending returns IS the scheduling policy. It was previously
-- unordered (insertion order), which starved newly-dispatched urgent work
-- behind an old backlog: a role with six queued jobs would not reach a job
-- sent today until the previous five were done.
--
-- Ordering is now (priority DESC, created_at ASC): highest priority first,
-- oldest first within a band, so raising priority cannot starve older work at
-- the same level. Default 0 leaves every existing job and caller unchanged.
--
-- The embedded LocalStore is schemaless JSON and needs no migration; this is
-- for the Postgres/PostgREST backend only.

ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;

-- Serves the pending-jobs inbox query.
CREATE INDEX IF NOT EXISTS idx_agent_jobs_pending_priority
    ON agent_jobs (target_agent_role, status, priority DESC, created_at ASC);
