"""
Declarative workflow DSL for the Job Board.

A workflow is a YAML file describing a DAG of steps. Each step becomes one job;
`depends_on` references other step ids and is translated to real job ids at
submit time, reusing the job board's existing dependency machinery (WAITING ->
PENDING unlock). Steps support the same governance controls as single jobs:
approval gates, retry budgets, and escalation roles.

Example:

    name: release-pipeline
    steps:
      - id: research
        role: claude
        title: Research the change
        instructions: Summarize the open issues for the release.
      - id: build
        role: codex
        title: Implement the change
        instructions: Apply the fixes identified by the research step.
        depends_on: [research]
        max_retries: 2
        escalate_to_role: human
      - id: ship
        role: codex
        title: Tag and publish
        instructions: Tag the release and publish artifacts.
        depends_on: [build]
        requires_approval: true
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from mco.orchestrator.client import GatewayClient

logger = logging.getLogger("mco.orchestrator.workflows")


class WorkflowError(ValueError):
    """Raised when a workflow definition is invalid."""


def load_workflow(source: Union[str, Path, dict], allow_path: bool = False) -> dict:
    """Load and validate a workflow from a YAML string/dict, or (only when
    explicitly requested) a local file path.

    `allow_path` defaults to False - reading `source` off the local disk is
    only safe for the trusted CLI operator (`mco workflow <file>`), never for
    `source` sourced from a network request (POST /api/workflows). Without
    this, any caller able to submit a workflow could pass a bare path and
    have the gateway read an arbitrary local file (CWE-22 path injection).
    """
    if isinstance(source, dict):
        workflow = source
    else:
        text = str(source)
        if allow_path and "\n" not in text:
            path = Path(text)
            if path.exists():
                text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)

    if not isinstance(workflow, dict):
        raise WorkflowError("Workflow must be a YAML mapping with 'name' and 'steps'")

    name = workflow.get("name")
    steps = workflow.get("steps")
    if not name:
        raise WorkflowError("Workflow is missing 'name'")
    if not isinstance(steps, list) or not steps:
        raise WorkflowError("Workflow must define a non-empty 'steps' list")

    seen_ids = set()
    for step in steps:
        if not isinstance(step, dict):
            raise WorkflowError("Each step must be a mapping")
        step_id = step.get("id")
        if not step_id:
            raise WorkflowError("Each step requires an 'id'")
        if step_id in seen_ids:
            raise WorkflowError(f"Duplicate step id: {step_id}")
        seen_ids.add(step_id)
        if not step.get("role"):
            raise WorkflowError(f"Step '{step_id}' is missing 'role'")
        if not step.get("title") and not step.get("instructions"):
            raise WorkflowError(f"Step '{step_id}' needs a 'title' or 'instructions'")

    for step in steps:
        for dep in (step.get("depends_on") or []):
            if dep not in seen_ids:
                raise WorkflowError(f"Step '{step['id']}' depends on unknown step '{dep}'")

    topo_order(steps)  # raises on cycles
    return workflow


def topo_order(steps: List[dict]) -> List[dict]:
    """Return steps in dependency order; raise WorkflowError on cycles."""
    by_id = {s["id"]: s for s in steps}
    ordered: List[dict] = []
    state: Dict[str, int] = {}  # 0=unvisited, 1=visiting, 2=done

    def visit(step_id: str) -> None:
        if state.get(step_id) == 2:
            return
        if state.get(step_id) == 1:
            raise WorkflowError(f"Workflow has a dependency cycle involving step '{step_id}'")
        state[step_id] = 1
        for dep in (by_id[step_id].get("depends_on") or []):
            visit(dep)
        state[step_id] = 2
        ordered.append(by_id[step_id])

    for s in steps:
        visit(s["id"])
    return ordered


def submit_workflow(client: GatewayClient, workflow: dict) -> Dict[str, str]:
    """Submit every step as a job (in dependency order) and return {step_id: job_id}.

    Every step is stamped with the same workflow run id
    (input_payload["workflow"] = {name, run, step}). Drumline tags each
    step's handoff with that run, and workers inject the whole thread into
    downstream steps - the Context Exchange that lets a Codex step pick up
    exactly where a Claude step left off.

    Raises WorkflowError if any step fails to create, listing the jobs already
    created so the caller can clean up or resume.
    """
    import uuid

    workflow = load_workflow(workflow)
    name = workflow["name"]
    run_id = uuid.uuid4().hex[:12]
    job_ids: Dict[str, str] = {}

    for step in topo_order(workflow["steps"]):
        step_id = step["id"]
        dep_job_ids = [job_ids[d] for d in (step.get("depends_on") or [])]
        res = client.send(
            to_role=step["role"],
            title=step.get("title") or f"{name}:{step_id}",
            instructions=step.get("instructions") or step.get("title") or "",
            to_instance=step.get("instance"),
            depends_on=dep_job_ids,
            requires_approval=bool(step.get("requires_approval")),
            max_retries=int(step.get("max_retries") or 0),
            escalate_to_role=step.get("escalate_to_role"),
            extra_payload={"workflow": {"name": name, "run": run_id, "step": step_id}},
        )
        job = (res or {}).get("job") or {}
        job_id = job.get("id")
        if not res.get("success") or not job_id:
            raise WorkflowError(
                f"Step '{step_id}' failed to submit (created so far: {job_ids})"
            )
        job_ids[step_id] = job_id
        logger.info(f"Workflow '{name}': step '{step_id}' submitted as job {job_id}")

    return job_ids
