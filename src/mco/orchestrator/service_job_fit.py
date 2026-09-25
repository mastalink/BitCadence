"""Single source of truth for the service_job_fit TypeSafe question set."""

from __future__ import annotations

from typing import Any, Dict

SERVICE_JOB_FIT = "service_job_fit"
SERVICE_JOB_FIT_VERSION = "1.0.0"

SERVICE_JOB_FIT_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "fleet_fit": {
        "type": "score",
        "instructions": "Can our fleet deliver a tested, checkable code/automation result?",
        "criteria": [
            "No fit: Requires physical presence, hardware we do not have, or non-software manual labor",
            "Weak fit: Highly subjective, creative, or untestable deliverables without clear code boundaries",
            "Moderate fit: Standard software development or scripting, but complex or ambiguous test boundary",
            "Strong fit: Well-defined coding, API integration, or automation task with clear verification path",
            "Ideal fit: Fully automatable, deterministic, checkable software or script with unambiguous test suite",
        ],
    },
    "value": {
        "type": "score",
        "instructions": "Rate the financial value of this job relative to realistic required engineering effort.",
        "criteria": [
            "Severely underpriced: Disproportionately high effort for negligible budget; unprofitable",
            "Below market: Budget is lower than standard effort warrants",
            "Fair: Compensation is proportionate to expected development and review time",
            "Profitable: Attractive budget with reasonable, well-bounded scope and good margins",
            "Exceptional: High budget for cleanly bounded, highly automatable deliverable",
        ],
    },
    "clarity": {
        "type": "score",
        "instructions": "Are acceptance criteria, requirements, and completion conditions inferable from the description?",
        "criteria": [
            "Vague or incoherent: Lacks actionable details, specifications, or clear deliverables",
            "Ambiguous: General goal stated, but crucial technical requirements and acceptance criteria are missing",
            "Adequate: Core requirements outlined; scope is inferable with standard engineering assumptions",
            "Clear: Specific requirements, deliverables, and expectations are articulated clearly",
            "Exemplary clarity: Precise specifications, explicit inputs/outputs, and definitive acceptance criteria",
        ],
    },
    "client_quality": {
        "type": "score",
        "instructions": "Evaluate the reputation, payment verification, spending history, and hire rate of the client.",
        "criteria": [
            "High risk: Unverified payment, poor ratings, dispute history, or unrealistic punitive terms",
            "Unproven: New account with zero spend, unverified payment, or no historical track record",
            "Average: Verified payment with modest past spend or acceptable rating and hire rate",
            "Established: Verified payment, strong hire rate, 4.5+ rating, and substantial lifetime spend",
            "Premier: Top-tier client with verified payment, high hire rate, excellent ratings, and $10k+ spend",
        ],
    },
    "competition": {
        "type": "score",
        "instructions": "Assess competition level and likelihood of proposals standing out based on proposals count.",
        "criteria": [
            "Overcrowded: 50+ proposals already submitted; low visibility probability",
            "High competition: 20 to 50 proposals submitted",
            "Moderate competition: 10 to 19 proposals submitted",
            "Low competition: 5 to 9 proposals submitted",
            "Open opportunity: Less than 5 proposals submitted; high visibility likelihood",
        ],
    },
    "delivery_risk": {
        "type": "score",
        "instructions": "Assess the risk of delivery failure, scope creep, hostile requirements, or unverified dependencies.",
        "criteria": [
            "Extreme risk: High probability of scope creep, hostile client demands, or untestable third-party blockers",
            "Elevated risk: Significant third-party dependencies, undocumented APIs, or shifting specifications",
            "Moderate risk: Standard project complexity with ordinary integration uncertainties",
            "Low risk: Well-defined scope using mature technologies with minimal external risk factors",
            "Minimal risk: Self-contained, fully testable, robustly bounded deliverable with near-zero external dependencies",
        ],
    },
}
