# Overview Dashboard

## Goal
Monitor the high-level health of your agent ecosystem from a single screen: inspect active jobs, running workflow pipelines, online agent capacity, recent audit events, and toggle between plain-English and engineering terminology.

---

## Step-by-Step Instructions

### 1. Navigate to Overview
Click **Overview** at the top of the left navigation sidebar.

![BitCadence Overview Mission Control](img/01-console-overview.png)

### 2. Read Fleet Metric Cards
At the top of the screen, four primary metric cards summarize the current status:
- **Active Jobs:** Number of tasks currently leased or executing (`leased`, `in_progress`).
- **Needs Approval:** Tasks halted at a human-in-the-loop governance gate. Clicking this card jumps straight to the **Approval Queue**.
- **Online Agents:** Connected worker instances actively sending heartbeats.
- **Completed Today:** Successfully finished jobs within the current operating window.

### 3. Track In-Flight Workflows
Beneath the metric cards, the **Running flows** strip groups active tasks by their parent pipeline (for example, `release-pipeline`). Each step is shown as a connected progress indicator showing whether a stage is completed (green), running (pulsing blue), or waiting (grey).

### 4. Monitor the Activity Stream
The right side of the Overview screen presents the real-time **Activity Feed**:
- Shows created, leased, approved, completed, and failed events as they occur.
- Clicking any event entry opens the full Job Detail Drawer for that task.

### 5. Toggle Interface Tone (Plain English vs. Expert Mode)
BitCadence accommodates both non-technical managers and systems engineers:
1. In the top-right header, open the **Tone** toggle (or via Settings).
2. Choose **Plain English** or **Expert**:
   - **Plain English:** Labels appear as *"All work"*, *"Needs your OK"*, *"Your agents"*, *"Flows"*, *"What happened"*.
   - **Expert:** Labels appear as *"Job Board"*, *"Approval Queue"*, *"Agent Fleet"*, *"Workflows"*, *"Audit Trail"*.

---

## The CLI Equivalent

To obtain a quick operational snapshot from your terminal:

```powershell
# Health check and diagnostic summary
mco status

# List active and pending jobs
mco jobs list

# List all agents and their online presence
mco agents
```

---

## What You'll See

- **Live Animated Counters:** Counters transition automatically as jobs are picked up by agents.
- **Interlock Warnings:** If any job fails with no retries remaining, a prominent alert highlights the failure and suggests escalation options.
- **Autonomy Link:** The Autonomy card displays the background Conductor state:
  ```text
  The background sweep engine is actively evaluating jobs, dispatching worker packets, and monitoring autonomous score runs.
  ```
  Clicking **Live look** opens the Autonomous Score Run monitor modal.

---

## If It Goes Wrong

### 1. "Activity stream shows stale timestamps"
- **Cause:** WebSocket connection was interrupted or disconnected.
- **Fix:** Check the top-right status dot. If yellow or grey, refresh the browser page (`F5`) to re-establish the `/ws/broadcast` WebSocket channel.

### 2. "Metric card shows 0 online agents"
- **Cause:** No background workers or daemon listeners are running.
- **Fix:** Start workers in the Desktop Manager, or launch a listener from a terminal:
  ```powershell
  mco listen --role codex --instance worker-1
  ```
