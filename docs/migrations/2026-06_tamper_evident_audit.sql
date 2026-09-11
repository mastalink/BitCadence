-- ═══════════════════════════════════════════════════════════════════════
-- BitCadence Tamper-Evident Audit Migration (June 2026)
-- Hash-chains the append-only agent_job_events audit trail: each row stores
-- the previous row's hash, its own content hash, and an optional HMAC
-- signature. Combined with the existing append-only trigger, this makes the
-- trail tamper-EVIDENT - any edit, deletion, or reordering breaks the chain.
-- Idempotent: safe to run more than once.
-- ═══════════════════════════════════════════════════════════════════════

-- ── agent_job_events: hash-chain columns ────────────────────────────────
-- prev_hash : hash of the immediately preceding event for the same job
--             ('' for the first event in a job's chain).
-- hash      : sha256(prev_hash || E'\n' || canonical(content)) of THIS row.
-- signature : optional HMAC-SHA256 over `hash` when an audit key is set.
alter table agent_job_events add column if not exists prev_hash text not null default '';
alter table agent_job_events add column if not exists hash      text not null default '';
alter table agent_job_events add column if not exists signature text;

-- The chain is walked per job in insertion order; this index already exists
-- from the governance migration but is repeated here (idempotently) so this
-- file stands alone if applied in isolation.
create index if not exists idx_agent_job_events_job
  on agent_job_events (job_id, created_at);

-- Note: immutability is still enforced by trg_agent_job_events_immutable
-- (see 2026-06_phase_a_governance.sql). The hash chain is the second,
-- application-verifiable line of defense layered on top of that trigger.
