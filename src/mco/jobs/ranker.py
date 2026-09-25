"""Service job ranking engine with Jev scoring and deterministic heuristic fallback.

Combines typed Jev scores with explicit documented weights into a 0-100 rank.
When Jev times out, fails, or is unconfigured, deterministically falls back to
a heuristic ranker, notes the fallback reason, and preserves the decision receipt
and probabilities for audit.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mco.jobs.filters import HardFilterEngine
from mco.jobs.models import JobPosting, RankedJob
from mco.orchestrator.jev import DecisionReceipt, JevProvider, get_question_set
from mco.orchestrator.jev_questions import SERVICE_JOB_FIT, SERVICE_JOB_FIT_VERSION


# ── Explicit Ranking Weights (Documented Constants) ──────────────────────────
# All weights sum to exactly 1.00.
WEIGHT_FLEET_FIT = 0.30       # Can our fleet deliver a tested, checkable result?
WEIGHT_VALUE = 0.25           # Budget vs realistic engineering effort
WEIGHT_CLARITY = 0.15         # Inferable acceptance criteria and explicit specs
WEIGHT_CLIENT_QUALITY = 0.15  # Payment verified, high rating, lifetime spend
WEIGHT_DELIVERY_RISK = 0.10   # Delivery safety / minimal scope risk (1.0 = lowest risk)
WEIGHT_COMPETITION = 0.05     # Proposal volume / early opportunity (1.0 = low proposals)

assert abs((WEIGHT_FLEET_FIT + WEIGHT_VALUE + WEIGHT_CLARITY +
            WEIGHT_CLIENT_QUALITY + WEIGHT_DELIVERY_RISK + WEIGHT_COMPETITION) - 1.0) < 1e-6


# ── Positive and Negative Heuristic Signals ─────────────────────────────────
_AUTOMATION_KEYWORDS = re.compile(
    r"\b(?:python|automation|api|fastapi|flask|django|cli|pytest|unittest|test\s+suite|"
    r"script|scripting|bot|integration|backend|sqlite|postgres|docker|playwright|selenium|"
    r"etl|data\s+pipeline|github\s+actions?|webhook|cron)\b",
    re.IGNORECASE,
)

_NON_SOFTWARE_KEYWORDS = re.compile(
    r"\b(?:wordpress|shopify|figma|ui\s+design|logo|copywriting|seo|video\s+editing|"
    r"social\s+media|virtual\s+assistant|sales\s+call|telemarketing|voiceover|translator)\b",
    re.IGNORECASE,
)

_SPEC_KEYWORDS = re.compile(
    r"\b(?:deliverables?|acceptance\s+criteria|requirements?|specifications?|inputs?|outputs?|"
    r"schema|swagger|openapi|postman|test\s+cases?|expected\s+behavior|definition\s+of\s+done)\b",
    re.IGNORECASE,
)

_RISK_KEYWORDS = re.compile(
    r"\b(?:urgent|asap|immediately|within\s+\d+\s+hours?|quick\s+fix|simple\s+task|"
    r"flexible\s+scope|ongoing\s+undefined|undocumented|reverse\s+engineer)\b",
    re.IGNORECASE,
)


def deterministic_heuristic_scores(posting: JobPosting) -> Dict[str, float]:
    """Deterministic offline heuristic scoring for job postings.

    Produces normalized scores (0.0 to 1.0) for:
    - fleet_fit: alignment with autonomous coding and test automation
    - value: budget vs expected engineering effort
    - clarity: structure, length, and presence of acceptance criteria
    - client_quality: payment verification, rating, and spend history
    - competition: proposals count (fewer proposals = higher opportunity score)
    - delivery_risk: lower risk signals = higher score (1.0 = minimal risk)
    """
    text = f"{posting.title}\n{posting.description}\n{' '.join(posting.skills)}"

    # 1. Fleet Fit (0.0 to 1.0)
    fit_score = 0.50
    auto_matches = len(_AUTOMATION_KEYWORDS.findall(text))
    non_matches = len(_NON_SOFTWARE_KEYWORDS.findall(text))
    fit_score += min(auto_matches * 0.12, 0.45)
    fit_score -= min(non_matches * 0.20, 0.45)
    fit_score = max(0.05, min(fit_score, 1.0))

    # 2. Value (0.0 to 1.0)
    val_score = 0.25
    btype = (posting.budget_type or "").strip().lower()
    bmax = posting.budget_max if posting.budget_max is not None else posting.budget_min

    if btype == "fixed" and bmax is not None:
        if bmax >= 2500:
            val_score = 1.00
        elif bmax >= 1500:
            val_score = 0.90
        elif bmax >= 800:
            val_score = 0.80
        elif bmax >= 500:
            val_score = 0.65
        elif bmax >= 300:
            val_score = 0.50
        elif bmax >= 150:
            val_score = 0.30
        elif bmax >= 50:
            val_score = 0.15
        else:
            val_score = 0.05
    elif btype == "hourly" and bmax is not None:
        if bmax >= 100:
            val_score = 1.00
        elif bmax >= 75:
            val_score = 0.85
        elif bmax >= 50:
            val_score = 0.70
        elif bmax >= 35:
            val_score = 0.50
        elif bmax >= 20:
            val_score = 0.30
        else:
            val_score = 0.10
    elif bmax is not None:
        if bmax >= 500:
            val_score = 0.70
        elif bmax >= 300:
            val_score = 0.50
        else:
            val_score = 0.20

    # 3. Clarity (0.0 to 1.0)
    clarity_score = 0.40
    words = len(posting.description.split())
    if words >= 200:
        clarity_score += 0.25
    elif words >= 80:
        clarity_score += 0.15
    elif words < 25:
        clarity_score -= 0.25

    if any(marker in posting.description for marker in ("- ", "* ", "1.", "\n-", "\n*")):
        clarity_score += 0.15
    spec_matches = len(_SPEC_KEYWORDS.findall(posting.description))
    clarity_score += min(spec_matches * 0.10, 0.25)
    clarity_score = max(0.05, min(clarity_score, 1.0))

    # 4. Client Quality (0.0 to 1.0)
    client_score = 0.30
    client = posting.client
    if client.payment_verified is True:
        client_score += 0.30
    elif client.payment_verified is False:
        client_score -= 0.15

    if client.rating is not None:
        if client.rating >= 4.8:
            client_score += 0.25
        elif client.rating >= 4.5:
            client_score += 0.15
        elif client.rating < 4.0:
            client_score -= 0.15

    if client.total_spent is not None:
        if client.total_spent >= 10000:
            client_score += 0.20
        elif client.total_spent >= 1000:
            client_score += 0.10

    if client.hire_rate is not None and client.hire_rate >= 0.50:
        client_score += 0.05
    client_score = max(0.05, min(client_score, 1.0))

    # 5. Competition (0.0 to 1.0, 1.0 = lowest competition)
    comp_score = 0.50
    props = posting.proposals_count
    if props is not None:
        if props < 5:
            comp_score = 1.00
        elif props < 10:
            comp_score = 0.80
        elif props < 15:
            comp_score = 0.60
        elif props < 25:
            comp_score = 0.40
        elif props < 50:
            comp_score = 0.20
        else:
            comp_score = 0.05

    # 6. Delivery Risk (0.0 to 1.0, 1.0 = lowest risk / safest)
    risk_score = 0.70
    risk_matches = len(_RISK_KEYWORDS.findall(posting.description))
    risk_score -= min(risk_matches * 0.15, 0.45)
    if clarity_score > 0.7:
        risk_score += 0.15
    risk_score = max(0.05, min(risk_score, 1.0))

    return {
        "fleet_fit": round(fit_score, 3),
        "value": round(val_score, 3),
        "clarity": round(clarity_score, 3),
        "client_quality": round(client_score, 3),
        "competition": round(comp_score, 3),
        "delivery_risk": round(risk_score, 3),
    }


def compute_composite_rank(scores: Mapping[str, float]) -> float:
    """Combine score mapping with documented weights into 0-100 rank."""
    raw = (
        WEIGHT_FLEET_FIT * scores.get("fleet_fit", 0.0)
        + WEIGHT_VALUE * scores.get("value", 0.0)
        + WEIGHT_CLARITY * scores.get("clarity", 0.0)
        + WEIGHT_CLIENT_QUALITY * scores.get("client_quality", 0.0)
        + WEIGHT_DELIVERY_RISK * scores.get("delivery_risk", 0.0)
        + WEIGHT_COMPETITION * scores.get("competition", 0.0)
    )
    return max(0.0, min(raw * 100.0, 100.0))


class JobRanker:
    """Ranks job postings using Jev when available with deterministic heuristic fallback."""

    def __init__(self, provider: Optional[JevProvider] = None) -> None:
        self.provider = provider

    def rank_posting(self, posting: JobPosting) -> RankedJob:
        # Step 1: Deterministic hard filters in CODE (never delegated to Jev)
        filter_result = HardFilterEngine.evaluate(posting)

        # Step 2: Attempt Jev ranking if provider is available
        scores: Dict[str, float]
        rank_source = "jev"
        fallback_reason: Optional[str] = None
        receipt_dict: Optional[Dict[str, Any]] = None

        jev_success = False
        if self.provider is not None:
            try:
                questions = get_question_set(SERVICE_JOB_FIT, SERVICE_JOB_FIT_VERSION)
                # State sent to Jev: the normalized posting only
                state = posting.to_dict()
                receipt = self.provider.decide(
                    use_case_id=SERVICE_JOB_FIT,
                    question_set_version=SERVICE_JOB_FIT_VERSION,
                    state=state,
                    questions=questions,
                )
                receipt_dict = receipt.to_dict()

                if receipt.outcome in {"success", "shadow"} and receipt.answers:
                    parsed_scores: Dict[str, float] = {}
                    for qname in ("fleet_fit", "value", "clarity", "client_quality", "competition", "delivery_risk"):
                        ans = receipt.answers.get(qname)
                        if isinstance(ans, dict) and "score" in ans:
                            s = float(ans["score"])
                            # Normalize if on a 0-4 rubric scale
                            parsed_scores[qname] = s if s <= 1.0 else s / 4.0

                    if len(parsed_scores) == 6:
                        scores = parsed_scores
                        jev_success = True
                    else:
                        fallback_reason = "Jev response missing required scores"
                else:
                    err = receipt.error_class or receipt.outcome or "fallback"
                    fallback_reason = f"Jev decision outcome '{receipt.outcome}' ({err})"
            except Exception as exc:
                fallback_reason = f"Jev call failed: {type(exc).__name__}: {exc}"

        if not jev_success:
            rank_source = "heuristic_fallback"
            if not fallback_reason:
                fallback_reason = "Jev provider disabled or unconfigured"
            scores = deterministic_heuristic_scores(posting)

        # Step 3: Compute final 0-100 rank
        if not filter_result.eligible:
            # Excluded jobs receive rank 0.0
            final_rank = 0.0
        else:
            final_rank = compute_composite_rank(scores)

        return RankedJob(
            rank=final_rank,
            posting=posting,
            scores=scores,
            eligible=filter_result.eligible,
            reasons=filter_result.reasons,
            rank_source=rank_source,
            fallback_reason=fallback_reason,
            receipt=receipt_dict,
        )

    def rank_postings(self, postings: Sequence[JobPosting]) -> List[RankedJob]:
        ranked = [self.rank_posting(p) for p in postings]
        # Order: eligible postings first by rank descending, then ineligible
        ranked.sort(key=lambda r: (r.eligible, r.rank), reverse=True)
        return ranked
