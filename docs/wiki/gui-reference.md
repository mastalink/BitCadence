# GUI reference: console, control plane, and Windows app

This guide inventories the controls implemented in the browser console (`/console`), technical control plane (`/dashboard`), and Windows Local Control app. It was checked against source on November 13, 2026, not against an authenticated production session. A visible button is not evidence that its operation is permitted or working. Your token, tenant, edition, policy, and the current job state still govern every action.

**Version boundary:** clean main `0f9f36e` and an existing shared checkout have small differences. The shared checkout adds **Manage fleet tokens**, makes Advanced expose Agent Fleet in plain tone, and repairs individual reassignment. Those changes are pending checkout work, not a verified release. Instructions below identify that difference explicitly.

## Choose the right window

| Surface | Use it for | Prerequisites |
|---|---|---|
| Browser `/console` | Everyday work, approvals, projects, Ask, helpers, schedules, memory | Running reachable gateway; connect in Settings with an existing token |
| Browser `/dashboard` | Agent scopes/status, model credentials, all server settings, workflow YAML, conductor controls | Gateway URL plus token with the required scopes; no demo mode |
| Windows **BitCadence — Local Control** | Starting/stopping local processes, fleet configuration, component logs | Windows and desktop dependencies; configured local installation |

The browser pages do not start local workers merely by being opened. The Windows app manages processes; it does not itself approve jobs. `/dashboard` is an administrative interface with capabilities absent from `/console`; it is not just a smaller status page.

In this guide, **read** means inspect or change the current view; **write** means change saved configuration, work, credentials, or process state; **approve** means an authorization decision. Copying a token/snippet places credentials or configuration on your clipboard. An external connection test may contact another service; a model Test makes a real provider request.

## Open and connect the console

1. Open your gateway's `/console` URL. The usual local address is `http://127.0.0.1:18789/console`. Use the actual configured gateway if different.
2. Choose **Settings**. In plain tone expand **Show advanced**. This disclosure is separate from the topbar **Advanced** switch.
3. In **Connection**, enter **Gateway URL** and **Agent token**, then **Connect**. Connection validates against the gateway before the console changes to Live.
4. Check the footer: **Live**, **Connecting…**, **Offline · connection failed**, or **Demo · simulated data**. Only Live indicates a current connection. Demo job actions change examples, not production jobs. Several features require Live and show an explanatory empty state in Demo.
5. To change gateway/token, choose **Disconnect** first, edit the fields, then Connect. Disconnecting this browser does not stop the gateway or workers. Saved connection data uses this browser's storage; do not treat it as a server identity change.

If authentication fails, obtain the correct existing token and check its scopes. A 403 is not proof that a worker should be promoted to approver. For network failures verify the actual URL and gateway availability. An Offline view can contain previously fetched data; reconnect before relying on it. See [connection troubleshooting](connecting-console.md#if-it-goes-wrong).

## Navigation and display controls

The sidebar has twelve entries: **Home**, **Portfolio**, **Job Board**, **Approvals**, **Governance**, **Ask for something**, **Helpers**, **Connect an AI**, **Schedules**, **Drumline**, **Activity**, **Settings**. Selecting one changes the view, not job state. The selected page and Advanced preference are stored locally.

The topbar **Advanced** switch reveals technical details such as payloads, full job IDs and retry/escalation fields. It does not grant authority. In clean main, plain-tone Helpers remains the simple Helpers page even with Advanced enabled. In the shared pending change, Advanced switches it to Agent Fleet and **Manage fleet tokens** is another way to enter that view. Turn Advanced off to return.

Some headings have two names: Job Board is **All work** in plain tone, Approvals is **Needs your OK**, Helpers is **Your helpers**, Activity is **What happened**. The sidebar keeps the original navigation name. The prototype Tweaks panel exposes accent, density, copy tone and live simulation only when the host activates edit mode; it is not a normal settings workflow. Do not depend on that hidden design tool to administer an installation.

Drawer **×**, **Cancel**, **Back**, and backdrop clicks close or dismiss their respective draft controls. They do not reverse a request already submitted. The shared Drawer has no implemented Escape handler/focus trap; use its visible close control. Notifications disappear after about four seconds; inspect History/Activity for durable evidence.

## Home and the Score live view

1. Choose **Home**. Read **What needs you** first. **Look first** opens job details. **Approve** is an immediate approval request and can release work; review the job first when unsure.
2. Read **What's running**. Click a normal card to inspect its job. A Score card opens **Live view**, an automatically drawn, read-only run view. **Back to Home** returns without changing the run.
3. **See all work** opens Job Board. Its initial data is still the bounded recent job collection, not complete history.

Home marks an active job Stuck after ten minutes without a usable recent work/heartbeat timestamp. That label is a view flag, not a cancellation or a restart. Waiting and approval jobs do not become Stuck merely for being old. Score cards group by run and obtain actual plan step counts when available; missing plan data can omit counts. Home uses at most 200 recent jobs and displays a coverage notice at that limit; at most twenty visible plans refresh every thirty seconds. See [Home rules](Console-home.md) and [Score live](Score-live.md).

## Portfolio

Choose **Portfolio** for one read-only run sheet of the projects you have listed in a local portfolio file. It replaces the earlier **Projects** sidebar entry (a saved "Projects" page choice now opens Portfolio). Roadmaps and milestone states come only from your local configuration; shared queues, workers, board jobs and flow warnings are live. Nothing on this page starts, pauses, moves or changes work.

If the page says **Portfolio is not configured**, copy `configs/portfolio.example.yaml` to your local BitCadence configuration location and list your own projects there (real project data stays out of the repository). If it says **Portfolio could not be refreshed**, read the message and choose **Try again**.

| Control | Steps and expected outcome | Effect |
|---|---|---|
| Flow flags | A red band at the top lists warnings such as a worker reported broken, work pending for a long time, or a recent stalled chain. Read each title and detail, then fix the cause where it happens. | Read |
| Capacity strip | Shows the GPU queue, CPU queue and worker counts (online, broken, away). It is a status view only. | Read |
| Project card | Select a card to expand it. It shows priority, status, the done definition, health (On track, Watch or Blocked), roadmap progress and the next milestone. Select it again to collapse. | Read |
| Recent board jobs | In an expanded card, select a job title to open its details drawer. Jobs appear only when they match the project's configured aliases. | Read |
| Open pull requests | Links open in a new tab when a repository command is configured; otherwise the card shows a notice or "No open pull requests." | Read |
| Refresh | Reloads the snapshot now. The page also refreshes itself every thirty seconds while it is open. | Read |

The card's "Priority and pause controls" line is a visible placeholder: those controls are read-only in this version. Assigning an existing job to a project is still possible through the job metadata and API described in [Projects and assignment](projects-dashboard.md#4-assigning-a-job-to-a-project).

## Job Board and job details

### Find and order work

Choose **Job Board**, then combine **All**, **Active**, **Needs approval**, **Waiting**, **Done**, or **Problems** with search and **All roles**. Search checks title, role and workflow; it is not full payload or full-history search. Active includes pending/leased/in-progress; Problems includes failed/rejected/halted/cancelled. Clearing a filter changes only the view.

**Sort jobs** offers Priority then oldest, Newest first, Oldest first, Status, Assigned role and Title A–Z. The preference is saved locally. Clicking Job/Status/Assigned to/Updated table headings also sorts (the dropdown provides the keyboard-accessible alternative). Sorting does not edit priority or grant a lease. Click a job row to open its drawer.

### Create a job

1. Choose **+ New job**.
2. Fill **What needs to happen?**, optional **Details**, and **Who should do it?**. Available roles come from enabled registered agents; no roles means register a worker first.
3. Select **Ask me before it runs** when this job needs its own approval gate. Server policy may impose a gate independently.
4. With Advanced enabled, set **Retry budget** (the form allows 0–5) and optional **Escalate to role**.
5. Choose **Create job**. Success closes the drawer and the job appears; it can immediately become eligible for a worker. **Cancel** or × dismisses before submission. On failure, the drawer retains the error; check connection/policy before trying again to avoid duplicate work.

CLI doorway: [send and workflow commands](mco-cli-reference.md#cli-mco-send). See [task guide](job-board-and-tasks.md#3-creating-a-new-job).

### Inspect and act on one job

The drawer shows status, approval-gate badge, description, assignee/requester, created time, workflow, decision actor when present, and History/Audit trail. Advanced adds full ID, retry budget and input/output JSON. **Connected steps / Dependencies** opens an available predecessor or dependent in the same drawer. Only dependencies in the fetched collection can appear; absence here is not proof of no dependencies.

| Action | Steps and expected outcome | Recovery / limits |
|---|---|---|
| Approve & run | For needs_approval, review details then choose Approve & run. Requires an authorized approver token. | Approval makes work eligible; it does not prove a helper has started or completed it. Inspect History and current state. |
| Reject… | Open rejection, enter a recorded reason, choose Reject. Cancel dismisses the rejection editor. | This is a decision, not an undo of an approval already made. |
| Try again | Available for failed/rejected/halted jobs. Review the failure, then choose Try again. | Can execute work again. Correct the cause first and check policy/gates afterward. |
| Call it off… | For active/nonterminal work, open the editor, enter a reason, then Call it off. Back dismisses. | Cancellation is a mutation that may stop work; already completed external effects are not undone. |
| Reassign… | Backend accepts unsuccessful terminal attempts (failed/rejected/cancelled/halted), producing replacement work for a chosen role. | Clean main has an inconsistent visible action/request body; do not infer success from its editor closing. The shared pending repair limits this to terminal attempts and corrects the individual payload. Use verified CLI/API if this installation rejects it. |

Always inspect the refreshed state and audit event. The UI may close an action editor before receiving a response; that closure is not evidence of success.

### Batch controls

Use row checkboxes or **Select all jobs** to select visible jobs. A toolbar offers applicable **Approve**, **Reject**, **Retry**, **Cancel**, **Archive**, **Reassign to role… / Apply**, and **Clear selection**. Counts describe eligible selected jobs, while several requests send every selected ID; mixed selections may partly fail. Changing filters can leave hidden selected jobs selected. Clear selection and rebuild it when changing scope.

Reject/Cancel prompt for a reason; cancelling those console prompts cancels the batch. Other actions submit immediately. Read success/failure counts and inspect each failed job before retrying. Archive hides eligible terminal work; there is no matching unarchive button in this console. Use the [archive/unarchive CLI reference](mco-cli-reference.md#cli-mco-unarchive) for restoration. Reassign batch remains subject to server state checks and payload compatibility; see the version warning above.

## Approvals and Governance

### Approvals

1. Choose **Approvals**. Select a queued item to read instructions, target role and requester.
2. **Full details →** opens the same job drawer. Review before authorizing.
3. **Approve & run** authorizes that job. To reject, fill **Reason for saying no / Rejection reason** then choose **Reject**.
4. For several jobs, use individual checkboxes or **Select all**, then **Approve (N)** or **Reject (N)**. Batch rejection prompts for a reason. Review the batch result; it is not necessarily atomic.
5. **Recent decisions** opens job details. Its non-rejected decision badge is rendered as completed; it does **not** prove the actual job completed. Check the drawer's actual status.

If blocked, the console offers **Got it** to dismiss the message. Its suggestion to run `bitcadence fix` is a repair workflow, not authorization to grant yourself approver rights. Use the role approved by your operator. See [approval troubleshooting](Approvals.md#when-you-cant-approve-yet) and [governance guide](approvals-and-governance.md).

### Governance

**Refresh** reloads recent oversight events. **Review** opens a pending job. Decision history is drawn from a bounded feed (150 events loaded, twelve decisions and eighteen oversight events displayed), not a complete compliance history. Click an oversight event to open its job when that job exists in the loaded collection.

For **Evidence export**, optionally fill **Start** and **End** dates, then **Export compliance evidence pack**. The browser downloads `bitcadence-evidence-pack.json` containing the response and file payloads, and shows event/approval/decision counts. Although the UI says “PDF cover page plus JSON,” this button downloads a JSON envelope; inspect its file entries instead of expecting separate files to appear. Export is a read/export action, not an approval and not a certification of legal compliance. A date or permission error appears beside the export. Preserve the downloaded result before refreshing.

## Ask for something

Prerequisite: Live connection and authority to create/approve the proposed work. See [Ask instructions](Ask.md#in-the-console).

1. Enter the request, or choose an example button to fill it. Examples do not start work.
2. Choose **Draft a plan**. Review every step, condition and “you decide” checkpoint. Drafting previews a plan; it does not create jobs.
3. **Remove a step** reveals step **Remove** buttons; at least one step remains. **Always ask me at the end / Asking you at the end: on** adds/removes the final checkpoint. **Make it repeat / Repeats: …** opens choices; select a repeat or **Don't repeat**. Review the revised drawing after each change.
4. **Change my request** returns to text. Edit it and draft again to make larger changes.
5. **Approve and start** creates work and authorizes the start. A repeat also persists a workflow and schedule. Only subsequent plan checkpoints ask again. This is a consequential write, not another preview.
6. The result offers **Watch it on Home** and **Ask for something else**. Watch current work and approval gates; a start receipt is not completion.

The planner uses predictable clause parsing, not unrestricted semantic understanding. Rewrite and redraft a mistaken plan. The old drag/drop console workflow builders are absent; technical YAML submission remains under `/dashboard` → Workflows and `mco workflow`.

## Helpers and fleet credentials

### Everyday Helpers

Choose **Helpers** in plain tone. Each card combines a shape, word and light with what the helper is doing and last contact. The list refreshes every fifteen seconds. Ready/Working presence is useful evidence but does not prove a worker can complete a particular task.

**+ Add a helper** opens AI and Name fields. Choose an offered AI, enter a name, then **Add helper**. Registration/sign-in is saved on the gateway computer; it is not a completed work test. Cancel closes the form. See [add a helper](Helpers.md#add-a-helper).

**Fix it** first requests a diagnosis. Review every proposed change in **Here's what I found**. **Not now** dismisses it without applying. **Yes, fix it** applies the proposed repair and returns results. This is a repair of the helper set, not necessarily only the card you clicked; inspect the full plan. Review permissions and active work before confirming. See [repair workflow](Helpers.md#fix-it).

### Agent Fleet / tokens

Use `/dashboard` → Agents & Tokens for a reliable administrative doorway on clean main. The shared pending console change exposes **Manage fleet tokens** and Advanced Agent Fleet. In that view:

1. **Add agent / Register agent** opens Instance ID, Role, and (with Advanced) Org. Enter the intended identity and an already configured tenant, then **Add agent / Register**.
2. Copy the resulting once-shown token using **Copy**, securely configure the intended worker, then **Done / Close**. Registration itself does not start a listener.
3. **New token / Reset token** asks for confirmation. It immediately invalidates the old token. Copy and install the replacement; there is no undo that restores the old secret.
4. **Remove** confirms immediate token invalidation and deletes the registration if server policy allows it. It is not the correct way to stop an actively leased worker. Inspect current leases and obey server refusal.
5. **Move…** appears for a host operator with multiple tenants. Choose a target, **Confirm**, then accept the boundary warning. Cancel dismisses. Agent visibility/management changes with its tenant; do not mistake an agent disappearing from this view for deletion.

Per-role online counts, current job and Advanced completed counts reflect the fetched collection. See [fleet guide](agent-fleet-and-presence.md) for CLI equivalents and lost-token recovery. Copy feedback or a displayed token is not evidence the executor now uses it.

## Connect an AI

This page edits client app configuration on the **gateway computer**. A remote browser does not make its own laptop's app configuration available to the gateway.

1. Choose **Connect an AI** after connecting the console itself. These are different connections.
2. For an installed app card choose **Connect**. Read the result and restart the target app if instructed. Existing configuration is backed up; unrelated settings are preserved by the connector's implementation.
3. **Send a test** checks the configured entry and executable availability. It is not proof that the target app completed a live MCP call/job.
4. **Disconnect** removes BitCadence's entry; it does not shut down the gateway. Verify the target app after restarting it.
5. For **Another app**, choose **Show me a code**, then **Copy**. Paste the snippet into the intended supported client's connection configuration. Treat any embedded credential as a secret. The button returns configuration rather than installing an arbitrary client.

Not found: install the app on the gateway host, then revisit/reload the page. For malformed configuration the connector should return an error rather than silently replace it; inspect the backup before recovery. See [supported apps and tests](Connect-an-AI.md#apps).

## Schedules

Reading needs `jobs:read`; adding/toggling needs `agents:manage`. A running scheduler is required for saved schedules to execute.

1. Choose **Schedules**. Existing cards show name, schedule sentence, next run and **On / Off**.
2. To create one, select **What should run?** from configured launchers. If none appear, create eligible work first; this page does not offer arbitrary app/page launches.
3. Choose **Every day**, **Every weekday**, **Certain days**, or **Every hour** as offered by the server. For Certain days toggle each day; at least one is required. For non-hourly frequencies set **At what time?**.
4. Read the sentence preview. Choose **Save** to persist; verify its card and next-run text.
5. Click an existing card's **On/Off** to change whether it runs. This saves immediately and can be reversed with the same control. It does not delete the schedule or cancel work already launched.

**Time-zone limitation:** the UI claims it detects “your computer” but does not send a browser zone. The backend detects the gateway host's zone. On remote gateways, verify the actual server zone/next-run time before enabling; see [scheduling configuration](scheduling-and-loops.md#2-configure-launchers-and-loops). There is no GUI timezone selector, delete button or full schedule editor here. [Plain schedule commands](Schedules.md#add-one) and the advanced scheduler configuration handle the remaining operations.

## Drumline: Context and Agent Exchange

Use **Drumline**, then **Context** or **Agent Exchange**. Arrow keys switch these two tabs. The last subview is stored locally. Both use the live gateway; tenant isolation still applies.

### Context

1. Enter **Search memory / Recall query** and optional comma-separated tags, then **Search / Recall**. Tags constrain results; the server ranks recall. At most 25 entries are requested.
2. **Show more / Collapse** expands entry text; **Drill down →** opens provenance/full text. The detail drawer includes provenance and **Copy text**; **Open Source Job** opens the linked job when available in the loaded collection. Advanced adds weight metadata.
3. **+ Add memory / + Remember** opens Title, Kind, Tags and Content. Fill title and content, choose the kind, then **Save memory / Remember**. This creates shared context that eligible agents can recall. The composer **Cancel** dismisses before saving; no edit/delete/undo button is exposed here.

Empty search: clear tags and reword the query before assuming the knowledge is absent. Distillation and injection settings affect whether memory is created/recalled; a saved memory is not a guarantee it appears in every future prompt. See [memory instructions](drumline-memory.md#step-by-step-instructions).

### Agent Exchange

Discussion is reference, not instructions or approval. Posting a “decision” does not authorize a job. Discussion enters recalled Context only through explicit promotion.

1. Choose **Job**, optionally filter **Show / kind**, then select a thread. Read author, replies and Resolved/Replaced/Promoted badges.
2. To start a thread, choose Type (question, proposal, blocker, decision, handoff), enter **Message** (8,000-character UI maximum), and **Post**.
3. **Reply**, **Resolve**, and **Replace** prepare a typed response to a message. Enter the explanation and Post; opening the composer alone changes nothing. **Cancel reply/resolution/supersession** returns to a new root message.
4. A decision or handoff offers **Promote to context**. Review the warning and preview, select Decision/Lesson/Handoff, optionally give a title, then **Promote to …**. This copies sanitized text into shared Context with a receipt. **Cancel** dismisses the promotion form. Resolution/replacement/promotion are recorded actions, not deletion of the original discussion.

If author identity is missing or a job is not in the picker, verify connection, tenant and loaded job window. Do not recreate discussion blindly. See [exchange guide](drumline-agent-exchange.md#step-by-step-instructions).

## Activity

Choose **Activity** for a cross-job audit view. In Live mode, enter search text (event/job/actor), choose event type, then **Refresh** to reload the current window. **Load more** increases the requested event limit; it does not broaden the ordinary job collection. Click a row only when its linked job is loaded. Advanced adds detail JSON, abbreviated in this table; use job History for fuller context.

The statistics summarize fetched events/jobs: Events in last day, failure rate, typical approval wait, and distinct active actors. They are not independently queried system-wide metrics. Refresh/load more before drawing conclusions about missing events; search filters only the fetched window. Demo shows a connection-required explanation. See [Home guide's Activity doorway](overview-dashboard.md#4-look-at-the-rest-of-the-menu).

## Settings

### Everyday settings

Plain Settings shows **Pause everything / Pause / Resume**, phone notification status, and a Memory pointer. The phone card is status-only; configuration is under Show advanced, and the text also points to `bitcadence settings`.

**Critical wording correction:** Pause writes `MCO_KILL_SWITCH`; despite the plain card saying “Jobs in progress finish,” the backend halts active attempts and pauses intake/leasing. Resume clears the pause; halted jobs require an explicit retry and do not automatically continue. Inspect current jobs, choose Pause only when that is intended, and verify History. This differs from `/dashboard`'s **Pause Conductor**, which controls the conductor sweep. Neither is a browser Disconnect.

### Show advanced

Expand **Show advanced** to reveal Connection, Experience, Gateway controls, Connectors and optional Tenancy. Connection was covered above. Experience's **Advanced mode** is the same local preference as the topbar switch, not a permission grant.

**Gateway controls** displays governance, notifications, presence and memory fields from server metadata. Edit the intended fields, then **Save gateway settings**; this console sends changed fields and reloads their values. A success message means settings saved and audited, not that queued jobs finished. Raw choice settings currently render as text fields in this component; use exactly the allowed values or use `/dashboard`, which renders choices as dropdowns.

**Connectors:** enter ServiceNow or Dynatrace fields, then **Save connectors** before **Test connection**. Secret fields start blank; blank keeps the existing secret. Test uses saved gateway configuration, not unsaved form text. A successful Test reveals **Sync now**, which can import/create/update work; it is a write, not another health probe. Inspect the resulting created/updated/skipped summary. A locked encrypted secret store must be unlocked through the supported setup workflow; do not put secrets into plaintext as a workaround.

**Tenancy** (Advanced and host-operator authority) lists configured orgs and in-use markers. Enter a new org name (1–64 letters/digits or `. _ : -`) and **Add org**. This persists the allowed-org list; it does not move existing jobs or agents. There is no remove/rename org button here. Use Fleet's Move or the technical admin surface only within authorized tenant boundaries.

For the full server-driven settings catalogue and groups omitted by this console, use [technical control-plane settings](#technical-control-plane-settings). See also [settings guide](settings-and-configuration.md).

## Technical control plane (`/dashboard`)

### Unlock and Lock

Open the gateway's `/dashboard`, paste an existing bearer token and choose **Unlock** (Enter also works). Authentication probes job access, then opens Operations. Scoped tokens can still receive permission errors on other pages; Unlock does not make them admins. **Lock** clears this page's stored token and hides its shell; it does not revoke the token, stop workers, or clear the separate console's saved connection. There is no Demo mode.

### Operations and conductor

Operations refreshes approximately every five seconds. Click Job/Title/Status/Priority/Target/Leased By/Created headings to toggle sorting. Use **Audit** to show a job's event text. **Approve / Reject** act on a queued gate; rejection prompts for a reason. **Retry** appears beside failed/rejected work.

Row and header checkboxes fill a shared selection across Approval Queue and Job Board. **Approve**, **Reject**, **Retry**, **Cancel**, **Archive**, **Reassign**, and **Clear** appear in the bulk bar. Reassign prompts for a role. Read result counts; partial errors are possible. **Known limitation:** cancelling the dashboard's Cancel-reason prompt still proceeds with an empty reason; do not use that prompt's Cancel as an abort mechanism. Use Clear before selecting a batch action. Individual Reject similarly turns prompt cancellation into an empty rejection request.

**Autonomy Engine** shows conductor state, sweep interval, shadow/live-write status and active-run count. **Pause / Resume** changes conductor sweeps; **Tick Now** triggers a real manual sweep that may advance jobs. These are writes and do not grant new authority or change shadow/live policy.

**Live Look** reads autonomy state and opens a modal with active/failing runs, database location and repository-write status. Its **Trigger Sweep Now**, **Pause Conductor / Resume Conductor** and each **Abort Run** mutate live state. **Close** or the backdrop dismisses the modal. **Known limitation:** Abort Run's reason prompt treats Cancel as “Aborted by operator” and still submits the abort. Do not click Abort Run merely to inspect it. This technical Live Look is different from Home's read-only Score drawing.

CLI doorway: [Scores and autonomy](scores-and-autonomy.md). A ready server, active conductor or visible run is not end-to-end evidence of work delivery.

### Agents & Tokens

1. Choose **Agents & Tokens**, then **+ Register agent**.
2. Fill Name, Role and offered Org. Select explicit Scopes only when needed; none checked requests role-derived defaults. **Register & generate token** submits; **Cancel** dismisses.
3. The token modal has **Copy token**, **Done**, plus worker/MCP configuration **Copy** controls. Save the once-shown token and configure the intended executor. Copy is not a listener start/test.
4. Existing **Edit** changes Role, Status (online/offline/disabled) and Scopes; **Save** submits, Cancel dismisses. This is access administration, not a harmless display preference. A manually set online status is not proof of heartbeat/execution.
5. **Reset token** asks confirmation, invalidates the old token and shows the replacement once. **Delete** confirms registration/token removal. Respect active-lease refusals. See [fleet troubleshooting](agent-fleet-and-presence.md#if-it-goes-wrong).

### Model Connections

These stored provider connections are for custom workers/executors. Adding one does not select or change a model for every agent.

1. Choose **Model Connections → + Add connection**.
2. Enter Name, choose an offered Provider, fill Base URL when the provider allows it, optional Model, and API key. **Add & save** persists; Cancel dismisses.
3. **Test** makes a real provider call and returns success/failure, detail and latency. It can consume provider quota; run it intentionally.
4. **Edit** can change Name, editable Base URL, Model and New API key. Blank key preserves the existing secret. **Save** submits; Cancel dismisses.
5. **Delete** confirms removal of the connection and stored key. It has no undelete control. Preserve needed configuration through your authorized secret/configuration process before deletion.

Recover from validation/auth/network errors by checking saved provider/endpoint/model/key, then retest once corrected. A successful credential test does not prove a fleet worker uses that connection.

### Workflows

Choose **Workflows**, paste valid workflow YAML, review roles, dependencies, conditions and approval flags, then **Submit workflow**. This creates governed jobs and shows run/job JSON; it is not a preview or dry-run. Use the CLI/offline validation path in [workflow guide](workflows-and-flow-control.md#the-cli-equivalent) before submission when needed. On parse/cycle/policy failure, correct the input; on ambiguous network failure inspect existing runs/jobs before resubmitting.

### Technical control-plane settings

Choose **Settings → Reload** to load current metadata, edition/features and integrations. Only authorized settings access can read or save these groups. Controls are driven by the server whitelist, so the following catalogue is a snapshot; absent fields can reflect version or permission differences.

For each group, edit the intended controls then choose its **Save [group]** button. It submits the entire displayed group (except blank secrets), not just changed fields. **Reload** discards unsaved edits. A choice dropdown restricts values; a boolean toggles on/off; a text field accepts server-validated text. Password fields report configured/unset without showing saved secrets. Blank preserves a secret; **Clear** confirms deletion of that secret. There is no universal settings undo: record the prior non-secret value and restore it explicitly if appropriate. `MCO_LOG_JSON` is labelled restart-to-apply even though the generic settings header claims immediate application.

| Group | Setting / control | Type and use |
|---|---|---|
| governance | `MCO_KILL_SWITCH` | bool: Stop work (halt active jobs and pause intake) |
| governance | `MCO_APPROVER_ROLES` | text: Approver roles (comma-separated) |
| governance | `MCO_POLICY_GATED_ROLES` | text: Always-gated roles (jobs to these pause for a human) |
| governance | `MCO_ESCALATION_CONNECTOR` | text: Escalation connector (mirror terminal failures to ITSM) |
| governance | `MCO_DEFAULT_JOB_SORT` | choice: Default Job Board sort order Allowed: `created_desc`, `created_asc`, `priority_desc`, `status`. |
| memory | `MCO_DRUMLINE_DISTILL` | bool: Distill completed jobs into shared memory |
| memory | `MCO_DRUMLINE_INJECT` | bool: Inject shared memory into worker prompts |
| presence | `MCO_AGENT_OFFLINE_AFTER` | text: Mark an agent offline after no contact for (seconds) |
| delivery | `MCO_DELIVERY_STALL_SECONDS` | text: Re-wake a pending job nobody took after (seconds; 0 = off) |
| delivery | `MCO_ROUTE_FALLBACKS` | text: Reroute stalled work to (role:fallback\|fallback, comma-separated) |
| delivery | `MCO_DELIVERY_MAX_REROUTES` | text: Maximum reroutes per job before escalating to a human |
| score | `MCO_SCORE_SWEEP_SECONDS` | text: Advance score runs from the gateway every (seconds; 0 = off, runs only move when someone types `mco score tick`) |
| jev | `MCO_JEV_MODE` | choice: Jev mode Allowed: `disabled`, `shadow`, `assist`, `active`. |
| jev | `MCO_JEV_MODEL` | text: Jev model |
| jev | `MCO_JEV_TIMEOUT_SECONDS` | text: Jev timeout (seconds) |
| jev | `MCO_JEV_MAX_RETRIES` | choice: Jev retry count Allowed: `0`, `1`, `2`. |
| observability | `MCO_METRICS_TOKEN` | secret: Protect /metrics with a bearer token (blank = open, like /healthz) |
| observability | `MCO_LOG_JSON` | bool: Structured JSON logs (restart to apply) |
| tenancy | `MCO_ORGS` | text: Allowed orgs (comma-separated; 'default' always exists). Orgs are isolation boundaries - agents only see jobs and memory inside their own org. |
| edition | `MCO_EDITION` | choice: Edition (blank = infer from configuration) Allowed: blank (infer), `community`, `team`, `enterprise`. |
| security | `MCO_TRUSTED_HEADER_AUTH` | bool: Trusted-header SSO (only behind an auth proxy!) |
| security | `MCO_TRUSTED_HEADER_USER` | text: Identity header |
| security | `MCO_TRUSTED_HEADER_ROLE` | text: Role header |
| security | `MCO_TRUSTED_HEADER_DEFAULT_ROLE` | text: Default role for SSO users |
| security | `MCO_TRUSTED_HEADER_SECRET` | secret: Proxy shared secret (X-MCO-Proxy-Secret) |
| security | `MCO_WEBHOOK_SECRET` | secret: Inbound webhook secret (enables /webhook ingestion) |
| notifications | `NTFY_SERVER` | text: ntfy server |
| notifications | `NTFY_TOPIC` | secret: ntfy topic (blank = notifications off) |
| connectors | `SERVICENOW_INSTANCE_URL` | text: ServiceNow instance URL |
| connectors | `SERVICENOW_USERNAME` | text: ServiceNow username |
| connectors | `SERVICENOW_PASSWORD` | secret: ServiceNow password |
| connectors | `DYNATRACE_BASE_URL` | text: Dynatrace URL |
| connectors | `DYNATRACE_API_TOKEN` | secret: Dynatrace API token (problems.read + problems.write) |

The **Edition** table is read-only feature availability evidence, not an activation of disabled paid capabilities. Changing the Edition setting does not substitute for required licensing/configuration. **Connectors** lists health and supported actions; **Sync now** imports connector work and reports counts. Unlike the everyday console, this page offers Sync without requiring a preceding successful Test. Correct credentials and inspect health before invoking it.

## Windows Local Control and tray

See [installation/shortcut setup](desktop-app-and-tray.md#1-installation-one-click-installer-desktop-shortcut). The following controls are verified in `src/mco/desktop/app.py` and `controller.py`; no live start/stop was performed for this guide.

| Control | Steps and actual scope | Expected feedback / recovery |
|---|---|---|
| Start all | Starts gateway, waits for readiness, then scheduler and configured enabled workers. Already-running external matches are recognized. | Inspect each row, not only global Gateway ready. Worker failures can be reported while other components start. |
| Stop all | Stops configured components, including exact matching external processes belonging to the same user, and disables/ends their scheduled tasks. | Can interrupt active executors. Inspect component rows and logs; it is broader than Exit. |
| Restart all | Stop all then Start all. | May interrupt active work and leave scheduled tasks disabled. Verify the complete stack afterward. |
| Component row | Select a row to filter the bottom log viewer to its last 100 lines. State/Process/Last error show component evidence. | External workers retain their original logs; an empty app log is not evidence they are silent. |
| Start selected | Select the intended component then Start selected. | Gateway must be ready before scheduler/workers. No selection is a silent no-op. Only the first selected row is used. |
| Stop selected | Select the intended component then Stop selected. | Stops its process tree/exact external match and disables its matching scheduled task. |
| Restart selected | Select the component then Restart selected. | Stop then start; inspect the row and work ownership before/after. |
| Open console | Opens the configured local `/console` in the default browser. | It does not start the server or authenticate that browser automatically. |
| Fleet settings | Opens the fleet TOML in Notepad, creating a starter file if absent. Save intentionally. | This button can write/create a file even before Reload. See [fleet schema](sidecars-and-fleet-daemons.md#supported-fields). |
| Reload settings | Reloads fleet definitions. Removed or changed workers can be stopped as part of reconciliation. | This is not a read-only refresh. Inspect changed worker rows, then explicitly start what should run. |
| Move workers into app | Disables matching recurring tasks, Stop all, then Start all under app supervision. | This is a consequential handoff that interrupts process trees. Do between jobs with appropriate authorization, not as a cosmetic warning dismissal. |
| Window X | Hides the window if tray support exists; otherwise exits. | Tray fallback explicitly warns that close exits. |

Right-click the tray icon: **Open BitCadence** restores the control window; **Start all**, **Stop all** and **Open console** have the same scope as above; **Exit** shuts down only supervisor-owned child processes and exits. Exact external workers remain unless Stop was used. A second shortcut launch brings forward the existing app; `--start-all` can also request a start in that instance.

“Scheduled workers are still enabled” means duplicate-start protection refused a new app-owned worker. Existing scheduled workers may still run. Either keep their current management or intentionally use Move workers into app between jobs. Do not kill arbitrary Python processes or erase `~/.mco` as a first recovery step. Read the specific component Last error and original log. Readiness alone does not establish a usable fleet.

Autostart is configured through the [autostart script](desktop-app-and-tray.md#5-configuring-autostart-on-sign-in), not a checkbox in this window. There is no GUI port editor or updater; launcher options/configuration and the documented installation update process provide those capabilities.

## Coverage and limits

The machine-readable [GUI feature coverage](_review/gui-feature-coverage.json) maps control/workflow groups to source and this guide. It includes twelve console pages, the read-only Score view and drawers, five technical control-plane pages/lock/modal flows, Windows window/tray actions and every current server-driven setting. It also marks unreachable legacy console components and pending checkout changes.

This is static source verification. Authentication, browser keyboard/assistive-technology behavior, remote host differences, external provider tests, downloads, live job transitions and Windows task handoffs require separate controlled runtime checks. No production jobs, schedules, tokens, services, approvals or GPU work were changed while writing it. Proposed improvements and known mismatches are in [UX review](UX-REVIEW.md).
