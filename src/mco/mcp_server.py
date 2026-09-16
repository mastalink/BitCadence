"""
MCO dropbox as an MCP stdio server.

Lets an IDE/agent (Claude, Codex, Antigravity) work the dropbox on its own
scheduler instead of running a `mco listen` daemon. Identity comes from env
(MCO_AGENT_TOKEN / AGENT_ROLE / AGENT_INSTANCE_ID / MCO_GATEWAY_URL).

IMPORTANT: stdio is the MCP transport — never print to stdout here.
"""

from typing import List

try:
    # mcp >= 2.0 (the 2026-07-28 spec): FastMCP was replaced by MCPServer.
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - depends on the installed SDK major
    # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from mco.orchestrator.client import GatewayClient

# Both classes take the server name positionally and expose .tool()/.run(), so
# every tool below is written once and works on either SDK major. Verified
# against mcp 2.0.0: all 21 tools register and their input schemas (including
# required-vs-defaulted args) come out identical.
mcp = _Server("mco")


_clients = {}

def _client() -> GatewayClient:
    # Preserve attempt proofs across MCP calls. Each HTTP request still uses
    # its own connection. Changing credentials selects a different client.
    import os
    key = tuple(os.environ.get(k, "") for k in ("MCO_GATEWAY_URL", "MCO_AGENT_TOKEN", "AGENT_ROLE", "AGENT_INSTANCE_ID"))
    if key not in _clients:
        _clients[key] = GatewayClient()
    return _clients[key]


@mcp.tool()
def mco_inbox() -> List[dict]:
    """List the jobs/messages addressed to you (your dropbox) that are pending."""
    client = _client()
    client.flush_reports()
    return client.inbox()


@mcp.tool()
def mco_lease_next(estimated_seconds: int = 0) -> dict:
    """Lease the highest-priority job addressed to you, chosen by the server.

    Prefer this over mco_inbox + mco_lease. The server applies the priority
    order and hands you exactly one job, so urgent work cannot be read past,
    and two workers on the same role cannot collide on the same entry.
    Returns {"success": true, "job": {...}, "lease": {...}} or
    {"success": false, "job": null} when nothing is waiting for you.

    estimated_seconds: how long you expect this job to take, if you have a
    sense of it (a quick fix vs. a full review with a fresh pytest run). The
    server adds a buffer on top and uses that as this lease's time-to-live, so
    careful work is not silently reclaimed out from under you mid-task. Omit
    it (0) for the default TTL, currently one hour. If you turn out to need
    more time than you get, call mco_renew with a fresh estimate rather than
    racing a clock you cannot see - an expired lease is reassigned, not
    waited for."""
    client = _client()
    client.flush_reports()
    return client.lease_next(estimated_seconds or None)


@mcp.tool()
def mco_lease(task_id: str, estimated_seconds: int = 0) -> dict:
    """Atomically claim a job before working it. Returns success and the lease proof; renew during long work.

    estimated_seconds: see mco_lease_next - same effect, same default."""
    return _client().lease(task_id, estimated_seconds or None)


@mcp.tool()
def mco_complete(task_id: str, output: str, summary: str = "",
                 decisions: str = "", files: str = "", gotchas: str = "",
                 follow_ups: str = "") -> dict:
    """Mark a leased job completed and attach its result text.

    The optional fields are the structured handoff for the next agent
    (Context Exchange): summary (one paragraph), decisions / files /
    gotchas / follow_ups (newline-separated lists). Fill them in - a
    deliberate handoff beats heuristic extraction and is what downstream
    workflow steps (any vendor) receive as their WORKFLOW THREAD context."""
    handoff = {
        "summary": summary, "decisions": decisions, "files": files,
        "gotchas": gotchas, "follow_ups": follow_ups,
    }
    handoff = {k: v for k, v in handoff.items() if v and v.strip()}
    return _client().complete(task_id, output, handoff=handoff or None)


@mcp.tool()
def mco_fail(task_id: str, error: str) -> dict:
    """Mark a leased job failed with an error message."""
    return _client().fail(task_id, error)


@mcp.tool()
def mco_send(to_role: str, title: str, instructions: str, to_instance: str = "",
             requires_approval: bool = False, max_retries: int = 0,
             escalate_to_role: str = "", priority: int = 0,
             expects_successor: bool = False) -> dict:
    """Drop a task/message into another agent's dropbox. to_instance is optional
    (omit to address the whole role). Set requires_approval=True to pause the job
    at a human approval gate; max_retries/escalate_to_role control what happens
    when the job fails. priority (higher first, default 0) jumps the queue:
    workers take the first job in their inbox, so an urgent job sent to a role
    with an existing backlog needs one to be picked up next.

    Set expects_successor=True when this job is one link in a chain and its
    worker owes a hand-off to the next agent. If that worker completes without
    creating a follow-up job, the gateway records a chain_stalled event and
    hands the stall to MCO_CHAIN_STALL_TO_ROLE - without it, a chain that
    forgets to dispatch leaves an empty board that looks perfectly healthy."""
    extra = {"expects_successor": True} if expects_successor else None
    return _client().send(to_role, title, instructions, to_instance or None,
                          requires_approval=requires_approval, max_retries=max_retries,
                          escalate_to_role=escalate_to_role or None,
                          priority=priority, extra_payload=extra)


@mcp.tool()
def mco_approve(task_id: str) -> dict:
    """Approve a job paused at the human-in-the-loop gate, releasing it for
    execution. Only approver roles (MCO_APPROVER_ROLES) may call this."""
    return _client().approve(task_id)


@mcp.tool()
def mco_reject(task_id: str, reason: str = "") -> dict:
    """Reject a job paused at the human-in-the-loop gate (terminal). Only
    approver roles (MCO_APPROVER_ROLES) may call this."""
    return _client().reject(task_id, reason)


@mcp.tool()
def mco_audit(task_id: str) -> List[dict]:
    """Read a job's immutable audit trail (create/lease/status/approval/retry/
    escalation events), oldest first."""
    return _client().events(task_id)


@mcp.tool()
def mco_retry(task_id: str) -> dict:
    """Re-queue a failed or rejected job back to pending (human override).
    Only approver roles (MCO_APPROVER_ROLES) may call this."""
    return _client().retry(task_id)


@mcp.tool()
def mco_jobs(include_archived: bool = False) -> List[dict]:
    """List the most recent jobs on the board (any status, your org only).
    Archived jobs are hidden unless include_archived=True."""
    return _client().jobs(include_archived=include_archived)


@mcp.tool()
def mco_cancel(task_id: str, reason: str = "") -> dict:
    """Call off a job that hasn't finished yet (waiting/needs_approval/
    pending/leased/in_progress -> cancelled). Unlike reject, this works on
    any non-terminal job, not just ones paused at an approval gate. Only
    approver roles (MCO_APPROVER_ROLES) may call this."""
    return _client().cancel(task_id, reason)


@mcp.tool()
def mco_archive(task_id: str) -> dict:
    """Hide a terminal job (completed/failed/rejected/cancelled) from the
    default board view. Reversible via mco_unarchive; the job's status and
    audit trail are untouched. Any authenticated agent may archive."""
    return _client().archive(task_id)


@mcp.tool()
def mco_unarchive(task_id: str) -> dict:
    """Undo mco_archive - brings a job back into the default board view."""
    return _client().unarchive(task_id)


@mcp.tool()
def mco_duplicates(task_id: str) -> List[dict]:
    """Check for other jobs that look like the same work: same title+target
    role, or already linked to this one via reassignment. Use before manually
    reposting a failed job, or to answer 'did someone already redo this?'"""
    return _client().duplicates(task_id)


@mcp.tool()
def mco_reassign(task_id: str, target_agent_role: str, target_agent_id: str = "",
                 instructions: str = "", title: str = "") -> dict:
    """Redo a failed/rejected/cancelled job with a different target (or the
    same target, for a plain retry-elsewhere). Unlike mco_retry, which
    re-queues the SAME job row, this clones a NEW job, links both rows to
    each other (so 'was a replacement done?' always has an answer), and
    auto-archives the old one. Leave instructions/title blank to reuse the
    original job's. Only approver roles (MCO_APPROVER_ROLES) may call this."""
    return _client().reassign(
        task_id, target_agent_role,
        target_agent_id=target_agent_id or None,
        instructions=instructions or None,
        title=title or None,
    )


@mcp.tool()
def mco_agents() -> List[dict]:
    """List registered agents and their online/offline presence."""
    return _client().agents()


@mcp.tool()
def mco_recall(query: str = "", tags: str = "", limit: int = 5) -> List[dict]:
    """Dip into Drumline, the mesh's shared context: recall the most relevant
    facts/decisions/lessons/handoffs recorded by any agent or distilled from
    completed jobs. Call this before starting non-trivial work."""
    tag_list = [t for t in tags.split(",") if t.strip()] if tags else None
    return _client().recall(query, tags=tag_list, limit=limit)


@mcp.tool()
def mco_remember(title: str, content: str, kind: str = "fact", tags: str = "") -> dict:
    """Write to Drumline, the mesh's shared context, for every agent downstream.
    kind: fact | decision | lesson | handoff | artifact. Record durable
    knowledge (decisions made, gotchas found, environment facts) - not chatter."""
    tag_list = [t for t in tags.split(",") if t.strip()] if tags else []
    return _client().remember(title, content, kind=kind, tags=tag_list)


@mcp.tool()
def mco_integrations() -> List[dict]:
    """List configured enterprise connectors (ServiceNow, Dynatrace, ...) with
    health status and the platform actions each one supports."""
    return _client().integrations()


@mcp.tool()
def mco_sync_connector(name: str) -> dict:
    """Pull open platform objects (ServiceNow incidents / Dynatrace problems)
    onto the job board as agent jobs. Idempotent - already-ingested objects are
    skipped via their external_id."""
    return _client().sync_connector(name)


@mcp.tool()
def mco_platform_action(name: str, action: str, params: dict = None) -> dict:
    """Run an enterprise platform action through a connector (e.g.
    servicenow create_incident / resolve_incident, dynatrace add_comment /
    close_problem). Requires an approver-role token."""
    return _client().platform_action(name, action, params or {})


def run() -> None:
    """Run the MCP server over stdio."""
    mcp.run()




@mcp.tool()
def mco_renew(task_id: str, estimated_seconds: int = 0) -> dict:
    """Renew the attempt leased through this MCP session. Call between work units.
    A 409 means stop: this attempt expired or an operator halted it.

    estimated_seconds: how much MORE time you now think you need, not your
    original estimate restated. Omit (0) for the default TTL again."""
    return _client().renew(task_id, estimated_seconds or None)


if __name__ == "__main__":
    run()
