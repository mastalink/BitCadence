"""CLI command for ranking job postings: `mco jobs rank`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from mco.config import get_config
from mco.jobs.importers import load_postings_from_file
from mco.jobs.models import JobPosting, RankedJob
from mco.jobs.ranker import JobRanker
from mco.jobs.upwork import UpworkAuthenticationError, UpworkError, UpworkGraphQLAdapter
from mco.orchestrator.jev import build_provider
from mco.orchestrator.routes import get_db_client


jobs_app = typer.Typer(help="BitCadence Job Board: score client jobs for fit, value, and risk.")
console = Console()


def format_markdown_table(ranked_jobs: List[RankedJob], limit: int = 20) -> str:
    """Format ranked job listings as a clean GitHub-flavored Markdown table."""
    headers = ["Rank", "Title", "Budget", "Fit", "Value", "Safety", "Reasons", "URL"]
    separator = ["---"] * len(headers)

    rows: List[List[str]] = [headers, separator]

    for idx, item in enumerate(ranked_jobs[:limit], 1):
        # Format Rank
        if item.eligible:
            rank_str = f"{idx} ({item.rank:.1f})"
        else:
            rank_str = f"EXCLUDED ({item.rank:.1f})"

        # Format Title (clean pipes for markdown table safety)
        title = item.posting.title.replace("|", "/").strip()
        if len(title) > 50:
            title = title[:47] + "..."

        # Format Budget
        btype = (item.posting.budget_type or "").lower()
        bmax = item.posting.budget_max if item.posting.budget_max is not None else item.posting.budget_min
        if btype == "fixed" and bmax is not None:
            budget_str = f"${bmax:,.0f} fixed"
        elif btype == "hourly" and bmax is not None:
            budget_str = f"${bmax:,.0f}/h"
        elif bmax is not None:
            budget_str = f"${bmax:,.0f}"
        else:
            budget_str = "Unstated"

        # Scores
        fit_str = f"{item.scores.get('fleet_fit', 0.0):.2f}"
        val_str = f"{item.scores.get('value', 0.0):.2f}"
        risk_str = f"{item.scores.get('delivery_risk', 0.0):.2f}"

        # Reasons
        if item.reasons:
            clean_reasons = [r.replace("|", "/") for r in item.reasons]
            reasons_str = "; ".join(clean_reasons)
            if len(reasons_str) > 70:
                reasons_str = reasons_str[:67] + "..."
        else:
            reasons_str = "eligible"

        # URL
        url_str = item.posting.url or ""

        rows.append([rank_str, title, budget_str, fit_str, val_str, risk_str, reasons_str, url_str])

    lines = [f"| {' | '.join(row)} |" for row in rows]
    return "\n".join(lines)


@jobs_app.command("rank")
def rank_command(
    postings_path: Optional[Path] = typer.Argument(
        None,
        help="Path to JSON or CSV file containing job postings.",
    ),
    top: int = typer.Option(
        20,
        "--top",
        "-n",
        help="Number of top ranked jobs to display in table.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help="Path to output JSON file with full ranked records and audit receipts.",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Job source: 'file', 'upwork', etc.",
    ),
    query: Optional[str] = typer.Option(
        None,
        "--query",
        "-q",
        help="Optional search query filter for marketplace API search.",
    ),
):
    """Score every available client job for fit, value and risk using Jev and hard filters."""
    postings: List[JobPosting] = []
    cfg = get_config()
    db = get_db_client()

    src_lower = (source or "").strip().lower()

    if src_lower == "upwork":
        adapter = UpworkGraphQLAdapter(config=cfg, db=db)
        if not adapter.is_configured:
            console.print("[bold red]Upwork OAuth2 token is not configured.[/bold red]")
            console.print("Set UPWORK_ACCESS_TOKEN or configure 'upwork/token' in SecretVault.")
            raise typer.Exit(code=1)
        try:
            postings = adapter.search_jobs(query=query, limit=top)
        except UpworkAuthenticationError as exc:
            console.print(f"[bold red]Authentication failed: {exc}[/bold red]")
            raise typer.Exit(code=1)
        except UpworkError as exc:
            console.print(f"[bold red]Upwork search error: {exc}[/bold red]")
            raise typer.Exit(code=1)
    else:
        if postings_path is None:
            console.print("[bold red]Missing postings file.[/bold red] Provide a path or use --source upwork.")
            raise typer.Exit(code=1)
        if not postings_path.is_file():
            console.print(f"[bold red]File not found:[/bold red] {postings_path}")
            raise typer.Exit(code=1)
        try:
            postings = load_postings_from_file(postings_path)
        except Exception as exc:
            console.print(f"[bold red]Error loading postings from {postings_path}:[/bold red] {exc}")
            raise typer.Exit(code=1)

    if not postings:
        console.print("[yellow]No job postings found to rank.[/yellow]")
        raise typer.Exit(code=0)

    # Initialize Jev provider (falls back deterministically if disabled/unconfigured/timeout)
    provider = build_provider(cfg, db)

    ranker = JobRanker(provider=provider)
    ranked = ranker.rank_postings(postings)

    # Output Markdown table
    md_table = format_markdown_table(ranked, limit=top)
    console.print(md_table)

    # Write JSON output if requested
    if out is not None:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            output_data = [r.to_dict() for r in ranked]
            out.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
            console.print(f"[dim]Wrote {len(ranked)} ranked job(s) to {out}[/dim]")
        except Exception as exc:
            console.print(f"[bold red]Failed to write output to {out}:[/bold red] {exc}")
            raise typer.Exit(code=1)
