"""Importers for JSON and CSV job postings."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, TextIO, Union

from mco.jobs.models import ClientInfo, JobPosting


def import_from_json(data: Union[str, bytes, list, dict]) -> List[JobPosting]:
    """Parse job postings from JSON string, bytes, or parsed objects."""
    if isinstance(data, (str, bytes)):
        parsed = json.loads(data)
    else:
        parsed = data

    items: List[Any] = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        for candidate_key in ("jobs", "postings", "data", "results", "edges"):
            val = parsed.get(candidate_key)
            if isinstance(val, list):
                items = val
                break
        if not items and "title" in parsed:
            items = [parsed]

    postings: List[JobPosting] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Support Upwork edge/node wrapper in JSON exports if present
        node = item.get("node") if isinstance(item.get("node"), dict) else item
        postings.append(JobPosting.from_dict(node))

    return postings


def import_from_csv(csv_content: Union[str, bytes, TextIO, Path]) -> List[JobPosting]:
    """Parse job postings from CSV content or file."""
    if isinstance(csv_content, Path) or (isinstance(csv_content, str) and "\n" not in csv_content and Path(csv_content).is_file()):
        content = Path(csv_content).read_text(encoding="utf-8")
        stream = io.StringIO(content)
    elif isinstance(csv_content, bytes):
        stream = io.StringIO(csv_content.decode("utf-8", errors="replace"))
    elif isinstance(csv_content, str):
        stream = io.StringIO(csv_content)
    else:
        stream = csv_content

    reader = csv.DictReader(stream)
    postings: List[JobPosting] = []

    for row in reader:
        if not any(row.values()):
            continue

        # Extract client information from prefixed or dot-notation columns
        client_country = (
            row.get("client.country")
            or row.get("client_country")
            or row.get("country")
        )
        client_verified = (
            row.get("client.payment_verified")
            or row.get("client_payment_verified")
            or row.get("payment_verified")
        )
        client_spent = (
            row.get("client.total_spent")
            or row.get("client_total_spent")
            or row.get("total_spent")
        )
        client_hire_rate = (
            row.get("client.hire_rate")
            or row.get("client_hire_rate")
            or row.get("hire_rate")
        )
        client_rating = (
            row.get("client.rating")
            or row.get("client_rating")
            or row.get("rating")
        )

        client_data: Dict[str, Any] = {
            "country": client_country,
            "payment_verified": client_verified,
            "total_spent": client_spent,
            "hire_rate": client_hire_rate,
            "rating": client_rating,
        }

        # Build normalized job posting dict
        posting_data: Dict[str, Any] = {
            "source": row.get("source") or "csv",
            "id": row.get("id") or row.get("job_id") or row.get("url") or "",
            "title": row.get("title") or "",
            "description": row.get("description") or "",
            "budget_type": row.get("budget_type"),
            "budget_min": row.get("budget_min") or row.get("budget"),
            "budget_max": row.get("budget_max") or row.get("budget"),
            "skills": row.get("skills") or "",
            "client": client_data,
            "proposals_count": row.get("proposals_count") or row.get("proposals"),
            "posted_at": row.get("posted_at") or row.get("date"),
            "url": row.get("url") or row.get("link"),
        }

        postings.append(JobPosting.from_dict(posting_data))

    return postings


def load_postings_from_file(file_path: Union[str, Path]) -> List[JobPosting]:
    """Auto-detect format and load job postings from file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Postings file not found: {path}")

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in {".json", ".js"}:
        return import_from_json(text)
    elif suffix in {".csv", ".tsv"}:
        return import_from_csv(text)
    else:
        # Try JSON first, fallback to CSV
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return import_from_json(text)
        return import_from_csv(text)
