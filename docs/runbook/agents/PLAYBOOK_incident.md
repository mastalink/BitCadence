# Playbook: Incident Response

Response procedures for stuck agents, outages, credential leaks, and review disputes. Plain words, short sentences.

## Standing Safety Rules
1. **Never print, copy, or commit secrets or tokens.**
2. **Escalation path is strictly ordered:** Worker -> Deputy (`grok-beast`) -> Chief (`claude-desktop`) -> Joseph.
3. **Nobody deploys to production or modifies production data except Joseph.**
4. **Log every incident immediately** in `docs/runbook/incident-log.md`.

---

## Escalation Chain of Command
```
Worker encountering error
  └─► Deputy Chief: grok-beast (first response, triage, single retry)
        └─► Chief: claude-desktop (policy, architecture, batched questions)
              └─► Owner: Joseph (spending, prod deployment, security, publishing)
```

---

## Scenario A: Agent is Stuck or Lease is Broken
1. **Detection:**
   - Watch officer `chief-beast` spots an active lease older than timeout without heartbeat.
   - `chief-beast` sends a single alert to Deputy `grok-beast`.
2. **Deputy Triage:**
   - `grok-beast` inspects `mco_jobs` and the worker's log (`~/.mco/logs/<instance>.log`).
   - If worker process is offline: run the restart launcher (e.g. `claude-worker-run.ps1` or `comfyui-start.cmd`).
   - If lease is expired or orphaned: create a single clean re-dispatch job with `escalate_to_role: grok`.
3. **If Claude is Out of Usage:**
   - Follow `docs/runbook/BACKUP_BUILDER.md`.
   - Write lock file `K:\SaintsStudio\saints\<year>\<folder>\BUILDER_LOCK.txt`.
   - Send stand-in job to `codex-beast`.
   - Log the backup switch in `incident-log.md`.
4. **If Failure Happens Twice:**
   - Do NOT retry a third time. Batch evidence and escalate to Chief `claude-desktop`.

---

## Scenario B: Production Outage / Gateway Unreachable
1. **Immediate containment:**
   - Check if Beast host or Mac host is offline via Tailscale ping (`<tailscale-ip>`).
   - Check gateway process at `http://127.0.0.1:18789`.
2. **Local triage (on Beast):**
   - Open BitCadence desktop supervisor on Beast. Click **Start all** or restart the gateway.
   - Verify port 18789 responds: `curl http://127.0.0.1:18789/api/jobs/capabilities`.
3. **Escalation:**
   - If the gateway fails to start due to corrupted local store or database lock, notify Chief immediately.
   - Chief prepares recovery options and brings concise action plan to Joseph.

---

## Scenario C: Leaked Token or Credential Exposure
1. **Immediate Revocation (Emergency Step 1):**
   - Revoke the exposed token immediately using `mco register --name <instance> --role <role>` to rotate the token, or revoke in the provider portal.
   - Invalidate any exposed external API keys (ElevenLabs, OpenAI, Anthropic, Google).
2. **Scrub Repositories:**
   - Check `git status` and `git diff`. Do NOT push commits containing secrets.
   - If pushed to a branch, force-delete or rebase immediately under Joseph's direction.
3. **Re-key Workers:**
   - Store new token in gitignored `.env` (mode 600) or `~/.mco/tokens/<instance>.token`.
   - Update consumer configurations (`~/.claude.json`, `config.toml`).
4. **Report:**
   - Log full incident in `incident-log.md` with timestamp, compromised credential name, and verification of rotation.
   - Notify Joseph immediately.

---

## Scenario D: Wrong or Disputed Review Gate
1. **Verification of Hash:**
   - Check if the review was run against an older file version. The Video Shepherd marks reviews stale automatically when file sha256 changes.
2. **Doctrinal or Moral Doubt:**
   - If `reviewer-beast` flags a theological issue or kid-safety concern that seems ambiguous, do NOT attempt to bypass the gate.
   - Escalate directly to Chief `claude-desktop`.
   - If Chief cannot settle the doctrinal question from Church teaching, Chief brings the exact text and citation to Joseph.
3. **Comedy Gate Disagreement:**
   - If a comedy script fails twice, the Shepherd automatically marks HOLD.
   - The episode moves to the owner review queue (`C:\AI\content-studio\REVIEW_QUEUE.md`).
   - Joseph reviews the punch-up list during his review window.
