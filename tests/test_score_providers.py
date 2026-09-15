"""Provider catalog stub: pause visibly, never silent reassignment, no LLM."""
from mco.orchestrator.score_providers import (
    DEFAULT_WEIGHTS,
    Provider,
    ProviderSelectError,
    Run,
    Task,
    canary_catalog,
    select,
)

DIGEST = "canary-digest"


def _run(**kwargs):
    grants = frozenset({"evidence:write", "evidence:review"})
    values = dict(
        digest=DIGEST,
        grants=grants,
        authorized_budget_cents=0,
        remaining_budget_cents=0,
        independence_class="same_provider",
    )
    values.update(kwargs)
    return Run(**values)


def _task(phase_author="score-canary-worker", **kwargs):
    values = dict(
        task_id="C01",
        role="score-canary-worker",
        review_role="score-canary-review",
        capabilities=frozenset({"evidence:write"}),
        max_cost_cents=0,
        attempt=1,
        author_id=phase_author,
        author_provider="fixed-handler",
    )
    values.update(kwargs)
    return Task(**values)


def test_weights_sum_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_canary_work_picks_worker_not_unavailable_fixture():
    worker, reviewer, down = canary_catalog(DIGEST)
    result = select(_task(), "work", (worker, reviewer, down), _run())
    assert result.status == "dispatch"
    assert result.chosen is worker
    assert down.instance_id in {row.provider.instance_id for row in result.classified if row.health == "provider_unavailable"}
    assert result.event["kind"] == "provider_selected"
    assert result.event["instance_id"] == "score-canary-worker"


def test_canary_review_excludes_author_and_picks_reviewer():
    worker, reviewer, down = canary_catalog(DIGEST)
    result = select(_task(), "review", (worker, reviewer, down), _run())
    assert result.status == "dispatch"
    assert result.chosen is reviewer
    classified_ids = {row.provider.instance_id for row in result.classified}
    assert "score-canary-worker" not in classified_ids


def test_review_role_holder_who_authored_is_rejected_not_chosen():
    impersonator = Provider(
        instance_id="score-canary-worker",
        role="score-canary-review",
        provider="fixed-handler",
        capabilities=frozenset({"evidence:write", "evidence:review"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write", "evidence:review"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
    )
    _, reviewer, _ = canary_catalog(DIGEST)
    result = select(_task(), "review", (impersonator, reviewer), _run())
    assert result.chosen is reviewer
    rejected = {row.provider.instance_id: row.rejected for row in result.classified if row.rejected}
    assert rejected["score-canary-worker"] == "author_of_attempt"


def test_cross_provider_review_does_not_use_author_vendor():
    author = Provider(
        instance_id="author-1",
        role="score-canary-worker",
        provider="acme",
        capabilities=frozenset({"evidence:write"}),
        independence_class="cross_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
    )
    same_vendor_reviewer = Provider(
        instance_id="review-acme",
        role="score-canary-review",
        provider="acme",
        capabilities=frozenset({"evidence:write"}),
        independence_class="cross_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
    )
    other = Provider(
        instance_id="review-other",
        role="score-canary-review",
        provider="other",
        capabilities=frozenset({"evidence:write"}),
        independence_class="cross_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=10,
    )
    task = _task(author_id="author-1", author_provider="acme")
    run = _run(independence_class="cross_provider")
    result = select(task, "review", (author, same_vendor_reviewer, other), run)
    assert result.chosen is other
    rejected = {row.provider.instance_id: row.rejected for row in result.classified if row.rejected}
    assert rejected["review-acme"] == "same_provider"


def test_no_qualified_provider_when_only_leftover_is_author():
    worker, _, down = canary_catalog(DIGEST)
    result = select(_task(), "review", (worker, down), _run())
    assert result.status == "blocked"
    assert result.error_class == "no_qualified_provider"
    assert result.chosen is None
    assert result.event["kind"] == "no_qualified_provider"


def test_quota_exhausted_waits_at_min_reset_and_does_not_dispatch():
    worker, reviewer, down = canary_catalog(DIGEST)
    tired = Provider(
        instance_id="score-canary-worker",
        role="score-canary-worker",
        provider="fixed-handler",
        capabilities=frozenset({"evidence:write", "evidence:review"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write", "evidence:review"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
        quota_reset_at=50,
    )
    also = Provider(
        instance_id="other-worker",
        role="score-canary-worker",
        provider="fixed-handler-2",
        capabilities=frozenset({"evidence:write", "evidence:review"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write", "evidence:review"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
        quota_reset_at=20,
    )
    result = select(_task(), "work", (tired, also, reviewer, down), _run())
    assert result.status == "waiting_provider"
    assert result.error_class == "provider_unavailable"
    assert result.wake_at == 20
    assert result.chosen is None


def test_cost_exceeded_is_not_ok_and_does_not_steal_work():
    cheap = Provider(
        instance_id="broke",
        role="score-canary-worker",
        provider="fixed-handler",
        capabilities=frozenset({"evidence:write"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=0,
    )
    funded = Provider(
        instance_id="funded",
        role="score-canary-worker",
        provider="fixed-handler-2",
        capabilities=frozenset({"evidence:write"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=25,
    )
    task = _task(max_cost_cents=10, author_id=None, author_provider=None)
    run = _run(authorized_budget_cents=100, remaining_budget_cents=100)
    result = select(task, "work", (cheap, funded), run)
    assert result.chosen is funded
    health = {row.provider.instance_id: row.health for row in result.classified}
    assert health["broke"] == "cost_exceeded"


def test_tie_break_lower_remaining_cost_then_instance_id():
    a = Provider(
        instance_id="aaa",
        role="score-canary-worker",
        provider="x",
        capabilities=frozenset({"evidence:write"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=5,
    )
    b = Provider(
        instance_id="bbb",
        role="score-canary-worker",
        provider="y",
        capabilities=frozenset({"evidence:write"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=5,
    )
    cheaper = Provider(
        instance_id="zzz",
        role="score-canary-worker",
        provider="z",
        capabilities=frozenset({"evidence:write"}),
        independence_class="same_provider",
        authority_scopes=frozenset({"evidence:write"}),
        approved_digests=frozenset({DIGEST}),
        remaining_cost_cents=1,
    )
    task = _task(author_id=None, author_provider=None)
    result = select(task, "work", (b, a, cheaper), _run())
    assert result.chosen is cheaper
    tied = select(task, "work", (b, a), _run())
    assert tied.chosen is a


def test_contribution_set_blocks_reviewer_even_if_role_differs():
    worker, reviewer, _ = canary_catalog(DIGEST)
    task = _task(
        contribution_instance_ids=frozenset({"score-canary-review"}),
    )
    result = select(task, "review", (worker, reviewer), _run())
    assert result.status == "blocked"
    assert result.error_class == "no_qualified_provider"


def test_unapproved_digest_is_invisible():
    worker, reviewer, down = canary_catalog("other-digest")
    result = select(_task(), "work", (worker, reviewer, down), _run())
    assert result.status == "blocked"
    assert result.classified == ()


def test_bad_weights_rejected():
    worker, reviewer, down = canary_catalog(DIGEST)
    try:
        select(_task(), "work", (worker, reviewer, down), _run(), weights={"capability_fit": 100})
    except ProviderSelectError:
        return
    raise AssertionError("expected ProviderSelectError")
