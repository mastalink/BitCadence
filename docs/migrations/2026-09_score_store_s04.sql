-- S04: owner grants and explicit human checkpoint records.
-- Authorizations remain in score_grants; checkpoint decisions are separate.

ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS not_before TIMESTAMPTZ;
ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS budget_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE score_grants ADD COLUMN IF NOT EXISTS id TEXT DEFAULT gen_random_uuid()::text;

-- Pre-S04 grants were not bound to a run and their signatures cannot be
-- upgraded safely. Quarantine them under a deterministic legacy scope; a
-- live run must receive a newly issued, run-bound grant.
UPDATE score_grants
SET run_id = 'legacy-unscoped:' || digest
WHERE run_id IS NULL OR btrim(run_id) = '';

ALTER TABLE score_grants ALTER COLUMN id SET NOT NULL;
ALTER TABLE score_grants ALTER COLUMN run_id SET NOT NULL;

DO $$
DECLARE
  current_primary_key TEXT;
BEGIN
  SELECT conname INTO current_primary_key
  FROM pg_constraint
  WHERE conrelid = 'score_grants'::regclass AND contype = 'p';

  IF current_primary_key IS NOT NULL THEN
    EXECUTE format('ALTER TABLE score_grants DROP CONSTRAINT %I', current_primary_key);
  END IF;
  ALTER TABLE score_grants
    ADD CONSTRAINT score_grants_pkey PRIMARY KEY (id);
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'score_grants'::regclass
      AND conname = 'uq_score_grants_authority'
  ) THEN
    ALTER TABLE score_grants
      ADD CONSTRAINT uq_score_grants_authority UNIQUE (org_id, run_id, digest);
  END IF;
END $$;

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
