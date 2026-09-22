from types import SimpleNamespace

from mco.orchestrator.codex_route import apply_codex_policy


def _annotation(**changes):
    value = {
        "task_kind": "implementation",
        "complexity": "standard",
        "reasoning_need": "medium",
        "execution_shape": "root_only",
        "needs_current_information": 0.1,
        "needs_workspace_evidence": 0.8,
        "needs_acceptance_criteria": 0.9,
        "needs_clarification": 0.1,
        "confidence": {"complexity": 0.8},
        "receipt": SimpleNamespace(outcome="shadow", model="jev-1.13"),
    }
    value.update(changes)
    return value


def test_standard_task_uses_terra_and_builds_prompt_guidance():
    route = apply_codex_policy(
        _annotation(), five_hour_remaining_percent=80, weekly_remaining_percent=70,
    )
    assert route["recommended_model"] == "gpt-5.6-terra"
    assert route["reasoning_effort"] == "medium"
    assert route["prompt_guidance"] == [
        "inspect the actual workspace or runtime and cite concrete evidence",
        "state a measurable success condition in delegated task packets",
    ]
    assert route["applied"] is False


def test_astra_requires_verified_capacity_and_low_context_pressure():
    frontier = _annotation(complexity="frontier", reasoning_need="extreme")
    eligible = apply_codex_policy(
        frontier, five_hour_remaining_percent=60, weekly_remaining_percent=60,
    )
    assert eligible["recommended_model"] == "gpt-6-astra"
    assert eligible["capacity"]["astra_eligible"] is True

    low_capacity = apply_codex_policy(
        frontier, five_hour_remaining_percent=25, weekly_remaining_percent=60,
    )
    assert low_capacity["recommended_model"] == "gpt-5.6-sol"
    assert low_capacity["reasoning_effort"] == "max"
    assert low_capacity["capacity"]["astra_eligible"] is False

    large_context = apply_codex_policy(
        frontier, five_hour_remaining_percent=60, weekly_remaining_percent=60,
        context_pressure="high",
    )
    assert large_context["recommended_model"] == "gpt-5.6-sol"
    assert "context pressure is high" in large_context["capacity"]["astra_ineligible_reasons"]


def test_critical_capacity_conserves_and_suppresses_parallel_subagents():
    route = apply_codex_policy(
        _annotation(complexity="complex", reasoning_need="high", execution_shape="subagents"),
        five_hour_remaining_percent=10,
        weekly_remaining_percent=50,
    )
    assert route["recommended_model"] == "gpt-5.6-terra"
    assert route["execution_shape"] == "root_only"
    assert route["max_parallel"] == 1
    assert route["capacity"]["conserving"] is True


def test_unknown_usage_never_selects_astra():
    route = apply_codex_policy(
        _annotation(complexity="frontier", reasoning_need="extreme"),
    )
    assert route["recommended_model"] == "gpt-5.6-sol"
    assert route["capacity"]["astra_eligible"] is False


def test_hybrid_becomes_durable_bitcadence_when_parallel_capacity_is_low():
    route = apply_codex_policy(
        _annotation(execution_shape="hybrid"),
        five_hour_remaining_percent=19,
        weekly_remaining_percent=80,
    )
    assert route["execution_shape"] == "bitcadence"
    assert route["max_parallel"] == 1
