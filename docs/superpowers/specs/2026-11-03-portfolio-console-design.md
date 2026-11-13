# Portfolio Console Design

## Intent

Give an authenticated BitCadence operator one read-only place to see whether every locally configured project is moving, what comes next, and where shared compute or workers are blocking flow. The portfolio is an operational projection, not a second job system: local configuration defines projects and roadmaps, while the board and optional local adapters supply live state.

## Goals

- Show one summary card per configured project with activity state, priority, done definition, roadmap progress, next milestone, last activity, and green/amber/red health.
- Provide a project drill-down with all milestones, recent matching board jobs, and open pull requests.
- Summarize GPU work, CPU work, worker presence, and an optional publishing pipeline adapter.
- Compute actionable flow flags and expose the same list from `GET /api/portfolio/flags`.
- Keep v1 controls visibly read-only with a clear extension seam for later priority and pause writes.

## Non-goals

- No notifier, background poller, persistence layer, or write endpoint.
- No automatic edits to project roadmaps or priorities.
- No requirement that optional local tools, repositories, GPU tooling, or publishing state exist.
- No real project information in the public repository.

## Constraints

- The repository is public. Real names, private paths, and project-private data must come only from a local gitignored YAML file selected by `MCO_PORTFOLIO_CONFIG` or the default user config location.
- Commit only a generic example configuration.
- Portfolio API routes require the existing `jobs:read` bearer-token scope.
- Optional adapters fail soft and return explicit availability/error metadata; they must not make the portfolio route fail.
- Subprocesses have short timeouts, no shell expansion, and commands/paths come only from local configuration.
- Existing dependencies only; use the already-declared YAML and HTTP libraries plus the standard library.

## Architecture

`portfolio.py` owns configuration validation, project matching, roadmap projection, adapter reads, and flag computation as small pure helpers around injected I/O. `portfolio_routes.py` composes those helpers with the org-scoped board store and exposes authenticated read-only endpoints. The console live adapter fetches the aggregate endpoint, while a dedicated React view renders summaries and drill-downs.

Project/job matching uses explicit configured aliases against existing job project metadata. Unmatched jobs remain on the normal job board; the portfolio never invents an assignment. Pull requests are read with an optional configured command per project. The publishing adapter first runs an optional JSON status command, then falls back to configured episode JSON files.

Milestone state is explicitly owned by the local YAML (`done`, `next`, or `blocked`); board jobs do not mutate or infer it. Progress is `done / total`. The next milestone is the first explicit `next`, then the first non-done milestone, or empty when all are done.

Project health precedence is red for a blocked milestone, failed/rejected live job, or project-associated red flow flag; amber for a paused incomplete project, approval/waiting work, no activity for 7 days, or an adapter warning; and green otherwise. A fully completed roadmap stays green even when the project is paused.

## API shape

- `GET /api/portfolio` returns `configured`, `source`, `projects`, `queues`, `workers`, `publishing`, `flags`, `generated_at`, and optional top-level warnings.
- `GET /api/portfolio/flags` returns `flags` and `generated_at` from the same snapshot builder.
- Project summaries include the full milestone list, recent jobs, and pull requests so the client needs one authenticated read.
- Missing configuration returns a successful, explicitly unconfigured snapshot with setup guidance and no private filesystem path.
- Invalid configuration also returns a successful degraded snapshot with a generic validation warning; it never leaks file contents or paths.
- Each project includes at most 20 newest matching jobs. Chain-stall flag input is bounded to the 100 newest matching events from the previous 24 hours.

## Flow flags

- GPU idle for more than 30 minutes while GPU-stage work has waited that long.
- Any worker reporting `broken`.
- Any board job pending for more than 2 hours.
- Any publishing episode due within 7 days that is not scheduled or published.
- Any recent `chain_stalled` board event.

Each flag has a stable id, kind, severity, title, detail, project id when known, and observed timestamp when available. The console shows red flag rows at the top of the portfolio screen.

## Visual direction

The page uses the existing BitCadence indigo and neutral surfaces, but reads as a conductor's run sheet rather than a grid of interchangeable SaaS cards. A narrow priority rail and compact roadmap bar make scanning order obvious; health colors are reserved for state. The memorable element is the shared-capacity strip—GPU, CPU, and workers presented as one continuous flow line above the project ledger.

Color tokens remain the console variables: indigo accent, neutral paper/background, green/amber/red status colors, and graphite text. Typography stays with the console system family for consistency. Layout is left-aligned, dense, keyboard accessible, responsive to one column, and motion-free except existing status behavior.

## Local configuration

The example documents generic fields for project identity, aliases, active/paused state, priority, done definition, milestones, repository adapter, GPU-stage markers, publishing adapter, GPU commands/URL, and thresholds. The real seed file is created outside the repository and is never printed into tests, screenshots, docs, or commits.

The default path is `~/.bitcadence/portfolio.yaml`. `MCO_PORTFOLIO_CONFIG`, when set, selects exactly that path; missing or invalid env-selected files do not fall back to the default.

Adapter contracts are deliberately narrow:

- `repository.pr_command` is an argv list whose stdout is a JSON array with `number`, `title`, `url`, and optional `updatedAt`.
- `publishing.status_command` is an argv list whose stdout is an object containing `episodes`, or an episode array. Each episode may contain `id`, `title`, `stage`/`status`, `publish_at`/`due_at`, and `gates_missing`. Fallback files use the same per-episode fields.
- `gpu.lock_command` is an argv list returning an object with optional `holder`, `acquired_at`, and `idle_since`; `gpu.comfy_url` points to a ComfyUI `/queue` response; `gpu.nvidia_smi_command` returns CSV utilization and memory values.
- GPU work is authoritative from the configured lock/Comfy tools plus board jobs explicitly marked by configured GPU-stage terms. CPU work and worker presence are authoritative from org-scoped board jobs and agent registry rows respectively; adapters are not merged into those counts.

## Extension seam

The API returns `controls: { read_only: true }`. The client labels priority and pause controls as read-only and contains a TODO at the adapter boundary for a later, separately authorized write API.
