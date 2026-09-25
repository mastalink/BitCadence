"""Upwork GraphQL API adapter for marketplace job postings search.

Queries the marketplaceJobPostingsSearch GraphQL endpoint behind a clean interface.
OAuth2 tokens are retrieved securely from environment variables or SecretVault,
and are NEVER logged or printed. Tests run against recorded fixtures only.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

import httpx

from mco.jobs.models import ClientInfo, JobPosting
from mco.secret_vault import SecretNotFoundError, SecretRef, VaultError, build_secret_vault


UPWORK_GRAPHQL_ENDPOINT = "https://api.upwork.com/graphql"

MARKETPLACE_JOB_SEARCH_QUERY = """
query marketplaceJobPostingsSearch($filter: MarketplaceJobPostingsFilter, $sortAttributes: [SortAttribute], $pagination: Pagination) {
  marketplaceJobPostingsSearch(filter: $filter, sortAttributes: $sortAttributes, pagination: $pagination) {
    totalCount
    edges {
      node {
        id
        ciphertext
        title
        description
        budget {
          amount
          currencyCode
        }
        hourlyBudget {
          min
          max
        }
        skills {
          name
        }
        client {
          location {
            country
          }
          paymentVerificationStatus
          totalSpent {
            amount
          }
          hireRate
          feedback {
            score
          }
        }
        proposalsCount
        publishedOn
        url
      }
    }
  }
}
"""


class UpworkError(Exception):
    """Base exception for Upwork adapter operations."""


class UpworkAuthenticationError(UpworkError):
    """Raised when the Upwork OAuth2 token is missing or rejected."""


@runtime_checkable
class JobPostingsSource(Protocol):
    """Interface for job posting search sources."""

    def search_jobs(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[JobPosting]:
        ...


class UpworkGraphQLAdapter(JobPostingsSource):
    """Adapter for searching Upwork marketplace job postings via GraphQL API."""

    def __init__(
        self,
        token: Optional[str] = None,
        config: Optional[Any] = None,
        db: Optional[Any] = None,
        org_id: str = "default",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 10.0,
    ) -> None:
        self._config = config
        self._db = db
        self._org_id = org_id
        self._transport = transport
        self._timeout = timeout
        self._token = token or self._resolve_token(config, db, org_id)

    @classmethod
    def _resolve_token(
        cls,
        config: Optional[Any] = None,
        db: Optional[Any] = None,
        org_id: str = "default",
    ) -> Optional[str]:
        """Resolve token from environment or SecretVault without logging."""
        for env_var in ("UPWORK_ACCESS_TOKEN", "UPWORK_TOKEN", "MCO_UPWORK_TOKEN"):
            val = os.environ.get(env_var)
            if val and val.strip():
                return val.strip()

        if config is not None:
            for key in ("UPWORK_ACCESS_TOKEN", "UPWORK_TOKEN", "MCO_UPWORK_TOKEN"):
                val = config.get(key)
                if val and str(val).strip():
                    return str(val).strip()

        try:
            vault = build_secret_vault(config, db)
            return vault.get(SecretRef(org_id=org_id, scope="upwork", name="token"))
        except (SecretNotFoundError, VaultError, Exception):
            pass

        try:
            vault = build_secret_vault(config, db)
            return vault.get(SecretRef(org_id=org_id, scope="upwork", name="api_key"))
        except (SecretNotFoundError, VaultError, Exception):
            pass

        return None

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    def search_jobs(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[JobPosting]:
        """Execute GraphQL search against Upwork marketplace API."""
        if not self._token:
            raise UpworkAuthenticationError(
                "Upwork OAuth2 token is not configured. Set UPWORK_ACCESS_TOKEN "
                "or configure 'upwork/token' in SecretVault."
            )

        variables: Dict[str, Any] = {
            "pagination": {"limit": limit},
        }
        if query:
            variables["filter"] = {"q": query}

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "query": MARKETPLACE_JOB_SEARCH_QUERY,
            "variables": variables,
        }

        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
            ) as client:
                resp = client.post(
                    UPWORK_GRAPHQL_ENDPOINT,
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise UpworkError(f"HTTP error connecting to Upwork API: {type(exc).__name__}") from None

        if resp.status_code in {401, 403}:
            raise UpworkAuthenticationError("Upwork API credentials rejected (401/403)")
        if resp.status_code >= 400:
            raise UpworkError(f"Upwork API returned HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception as exc:
            raise UpworkError("Failed to parse Upwork API JSON response") from exc

        return self.normalize_graphql_response(data)

    @classmethod
    def normalize_graphql_response(cls, response_dict: Mapping[str, Any]) -> List[JobPosting]:
        """Normalize Upwork marketplaceJobPostingsSearch response into JobPosting list."""
        if not isinstance(response_dict, Mapping):
            return []

        data_root = response_dict.get("data")
        if isinstance(data_root, Mapping):
            search_data = data_root.get("marketplaceJobPostingsSearch")
        else:
            search_data = response_dict.get("marketplaceJobPostingsSearch") or response_dict

        if not isinstance(search_data, Mapping):
            return []

        edges = search_data.get("edges") or []
        postings: List[JobPosting] = []

        for edge in edges:
            if not isinstance(edge, Mapping):
                continue
            node = edge.get("node")
            if not isinstance(node, Mapping):
                continue

            posting = cls._normalize_node(node)
            postings.append(posting)

        return postings

    @classmethod
    def _normalize_node(cls, node: Mapping[str, Any]) -> JobPosting:
        # ID and ciphertext
        job_id = str(node.get("id") or node.get("ciphertext") or "")
        ciphertext = node.get("ciphertext")

        # Title & description
        title = str(node.get("title") or "")
        description = str(node.get("description") or "")

        # Budget
        budget_type: Optional[str] = None
        budget_min: Optional[float] = None
        budget_max: Optional[float] = None

        raw_budget = node.get("budget")
        raw_hourly = node.get("hourlyBudget")

        if isinstance(raw_budget, Mapping) and raw_budget.get("amount") is not None:
            budget_type = "fixed"
            try:
                amt = float(raw_budget["amount"])
                budget_min = amt
                budget_max = amt
            except (TypeError, ValueError):
                pass
        elif isinstance(raw_hourly, Mapping):
            budget_type = "hourly"
            try:
                if raw_hourly.get("min") is not None:
                    budget_min = float(raw_hourly["min"])
                if raw_hourly.get("max") is not None:
                    budget_max = float(raw_hourly["max"])
            except (TypeError, ValueError):
                pass

        # Skills
        raw_skills = node.get("skills") or []
        skills: List[str] = []
        for s in raw_skills:
            if isinstance(s, Mapping) and s.get("name"):
                skills.append(str(s["name"]).strip())
            elif isinstance(s, str) and s.strip():
                skills.append(s.strip())

        # Client info
        raw_client = node.get("client") or {}
        country = None
        if isinstance(raw_client.get("location"), Mapping):
            country = raw_client["location"].get("country")

        verified_val = raw_client.get("paymentVerificationStatus")
        payment_verified = True if str(verified_val).upper() == "VERIFIED" else (
            False if verified_val is not None else None
        )

        total_spent = None
        if isinstance(raw_client.get("totalSpent"), Mapping):
            try:
                total_spent = float(raw_client["totalSpent"].get("amount"))
            except (TypeError, ValueError):
                pass

        hire_rate = None
        if raw_client.get("hireRate") is not None:
            try:
                hire_rate = float(raw_client["hireRate"])
            except (TypeError, ValueError):
                pass

        rating = None
        if isinstance(raw_client.get("feedback"), Mapping):
            try:
                rating = float(raw_client["feedback"].get("score"))
            except (TypeError, ValueError):
                pass

        client = ClientInfo(
            country=country,
            payment_verified=payment_verified,
            total_spent=total_spent,
            hire_rate=hire_rate,
            rating=rating,
        )

        # Proposals count
        proposals_count = None
        if node.get("proposalsCount") is not None:
            try:
                proposals_count = int(node["proposalsCount"])
            except (TypeError, ValueError):
                pass

        # Posted at & URL
        posted_at = node.get("publishedOn")
        url = node.get("url")
        if not url and ciphertext:
            url = f"https://www.upwork.com/jobs/{ciphertext}"

        return JobPosting(
            source="upwork",
            id=job_id,
            title=title,
            description=description,
            budget_type=budget_type,
            budget_min=budget_min,
            budget_max=budget_max,
            skills=skills,
            client=client,
            proposals_count=proposals_count,
            posted_at=str(posted_at) if posted_at else None,
            url=str(url) if url else None,
        )
