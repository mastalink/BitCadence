"""Durable read-only score bridge; live gateway create_with_id v1 required.

Local SQLite is a development control plane, not VIA production infrastructure.
No production mutation, budgeted tasks or human-gate authorization supported.
"""
import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from mco.orchestrator.score_adapters import Operation
from mco.orchestrator.score_adapters_live import LiveScoreAdapterExecutor, LiveAdapterError, _run_git, verify_not_denied_branch
from mco.orchestrator.score_authority import AuthorityError
from mco.orchestrator.score_dispatcher import score_job_id
from mco.orchestrator.score_policy import TASK_CHECKPOINT
from mco.orchestrator.scores import ScoreError, ScoreIdentityError, digest, load_score

logger = logging.getLogger("mco.orchestrator.score_bridge")


class ScoreAuthorityError(ScoreError, AuthorityError):
    """Authority error encountered during score execution."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class GatewayBoard:
    def __init__(self, client):
        self.client = client
        self.identity = hashlib.sha256(client.token.encode()).hexdigest()

    def request(self, method, path, **kwargs):
        with self.client._client() as http:
            result = http.request(method, path, **kwargs)
            result.raise_for_status()
            return result.json()

    def capabilities(self):
        return self.request("GET", "/api/jobs/capabilities")

    def create(self, payload):
        return self.request("POST", "/api/jobs", json=payload)["job"]

    def get(self, job_id):
        value = self.request("GET", f"/api/jobs/{job_id}")
        return value.get("job", value)

    def events(self, job_id):
        return self.request("GET", f"/api/jobs/{job_id}/events")


class ScoreBridge:
    def __init__(self, database, artifact_root, clock=time.time, gate_service=None, live_executor=None):
        self.database = str(database)
        self.clock = clock
        self.root = Path(artifact_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.gate_service = gate_service
        self.live_executor = live_executor
        with self.tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, definition TEXT NOT NULL, digest TEXT NOT NULL, principal TEXT NOT NULL, credential_hash TEXT NOT NULL, org TEXT NOT NULL, targets TEXT NOT NULL, artifact_root TEXT NOT NULL, status TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS dispatch(run TEXT NOT NULL, task TEXT NOT NULL, phase TEXT NOT NULL, job_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT, deadline INTEGER NOT NULL, PRIMARY KEY(run,task,phase))")
            db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL, at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")

    def get_gate_service(self):
        if self.gate_service is not None:
            return self.gate_service
        try:
            from mco.orchestrator.routes import get_db_client
            from mco.orchestrator.score_policy import GateService
            client = get_db_client()
            if client is not None:
                return GateService(client)
        except Exception:
            pass

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, run, event, detail):
        db.execute("INSERT INTO events(run,event,detail) VALUES(?,?,?)", (run, event, encoded(detail)))

    def run(self, db, run_id):
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ScoreError("Unknown run")
        if row["artifact_root"] != str(self.root):
            raise ScoreError("Run artifact root mismatch")
        return dict(row)

    @staticmethod
    def _lineage_info(tasks, rejected_reviews, task_id):
        tasks_by_id = {t["id"]: t for t in tasks}
        on_reject_targets = {t["on_reject"]: t["id"] for t in tasks if t.get("on_reject")}
        curr = task_id
        while curr in on_reject_targets:
            curr = on_reject_targets[curr]
        root_id = curr
        chain = [root_id]
        node = root_id
        while tasks_by_id[node].get("on_reject"):
            node = tasks_by_id[node]["on_reject"]
            chain.append(node)
        rejections = sum(1 for node in chain if node in rejected_reviews)
        return root_id, chain, rejections

    def initialize(self, run_id, score, *, principal, org, targets, credential_hash):
        score = load_score(score)
        if not all(isinstance(x, str) and x for x in (run_id, principal, org, credential_hash)):
            raise ScoreError("Explicit identity required")
        if score["budget_cents"]:
            raise ScoreError("Paid execution unsupported")
        for t in score["tasks"]:
            if t["max_attempts"] != 1:
                raise ScoreError("This bridge supports one work attempt; job lease recovery is gateway-owned")
            if t["max_cost_cents"]:
                raise ScoreError("Only read-only audit authority supported")

            caps = set(t["capabilities"])
            audit_caps = {"cloud:inspect", "repository:read", "evidence:write", "evidence:review"}
            is_audit = caps <= audit_caps

            has_repo_write = "repository:write" in caps
            repo_write_caps = {"repository:write", "evidence:write", "evidence:review"}
            is_repo_write = has_repo_write and (caps <= repo_write_caps)

            if has_repo_write:
                if self.live_executor is None:
                    raise ScoreError("Only read-only audit authority supported")
                if not is_repo_write:
                    raise ScoreError("repository:write cannot be mixed with audit capabilities")
                commit_conf = t.get("commit")
                if commit_conf is None:
                    raise ScoreError(f"Task '{t['id']}' with repository:write requires commit configuration")
                target_branch = commit_conf.get("target_branch")
                try:
                    verify_not_denied_branch(target_branch)
                except LiveAdapterError as exc:
                    raise ScoreError(f"Denied target branch: {exc}") from exc
                wt_path = commit_conf.get("worktree_path")
                if not wt_path or wt_path not in t["resources"]:
                    raise ScoreError(f"Task '{t['id']}' with repository:write must include worktree_path in resources")
            elif not is_audit:
                raise ScoreError("Only read-only audit authority supported")
            for role in (t["role"], t["review_role"]):
                val = targets.get(role)
                if isinstance(val, str):
                    if not val:
                        raise ScoreError("Explicit worker identity required")
                elif isinstance(val, (list, tuple)):
                    if not val or not all(isinstance(x, str) and x for x in val):
                        raise ScoreError("Explicit worker identity required")
                else:
                    raise ScoreError("Explicit worker identity required")
            author_targets = [targets[t["role"]]] if isinstance(targets[t["role"]], str) else list(targets[t["role"]])
            reviewer_targets = [targets[t["review_role"]]] if isinstance(targets[t["review_role"]], str) else list(targets[t["review_role"]])
            if set(author_targets) & set(reviewer_targets):
                raise ScoreError("Independent identity required")
        values = (run_id, encoded(score), digest(score), principal, credential_hash, org, encoded(targets), str(self.root))
        with self.tx() as db:
            old = db.execute("SELECT id,definition,digest,principal,credential_hash,org,targets,artifact_root FROM runs WHERE id=?", (run_id,)).fetchone()
            if old:
                if tuple(old) != values:
                    raise ScoreError("Existing run policy/identity mismatch")
                return
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,'running')", values)
            self.event(db, run_id, "initialized", {"digest": values[2]})

    def plan(self, run_id):
        self.check_deadlines(run_id)
        with self.tx() as db:
            run = self.run(db, run_id)
            if run["status"] not in ("running", "waiting_on_gate"):
                return []
            score, targets = json.loads(run["definition"]), json.loads(run["targets"])
            rows = {(r["task"], r["phase"]): dict(r) for r in db.execute("SELECT * FROM dispatch WHERE run=?", (run_id,))}
            accepted = {k for (k, phase), r in rows.items() if phase == "review" and r["status"] == "accepted"}
            rejected = {k for (k, phase), r in rows.items() if phase == "review" and r["status"] == "rejected"}
            tasks = {t["id"]: t for t in score["tasks"]}
            active = {k for (k, _) in rows if k not in accepted and k not in rejected}
            created = []
            gate_rejected = False

            on_reject_targets = {t["on_reject"]: t["id"] for t in score["tasks"] if t.get("on_reject")}

            def _lineage_satisfied(dep_id):
                if dep_id in accepted:
                    return True
                curr = dep_id
                while tasks[curr].get("on_reject"):
                    curr = tasks[curr]["on_reject"]
                    if curr in accepted:
                        return True
                return False

            for key, t in tasks.items():
                if key in accepted:
                    continue
                pred_id = on_reject_targets.get(key)
                if pred_id is not None:
                    # Fix attempt task: only run if predecessor review was rejected
                    if pred_id not in rejected:
                        continue
                    other_deps = set(t["depends_on"]) - {pred_id}
                    if not all(_lineage_satisfied(d) for d in other_deps):
                        continue
                    root_id, chain, rejections = self._lineage_info(score["tasks"], rejected, pred_id)
                    if rejections > 3:
                        db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                        continue
                else:
                    if not all(_lineage_satisfied(d) for d in t["depends_on"]):
                        continue

                work = rows.get((key, "work"))

                # Checkpoint gate evaluation for work phase
                if t.get("checkpoint"):
                    checkpoint = t["checkpoint"]
                    gate_kind = TASK_CHECKPOINT
                    gate_service = self.get_gate_service()

                    if gate_service is None:
                        if work is None:
                            job_id = score_job_id(run["org"], run_id, run["digest"], key, "work")
                            contract = dict(protocol="score-v1", score_id=score["id"], run_id=run_id, digest=run["digest"], task=key, attempt=1, phase="work", artifact_root=str(self.root), required_evidence=t["evidence"], review_of=None, constraints=score["constraints"])
                            prompt = t["instructions"] + " Return strict JSON {artifacts: {required_name: {path: relative_path, sha256: lowercase_digest}}}. Save evidence only beneath artifact_root; no secrets."
                            payload = dict(id=job_id, title=f"Score {run_id} {key} work", description=prompt, target_agent_role=t["role"], target_agent_id=targets[t["role"]], depends_on=[], input_payload={"prompt": prompt, "score": contract, "no_reroute": True}, max_retries=0, requires_approval=False, priority=0)
                            db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,'waiting_on_gate',NULL,?)", (run_id, key, "work", job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"]))
                            self.event(db, run_id, "waiting_on_gate", {"task": key, "reason": "no gate service configured"})
                            rows[(key, "work")] = {"task": key, "phase": "work", "job_id": job_id, "status": "waiting_on_gate"}
                            active.add(key)
                        continue

                    evidence = {
                        "checkpoint": checkpoint,
                        "task_id": key,
                        "role": t["role"],
                        "reason": str(checkpoint.get("reason", "Human gate required")),
                    }
                    gate = gate_service.request(
                        org_id=run["org"],
                        run_id=run_id,
                        digest=run["digest"],
                        task_id=key,
                        kind=gate_kind,
                        evidence=evidence,
                    )

                    decision = gate_service.decision_for(
                        org_id=run["org"],
                        run_id=run_id,
                        digest=run["digest"],
                        task_id=key,
                        kind=gate_kind,
                    )

                    if decision and decision.get("decision") == "rejected":
                        job_id = work["job_id"] if work else score_job_id(run["org"], run_id, run["digest"], key, "work")
                        if work is None:
                            contract = dict(protocol="score-v1", score_id=score["id"], run_id=run_id, digest=run["digest"], task=key, attempt=1, phase="work", artifact_root=str(self.root), required_evidence=t["evidence"], review_of=None, constraints=score["constraints"])
                            prompt = t["instructions"]
                            payload = dict(id=job_id, title=f"Score {run_id} {key} work", description=prompt, target_agent_role=t["role"], target_agent_id=targets[t["role"]], depends_on=[], input_payload={"prompt": prompt, "score": contract, "no_reroute": True}, max_retries=0, requires_approval=False, priority=0)
                            db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,'rejected',NULL,?)", (run_id, key, "work", job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"]))
                        else:
                            db.execute("UPDATE dispatch SET status='rejected' WHERE run=? AND task=? AND phase='work'", (run_id, key))
                        db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                        self.event(db, run_id, "gate_rejected", {"task": key, "gate_id": gate["id"], "decision": decision})
                        self.event(db, run_id, "run_blocked", {"reason": f"Gate rejected for task {key}"})
                        gate_rejected = True
                        break

                    if not (decision and decision.get("decision") == "approved"):
                        if work is None:
                            job_id = score_job_id(run["org"], run_id, run["digest"], key, "work")
                            contract = dict(protocol="score-v1", score_id=score["id"], run_id=run_id, digest=run["digest"], task=key, attempt=1, phase="work", artifact_root=str(self.root), required_evidence=t["evidence"], review_of=None, constraints=score["constraints"])
                            prompt = t["instructions"] + " Return strict JSON {artifacts: {required_name: {path: relative_path, sha256: lowercase_digest}}}. Save evidence only beneath artifact_root; no secrets."
                            payload = dict(id=job_id, title=f"Score {run_id} {key} work", description=prompt, target_agent_role=t["role"], target_agent_id=targets[t["role"]], depends_on=[], input_payload={"prompt": prompt, "score": contract, "no_reroute": True}, max_retries=0, requires_approval=False, priority=0)
                            db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,'waiting_on_gate',NULL,?)", (run_id, key, "work", job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"]))
                            self.event(db, run_id, "waiting_on_gate", {"task": key, "gate_id": gate["id"]})
                            rows[(key, "work")] = {"task": key, "phase": "work", "job_id": job_id, "status": "waiting_on_gate"}
                            active.add(key)
                        continue

                    if work is not None and work["status"] == "waiting_on_gate":
                        new_deadline = int(self.clock()) + t["timeout_seconds"]
                        db.execute("UPDATE dispatch SET status='planned', deadline=? WHERE run=? AND task=? AND phase='work' AND status='waiting_on_gate'", (new_deadline, run_id, key))
                        self.event(db, run_id, "gate_approved", {"task": key, "gate_id": gate["id"]})
                        self.event(db, run_id, "planned", {"job_id": work["job_id"], "task": key, "phase": "work"})
                        created.append(work["job_id"])
                        work["status"] = "planned"
                        continue

                rev_row = rows.get((key, "review"))
                if work is None:
                    if len(active) >= score["max_parallel"] or any(set(t["resources"]) & set(tasks[k]["resources"]) for k in active):
                        continue
                    phase = "work"
                elif work["status"] == "validated":
                    if rev_row is None:
                        phase = "review"
                    elif rev_row["status"] == "waiting_on_gate":
                        phase = "review"
                    else:
                        continue
                else:
                    continue

                role = t["role"] if phase == "work" else t["review_role"]
                job_id = score_job_id(run["org"], run_id, run["digest"], key, phase)
                review_of = json.loads(work["evidence"]) if phase == "review" else None

                findings = []
                if pred_id is not None:
                    pred_review = rows.get((pred_id, "review"))
                    if pred_review:
                        for er in db.execute("SELECT detail FROM events WHERE run=? AND event='rejected' ORDER BY seq DESC", (run_id,)):
                            dt = json.loads(er["detail"])
                            if dt.get("job_id") == pred_review["job_id"]:
                                findings = dt.get("result", {}).get("findings", [])
                                break

                contract = dict(protocol="score-v1", score_id=score["id"], run_id=run_id, digest=run["digest"], task=key, attempt=1, phase=phase, artifact_root=str(self.root), required_evidence=t["evidence"], review_of=review_of, constraints=score["constraints"])
                if "repository:write" in t["capabilities"] and phase == "work":
                    expected_sha = (t.get("commit") or {}).get("expected_before_sha")
                    if not expected_sha:
                        wt_path = (t.get("commit") or {}).get("worktree_path")
                        if wt_path:
                            try:
                                proc = _run_git(["rev-parse", "HEAD"], cwd=wt_path)
                                if proc.returncode == 0:
                                    expected_sha = proc.stdout.strip()
                                else:
                                    logger.debug(
                                        "Could not resolve HEAD in %s (exit %d): %s",
                                        wt_path,
                                        proc.returncode,
                                        proc.stderr.strip(),
                                    )
                            except (subprocess.SubprocessError, OSError) as exc:
                                logger.debug("Failed to run git rev-parse HEAD in worktree %s: %s", wt_path, exc)
                    if expected_sha:
                        contract["expected_before_sha"] = expected_sha
                if pred_id is not None:
                    contract["rejection_findings"] = findings

                if phase == "work":
                    prompt = t["instructions"]
                    if findings:
                        prompt += "\nRejection findings to fix:\n" + "\n".join(f"- {f}" for f in findings)
                    if "repository:write" in t["capabilities"]:
                        prompt += " Edit files in the worktree as instructed. When changes are ready, return strict JSON {\"ready\": true}. Do not commit."
                    else:
                        prompt += " Return strict JSON {artifacts: {required_name: {path: relative_path, sha256: lowercase_digest}}}. Save evidence only beneath artifact_root; no secrets."
                else:
                    if "repository:write" in t["capabilities"]:
                        prompt = "Independently verify the repository commit and test evidence. Return strict JSON {verdict: pass|fail, review_of: EXACT_CONTRACT_MAP, findings: [strings]}. Copy review_of exactly from the supplied contract map; do not add, remove, or rename keys."
                    else:
                        prompt = "Independently verify these read-only audit artifacts, hashes, observations and limitations. No cloud or artifact mutations. Pass means an honest evidence-backed audit, NOT launch readiness. Return strict JSON {verdict: pass|fail, review_of: EXACT_CONTRACT_MAP, findings: [strings]}. Copy review_of exactly from the supplied contract map; do not add, remove, or rename keys."

                if phase == "review":
                    author_id = json.loads(work["payload"])["target_agent_id"]
                    excluded_reviewers = {author_id}
                    curr_pred = pred_id
                    while curr_pred:
                        p_rev = rows.get((curr_pred, "review"))
                        if p_rev:
                            p_reviewer = json.loads(p_rev["payload"])["target_agent_id"]
                            excluded_reviewers.add(p_reviewer)
                        curr_pred = on_reject_targets.get(curr_pred)

                    candidate_targets = targets.get(role)
                    if isinstance(candidate_targets, str):
                        candidates = [candidate_targets]
                    elif isinstance(candidate_targets, (list, tuple)):
                        candidates = list(candidate_targets)
                    else:
                        candidates = []

                    eligible_candidates = [c for c in candidates if c not in excluded_reviewers]

                    if not eligible_candidates:
                        if rev_row is None:
                            payload = dict(
                                id=job_id,
                                title=f"Score {run_id} {key} {phase}",
                                description=prompt,
                                target_agent_role=role,
                                target_agent_id=candidates[0] if candidates else "",
                                depends_on=[],
                                input_payload={"prompt": prompt, "score": contract, "no_reroute": True},
                                max_retries=0,
                                requires_approval=False,
                                priority=0,
                            )
                            db.execute(
                                "INSERT INTO dispatch VALUES(?,?,?,?,?,'waiting_on_gate',NULL,?)",
                                (run_id, key, phase, job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"])
                            )
                            self.event(db, run_id, "waiting_on_gate", {
                                "task": key,
                                "phase": phase,
                                "reason": "no eligible reviewer available",
                                "excluded_reviewers": sorted(excluded_reviewers),
                            })
                            rows[(key, phase)] = {"task": key, "phase": phase, "job_id": job_id, "status": "waiting_on_gate"}
                            active.add(key)
                        continue

                    target_agent_id = eligible_candidates[0]
                    if rev_row is not None and rev_row["status"] == "waiting_on_gate":
                        payload = dict(
                            id=job_id,
                            title=f"Score {run_id} {key} {phase}",
                            description=prompt,
                            target_agent_role=role,
                            target_agent_id=target_agent_id,
                            depends_on=[],
                            input_payload={"prompt": prompt, "score": contract, "no_reroute": True},
                            max_retries=0,
                            requires_approval=False,
                            priority=0,
                        )
                        new_deadline = int(self.clock()) + t["timeout_seconds"]
                        db.execute(
                            "UPDATE dispatch SET status='planned', payload=?, deadline=? WHERE run=? AND task=? AND phase='review' AND status='waiting_on_gate'",
                            (encoded(payload), new_deadline, run_id, key)
                        )
                        self.event(db, run_id, "planned", {"job_id": job_id, "task": key, "phase": phase})
                        created.append(job_id)
                        rev_row["status"] = "planned"
                        continue
                else:
                    target_agent_id = targets[role][0] if isinstance(targets[role], (list, tuple)) else targets[role]

                payload = dict(id=job_id, title=f"Score {run_id} {key} {phase}", description=prompt, target_agent_role=role, target_agent_id=target_agent_id, depends_on=[], input_payload={"prompt": prompt, "score": contract, "no_reroute": True}, max_retries=0, requires_approval=False, priority=0)
                db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,'planned',NULL,?)", (run_id, key, phase, job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"]))
                self.event(db, run_id, "planned", {"job_id": job_id, "task": key, "phase": phase})
                created.append(job_id)
                active.add(key)

            if not gate_rejected:
                dispatches = [dict(r) for r in db.execute("SELECT task, phase, status FROM dispatch WHERE run=?", (run_id,))]
                has_waiting = any(r["status"] == "waiting_on_gate" for r in dispatches)
                reviews_by_task = {r["task"]: r["status"] for r in dispatches if r["phase"] == "review"}
                has_active = any(
                    r["status"] in ("planned", "sending", "submitted") or
                    (r["phase"] == "work" and r["status"] == "validated" and r["task"] not in reviews_by_task)
                    for r in dispatches
                )
                if has_waiting and not has_active:
                    db.execute("UPDATE runs SET status='waiting_on_gate' WHERE id=? AND status='running'", (run_id,))
                elif not has_waiting or has_active:
                    db.execute("UPDATE runs SET status='running' WHERE id=? AND status='waiting_on_gate'", (run_id,))

            return created

    @staticmethod
    def validate_job(run, row, job):
        payload = json.loads(row["payload"])
        if job.get("id") != row["job_id"] or job.get("source_agent_id") != run["principal"] or job.get("org_id", "default") != run["org"]:
            raise ScoreError("Job tenant/creator identity mismatch")
        for key in ("title", "description", "target_agent_role", "target_agent_id", "input_payload"):
            if job.get(key) != payload[key]:
                raise ScoreError("Immutable job intent mismatch")

    @staticmethod
    def identity(run, board):
        if board.identity != run["credential_hash"]:
            raise ScoreIdentityError(
                "Conductor credential changed; explicit reauthorization required")

    def dispatch(self, run_id, board):
        self.check_deadlines(run_id)
        with self.tx() as db:
            run = self.run(db, run_id)
            self.identity(run, board)
            if run["status"] not in ("running", "waiting_on_gate"):
                return []
        if board.capabilities().get("create_with_id") != 1:
            raise ScoreError("Retry-safe gateway protocol unavailable")
        with self.tx() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM dispatch WHERE run=? AND status IN ('planned','sending')", (run_id,))]
            for row in rows:
                db.execute("UPDATE dispatch SET status='sending' WHERE job_id=?", (row["job_id"],))
        sent = []
        for row in rows:
            job = board.create(json.loads(row["payload"]))
            self.validate_job(run, row, job)
            with self.tx() as db:
                if db.execute("UPDATE dispatch SET status='submitted' WHERE job_id=? AND status='sending'", (row["job_id"],)).rowcount:
                    self.event(db, run_id, "submitted", {"job_id": row["job_id"]})
            sent.append(row["job_id"])
        return sent

    def artifacts(self, artifacts, required):
        if not isinstance(artifacts, dict) or set(artifacts) != set(required):
            raise ScoreError("Missing/extra evidence")
        for ref in artifacts.values():
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256"} or not isinstance(ref["path"], str) or Path(ref["path"]).is_absolute():
                raise ScoreError("Invalid relative artifact reference")
            if not isinstance(ref["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]):
                raise ScoreError("Invalid artifact SHA-256")
            path = (self.root / ref["path"]).resolve()
            if self.root not in path.parents or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ScoreError("Evidence missing/outside root/oversized")
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise ScoreError("Evidence digest mismatch")
        return artifacts

    def check_deadlines(self, run_id):
        with self.tx() as db:
            run = self.run(db, run_id)
            expired = [r[0] for r in db.execute("SELECT job_id FROM dispatch WHERE run=? AND status IN ('planned','sending','submitted') AND deadline<=?", (run_id, int(self.clock())))]
            if expired and run["status"] in ("running", "waiting_on_gate"):
                db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                self.event(db, run_id, "deadline_exceeded", {"jobs": expired, "policy": "stop advancement; do not cancel unrelated work or resubmit"})

    def poll(self, run_id, board):
        self.check_deadlines(run_id)
        try:
            self._poll(run_id, board)
        except ScoreIdentityError:
            # Not ours to advance, so not ours to block either. Whether a
            # credential change stops a run durably is the caller's decision -
            # a typed tick says yes, the automatic sweep says no - and blocking
            # it here would take that decision away from both of them.
            raise
        except ScoreError as exc:
            with self.tx() as db:
                self.run(db, run_id)
                db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                self.event(db, run_id, "validation_blocked", {"reason": str(exc)})
            raise

    def _poll(self, run_id, board):
        with self.tx() as db:
            run = self.run(db, run_id)
            self.identity(run, board)
            if run["status"] not in ("running", "waiting_on_gate"):
                return
            rows = [dict(r) for r in db.execute("SELECT * FROM dispatch WHERE run=? AND status='submitted'", (run_id,))]
        for row in rows:
            job = board.get(row["job_id"])
            self.validate_job(run, row, job)
            if job.get("status") in ("failed", "rejected", "cancelled", "halted"):
                with self.tx() as db:
                    db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                    self.event(db, run_id, "job_blocked", {"id": row["job_id"], "status": job["status"]})
                return
            if job.get("status") != "completed":
                continue
            payload = json.loads(row["payload"])
            expected = payload["target_agent_id"]
            if job.get("leased_by_instance_id") != expected or not any(e.get("job_id") == row["job_id"] and e.get("event") == "status:completed" and e.get("actor_id") == expected and e.get("actor_role") == payload["target_agent_role"] for e in board.events(row["job_id"])):
                raise ScoreError("Missing authenticated lease/completion identity")
            try:
                output = json.loads(job["output_payload"]["result"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ScoreError("Strict JSON result required") from exc
            if not isinstance(output, dict):
                raise ScoreError("JSON result must be an object")
            contract = payload["input_payload"]["score"]
            tasks = json.loads(run["definition"])["tasks"]
            task_id = row["task"]
            task_def = next((t for t in tasks if t["id"] == task_id), None)
            if task_def is None:
                raise ScoreError(f"Task {task_id} not found in score definition")
            is_repo_write = "repository:write" in task_def["capabilities"]

            if row["phase"] == "work":
                if is_repo_write:
                    if not (output.get("ready") is True or output.get("status") in ("ready", "changes_ready")):
                        raise ScoreError("Worker must signal changes are ready")
                    if self.live_executor is None:
                        raise ScoreError("Live executor required for repository:write task")
                    executor = self.live_executor

                    commit_conf = task_def["commit"]
                    wt_path = commit_conf["worktree_path"]
                    target_branch = commit_conf["target_branch"]
                    allowed_paths = commit_conf["allowed_paths"]
                    commit_message = commit_conf.get("commit_message") or f"Score {run_id} {task_id}: {task_def['title']}"
                    expected_before_sha = commit_conf.get("expected_before_sha") or contract.get("expected_before_sha")
                    if not expected_before_sha:
                        proc = _run_git(["rev-parse", "HEAD"], cwd=wt_path)
                        if proc.returncode != 0:
                            raise ScoreError(f"Failed to resolve HEAD in worktree: {proc.stderr.strip()}")
                        expected_before_sha = proc.stdout.strip()

                    resource = commit_conf["worktree_path"]
                    env = "test"
                    owner_principal = run["principal"]
                    if hasattr(executor, "grants") and executor.grants is not None:
                        try:
                            grant = executor.grants.load(org_id=run["org"], run_id=run_id, digest=run["digest"])
                            env = grant.get("env", "test")
                            grant_resources = grant.get("resources", [])
                            if resource not in grant_resources:
                                for r in task_def.get("resources", []):
                                    if r in grant_resources:
                                        resource = r
                                        break
                            if grant.get("human_principal"):
                                owner_principal = grant["human_principal"]
                        except AuthorityError as exc:
                            if "issued_grant_not_found" in str(exc):
                                logger.debug("No issued grant found for run %s: using defaults", run_id)
                            else:
                                logger.warning("Authority error loading grant for run %s: %s", run_id, exc)
                                raise ScoreAuthorityError(f"Invalid authority grant: {exc}") from exc
                        except Exception as exc:
                            logger.error("Unexpected error loading grant for run %s: %s", run_id, exc)
                            raise ScoreError(f"Unexpected error loading grant: {exc}") from exc

                    op = Operation(
                        org_id=run["org"],
                        run_id=run_id,
                        digest=run["digest"],
                        task_id=task_id,
                        attempt=1,
                        adapter="repository-worktree-commit",
                        action="repository:write",
                        resource=resource,
                        environment=env,
                        owner_principal=owner_principal,
                        desired_state={
                            "worktree_path": str(wt_path),
                            "target_branch": str(target_branch),
                            "allowed_paths": list(allowed_paths),
                            "commit_message": str(commit_message),
                            "expected_before_sha": str(expected_before_sha),
                        },
                        cost_cents=0,
                        dry_run=False,
                    )
                    try:
                        receipt = executor.execute(op)
                    except Exception as exc:
                        with self.tx() as db:
                            db.execute("UPDATE dispatch SET status='failed' WHERE job_id=?", (row["job_id"],))
                            self.event(db, run_id, "adapter_failed", {"job_id": row["job_id"], "task": task_id, "error": str(exc)})
                        raise ScoreError(f"Adapter execution failed: {exc}") from exc

                    if receipt.get("status") != "succeeded":
                        with self.tx() as db:
                            db.execute("UPDATE dispatch SET status='failed' WHERE job_id=?", (row["job_id"],))
                        raise ScoreError(f"Adapter commit failed: {receipt.get('detail')}")

                    new_sha = receipt["observed_state"]["commit_sha"]
                    evidence = {}
                    for req in task_def["evidence"]:
                        if req in receipt["observed_state"]:
                            evidence[req] = receipt["observed_state"][req]
                        elif req == "commit":
                            evidence[req] = {"sha": new_sha, "target_branch": str(target_branch)}
                        else:
                            evidence[req] = new_sha
                    if not evidence:
                        evidence = {"commit_sha": new_sha}
                    status = "validated"
                else:
                    evidence = self.artifacts(output.get("artifacts"), contract["required_evidence"])
                    status = "validated"
            else:
                if is_repo_write:
                    evidence = contract["review_of"]
                    if not isinstance(evidence, dict) or set(evidence) != set(contract["required_evidence"]):
                        raise ScoreError("Review evidence mismatch")
                    if output.get("review_of") != evidence or output.get("verdict") not in ("pass", "fail") or not isinstance(output.get("findings"), list) or not all(isinstance(x, str) for x in output["findings"]):
                        raise ScoreError("Review not bound to exact evidence")
                    status = "accepted" if output["verdict"] == "pass" else "rejected"
                else:
                    evidence = self.artifacts(contract["review_of"], contract["required_evidence"])
                    if output.get("review_of") != evidence or output.get("verdict") not in ("pass", "fail") or not isinstance(output.get("findings"), list) or not all(isinstance(x, str) for x in output["findings"]):
                        raise ScoreError("Review not bound to exact evidence")
                    status = "accepted" if output["verdict"] == "pass" else "rejected"
            with self.tx() as db:
                if db.execute("UPDATE dispatch SET status=?,evidence=? WHERE job_id=? AND status='submitted'", (status, encoded(evidence), row["job_id"])).rowcount:
                    self.event(db, run_id, status, {"job_id": row["job_id"], "actor": expected, "result": output})
                    if status == "rejected":
                        tasks = json.loads(run["definition"])["tasks"]
                        task_id = row["task"]
                        task_def = next((t for t in tasks if t["id"] == task_id), None)
                        has_on_reject = bool(task_def and task_def.get("on_reject"))
                        rejected_reviews = {r["task"] for r in db.execute("SELECT task FROM dispatch WHERE run=? AND phase='review' AND status='rejected'", (run_id,))}
                        root_id, chain, rejections = self._lineage_info(tasks, rejected_reviews, task_id)
                        if has_on_reject and rejections <= 3:
                            self.event(db, run_id, "fix_attempt_ready", {
                                "original_task": root_id,
                                "rejected_task": task_id,
                                "fix_task": task_def["on_reject"],
                                "fix_attempt": rejections,
                            })
                        else:
                            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
        with self.tx() as db:
            run = self.run(db, run_id)
            accepted = {r[0] for r in db.execute("SELECT task FROM dispatch WHERE run=? AND phase='review' AND status='accepted'", (run_id,))}
            tasks = json.loads(run["definition"])["tasks"]
            on_reject_targets = {t["on_reject"] for t in tasks if t.get("on_reject")}
            root_tasks = [t for t in tasks if t["id"] not in on_reject_targets]
            tasks_by_id = {t["id"]: t for t in tasks}
            lineages = []
            for root in root_tasks:
                lineage = [root["id"]]
                curr = root["id"]
                while tasks_by_id[curr].get("on_reject"):
                    curr = tasks_by_id[curr]["on_reject"]
                    lineage.append(curr)
                lineages.append(lineage)
            all_accepted = all(any(tid in accepted for tid in lin) for lin in lineages)
            if all_accepted and run["status"] in ("running", "waiting_on_gate"):
                db.execute("UPDATE runs SET status='accepted' WHERE id=?", (run_id,))
                self.event(db, run_id, "score_accepted", {"digest": run["digest"]})

    def status(self, run_id):
        with self.tx() as db:
            run = self.run(db, run_id)
            dispatches = [dict(r) for r in db.execute("SELECT task,phase,job_id,status,evidence FROM dispatch WHERE run=?", (run_id,))]
            waiting_on_gate = [r["task"] for r in dispatches if r["status"] == "waiting_on_gate"]
            return dict(
                run_id=run_id,
                digest=run["digest"],
                status=run["status"],
                scope="read_only_audit_not_product_launch",
                gated=bool(waiting_on_gate),
                waiting_on_gate=waiting_on_gate,
                dispatches=dispatches,
                events=[dict(r) for r in db.execute("SELECT * FROM events WHERE run=? ORDER BY seq", (run_id,))]
            )
