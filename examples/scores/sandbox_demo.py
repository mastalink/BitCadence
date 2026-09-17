"""Run an entirely offline score through retry, independent review and a gate.

This deliberately supplies synthetic trusted identities/evidence. It proves
policy transitions only, not authenticated users, actual agents or deployment.
"""
import json

from mco.orchestrator.scores import SandboxRun, ScoreError


def main():
    def task(key, dependencies, gate=None):
        return dict(id=key, goal=key, title=key, instructions="Synthetic proof only",
                    role="builder", review_role="reviewer", depends_on=dependencies,
                    resources=["demo"], capabilities=["sandbox:write"],
                    evidence=["artifact", "test_report"], max_attempts=2,
                    timeout_seconds=10, max_cost_cents=0, checkpoint=gate)

    score = dict(score_version=1, id="sandbox-proof", revision=1,
                 objective="Exercise policy, not real launch", constraints=["No external effects"],
                 budget_cents=0, max_parallel=1, launch_requires=["launch"],
                 tasks=[task("build", []), task("launch", ["build"],
                        {"id": "demo-human-gate", "reason": "Explicit test checkpoint"})])
    run = SandboxRun(score, "synthetic-demo", grants=["sandbox:write"])
    stale = run.start("build", actor="worker-one", role="builder", now=0)
    run.expire(now=10)
    token = run.start("build", actor="worker-two", role="builder", now=11)
    try:
        run.finish("build", stale, actor="worker-one", evidence={"artifact": "fake", "test_report": "fake"}, now=12)
    except ScoreError:
        stale_rejected = True
    else:
        raise AssertionError("Stale writer was accepted")
    evidence = {"artifact": "synthetic-artifact-digest", "test_report": "synthetic-passing-report"}
    run.finish("build", token, actor="worker-two", evidence=evidence, now=13)
    run.review("build", token, actor="independent-reviewer", role="reviewer", passed=True, verified_evidence=True, now=14)
    assert run.blockers("launch") == ["human_checkpoint"]
    run.approve("launch", actor="synthetic-human", actor_kind="human")
    token = run.start("launch", actor="worker-two", role="builder", now=15)
    run.finish("launch", token, actor="worker-two", evidence=evidence, now=16)
    run.review("launch", token, actor="independent-reviewer", role="reviewer", passed=True, verified_evidence=True, now=17)
    report = run.report()
    report.update(stale_writer_rejected=stale_rejected, actual_agents_used=False,
                  actual_deployment=False, human_identity_authenticated=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
