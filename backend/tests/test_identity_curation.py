"""Phase 13 — identity curation: human-confirmed merge, undo, distinct, suggestions.

Exit criterion: a person can resolve an identity ORBIT could not decide; the merged
belief is re-derived from the evidence of both records under the normal policy;
earlier "as known at" answers are unchanged; and the merge can be undone.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, ObservedRelation
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType, IdentityStatus, PrincipalKind, ResolutionMethod, Scope
from backend.app.services.identity import CurationDenied, IdentityError
from backend.app.services.sandbox import fork

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(m):
    return T0 + timedelta(minutes=m)


class World:
    def __init__(self, repo):
        self.repo = repo
        self.svc = OrbitServices.build(repo)
        self.svc.actions.register_principal("ana", PrincipalKind.HUMAN, [Scope.CURATE], T0)
        self.n = 0

    def see(self, m, *detections, source="camera"):
        self.n += 1
        oid = f"o{self.n}"
        self.svc.engine.record_observation(Observation(id=oid, timestamp=at(m), source=source, observed_entities=list(detections)))
        return self.repo.get_observation(oid)

    def assess(self, eid, attr, m):
        return self.svc.engine.claims.assess_attribute(eid, attr, at(m))


def bottle(loc, cid=None, **attrs):
    return ObservedEntity(candidate_entity_id=cid, type="bottle", location=loc, attributes={"color": "blue", **attrs})


@pytest.fixture
def w(repo):
    world = World(repo)
    world.see(0, bottle("desk_left", "bottle_a"), bottle("desk_right", "bottle_b"))
    obs = world.see(60, bottle("kitchen", fill="half"))  # cannot tell which bottle this is
    world.ambiguous = obs.resolutions[0].entity_id
    assert obs.resolutions[0].method == ResolutionMethod.NEW_AMBIGUOUS
    return world


# ----------------------------------------------------------------- ambiguity
def test_ambiguity_casts_doubt_on_every_candidate(w):
    for eid in ("bottle_a", "bottle_b"):
        a = w.assess(eid, "location", 61)
        assert a.status == S.STALE and "indistinguishable bottle was seen at kitchen" in a.reason
    assert w.svc.agent.answer("Where is bottle_a?", at(61)).abstained


# --------------------------------------------------------------------- merge
def test_merge_rederives_belief_from_both_histories(w, repo):
    snapshot_before = w.svc.memory.world_snapshot(at(65))
    merge = w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(70), "label on the cap reads A")

    a = w.assess("bottle_a", "location", 71)
    assert (a.value, a.status) == ("kitchen", S.OBSERVED)
    assert w.assess("bottle_a", "fill", 71).value == "half"  # attribute known only to the ambiguous record
    assert w.assess("bottle_b", "location", 71).value == "desk_right"  # doubt lifted: B was never involved
    hidden = repo.get_entity(w.ambiguous)
    assert (hidden.merged_into, hidden.merged_at) == ("bottle_a", at(70))
    assert w.ambiguous not in w.svc.memory.world_snapshot(at(71)).entities
    assert repo.get_entity("bottle_a").identity_status == IdentityStatus.ESTABLISHED
    assert merge.source_version_ids and merge.target_version_ids
    # Bitemporal: what ORBIT believed before the merge is unchanged.
    assert w.svc.memory.world_snapshot(at(65)) == snapshot_before
    assert [e.event_type for e in repo.get_events_for_entity(w.ambiguous)][-1] == EventType.IDENTITY_MERGED


def test_replay_surfaces_disagreement_instead_of_picking_a_side(repo):
    world = World(repo)
    world.see(0, ObservedEntity(candidate_entity_id="pump", type="pump", attributes={"configuration": "R6"}))
    world.see(0, ObservedEntity(candidate_entity_id="pump_dup", type="pump", attributes={"configuration": "R7"}), source="phone_camera")
    world.svc.identity.merge("pump_dup", "pump", "ana", at(10), "same asset, registered twice")
    c = world.assess("pump", "configuration", 11)
    assert c.status == S.CONTRADICTED and sorted(s.value for s in c.conflicts) == ["R6", "R7"]
    assert world.assess("pump", "configuration", 5).status == S.OBSERVED  # not contradicted *before* the merge


def test_merge_keeps_invalidations_and_absences(repo):
    world = World(repo)
    world.see(0, ObservedEntity(candidate_entity_id="t", type="tool", location="bench", attributes={"calibration": "ok"}))
    world.see(0, ObservedEntity(candidate_entity_id="t2", type="tool", location="shelf"), source="phone_camera")
    world.svc.engine.record_intervention("t", at(5), "tool dropped", attributes=["calibration"])
    world.svc.search.record_search("shelf", at(6), ["t2"])
    world.svc.identity.merge("t2", "t", "ana", at(10), "same torque wrench")
    assert world.assess("t", "calibration", 11).status == S.STALE
    loc = world.assess("t", "location", 11)
    assert loc.value == "bench"  # t2's sighting on the shelf was later confirmed absent; t's own bench sighting stands


def test_merged_id_is_an_alias(w, repo):
    w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(70), "label")
    obs = w.see(80, bottle("kitchen", w.ambiguous))  # an old track id still in use
    assert obs.resolutions[0].entity_id == "bottle_a" and "alias of bottle_a" in obs.resolutions[0].reason
    obs = w.see(90, bottle("kitchen"))  # anonymous: matches the live entity, never the alias
    assert obs.resolutions[0].entity_id == "bottle_a"
    assert w.svc.agent.answer(f"Where is {w.ambiguous}?", at(91)).answer.startswith("bottle_a is at kitchen")
    assert w.ambiguous not in [e.id for e in repo.list_entities() if not e.merged_into]


def test_world_diff_reports_the_correction(w):
    w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(70), "label")
    diff = w.svc.diff.diff(at(65), at(71))
    merged = [c for c in diff.changes if c.change_type == EventType.IDENTITY_MERGED]
    assert [(c.entity_id, c.after) for c in merged] == [(w.ambiguous, "bottle_a")]
    assert (EventType.OBJECT_MOVED, "bottle_a") in [(c.change_type, c.entity_id) for c in diff.changes]


def test_relations_move_with_the_identity(repo):
    world = World(repo)
    world.see(0, ObservedEntity(candidate_entity_id="cable", type="cable",
                                relations=[ObservedRelation(relation_type="connected_to", target="laptop")]),
              ObservedEntity(candidate_entity_id="laptop", type="laptop"))
    world.see(5, ObservedEntity(candidate_entity_id="cable_2", type="cable"), source="phone_camera")
    world.svc.identity.merge("cable", "cable_2", "ana", at(10), "re-tagged cable")
    assert [r.target_entity for r in world.svc.relations.active_relations("cable_2")] == ["laptop"]
    assert world.svc.relations.active_relations("cable") == []


# --------------------------------------------------------------- refusals
def test_merge_refusals(repo):
    world = World(repo)
    world.see(0, ObservedEntity(candidate_entity_id="a", type="bottle"), ObservedEntity(candidate_entity_id="b", type="bottle"))
    world.see(1, ObservedEntity(candidate_entity_id="mug", type="mug"))
    world.see(2, ObservedEntity(candidate_entity_id="p1", type="pump", identifiers={"serial_number": "S1"}))
    world.see(3, ObservedEntity(candidate_entity_id="p2", type="pump", identifiers={"serial_number": "S2"}))
    world.see(4, ObservedEntity(candidate_entity_id="c", type="bottle"))
    ids = world.svc.identity
    with pytest.raises(IdentityError, match="same observation"):
        ids.merge("a", "b", "ana", at(10), "looked the same")
    with pytest.raises(IdentityError, match="types differ"):
        ids.merge("mug", "a", "ana", at(10), "x")
    with pytest.raises(IdentityError, match="identifiers conflict"):
        ids.merge("p1", "p2", "ana", at(10), "x")
    with pytest.raises(IdentityError, match="itself"):
        ids.merge("a", "a", "ana", at(10), "x")
    with pytest.raises(IdentityError, match="needs a reason"):
        ids.merge("c", "a", "ana", at(10), " ")
    world.svc.actions.register_principal("bob", PrincipalKind.HUMAN, [Scope.AUTHORIZE], T0)
    for who in ("orbit-agent", "bob"):
        with pytest.raises(CurationDenied):
            ids.merge("c", "a", who, at(10), "x")
    ids.merge("c", "a", "ana", at(10), "same bottle")
    with pytest.raises(IdentityError, match="already merged"):
        ids.merge("c", "b", "ana", at(11), "x")


# ---------------------------------------------------------------------- undo
def test_undo_restores_both_identities(w, repo):
    before = {e: (w.assess(e, "location", 69).last_known_value, w.assess(e, "location", 69).status) for e in ("bottle_a", w.ambiguous)}
    merge = w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(70), "label")
    w.see(75, bottle("kitchen", "bottle_a"))  # learned after the merge: stays with the target
    undone = w.svc.identity.undo(merge.id, "ana", at(80), "wrong bottle — label was B")
    assert undone.status == "UNDONE" and undone.undone_by == "ana"

    src = repo.get_entity(w.ambiguous)
    assert src.merged_into is None and src.identity_status == IdentityStatus.AMBIGUOUS
    assert w.assess(w.ambiguous, "location", 81).last_known_value == before[w.ambiguous][0]
    assert w.assess("bottle_a", "location", 81).value == "kitchen"  # the 75-minute sighting
    assert w.assess("bottle_a", "fill", 81).has_current_claim is False  # went back with the source
    assert w.ambiguous in w.svc.memory.world_snapshot(at(81)).entities
    assert w.assess("bottle_b", "location", 81).status == S.STALE  # ambiguity is back, so is the doubt
    assert w.ambiguous not in w.svc.memory.world_snapshot(at(77)).entities  # history of the merged period kept
    with pytest.raises(IdentityError, match="already undone"):
        w.svc.identity.undo(merge.id, "ana", at(81), "again")


# ------------------------------------------------------------------ distinct
def test_confirm_distinct_lifts_doubt_and_ambiguity(w, repo):
    a, b = w.svc.identity.confirm_distinct(w.ambiguous, "bottle_b", "ana", at(70), "B never left the desk drawer")
    assert a.identity_candidates == ["bottle_a"] and a.identity_status == IdentityStatus.AMBIGUOUS
    assert "bottle_b" in a.distinct_from and w.ambiguous in b.distinct_from
    assert w.assess("bottle_b", "location", 71).status == S.OBSERVED  # doubt lifted
    assert w.assess("bottle_a", "location", 71).status == S.STALE  # still possibly the kitchen bottle
    a, _ = w.svc.identity.confirm_distinct(w.ambiguous, "bottle_a", "ana", at(72), "different brand")
    assert a.identity_status == IdentityStatus.ESTABLISHED and a.identity_candidates == []
    with pytest.raises(IdentityError, match="confirmed distinct"):
        w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(73), "changed my mind")


# --------------------------------------------------------------- suggestions
def test_suggestions(repo):
    world = World(repo)
    world.see(0, bottle("desk", "bottle_a"), bottle("shelf", "bottle_b"))
    amb = world.see(30, bottle("kitchen")).resolutions[0].entity_id
    world.see(40, ObservedEntity(candidate_entity_id="pump_1", type="pump", identifiers={"asset_tag": "AT-9"}))
    world.see(50, ObservedEntity(candidate_entity_id="pump_x", type="pump"), source="phone_camera")  # tag unreadable…
    world.see(55, ObservedEntity(candidate_entity_id="pump_x", type="pump", identifiers={"asset_tag": "AT-9"}), source="phone_camera")  # …read later
    found = {(s.source_id, s.target_id): s for s in world.svc.identity.suggestions(at(60))}
    assert set(found) == {(amb, "bottle_a"), (amb, "bottle_b"), ("pump_x", "pump_1")}
    assert "same asset_tag" in found[("pump_x", "pump_1")].reasons
    assert ("bottle_b", "bottle_a") not in found  # seen together: two objects
    world.svc.identity.confirm_distinct(amb, "bottle_b", "ana", at(61), "no")
    assert (amb, "bottle_b") not in {(s.source_id, s.target_id) for s in world.svc.identity.suggestions(at(62))}


# ------------------------------------------------------- rewind / projection
def test_projection_rolls_back_merges(w):
    merge = w.svc.identity.merge(w.ambiguous, "bottle_a", "ana", at(70), "label")
    w.svc.identity.undo(merge.id, "ana", at(80), "wrong")
    for m in (65, 75, 85):
        sb = fork(w.svc, at(m))
        assert sb.services.memory.world_snapshot(at(m)) == w.svc.memory.world_snapshot(at(m)), m
    during = fork(w.svc, at(75)).services.repo
    assert during.get_entity(w.ambiguous).merged_into == "bottle_a" and during.list_merges()[0].status == "ACTIVE"


def test_replay_reproduces_every_bench_entity():
    from experiments.scenarios.catalog import SCENARIOS
    from backend.app.evaluation.bench import VARIANTS, ScenarioRunner

    for sc in SCENARIOS:
        r = ScenarioRunner(sc, VARIANTS[0])
        r.run()
        end = max(e.timestamp for e in r.repo.list_events()) + timedelta(minutes=1)
        for e in [x for x in r.repo.list_entities() if not x.merged_into]:
            attrs = sorted({v.attribute for v in r.repo.get_state_versions_for_entity(e.id)})
            key = lambda a: (a.value, a.status, a.last_known_value, a.has_current_claim)  # noqa: E731
            before = {a: key(r.svc.engine.claims.assess_attribute(e.id, a, end)) for a in attrs}
            r.svc.identity.rebuild(e.id, end)
            assert {a: key(r.svc.engine.claims.assess_attribute(e.id, a, end)) for a in attrs} == before, (sc.id, e.id)


# ----------------------------------------------------------------------- API
def test_identity_api(client):
    t = lambda m: at(m).isoformat()  # noqa: E731
    client.post("/principals", json={"id": "ana", "kind": "HUMAN", "scopes": ["curate"]})
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "a", "type": "bottle", "location": "desk_left", "attributes": {"color": "blue"}},
        {"candidate_entity_id": "b", "type": "bottle", "location": "desk_right", "attributes": {"color": "blue"}}]})
    client.post("/observations", json={"id": "o2", "timestamp": t(60), "source": "camera", "observed_entities": [
        {"type": "bottle", "location": "kitchen", "attributes": {"color": "blue"}}]})
    sugg = client.get("/identity/suggestions", params={"as_of": t(61)}).json()
    amb = sugg[0]["source_id"]
    assert {s["target_id"] for s in sugg} == {"a", "b"}
    assert client.post("/identity/merge", json={"source_id": amb, "target_id": "a", "principal_id": "orbit-agent", "reason": "x"}).status_code == 403
    assert client.post("/identity/merge", json={"source_id": "a", "target_id": "b", "principal_id": "ana", "reason": "x"}).status_code == 409
    merge = client.post("/identity/merge", json={"source_id": amb, "target_id": "a", "principal_id": "ana",
                                                 "reason": "label", "at": t(70)}).json()
    assert client.get("/entities/a").json()["current_state"]["location"] == "kitchen"
    assert amb not in [e["id"] for e in client.get("/entities").json()]
    assert amb in [e["id"] for e in client.get("/entities", params={"include_merged": True}).json()]
    assert client.get("/identity/merges").json()[0]["id"] == merge["id"]
    undone = client.post(f"/identity/merges/{merge['id']}/undo", json={"principal_id": "ana", "reason": "wrong", "at": t(80)}).json()
    assert undone["status"] == "UNDONE"
    res = client.post("/identity/distinct", json={"entity_a": amb, "entity_b": "b", "principal_id": "ana", "reason": "no", "at": t(81)})
    assert res.status_code == 200 and "b" in res.json()[0]["distinct_from"]
    assert client.post("/identity/merges/nope/undo", json={"principal_id": "ana", "reason": "x"}).status_code == 409
