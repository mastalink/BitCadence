-- Retry-safe caller-assigned UUIDs for POST /api/jobs.
--
-- The UUID primary key atomically chooses the sole creator. This digest binds
-- that ID to the authenticated caller and immutable request so a retry can
-- return the existing row without producing duplicate work or side effects.

ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS create_intent_hash TEXT;
