# Coverage and verification

This wiki was checked against the BitCadence source snapshot on **November 13, 2026**. It separates everyday walkthroughs from exact references, and shipped routes from proposed designs.

## CLI coverage

The [complete CLI reference](mco-cli-reference.md) maps **95 command/group paths** and **308 arguments and options** across the shared `bitcadence` / `mco` tree. It includes nested groups, defaults, required arguments, option aliases, repeatable values, hidden compatibility options and synthesized help/completion options. Each command has a purpose, safe example, expected result and recovery notes.

The [machine-readable inventory](_review/cli-coverage.json) binds every path and parameter to a documentation anchor and source metadata. The checker compares the actual Typer command tree without invoking operational callbacks. Unknown commands/options, changed defaults and missing anchors are failures. Negative-control checks also verified that the checker rejects missing/stale commands and parameters.

The desktop executable is separate and is covered under [Windows Local Control and tray](gui-reference.md#windows-local-control-and-tray).

## GUI coverage

The [GUI reference](gui-reference.md) covers thirteen console destinations, Score/detail views, the five-page dashboard, administration/lock/modal flows, Windows/tray actions and **33 server-driven settings**. Its [319 grouped control/workflow mappings](_review/gui-feature-coverage.json) link instructions to current component/function locations. This describes the UX branch based on parent `d511c6d`, not a running-app update.

This is grouped coverage, not an enumeration of every dynamic record/option. Unreachable legacy components are identified separately. Isolated behavior fixtures support the [U01–U24 status ledger](UX-REVIEW.md); deployment, real accessibility and end-to-end delivery are not inferred from them.

The generator resolves component/function anchors afresh. Source fingerprints were frozen after the final application changes, and strict hash verification passed. On the next change, run `python docs/wiki/_review/build_inventory.py --freeze-source-hashes` after the source settles, then the strict hash verifier. Hashes prove only the baseline, not rendered behavior or effects.

## What was checked safely

- Recursive command/parameter coverage, required documentation anchors and parser-valid examples.
- Every local documentation link, image and fragment in the generated website.
- The help site's section search, keyboard access, copy controls and narrow-screen navigation.
- Regression tests for technical-dashboard prompt cancellation: pressing Cancel submits no abort or batch-cancel request; an intentional blank confirmation still follows the supported behavior.
- Twenty-nine browser checks against a disposable authenticated gateway: thirteen navigation destinations, Administration deep links and modal, dialog Tab/Escape/focus return, 390px and 320px layouts, and short-screen navigation. No browser script errors. This does not establish screen-reader conformance or native desktop acceptance.
- Legacy-domain redirects for both old hostnames, HTTP/HTTPS, root and path/query requests; removal of the obsolete site project.

No real jobs were approved, cancelled, retried, reassigned or published to perform these checks. No live helper was stopped and no production token/configuration was changed. Provider calls and destructive product controls require disposable fixtures or separate operational authorization, not a documentation test against someone's live work.

## Current limits and known issues

The [usability review](UX-REVIEW.md) contains source-confirmed issues and concrete acceptance checks. Product GUI behavior is source-reviewed; this refresh does not claim that every backend integration was exercised on every operating system. Existing screenshots illustrate the workflow and may precede small label changes.

The Cancel-prompt fix is included in this source branch. An already-running gateway does not acquire a code patch just because the public help site was published. The live product needs its normal reviewed release/update before that fix can be assumed installed.

## Keep the wiki complete

From a source checkout, using the project's Python environment:

```bash
python docs/wiki/_review/verify_cli_coverage.py --repo .
python docs/wiki/_review/verify_gui_coverage.py --repo . --strict-hashes
python docs/wiki/_review/verify_cli_examples.py docs/wiki
python scripts/build_wiki.py
```

CI runs the coverage checkers and site build alongside the tests. When a feature changes, update its steps, expected result, failure/recovery guidance and inventory entry, then rebuild. Keep credentials and household/machine-specific data out of public documentation.
