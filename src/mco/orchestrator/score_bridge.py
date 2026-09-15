"""Durable read-only score bridge; live gateway create_with_id v1 required.

Local SQLite is a development control plane, not VIA production infrastructure.
No production mutation, budgeted tasks or human-gate authorization supported.
"""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from mco.orchestrator.score_dispatcher import score_job_id
from mco.orchestrator.scores import ScoreError, digest, load_score


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
    def __init__(self, database, artifact_root, clock=time.time):
        self.database = str(database)
        self.clock = clock
        self.root = Path(artifact_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, definition TEXT NOT NULL, digest TEXT NOT NULL, principal TEXT NOT NULL, credential_hash TEXT NOT NULL, org TEXT NOT NULL, targets TEXT NOT NULL, artifact_root TEXT NOT NULL, status TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS dispatch(run TEXT NOT NULL, task TEXT NOT NULL, phase TEXT NOT NULL, job_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT, deadline INTEGER NOT NULL, PRIMARY KEY(run,task,phase))")
            db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL, at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")

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

    def initialize(self, run_id, score, *, principal, org, targets, credential_hash):
        score = load_score(score)
        if not all(isinstance(x, str) and x for x in (run_id, principal, org, credential_hash)):
            raise ScoreError("Explicit identity required")
        if score["budget_cents"]:
            raise ScoreError("Paid execution unsupported")
        for t in score["tasks"]:
            if t["max_attempts"] != 1:
                raise ScoreError("This bridge supports one work attempt; job lease recovery is gateway-owned")
            if t["checkpoint"] or t["max_cost_cents"] or not set(t["capabilities"]) <= {"cloud:inspect", "repository:read", "evidence:write", "evidence:review"}:
                raise ScoreError("Only read-only audit authority supported")
            for role in (t["role"], t["review_role"]):
                if not isinstance(targets.get(role), str) or not targets[role]:
                    raise ScoreError("Explicit worker identity required")
            if targets[t["role"]] == targets[t["review_role"]]:
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
            if run["status"] != "running":
                return []
            score, targets = json.loads(run["definition"]), json.loads(run["targets"])
            rows = {(r["task"], r["phase"]): dict(r) for r in db.execute("SELECT * FROM dispatch WHERE run=?", (run_id,))}
            accepted = {k for (k, phase), r in rows.items() if phase == "review" and r["status"] == "accepted"}
            tasks = {t["id"]: t for t in score["tasks"]}
            active = {k for k, _ in rows if k not in accepted}
            created = []
            for key, t in tasks.items():
                if key in accepted or not set(t["depends_on"]) <= accepted:
                    continue
                work = rows.get((key, "work"))
                if work is None:
                    if len(active) >= score["max_parallel"] or any(set(t["resources"]) & set(tasks[k]["resources"]) for k in active):
                        continue
                    phase = "work"
                elif work["status"] == "validated" and (key, "review") not in rows:
                    phase = "review"
                else:
                    continue
                role = t["role"] if phase == "work" else t["review_role"]
                job_id = score_job_id(run["org"], run_id, run["digest"], key, phase)
                review_of = json.loads(work["evidence"]) if phase == "review" else None
                contract = dict(protocol="score-v1", score_id=score["id"], run_id=run_id, digest=run["digest"], task=key, attempt=1, phase=phase, artifact_root=str(self.root), required_evidence=t["evidence"], review_of=review_of, constraints=score["constraints"])
                prompt = t["instructions"] if phase == "work" else "Independently verify these read-only audit artifacts, hashes, observations and limitations. No cloud or artifact mutations. Pass means an honest evidence-backed audit, NOT launch readiness. Return strict JSON {verdict: pass|fail, review_of: EXACT_CONTRACT_MAP, findings: [strings]}."
                if phase == "work":
                    prompt += " Return strict JSON {artifacts: {required_name: {path: relative_path, sha256: lowercase_digest}}}. Save evidence only beneath artifact_root; no secrets."
                payload = dict(id=job_id, title=f"Score {run_id} {key} {phase}", description=prompt, target_agent_role=role, target_agent_id=targets[role], depends_on=[], input_payload={"prompt": prompt, "score": contract}, max_retries=0, requires_approval=False, priority=0)
                db.execute("INSERT INTO dispatch VALUES(?,?,?,?,?,'planned',NULL,?)", (run_id, key, phase, job_id, encoded(payload), int(self.clock()) + t["timeout_seconds"]))
                self.event(db, run_id, "planned", {"job_id": job_id, "task": key, "phase": phase})
                created.append(job_id)
                active.add(key)
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
            raise ScoreError("Conductor credential changed; explicit reauthorization required")

    def dispatch(self, run_id, board):
        self.check_deadlines(run_id)
        with self.tx() as db:
            run = self.run(db, run_id)
            self.identity(run, board)
            if run["status"] != "running":
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
            if expired and run["status"] == "running":
                db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
                self.event(db, run_id, "deadline_exceeded", {"jobs": expired, "policy": "stop advancement; do not cancel unrelated work or resubmit"})

    def poll(self, run_id, board):
        self.check_deadlines(run_id)
        try:
            self._poll(run_id, board)
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
            if run["status"] != "running":
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
            if row["phase"] == "work":
                evidence = self.artifacts(output.get("artifacts"), contract["required_evidence"])
                status = "validated"
            else:
                evidence = self.artifacts(contract["review_of"], contract["required_evidence"])
                if output.get("review_of") != evidence or output.get("verdict") not in ("pass", "fail") or not isinstance(output.get("findings"), list) or not all(isinstance(x, str) for x in output["findings"]):
                    raise ScoreError("Review not bound to exact evidence")
                status = "accepted" if output["verdict"] == "pass" else "rejected"
            with self.tx() as db:
                if db.execute("UPDATE dispatch SET status=?,evidence=? WHERE job_id=? AND status='submitted'", (status, encoded(evidence), row["job_id"])).rowcount:
                    self.event(db, run_id, status, {"job_id": row["job_id"], "actor": expected, "result": output})
                    if status == "rejected":
                        db.execute("UPDATE runs SET status='blocked' WHERE id=?", (run_id,))
        with self.tx() as db:
            run = self.run(db, run_id)
            accepted = {r[0] for r in db.execute("SELECT task FROM dispatch WHERE run=? AND phase='review' AND status='accepted'", (run_id,))}
            if {t["id"] for t in json.loads(run["definition"])["tasks"]} <= accepted and run["status"] == "running":
                db.execute("UPDATE runs SET status='accepted' WHERE id=?", (run_id,))
                self.event(db, run_id, "score_accepted", {"digest": run["digest"]})

    def status(self, run_id):
        with self.tx() as db:
            run = self.run(db, run_id)
            return dict(run_id=run_id, digest=run["digest"], status=run["status"], scope="read_only_audit_not_product_launch", dispatches=[dict(r) for r in db.execute("SELECT task,phase,job_id,status,evidence FROM dispatch WHERE run=?", (run_id,))], events=[dict(r) for r in db.execute("SELECT * FROM events WHERE run=? ORDER BY seq", (run_id,))])
