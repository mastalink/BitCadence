# The Grand Tour — Zero to Everything in 20 Minutes

This is three things at once: your own learning path, the script for the
demo video, and the agenda for every pilot call. Every command is real;
run it as you read. Windows shown; macOS/Linux equivalents in parentheses.

---

## Act 1 — The Dad Test (minutes 0–3)

**The line:** *"Install is one double-click. No cloud account, no database,
no API key."*

1. Double-click `install.bat` (or `curl -sSf https://bitcadence.ai/install.sh | bash`).
2. Watch it: finds/installs Python, builds the venv, generates your access
   token, puts `mco` on the PATH, drops a Desktop shortcut.
3. Choose **[2] Connect now**. Your browser opens the console; the token is
   already on your clipboard. Paste → Connect.

You are now looking at a live, governed agent control plane running
entirely on this machine. **Nothing has left the computer.**

```
mco status        # health check: profile, store, gateway
mco edition       # community edition, full feature matrix - nothing hidden
```

## Act 2 — The job board: agents as mail (minutes 3–7)

**The line:** *"Agents don't call each other. They drop mail. Mail can be
audited, gated, retried, and escalated. Function calls can't."*

Open a second terminal — this is your worker fleet:

```
mco register --name demo-worker --role codex
# copy the token it prints, then:
set MCO_AGENT_TOKEN=mco_tok_...        (export MCO_AGENT_TOKEN=...)
mco listen --role codex --instance demo-worker
```

In the console GUI: create a job targeting role `codex` — title
"Summarize the repo", instructions whatever you like. Watch the worker
terminal: it leases the job atomically (two workers never collide), runs
it, reports back. The console updates live over WebSocket.

## Act 3 — Governance: the enterprise heart (minutes 7–12)

**The line:** *"This is what the EU AI Act calls human oversight and an
immutable audit trail. We had it before the law did."*

1. Create a job with **requires approval** checked (or via workflow below).
   It pauses at `needs_approval` — the worker can see it but cannot touch it.
2. Approve it from the console or:
   ```
   mco approve <job-id>
   ```
3. Now the receipts:
   ```
   mco audit <job-id>
   ```
   Every transition — created, approved (by whom), leased, completed,
   distilled — in an **append-only** trail. On Postgres a DB trigger
   physically rejects UPDATE/DELETE on history; the embedded store enforces
   the same. *Your auditors can believe this.*
4. The panic button: set `MCO_KILL_SWITCH=true` and restart — no new jobs,
   no new leases, in-flight work finishes, humans can still approve/audit.

## Act 4 — Drumline + the Context Exchange: the moat (minutes 12–17)

**The line:** *"Your agent fleet has amnesia. Claude doesn't know what
Codex did an hour ago. Drumline is the cure — and it works across vendors,
which no vendor will ever build for you."*

Run a real multi-step, multi-vendor pipeline:

```yaml
# tour.yaml
name: grand-tour
steps:
  - id: research
    role: claude
    title: Research the bug
    instructions: Find the root cause of the flaky test.
  - id: fix
    role: codex
    title: Implement the fix
    instructions: Apply the fix the research step identified.
    depends_on: [research]
  - id: ship
    role: codex
    title: Tag the release
    instructions: Tag and summarize.
    depends_on: [fix]
    requires_approval: true        # governance inside the pipeline
```

```
mco workflow tour.yaml
```

What to show:
- Step 2's prompt arrives prefixed with a **WORKFLOW THREAD** block —
  step 1's decisions, files, and gotchas, verbatim. Different vendor,
  zero context lost. Deterministic, not "hopefully the search found it."
- Step 3 pauses for a human. Approve it. Audit it.
- Ask any agent later: `mco_recall(query="flaky test")` from Claude
  Desktop/Codex via MCP — the knowledge outlived the jobs.
- The memory is deterministic scoring, no embeddings, no external calls:
  every recall is **explainable in an audit**.

Wire a GUI agent in one config block (`configs/`): Claude Desktop, Codex,
and Antigravity get `mco_inbox / mco_lease / mco_complete / mco_remember /
mco_recall` as native tools — agents coordinating agents over MCP.

## Act 5 — Enterprise posture (minutes 17–20)

**The line:** *"When you're ready for the enterprise story, it's the same
codebase — flip the edition."*

- **Connectors:** `mco connectors`, `mco sync servicenow` — incidents become
  governed jobs; closing the loop writes back, gated behind approver roles.
  Failed jobs auto-open tickets (escalation bridge).
- **RBAC:** `mco register --name wallboard --role viewer --scope jobs:read`
  — least-privilege tokens; the 403 names the missing scope.
- **SSO without SAML code:** put it behind the proxy you already run
  (Cloudflare Access / oauth2-proxy); `MCO_TRUSTED_HEADER_AUTH=true`.
- **Air-gapped:** `scripts\make-offline-bundle.ps1` → 21 MB zip → install
  on a machine with no internet. *Zero data leaves your network — ever.*
- **Multi-tenancy:** orgs are hard-isolated; jobs, memory, and audit never
  cross the boundary.

```
mco stop          # graceful shutdown; the formal off switch
```

---

## The closing line

> "Everything you just saw is MIT-licensed and ran on one laptop with no
> cloud account. The team edition is a Postgres URL. The enterprise edition
> is a config flag. **We coordinate the agents you already have — we don't
> replace anything.**"
