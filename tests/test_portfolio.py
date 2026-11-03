from datetime import datetime, timezone

import pytest

from mco.orchestrator.portfolio import (
    PortfolioConfigError,
    load_portfolio_config,
    project_snapshot,
)


NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)


def test_missing_config_is_safe_and_does_not_disclose_path(tmp_path):
    result = load_portfolio_config(tmp_path / "private" / "portfolio.yaml")

    assert result.configured is False
    assert result.config == {}
    assert result.source == "explicit"
    assert result.warning == "Portfolio configuration was not found."
    assert str(tmp_path) not in result.warning


def test_valid_config_loads_generic_projects(tmp_path):
    path = tmp_path / "portfolio.yaml"
    path.write_text(
        """
version: 1
projects:
  - id: alpha
    name: Project Alpha
    status: active
    priority: 1
    done_definition: Customers can complete the primary workflow.
    aliases: [alpha-app]
    milestones:
      - {title: Foundation, status: done}
      - {title: Pilot, status: next}
""",
        encoding="utf-8",
    )

    result = load_portfolio_config(path)

    assert result.configured is True
    assert result.warning is None
    assert result.config["projects"][0]["id"] == "alpha"


@pytest.mark.parametrize("status", ["", "working", "complete", None])
def test_invalid_milestone_status_is_rejected_without_echoing_value(tmp_path, status):
    path = tmp_path / "portfolio.yaml"
    rendered = "null" if status is None else repr(status)
    path.write_text(
        "version: 1\nprojects:\n  - id: alpha\n    name: Alpha\n"
        "    status: active\n    priority: 1\n    done_definition: Done.\n"
        f"    milestones:\n      - title: First\n        status: {rendered}\n",
        encoding="utf-8",
    )

    with pytest.raises(PortfolioConfigError, match="milestone status") as exc:
        load_portfolio_config(path)

    if status not in ("", None):
        assert str(status) not in str(exc.value)


def test_project_projection_uses_explicit_alias_and_roadmap_state():
    config = {
        "projects": [
            {
                "id": "alpha",
                "name": "Project Alpha",
                "status": "active",
                "priority": 1,
                "done_definition": "The primary workflow is dependable.",
                "aliases": ["alpha-app"],
                "milestones": [
                    {"title": "Foundation", "status": "done"},
                    {"title": "Pilot", "status": "next"},
                    {"title": "Release", "status": "blocked"},
                ],
            }
        ]
    }
    jobs = [
        {
            "id": "newer",
            "title": "Pilot work",
            "status": "in_progress",
            "updated_at": "2026-11-03T19:00:00Z",
            "input_payload": {"project": "alpha-app"},
        },
        {
            "id": "older",
            "title": "Foundation work",
            "status": "completed",
            "updated_at": "2026-10-01T12:00:00Z",
            "project_name": "Project Alpha",
        },
        {
            "id": "other",
            "title": "Other work",
            "status": "failed",
            "updated_at": "2026-11-03T19:30:00Z",
            "input_payload": {"project": "not-alpha"},
        },
    ]

    result = project_snapshot(config, jobs, NOW)

    assert len(result) == 1
    project = result[0]
    assert project["progress"] == {"done": 1, "total": 3, "percent": 33}
    assert project["next_milestone"] == "Pilot"
    assert [job["id"] for job in project["recent_jobs"]] == ["newer", "older"]
    assert project["last_activity"] == "2026-11-03T19:00:00+00:00"
    assert project["health"] == "red"


def test_project_projection_tolerates_bad_dates_and_caps_recent_jobs():
    config = {
        "projects": [
            {
                "id": "alpha",
                "name": "Project Alpha",
                "status": "active",
                "priority": 2,
                "done_definition": "Done.",
                "aliases": [],
                "milestones": [{"title": "Launch", "status": "done"}],
            }
        ]
    }
    jobs = [
        {
            "id": f"job-{n}",
            "title": f"Work {n}",
            "status": "completed",
            "updated_at": "not-a-date",
            "project_id": "alpha",
        }
        for n in range(25)
    ]

    project = project_snapshot(config, jobs, NOW)[0]

    assert project["last_activity"] is None
    assert len(project["recent_jobs"]) == 20
    assert project["health"] == "green"


def test_next_milestone_falls_back_to_first_non_done():
    config = {
        "projects": [
            {
                "id": "alpha",
                "name": "Alpha",
                "status": "paused",
                "priority": 3,
                "done_definition": "Done.",
                "milestones": [
                    {"title": "One", "status": "done"},
                    {"title": "Two", "status": "blocked"},
                ],
            }
        ]
    }

    project = project_snapshot(config, [], NOW)[0]

    assert project["next_milestone"] == "Two"
    assert project["health"] == "red"
