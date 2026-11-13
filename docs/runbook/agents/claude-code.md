# claude-code

## Purpose
The primary code builder for BitCadence, Score steps, Blender automation scripts, and repository improvements.

## Good at
- Python core software development, refactoring, and test suites.
- Implementing Score contracts, executors, and handlers.
- Writing headless Blender 3D scripts for titles and intros.
- Diagnosing complex software regressions and authoring Git PRs.

## Not allowed (hard rules)
- Never pushes directly to remote `main` branch or shared repos without explicit instructions.
- Never deploys production infrastructure (only the owner merges and deploys).
- Never writes credentials, tokens, or environment secrets into committed files.
- Never performs media rendering or ComfyUI model execution (delegate to `claude-beast`).

## How to give it a job
Send via `mco_send`:
- `to_role`: `claude`
- `to_instance`: `claude-code`
- `title`: Specific code feature or bugfix title
- `instructions`: Detailed requirements, target repository path, test expectations, and branch name
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "claude",
  "to_instance": "claude-code",
  "title": "Add lease expiry check in gateway worker sweeper",
  "instructions": "In C:\\AI\\baton\\Batoncadence, inspect src/mco/orchestrator/leases.py. Add an explicit check ensuring expired leases are returned to pending rather than permanently orphaned. Run tests/test_leases.py. Provide diff and test results in completion handoff. Do not push to remote.",
  "priority": 3
}
```

## What it needs in the job
- Absolute path to the repository (`C:\AI\baton\Batoncadence` on Beast).
- Branch name or worktree location.
- Targeted files and exact test command to verify.
- Clean separation from media build work.

## Signs it is stuck and the fix
- **Signs:** Claude usage limits hit, or worker hangs on interactive shell prompts.
- **Fix:** Check `~/.mco/logs/claude-code.log`. If out of Claude usage, reassign code tasks to `codex-beast`.

## Who reviews its work
`antigravity-beast` or `agy-studio-3` (code review), and the owner before merging.
