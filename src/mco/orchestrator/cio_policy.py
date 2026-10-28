"""Deterministic policy checks for the claude-cio sidecar.

The sidecar itself is an LLM reading `ops/CIO_BRIEF.md` - free-form proposals
need judgment a fixed function cannot supply. But a handful of the brief's
rules ARE mechanical (a dollar figure against a cap, a category against a
prohibited list, "did the proposer also plan this"), and those are exactly
the rules an LLM under time pressure is most likely to round in its own
favor. This module makes them a function call instead of a vibe, so the
worker loop (or a test) can check a proposal the same way twice.

This is a policy boundary, not a dispatch adapter: it returns a verdict and
reasons, and never itself calls a notifier, the gateway, or Exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

APPROVE = "approve"
REJECT = "reject"
ESCALATE = "escalate"

CHIEF_ONLINE_THRESHOLD_SECONDS = 300

# Cents, to avoid float comparison on money. `None` cap = no automatic figure
# (Operate has "new spend under $10" with no monthly total defined here).
SPEND_CAPS_CENTS: dict[str, Optional[int]] = {
    "via": 15_000,
    "mymeals": 1_500,
    "sim_lab": 2_500,
    "operate": 1_000,
}
SIM_LAB_HARD_STOP_CENTS = 4_000

PROHIBITED_CATEGORIES = frozenset({
    "bank_client",
    "finance_client",
    "live_trading",
    "bypass_bot_check",
    "paid_github",
    "mac_claude",
    "codex_mac",
    "automated_client_messaging",
    "print_secret",
})

ESCALATE_CATEGORIES = frozenset({
    "security_credential_change",
    "legal_trademark_licensing",
    "real_money_finance",
    "delete_data",
} | PROHIBITED_CATEGORIES)


@dataclass(frozen=True)
class Proposal:
    """The minimum a proposal must state for policy to be checkable at all."""
    project: str = ""
    spend_cents: int = 0
    categories: frozenset[str] = field(default_factory=frozenset)
    proposed_by: str = ""


@dataclass(frozen=True)
class Decision:
    verdict: str
    reasons: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return self.verdict == APPROVE


def chief_is_online(claude_desktop_last_seen_seconds: Optional[float]) -> bool:
    """True when claude-desktop's own heartbeat means it - not the sidecar -
    holds the Chief seat right now."""
    if claude_desktop_last_seen_seconds is None:
        return False
    return claude_desktop_last_seen_seconds < CHIEF_ONLINE_THRESHOLD_SECONDS


def _normalize_project(project: str) -> str:
    return (project or "").strip().lower().replace(" ", "_").replace("-", "_")


def known_project(project: str) -> bool:
    """True when `project` has a defined spend cap in the brief.

    A project the brief never mentions has no defined cap to check against,
    which is not the same as "no cap" - `evaluate` must fail closed on it
    rather than reading absence-of-a-rule as approval.
    """
    return _normalize_project(project) in SPEND_CAPS_CENTS


def over_spend_cap(project: str, spend_cents: int) -> bool:
    key = _normalize_project(project)
    cap = SPEND_CAPS_CENTS.get(key)
    if cap is None:
        return False
    if key == "sim_lab" and spend_cents >= SIM_LAB_HARD_STOP_CENTS:
        return True
    return spend_cents >= cap


def evaluate(proposal: Proposal, *, decider: str) -> Decision:
    """Check one proposal against the brief's mechanical rules.

    `decider` is the identity about to render the verdict - passed
    explicitly (never inferred) so a self-approval check cannot be skipped
    by a caller who forgot to set it.

    Fails closed, not open: a proposal missing the identity needed to rule
    out self-approval, or naming spend against a project this module has no
    cap for, is escalated rather than silently approved. An LLM under time
    pressure is exactly the caller most likely to round a missing field in
    its own favor, which is the failure mode this module exists to remove.
    """
    reasons: list[str] = []

    proposed_by = proposal.proposed_by.strip()
    if not proposed_by:
        return Decision(
            ESCALATE,
            ("proposal has no `proposed_by` identity; self-approval cannot be "
             "ruled out, so it may not be auto-approved (route to Grok or to "
             "The owner)",),
        )

    decider_normalized = (decider or "").strip()
    if not decider_normalized:
        return Decision(
            ESCALATE,
            ("no `decider` identity was given; self-approval cannot be ruled "
             "out, so it may not be auto-approved (route to Grok or to "
             "The owner)",),
        )

    if proposed_by.casefold() == decider_normalized.casefold():
        return Decision(
            ESCALATE,
            (f"{decider} proposed this and may not also approve it (self-approval "
             "is never permitted; route to Grok or to the owner)",),
        )

    blocking = sorted(proposal.categories & PROHIBITED_CATEGORIES)
    if blocking:
        return Decision(
            REJECT,
            tuple(f"prohibited: {category}" for category in blocking),
        )

    escalating = sorted(proposal.categories & ESCALATE_CATEGORIES)
    if escalating:
        reasons.extend(f"escalation category: {category}" for category in escalating)

    if proposal.spend_cents > 0 and not known_project(proposal.project):
        reasons.append(
            f"project '{proposal.project or '(unspecified)'}' has no defined spend "
            f"cap; spend of ${proposal.spend_cents / 100:.2f} cannot be auto-approved"
        )
    elif over_spend_cap(proposal.project, proposal.spend_cents):
        reasons.append(
            f"spend ${proposal.spend_cents / 100:.2f} is at or over the "
            f"{proposal.project or 'unspecified'} cap"
        )

    if reasons:
        return Decision(ESCALATE, tuple(reasons))

    return Decision(
        APPROVE,
        (f"no prohibited or escalation category matched; spend "
         f"${proposal.spend_cents / 100:.2f} is within the {proposal.project or 'unspecified'} "
         "cap" if proposal.project else "no prohibited or escalation category matched",),
    )
