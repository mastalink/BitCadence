# BitCadence interface review

Reviewed November 13, 2026. Lens: clear language, consistent meanings, details when needed, reliable feedback, and deliberate reversible actions. Findings below come from static source and routing inspection of clean main `0f9f36e` plus the existing shared checkout. They are not claims that a browser interaction or production failure was reproduced. No gateway, worker, token, schedule, job, approval or GPU state was changed.

## Highest-value changes

Fix misleading stop/approval feedback and cancellation behavior first. Then make administrative functions discoverable without forcing users to learn two partially overlapping web apps. The current everyday surfaces already have useful foundations: Ask previews before starting, helper repair diagnoses before confirmation, card states use words plus shapes, and Context promotion explains its future effect. Extend those patterns consistently rather than adding another flow builder or control panel.

## Verified static findings

Source paths are repository-relative. Long UUID source filenames are mapped at the end. Priority indicates user impact, not a release approval.

| ID / priority | Evidence and practical impact | Concrete simpler proposal | Acceptance check |
|---|---|---|---|
| U01 / P1 | `ops.js:1020–1039` says Pause lets in-progress jobs finish. Its handler writes `MCO_KILL_SWITCH`; `admin_routes.py:413–458` calls `set_paused`, and `leases.py:241–255` marks renewable jobs halted and increments lease epoch. The same screen's advanced copy says active work halts. A person cannot predict what Pause will do. | Label it **Stop work**, explain **Halts active attempts and pauses new work. Resume permits new work; halted jobs need Retry.** Keep backend governance unchanged. Use a deliberate confirm summary for the existing effect; retain a fast emergency path where required. | Assert displayed explanation matches halt behavior in an isolated test; Resume never implies halted work auto-resumes. |
| U02 / P1 | `dashboard.py:421–432`: `prompt(...) || 'Aborted by operator'` submits an abort even when the user cancels. `:361–395` batch Cancel and `:526–530` individual Reject similarly convert prompt Cancel to an empty reason and continue. | Store prompt result; return if `=== null`. Use an inline confirmation with job/run title, effect, reason and explicit Cancel. | Cancelling every reason dialog sends zero mutation requests. Confirming sends one request. |
| U03 / P1 | Console adapter `adapter.js:304–319` renders `res.success_count` and `res.failure_count`, but `routes.py:1670–1677` returns `succeeded` and `failed`. Batch feedback can say undefined succeeded/failed and mark partial failure as success. Selections are then cleared. | Derive counts from `succeeded.length` and `Object.keys(failed).length`; retain failed selections and a durable per-item result panel. | A controlled one-success/one-failure response shows accurate counts, reasons and retry scope. |
| U04 / P1 | Clean-main `jobs.js:505–596` offers Reassign on active jobs, while the backend accepts unsuccessful terminal work. Clean `adapter.js:285–297` uses `to_role`/`to_instance` rather than required target fields. The shared dirty checkout repairs individual UI/body; that is pending work, not deployed proof. Batch UI still sends `to_role` (`jobs.js:385`) and adapter forwards it verbatim. | Integrate the existing individual repair through review; make batch use `target_agent_role`, state eligibility and replacement semantics. Keep live cancellation separate. | Failed/rejected/cancelled/halted can request a replacement; active/completed cannot. Single and batch body keys match schema and results show replacement identity. |
| U05 / P1 | `pages.js:820–825` renders every non-rejected Recent decision with completed status, even if the approved job is waiting/running. This turns approval into false delivery evidence. | Show **Approved** as decision and actual job state separately. | An approved pending job reads Approved / Waiting, never Completed. |
| U06 / P1 | `adapter.js:70–80` maps every HTTP 403 in `plainFailure` to “Your account isn't an approver yet” and `bitcadence fix`. This helper is used by batch and other operations. Scope, tenant or policy denial can receive an irrelevant privilege-repair instruction. | Preserve a typed server denial; distinguish missing scope, approver authority, tenant boundary and policy. Link to operator-admin instructions, not automatic privilege elevation. | Representative 403s retain their true reason and do not recommend unauthorized authority changes. |
| U07 / P1 | `ops.js:535–547,595` says timezone comes from “your computer,” but Save sends no zone. `schedules_routes.py:48` defaults to `schedules_plain.detect_timezone()` on gateway host (`schedules_plain.py:73–89`). Remote browsers can create schedules at the wrong local time. Fixed-offset fallback can also ignore future DST. | Show **Time zone: [explicit IANA zone]** next to time and next occurrence. Send selected zone, validate it, and show the next occurrence including offset/date before save. | Browser and gateway in different zones produce the selected zone; next-run checks span a DST change. |
| U08 / P1 | `desktop/app.py:77` directly calls `take_control`. `controller.py:153–161` disables scheduled tasks, stops all and restarts. UI label **Move workers into app** does not expose the process interruption or scheduled-task changes. **Stop all** also disables tasks and stops exact matching external workers (`controller.py:228–276`), while Exit only closes owned processes. | Add a review sheet listing affected components/tasks and current work, then **Move and restart workers**. Clarify Stop all vs Exit scopes beside the actions. Keep duplicate-start guard and ownership boundaries. | Read-only review sends no stop/task writes; confirmed handoff lists exact scope and preserves current ownership rules. |
| U09 / P2 | Clean `shell.js:127` routes plain-tone Helpers without considering Advanced; token administration requires expert copy tone, available only through host prototype tweaks. Shared checkout introduces a pending Advanced/Manage fleet tokens doorway. | Expose a visible **Manage credentials…** disclosure from Helpers, with explicit admin scope, no design-mode dependency. Reuse the pending doorway after review. | Starting in default plain mode, an authorized user reaches token administration and returns without changing copy tone or unrelated preferences. |
| U10 / P2 | `shell.js:203–216` has global Advanced; plain Settings nests another **Show advanced** disclosure (`ops.js:1049–1054`). A user must open technical detail to connect at all. The controls have different scope under the same word. | Put initial Connect setup directly in the disconnected empty state. Rename global toggle **Technical details**, local disclosure **Server administration**; retain the same permission checks. | First-time user can connect from empty state without searching for hidden fields; toggles describe what they reveal. |
| U11 / P2 | `/console` has twelve pages; `/dashboard` has five overlapping pages and unique model connections, scoped agent editing, full settings and conductor controls (`dashboard.py:121–125`; `shell.js:4–17`). Current overview docs describe dashboard as merely “smaller, plainer.” | Make `/console` the clear everyday entry. Add one **Administration** doorway containing advanced capabilities with shared terminology/component patterns. Until merged, document dashboard's full scope and cross-link it. | Every unique action has one discoverable authoritative doorway; no feature is silently lost in consolidation. |
| U12 / P2 | `ui.js:144–158` Drawer has no dialog role, label, Escape handling, focus trap or restoration. Job rows (`jobs.js:446`) and sortable `<th>` (`:402–439`) are mouse-click targets without key handlers. Approval queue nests checkbox inputs inside buttons (`pages.js:767–774`). Several inputs have placeholders/div text without associated labels. | Use accessible dialog primitives, native links/buttons inside cells, labelled controls and valid selectable-list markup. Keep sort dropdown as an alternative; add current-sort semantics. | Keyboard-only open/close/operate/return focus on each drawer; screen-reader names/states; valid interactive nesting. Static absence is verified; assistive-tech experience still needs runtime tests. |
| U13 / P2 | Sidebar is fixed 216px with 12 controls of minimum 48px, sticky `height:100vh` and no own scrolling (`shell.js:138–190`). Content padding and several minimum grid widths consume small screens. Responsive sidebar rules are embedded only in mounted Home/Score and Ask styles (`pages.js:631`, `ask.js:34`), so navigation can change geometry across pages. | Collapsible navigation on narrow widths, independently scrollable sidebar for short screens, and single-column forms/details. | At 320px width and short-height/200% zoom, every nav item and primary action remains reachable without horizontal page travel. Actual overflow needs browser verification. |
| U14 / P2 | `ui.js:160–188` removes toasts after 4.2 seconds, has no status/alert semantics, truncates bodies to one line, and offers no durable details. Several job handlers close editors without awaiting mutation success (`jobs.js:549,584,600`). | Await results, disable duplicate submit, keep errors beside the input, and retain an accessible result record linked to History. Do not close an unsuccessful form. | Controlled rejection stays visible and retains values; announcements are available; no double-submission while pending. |
| U15 / P2 | JobBoard retains a selected-ID set while filters change (`jobs.js:225–303`); toolbar counts eligible jobs but several requests send all selected IDs (`:298–320`). Hidden selection and mixed eligibility undermine batch predictability. | Clear selection on scope changes or visibly show **N selected, M hidden**. Before action show exact eligible count and skipped count; retain failures after response. | Filtering a selected set never silently authorizes hidden work. Mixed-state batch describes all outcomes. |
| U16 / P2 | `ops.js:1074–1091` renders every non-bool Gateway control as a text input even for choice metadata such as default sort. `/dashboard` already honors choice fields (`dashboard.py:831–841`). | Share the metadata renderer: switch, dropdown, labelled text or secret with field help. | Only allowed values are offered for choices; errors identify the actual field and retain unsaved values. |
| U17 / P2 | Console Connector Test uses saved settings (`ops.js:722`), while fields remain editable and Save is separate. A green test can coexist with unsaved form changes; Sync appears after test success and can create work (`:774`). | Show dirty state and **Save and test**; invalidate prior success when relevant fields change. Label sync **Import now** with a preview/count where backend supports it. | Unsaved credentials cannot appear successfully tested; save/test/sync outcomes are distinct and tied to a settings revision. |
| U18 / P2 | Governance copy promises “PDF cover page plus JSON” (`pages.js:935`) while export handler creates a single JSON blob (`:862–874`). | Call it **Download evidence JSON** and offer separate extract/download controls for returned file entries. Avoid implying legal certification. | Download labels and resulting filenames/types match exactly; bounded count/date coverage is visible. |
| U19 / P2 | Job Board takes the shared 200-job window (`adapter.js:98`), Activity fetch caps at 500 (`jobs.js:927`), Context at 25 (`pages.js:1124`), Projects at 5,000 (`adapter.js:34`). Home/Projects expose limits, but “All work” and “Every step every agent took” overstate completeness. | Display **Showing latest N** and searched date/range consistently. Offer real server pagination/filtering rather than silently treating client filters as global search. | Missing older items never produce an unqualified “nothing found”; every count states its window. |
| U20 / P2 | Connect AI filesystem actions are performed by gateway-side implementation; UI says apps “on this computer” and offers one-tap configuration (`ops.js:414–496`). A remote browser can interpret that as its own machine. | Show **Apps on [gateway host]** and a clear local/remote setup distinction. Only offer local installer-like claims for a local gateway. | A remote session names the target host before changes; client-side app discovery is not implied. |
| U21 / P3 | Sidebar Jobs/Approvals/Helpers/Activity names differ from plain page titles (All work/Needs your OK/Your helpers/What happened), `shell.js:71–73,162`. Project docs say Uncategorized while source says Unassigned work. | Use one noun per destination in nav, headings, links and search aliases; preserve older names only as aliases. | Search for either historical name finds the one canonical guide and current control name. |
| U22 / P3 | Existing `jev-routing.md:18–25` claims Settings → LLM Connections / Routing and sample model/confidence output, but neither routed GUI contains that task-routing form. `Overview`, `AutonomyControlCard`, `DemoLaunchPanel` and live-look components remain defined in `pages.js` but only Home is routed (`shell.js:121`). | Replace imaginary GUI steps with verified CLI instructions. Mark unreachable components as legacy/internal; remove obsolete guidance and screenshots after source owner review. | Every documented button has a reachable route and source reference; no mockup is presented as an implemented workflow. |
| U23 / P3 | Desktop row actions silently return without selection (`desktop/app.py:117–120`), operate only on `selection()[0]`, yet Tk table permits multiple selection by default (`:60`). Reload settings can stop changed/removed workers (`controller.py:163–186`) despite sounding read-only. | Use single-row selection or implement explicit multi-row semantics; disable actions until selected. Label reload **Apply fleet changes…** and show affected workers before mutation. | Selection/action scope is unambiguous; applying config never surprises the user by stopping an undisclosed worker. |
| U24 / P3 | Source paths and technical state locations appear in dashboard Live Look, while basic console has nested routes and some capabilities only in a second URL. Docs often expose long manual command/path workarounds for everyday actions. | Put operational effect and named host first, provide **Copy diagnostic details** disclosure for paths/IDs; make docs task-based with direct doorway links and recovery. Keep exact paths available for support. | A first-time operator identifies what is waiting and chooses the next action without reading a UUID/path; an expert can still copy exact evidence. |

## Small copy patch for the source owner to review

This is a proposal, not an applied source edit. In `ops.js`, replace only these strings; rebuild the generated console bundle after normal source review. No backend authority or behavior changes are proposed here.

```diff
- setNote(paused ? "Resumed. Work will pick up again." : "Paused. Jobs in progress finish.");
+ setNote(paused ? "New work can start again. Halted jobs need Retry." : "Active attempts halted. New work is paused.");
- <b>Pause everything</b>
+ <b>Stop work</b>
- Stops helpers from taking new work. Jobs in progress finish.
+ Halts active attempts and pauses new work. Resume allows new work; halted jobs need Retry.
```

For every reason prompt that can cancel, the minimal behavioral fix is:

```javascript
const reason = prompt("Reason for aborting run " + runId + "?");
if (reason === null) return;
// Only here submit with reason || "Aborted by operator".
```

The approval-role policy, kill-switch enforcement, scheduled-worker duplicate guard, tenant isolation and shadow/live-write gates must remain unchanged while simplifying wording and interaction.

## Documentation corrections to integrate

The new [GUI reference](gui-reference.md) addresses these, but old task guides must not contradict it:

- `desktop-app-and-tray.md`: remove erase-`~/.mco` as casual uninstall/recovery, qualify external logs, explain Stop/Exit/Reload/task-handoff effects, remove nonexistent GUI port editor, distinguish readiness from end-to-end success.
- `overview-dashboard.md`: `/dashboard` is a full technical admin interface; Activity is a fetched window; sidebar Advanced is not a universal admin doorway on clean main.
- `jev-routing.md`: remove unsupported routing GUI, invented model-output examples and “Zero Hallucination Routing” guarantee; keep sourced advisory boundaries.
- `Settings.md`, `approvals-and-governance.md`: use actual halt semantics, explain Resume/retry and conductor pause distinction; correct evidence-download type.
- `Schedules.md`, `scheduling-and-loops.md`: name gateway-detected timezone and show the remote-host limitation.
- `projects-dashboard.md`: use Unassigned work and explain clearing explicit assignment can fall back to workflow grouping.
- `agent-fleet-and-presence.md`, `job-board-and-tasks.md`: distinguish clean main from pending admin doorway/reassignment repair; registration/presence is not task execution proof.
- `drumline-memory.md`: eligible ranked recall is not every saved entry injected into every prompt; promotion is separate from discussion and approval.

## Controlled runtime checks still needed

These are checks to run against a disposable fixture gateway with synthetic jobs/tokens/configuration, never permission to operate production:

1. Capture desktop/mobile/short viewport at 100% and 200% zoom, light/dark, reduced motion, keyboard-only and screen-reader naming.
2. Exercise disconnected, connecting, Live, Offline, invalid token and each relevant scoped denial; confirm no demo fallback is mistaken for successful production recovery.
3. Use deterministic fixture responses for partial batch results, slow/error saves, duplicate clicks and dependency outside the loaded job window.
4. Test Cancel in every destructive prompt and backdrop/drawer dismissal with an unsaved draft.
5. Verify JSON evidence download, file entries and UTC date boundary handling without claiming certification.
6. Exercise browser/gateway timezone differences and a DST transition with the scheduler disabled; inspect written fixture schedule only.
7. Run desktop controller mocks for selected/all/Exit/task handoff with active ownership records; no actual worker termination needed for the UI test.
8. Model-provider Test should use a mocked response unless a provider owner authorizes a real request; service health is not a replacement for an end-to-end worker receipt.

## Source key

| Alias | Repository path |
|---|---|
| shell.js | `src/mco/console_src/8ec84a72-7b37-434d-a68b-2ac1188e3e6d.js` |
| jobs.js | `src/mco/console_src/5adac14f-6645-4e02-866b-22c4e571989b.js` |
| pages.js | `src/mco/console_src/43b328d0-9d0e-4fce-a105-be0a939d7e48.js` |
| ops.js | `src/mco/console_src/2ed3f6b1-e1c6-43fc-8b29-31917195cbd5.js` |
| adapter.js | `src/mco/console_src/47e66145-9c4d-41a1-acbb-42b12848f160.js` |
| ui.js | `src/mco/console_src/32c20ad6-cc53-43b3-a432-3a75e249b495.js` |
| dashboard.py | `src/mco/dashboard.py` |
| admin_routes.py | `src/mco/orchestrator/admin_routes.py` |
| routes.py | `src/mco/orchestrator/routes.py` |
| leases.py | `src/mco/orchestrator/leases.py` |
| schedules_routes.py | `src/mco/orchestrator/schedules_routes.py` |
| schedules_plain.py | `src/mco/schedules_plain.py` |
| desktop/app.py / controller.py | `src/mco/desktop/app.py`, `src/mco/desktop/controller.py` |

Line numbers describe the inspected shared snapshot. Clean-main source hashes and pending-change classifications are recorded in [GUI coverage](_review/gui-feature-coverage.json); verify line positions after concurrent edits. Hidden design tools and unreachable legacy components are not counted as normal operational screens. No claim of screenshot/runtime verification is made.


## Changes included in this refresh

The documentation refresh branch fixes the technical dashboard's **Cancel** behavior for Abort Run and batch cancellation. Cancel now returns before any request. Six JavaScript regression cases exercise cancelled, blank-confirmed and reason-confirmed paths. This source patch has not been applied to an already running gateway; installed builds may still need the update. No execution policy or approval requirement changed.

The help site now uses one task-first home, searchable sections, stable command/control anchors, progressive disclosure for secondary guides, keyboard search, copy feedback and mobile navigation. These are implemented help-site improvements, not a claim that all product recommendations above have shipped.

## Apple guidance used for the review

- [Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles/): keep meanings and interactions consistent, with useful feedback and room for character.
- [Layout](https://developer.apple.com/design/human-interface-guidelines/layout): use progressive disclosure to keep common work easy to find.
- [Writing](https://developer.apple.com/design/human-interface-guidelines/writing): use concise action labels and familiar terms.
- [Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback): make status visible without unnecessary interruption.
- [Alerts](https://developer.apple.com/design/human-interface-guidelines/alerts): reserve disruptive confirmation for consequential actions; a cancelled action must remain cancelled.

Apple's guidance is a design lens, not a claim of Apple certification or an instruction to copy an Apple product.
