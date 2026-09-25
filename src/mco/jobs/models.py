"""Normalized job posting and ranking models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class ClientInfo:
    country: Optional[str] = None
    payment_verified: Optional[bool] = None
    total_spent: Optional[float] = None
    hire_rate: Optional[float] = None
    rating: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "country": self.country,
            "payment_verified": self.payment_verified,
            "total_spent": self.total_spent,
            "hire_rate": self.hire_rate,
            "rating": self.rating,
        }

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> ClientInfo:
        if not data:
            return cls()
        def _parse_float(val: Any) -> Optional[float]:
            if val is None or val == "":
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        def _parse_bool(val: Any) -> Optional[bool]:
            if val is None:
                return None
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                return bool(val)
            s = str(val).strip().lower()
            if s in {"true", "1", "yes", "verified", "y"}:
                return True
            if s in {"false", "0", "no", "unverified", "n"}:
                return False
            return None

        return cls(
            country=str(data["country"]).strip() if data.get("country") is not None else None,
            payment_verified=_parse_bool(data.get("payment_verified")),
            total_spent=_parse_float(data.get("total_spent")),
            hire_rate=_parse_float(data.get("hire_rate")),
            rating=_parse_float(data.get("rating")),
        )


@dataclass
class JobPosting:
    source: str
    id: str
    title: str
    description: str
    budget_type: Optional[str] = None  # "fixed", "hourly", or None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    skills: List[str] = field(default_factory=list)
    client: ClientInfo = field(default_factory=ClientInfo)
    proposals_count: Optional[int] = None
    posted_at: Optional[str] = None
    url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "budget_type": self.budget_type,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "skills": list(self.skills),
            "client": self.client.to_dict(),
            "proposals_count": self.proposals_count,
            "posted_at": self.posted_at,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> JobPosting:
        def _parse_float(val: Any) -> Optional[float]:
            if val is None or val == "":
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        def _parse_int(val: Any) -> Optional[int]:
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        raw_skills = data.get("skills") or []
        skills: List[str] = []
        if isinstance(raw_skills, list):
            for s in raw_skills:
                if isinstance(s, dict):
                    name = s.get("name") or s.get("skill") or ""
                    if name:
                        skills.append(str(name).strip())
                elif s:
                    skills.append(str(s).strip())
        elif isinstance(raw_skills, str):
            for s in raw_skills.replace(";", ",").split(","):
                if s.strip():
                    skills.append(s.strip())

        raw_client = data.get("client")
        if isinstance(raw_client, dict):
            client = ClientInfo.from_dict(raw_client)
        else:
            client = ClientInfo(
                country=str(data.get("client_country")).strip() if data.get("client_country") else None,
                payment_verified=data.get("client_payment_verified"),
                total_spent=_parse_float(data.get("client_total_spent")),
                hire_rate=_parse_float(data.get("client_hire_rate")),
                rating=_parse_float(data.get("client_rating")),
            )

        budget_type = data.get("budget_type")
        if budget_type:
            budget_type = str(budget_type).strip().lower()

        return cls(
            source=str(data.get("source") or "unknown"),
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            budget_type=budget_type,
            budget_min=_parse_float(data.get("budget_min")),
            budget_max=_parse_float(data.get("budget_max")),
            skills=skills,
            client=client,
            proposals_count=_parse_int(data.get("proposals_count")),
            posted_at=str(data.get("posted_at")) if data.get("posted_at") else None,
            url=str(data.get("url")) if data.get("url") else None,
        )


@dataclass
class RankedJob:
    rank: float  # 0.0 to 100.0
    posting: JobPosting
    scores: Dict[str, float]
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    rank_source: str = "jev"  # "jev" or "heuristic_fallback"
    fallback_reason: Optional[str] = None
    receipt: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": round(self.rank, 1),
            "posting": self.posting.to_dict(),
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "rank_source": self.rank_source,
            "fallback_reason": self.fallback_reason,
            "receipt": self.receipt,
        }
