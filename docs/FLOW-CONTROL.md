# Flow Control — the board as a live diagram

`http://<gateway>/flow` · `mco gui --flow`

One canvas that is the **run view, the approval surface, and the audit trail** at
once. The reference points are n8n (visual flow), PLC/SCADA (live process state
with interlocks), and a production ops dashboard — but the governance is the part
neither of the first two has.

## What makes it not a diagram

**The edges are real.** Every arrow is a `depends_on` relationship the
orchestrator actually enforces: the downstream job sits in `waiting` until its
upstream completes. This is not a picture drawn alongside the data — it *is* the
data. A node cannot be made to run by moving it on screen, and an edge cannot be
drawn that the runtime won't honour.

That's the PLC borrowing: in ladder logic the diagram is the program, and an
interlock is a real gate rather than an annotation. Here the dependency is the
interlock.

## Reading the canvas

| Colour | Status | Meaning |
|---|---|---|
| grey | `waiting` | blocked by an upstream dependency |
| amber ⏸ | `needs_approval` | **stopped at a human gate** |
| blue | `pending` | ready, waiting for an agent to lease it |
| green | `leased` / `in_progress` | an agent is working it now |
| dark green | `completed` | done |
| red | `failed` | failed (retry available) |
| purple | `rejected` / `cancelled` | a human stopped it |

An edge **animates** when completed work feeds something still active — so live
flow through the graph is visible at a glance, the way a SCADA mimic shows
product moving through a plant.

Layers are dependency depth, left to right. A layer wider than 12 jobs wraps into
a grid rather than one unusable tall column — most real boards are mostly
independent work.

## Acting on it

Click any node for a side panel: identity, assignment, dependencies, the
instructions, and the **full audit trail** from `/api/jobs/{id}/events`.

Buttons are governance-aware — they enable only for states where the action is
legal:

| Action | Enabled when |
|---|---|
| Approve / Reject | `needs_approval` |
| Retry | `failed`, `rejected`, `cancelled` |
| Cancel | any non-terminal state |

Every one confirms first, then round-trips through the same authenticated REST
API the CLI uses. There is no privileged path here: the canvas is a client, and
the server enforces exactly what it enforces for `mco approve`.

## Designing a workflow

Switch to **Design workflow** to author the same schema accepted by
`mco workflow` and `POST /api/workflows`.

1. Drag **New step** from the drafting rail onto the canvas. Press Enter on the
   stencil for the keyboard equivalent.
2. Select the card and edit its id, role, title, instructions, approval gate,
   retry budget, and escalation role in the inspector.
3. Draw from one step's **then** output port to another step's **needs** input
   port. That creates `depends_on` on the receiving step. Remove dependencies
   from the inspector.
4. Validate, then **Export YAML**. The download contains workflow data, not
   canvas positions.

Import accepts the workflow subset BitCadence owns: `name`, `steps`, and the
documented step fields. It supports inline or indented `depends_on` lists and
literal-block instructions. Unsupported fields fail visibly instead of being
silently discarded.

Validation runs before export and mirrors the runtime's important checks:
workflow name, a non-empty step list, unique ids, roles, useful title or
instructions, known dependencies, and an acyclic graph. The gateway validates
again if the workflow is eventually run.

### Export is not execution

**Export YAML** only opens a reviewable file and never calls the gateway.
**Run workflow** is a separate, red action. It names how many real jobs will be
created, requires explicit confirmation, then submits through
`POST /api/workflows`. Import also changes only the local draft.

That separation is deliberate. A workflow can be reviewed, versioned, or handed
to another operator without creating work by accident.

## Origin — "what created this?"

If a job came from the scheduler, the panel shows its stamp:

```
Origin    loop: nightly-release #3
```

That's the question plain cron structurally cannot answer months later. Design
intent (a launcher), the run (iteration 3 of a bounded loop), the approval, and
the audit trail all end up on one surface.

See [SCHEDULING.md](SCHEDULING.md) for where those stamps come from.

## Notes

- **Self-contained** — one HTML file, no build step, no CDN, no `node_modules`.
  It renders identically on an air-gapped install. (A test asserts no remote
  references creep in.)
- **Auth is the same model as the console** — the bearer token lives in browser
  `localStorage`; the page itself is public, every API call is not.
- Polls every 5s. Toggle **active only** to hide finished work.
- `Esc` closes the panel.
- Design mode is browser-local until export or explicit execution. Moving a card
  changes only the drafting layout; positions are not part of workflow YAML.
