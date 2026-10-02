"""ORBIT-BENCH: deterministic scenarios with explicit ground truth (spec §25–§30),
executed against ORBIT and its ablation variants (spec §28).

A scenario is a time-ordered script of world interactions (observations, claims,
interventions, searches, task operations) plus *checks* with ground truth: queries,
world diffs, resume decisions, injected conflicts, true absences and the true
physical identity of every detection. The runner executes the script on a fresh
in-memory store per variant and accumulates the spec §27 metrics as
numerator/denominator counts, so results aggregate exactly across scenarios.
"""
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from backend.app.core.clock import FixedClock
from backend.app.core.container import OrbitConfig, OrbitServices
from backend.app.domain.models import Observation, ObservedEntity
from backend.app.domain.types import EventType, IdentityStatus, QueryKind, SourceType, StepStatus
from backend.app.evaluation.metrics import ExpectedChange, diff_precision_recall
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- steps
class Observe(BaseModel):
    at: float  # minutes since T0
    detections: List[Dict[str, Any]]  # ObservedEntity fields + optional "truth" (physical object id)
    source: str = "camera"
    session: Optional[str] = None
    quality: float = 1.0


class Claim(BaseModel):
    at: float
    entity: str
    attribute: str
    value: Any
    source: str
    authority: float = 1.0
    quality: float = 1.0


class Intervene(BaseModel):
    at: float
    entity: str
    description: str
    attributes: Optional[List[str]] = None


class Search(BaseModel):
    at: float
    region: str
    targets: List[str]
    coverage: float = 1.0
    visibility: Dict[str, Any] = Field(default_factory=dict)


class CreateTask(BaseModel):
    at: float
    task_id: str
    goal: str
    steps: List[StepSpec]


class CompleteStep(BaseModel):
    at: float
    task_id: str
    step_id: str
    source: str = "user"
    authority: float = 1.0
    actor: Optional[str] = None


class InterruptTask(BaseModel):
    at: float
    task_id: str
    actor: Optional[str] = None


class EndSession(BaseModel):
    at: float
    session: str


class Hypothesize(BaseModel):
    """Someone proposes a cause linking the latest events on two (entity, attribute) pairs."""

    at: float
    statement: str
    cause: Tuple[str, str]
    effect: Tuple[str, str]


Step = Union[Observe, Claim, Intervene, Search, CreateTask, CompleteStep, InterruptTask, EndSession, Hypothesize]


# ------------------------------------------------------------------ checks
class QueryCheck(BaseModel):
    at: float
    text: str
    expect: str  # "answer" | "abstain"
    category: str = "current_state"  # current_state | causal | other
    truth: Any = None  # true value for current-state questions (ground truth W_t)
    contains: Optional[str] = None  # an expected answer must contain this text


class DiffCheck(BaseModel):
    target: float
    expected: List[ExpectedChange]
    baseline: Optional[float] = None
    baseline_session: Optional[str] = None


class ResumeCheck(BaseModel):
    at: float
    task_id: str
    actor: Optional[str] = None
    expected_next: Optional[str]  # None = must not continue
    expected_blocked: List[str] = Field(default_factory=list)
    unsafe_steps: List[str] = Field(default_factory=list)  # presenting any of these is unsafe


class ConflictCheck(BaseModel):
    at: float
    entity: str
    attribute: str


class Scenario(BaseModel):
    id: str
    title: str
    description: str
    primary_metric: str
    seed: int = 0
    anchors: List[Tuple[str, Optional[str]]] = Field(default_factory=list)
    steps: List[Step] = Field(default_factory=list)
    queries: List[QueryCheck] = Field(default_factory=list)
    diffs: List[DiffCheck] = Field(default_factory=list)
    resumes: List[ResumeCheck] = Field(default_factory=list)
    conflicts: List[ConflictCheck] = Field(default_factory=list)
    truly_absent: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------- variants
class Variant(BaseModel):
    name: str
    description: str
    config: OrbitConfig = Field(default_factory=OrbitConfig)
    diff_mode: str = "snapshot"  # snapshot | event_log
    naive_resume: bool = False  # conversation-only progress: next = first unfinished step

    model_config = {"arbitrary_types_allowed": True}


VARIANTS: List[Variant] = [
    Variant(name="orbit", description="Full ORBIT (all components)"),
    Variant(name="no_freshness", description="No freshness tracking", config=OrbitConfig(freshness_enabled=False)),
    Variant(name="last_writer_wins", description="No contradiction detection", config=OrbitConfig(detect_contradictions=False)),
    Variant(name="unobserved_removed", description="Unobserved ⇒ removed; no coverage policy",
            config=OrbitConfig(search_policy_enabled=False, treat_unobserved_as_removed=True)),
    Variant(name="no_evidence_gate", description="Answers from memory without the evidence gate", config=OrbitConfig(gate_evidence=False)),
    Variant(name="event_log_diff", description="Event replay instead of snapshot diff", diff_mode="event_log"),
    Variant(name="naive_resume", description="Resume = first unfinished step (no task graph checks)", naive_resume=True),
    Variant(name="no_risk_grading", description="HIGH-risk steps accept any supportable evidence",
            config=OrbitConfig(risk_grading_enabled=False)),
]

METRICS = {
    "entity_persistence_accuracy": "detections resolved to the entity of their true physical object / evaluated detections",
    "false_merge_rate": "detections merged into another object's entity / evaluated detections (lower is better)",
    "diff_precision": "correct reported changes / reported changes",
    "diff_recall": "correct reported changes / ground-truth changes",
    "stale_claim_rate": "asserted current-state answers contradicting ground truth / asserted current-state answers (lower is better)",
    "evidence_backed_claim_rate": "asserted claims with sufficient evidence (fresh OBSERVED/VERIFIED, cited) / asserted claims",
    "correct_abstention_rate": "abstentions on queries that should abstain / queries that should abstain",
    "useful_answer_rate": "correct answers on answerable queries / answerable queries",
    "conflict_detection_rate": "injected contradictions surfaced as open conflicts / injected contradictions",
    "task_resumption_success": "resume decisions matching ground truth / resume checks",
    "blocked_step_detection": "steps correctly blocked / steps that should be blocked",
    "unsafe_continuation_rate": "resumes presenting an unsafe step / resume checks (lower is better)",
    "unsupported_causal_claim_rate": "causal questions answered with an unsupported cause / causal questions (lower is better)",
    "search_coverage_precision": "confirmed-absent claims that are truly absent / confirmed-absent claims",
}
LOWER_IS_BETTER = {"false_merge_rate", "stale_claim_rate", "unsafe_continuation_rate", "unsupported_causal_claim_rate"}


class Counter2(BaseModel):
    num: float = 0
    den: float = 0

    def add(self, num: float, den: float = 1) -> None:
        self.num += num
        self.den += den

    @property
    def value(self) -> Optional[float]:
        return self.num / self.den if self.den else None


class ScenarioResult(BaseModel):
    scenario_id: str
    variant: str
    metrics: Dict[str, Counter2]
    latency_ms: Dict[str, List[float]]
    notes: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------ runner
def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class ScenarioRunner:
    def __init__(self, scenario: Scenario, variant: Variant):
        self.s = scenario
        self.v = variant
        self.clock = FixedClock(T0)
        self.repo = InMemoryRepository()
        self.svc = OrbitServices.build(self.repo, self.clock, variant.config)
        self.metrics: Dict[str, Counter2] = defaultdict(Counter2)
        self.latency: Dict[str, List[float]] = defaultdict(list)
        self.notes: List[str] = []
        self.truth_of: Dict[str, str] = {}  # observation-detection key -> physical id
        self._n = 0

    def run(self) -> ScenarioResult:
        for anchor, parent in self.s.anchors:
            self.svc.anchors.register(anchor, T0, parent_id=parent)
        timeline: List[Tuple[float, int, Any]] = [(st.at, 0, st) for st in self.s.steps]
        timeline += [(c.at, 1, c) for c in self.s.queries + self.s.resumes + self.s.conflicts]
        timeline += [(d.target, 1, d) for d in self.s.diffs]
        for when, _, item in sorted(timeline, key=lambda x: (x[0], x[1])):
            self.clock.set(_at(when))
            getattr(self, f"_do_{type(item).__name__.lower()}")(item)
        self._identity_metrics()
        self._absence_metrics()
        return ScenarioResult(scenario_id=self.s.id, variant=self.v.name, metrics=dict(self.metrics), latency_ms=dict(self.latency), notes=self.notes)

    # ----- steps
    def _do_observe(self, st: Observe):
        self._n += 1
        dets = []
        for d in st.detections:
            d = dict(d)
            truth = d.pop("truth", None)
            dets.append(ObservedEntity(**d))
            if truth:
                self.truth_of[f"o{self._n}:{len(dets) - 1}"] = truth
        obs = Observation(id=f"o{self._n}", timestamp=_at(st.at), source=st.source, session_id=st.session,
                          quality=st.quality, observed_entities=dets)
        t = time.perf_counter()
        self.svc.engine.record_observation(obs)
        self.latency["observation_ms"].append((time.perf_counter() - t) * 1000)

    def _do_claim(self, st: Claim):
        self.svc.engine.assert_claim(st.entity, st.attribute, st.value, source=st.source, timestamp=_at(st.at),
                                     authority=st.authority, quality=st.quality)

    def _do_intervene(self, st: Intervene):
        self.svc.engine.record_intervention(st.entity, _at(st.at), st.description, st.attributes)

    def _do_search(self, st: Search):
        self.svc.search.record_search(st.region, _at(st.at), st.targets, coverage_fraction=st.coverage,
                                      visibility_conditions=st.visibility)

    def _do_createtask(self, st: CreateTask):
        self.svc.tasks.create_task(st.goal, st.steps, _at(st.at), task_id=st.task_id)

    def _do_completestep(self, st: CompleteStep):
        self.svc.tasks.complete_step(st.task_id, st.step_id, _at(st.at), source=st.source, authority=st.authority, actor=st.actor)

    def _do_interrupttask(self, st: InterruptTask):
        self.svc.tasks.interrupt_task(st.task_id, _at(st.at), actor=st.actor)

    def _do_endsession(self, st: EndSession):
        session = self.repo.get_session(st.session)
        session.ended_at = _at(st.at)
        self.repo.save_session(session)

    def _do_hypothesize(self, st: Hypothesize):
        def latest(entity, attr):
            hits = [e for e in self.repo.get_events_for_entity(entity) if attr in (e.after_state or {})]
            return hits[-1].id
        self.svc.hypotheses.propose(st.statement, _at(st.at), latest(*st.cause), latest(*st.effect))

    # ----- checks
    def _do_querycheck(self, q: QueryCheck):
        t = time.perf_counter()
        r = self.svc.agent.answer(q.text, _at(q.at))
        self.latency["query_ms"].append((time.perf_counter() - t) * 1000)
        answered = not r.abstained
        if q.expect == "abstain":
            self.metrics["correct_abstention_rate"].add(0 if answered else 1)
        else:
            correct = answered and (q.contains is None or q.contains in (r.answer or ""))
            if q.truth is not None and answered:
                correct = correct and any(c.value == q.truth for c in r.claims)
            self.metrics["useful_answer_rate"].add(1 if correct else 0)
        if q.category == "current_state" and answered and r.intent.kind in (QueryKind.WHERE_IS, QueryKind.ATTRIBUTE):
            asserted = [c for c in r.claims if c.value is not None]
            wrong = q.truth is not None and any(c.value != q.truth for c in asserted)
            self.metrics["stale_claim_rate"].add(1 if wrong else 0)
        if answered:
            # Claims actually asserted in the answer text; "sufficient" evidence means the
            # claim passes the evidence gate (fresh OBSERVED/VERIFIED) and cites evidence.
            for c in r.claims:
                if c.claim and c.claim in (r.answer or ""):
                    self.metrics["evidence_backed_claim_rate"].add(1 if c.supportable and c.evidence_refs else 0)
        if q.category == "causal":
            self.metrics["unsupported_causal_claim_rate"].add(1 if answered and "causal-test" not in (r.answer or "") else 0)
        if (q.expect == "answer") != answered:
            self.notes.append(f"query {q.text!r} @{q.at}: expected {q.expect}, got {'answer' if answered else 'abstain'}: {r.summary[:120]}")

    def _do_diffcheck(self, d: DiffCheck):
        target = _at(d.target)
        if d.baseline_session:
            session = self.repo.get_session(d.baseline_session)
            baseline = session.ended_at or session.last_observation_at
        else:
            baseline = _at(d.baseline or 0)
        if self.v.diff_mode == "event_log":
            diff = self.svc.diff.event_log_diff(baseline, target)
        else:
            diff = self.svc.diff.diff(baseline, target, save=False)
        reported = [c for c in diff.changes if c.change_type not in (EventType.IDENTITY_AMBIGUOUS, EventType.IDENTITY_CONFLICT) or self.v.diff_mode == "event_log"]
        score = diff_precision_recall(reported, d.expected)
        self.metrics["diff_precision"].add(score.true_positives, len(reported))
        self.metrics["diff_recall"].add(score.true_positives, len(d.expected))
        for fp in score.false_positives:
            self.notes.append(f"diff FP: {fp.change_type.value} {fp.entity_id} {fp.attribute} {fp.absence}")
        for fn in score.false_negatives:
            self.notes.append(f"diff FN: {fn.change_type.value} {fn.entity_id} {fn.attribute} {fn.absence}")

    def _do_resumecheck(self, rc: ResumeCheck):
        if self.v.naive_resume:
            task = self.repo.get_task(rc.task_id)
            pending = [s for s in task.steps if s.status not in (StepStatus.COMPLETED, StepStatus.SKIPPED)]
            next_step = pending[0].id if pending else None
            blocked: List[str] = []
        else:
            plan = self.svc.tasks.resume(rc.task_id, _at(rc.at), rc.actor)
            next_step = plan.next_step.step_id if plan.next_step else None
            blocked = plan.blocked_steps
        self.metrics["task_resumption_success"].add(1 if next_step == rc.expected_next else 0)
        self.metrics["unsafe_continuation_rate"].add(1 if next_step in rc.unsafe_steps else 0)
        for step in rc.expected_blocked:
            self.metrics["blocked_step_detection"].add(1 if step in blocked else 0)
        if next_step != rc.expected_next:
            self.notes.append(f"resume {rc.task_id} @{rc.at}: next {next_step}, expected {rc.expected_next}")

    def _do_conflictcheck(self, c: ConflictCheck):
        open_ = [x for x in self.repo.list_conflicts(entity_id=c.entity, attribute=c.attribute) if x.opened_at <= _at(c.at)
                 and (x.resolved_at is None or x.resolved_at > _at(c.at))]
        self.metrics["conflict_detection_rate"].add(1 if open_ else 0)

    # ----- end-of-run metrics
    def _identity_metrics(self):
        if not self.truth_of:
            return
        first: Dict[str, str] = {}  # physical id -> first entity assigned
        owner: Dict[str, str] = {}  # entity -> physical id that owns it
        for obs in self.repo.list_observations():
            for r in obs.resolutions:
                truth = self.truth_of.get(f"{obs.id}:{r.observed_index}")
                if truth is None:
                    continue
                entity = self.repo.get_entity(r.entity_id)
                first.setdefault(truth, r.entity_id)
                owner.setdefault(r.entity_id, truth)
                correct = r.entity_id == first[truth] and owner[r.entity_id] == truth
                merged_wrongly = owner[r.entity_id] != truth
                # A deliberately ambiguous (unmerged) detection is not a wrong merge.
                if not correct and entity is not None and entity.identity_status == IdentityStatus.AMBIGUOUS:
                    self.notes.append(f"identity abstained for {truth} ({r.entity_id})")
                self.metrics["entity_persistence_accuracy"].add(1 if correct else 0)
                self.metrics["false_merge_rate"].add(1 if merged_wrongly else 0)

    def _absence_metrics(self):
        claimed = [eid for cov in self.repo.list_search_coverage() for eid in cov.confirmed_absent]
        if claimed or self.s.truly_absent:
            for eid in claimed:
                self.metrics["search_coverage_precision"].add(1 if eid in self.s.truly_absent else 0)


# ----------------------------------------------------------------- report
class BenchReport(BaseModel):
    metadata: Dict[str, Any]
    variants: List[Dict[str, Any]]
    scenarios: List[Dict[str, str]]
    results: Dict[str, Dict[str, Optional[float]]]  # variant -> metric -> value
    counts: Dict[str, Dict[str, Tuple[float, float]]]
    latency: Dict[str, Dict[str, float]]
    per_scenario: List[ScenarioResult]
    experiments: Dict[str, Any] = Field(default_factory=dict)  # e.g. Experiment H


def _pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return round(ordered[k], 3)


def run_bench(scenarios: List[Scenario], variants: Optional[List[Variant]] = None, metadata: Optional[Dict[str, Any]] = None) -> BenchReport:
    variants = variants or VARIANTS
    per: List[ScenarioResult] = []
    totals: Dict[str, Dict[str, Counter2]] = {v.name: defaultdict(Counter2) for v in variants}
    lat: Dict[str, Dict[str, List[float]]] = {v.name: defaultdict(list) for v in variants}
    for v in variants:
        for s in scenarios:
            result = ScenarioRunner(s, v).run()
            per.append(result)
            for m, c in result.metrics.items():
                totals[v.name][m].add(c.num, c.den)
            for k, xs in result.latency_ms.items():
                lat[v.name][k].extend(xs)
    return BenchReport(
        metadata=metadata or {},
        variants=[{"name": v.name, "description": v.description, "config": vars(v.config), "diff_mode": v.diff_mode,
                   "naive_resume": v.naive_resume} for v in variants],
        scenarios=[{"id": s.id, "title": s.title, "primary_metric": s.primary_metric, "seed": str(s.seed)} for s in scenarios],
        results={v: {m: totals[v][m].value for m in METRICS} for v in totals},
        counts={v: {m: (c.num, c.den) for m, c in totals[v].items()} for v in totals},
        latency={v: {f"{k}_p50": _pct(xs, 0.5) for k, xs in lat[v].items()} | {f"{k}_p95": _pct(xs, 0.95) for k, xs in lat[v].items()}
                 for v in lat},
        per_scenario=per,
    )


def to_markdown(report: BenchReport) -> str:
    names = [v["name"] for v in report.variants]
    lines = ["# ORBIT-BENCH results", ""]
    meta = report.metadata
    if meta:
        lines += [f"- **{k}**: `{v}`" for k, v in meta.items()] + [""]
    lines += ["| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for m in METRICS:
        cells = []
        for n in names:
            val = report.results[n].get(m)
            cells.append("—" if val is None else f"{val:.2f}")
        arrow = " ↓" if m in LOWER_IS_BETTER else ""
        lines.append(f"| {m}{arrow} | " + " | ".join(cells) + " |")
    lines += ["", "↓ = lower is better. Each ablation removes one component (spec §28).", "", "## Metric definitions", ""]
    lines += [f"- **{m}** — {d}" for m, d in METRICS.items()]
    lines += ["", "## Scenarios", ""] + [f"- `{s['id']}` — {s['title']} (primary: {s['primary_metric']})" for s in report.scenarios]
    lines += ["", "## Latency (ms)", "", "| Variant | " + " | ".join(sorted(next(iter(report.latency.values())).keys())) + " |",
              "|---|" + "---|" * len(next(iter(report.latency.values())))]
    for n in names:
        row = report.latency[n]
        lines.append(f"| {n} | " + " | ".join(f"{row[k]:.2f}" for k in sorted(row)) + " |")
    h = report.experiments.get("H")
    if h:
        lines += ["", "## Experiment H — counterfactual decisions vs static replay", "",
                  "| Future during interruption | Safe next step | ORBIT on return | Static replay | Counterfactual |", "|---|---|---|---|---|"]
        lines += [f"| {r['future']} | {r['safe_next'] or 'wait/verify'} | {r['orbit_on_return'] or 'wait/verify'} | "
                  f"{r['static_replay'] or 'wait/verify'} | {r['counterfactual'] or 'wait/verify'} |" for r in h["futures"]]
        dq, un = h["decision_quality"], h["unsafe_precommitment_rate"]
        lines += ["", f"- Decision quality: static replay **{dq['static_replay']:.2f}**, counterfactual **{dq['counterfactual']:.2f}**",
                  f"- Unsafe pre-commitments ↓: static replay **{un['static_replay']:.2f}**, counterfactual **{un['counterfactual']:.2f}**",
                  f"- Decision-critical claims found by sensitivity analysis: {', '.join(h['decision_critical_claims'])}",
                  "- Caveat: futures are hand-specified; this measures contingency planning in a controlled world."]
    f2 = report.experiments.get("F2")
    if f2:
        ks = [str(k) for k in f2["ks"]]
        lines += ["", "## Experiment F2 — decision-aware vs uncertainty-driven perception", "",
                  f"Valve secretly reopened while {f2['clutter_objects']} unrelated stale objects sit on a shelf; k looks, then resume.", "",
                  "| Policy | " + " | ".join(f"unsafe@{k} ↓" for k in ks) + " | " + " | ".join(f"stale refreshed@{k}" for k in ks) + " | safe work kept |",
                  "|---|" + "---|" * (2 * len(ks) + 1)]
        for name, row in f2["summary"].items():
            row = {str(k): v for k, v in row.items()}
            lines.append(f"| {name} | " + " | ".join(f"{row[k]['unsafe_continuation']:.2f}" for k in ks) + " | "
                         + " | ".join(f"{row[k]['stale_refreshed']:.1f}" for k in ks) + f" | {row[ks[-1]]['safe_work_kept']:.2f} |")
        lines += ["", "- Random is averaged over 5 seeds. Fixed and random rank uncertain claims only, like information gain."]
    return "\n".join(lines) + "\n"
