-- S04: owner grants and explicit human checkpoint records.
-- Authorizations remain in score_grants; checkpoint decisions are separate.

ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS not_before TIMESTAMPTZ;
ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS budget_cents BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_score_grants_run
  ON score_grants (org_id, run_id, digest);

CREATE TABLE IF NOT EXISTS score_gate_requests (
  id          TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL DEFAULT 'default',
  run_id      TEXT NOT NULL,
  digest      TEXT NOT NULL,
  task_id     TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('g08_launch_signoff', 'spend_above_cap')),
  evidence    JSONB NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_score_gate_request UNIQUE (org_id, run_id, digest, task_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_score_gate_requests_view
  ON score_gate_requests (org_id, status, created_at);

CREATE TABLE IF NOT EXISTS score_checkpoint_decisions (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL DEFAULT 'default',
  gate_id           TEXT NOT NULL UNIQUE,
  run_id            TEXT NOT NULL,
  digest            TEXT NOT NULL,
  task_id           TEXT NOT NULL,
  decision          TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
  human_principal   TEXT NOT NULL,
  reason            TEXT NOT NULL DEFAULT '',
  decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_score_checkpoint_decisions_run
  ON score_checkpoint_decisions (org_id, run_id, task_id);

CREATE OR REPLACE FUNCTION score_checkpoint_decisions_block_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'score_checkpoint_decisions is append-only: % is not allowed', tg_op;
END;
$$;

DROP TRIGGER IF EXISTS trg_score_checkpoint_decisions_immutable ON score_checkpoint_decisions;
CREATE TRIGGER trg_score_checkpoint_decisions_immutable
  BEFORE UPDATE OR DELETE ON score_checkpoint_decisions
  FOR EACH ROW EXECUTE FUNCTION score_checkpoint_decisions_block_mutation();
