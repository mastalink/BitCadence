import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.scores import ScoreError


def score():
    task = dict(id="audit", goal="G01", title="Audit", instructions="Read-only", role="auditor", review_role="reviewer", depends_on=[], resources=["audit"], capabilities=["cloud:inspect"], evidence=["report"], max_attempts=1, timeout_seconds=300, max_cost_cents=0, checkpoint=None)
    return dict(score_version=1,id="audit",revision=1,objective="Audit",constraints=["Read only"],budget_cents=0,max_parallel=1,tasks=[task],launch_requires=["audit"])


class Board:
    identity = "credential-fingerprint"
    def __init__(self):
        self.jobs = {}
        self.creates = 0
        self.lose_ack = False
    def capabilities(self):
        return {"create_with_id": 1}
    def create(self, payload):
        self.creates += 1
        if payload["id"] not in self.jobs:
            self.jobs[payload["id"]] = dict(copy.deepcopy(payload), source_agent_id="conductor", org_id="default", status="pending")
        elif any(self.jobs[payload["id"]].get(k) != v for k,v in payload.items()):
            raise ScoreError("Conflicting intent")
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("ack lost")
        return self.jobs[payload["id"]]
    def get(self, job_id):
        return self.jobs[job_id]
    def events(self, job_id):
        j = self.jobs[job_id]
        return [dict(job_id=job_id,event="status:completed",actor_id=j["target_agent_id"],actor_role=j["target_agent_role"])]
    def complete(self, job_id, result):
        j = self.jobs[job_id]
        j.update(status="completed",leased_by_instance_id=j["target_agent_id"],output_payload={"result":json.dumps(result)})


@pytest.fixture
def setup(tmp_path):
    bridge = ScoreBridge(tmp_path/"state.db",tmp_path/"artifacts")
    board = Board()
    bridge.initialize("run",score(),principal="conductor",org="default",targets={"auditor":"worker","reviewer":"independent"},credential_hash=board.identity)
    path = bridge.root/"report.json"
    path.write_text('{"audit":"test"}',encoding="utf-8")
    evidence={"report":{"path":"report.json","sha256":hashlib.sha256(path.read_bytes()).hexdigest()}}
    return bridge,board,evidence


def test_end_to_end_persisted_restart_review_gate(setup):
    b,g,e=setup
    work=b.plan("run")[0]
    b.dispatch("run",g)
    g.complete(work,{"artifacts":e})
    b=ScoreBridge(b.database,b.root)
    b.poll("run",g)
    assert b.status("run")["status"]=="running"
    review=b.plan("run")[0]
    b.dispatch("run",g)
    g.complete(review,{"verdict":"pass","review_of":e,"findings":[]})
    b=ScoreBridge(b.database,b.root)
    b.poll("run",g)
    assert b.status("run")["status"]=="accepted"
    assert b.plan("run")==[]
    assert b.dispatch("run",g)==[]


def test_lost_ack_retries_exact_id_after_restart(setup):
    b,g,e=setup
    job=b.plan("run")[0]
    g.lose_ack=True
    with pytest.raises(TimeoutError): b.dispatch("run",g)
    b=ScoreBridge(b.database,b.root)
    b.dispatch("run",g)
    assert list(g.jobs)==[job]
    assert g.creates==2
    assert b.status("run")["dispatches"][0]["status"]=="submitted"


def test_concurrent_planners_create_one_intent(setup):
    b,g,e=setup
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:ScoreBridge(b.database,b.root).plan("run"),range(4)))
    assert sum(map(len,results))==1


@pytest.mark.parametrize("mutation",[
    lambda s:s.update(budget_cents=1),
    lambda s:s["tasks"][0].update(capabilities=["cloud:change"]),
    lambda s:s["tasks"][0].update(max_cost_cents=100),
])
def test_authority_rejected(tmp_path,mutation):
    s=score();mutation(s)
    b=ScoreBridge(tmp_path/"state.db",tmp_path/"artifacts")
    with pytest.raises(ScoreError):
        b.initialize("r",s,principal="c",org="default",targets={"auditor":"a","reviewer":"b"},credential_hash="hash")


def test_checkpointed_task_initializes(tmp_path):
    s = score()
    s["tasks"][0].update(checkpoint={"id": "pause_1", "reason": "human inspection"})
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    b.initialize("r", s, principal="c", org="default", targets={"auditor": "a", "reviewer": "b"}, credential_hash="hash")
    status = b.status("r")
    assert status["status"] == "running"


def test_changed_identity_rejected_before_network(setup):
    b,g,e=setup
    b.plan("run")
    g.identity="changed"
    with pytest.raises(ScoreError):b.dispatch("run",g)
    assert not g.jobs


def test_changed_definition_rejected(setup):
    b,g,e=setup
    s=score();s["objective"]="different"
    with pytest.raises(ScoreError):
        b.initialize("run",s,principal="conductor",org="default",targets={"auditor":"worker","reviewer":"independent"},credential_hash=g.identity)


def test_old_gateway_fail_closed(setup):
    b,g,e=setup
    b.plan("run")
    g.capabilities=lambda:{}
    with pytest.raises(ScoreError):b.dispatch("run",g)
    assert not g.jobs


@pytest.mark.parametrize("mode",["wrong_actor","wrong_event","missing_file","hash_changed","outside_root","missing_evidence","wrong_tenant"])
def test_untrusted_completion_does_not_advance(setup,mode):
    b,g,e=setup
    job=b.plan("run")[0];b.dispatch("run",g)
    g.complete(job,{"artifacts":e})
    if mode=="wrong_actor":g.jobs[job]["leased_by_instance_id"]="attacker"
    if mode=="wrong_event":g.events=lambda _: []
    if mode=="missing_file":(b.root/"report.json").unlink()
    if mode=="hash_changed":(b.root/"report.json").write_text("changed")
    if mode=="outside_root":
        e["report"]["path"]="../outside.json";g.complete(job,{"artifacts":e})
    if mode=="missing_evidence":g.complete(job,{"artifacts":{}})
    if mode=="wrong_tenant":g.jobs[job]["org_id"]="other"
    with pytest.raises(ScoreError):b.poll("run",g)
    assert b.plan("run")==[]


@pytest.mark.parametrize("verdict",["fail","wrong_hash"])
def test_failed_or_unbound_review_cannot_accept(setup,verdict):
    b,g,e=setup
    work=b.plan("run")[0];b.dispatch("run",g);g.complete(work,{"artifacts":e});b.poll("run",g)
    review=b.plan("run")[0];b.dispatch("run",g)
    result={"verdict":"fail" if verdict=="fail" else "pass","review_of":e if verdict=="fail" else {},"findings":["issue"]}
    g.complete(review,result)
    if verdict=="wrong_hash":
        with pytest.raises(ScoreError):b.poll("run",g)
    else:
        b.poll("run",g)
        assert b.status("run")["status"]=="blocked"
    assert b.status("run")["status"]!="accepted"


def test_completed_without_review_not_acceptance(setup):
    b,g,e=setup
    job=b.plan("run")[0];b.dispatch("run",g);g.complete(job,{"artifacts":e});b.poll("run",g)
    assert b.status("run")["status"]=="running"


def test_self_review_routing_rejected(tmp_path):
    b=ScoreBridge(tmp_path/"db",tmp_path/"artifacts")
    with pytest.raises(ScoreError):
        b.initialize("r",score(),principal="c",org="default",targets={"auditor":"same","reviewer":"same"},credential_hash="hash")
