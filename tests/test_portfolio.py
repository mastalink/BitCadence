from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from mco.orchestrator.portfolio import (
    PortfolioConfigError,
    compute_flow_flags,
    cpu_queue_snapshot,
    load_portfolio_config,
    project_snapshot,
    read_gpu_state,
    read_open_prs,
    read_publishing_state,
    worker_snapshot,
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


def completed(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_publishing_status_command_normalizes_episode_fields():
    config = {"status_command": ["publisher", "status", "--json"]}
    payload = {
        "episodes": [
            {
                "id": "ep-1",
                "title": "Episode One",
                "stage": "review",
                "publish_at": "2026-11-07T12:00:00Z",
                "gates_missing": ["thumbnail"],
            }
        ]
    }

    result = read_publishing_state(
        config, runner=lambda argv, timeout: completed(json.dumps(payload))
    )

    assert result["available"] is True
    assert result["source"] == "command"
    assert result["counts"] == {"review": 1}
    assert result["scheduled"] == 0
    assert result["published"] == 0
    assert result["episodes"][0]["due_at"] == "2026-11-07T12:00:00Z"


def test_publishing_falls_back_to_episode_files_when_command_fails(tmp_path):
    episode = tmp_path / "show" / "episode.json"
    episode.parent.mkdir()
    episode.write_text(
        json.dumps({"id": "ep-2", "title": "Episode Two", "status": "scheduled"}),
        encoding="utf-8",
    )

    result = read_publishing_state(
        {"status_command": ["missing"], "episodes_root": str(tmp_path)},
        runner=lambda argv, timeout: (_ for _ in ()).throw(TimeoutError()),
    )

    assert result["available"] is True
    assert result["source"] == "episodes"
    assert result["scheduled"] == 1
    assert result["warning"] == "Publishing status command was unavailable; using episode files."


def test_publishing_malformed_external_data_fails_soft(tmp_path):
    (tmp_path / "episode.json").write_text("{bad json", encoding="utf-8")

    result = read_publishing_state({"episodes_root": str(tmp_path)})

    assert result["available"] is False
    assert result["episodes"] == []
    assert "path" not in result.get("warning", "").lower()


def test_gpu_adapter_combines_lock_comfy_board_queue_and_nvidia():
    responses = {
        "lock": {"holder": None, "idle_since": "2026-11-03T18:00:00Z"},
        "smi": "0, 1024, 8192\n",
    }

    def runner(argv, timeout):
        key = "lock" if argv[0] == "lock-status" else "smi"
        value = responses[key]
        return completed(json.dumps(value) if isinstance(value, dict) else value)

    jobs = [
        {
            "id": "gpu-job",
            "title": "Render images",
            "status": "pending",
            "created_at": "2026-11-03T18:30:00Z",
            "input_payload": {"stage": "image generation"},
        }
    ]
    result = read_gpu_state(
        {
            "lock_command": ["lock-status"],
            "comfy_url": "http://comfy.test",
            "nvidia_smi_command": ["nvidia-smi"],
            "stage_terms": ["image generation"],
        },
        jobs,
        runner=runner,
        fetcher=lambda url, timeout: {"queue_running": [], "queue_pending": [[1, {}]]},
    )

    assert result["available"] is True
    assert result["holder"] is None
    assert result["utilization_percent"] == 0
    assert result["memory"] == {"used_mb": 1024, "total_mb": 8192}
    assert result["queued"] == 2
    assert result["queued_since"] == "2026-11-03T18:30:00+00:00"
    assert result["idle"] is True


def test_gpu_adapter_timeout_is_reported_without_raising():
    result = read_gpu_state(
        {"lock_command": ["slow"], "stage_terms": []},
        [],
        runner=lambda argv, timeout: (_ for _ in ()).throw(TimeoutError()),
    )

    assert result["available"] is False
    assert result["warnings"] == ["GPU lock status was unavailable."]


def test_cpu_queue_and_worker_snapshots_use_board_authority():
    jobs = [
        {"id": "run", "status": "in_progress", "leased_by_instance_id": "worker-a"},
        {"id": "lease", "status": "leased", "leased_by_instance_id": "worker-a"},
        {"id": "wait", "status": "pending", "target_agent_role": "codex"},
        {"id": "done", "status": "completed", "leased_by_instance_id": "worker-b"},
    ]
    agents = [
        {"instance_id": "worker-a", "role": "codex", "status": "online"},
        {"instance_id": "worker-b", "role": "claude", "status": "broken"},
        {"instance_id": "worker-c", "role": "reviewer", "status": "away"},
    ]

    queue = cpu_queue_snapshot(jobs)
    workers = worker_snapshot(agents)

    assert [job["id"] for job in queue["running"]] == ["run", "lease"]
    assert [job["id"] for job in queue["pending"]] == ["wait"]
    assert queue["by_worker"]["worker-a"] == 2
    assert workers["counts"] == {"online": 1, "broken": 1, "away": 1}


def test_open_pr_adapter_accepts_only_safe_fields_and_fails_soft():
    project = {"repository": {"pr_command": ["gh", "pr", "list"]}}
    rows = [{"number": 7, "title": "Pilot", "url": "https://example.test/pr/7", "body": "private"}]

    result = read_open_prs(
        project, runner=lambda argv, timeout: completed(json.dumps(rows))
    )
    unavailable = read_open_prs(
        project, runner=lambda argv, timeout: completed("not json")
    )

    assert result == [{"number": 7, "title": "Pilot", "url": "https://example.test/pr/7"}]
    assert unavailable == []


def test_flow_flags_cover_every_v1_stall_condition():
    jobs = [
        {
            "id": "old",
            "title": "Old queued work",
            "status": "pending",
            "created_at": "2026-11-03T17:00:00Z",
            "input_payload": {"project": "alpha"},
        }
    ]
    projects = [{"id": "alpha", "name": "Alpha", "recent_jobs": [{"id": "old"}]}]
    workers = {
        "workers": [{"instance_id": "worker-b", "role": "claude", "status": "broken"}],
        "counts": {"broken": 1},
    }
    gpu = {
        "idle": True,
        "queued": 1,
        "queued_since": "2026-11-03T18:00:00Z",
    }
    publishing = {
        "episodes": [
            {
                "id": "ep-1",
                "title": "Episode One",
                "stage": "review",
                "due_at": "2026-11-07T12:00:00Z",
                "project_id": "alpha",
            }
        ]
    }
    events = [
        {
            "id": "event-1",
            "job_id": "old",
            "event": "chain_stalled",
            "created_at": "2026-11-03T19:00:00Z",
        }
    ]

    flags = compute_flow_flags(
        projects=projects,
        jobs=jobs,
        workers=workers,
        gpu=gpu,
        publishing=publishing,
        events=events,
        thresholds={"gpu_idle_minutes": 30, "pending_hours": 2, "publish_due_days": 7},
        now=NOW,
    )

    assert {flag["kind"] for flag in flags} == {
        "gpu_idle",
        "worker_broken",
        "job_pending",
        "publish_due",
        "chain_stalled",
    }
    assert all(flag["severity"] == "red" for flag in flags)
    assert next(flag for flag in flags if flag["kind"] == "job_pending")["project_id"] == "alpha"


def test_flow_flags_ignore_recent_or_malformed_timestamps():
    flags = compute_flow_flags(
        projects=[],
        jobs=[
            {"id": "recent", "status": "pending", "created_at": "2026-11-03T19:30:00Z"},
            {"id": "bad", "status": "pending", "created_at": "bad"},
        ],
        workers={"workers": [], "counts": {}},
        gpu={"idle": True, "queued": 1, "queued_since": "bad"},
        publishing={"episodes": [{"id": "bad", "stage": "draft", "due_at": "bad"}]},
        events=[{"id": "old", "event": "chain_stalled", "created_at": "2026-10-01T00:00:00Z"}],
        thresholds={},
        now=NOW,
    )

    assert flags == []
