"""Phase 4 — longitudinal world diff and negative search memory.

Exit criterion: known scene changes are measured with precision/recall.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.models import Observation, ObservedEntity, ObservedRelation, WorldChange
from backend.app.domain.types import AbsenceStatus as A
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType as E
from backend.app.domain.types import SearchResult
from backend.app.evaluation.metrics import ExpectedChange, diff_precision_recall
from backend.app.services.memory import MemoryService
from backend.app.services.search import SearchPolicy, SearchService
from backend.app.services.tasks import StepSpec, TaskService
from backend.app.services.world_diff import WorldDiffService
from backend.app.services.world_state_engine import WorldStateEngine

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


class World:
    """Small harness bundling the services a diff needs."""

    def __init__(self, repo, search_policy=None, unobserved_as_removed=False):
        self.repo = repo
        self.engine = WorldStateEngine(repo)
        for anchor, parent in (("desk", None), ("desk_left", "desk"), ("desk_right", "desk"), ("shelf", None)):
            self.engine.anchors.register(anchor, T0, parent_id=parent)
        self.memory = MemoryService(repo, self.engine.claims, self.engine.relations, self.engine.anchors)
        self.tasks = TaskService(repo, self.engine)
        self.search = SearchService(repo, self.engine, search_policy)
        self.diffs = WorldDiffService(repo, self.memory, self.tasks, treat_unobserved_as_removed=unobserved_as_removed)
        self._n = 0

    def observe(self, minutes, *entities, session=None, source="camera", **kw):
        self._n += 1
        obs = Observation(id=f"o{self._n}", timestamp=at(minutes), source=source, session_id=session,
                          observed_entities=list(entities), **kw)
        self.engine.record_observation(obs)
        return self.repo.get_observation(obs.id)


@pytest.fixture
def w(repo):
    return World(repo)


def ent(cid, loc=None, type="thing", **kw):
    return ObservedEntity(candidate_entity_id=cid, type=type, location=loc, **kw)


def kinds(diff):
    return [(c.change_type, c.entity_id, c.attribute) for c in diff.changes]


# ----------------------------------------------------------------- semantics
def test_net_semantics_and_event_log_baseline(w):
    w.observe(0, ent("bottle", "desk_left"))
    w.observe(10, ent("bottle", "shelf"))
    w.observe(20, ent("bottle", "desk_left"))
    snapshot = w.diffs.diff(at(1), at(30))
    assert snapshot.changes == []  # A → B → A: nothing changed
    log = w.diffs.event_log_diff(at(1), at(30))
    assert [c.change_type for c in log.changes] == [E.OBJECT_MOVED, E.OBJECT_MOVED]  # baseline over-reports


def test_moves_state_changes_additions_and_revisions(w):
    w.observe(0, ent("m17", "desk_left", attributes={"configuration": "R6"}), ent("sop", type="procedure", attributes={"procedure_revision": "rev3"}))
    w.observe(30, ent("m17", "desk_right", attributes={"configuration": "R7"}), ent("sop", type="procedure", attributes={"procedure_revision": "rev4"}),
              ent("probe", "desk_right", attributes={"color": "red"}))
    diff = w.diffs.diff(at(1), at(31))
    assert kinds(diff) == [
        (E.OBJECT_MOVED, "m17", "location"),
        (E.OBJECT_STATE_CHANGED, "m17", "configuration"),
        (E.OBJECT_ADDED, "probe", None),
        (E.PROCEDURE_REVISION_DETECTED, "sop", "procedure_revision"),
    ]
    moved = diff.changes[0]
    assert (moved.before, moved.after, moved.status, moved.timestamp) == ("desk_left", "desk_right", S.OBSERVED, at(30))
    assert moved.evidence_refs and w.repo.get_evidence(moved.evidence_refs[0]).source_reference == "o2"
    assert diff.changes[2].after == {"location": "desk_right", "color": "red"}


def test_first_observation_of_an_attribute_is_not_a_change(w):
    w.observe(0, ent("pump", "desk_left"))
    w.observe(10, ent("pump", "desk_left", attributes={"serial_label": "SN-7"}))
    assert w.diffs.diff(at(1), at(11)).changes == []


# ------------------------------------------------------------- unknown ≠ absent
def test_not_reobserved_is_not_removed(w):
    w.observe(0, ent("cable", "desk_left"), ent("tool", "desk_left"))
    w.observe(30, ent("tool", "desk_left"))  # camera panned away from the cable
    diff = w.diffs.diff(at(1), at(31))
    [change] = diff.changes
    assert (change.change_type, change.entity_id, change.absence) == (E.OBJECT_REMOVED_OR_UNOBSERVED, "cable", A.NOT_REOBSERVED)
    assert change.after == "desk_left"  # belief unchanged
    assert w.repo.get_entity("cable").current_state["location"] == "desk_left"


@pytest.mark.parametrize(
    "coverage,visibility,confidence",
    [(0.5, {}, 1.0), (1.0, {"lighting": "poor"}, 1.0), (1.0, {"occlusion": 0.6}, 1.0), (1.0, {}, 0.4)],
)
def test_inadequate_search_is_inconclusive(w, coverage, visibility, confidence):
    w.observe(0, ent("cable", "desk_left"))
    cov = w.search.record_search("desk_left", at(30), ["cable"], coverage_fraction=coverage,
                                 visibility_conditions=visibility, confidence=confidence)
    assert cov.result == SearchResult.INCONCLUSIVE and cov.inconclusive == ["cable"]
    assert w.engine.claims.assess_attribute("cable", "location", at(31)).value == "desk_left"
    [change] = w.diffs.diff(at(1), at(31)).changes
    assert change.absence == A.NOT_FOUND_PARTIAL_COVERAGE


def test_validated_search_confirms_absence_then_refound(w, repo):
    w.observe(0, ent("cable", "desk_left"))
    cov = w.search.record_search("desk", at(30), ["cable"], coverage_fraction=0.95, visibility_conditions={"lighting": "good"})
    assert cov.result == SearchResult.NOT_FOUND_IN_COVERAGE and cov.confirmed_absent == ["cable"]
    assert repo.get_evidence(cov.evidence_refs[0]).content["validated"] is True

    loc = w.engine.claims.assess_attribute("cable", "location", at(31))
    assert (loc.status, loc.has_current_claim, loc.last_known_value) == (S.UNKNOWN, False, "desk_left")
    assert "confirmed absent from desk" in loc.reason
    assert repo.get_entity("cable").current_state["location"] is None

    [change] = w.diffs.diff(at(1), at(31)).changes
    assert (change.change_type, change.absence, change.before, change.after) == (E.OBJECT_REMOVED_OR_UNOBSERVED, A.CONFIRMED_ABSENT, "desk_left", None)
    assert change.evidence_refs == cov.evidence_refs

    w.observe(60, ent("cable", "shelf"))
    [refound] = w.diffs.diff(at(31), at(61)).changes
    assert (refound.change_type, refound.before, refound.after, refound.note) == (E.OBJECT_MOVED, None, "shelf", "re-found after confirmed absence")


def test_search_says_nothing_about_targets_believed_elsewhere(w):
    w.observe(0, ent("cable", "desk_left"))
    cov = w.search.record_search("shelf", at(30), ["cable"])
    assert cov.confirmed_absent == [] and cov.inconclusive == ["cable"]
    assert w.engine.claims.assess_attribute("cable", "location", at(31)).value == "desk_left"


def test_search_by_type_and_found_targets(w):
    w.observe(0, ent("b1", "desk_left", type="bottle"), ent("b2", "desk_right", type="bottle"), ent("b3", "shelf", type="bottle"))
    seen = Observation(id="search_frame", timestamp=at(30), source="camera", observed_entities=[ent("b1", "desk_left", type="bottle")])
    cov = w.search.record_search("desk", at(30), ["type:bottle"], observation=seen)
    assert (cov.found, cov.confirmed_absent) == (["b1"], ["b2"])  # b3 is on the shelf: not a target


# ----------------------------------------------------------------- relations
def test_relation_changes(w):
    w.observe(0, ent("tray", "desk_left"), ent("cart", "desk_left"), ent("laptop", "desk_left"),
              ent("cable", "desk_left", relations=[ObservedRelation(relation_type="connected_to", target="laptop")]),
              ent("tool", "desk_left", relations=[ObservedRelation(relation_type="on", target="tray")]))
    w.observe(30, ent("cable", "desk_left", relations=[ObservedRelation(relation_type="connected_to", target="laptop", present=False)]),
              ent("tool", "desk_left", relations=[ObservedRelation(relation_type="on", target="cart")]),
              ent("tray", "desk_left"), ent("cart", "desk_left"), ent("laptop", "desk_left"))
    rel = [(c.entity_id, c.attribute, c.before, c.after) for c in w.diffs.diff(at(1), at(31)).changes]
    assert rel == [("cable", "connected_to", "laptop", None), ("tool", "on", "tray", "cart")]


def test_evidence_conflict_in_diff(w):
    w.observe(0, ent("m17", "desk_left", attributes={"configuration": "R6"}))
    w.engine.assert_claim("m17", "configuration", "R7", source="digital_registry", timestamp=at(5))
    [change] = w.diffs.diff(at(1), at(6)).changes
    assert change.change_type == E.EVIDENCE_CONFLICT and sorted(change.after) == ["R6", "R7"]
    assert change.status == S.CONTRADICTED and change.before == "R6"


def test_replacement_hints_are_inferred(w):
    w.observe(0, ent("n1", "desk_right", type="notebook", identifiers={"serial_number": "NB-1"}),
              ent("c4", "desk_left", type="cable", identifiers={"serial_number": "C-1"}))
    obs = w.observe(30, ent("n1", "desk_right", type="notebook", identifiers={"serial_number": "NB-2"}),
                    ent(None, "desk_left", type="cable", identifiers={"serial_number": "C-9"}))
    w.search.record_search("desk_left", at(31), ["c4"])
    diff = w.diffs.diff(at(1), at(32))
    new_notebook, new_cable = obs.resolutions[0].entity_id, obs.resolutions[1].entity_id
    added = {c.entity_id: c for c in diff.changes if c.change_type == E.OBJECT_ADDED}
    assert added[new_notebook].note.startswith("INFERRED possible replacement of n1 (identifier mismatch)")
    assert added[new_cable].note.startswith("INFERRED possible replacement of c4 (same type and location")
    assert added[new_cable].related_entity_ids == ["c4"]


def test_task_progress_in_diff(w):
    w.tasks.create_task("t", [StepSpec(id="a", description="a"), StepSpec(id="b", description="b")], at(0), task_id="T")
    w.tasks.complete_step("T", "a", at(5))
    w.tasks.complete_step("T", "b", at(40))
    changes = [(c.entity_id, c.attribute, c.before, c.after) for c in w.diffs.diff(at(10), at(41)).changes]
    assert changes == [("T", None, "IN_PROGRESS", "COMPLETED"), ("T", "b", "PENDING", "COMPLETED")]


def test_uncertain_claims_are_listed_separately(w):
    w.observe(0, ent("pump", "desk_left", attributes={"power": "on"}), source="sensor")
    w.observe(30, ent("pump", "shelf"), quality=0.2)  # unconfirmed move report
    diff = w.diffs.diff(at(1), at(31))
    assert diff.changes == []
    assert [(u.entity_id, u.attribute, u.status) for u in diff.uncertain] == [("pump", "location", S.UNKNOWN), ("pump", "power", S.STALE)]


def test_diff_since_session(w):
    w.observe(0, ent("bottle", "desk_left"), session="A")
    w.observe(5, ent("bottle", "desk_left"), session="A")
    w.observe(120, ent("bottle", "shelf"), session="B")
    diff = w.diffs.diff_since_session("A", at(121))
    assert diff.baseline_timestamp == at(5) and kinds(diff) == [(E.OBJECT_MOVED, "bottle", "location")]


# ------------------------------------------------------------------- ablations
def test_ablation_unobserved_as_removed(repo):
    w = World(repo, unobserved_as_removed=True)
    w.observe(0, ent("cable", "desk_left"), ent("tool", "desk_left"))
    w.observe(30, ent("tool", "desk_left"))
    [change] = w.diffs.diff(at(1), at(31)).changes
    assert change.absence == A.CONFIRMED_ABSENT  # the false removal ORBIT is designed to avoid


def test_ablation_search_policy_disabled(repo):
    w = World(repo, search_policy=SearchPolicy(enabled=False))
    w.observe(0, ent("cable", "desk_left"))
    cov = w.search.record_search("shelf", at(30), ["cable"], coverage_fraction=0.1)
    assert cov.confirmed_absent == ["cable"]


# ------------------------------------------------------------------- metrics
def test_metric_matching_is_one_to_one():
    c = lambda t, e, a=None, **kw: WorldChange(change_type=t, entity_id=e, attribute=a, timestamp=T0, **kw)
    reported = [c(E.OBJECT_MOVED, "b", "location", after="shelf"), c(E.OBJECT_MOVED, "b", "location", after="shelf"), c(E.OBJECT_ADDED, "x")]
    truth = [ExpectedChange(change_type=E.OBJECT_MOVED, entity_id="b", attribute="location", after="shelf", check_after=True),
             ExpectedChange(change_type=E.OBJECT_MOVED, entity_id="z")]
    score = diff_precision_recall(reported, truth)
    assert (score.true_positives, round(score.precision, 3), score.recall) == (1, 0.333, 0.5)
    assert diff_precision_recall([], []).precision == 1.0


# -------------------------------------------------------------- exit criterion
def test_experiment_b0_controlled_desk_scene(w):
    """Phase 4 exit: known scene changes measured with precision/recall (protocol §1)."""
    # Session A — observe the scene, relations and a task.
    w.observe(0, ent("laptop", "desk_left", type="laptop"), ent("phone", "desk_right", type="phone"),
              ent("bottle", "desk_left", type="bottle", attributes={"color": "blue"}),
              ent("notebook_n1", "desk_right", type="notebook", identifiers={"serial_number": "NB-1"}),
              ent("cable", "desk_left", type="cable", relations=[ObservedRelation(relation_type="connected_to", target="laptop")]),
              session="A")
    w.tasks.create_task("set up workstation", [StepSpec(id="s1", description="plug cable"), StepSpec(id="s2", description="charge phone", dependencies=["s1"]),
                                               StepSpec(id="s3", description="log in", dependencies=["s2"])], at(1), task_id="T1")
    w.tasks.complete_step("T1", "s1", at(2), source="manual_verification", authority=0.95)
    w.observe(5, ent("laptop", "desk_left", type="laptop"), session="A")

    # Between sessions the experimenter moves bottle and cable, re-plugs the cable into
    # the phone and replaces the notebook. Session B sees part of the desk.
    obs_b = w.observe(
        120,
        ent("laptop", "desk_left", type="laptop"),
        ent("bottle", "shelf", type="bottle", attributes={"color": "blue"}),
        ent("cable", "desk_right", type="cable", relations=[ObservedRelation(relation_type="connected_to", target="laptop", present=False),
                                                           ObservedRelation(relation_type="connected_to", target="phone")]),
        ent("notebook_n1", "desk_right", type="notebook", identifiers={"serial_number": "NB-2"}),
        session="B",
    )
    w.search.record_search("desk_right", at(121), ["notebook_n1"], coverage_fraction=1.0, session_id="B")
    w.tasks.complete_step("T1", "s2", at(125))
    new_notebook = obs_b.resolutions[3].entity_id

    truth = [
        ExpectedChange(change_type=E.OBJECT_MOVED, entity_id="bottle", attribute="location", after="shelf", check_after=True),
        ExpectedChange(change_type=E.OBJECT_MOVED, entity_id="cable", attribute="location", after="desk_right", check_after=True),
        ExpectedChange(change_type=E.RELATION_CHANGED, entity_id="cable", attribute="connected_to", after=None, check_after=True),
        ExpectedChange(change_type=E.RELATION_CHANGED, entity_id="cable", attribute="connected_to", after="phone", check_after=True),
        ExpectedChange(change_type=E.OBJECT_ADDED, entity_id=new_notebook),
        ExpectedChange(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="notebook_n1", absence=A.CONFIRMED_ABSENT),
        ExpectedChange(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="phone", absence=A.NOT_REOBSERVED),
        ExpectedChange(change_type=E.TASK_PROGRESS_CHANGED, entity_id="T1", attribute="s2", after="COMPLETED", check_after=True),
    ]
    diff = w.diffs.diff_since_session("A", at(130))
    score = diff_precision_recall(diff.changes, truth)
    assert (score.precision, score.recall) == (1.0, 1.0), (score.false_positives, score.false_negatives)
    # Identity preserved for every object that persisted.
    assert {r.entity_id for r in obs_b.resolutions[:3]} == {"laptop", "bottle", "cable"}

    # The event-replay baseline is measurably worse on the same scene.
    baseline = diff_precision_recall(w.diffs.event_log_diff(at(5), at(130)).changes, truth)
    assert baseline.precision < 1.0 and baseline.recall < 1.0


# ---------------------------------------------------------------------- API
def test_diff_and_search_api(client):
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()
    client.post("/anchors", json={"id": "desk"})
    obs = lambda i, m, loc, session: {"id": i, "timestamp": t(m), "source": "camera", "session_id": session,
                                      "observed_entities": [{"candidate_entity_id": "cable", "type": "cable", "location": loc}]}
    client.post("/observations", json=obs("o1", 0, "desk", "A"))
    res = client.post("/search", json={"region": "desk", "searched_for": ["cable"], "coverage_fraction": 0.4, "timestamp": t(10)}).json()
    assert res["result"] == "INCONCLUSIVE"
    res = client.post("/search", json={"region": "desk", "searched_for": ["cable"], "timestamp": t(20), "session_id": "B"}).json()
    assert res["result"] == "NOT_FOUND_IN_COVERAGE"
    assert len(client.get("/search-coverage").json()) == 2

    diff = client.post("/world/diff", json={"baseline_session_id": "A", "target_timestamp": t(30)}).json()
    assert [(c["change_type"], c["absence"]) for c in diff["changes"]] == [("OBJECT_REMOVED_OR_UNOBSERVED", "CONFIRMED_ABSENT")]
    assert client.post("/world/diff", json={"mode": "event_log", "target_timestamp": t(30)}).json()["mode"] == "event_log"
    assert client.post("/world/diff", json={"target_timestamp": t(30)}).status_code == 422
    assert client.post("/world/diff", json={"baseline_session_id": "nope"}).status_code == 404
    assert client.post("/search", json={"region": "desk", "searched_for": ["ghost"]}).status_code == 404
