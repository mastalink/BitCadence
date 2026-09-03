# One worktree per worker

**Problem:** every unattended worker and every interactive Claude Code session
used the same checkout, `C:\AI\baton\Batoncadence`. git's current branch, index
and working tree are **global per checkout**, so N actors in 1 checkout race.

**Fix:** each code-touching worker gets its own persistent git worktree, and a
pre-commit guard refuses a worker's commit in the shared checkout.

---

## What went wrong before the fix

All of this happened in a single day, and none of it announced itself:

- A CI fix committed onto **another agent's PR branch** instead of `main`,
  because that agent had left its branch checked out.
- An interactive session found itself on an agent's branch **three separate
  times**, discovering it only when a commit landed in the wrong place.
- `reviewer-beast` finished a 60-line critique of the EPCOT demo and left it
  **uncommitted in the shared working tree**. It was never committed to a
  branch and never pushed. A single `git checkout` would have destroyed it. It
  survived by luck.

The last one is the important one. The failure mode is not "two agents edit the
same file" — it is **silent loss of finished work**.

## The design

| Piece | What it does |
|---|---|
| `scripts/fleet-worktree.ps1` | Provisions/refreshes `C:\AI\baton\wt\<instance>` and prints the path |
| `scripts/hooks/pre-commit` | Refuses a commit when `BC_FLEET_WORKTREE=1` **and** the repo is the shared checkout |
| Worker wrappers (`~/.mco/bin/*-run.cmd`) | Set `BC_FLEET_WORKTREE=1`, resolve their worktree, `Set-Location` into it |
| Worker prompts (`~/.mco/prompts/*.txt`) | Tell the agent to stay in its CWD and never `cd` to the shared checkout |

Three layers, deliberately. The wrapper puts the worker in the right place, the
prompt stops it wandering back, and the hook catches it if both fail.

### Why detached HEAD

Two worktrees may not have the same branch checked out. If a worker's worktree
sat on `main`, it would fight the shared checkout for it. Each worktree is
parked on a **detached HEAD at `origin/main`**, and the agent creates its job
branch from there — a branch nothing else holds.

### Why it never deletes

`fleet-worktree.ps1` refreshes a worktree between runs, but a dirty tree is
**stashed with a timestamped message**, never reset away:

```powershell
git stash push --include-untracked --message "auto: <instance> pre-run <ts>"
```

Recover with `git -C C:\AI\baton\wt\<instance> stash list`. Given that the bug
being fixed was *losing finished work*, the fix must not lose work either.

### Who gets a worktree

| Worker | Location | Why |
|---|---|---|
| `codex-beast` | own worktree | writes code |
| `reviewer-beast` | own worktree | reviews code in a checkout |
| `grok-beast` | own worktree | writes code |
| `opencode-beast` | own worktree | writes code |
| `chief-beast` | `C:\Users\masta` — **unchanged** | never edits code; decides and dispatches. Still carries `BC_FLEET_WORKTREE=1` so the guard covers it if it ever wanders. |

**A note on `.mcp.json`.** `codex-beast` and `chief-beast` deliberately started
outside the repo because a project `.mcp.json` shadows `~/.codex/config.toml`
and would give the worker `claude-beast`'s identity — silent attribution
corruption with a live token. That hazard does **not** apply in a worktree:
`.mcp.json` is gitignored, and **git does not copy ignored or untracked files
into a new worktree**. Verified: a fresh worktree has no `.mcp.json`.

## Using it

```powershell
# provision or refresh; prints the path
pwsh -File scripts\fleet-worktree.ps1 -Instance reviewer-beast
```

Wrappers call it and `Set-Location` to the result. It is idempotent, so every
run is safe.

## Verifying the guard

```bash
# worker in the shared checkout -> refused
BC_FLEET_WORKTREE=1 git commit -m "x"      # ✗ blocked

# same worker in its own worktree -> allowed
BC_FLEET_WORKTREE=1 git -C C:/AI/baton/wt/reviewer-beast commit -m "x"   # ✓

# human, no env var, shared checkout -> allowed
git commit -m "x"                          # ✓
```

All three were tested when the guard landed.

`core.hooksPath` is repo-local config and therefore untracked; the provisioning
script re-points it on every run, so the guard self-heals rather than depending
on a manual install.

## What this does not fix

- **Ad-hoc per-job worktrees.** Agents also create `Batoncadence-<job>` trees on
  their own. Those are fine — they are already isolated. This adds a *stable*
  home so an agent never has to start in the shared checkout to make one.
- **Two workers on one branch.** Still refused by git, as it should be. Job
  branches are per-job, so it does not arise in practice.
- **The shared checkout itself.** Humans and interactive sessions still share
  it. That is correct: they are interactive and can see each other.
