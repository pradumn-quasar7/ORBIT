"""Phase 6 — hybrid retrieval and the grounded query agent (spec §8, §14, §15)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, StateCondition
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import QueryKind, SourceType
from backend.app.providers.base import Vocabulary
from backend.app.providers.embedding import HashingEmbeddingProvider
from backend.app.providers.reasoning import RuleBasedReasoningProvider
from backend.app.services.query_agent import QueryAgent
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


class Lab:
    def __init__(self, repo):
        self.svc = OrbitServices.build(repo)
        self.repo = repo
        for a, p in (("lab204", None), ("bench_3", "lab204"), ("shelf_a", "lab204")):
            self.svc.anchors.register(a, T0, parent_id=p)
        self._n = 0

    def see(self, minutes, *entities, session=None, source="camera"):
        self._n += 1
        self.svc.engine.record_observation(Observation(id=f"o{self._n}", timestamp=at(minutes), source=source,
                                                       session_id=session, observed_entities=list(entities)))

    def ask(self, q, minutes, agent=None):
        r = (agent or self.svc.agent).answer(q, at(minutes))
        check_contract(r)
        return r


def check_contract(r):
    """Spec §15: an answer only when supportable; supportable claims carry evidence."""
    assert r.abstained == (r.answer is None)
    for c in r.claims:
        if c.supportable and "Task" not in c.claim and "step" not in c.claim:
            assert c.evidence_refs, c
    assert r.providers["reasoning"] == "rule-based-v1"


def ent(cid, loc=None, type="thing", **kw):
    return ObservedEntity(candidate_entity_id=cid, type=type, location=loc, **kw)


@pytest.fixture
def lab(repo):
    lab = Lab(repo)
    lab.see(0, ent("m17", "bench_3", "microscope", name="Microscope M17", attributes={"configuration": "R6"}),
            ent("bottle_01", "bench_3", "bottle"), ent("bottle_02", "shelf_a", "bottle"),
            ent("pump_old", "bench_3", "pump", attributes={"power": "on"}), ent("pump_new", "shelf_a", "pump"),
            session="A", source="camera")
    return lab


# --------------------------------------------------------------- interpretation
@pytest.mark.parametrize(
    "query,kind,entities,extra",
    [
        ("Where is M17 right now?", QueryKind.WHERE_IS, ["m17"], {}),
        ("where is microscope m17", QueryKind.WHERE_IS, ["m17"], {}),
        ("Where's pump_new?", QueryKind.WHERE_IS, ["pump_new"], {}),
        ("where was the microscope at 09:30", QueryKind.WHERE_IS, ["m17"], {"as_of": at(30)}),
        ("What changed?", QueryKind.WHAT_CHANGED, [], {}),
        ("Continue.", QueryKind.CONTINUE, [], {}),
        ("What is the configuration of M17?", QueryKind.ATTRIBUTE, ["m17"], {"attribute": "configuration"}),
        ("what's on bench 3", QueryKind.CONTENTS, [], {"anchor_id": "bench_3"}),
        ("What happened to pump_old yesterday?", QueryKind.WHAT_HAPPENED, ["pump_old"], {"since": T0.replace(hour=0) , "until": T0.replace(hour=0) + timedelta(days=1)}),
        ("why did the microscope fail", QueryKind.WHY, ["m17"], {}),
    ],
)
def test_interpretation(lab, query, kind, entities, extra):
    now = at(60) if "yesterday" not in query else at(60 * 24)
    intent = RuleBasedReasoningProvider().interpret(query, lab.svc.agent.vocabulary(), now)
    assert intent.kind == kind and intent.entity_ids == entities
    for key, value in extra.items():
        assert getattr(intent, key) == value, key


def test_type_mention_is_ambiguous_with_two_bottles(lab):
    intent = RuleBasedReasoningProvider().interpret("where is the bottle?", lab.svc.agent.vocabulary(), at(1))
    assert intent.entity_ids == [] and intent.ambiguous == {"bottle": ["bottle_01", "bottle_02"]}
    r = lab.ask("where is the bottle?", 1)
    assert r.abstained and r.summary == "Which bottle do you mean: bottle_01 (last at bench_3), bottle_02 (last at shelf_a)?"


# ----------------------------------------------------------- point-in-time
def test_where_is_fresh(lab):
    r = lab.ask("Where is M17 right now?", 10)
    assert r.answer == "Microscope M17 is at bench_3 (OBSERVED, last supported 2026-09-30 09:00 UTC)."
    [claim] = r.claims
    assert (claim.status, claim.supportable, claim.value) == (S.OBSERVED, True, "bench_3")
    assert claim.freshness.state.value == "FRESH" and claim.evidence_refs


def test_where_is_stale_abstains_and_requests_observation(lab):
    r = lab.ask("Where is M17?", 60 * 48)
    assert r.answer is None and r.abstained
    assert "last seen at bench_3" in r.summary and "stale" in r.summary
    assert r.requested_observation.instruction == "Point the camera near bench_3 so I can find Microscope M17."
    assert r.claims[0].status == S.STALE and not r.claims[0].supportable


def test_historical_question(lab):
    lab.see(30, ent("m17", "shelf_a", "microscope"))
    r = lab.ask("where was M17 at 09:15", 40)
    assert r.answer.startswith("Microscope M17 was at bench_3")
    assert lab.ask("where is M17", 40).answer.startswith("Microscope M17 is at shelf_a")


def test_contradiction_is_surfaced_not_resolved(lab):
    lab.svc.engine.assert_claim("m17", "configuration", "R7", source="digital_registry", timestamp=at(5))
    r = lab.ask("What is the configuration of M17?", 6)
    assert r.abstained and len(r.conflicts) == 2
    assert "Sources disagree" in r.summary and "I won't pick one" in r.summary
    assert r.requested_observation.instruction.startswith("Sources disagree about Microscope M17's configuration.")


def test_confirmed_absence(lab):
    lab.svc.search.record_search("bench_3", at(20), ["pump_old"])
    r = lab.ask("where is pump_old", 21)
    assert r.abstained and "confirmed absent from bench_3" in r.summary and "unknown" in r.summary


def test_stale_high_volatility_attribute(lab):
    r = lab.ask("what is the power of pump_old", 30)
    assert r.abstained and "stale" in r.summary


def test_evidence_gate_ablation_answers_stale_memory(lab, repo):
    s = lab.svc
    ungated = QueryAgent(repo, s.engine, s.memory, s.diff, s.tasks, s.hypotheses, gate_evidence=False)
    r = ungated.answer("Where is M17?", at(60 * 48))
    assert r.answer.startswith("Microscope M17 is at bench_3 (STALE")  # the stale claim ORBIT refuses to make
    assert r.providers["evidence_gate"] == "off"


def test_contents(lab):
    r = lab.ask("what's on bench 3", 1)
    assert r.answer.startswith("On/in bench_3: ")
    assert {c.entity_id for c in r.claims} == {"m17", "bottle_01", "pump_old"}


# ------------------------------------------------------------ what changed
def test_what_changed_since_last_session(lab):
    lab.see(5, ent("m17", "bench_3", "microscope"), session="A")
    lab.see(120, ent("m17", "shelf_a", "microscope"), ent("bottle_01", "bench_3", "bottle"), ent("bottle_02", "shelf_a", "bottle"),
            ent("pump_new", "shelf_a", "pump"), session="B")
    r = lab.ask("What changed?", 121)
    assert not r.abstained and r.answer.startswith("Since 2026-09-30 09:05 UTC:")
    assert "Microscope M17 moved from bench_3 to shelf_a." in r.answer
    assert "pump_old was not re-observed (last known at bench_3); unknown, not assumed removed." in r.answer
    assert all(c.evidence_refs for c in r.claims)
    # pump_old power went stale: listed as needing a fresh look, with an instruction.
    assert ("pump_old", "power") in [(q.entity_id, q.attribute) for q in r.requested_observations]
    assert "Not confirmed" in r.summary


def test_what_changed_without_baseline_abstains(repo):
    lab = Lab(repo)
    lab.see(0, ent("x", "bench_3"))
    assert lab.ask("what changed?", 1).abstained


# ----------------------------------------------------------------- continue
def test_continue_runs_resume_protocol(lab):
    lab.see(1, ent("valve", "bench_3", "valve", attributes={"state": "closed"}))
    lab.svc.tasks.create_task(
        "Replace coolant pump",
        [StepSpec(id="s5", step_order=5, description="isolate system", postconditions=[StateCondition(entity_id="valve", attribute="state", expected="closed")]),
         StepSpec(id="s7", step_order=7, description="install new pump", dependencies=["s5"],
                  preconditions=[StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200", min_status=S.VERIFIED)])],
        at(2), task_id="T12")
    lab.svc.tasks.complete_step("T12", "s5", at(3), source="manual_verification", authority=0.95)
    lab.svc.tasks.interrupt_task("T12", at(4))
    r = lab.ask("Continue.", 60)
    assert r.abstained and r.resume_plan.task_id == "T12"
    assert r.requested_observation.instruction == "Move closer so I can read the model number on pump_new."

    lab.svc.engine.assert_claim("pump_new", "model_number", "CP-200", source="manual_verification", authority=0.95, timestamp=at(61))
    r = lab.ask("continue", 62)
    assert r.answer == "Next: step 7 — install new pump."


# ------------------------------------------------------------ what happened
def test_what_happened_combines_recall_with_structured_state(lab):
    lab.see(60, ent("pump_old", "bench_3", "pump", attributes={"power": "off"}), source="sensor")
    lab.see(90, ent("pump_old", "shelf_a", "pump", attributes={"power": "off"}), source="camera")
    r = lab.ask("What happened to pump_old?", 100)
    texts = [c.claim for c in r.claims]
    assert texts[0].startswith("2026-09-30 09:00 UTC: Entity pump_old (pump) added")
    assert any("'power' changed from on to off" in t for t in texts)
    assert any("moved from bench_3 to shelf_a" in t for t in texts)
    assert [c.claim for c in r.claims] == sorted(texts)  # chronological
    assert r.retrieval and all(h.entity_ids and "pump_old" in h.entity_ids for h in r.retrieval)


def test_semantic_recall_ranks_relevant_memories():
    from backend.app.providers.base import MemoryDocument
    from backend.app.providers.retrieval import InMemoryVectorIndex

    index = InMemoryVectorIndex(HashingEmbeddingProvider())
    index.index([
        MemoryDocument("1", "event", "e1", "coolant pump power changed from on to off"),
        MemoryDocument("2", "event", "e2", "bottle moved from desk to shelf"),
        MemoryDocument("3", "event", "e3", "microscope configuration changed"),
    ])
    assert [h.ref_id for h in index.search("what happened to the pump power", k=3)][0] == "e1"
    a, b = HashingEmbeddingProvider().embed(["same text"]), HashingEmbeddingProvider().embed(["same text"])
    assert a == b  # deterministic across instances


# ---------------------------------------------------------------------- why
def test_why_never_asserts_unsupported_causation(lab):
    lab.see(10, ent("m17", "bench_3", "microscope", attributes={"firmware": "1.1"}))
    lab.see(40, ent("m17", "bench_3", "microscope", attributes={"health": "failed"}))
    r = lab.ask("why did the microscope fail?", 41)
    assert r.abstained and "won't infer a cause" in r.summary

    events = lab.repo.get_events_for_entity("m17")
    hyp = lab.svc.hypotheses.propose("firmware 1.1 may explain the failure", at(42), events[-2].id, events[-1].id)
    r = lab.ask("why did the microscope fail?", 43)
    assert r.abstained and "Open hypotheses (INFERRED, not established)" in r.summary
    assert r.claims[0].status == S.INFERRED and not r.claims[0].supportable

    lab.svc.hypotheses.add_evidence(hyp.id, at(50), "diagnostic_rig", True, "rollback restores function",
                                    kind="causal_test", source_type=SourceType.TOOL_OUTPUT, authority=0.95)
    r = lab.ask("why did the microscope fail?", 51)
    assert r.answer == "Supported by causal-test evidence: firmware 1.1 may explain the failure."


def test_unknown_query_abstains_with_recall_aids(lab):
    r = lab.ask("tell me a joke about microscopes", 1)
    assert r.abstained and r.intent.kind == QueryKind.UNKNOWN
    assert "recall aids only" in r.summary


# ---------------------------------------------------------------------- API
def test_query_api(client):
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera",
                                        "observed_entities": [{"candidate_entity_id": "m17", "type": "microscope", "location": "bench_3"}]})
    r = client.post("/queries", json={"query": "Where is m17?", "at": t(5)}).json()
    assert r["answer"].startswith("m17 is at bench_3") and r["claims"][0]["evidence_refs"]
    r = client.post("/queries", json={"query": "Where is m17?", "at": t(60 * 49)}).json()
    assert r["answer"] is None and r["requested_observation"]["instruction"].startswith("Point the camera")
    hits = client.get("/memory/search", params={"q": "microscope added"}).json()
    assert hits and hits[0]["kind"] == "event"
