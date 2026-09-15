"""Score-owned transactional outbox delivery to the existing job board.

This module deliberately does not interpret worker completion or mutate Score
task state.  A conductor tick owns review-ready/accepted transitions; the
dispatcher owns only deterministic job identity and retry-safe delivery.
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from mco.orchestrator.scores import ScoreError


_PHASES = frozenset({"work", "review"})
_STAMP_FIELDS = (
    "org_id",
    "score_id",
    "run_id",
    "digest",
    "task_id",
    "attempt",
    "phase",
    "job_id",
    "payload",
)


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ScoreError(f"{name} must be a nonempty string")
    return value


def score_job_id(org_id: str, run_id: str, digest: str, task_id: str, phase: str) -> str:
    """Mint the sole job UUID for one Score run/task/phase dispatch."""
    for name, value in (
        ("org_id", org_id),
        ("run_id", run_id),
        ("digest", digest),
        ("task_id", task_id),
        ("phase", phase),
    ):
        _nonempty(name, value)
    if phase not in _PHASES:
        raise ScoreError("phase must be work or review")
    material = f"score-v1:{org_id}:{run_id}:{digest}:{task_id}:{phase}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, material))


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ScoreOutboxDispatcher:
    """Persist immutable Score dispatch intent before contacting the gateway."""

    def __init__(self, db_client: Any):
        self.db = db_client

    @staticmethod
    def _payload(
        payload: dict,
        *,
        job_id: str,
        score_id: str,
        run_id: str,
        digest: str,
        task_id: str,
        attempt: int,
        phase: str,
    ) -> dict:
        if not isinstance(payload, dict):
            raise ScoreError("job payload must be an object")
        value = copy.deepcopy(payload)
        if value.get("id") not in (None, job_id):
            raise ScoreError("job id is minted by the Score outbox")
        if value.get("depends_on") not in (None, []):
            raise ScoreError("Score dependencies cannot use agent_jobs.depends_on")
        input_payload = value.get("input_payload") or {}
        if not isinstance(input_payload, dict):
            raise ScoreError("input_payload must be an object")
        score = input_payload.get("score") or {}
        if not isinstance(score, dict):
            raise ScoreError("input_payload.score must be an object")
        stamps = {
            "protocol": "score-v1",
            "score_id": score_id,
            "run_id": run_id,
            "digest": digest,
            "task": task_id,
            "attempt": attempt,
            "phase": phase,
        }
        for key, expected in stamps.items():
            if key in score and score[key] != expected:
                raise ScoreError(f"conflicting Score stamp: {key}")
        score.update(stamps)
        input_payload["score"] = score
        value.update(id=job_id, depends_on=[], input_payload=input_payload)
        return value

    @staticmethod
    def _same_intent(actual: dict, expected: dict) -> bool:
        return all(
            _encoded(actual.get(key)) == _encoded(expected.get(key))
            for key in _STAMP_FIELDS
        )

    def _by_key(self, org_id: str, run_id: str, task_id: str, phase: str) -> dict | None:
        rows = (
            self.db.table("score_outbox")
            .select("*")
            .eq("org_id", org_id)
            .eq("run_id", run_id)
            .eq("task_id", task_id)
            .eq("phase", phase)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def enqueue(
        self,
        *,
        org_id: str,
        score_id: str,
        run_id: str,
        digest: str,
        task_id: str,
        attempt: int,
        phase: str,
        payload: dict,
    ) -> dict:
        """Create or reconcile one immutable outbox row by its natural key."""
        for name, value in (
            ("org_id", org_id),
            ("score_id", score_id),
            ("run_id", run_id),
            ("digest", digest),
            ("task_id", task_id),
        ):
            _nonempty(name, value)
        if type(attempt) is not int or attempt < 1:
            raise ScoreError("attempt must be an integer >= 1")
        if phase not in _PHASES:
            raise ScoreError("phase must be work or review")

        job_id = score_job_id(org_id, run_id, digest, task_id, phase)
        stamped_payload = self._payload(
            payload,
            job_id=job_id,
            score_id=score_id,
            run_id=run_id,
            digest=digest,
            task_id=task_id,
            attempt=attempt,
            phase=phase,
        )
        expected = {
            "org_id": org_id,
            "score_id": score_id,
            "run_id": run_id,
            "digest": digest,
            "task_id": task_id,
            "attempt": attempt,
            "phase": phase,
            "job_id": job_id,
            "payload": stamped_payload,
            "status": "planned",
        }

        existing = self._by_key(org_id, run_id, task_id, phase)
        if existing:
            if not self._same_intent(existing, expected):
                raise ScoreError("outbox dispatch key collision")
            return existing

        job_owner = (
            self.db.table("score_outbox")
            .select("*")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if job_owner:
            raise ScoreError("Score job id is already owned by another outbox row")
        try:
            result = self.db.table("score_outbox").insert(expected).execute()
        except Exception:
            existing = self._by_key(org_id, run_id, task_id, phase)
            if existing:
                if not self._same_intent(existing, expected):
                    raise ScoreError("outbox dispatch key collision")
                return existing
            raise
        if not result.data:
            raise ScoreError("failed to persist Score outbox row")
        return result.data[0]

    @staticmethod
    def _validate_row(row: dict) -> dict:
        _nonempty("score_id", row.get("score_id"))
        expected_job_id = score_job_id(
            row.get("org_id"),
            row.get("run_id"),
            row.get("digest"),
            row.get("task_id"),
            row.get("phase"),
        )
        if row.get("job_id") != expected_job_id:
            raise ScoreError("outbox job id is not the Score-minted UUID")
        if type(row.get("attempt")) is not int or row["attempt"] < 1:
            raise ScoreError("outbox attempt stamp is missing")
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise ScoreError("outbox payload is not valid JSON") from exc
        expected_payload = ScoreOutboxDispatcher._payload(
            payload,
            job_id=expected_job_id,
            score_id=row.get("score_id"),
            run_id=row.get("run_id"),
            digest=row.get("digest"),
            task_id=row.get("task_id"),
            attempt=row.get("attempt"),
            phase=row.get("phase"),
        )
        if _encoded(payload) != _encoded(expected_payload):
            raise ScoreError("outbox payload is missing immutable Score stamps")
        return payload

    @staticmethod
    def _validate_job(row: dict, job: dict, payload: dict) -> None:
        if not isinstance(job, dict) or job.get("id") != row["job_id"]:
            raise ScoreError("gateway returned the wrong Score job")
        for key in (
            "title",
            "description",
            "target_agent_role",
            "target_agent_id",
            "depends_on",
            "input_payload",
        ):
            if job.get(key) != payload.get(key):
                raise ScoreError("gateway returned conflicting job intent")

    def dispatch(self, board: Any, *, org_id: str | None = None) -> list[str]:
        """Send planned/sending rows; a lost acknowledgement is replayed by ID."""
        if board.capabilities().get("create_with_id") != 1:
            raise ScoreError("retry-safe gateway protocol unavailable")
        query = self.db.table("score_outbox").select("*").in_(
            "status", ["planned", "sending"]
        )
        if org_id is not None:
            query = query.eq("org_id", org_id)
        rows = query.execute().data or []
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
        sent = []
        for row in rows:
            payload = self._validate_row(row)
            if row.get("status") == "planned":
                claimed = (
                    self.db.table("score_outbox")
                    .update({"status": "sending"})
                    .eq("id", row["id"])
                    .eq("status", "planned")
                    .execute()
                    .data
                    or []
                )
                if not claimed:
                    continue
            job = board.create(copy.deepcopy(payload))
            self._validate_job(row, job, payload)
            updated = (
                self.db.table("score_outbox")
                .update({"status": "submitted"})
                .eq("id", row["id"])
                .eq("status", "sending")
                .execute()
                .data
                or []
            )
            if updated:
                sent.append(row["job_id"])
        return sent
