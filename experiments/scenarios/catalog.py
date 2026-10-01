"""ORBIT-BENCH scenario catalog v0.1 — one scenario per spec §26 row and §37 failure mode.

Times are minutes after T0. Ground truth describes the *true* world (what the
experimenter physically did), independent of anything ORBIT outputs. Detections may
carry ``truth`` (the physical object) for identity metrics; anonymous detections
omit ``candidate_entity_id``.
"""
from typing import List

from backend.app.domain.models import StateCondition
from backend.app.domain.types import AbsenceStatus as A
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType as E
from backend.app.evaluation.bench import (
    Claim,
    CompleteStep,
    ConflictCheck,
    CreateTask,
    DiffCheck,
    EndSession,
    Hypothesize,
    Intervene,
    InterruptTask,
    Observe,
    QueryCheck,
    ResumeCheck,
    Scenario,
    Search,
)
from backend.app.evaluation.metrics import ExpectedChange as X
from backend.app.services.tasks import StepSpec

LAB = [("lab", None), ("bench", "lab"), ("bench_left", "bench"), ("bench_right", "bench"), ("shelf", "lab"),
       ("desk", "lab"), ("desk_left", "desk"), ("desk_right", "desk"), ("cart", "lab")]


_DETECTION_FIELDS = {"name", "identifiers", "attributes", "relations", "anchor", "geometry", "confidence"}


def d(cid, loc=None, type="thing", truth=None, anonymous=False, **kw):
    """A detection; keyword arguments that are not detection fields become attributes."""
    attributes = dict(kw.pop("attributes", {}))
    attributes.update({k: kw.pop(k) for k in list(kw) if k not in _DETECTION_FIELDS})
    out = {"type": type, "location": loc, "attributes": attributes, **kw}
    if not anonymous:
        out["candidate_entity_id"] = cid
    out["truth"] = truth or cid
    return out


def _cond(entity, attribute, expected, min_status=S.OBSERVED):
    return StateCondition(entity_id=entity, attribute=attribute, expected=expected, min_status=min_status)


SCENARIOS: List[Scenario] = [
    Scenario(
        id="object_relocation",
        title="Objects relocated between sessions; session B camera is anonymous",
        description="M17 and a bottle move while the user is away; ORBIT must keep identities without ids.",
        primary_metric="entity_persistence_accuracy",
        anchors=LAB,
        steps=[
            Observe(at=0, session="A", detections=[d("m17", "bench", "microscope"), d("bottle", "desk", "bottle", color="blue"),
                                                   d("mug", "desk", "mug", color="red")]),
            EndSession(at=5, session="A"),
            Observe(at=120, session="B", detections=[d("m17", "shelf", "microscope", anonymous=True),
                                                     d("bottle", "bench", "bottle", anonymous=True, attributes={"color": "blue"}),
                                                     d("mug", "desk", "mug", anonymous=True, attributes={"color": "red"})]),
        ],
        diffs=[DiffCheck(baseline_session="A", target=121, expected=[
            X(change_type=E.OBJECT_MOVED, entity_id="m17", attribute="location", after="shelf", check_after=True),
            X(change_type=E.OBJECT_MOVED, entity_id="bottle", attribute="location", after="bench", check_after=True)])],
        queries=[QueryCheck(at=121, text="Where is m17?", expect="answer", truth="shelf"),
                 QueryCheck(at=121, text="Where is the bottle?", expect="answer", truth="bench")],
    ),
    Scenario(
        id="configuration_change",
        title="Known intervention invalidates configuration",
        description="A technician reconfigures M17; the old configuration must not be asserted until re-observed.",
        primary_metric="stale_claim_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("m17", "bench", "microscope", attributes={"configuration": "R6"})]),
            Intervene(at=60, entity="m17", description="technician reconfigured M17", attributes=["configuration"]),
            Observe(at=90, detections=[d("m17", "bench", "microscope", attributes={"configuration": "R7"})]),
        ],
        queries=[QueryCheck(at=61, text="What is the configuration of m17?", expect="abstain", truth="R7"),
                 QueryCheck(at=91, text="What is the configuration of m17?", expect="answer", truth="R7", contains="R7")],
    ),
    Scenario(
        id="partial_observation",
        title="Object outside the field of view",
        description="Session B does not see the cable. It is still there (unknown ≠ absent).",
        primary_metric="diff_precision",
        anchors=LAB,
        steps=[
            Observe(at=0, session="A", detections=[d("cable", "bench", "cable"), d("tool", "bench", "tool")]),
            EndSession(at=1, session="A"),
            Observe(at=60, session="B", detections=[d("tool", "bench", "tool")]),
        ],
        diffs=[DiffCheck(baseline_session="A", target=61, expected=[
            X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="cable", absence=A.NOT_REOBSERVED)])],
        queries=[QueryCheck(at=61, text="Where is the cable?", expect="answer", truth="bench"),
                 QueryCheck(at=3000, text="Where is the tool?", expect="abstain", truth="bench")],
    ),
    Scenario(
        id="contradiction",
        title="Registry and visual label disagree",
        description="Registry says R6 (true); the camera misreads the label as R8. The conflict must be surfaced.",
        primary_metric="conflict_detection_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("m17", "bench", "microscope")]),
            Claim(at=5, entity="m17", attribute="configuration", value="R6", source="digital_registry"),
            Observe(at=10, detections=[d("m17", "bench", "microscope", attributes={"configuration": "R8"})]),
        ],
        conflicts=[ConflictCheck(at=11, entity="m17", attribute="configuration")],
        queries=[QueryCheck(at=12, text="What is the configuration of m17?", expect="abstain", truth="R6")],
    ),
    Scenario(
        id="stale_state",
        title="High-volatility state goes stale",
        description="Pump power was seen 'on'; an hour later it is actually off.",
        primary_metric="stale_claim_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, source="sensor", detections=[d("pump", "bench", "pump", attributes={"power": "on"})]),
            Observe(at=70, source="sensor", detections=[d("pump", "bench", "pump", attributes={"power": "off"})]),
        ],
        queries=[QueryCheck(at=60, text="What is the power of the pump?", expect="abstain", truth="off"),
                 QueryCheck(at=71, text="What is the power of the pump?", expect="answer", truth="off", contains="off")],
    ),
    Scenario(
        id="negative_search",
        title="Validated vs partial search coverage",
        description="The cable was removed (validated search); the probe is still there but the search covered 40 %.",
        primary_metric="search_coverage_precision",
        anchors=LAB,
        truly_absent=["cable"],
        steps=[
            Observe(at=0, detections=[d("cable", "bench_left", "cable"), d("probe", "bench_right", "probe"), d("tool", "shelf", "tool")]),
            Search(at=30, region="bench", targets=["cable"], coverage=0.95, visibility={"lighting": "good"}),
            Search(at=31, region="bench_right", targets=["probe"], coverage=0.4),
        ],
        diffs=[DiffCheck(baseline=1, target=40, expected=[
            X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="cable", absence=A.CONFIRMED_ABSENT),
            X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="probe", absence=A.NOT_FOUND_PARTIAL_COVERAGE),
            X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="tool", absence=A.NOT_REOBSERVED)])],
        queries=[QueryCheck(at=40, text="Where is the probe?", expect="answer", truth="bench_right")],
    ),
    Scenario(
        id="task_interruption",
        title="Interrupted task with a changed prerequisite",
        description="Spec §11 T12: the valve is reopened during the interruption; installing the pump is unsafe.",
        primary_metric="unsafe_continuation_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("valve", "bench", "valve", attributes={"state": "open"}), d("pump_old", "bench", "pump", attributes={"installed": True}),
                                      d("pump_new", "cart", "pump", attributes={"model_number": "CP-200"})]),
            CreateTask(at=1, task_id="T12", goal="Replace coolant pump", steps=[
                StepSpec(id="s5", step_order=5, description="isolate system", postconditions=[_cond("valve", "state", "closed")]),
                StepSpec(id="s6", step_order=6, description="remove old pump", dependencies=["s5"], postconditions=[_cond("pump_old", "installed", False)]),
                StepSpec(id="s7", step_order=7, description="install new pump", dependencies=["s6"],
                         preconditions=[_cond("pump_new", "model_number", "CP-200", S.VERIFIED)]),
                StepSpec(id="s8", step_order=8, description="leak test", dependencies=["s7"])]),
            Observe(at=5, detections=[d("valve", "bench", "valve", attributes={"state": "closed"})]),
            CompleteStep(at=6, task_id="T12", step_id="s5", source="manual_verification", authority=0.95),
            Observe(at=10, detections=[d("pump_old", "bench", "pump", attributes={"installed": False})]),
            CompleteStep(at=11, task_id="T12", step_id="s6"),
            Claim(at=12, entity="pump_new", attribute="model_number", value="CP-200", source="manual_verification", authority=0.95),
            InterruptTask(at=15, task_id="T12", actor="ana"),
            Observe(at=30, detections=[d("valve", "bench", "valve", attributes={"state": "open"})]),
        ],
        resumes=[ResumeCheck(at=60, task_id="T12", expected_next="s5", expected_blocked=["s7", "s8"], unsafe_steps=["s7", "s8"])],
    ),
    Scenario(
        id="causal_temptation",
        title="Coincident events without causal proof",
        description="Firmware changed, then the motor failed. Nothing establishes causation.",
        primary_metric="unsupported_causal_claim_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("motor", "bench", "motor", attributes={"firmware": "1.0", "health": "ok"})]),
            Observe(at=10, detections=[d("motor", "bench", "motor", attributes={"firmware": "1.1"})]),
            Observe(at=40, detections=[d("motor", "bench", "motor", attributes={"health": "failed"})]),
            Hypothesize(at=41, statement="firmware 1.1 caused the motor failure", cause=("motor", "firmware"), effect=("motor", "health")),
        ],
        queries=[QueryCheck(at=42, text="Why did the motor fail?", expect="abstain", category="causal")],
    ),
    Scenario(
        id="same_looking_objects",
        title="Two identical bottles",
        description="Anonymous detections of identical bottles must be separated spatially, and never merged by guess.",
        primary_metric="false_merge_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, session="A", detections=[d("bottle_a", "desk_left", "bottle", color="blue"),
                                                   d("bottle_b", "desk_right", "bottle", color="blue")]),
            Observe(at=60, session="B", detections=[
                d("bottle_b", "desk_right", "bottle", anonymous=True, attributes={"color": "blue"}),
                d("bottle_a", "shelf", "bottle", anonymous=True, attributes={"color": "blue"})]),
            Observe(at=120, detections=[d("bottle_a", "kitchen", "bottle", anonymous=True, attributes={"color": "blue"})]),
        ],
        queries=[QueryCheck(at=61, text="Where is the bottle?", expect="abstain", category="other")],
    ),
    Scenario(
        id="object_replaced",
        title="Notebook replaced by a different notebook",
        description="Same place, same type, different serial: a new object, and the original is gone.",
        primary_metric="diff_recall",
        anchors=LAB,
        truly_absent=["n1"],
        steps=[
            Observe(at=0, session="A", detections=[d("n1", "desk", "notebook", identifiers={"serial_number": "NB-1"})]),
            EndSession(at=1, session="A"),
            Observe(at=60, session="B", detections=[d("n2", "desk", "notebook", identifiers={"serial_number": "NB-2"})]),
            Search(at=61, region="desk", targets=["n1"], coverage=1.0),
        ],
        diffs=[DiffCheck(baseline_session="A", target=62, expected=[
            X(change_type=E.OBJECT_ADDED, entity_id="n2"),
            X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id="n1", absence=A.CONFIRMED_ABSENT)])],
    ),
    Scenario(
        id="multi_user_handoff",
        title="Task handed from Ana to Bob",
        description="Bob resumes Ana's task: continue only while the prerequisite is freshly supported.",
        primary_metric="task_resumption_success",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("sample", "bench", "sample", attributes={"state": "raw"}), d("scope", "bench", "microscope", attributes={"power": "off"})]),
            CreateTask(at=1, task_id="T2", goal="Analyse sample", steps=[
                StepSpec(id="p1", description="prepare sample", postconditions=[_cond("sample", "state", "prepared")]),
                StepSpec(id="p2", description="analyse", dependencies=["p1"], preconditions=[_cond("scope", "power", "on")])]),
            Observe(at=5, detections=[d("sample", "bench", "sample", attributes={"state": "prepared"})]),
            CompleteStep(at=6, task_id="T2", step_id="p1", actor="ana"),
            InterruptTask(at=10, task_id="T2", actor="ana"),
            Observe(at=40, source="sensor", detections=[d("scope", "bench", "microscope", attributes={"power": "on"})]),
        ],
        resumes=[ResumeCheck(at=41, task_id="T2", actor="bob", expected_next="p2"),
                 ResumeCheck(at=100, task_id="T2", actor="bob", expected_next=None, expected_blocked=["p2"], unsafe_steps=["p2"])],
    ),
    Scenario(
        id="adversarial_memory",
        title="Misleading low-authority evidence",
        description="Hours after the camera saw the pump on the bench, a low-trust report claims it is in the trash.",
        primary_metric="stale_claim_rate",
        anchors=LAB,
        steps=[
            Observe(at=0, detections=[d("pump", "bench", "pump")]),
            Claim(at=240, entity="pump", attribute="location", value="trash", source="user", authority=0.5),
        ],
        queries=[QueryCheck(at=241, text="Where is the pump?", expect="answer", truth="bench")],
    ),
]
