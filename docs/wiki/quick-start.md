# From installed to your first result

**Goal:** complete one small, low-risk task and know where to find its result.

## Before you begin

Follow the [install guide](install-first-run.md). You need a running gateway, a connected console, and a helper configured to accept the role you choose. Setup screens do not buy model credits or make an unavailable provider start working.

## Start and check

```bash
bitcadence start
bitcadence status
bitcadence helpers
mco gui
```

**Expected:** the console opens. If it shows **Demo**, [connect to the gateway](connecting-console.md) before submitting real work. An online helper is a useful signal; the proof is a completed task with a recorded result.

## Submit a small task

1. Choose **Ask for something**.
2. Describe a bounded task, such as “Summarize the README and suggest three clearer headings.” Choose files your helper can access.
3. Choose **Draft a plan** and read each step. The planner uses predictable sentence rules, not a full understanding of your intent.
4. Remove unwanted steps or change the request and draft again. Add a final approval where you need a decision.
5. Choose **Approve and start** when the plan matches your intent.
6. Find the task on **Home** or **Job board** and open its details.

[Ask for something](Ask.md) covers repeats, conditional steps and the CLI equivalent. A saved YAML plan submitted with `--file` is real execution, not a preview. The text-based CLI Ask path still requires its first job to be approved after the initial plan confirmation, including with `--yes`.

## Know when you are done

Look for a terminal success state **and** the recorded result or handoff. Open the referenced output. A green light or an empty queue alone does not prove the work was completed.

For waiting decisions, use [Approvals](Approvals.md). If no worker takes a job, check its role/instance, helper credentials and worker process in [worker setup](workers-and-daemon-listening.md). Understand a failure before retrying; avoid duplicate jobs.

## If you get stuck

```bash
bitcadence status --details
bitcadence fix
```

**Expected:** details describe the problem; Fix offers repairs. Read each repair before accepting it. Avoid `--yes` while learning the workflow.

The [pause guide](approvals-and-governance.md) explains execution controls. Pausing can halt active attempts. Inspect affected jobs before retrying an interrupted action.
