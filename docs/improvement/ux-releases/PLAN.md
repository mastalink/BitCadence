# Four-release UX implementation ledger

Owner approved November13: implement all four releases and keep wiki current. Base origin/main d511c6d; PR173 already merged with CI passing. Work stays isolated from the shared checkout.

## Release1: existing wiki and Cancel safety
PR173 merged. Verify remaining individual Reject cancellation and keep existing safety regression behavior. No assumption that a running gateway has reloaded source.

## Release2: trustworthy actions/status
U01 stop/resume meaning; U02 all reason-dialog Cancel; U03 partial batch feedback; U04 eligible reassignment/schema; U05 approval versus execution state; U06 precise permission denial; U07 explicit schedule timezone/DST; U08 read-only desktop handoff preview. Preserve policy and ownership boundaries.

## Release3: simpler everyday work and accessibility
U09 visible credential doorway; U10 obvious connection/clearly named detail scopes; U12 dialog focus/Escape/keyboard controls; U13 common responsive navigation; U14 pending/success/error feedback and draft retention; U15 predictable selection scope; U16 metadata choices; U17 saved/tested connector revision; U18 truthful JSON evidence label; U19 honest data windows; U20 gateway-host disclosure; U21 consistent destinations; U23 desktop action scope/apply-changes preview.

## Release4: consolidation and polish
U11 one Administration hub for advanced capabilities, reuse existing implementations and keep old URLs; U22 current routed documentation; U24 human effects first with diagnostic details available. No feature deletion or permission widening.

## Owners
Adapter/jobs: cli_wiki worker, unique fixture tests. Operations/schedules/desktop: gui_wiki worker, unique fixture tests. Shared UI/shell/pages/dashboard, integration/build/docs/browser/release: root. No concurrent generated artifact writes; root builds console/wiki after sources settle.

## Acceptance
Use real production functions with synthetic data only. Cancel=>zero writes, confirmed action=>one write; mixed batch accurately retains failures; approved/pending never Completed; truthful denial; browser/gateway timezone difference and DST fixtures; desktop preview performs no process/task mutations. All navigation/controls reachable at390px/short viewport, keyboard dialogs trap/restore focus and close onEscape, errors remain available, no state/feature lost through Administration. Regenerate CLI/GUI coverage, build console/wiki, run appropriate tests, independent review and normal PRchecks. Live changes only after checks and without interrupting active ownership.

## Progress
- PR173 merged; fresh source selected.
- Releases2–4 implemented in the isolated branch; existing specialist capabilities remain reachable through Administration. No token, policy, tenant, lease or approval boundary widened.
- Focused safety/status/integration suite: 105 passed. Follow-up shell, Connect and installer tests: 37 passed after the autofocus restoration fix and explicit Bash executable resolution.
- Disposable authenticated Edge browser: 29 checks passed, no page errors or nonfixture API calls. Covers all13 destinations, Administration deeplink/modal, keyboard containment/restoration, 320/390px layouts and short navigation.
- Console round-trip: 9 sources, zero differences. CLI:95 paths/308 parameters, zero coverage errors. GUI:319 mappings/33 settings, strict hashes pass. CLI examples:414 pass,1 placeholder skip. Wiki:38 pages/440 search sections, no broken links.
- Hosted wiki preview search, Escape and mobile menu passed. Current launch film/homepage preserved.
- Independent review found two issues (prefixed gateway API boundary, competing nested dialog traps); both corrected with regression fixtures. Browser testing found child autofocus prevented return to opener; corrected and browser-verified.
- Full-suite/hosted CI and normal PR integration are the next release gates. The initial full local run selected the unavailable Windows WSL launcher for two Bash tests; explicit executable lookup fixes the harness, and both tests pass with Git Bash.
- Live gateway/desktop installation is a separate boundary: active leased work was observed. Do not overwrite the dirty shared checkout, restart it, transfer workers or imply the running gateway has these changes merely because source/wiki is published.

## Deliberate scope limits
Data-window counts and exact-ID recovery replace misleading completeness claims; global historical pagination is future work. Existing schedules and Ask repeats retain existing zone behavior. Connector dry-run counts are not fabricated. Notification history is session-only; closing an editor does not save its draft. Desktop handoff preview reports component/task scope, with job/lease state unknown. Browser checks do not establish screen-reader conformance or prove native Windows interaction. See docs/wiki/UX-REVIEW.md for every U01–U24 boundary.
