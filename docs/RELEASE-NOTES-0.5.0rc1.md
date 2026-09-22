# BitCadence v0.5.0rc1 — Jev public preview

This is a GitHub prerelease, not the stable 0.5.0 release. The documented
live AWS release-candidate chaos board remains the stable-release gate.

## Why this release

The agent doing deep work should not spend its context on every small routing
or relevance judgment. BitCadence now has an optional two-speed path: TypeSafe
Jev returns typed, bounded semantic advice; Codex, Claude, and other workers
handle the longer work. Deterministic policy still owns capacity eligibility,
leases, budgets, human approval, and effects. Jev is not an approver or
autonomous conductor. A separately owner-approved VIA Lorain path may use Jev
to select among schedule candidates extracted from retained parish evidence,
with code revalidation and deterministic fallback. That implementation is not
part of this release candidate. We are not claiming a measured speed or cost
gain.

## What is in this preview

- Advisory Codex task routing, including deterministic model/capacity rules and
  a narrow `mco_jev_route` MCP entry point.
- Advisory Claude model routing and annotation-only Jev checks for Drumline,
  watchdog symptoms, and notification quality.
- Decision receipts and safe deterministic fallback when Jev is disabled,
  unavailable, or abstains. Jev remains disabled by default.
- The updated [website](https://bitcadence.ai/#jev) and
  [setup/authority guide](https://github.com/mastalink/BitCadence/blob/main/docs/JEV-DECISION-PROVIDER.md).

## Start safely

Install BitCadence as usual; no TypeSafe account is required for core work.
If you want Jev, configure the server-side key and `shadow` mode using the
[PowerShell walkthrough](https://github.com/mastalink/BitCadence/blob/main/docs/JEV-DECISION-PROVIDER.md#first-time-setup-on-windows-powershell),
then call the explicit connection test. Do not put the key in a browser, web
page, agent job, or repository. Keep shadow mode while reviewing receipts and
fallbacks. The dedicated Jev key form in the Admin Console is not yet shipped.

The Python package version is `0.5.0rc1`; the CLI remains `mco`, with existing
`MCO_*` settings and `~/.mco` paths unchanged. GitHub Actions attaches the
source distribution and wheel and builds the matching GHCR image. PyPI
publication is best-effort until trusted publishing is configured.
