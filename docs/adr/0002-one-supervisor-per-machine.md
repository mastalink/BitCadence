---
status: accepted
---

# One supervisor per machine, one pane of glass

**Supersedes:** the per-worker service model in `fleet.apply_fleet()`.
**Implementation status:** specified, not yet built.

## The problem, stated by the operator

> "The problem is the constant popup and disappearing terminals. Now I have to
> manage and maintain and clean up all these new or old ones that are in Task
> Scheduler. This is a serious app and should be owning what we are building."

> "I also want one place that I can go and manage all of this as if I'm a
> kindergartner and I think the mouse is a foot pedal."

Two complaints, one root cause. `apply_fleet()` reads `~/.mco/fleet.toml` and
installs **one OS service per worker** — `install_waker()` and `install_poll()`
in a loop. A machine running claude + codex + grok + a local model ends up with:

```
BitCadence-gateway
BitCadence-scheduler
BitCadence-wake-claude-w1      BitCadence-poll-claude-w1
BitCadence-wake-codex-beast    BitCadence-poll-codex-beast
BitCadence-wake-grok-beast     BitCadence-poll-grok-beast
...
```

That is `2 + 2N` OS-registered services. Every one is a thing that can go stale,
that survives an uninstall, and that the operator cleans up by hand. Task
Scheduler becomes a graveyard, and nothing in the product owns it.

**Target: `2` services, fixed, regardless of worker count.** On a client-role
machine, `1`.

### What this is not

This is not an init system. The OS keeps supervising *one* long-lived process;
that process supervises its own children. That is a worker pool, which is what
Ollama, Tailscale, Syncthing, and Docker Desktop all ship. Reimplementing
systemd would be the mistake. Owning our own footprint is not.

---

## Design

### 1. Components

| Component | What it is | Lifetime |
|---|---|---|
| `bitcadenced` | One supervisor per machine. Owns every local worker process. | OS service |
| Console **Machine** tab | The single pane of glass. Drives the daemon. | Served by the gateway |
| Tray / menu-bar app | Status light and a door into the console. | User session |

### 2. Service inventory after this lands

| Node role | Services |
|---|---|
| `server` | `BitCadence-gateway`, `BitCadence-agentd` |
| `client` | `BitCadence-agentd` |

`BitCadence-scheduler` folds into the daemon as a supervised child, since it is
just another long-lived local process. Per-worker wake/poll services are
**removed entirely**.

### 3. Why the gateway stays separate

The gateway is deliberately *not* a daemon child:

- A `server` node must serve the API even with zero local workers.
- Restarting workers must never drop the API out from under remote clients.
- The daemon talks to the gateway as an HTTP client; folding one into the other
  couples two very different failure domains.

Two fixed services is the honest win: `O(N) → O(1)`, not `→ 0`. Claiming one
process would mean hiding the gateway inside the daemon and making every worker
restart an API outage.

---

## 4. The daemon

### 4.1 Responsibilities

1. Read `~/.mco/fleet.toml` — already the declarative source of truth.
2. Spawn each enabled worker as a child process, **with no console window**.
3. Supervise: restart with backoff, detect crash loops, stop and report.
4. Reconcile on config change without a restart.
5. Serve a local control API for the console and tray.
6. Aggregate child stdout/stderr into one log stream.
7. Shut down cleanly — never orphan a child.

### 4.2 Supervision policy

This is the part that must not be naive.

- **Backoff:** restart delay `min(60, 2 ** consecutive_failures)` seconds,
  starting at 1s.
- **Flap reset:** a child that stays up **> 60s** resets its failure counter to
  zero. Without this, a worker that restarts once a day eventually hits the cap.
- **Crash loop:** `5` failures inside `300s` → state `crashlooped`, **stop
  restarting**, surface in the console and tray. Silent infinite retry is the
  behaviour that makes operators distrust a supervisor; refusing to retry and
  saying so loudly is correct.
- **Manual reset:** the console can clear `crashlooped` and retry immediately.
- **Never restart on clean exit** (`returncode == 0`) for one-shot poll workers;
  wakers are long-lived and a clean exit *is* a fault.

### 4.3 State machine

```
        ┌────────────────────────────────────────────────┐
        │                                                │
  stopped ──start──> starting ──> running ──exit(0)──────┘
        ▲                            │
        │                            └─exit(≠0)─> backoff ──> starting
        │                                            │
        └──────────stop/disable──────────────────────┤
                                                     │
                                    5 fails / 300s ──▼
                                                crashlooped
                                                     │
                                              (manual retry)
```

### 4.4 Control API — v1

Local HTTP on `127.0.0.1:18790`, bearer token read from `~/.mco/.env`
(`MCO_LOCAL_TOKEN`, the same token the gateway already uses). One
implementation, works for both console and tray.

```
GET    /v1/status              -> {daemon, workers[], gateway_reachable}
GET    /v1/workers             -> [{name, role, instance, state, pid,
                                    uptime_s, restarts, last_exit, last_error}]
POST   /v1/workers/{name}/start
POST   /v1/workers/{name}/stop
POST   /v1/workers/{name}/restart
POST   /v1/workers/{name}/reset    # clear crashlooped
GET    /v1/config              -> current fleet.toml, parsed
PUT    /v1/config              -> validate, write, reconcile
POST   /v1/reload              -> re-read fleet.toml now
GET    /v1/logs?worker=&tail=  -> unified or per-worker log tail
```

**v2 (design for it, do not build it yet):** the daemon also registers with the
gateway as an agent with role `machine`, so a server-role console can manage
workers on remote client machines and every control action lands in the audit
trail. v1 must not make that harder — keep the handlers thin and the transport
swappable.

### 4.5 Config reconciliation

`fleet.toml` stays the only writer-visible config. The daemon polls its `mtime`
every `5s` (portable; avoids three filesystem-watch implementations) and on
change computes a diff:

- worker added → start it
- worker removed → stop it, forget it
- worker `enabled = false` → stop it, keep it listed
- exec/args changed → restart it
- interval changed → apply on next cycle, no restart

Config edits from the console go **through** `PUT /v1/config` so there is
exactly one writer and validation happens before the file is touched.

### 4.6 Log aggregation

Children get pipes, not inherited handles. The daemon reads both streams and
writes `~/.mco/logs/agentd.log` with a per-line prefix:

```
2026-09-01T16:22:36Z  codex-beast  stdout  leased job J-2041
```

Per-worker files stay at `~/.mco/logs/<worker>.log` for anyone who wants them.
Rotate at `10 MB`, keep `3`. Do not build anything cleverer.

---

## 5. Platform layer — the interface to build against

**Three agents implement this in parallel. This interface is the contract; do
not change it without updating this document.**

`src/mco/agentd/platform/base.py`:

```python
class ProcessHandle(Protocol):
    pid: int
    def poll(self) -> int | None: ...       # None while running
    def terminate(self) -> None: ...        # polite stop
    def kill(self) -> None: ...             # last resort
    def wait(self, timeout: float) -> int: ...


class PlatformAdapter(Protocol):
    def spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> ProcessHandle:
        """Start a child that shows NO console window and dies with the daemon."""

    def bind_to_parent_lifetime(self) -> None:
        """Arrange for children to be killed if this process dies. Idempotent.
        Called once at daemon startup, before any spawn."""

    def reap_orphans(self, pidfile: Path) -> list[int]:
        """Kill any children recorded in `pidfile` that outlived a previous
        daemon. Returns the pids actually killed. Called at startup."""

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]: ...
    def uninstall_service(self, name: str) -> tuple[bool, str]: ...
```

### 5.1 Platform matrix

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| Daemon registered as | Task Scheduler task | launchd LaunchAgent | systemd `--user` unit |
| No console window | `CREATE_NO_WINDOW` (`0x08000000`) | n/a | n/a |
| Interpreter | `pythonw.exe` (see `service._service_python()`) | `sys.executable` | `sys.executable` |
| Children die with parent | **Job Object** + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` | **no kernel support** | `prctl(PR_SET_PDEATHSIG, SIGTERM)` |
| Polite stop | `CTRL_BREAK_EVENT` (needs `CREATE_NEW_PROCESS_GROUP`) | `SIGTERM` | `SIGTERM` |
| Process group | `CREATE_NEW_PROCESS_GROUP` | `start_new_session=True` | `start_new_session=True` |
| Tray autostart | Run key / Startup folder | LaunchAgent `RunAtLoad` | XDG `autostart/*.desktop` |

### 5.2 The macOS gotcha — read this before implementing

**macOS has no `PR_SET_PDEATHSIG` and no Job Objects.** If the daemon is
`SIGKILL`ed, its children survive as orphans. There is no kernel mechanism to
prevent this. Do not pretend otherwise.

Mitigation, in this order:

1. Write every spawned pid to `~/.mco/agentd.pids` **before** the child starts
   doing work, and remove it on clean exit.
2. On daemon startup, `reap_orphans()` reads that file and kills anything still
   alive whose process name matches the expected worker command. Match on
   command, not pid alone — pids are recycled and killing a stranger's process
   is unacceptable.
3. Normal shutdown paths (`SIGTERM`, launchd stop) terminate children
   explicitly, so step 2 only fires after a hard kill or a power loss.

Linux gets `PDEATHSIG` *and* the pidfile. Windows gets the Job Object *and* the
pidfile. Belt and braces everywhere; on macOS the belt is all there is.

---

## 6. Migration — `mco fleet migrate`

This is the command that answers "clean up all these old ones." It must be
idempotent and it must never silently delete something it did not recognise.

1. Enumerate installed services matching `BitCadence-wake-*` and
   `BitCadence-poll-*` across every brand prefix (`service.BRAND_PREFIXES`
   already handles the pre-rename names).
2. For each, ensure a matching entry exists in `fleet.toml`; if the service has
   no entry, **report it as an orphan and ask** — never delete unprompted.
3. Uninstall the per-worker services.
4. Install `BitCadence-agentd`.
5. Print a table of what moved, what was orphaned, and what was left alone.

`--dry-run` must show the whole plan and change nothing. Default to dry-run and
require `--apply`; the operator has been burned by state changes here already.

---

## 7. Console — the Machine tab

Lives in the existing console (`src/mco/console_src/`). Four sections:

**Services** — gateway and daemon: up/down, uptime, start/stop/restart. No
operator should ever open Task Scheduler again.

**Workers** — a table: role, instance, state, uptime, restarts, last exit. Add,
remove, enable, disable, restart. Editing here writes `fleet.toml` through the
daemon.

**Health divergence** — the one genuinely novel panel. The daemon knows which
processes are *alive*; the gateway knows which agents are *registered*. When
those disagree, something is misconfigured — a bad token, a firewall, a wrong
gateway URL. Show:

| Worker | Process | Registered | Meaning |
|---|---|---|---|
| codex-beast | running | online | healthy |
| grok-beast | running | **offline** | alive but not reaching the gateway — check token/URL |
| claude-mac | **stopped** | online | stale registration, presence not yet expired |

Nothing in the product surfaces this today and it is the single most common
"why isn't my worker picking up jobs" question.

**Logs** — unified tail, filterable per worker.

---

## 8. Tray / menu bar

`pystray` — one codebase across Windows tray, macOS menu bar, Linux AppIndicator.
It will not feel fully native on macOS; if that becomes a complaint, `rumps` is
the native path. Do not start there.

**Scope it deliberately small. The tray is a status light and a door, not a
control panel.** There is already a good console; a second configuration UI is
a second thing to maintain and it will be worse.

- **Icon states:** green all healthy · amber something restarting or degraded ·
  red crash-looped · grey daemon not running
- **Badge / tooltip:** count of jobs awaiting approval. A human-in-the-loop
  product where the human does not know a job is waiting is broken, and this is
  the right surface for it.
- **Menu:** Open Console · Start all · Stop all · Restart all · per-worker
  submenu (state + restart) · Quit
- **Everything else** is a click into the console.

**The tray must be optional.** Headless servers have no tray. The daemon is
fully usable without it, and nothing may depend on it running.

---

## 9. Acceptance criteria

- [ ] A machine with 6 workers registers exactly **2** OS services (`server`) or **1** (`client`).
- [ ] No console window appears at any point, on any trigger, on Windows.
- [ ] Killing a worker process gets it restarted with visible backoff.
- [ ] A worker that fails 5 times in 300s lands in `crashlooped` and **stops**, visibly.
- [ ] Editing `fleet.toml` adds/removes a running worker within 5s, no daemon restart.
- [ ] `SIGKILL`ing the daemon and restarting it leaves zero orphaned children (all three platforms).
- [ ] `mco fleet migrate --dry-run` lists every legacy service and changes nothing.
- [ ] `mco fleet migrate --apply` leaves Task Scheduler / launchd / systemd with only the two services.
- [ ] The console Machine tab can start, stop and restart every worker without a terminal.
- [ ] The divergence panel correctly flags a worker running with a bad token.
- [ ] Tray reflects daemon state within 5s and badges pending approvals.

## 10. Non-goals

- Supervising **other vendors'** background processes. The operator's tray is
  full of other products; consolidating ours is in scope, adopting theirs is a
  different product.
- Log shipping, metrics, or rotation beyond size-capped local files.
- Remote worker management (that is control-API v2).
- Replacing the gateway service.

---

## Review findings

**Verdict: do not implement this ADR as written.** The fixed-size service
inventory is a good objective, but the design does not yet guarantee exclusive
worker ownership, safe configuration reconciliation, or safe orphan cleanup.
Those are correctness boundaries, not implementation details. Findings are
ranked below by severity.

### Critical — migration and normal startup have no single-owner fence

**Failure scenario.** Section 6 currently says to uninstall legacy worker
services before installing `BitCadence-agentd`, but that order is not sufficient
on Windows: `service._win_uninstall()` only runs `schtasks /Delete`; it never
runs `schtasks /End` or waits for the process to exit. A long-lived waker can
therefore keep running after its registration disappears, and the newly
installed daemon can start a second waker with the same role, instance, and
token. A future implementation that installs the daemon first would make the
overlap deterministic on every platform. The atomic MCO job lease prevents two
processes from leasing the *same* pending row, but it does not prevent the two
copies from leasing different jobs, duplicating concurrency, heartbeats, local
side effects, or non-MCO polling work.

The same race exists outside migration. Two checkouts, two user sessions, or a
manually launched daemon plus the service can all supervise the same
`fleet.toml`. Nothing in the ADR establishes that "one supervisor per machine"
is true rather than aspirational. The current fleet discovery helper also only
filters `bitcadence-*`, despite `service.BRAND_PREFIXES` including the older
`BatonCadence` name, so reusing it naively would leave legacy services alive.

**Required fix.** Define a machine/user ownership boundary and enforce it with
an OS-visible singleton lock (Windows named mutex with an explicit ACL; an
advisory locked file or equivalent on POSIX) before binding the API or spawning
anything. Add a per-worker execution lock keyed by canonical worker identity so
an old supervisor and a new one cannot overlap even during handoff. Migration
must: acquire the global lock or coordinate with the running daemon; explicitly
stop each legacy service; verify its process has exited; install/start the
daemon; verify worker adoption; and roll back or report a recoverable stopped
state on failure. Add an acceptance test that holds a legacy Windows task open
after deletion and proves no second copy starts.

### High — the crash-loop rule has the exact 61-second hole described

**Failure scenario.** If "> 60s uptime resets the failure counter" clears the
same history used by "5 failures inside 300s," a worker that exits every 61
seconds always returns to failure zero. It restarts forever, even though five
such failures fit within a roughly 248-second rolling window once the one-second
restarts are included. A daemon restart can create the same escape if the
crash-loop state exists only in memory.

**Required fix.** Specify two independent pieces of state:

- a backoff exponent, which may reset after 60 seconds of healthy uptime; and
- a deque of failure timestamps, from which entries older than 300 seconds are
  pruned and which is **not** cleared by the 60-second backoff reset.

The fifth remaining timestamp transitions to `crashlooped`. Persist the
crash-loop latch and recent timestamps (including enough boot/process identity
to interpret them) so restarting or upgrading the daemon does not silently
clear the guard. Define whether a machine reboot is an intentional reset; do not
let that behavior emerge accidentally from volatile state.

### High — an in-place or partial config write can drive reconciliation

**Failure scenario.** External editors are explicitly allowed, and the existing
`fleet.set_worker_value()` uses `Path.write_text()` directly on the live file.
That truncates before the new TOML is fully written. The five-second poll can
observe the truncated file, `load_fleet()` can raise `FleetConfigError`, and an
incautious reconciler can either die or interpret a partial-but-valid document
as worker removal and stop healthy workers. Even with atomic editors, `mtime`
alone can miss rapid writes because of timestamp granularity/coalescing, and
console PUT can overwrite a newer manual edit.

**Required fix.** Reconciliation must be transactional: read the whole
candidate, parse and validate it, compute the diff, then commit that candidate
as the new last-known-good generation. On any read/parse/validation error, keep
the current generation running, surface the error, and retry after the file is
stable; never reconcile an invalid candidate. Product writers must write a temp
file in the same directory, flush/fsync it, and atomically replace the target.
Detect content generations (hash plus metadata is sufficient), not `mtime`
alone, and give PUT an expected generation/ETag so competing writers get a
conflict instead of lost updates. Test a slow in-place write, invalid TOML, two
writes within one timestamp tick, and replacement by rename.

### High — the ADR does not use the fleet schema that exists today

**Failure scenario.** Section 4.5 reconciles `enabled = false` and changes to
`exec/args`, but `fleet.parse_fleet_data()` currently rejects unknown fields and
only accepts `role`, `instance`, `mode`, `exec`, `min_interval`, and
`poll_interval`. Disabled workers are represented by `mode = "off"`; there is
no `enabled` or `args`. Parallel implementations can therefore make mutually
incompatible assumptions: one daemon rejects existing config, another ignores
`mode = "off"` and starts a disabled worker, and the console writes fields the
CLI cannot subsequently parse. The scheduler is also said to become a child
without any schema representation for it.

**Required fix.** Make the fleet schema an explicit, versioned part of this
ADR. Either retain `mode` and define how each mode maps to daemon children, or
specify a one-time schema migration to `enabled` plus an explicit worker kind;
do not support both with implicit precedence. Decide whether command execution
is a shell string or an argv array and validate/render it consistently on all
platforms. Specify the scheduler's desired-state entry. Add round-trip tests in
which the current CLI, daemon API, migration command, and console all read and
write the same file without semantic drift.

### High — command-name orphan reaping can kill an unrelated process

**Failure scenario.** A stale pidfile names PID 412 and the expected command is
`python`. Before restart, PID 412 is recycled to another user's legitimate
Python worker—or to a newly started, legitimately identical BitCadence command.
Command-name matching succeeds and the daemon kills the wrong process. Matching
full argv or executable path narrows the error but does not remove the PID reuse
race between inspection and signal delivery.

**Required fix.** The rule must be "do not kill unless identity is proven," not
"name looks plausible." Record and verify at least uid, process start time,
canonical executable, full argv/config identity, and a random supervisor
generation. More importantly, make children or a small wrapper hold/watch a
supervisor-owned control pipe: daemon death closes the pipe in the kernel and
the wrapper terminates the worker process group. That gives macOS a positive
lifetime signal without guessing from a stale PID. A pidfile can remain for
diagnostics, but ambiguous records must be reported rather than killed. Until a
race-safe mechanism is selected, the all-platform "zero orphaned children"
acceptance criterion is unsupported and section 5.2 is invalid.

### High — loopback plus the gateway token is not a least-privilege boundary

**Failure scenario.** Any local process can connect to `127.0.0.1:18790`; TCP
loopback does not identify the calling OS user. `MCO_LOCAL_TOKEN` is the local
operator/admin credential in the existing gateway auth path. Reusing it means a
token disclosed from either the browser/gateway surface or the new daemon
surface compromises both. The daemon API is especially powerful: config PUT
can change an executable and restart it, which is host-level code execution as
the daemon's account. On a shared machine, another user cannot necessarily read
the protected token file, but they can still reach and probe the port, race a
weak/mis-permissioned deployment, and conflict with a daemon in another session.

**Required fix.** Prefer an OS-authenticated local transport: a Unix-domain
socket with restrictive ownership/mode and a Windows named pipe with an
explicit user/service SID ACL, with the gateway proxying console requests. If
TCP must remain, issue a separate random per-machine daemon credential stored
with verified owner-only permissions, use constant-time comparison, rate-limit
failures, define endpoint capabilities, and never grant config mutation merely
because a gateway bearer token is valid. Specify port collision and multi-user
behavior. Add tests proving a gateway-only token cannot mutate daemon config
and a daemon-only token cannot administer the gateway.

### High — the chosen "OS services" do not establish a machine-wide lifetime

**Failure scenario.** The current Windows XML uses
`<LogonType>InteractiveToken</LogonType>`, while the ADR proposes both boot and
logon triggers. An interactive-token task cannot provide a reliable pre-login
machine daemon, and logout/session changes can end or strand the expected
lifetime. Linux uses `systemd --user` and even the existing installer says that
boot-without-login requires lingering; macOS uses a LaunchAgent, which is also
session-scoped. On a multi-user host, each user may attempt to bind port 18790
and claim "the" machine fleet. Windows Fast Startup, sleep/resume, and missed
triggers further complicate the undefined boot boundary.

**Required fix.** Decide explicitly whether the product is one supervisor per
machine or per logged-in user. For machine scope, use true system facilities
(Windows service or non-interactive scheduled-task identity, LaunchDaemon, and
system systemd unit) and define credential/config ownership. For user scope,
rename the promise, use per-user endpoints/locks, and document that workers do
not run before login unless the user enables the platform-specific facility.
Add cold boot, Fast Startup, logout/login, sleep/wake, and two-user acceptance
tests.

### High — desired state, operator overrides, and upgrades are unspecified

**Failure scenario.** An operator manually stops a worker through the API; five
seconds later an unrelated config edit may reconcile `enabled = true` and start
it again. A crash-looped worker may resume after an agentd service restart. An
upgrade can replace code while children still execute the old protocol, then
start new children before the old ones drain. Gateway, daemon, console, and tray
can temporarily speak incompatible API/config versions. None of those outcomes
is defined, so different implementations can all satisfy the prose while
behaving incompatibly.

**Required fix.** Specify persisted desired state versus observed state:
whether manual stop is a durable override, how reset interacts with config,
which transitions survive daemon restart, and how overrides are cleared. Define
a graceful upgrade protocol: stop leasing/spawning, drain or terminate with a
deadline, preserve ownership locks across handoff, atomically install, negotiate
API/config schema versions, health-check, then resume or roll back. Include an
upgrade with an in-progress worker and a crashlooped worker in acceptance tests.

### Medium — keeping the gateway separate is defensible, but section 3's argument is not

**Failure scenario.** Section 3 says folding the gateway under the daemon would
make every worker restart an API outage. That does not follow: a supervisor can
own independent gateway and worker children, and restarting one worker need not
restart either the supervisor or gateway. The real trade-off is whether daemon
failure/upgrade should share the gateway's failure domain, versus the operational
simplicity of one registered service with unified lifecycle and logging.

**Required fix.** Keep two services only if the availability requirement says
the gateway must survive daemon crashes and rolling daemon upgrades. State and
test that requirement (including what happens when the gateway and daemon
versions differ). Otherwise, one supervisor with the gateway as an independently
supervised child is a coherent design and more directly satisfies the
operator's "one thing to own" request. The current two-service conclusion may
still be right, but the ADR has not yet earned it.

### Medium — shutdown and log handling need bounded, testable contracts

**Failure scenario.** A child ignores polite termination, forks a descendant,
or floods stdout while stderr is not drained. Shutdown can hang, descendants
can escape the tracked process, or a full pipe can deadlock the worker and make
it look healthy. A token or sensitive payload printed by a worker is then copied
into two retained log files and exposed by `/v1/logs` without any authorization
or redaction policy beyond the shared bearer token.

**Required fix.** Define process-group ownership, terminate/kill deadlines,
descendant handling, concurrent draining, bounded buffering/backpressure, and
what happens when disk writes fail. Restrict log access as a separate
capability, document that secrets must not be logged, and test a forked child,
ignored SIGTERM/CTRL_BREAK, stdout/stderr flood, disk-full condition, and daemon
shutdown during each state transition.
