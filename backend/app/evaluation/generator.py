"""Seeded random world generator for ORBIT-BENCH (Phase 14).

Hand-written scenarios were written by someone who knows ORBIT; they regression-test
the design but are not evidence. Here a simulator keeps a hidden *true world*,
produces only camera-style observations from it, and derives every expectation
from that true world — never from ORBIT's behaviour.

Families
* ``scene``    — 5–12 objects on 3–6 surfaces; random moves / removals / additions;
                 a second visit that sees a random subset of surfaces; sometimes an
                 anonymous camera; sometimes validated or partial searches; gaps
                 from 1 h to 2 days.
* ``task``     — a random procedure of valve-closing steps plus a final work step,
                 random risk levels, interrupted; during the interruption a
                 completed step's valve may be reopened, observed or not.
* ``conflict`` — devices whose registry configuration and camera reading agree or
                 disagree at random.

Questions whose answer ORBIT's information cannot settle (moved while unobserved,
memory not yet expired) are marked ``expect="either"`` and are judged only against
the truth (stale-claim rate), not as abstention errors.
"""
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.app.domain.models import StateCondition
from backend.app.domain.types import AbsenceStatus as A
from backend.app.domain.types import EventType as E
from backend.app.domain.types import RiskLevel
from backend.app.evaluation.bench import (
    Claim,
    CompleteStep,
    ConflictCheck,
    CreateTask,
    DiffCheck,
    EndSession,
    InterruptTask,
    Observe,
    QueryCheck,
    ResumeCheck,
    Scenario,
    Search,
)
from backend.app.evaluation.metrics import ExpectedChange as X
from backend.app.services.tasks import StepSpec

FAMILIES = ("scene", "task", "conflict")
TYPES = ("bottle", "cable", "tool", "mug", "notebook")
COLORS = ("blue", "red", "green")
ABSENT = "__absent__"  # truth for objects no longer anywhere ORBIT could point to
DAY = 1440


@dataclass
class _Obj:
    id: str
    type: str
    color: str
    serial: Optional[str]
    start: str
    now: Optional[str]  # true location; None = removed from the workspace


def _detection(o: _Obj, location: str, anonymous: bool) -> Dict:
    d = {"type": o.type, "location": location, "attributes": {"color": o.color}, "truth": o.id}
    if o.serial:
        d["identifiers"] = {"serial_number": o.serial}
    if not anonymous:
        d["candidate_entity_id"] = o.id
    return d


def scene(seed: int) -> Scenario:
    rng = random.Random(f"scene-{seed}")
    surfaces = [f"s{i}" for i in range(1, rng.randint(3, 6) + 1)]
    objs = []
    for i in range(rng.randint(5, 12)):
        t = rng.choice(TYPES)
        objs.append(_Obj(f"{t}_{i}", t, rng.choice(COLORS), f"SN-{seed}-{i}" if rng.random() < 0.4 else None,
                         start := rng.choice(surfaces), start))
    gap = rng.choice([60, 240, 2 * DAY])
    anonymous = rng.random() < 0.4
    for o in objs:
        r = rng.random()
        if r < 0.25:
            o.now = rng.choice([s for s in surfaces if s != o.start])
        elif r < 0.35:
            o.now = None
    added = [_Obj(f"new_{i}", t, rng.choice(COLORS), None, loc := rng.choice(surfaces), loc)
             for i, t in enumerate(rng.choice(TYPES) for _ in range(rng.randint(0, 2)))]
    viewed = set(rng.sample(surfaces, rng.randint(1, len(surfaces))))

    steps: List = [
        Observe(at=0, session="A", detections=[_detection(o, o.start, anonymous=False) for o in objs]),
        EndSession(at=1, session="A"),
        Observe(at=gap, session="B", view=sorted(viewed),
                detections=[_detection(o, o.now, anonymous) for o in objs + added if o.now in viewed]),
    ]
    validated, partial, truly_absent = set(), set(), []
    for surface in sorted(viewed):
        targets = [o.id for o in objs if o.start == surface]
        if not targets or rng.random() < 0.5:
            continue
        coverage = rng.choice([0.95, 0.5])
        seen = [_detection(o, o.now, anonymous) for o in objs + added if o.now == surface]
        steps.append(Search(at=gap + 1, region=surface, targets=targets, coverage=coverage, detections=seen))
        for o in objs:
            if o.start == surface and o.now != surface:
                (validated if coverage >= 0.9 else partial).add(o.id)
                if coverage >= 0.9:
                    truly_absent.append(o.id)

    expected, queries = [], []
    for o in objs:
        seen = o.now in viewed
        if seen and o.now != o.start:
            expected.append(X(change_type=E.OBJECT_MOVED, entity_id=f"truth:{o.id}", attribute="location", after=o.now, check_after=True))
        elif not seen:
            absence = A.CONFIRMED_ABSENT if o.id in validated else A.NOT_FOUND_PARTIAL_COVERAGE if o.id in partial else A.NOT_REOBSERVED
            expected.append(X(change_type=E.OBJECT_REMOVED_OR_UNOBSERVED, entity_id=f"truth:{o.id}", absence=absence))
    expected += [X(change_type=E.OBJECT_ADDED, entity_id=f"truth:{o.id}") for o in added if o.now in viewed]

    for o in rng.sample(objs, min(4, len(objs))):
        truth = o.now or ABSENT
        if o.now in viewed:
            expect = "answer"
        elif o.id in validated or gap > DAY:
            expect = "abstain"
        else:
            expect = "either"  # unobserved since the first visit, memory not expired
        queries.append(QueryCheck(at=gap + 3, text=f"Where is {o.id}?", expect=expect, truth=truth))

    return Scenario(
        id=f"gen-scene-{seed}",
        title=f"random scene (seed {seed}): {len(objs)} objects, {len(surfaces)} surfaces, gap {gap} min"
              + (", anonymous camera" if anonymous else ""),
        description="generated", primary_metric="diff_precision", seed=seed,
        anchors=[("lab", None, "room")] + [(s, "lab", "surface") for s in surfaces],
        steps=steps, diffs=[DiffCheck(baseline_session="A", target=gap + 2, expected=expected)],
        queries=queries, truly_absent=truly_absent,
    )


def task(seed: int) -> Scenario:
    rng = random.Random(f"task-{seed}")
    n = rng.randint(3, 5)
    valves = [f"valve_{i}" for i in range(1, n + 1)]
    steps_spec = [
        StepSpec(id=f"k{i}", step_order=i, description=f"close {v}", dependencies=[f"k{i - 1}"] if i > 1 else [],
                 postconditions=[StateCondition(entity_id=v, attribute="state", expected="closed")],
                 risk=RiskLevel.HIGH if rng.random() < 0.3 else RiskLevel.MEDIUM)
        for i, v in enumerate(valves, 1)
    ]
    steps_spec.append(StepSpec(id="work", step_order=n + 1, description="do the work", dependencies=[f"k{n}"],
                               risk=RiskLevel.HIGH if rng.random() < 0.5 else RiskLevel.MEDIUM))
    done = rng.randint(1, n)
    gap = rng.choice([20, 120, 600])
    disruption = rng.choice(["none", "reopen_observed", "reopen_unobserved"])

    steps: List = [
        Observe(at=0, detections=[{"candidate_entity_id": v, "type": "valve", "location": "rig", "attributes": {"state": "open"}}
                                  for v in valves]),
        CreateTask(at=1, task_id="T", goal="isolate and work", steps=steps_spec),
    ]
    for i in range(1, done + 1):
        steps.append(Observe(at=1 + 2 * i, detections=[{"candidate_entity_id": valves[i - 1], "type": "valve", "attributes": {"state": "closed"}}]))
        steps.append(CompleteStep(at=2 + 2 * i, task_id="T", step_id=f"k{i}"))
    stop = 3 + 2 * done
    steps.append(InterruptTask(at=stop, task_id="T"))
    reopened = None
    if disruption != "none":
        reopened = rng.randint(1, done)
        if disruption == "reopen_observed":
            steps.append(Observe(at=stop + gap / 2, detections=[{"candidate_entity_id": valves[reopened - 1], "type": "valve",
                                                                 "attributes": {"state": "open"}}]))
    # In the true world, every step after a reopened valve is unsafe to present.
    order = [f"k{i}" for i in range(1, n + 1)] + ["work"]
    unsafe = order[reopened:] if reopened else []
    return Scenario(
        id=f"gen-task-{seed}",
        title=f"random task (seed {seed}): {n} valves, {done} closed, gap {gap} min, {disruption}",
        description="generated", primary_metric="unsafe_continuation_rate", seed=seed,
        anchors=[("rig", None)], steps=steps,
        resumes=[ResumeCheck(at=stop + gap, task_id="T", check_next=False, unsafe_steps=unsafe, progress_possible=True)],
    )


def conflict(seed: int) -> Scenario:
    rng = random.Random(f"conflict-{seed}")
    steps: List = []
    checks, queries = [], []
    for i in range(rng.randint(2, 5)):
        dev, record = f"dev_{i}", f"R{rng.randint(1, 9)}"
        reading = record if rng.random() < 0.5 else f"R{rng.randint(10, 19)}"
        truth = record if rng.random() < 0.5 else reading
        steps += [
            Observe(at=0, detections=[{"candidate_entity_id": dev, "type": "device", "location": "rack"}]),
            Claim(at=5 + i, entity=dev, attribute="configuration", value=record, source="digital_registry"),
            Observe(at=10 + i, detections=[{"candidate_entity_id": dev, "type": "device", "attributes": {"configuration": reading}}]),
        ]
        q = f"What is the configuration of {dev}?"
        if reading != record:
            checks.append(ConflictCheck(at=30, entity=dev, attribute="configuration"))
            queries.append(QueryCheck(at=31, text=q, expect="abstain", truth=truth))
        else:
            queries.append(QueryCheck(at=31, text=q, expect="answer", truth=truth))
    return Scenario(id=f"gen-conflict-{seed}", title=f"random conflicts (seed {seed})", description="generated",
                    primary_metric="conflict_detection_rate", seed=seed, anchors=[("rack", None)], steps=steps,
                    conflicts=checks, queries=queries)


GENERATORS = {"scene": scene, "task": task, "conflict": conflict}


def generate_suite(per_family: int, seed: int = 0) -> List[Scenario]:
    return [GENERATORS[f](seed * 10_000 + i) for f in FAMILIES for i in range(per_family)]
