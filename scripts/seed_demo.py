"""Seed a database with the spec §4 flagship scenario, driven through simulated perception.

Session A (3 h ago): bench B3 is observed; a technician works on task T12 and leaves.
Between sessions: M17 is moved, cable C4 is replaced, the registry reports a different
M17 configuration, the bottle on the desk is never re-seen.
Session B (5 min ago): the bench is observed again and C4's spot is searched.

Usage:
    .venv/bin/python scripts/seed_demo.py [--db sqlite:///./orbit_demo.db] [--reset]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.core.clock import FixedClock  # noqa: E402
from backend.app.core.container import OrbitServices  # noqa: E402
from backend.app.domain.models import ObservedRelation, StateCondition  # noqa: E402
from backend.app.domain.types import EpistemicStatus, PrincipalKind, Scope  # noqa: E402
from backend.app.providers.perception import SimulatedPerceptionProvider, SimulatedScene, make_frame  # noqa: E402
from backend.app.repositories.sql import SqlRepository  # noqa: E402
from backend.app.services.perception_gateway import PerceptionGateway  # noqa: E402
from backend.app.services.tasks import StepSpec  # noqa: E402
from database.migrate import upgrade_to_head  # noqa: E402


def seed(url: str, now: datetime) -> OrbitServices:
    upgrade_to_head(url)
    svc = OrbitServices.build(SqlRepository(url), FixedClock(now))
    t = lambda minutes_ago: now - timedelta(minutes=minutes_ago)  # noqa: E731

    for anchor, parent, kind in (("lab204", None, "room"), ("bench_3", "lab204", "surface"), ("bench_4", "lab204", "surface"),
                                 ("cart", "lab204", "surface"), ("desk", "lab204", "surface")):
        svc.anchors.register(anchor, t(200), name=anchor.replace("_", " ").title(), anchor_type=kind, parent_id=parent)

    scene = SimulatedScene()
    scene.place("m17", "microscope", "bench_3", attributes={"configuration": "R6", "power": "off"})
    scene.place("c4", "cable", "bench_3", identifiers={"serial_number": "C4-A"},
                relations=[ObservedRelation(relation_type="connected_to", target="m17")])
    scene.place("t1", "tool", "bench_3")
    scene.place("n1", "notebook", "bench_3", identifiers={"serial_number": "NB-1"})
    scene.place("valve", "valve", "bench_3", attributes={"state": "open"})
    scene.place("pump_old", "pump", "bench_3", attributes={"installed": True})
    scene.place("pump_new", "pump", "cart", attributes={"model_number": "CP-200"})
    scene.place("bottle", "bottle", "desk", attributes={"color": "blue"})
    camera = SimulatedPerceptionProvider(scene, svc.anchors.is_within, seed=7)
    gateway = PerceptionGateway(svc.repo, svc.engine)
    names = {"m17": "Microscope M17", "c4": "Cable C4", "t1": "Tool T1", "n1": "Notebook N1"}

    def look(minutes_ago, view, session):
        obs, _ = gateway.ingest(make_frame("lab_camera", t(minutes_ago), b"frame", field_of_view=view, session_id=session), camera)
        for eid, name in names.items():  # give the demo entities readable names
            e = svc.repo.get_entity(eid)
            if e is not None and e.name != name:
                e.name = name
                svc.repo.save_entity(e)
        return obs

    # ---------------------------------------------------------------- Session A
    look(180, "lab204", "session_A")
    svc.engine.assert_claim("m17", "configuration", "R6", source="digital_registry", timestamp=t(179))
    svc.tasks.create_task(
        "Replace coolant pump",
        [
            StepSpec(id="s5", step_order=5, description="Isolate system",
                     postconditions=[StateCondition(entity_id="valve", attribute="state", expected="closed")]),
            StepSpec(id="s6", step_order=6, description="Remove old pump", dependencies=["s5"],
                     postconditions=[StateCondition(entity_id="pump_old", attribute="installed", expected=False)]),
            StepSpec(id="s7", step_order=7, description="Install new pump", dependencies=["s6"],
                     preconditions=[StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200",
                                                   min_status=EpistemicStatus.VERIFIED)]),
            StepSpec(id="s8", step_order=8, description="Leak test", dependencies=["s7"]),
        ],
        t(178), task_id="T12",
    )
    scene.set("valve", state="closed")
    look(175, "bench_3", "session_A")
    svc.tasks.complete_step("T12", "s5", t(174), source="manual_verification", authority=0.95, actor="ana")
    scene.set("pump_old", installed=False)
    look(170, "bench_3", "session_A")
    svc.tasks.complete_step("T12", "s6", t(169), source="user", actor="ana")
    svc.tasks.interrupt_task("T12", t(165), reason="end of shift", actor="ana")
    session = svc.repo.get_session("session_A")
    session.ended_at = t(165)
    svc.repo.save_session(session)

    # ------------------------------------------------------- between sessions
    scene.move("m17", "bench_4")
    scene.remove("c4")
    scene.place("c4_new", "cable", "bench_3", identifiers={"serial_number": "C4-B"},
                relations=[ObservedRelation(relation_type="connected_to", target="t1")])
    svc.engine.assert_claim("m17", "configuration", "R7", source="digital_registry", timestamp=t(30))

    # ---------------------------------------------------------------- Session B
    camera.stable_ids = False  # session B camera has no fiducial markers: ORBIT must re-identify
    scene.objects["c4_new"].identifiers = {"serial_number": "C4-B"}
    look(5, "bench_3", "session_B")
    look(4, "bench_4", "session_B")
    svc.search.record_search("bench_3", t(3), ["c4"], coverage_fraction=0.95, visibility_conditions={"lighting": "good"},
                             session_id="session_B")
    # The person at the keyboard: may authorise and perform actions the assistant prepares (Phase 16).
    svc.actions.register_principal("operator", PrincipalKind.HUMAN, [Scope.OBSERVE, Scope.AUTHORIZE, Scope.ACTUATE], t(200))
    return svc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("ORBIT_DATABASE_URL", "sqlite:///./orbit_demo.db"))
    parser.add_argument("--reset", action="store_true", help="delete the SQLite file first")
    args = parser.parse_args()
    if args.reset and args.db.startswith("sqlite:///"):
        Path(args.db.replace("sqlite:///", "")).unlink(missing_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    svc = seed(args.db, now)
    print(f"Seeded {len(svc.repo.list_entities())} entities, {len(svc.repo.list_events())} events into {args.db}")
    answer = svc.agent.answer("What changed?", now)
    print("\nORBIT> What changed?\n" + answer.summary)
    answer = svc.agent.answer("Continue.", now)
    print("\nORBIT> Continue.\n" + answer.summary)


if __name__ == "__main__":
    main()
