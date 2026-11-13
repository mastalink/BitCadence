# Interface improvement status: U01–U24

Current status reviewed November 13, 2026 against the UX branch based on parent `d511c6d`. **Implemented** means source exists; **fixture tested** means isolated regressions cover the named behavior. Neither means the running app is updated, production work completed, or a deployment occurred. No live jobs, tokens, schedules, provider requests, worker handoffs or GPU operations were used for this review.

The [GUI reference](gui-reference.md) gives current instructions. Existing wiki URLs remain valid; older screenshots may show old labels. Source fingerprints are frozen and strictly verified. A disposable authenticated gateway passed 29 browser checks: all thirteen main destinations, Administration deep links and modal, Tab/Escape/focus return, 390px and 320px layouts and short-screen navigation, with no browser script errors. Broader desktop, assistive-technology and production end-to-end acceptance remains pending; individual limits below still apply.

## Current implementation ledger

Source aliases are expanded in the Source key below. Named functions/components locate current source; the historical line numbers further down are not current implementation references.

| ID | Status and current source | Evidence and limits |
|---|---|---|
| U01 | **Implemented:** `ops.js` → `PlainSettings` offers Stop work… → Stop work now / Cancel, truthful halt/Resume wording; advanced kill-switch save confirms too. Backend meaning is unchanged. | `test_ux_ops.py`: cancellation writes nothing; confirmation sends existing setting. Resume does not retry halted work. No live stop was performed. |
| U02 | **Implemented:** `dashboard.py` → `bulkAction`, `abortActiveRun`, `decide` return on prompt cancellation, including individual Reject. | `test_dashboard_cancel_prompts.py` covers Cancel paths. Native prompts remain; a new inline dialog design is not claimed. |
| U03 | **Implemented:** `adapter.js` → `batchAction` reads succeeded/failed; `jobs.js` → `JobBoard` shows succeeded/failed/skipped and per-item reasons, retains failures and replacement links. Session result state survives page remount. | `test_ux_jobs.py`: schema, partial/unreported outcomes and remount. This is not server-side history across browser restart. |
| U04 | **Implemented:** `jobActionEligible`, `JobDetail`, `JobBoard` and `reassignJob` use terminal-attempt eligibility and target fields; replacements are inspectable without repeating the mutation. | `test_ux_jobs.py`: state eligibility, payload and replacement lookup. Active jobs must be cancelled first; authority is unchanged. |
| U05 | **Implemented:** `pages.js` → `approvalDecision`, `Approvals` separate Approved/Rejected from execution status. | `test_ux_shell.py`: approval is not completion. Actual live event ordering remains runtime pending. |
| U06 | **Implemented:** `adapter.js` → `plainFailure` preserves denial reasons and distinguishes scope, approver, tenant and policy cases. | `test_ux_jobs.py`: denial fixtures. Existing local CLI repair is separate; no web authority promotion was added. |
| U07 | **Implemented for Schedules:** `SchedulesPage` sends an editable browser IANA zone and requires a matching preview. `schedules_routes.preview` / `schedules_plain.preview_schedule` validate it and calculate next date/offset with the existing scheduler. | `test_ux_ops.py`: payload, invalid-zone no-write, read-only preview, spring/autumn New York offsets. Existing schedules are not migrated; legacy clients and Ask repeat retain their existing server-default path. |
| U08 | **Implemented scope review:** desktop `review_handoff` / `preview_handoff` list components, observed PIDs/states and enabled tasks before existing handoff. Stop all versus Exit scope is visible. | `test_ux_desktop.py`: read-only preview and cancel/confirm. Work/lease status is unknown; process presence does not prove a worker is between jobs. Synchronous read latency and actual handoff remain runtime pending. |
| U09 | **Implemented:** `shell.js` → `Administration`, and Helpers' Manage credentials… doorway, expose AgentFleet independently of prototype copy tone. | Routing source inspected; safe administration links have `test_ux_shell.py` coverage. Credential mutations retain server checks; no real token was rotated. |
| U10 | **Implemented:** Technical details is the global/Experience label; Server administration is the connected Settings disclosure. Disconnected fields and shell Connect doorway are visible immediately. | `test_ux_ops.py`: disconnected setup has no disclosure barrier. Display preferences do not grant permissions. |
| U11 | **Implemented as a hub:** Administration groups credentials and configured-gateway specialist links. Dashboard has allowlisted hashes and Back to workspace. Links contain no bearer token; browser-storage reuse requires matching gateway origin/path. | `test_ux_shell.py`: safe URLs, allowlist, credential match. Specialist screens remain separate; no full component consolidation is claimed. |
| U12 | **Implemented core overlays/controls:** shared Drawer has dialog semantics, focus management, Tab containment, Escape, topmost nested-dialog handling and restoration. MemoryDetailDrawer uses that Drawer; the dashboard modal implements corresponding dialog behavior. Job open/sort uses native buttons and form labels improved. | Job fixtures cover native controls; shell fixtures cover memory/nested dialogs. Real browser Tab/Escape/focus-return checks passed for the composer and Administration modal. Screen-reader acceptance and older form audit remain pending. No accessibility conformance claim. |
| U13 | **Implemented layout changes:** shared shell scrolling sidebar and narrow-screen Menu/backdrop; Job Board uses stacked labeled rows up to760px with wide headlines, whole filter buttons and44px touch targets. Desktop keeps its table and header sorting; phones use Sort jobs. | Earlier no-overflow checks missed fragmented words. Follow-up `check_job_board_phone.py` checks320/390/430px in both detail modes: word rectangles, headline width/type size, long helper/source/workflow bounds, expanded selection clicks, select-all, reassignment controls, empty/search/sort and keyboard open/Escape. Disposable jobs remain unchanged. Visual screenshots reviewed; screen-reader and200%-zoom conformance are not claimed. |
| U14 | **Implemented core feedback:** ToastHost wraps messages, keeps error/approval notices until dismissal, and provides latest-40 Recent notifications. Job/approval actions await responses, show errors and prevent duplicate submission. | `test_ux_jobs.py` covers retained failure input/duplicate guard; `test_ux_shell.py` covers full notification text. History is session-only. Closing an unsaved editor is not a persistent save-draft feature. |
| U15 | **Implemented:** JobBoard constrains selection to visible scope, submits eligible IDs, reports skipped IDs, and retains failures/results. | `test_ux_jobs.py`: eligible-visible selection, partial results and remount. No universal batch undo across sessions. |
| U16 | **Implemented:** LiveControlSettings renders metadata choice dropdowns and existing boolean toggles. | `test_ux_ops.py`: real JSX options and choice submission. This is not a unified renderer for every legacy field. |
| U17 | **Implemented save-before-test binding:** ConnectorsCard invalidates old test/import availability on edits/save/reconnect and ignores late old test results; mutation is Import now. | `test_ux_ops.py`: dirty edits and delayed results after edit/reconnect. No import dry-run or pre-import item-count preview was added. |
| U18 | **Implemented label:** Governance says Download evidence JSON for the existing response envelope. | Handler/label source verified. Separate extraction/download controls for file entries are not implemented; download/date-boundary acceptance remains pending. No legal certification implied. |
| U19 | **Partially implemented:** Job Board states up-to-200 window, Activity states fetched count/500 cap, Context states 25 entries; exact-ID detail can retrieve an out-of-window job. Portfolio has separate aggregate coverage. | `test_ux_jobs.py`: bounded labels and exact-ID detail. Global server pagination/filtering and complete historical search were not added. |
| U20 | **Implemented:** ConnectPage names the gateway host and explains app changes happen on its computer; helper sign-in feedback also says gateway computer. | `test_ux_ops.py`: remote-target wording. App discovery and a successful client MCP call remain separate runtime checks. |
| U21 | **Implemented routed names:** shell normalizes Job Board, Approvals, Helpers and Activity across copy tones. Portfolio is current; saved `projects` page state maps to it. Wiki URLs remain. | Static headings/aliases checked. Some lower-level expert/plain vocabulary remains intentional. |
| U22 | **Documentation corrected:** Jev guide distinguishes actual CLI/provider setup from the nonexistent task-routing form; GUI reference identifies unreachable legacy components and current specialist doorways. | Source-route/link checks. Removing all legacy component definitions is not part of this work. |
| U23 | **Implemented:** desktop selection is single-row; selected actions disable without selection/while busy. Apply fleet changes… reviews add/change/remove effects before existing reload. | `test_ux_desktop.py`: button state, no-write preview, cancel/confirm. File/process scope can change before application; added definitions are not automatically started. |
| U24 | **Implemented scoped polish:** administration entry, truthful host/effect wording, current task guides, bounded views and source-indexed instructions reduce path hunting. Dashboard Live Look places technical paths in Show technical details and offers Copy diagnostic details. | Documentation/link and source verification. Diagnostic copy is local to Live Look; a universal support bundle and measured efficiency improvement are not claimed. |

Fixture sources: `tests/test_ux_ops.py`, `tests/test_ux_desktop.py`, `tests/test_ux_jobs.py`, `tests/test_ux_shell.py`, `tests/test_dashboard_cancel_prompts.py`. Final integrated-suite results belong to the release verification record; this ledger does not infer an all-green run.

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

## Runtime acceptance still required

1. Use a disposable gateway with synthetic jobs/tokens to check connection, scoped denial, reconnection, exact-ID recovery and notification history.
2. Test Escape/Tab/focus restoration, nested overlays, screen-reader names, 320px width, short height, 200% zoom, light/dark and reduced motion.
3. Exercise partial batch errors and delayed responses; closing an editor must not be confused with success or persistent draft storage.
4. Download evidence JSON and inspect file entries/date boundaries. Export is not certification.
5. Preview browser/server zone differences and DST with the scheduler disabled. Inspect legacy schedules/repeats separately.
6. Keep actual handoff, worker interruption and provider requests behind normal operational authority. Preview/fixture success does not authorize them.

Kill-switch enforcement, approver authority, tenant boundaries, scheduled-worker duplicate protection, leases and shadow/live-write policy remain authoritative. No running-app update or production delivery is inferred.
