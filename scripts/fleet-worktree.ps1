<#
.SYNOPSIS
  Give a fleet worker its own git worktree, and keep it out of the shared checkout.

.DESCRIPTION
  Every unattended worker used to start in C:\AI\baton\Batoncadence - the same
  checkout a human and Claude Code sessions use. git's current branch, index and
  working tree are global per checkout, so N actors in 1 checkout race. Observed
  damage, all in one day:

    * a fix committed onto another agent's PR branch instead of main
    * a session finding itself on an agent's branch three separate times
    * a reviewer's finished critique left uncommitted in the shared working tree,
      one `git checkout` away from being destroyed

  This script gives each worker instance a persistent worktree at
  <WorktreeRoot>\<instance>, parked on a DETACHED HEAD at origin/main. Detached
  matters: two worktrees may not have the same branch checked out, so a detached
  worktree never fights the shared checkout over main.

  It never destroys work. A dirty worktree is stashed (including untracked
  files) with a timestamped message before reset, so anything a previous run
  left behind is recoverable with `git stash list`.

.EXAMPLE
  pwsh -File scripts\fleet-worktree.ps1 -Instance reviewer-beast
  # prints the worktree path on stdout; wrappers Set-Location to it
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Instance,

    # The shared checkout. Workers must never run here.
    [string]$SharedCheckout = 'C:\AI\baton\Batoncadence',

    # Deliberately a sibling directory, not Batoncadence-*, so per-worker trees
    # are visually distinct from the ad-hoc per-job ones agents already create.
    [string]$WorktreeRoot = 'C:\AI\baton\wt',

    # Park point. Workers branch from here for their job.
    [string]$BaseRef = 'origin/main'
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Error "fleet-worktree: $msg"; exit 1 }

if ($Instance -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    Fail "instance '$Instance' is not a safe directory name"
}
if (-not (Test-Path (Join-Path $SharedCheckout '.git'))) {
    Fail "no git repository at $SharedCheckout"
}

$tree = Join-Path $WorktreeRoot $Instance

# The guard this whole script exists for.
$resolvedShared = (Resolve-Path $SharedCheckout).Path.TrimEnd('\')
if ((Test-Path $tree) -and ((Resolve-Path $tree).Path.TrimEnd('\') -eq $resolvedShared)) {
    Fail "refusing: the worktree path resolves to the shared checkout"
}

# Make the guard hook active for this repo and every worktree of it.
# core.hooksPath is repo-local config and is not tracked, so set it here where
# it self-heals on every run rather than depending on a manual install step.
$hooks = Join-Path $SharedCheckout 'scripts\hooks'
if (Test-Path $hooks) {
    $current = (& git -C $SharedCheckout config --local --get core.hooksPath) 2>$null
    if ($current -ne $hooks) { & git -C $SharedCheckout config --local core.hooksPath $hooks | Out-Null }
}

& git -C $SharedCheckout fetch --quiet origin 2>$null | Out-Null

if (-not (Test-Path $tree)) {
    New-Item -ItemType Directory -Force -Path $WorktreeRoot | Out-Null
    # --detach: never hold a branch another worktree might want.
    & git -C $SharedCheckout worktree add --detach $tree $BaseRef | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "could not create worktree at $tree" }
    Write-Verbose "created worktree $tree"
} else {
    # Park anything the previous run left. Never delete it.
    $dirty = & git -C $tree status --porcelain
    if ($dirty) {
        $stamp = Get-Date -Format 'yyyy-MM-ddTHH-mm-ssZ'
        & git -C $tree stash push --include-untracked --message "auto: $Instance pre-run $stamp" | Out-Null
        Write-Warning "fleet-worktree: stashed uncommitted work in $tree (git -C '$tree' stash list)"
    }
    & git -C $tree fetch --quiet origin 2>$null | Out-Null
    # Detach first so a job branch from the previous run is released for reuse.
    & git -C $tree switch --detach $BaseRef --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { & git -C $tree checkout --detach $BaseRef --quiet 2>$null | Out-Null }
}

# The wrapper reads this to Set-Location.
Write-Output $tree
