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
