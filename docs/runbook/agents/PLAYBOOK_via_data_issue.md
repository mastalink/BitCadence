# Playbook: Via Data Issue Resolution

Handling Catholic parish data errors from public API discovery to merged pull request. Plain words, short sentences.

## Standing Safety Rules
1. **Nobody deploys to production except the owner.** Agents create Git PRs only.
2. **Never modify production database tables directly.**
3. **Two model families must review the fix.**
4. **Never print secrets, database credentials, or tokens.**

---

## Step 1: Detect and Reproduce from Public API
- **Trigger:** A parish report, failed schedule audit, or parish finder regression in the Via Catholic church finder app.
- **Discovery action:**
  - Query the public Via API endpoint or local test client: `GET /api/v1/parishes/{id}`.
  - Fetch the corresponding raw schedule from the public parish website or bulletin.
  - Document the discrepancy: wrong Mass time, missing Spanish Mass, obsolete confession window, or bad geolocation.
- **Artifact:** Save reproduction payload as `issue_<parish_id>_evidence.json`.

## Step 2: Create Bounded Job for Codex
- **Who dispatches:** Deputy `grok-beast` or Chief `claude-desktop`.
- **Target worker:** `codex-beast` (on Beast) or `codex-mac` (on Mac Studio).
- **Dispatch fields:**
  - `to_role`: `codex`
  - `to_instance`: `codex-beast`
  - `title`: `Fix parish schedule parser for <Parish Name>`
  - `instructions`: Path to `issue_<parish_id>_evidence.json`, target repository `C:\AI\via-catholica`, branch `fix/parish-<id>`, and required unit test.

## Step 3: Implement Parser/Data Fix and Regression Test
- **Who:** `codex-beast` (or `codex-mac`).
- **Process:**
  - Check out a clean branch in the dedicated worktree: `git checkout -b fix/parish-<id>`.
  - Fix the parish schedule parser, regex, or ingestion mapping.
  - Add a regression test in `tests/test_parish_parsers.py` using the recorded reproduction fixture.
  - Run the test suite: verify 100% pass rate.
  - Commit changes with a clean, descriptive message referencing the issue.

## Step 4: Open Pull Request (PR Only)
- **Who:** `codex-beast` (or `codex-mac`).
- **Process:**
  - Push branch to remote `origin`.
  - Open a Pull Request using `gh pr create` or the repository template.
  - PR description must include:
    1. Public API reproduction evidence before fix.
    2. Parsed schedule output after fix.
    3. Passing test command and output receipt.
- **Rule:** The worker NEVER merges the PR.

## Step 5: Independent Code Review
- **Who:** `antigravity-beast` or `agy-studio-3` (Gemini model family).
- **Process:**
  - Inspect the PR diff against repository conventions.
  - Verify that no credentials, tokens, or unrelated files were committed.
  - Confirm that the regression test genuinely asserts the fix.
  - Post review verdict to the job board and PR comments.

## Step 6: Owner Review and Merge
- **Who:** The owner Owner (owner).
- **Process:**
  - The owner inspects the PR and review evidence.
  - The owner clicks merge on GitHub.
  - Deployment occurs only after the owner approves the release.
