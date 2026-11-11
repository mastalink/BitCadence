"""Local owner-approved Score classes. No wildcard authority or cached grants.

Only the trusted human CLI issues/revokes these records. A class is an exact
Score digest, including constraints, budgets, capabilities and instructions.
"""
from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timezone

from mco.localstore import LocalStore
from mco.orchestrator.score_authority import AuthorityError, sign_grant, verify_grant_signature
from mco.orchestrator.scores import digest, load_score


ALLOWED_ACTIONS = frozenset({"repository:read", "repository:write", "evidence:write", "evidence:review", "cloud:inspect"})
ALLOWED_ENVIRONMENTS = frozenset({"test", "development", "staging"})


class StandingGrants:
    def __init__(self, db, key):
        if not isinstance(db, LocalStore):
            raise AuthorityError("standing_grants_require_local_store")
        self.db, self.key = db, key

    def event(self, kind, *, org_id, grant_id=None, run_id=None, digest=None,
              principal=None, reason=None, request=None):
        self.db.table("score_standing_events").insert({
            "id": str(uuid.uuid4()), "kind": kind, "org_id": org_id,
            "grant_id": grant_id, "run_id": run_id, "digest": digest,
            "principal": principal, "reason": reason,
            "request": request,
            "at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    @staticmethod
    def check_stop():
        from mco.orchestrator.routes import kill_switch_active
        if kill_switch_active():
            raise AuthorityError("standing_grant_STOP_active")

    def issue(self, *, score, org_id, human_principal, environment, not_before, expires_at):
        self.check_stop()
        score = load_score(score)
        actions = sorted({a for task in score["tasks"] for a in task["capabilities"]})
        if not set(actions) <= ALLOWED_ACTIONS:
            raise AuthorityError("standing_capability_forbidden")
        if environment not in ALLOWED_ENVIRONMENTS:
            raise AuthorityError("standing_environment_forbidden")
        if any(not isinstance(v, str) or not v.strip() for v in (org_id, human_principal)):
            raise AuthorityError("standing_principal_and_org_required")
        try:
            start = datetime.fromisoformat(str(not_before).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            valid = (start.tzinfo is not None and end.tzinfo is not None
                     and end > start and (end - start).total_seconds() <= 316 * 3600
                     and end > datetime.now(timezone.utc))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise AuthorityError("malformed_standing_time_window")
        grant_id = str(uuid.uuid4())
        signed = sign_grant({
            "org_id": org_id, "run_id": "standing:" + grant_id,
            "digest": digest(score), "actions": actions,
            "resources": sorted({r for t in score["tasks"] for r in t["resources"]}),
            "env": environment, "not_before": start.isoformat(), "expires_at": end.isoformat(),
            "budget_cents": score["budget_cents"], "human_principal": human_principal,
        }, self.key)
        with self.db.transaction():
            row = self.db.table("score_standing_grants").insert({"id": grant_id, "org_id": org_id,
                "digest": signed["digest"], "grant": signed}).execute().data[0]
            self.event("issued", org_id=org_id, grant_id=grant_id, digest=signed["digest"],
                       principal=human_principal)
        return row

    def revoke(self, grant_id, *, org_id, human_principal):
        # Revocation stays available under STOP and after expiry.
        with self.db.transaction():
            rows = self.db.table("score_standing_grants").select("*").eq("id", grant_id).eq("org_id", org_id).execute().data
            if len(rows) != 1:
                raise AuthorityError("standing_grant_not_found")
            verify_grant_signature(rows[0]["grant"], self.key)
            if rows[0]["grant"]["human_principal"] != human_principal:
                raise AuthorityError("standing_grant_not_approved_by_owner")
            prior = self.db.table("score_standing_revocations").select("*").eq("id", grant_id).execute().data
            if prior:
                return prior[0]
            saved = self.db.table("score_standing_revocations").insert({
                "id": grant_id, "org_id": org_id, "human_principal": human_principal,
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            }).execute().data[0]
            self.event("revoked", org_id=org_id, grant_id=grant_id, digest=rows[0]["digest"], principal=human_principal)
            return saved

    def load(self, *, org_id, run_id, digest):
        self.check_stop()
        binding = self.db.table("score_standing_runs").select("*").eq("id", self.run_key(org_id, run_id, digest)).execute().data
        query = self.db.table("score_standing_grants").select("*").eq("org_id", org_id).eq("digest", digest)
        if binding:
            query = query.eq("id", binding[0]["grant_id"])
        rows = query.order("created_at").execute().data
        # Newest approval takes precedence; never fall back past a revocation.
        if not rows:
            raise AuthorityError("issued_grant_not_found")
        row = rows[-1]
        template = row["grant"]
        verify_grant_signature(template, self.key)
        if (template["org_id"] != org_id or template["digest"] != digest
                or template["run_id"] != "standing:" + row["id"]):
            raise AuthorityError("standing_grant_binding_invalid")
        if self.db.table("score_standing_revocations").select("*").eq("id", row["id"]).execute().data:
            raise AuthorityError("standing_grant_revoked")
        if not set(template["actions"]) <= ALLOWED_ACTIONS or template["env"] not in ALLOWED_ENVIRONMENTS:
            raise AuthorityError("standing_scope_forbidden")
        # Ephemeral, signed run binding. Never persist to score_grants: doing so
        # would leave reusable authority after revocation of the parent.
        return sign_grant(dict(template, run_id=run_id, standing_grant_id=row["id"]), self.key)

    @staticmethod
    def run_key(org_id, run_id, digest):
        return hashlib.sha256(json.dumps([org_id, run_id, digest], separators=(",", ":")).encode()).hexdigest()

    def pin(self, grant):
        """Serialize first use against revocation and concurrent approvals.

        A run cannot silently adopt newer authority after its parent is revoked.
        This stores a reference, never a detached grant.
        """
        scope = {field: grant[field] for field in ("org_id", "run_id", "digest")}
        with self.db.transaction():
            current = self.load(**scope)
            if current["standing_grant_id"] != grant["standing_grant_id"]:
                raise AuthorityError("standing_grant_changed_during_authorization")
            key = self.run_key(**scope)
            if not self.db.table("score_standing_runs").select("*").eq("id", key).execute().data:
                self.db.table("score_standing_runs").insert(dict(scope, id=key,
                    grant_id=grant["standing_grant_id"])).execute()
                self.event("run_bound", **scope, grant_id=grant["standing_grant_id"])
