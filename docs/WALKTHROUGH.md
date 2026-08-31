# BatonCadence — Founder's Personal Walkthrough

A hands-on, copy-paste pass where **you** drive the whole system end to end and
watch every feature work. ~30–40 min. Windows / PowerShell. Each step is
**Do → Expect**; tick the box when you see the expected result.

Steps are tagged with internal test-case ids (TC-*) in brackets. You'll want
**two PowerShell windows** open: **(A)** the gateway, **(B)** a worker + your
commands.

> Convention: every command assumes the project venv is active. In each window:
> ```powershell
> cd C:\ai\baton\Batoncadence
> .\.venv\Scripts\Activate.ps1      # prompt should now show (.venv)
> ```

---

## Step 0 — Clean slate (clear the noisy secret store)

You currently have an orphaned `secrets.enc` that warns on every command.

```powershell
# See if it holds anything you set on purpose:
Test-Path $env:USERPROFILE\.mco\secrets.enc
```

- **If it's from an interrupted setup (you don't remember a master password):**
  delete it — it holds nothing usable.
  ```powershell
  Remove-Item $env:USERPROFILE\.mco\secrets.enc
  ```
- **If you DID store real secrets there:** don't delete — instead
  `mco setup --menu` → Security → unlock with your master password.

✅ **[ ]** No more "secret store … no available key unlocks it" warning on the next command.

For the core walkthrough, pin the base edition so behaviour is deterministic
(we'll switch to enterprise in Step 6):

```powershell
$env:MCO_EDITION = "community"
```

---

## Step 1 — Doctor: prove the install is healthy  *(TC-B1)*

```powershell
mco doctor
```

✅ **[ ]** Reports Python OK, config loaded, edition = community, backend =
Local-Only (embedded), secret store OK. **Exit code 0** (run `$LASTEXITCODE`).
Any red line is a real problem — fix before continuing.

---

## Step 2 — Start the gateway + open the Control Panel  *(TC-A2, TC-D1)*

**Window A:**
```powershell
mco setup            # if you've never set MCO_LOCAL_TOKEN: run this once,
                     # choose a token, COPY IT. Otherwise skip.
mco start
```

> ℹ **Local-Only operator auth — handled automatically.** The operator
> commands (`send` / `approve` / `audit` / `workflow` / `sync`) run as the
> local operator that the gateway seeds from `MCO_LOCAL_TOKEN`. If you haven't
> set a separate `MCO_AGENT_TOKEN`, the CLI falls back to `MCO_LOCAL_TOKEN`
> automatically, so a fresh Local-Only install just works. (Set
> `MCO_AGENT_TOKEN` explicitly only if you want a distinct operator identity.)

✅ **[ ]** Prints `Gateway up`, `Console: http://127.0.0.1:18789/console`,
`Dashboard: http://127.0.0.1:18789/dashboard`.

Open **http://127.0.0.1:18789/dashboard** in your browser.

✅ **[ ]** You're prompted for a token. Paste a **wrong** value → **rejected**.
Paste your real `MCO_LOCAL_TOKEN` → the Control Panel loads. *(That's the auth
gate working — TC-D1.)*

> Don't know your token? `mco setup --menu` → it's the `MCO_LOCAL_TOKEN` value,
> or set a fresh one there.

---

## Step 3 — Register agents; see presence in CLI **and** web  *(TC-B3, TC-D3)*

**Window B** — register an operator-style agent and a worker:

```powershell
mco register --name demo-codex --role codex
```

✅ **[ ]** Prints a one-time token starting `mco_tok_…`. **Copy the demo-codex
token** — you'll need it in Step 5. (It is not shown again — TC-A2 contract.)

```powershell
mco agents
```

✅ **[ ]** `demo-codex` is listed, role `codex`, status **offline** (it hasn't
polled yet).

Now refresh the dashboard **Agents** tab.

✅ **[ ]** `demo-codex` appears **in the web UI too** (the CLI/web parity bug we
fixed — TC-D3). Status badge shows offline; "seen never".

---

## Step 4 — A governed job: send → approval gate → audit trail  *(TC-B2, TC-F1, TC-F2)*

**Window B:**
```powershell
mco send codex -t "Investigate the checkout 503s" --approve
```

✅ **[ ]** Prints a **job id** and status **needs_approval**. Copy the id.

Look at the dashboard **Operations** board.

✅ **[ ]** The job sits at **needs_approval** — nothing runs yet (the human gate).

Approve it:
```powershell
mco approve <PASTE_JOB_ID>
```

✅ **[ ]** Status flips to **pending/approved**. *(Try `mco reject <id>` on a
throwaway job too, and confirm it goes terminal — TC-F1.)*

Now inspect the immutable trail:
```powershell
mco audit <PASTE_JOB_ID>
```

✅ **[ ]** Shows oldest-first events: **created → approved (by you) → …**, each
stamped with *who* and *when*. This is the EU-AI-Act audit story (TC-F2).

---

## Step 5 — The moat: workflow handoff via Drumline shared memory  *(TC-E2)*

This is the demo that sells the vision: a `research` step's findings reach the
`implement` step automatically. We'll run a tiny deterministic worker so it
completes without needing codex/claude installed.

**5a. Save the demo worker.** Create `demo_worker.py` in the repo root:

```python
import os
from mco.sdk import BatonAgent

agent = BatonAgent(
    role="codex",
    instance_id="demo-codex",
    token=os.environ["DEMO_WORKER_TOKEN"],
    gateway="http://127.0.0.1:18789",
    poll_interval=3.0,
)

@agent.handler
def handle(job, prompt):
    title = (job.get("title") or "").lower()
    saw_thread = "WORKFLOW THREAD" in prompt          # the Drumline handoff
    if "research" in title or "investigate" in title:
        return ("Root cause: a retry storm in the payment webhook saturated the DB pool.",
                {"summary": "Found the root cause.",
                 "decisions": ["Fix the webhook handler, not the DB"],
                 "files": ["src/payments/webhook_handler.py"],
                 "gotchas": ["Pool size is set in two places; env wins"],
                 "follow_ups": ["Add a circuit breaker"]})
    note = " (used the research handoff)" if saw_thread else ""
    return (f"Applied a bounded retry + circuit breaker{note}. Pool stable under load.",
            {"summary": "Implemented the fix the research step found.",
             "decisions": ["Bounded retries to 3"],
             "files": ["src/payments/webhook_handler.py", "tests/test_webhook.py"]})

if __name__ == "__main__":
    print(f"Demo worker online as {agent.instance_id}; polling…")
    agent.run()
```

**5b. Save the workflow.** Create `demo_workflow.yaml`:

```yaml
name: payment-latency-fix
steps:
  - id: research
    role: codex
    title: Investigate the payment latency spike
    instructions: Find the root cause of the p99 latency spike on checkout.
  - id: implement
    role: codex
    title: Implement the fix
    instructions: Apply the fix the research step identified, with a test.
    depends_on: [research]
```

**5c. Start the worker (Window B):**
```powershell
$env:DEMO_WORKER_TOKEN = "<demo-codex token from Step 3>"
python demo_worker.py
```

✅ **[ ]** Prints "Demo worker online as demo-codex; polling…". In the dashboard
Agents tab, `demo-codex` flips to **online** within a few seconds (TC-B3 presence).

**5d. Submit the workflow (open a third window, or stop the worker is NOT needed — use Window A after `mco start` returns):**
```powershell
mco workflow demo_workflow.yaml
```

✅ **[ ]** Prints a **run id** and two jobs (`research`, `implement`). `implement`
starts **waiting** (gated behind `research`).

Watch the board / worker output. The worker runs `research`, then `implement`.

✅ **[ ]** When `implement` completes, its result contains **"(used the research
handoff)"** — proving the research step's structured findings were injected into
the implement step's prompt via **Drumline**. Open `mco audit <implement_job_id>`
and the dashboard **Drumline/Memory** view to see the decisions/files/gotchas
carried across (TC-E1/E2). **This is the moat, working.**

Stop the worker with **Ctrl+C** in Window B when done.

---

## Step 6 — Enterprise connectors (optional, needs real accounts)  *(TC-G1)*

Only if you want to validate the ServiceNow/Dynatrace path. This needs live
credentials — follow the dedicated runbook, which has the free-account signups:

```powershell
$env:MCO_EDITION = "enterprise"
mco connectors           # lists configured connectors + health
mco sync                 # pulls open incidents/problems onto the board
```

✅ **[ ]** `mco connectors` shows your configured connectors as healthy.
✅ **[ ]** `mco sync` drops real incidents/problems onto the job board.

Full live cross-vendor proof (Dynatrace problem → governed agent → ServiceNow
incident → audit chain): see **`SMOKE_TEST.md`**.

*Posture check (TC-H1):* with `$env:MCO_EDITION="community"`, run
`mco connectors` → it should refuse (connectors are enterprise-only). That proves
nothing enterprise leaks into the free edition.

---

## Step 7 — Observability  *(TC-I1)*

Right after the gateway starts, `/healthz` and `/console` can 500 for ~10s
(asyncio backend import). Retry; the first 500 is warmup, not death.

```powershell
# Retry until 200 (do not treat the first 500 as a failed install).
$ok = $false
foreach ($i in 1..30) {
  try {
    $code = (Invoke-WebRequest http://127.0.0.1:18789/healthz -UseBasicParsing).StatusCode
    if ($code -eq 200) { $ok = $true; break }
  } catch { }
  Start-Sleep -Seconds 1
}
$ok   # True
(Invoke-WebRequest http://127.0.0.1:18789/metrics -UseBasicParsing).Content
```

✅ **[ ]** `/healthz` → 200. `/metrics` → Prometheus text reflecting your live
job counts and agent fleet (the jobs you just created appear in the gauges).

---

## Step 8 — Security spot-checks (prove this cycle's fixes)  *(TC-K1, TC-K2)*

**8a. Stored-XSS rejection (the High finding we closed).** In the dashboard
**Agents → Register** form, enter the name:

```
test'><img src=x onerror=alert(1)>
```

✅ **[ ]** Registration is **rejected** with "may contain only letters, digits,
and . _ : -". No alert box ever appears. *(The API returns 400; the value can't
reach an HTML/JS sink — TC-K1.)*

**8b. Token rejection.** Log out of the dashboard (clear the token) and try a
wrong token.

✅ **[ ]** Rejected. *(Constant-time compare under the hood — TC-K2.)*

**8c. Kill switch (TC-F3).** Dashboard **Settings → Governance → Kill switch:
on**. Send a job; workers stop leasing. Turn it back off.

✅ **[ ]** With the kill switch on, no new job gets leased; flipping it off
resumes normal flow.

---

## Step 9 — CLI ⇄ Web parity (the OS X-style control panel)  *(TC-D2, TC-D4)*

In the dashboard **Settings**, change a few things and confirm they persist:
governance approver roles, presence health-check threshold (minutes), tenancy
org list, observability/metrics token.

✅ **[ ]** Each setting saves and survives a refresh.
✅ **[ ]** Try to save a key that isn't on the allowlist (the UI won't offer one,
but the API rejects arbitrary env writes) — secrets show as **set/unset**, never
the value (TC-D4).

---

## Step 10 — Service mode (optional)  *(TC-J1)*

```powershell
mco service install
mco service status      # running
# reboot if you want to prove persistence, then:
mco service status
mco service uninstall
```

✅ **[ ]** Installs as a boot-persistent service, reports running, uninstalls clean.

---

## Step 11 — Live site + demo video  *(TC-L1)*

Open **https://batoncadence.com/**.

✅ **[ ]** Page loads; the "See it run" demo video plays (not a broken link).
✅ **[ ]** Shrink the browser to phone width — the video keeps its side margin
like every other section (the mobile-gutter fix).

---

## Step 12 — Tear-down

```powershell
mco stop                              # stops the gateway (Window A)
Remove-Item demo_worker.py, demo_workflow.yaml   # throwaway scaffolding
```

---

## Done — your personal sign-off

You have personally exercised, end to end:

- [ ] Install health (`doctor`) and gateway start
- [ ] Dashboard auth + CLI/web parity + presence
- [ ] Governed job: approval gate + immutable audit trail
- [ ] Workflow DAG + Drumline shared-memory handoff (the moat)
- [ ] Connectors / sync (if you did Step 6) + edition posture
- [ ] Observability (`/healthz`, `/metrics`)
- [ ] Security: XSS rejection, token rejection, kill switch
- [ ] Settings persistence + secret masking
- [ ] Service mode (optional)
- [ ] Live marketing site + demo video

If every box is ticked, the product does what you say it does — and you can
demo any of it from memory in front of a buyer or investor.
