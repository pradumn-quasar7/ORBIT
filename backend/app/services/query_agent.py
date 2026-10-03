"""Grounded query agent (spec §5.2 control loop, §14, §15).

Interpret → identify required claims → retrieve structured state (and semantic
recall where useful) → check freshness/evidence → answer only what is supportable;
otherwise surface conflicts, abstain and request a targeted observation.

The reasoning provider only maps language to an intent. Every fact in a response
comes from structured state through the evidence gate; similarity scores are
returned as recall aids, never as support.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional

from backend.app.domain.models import (
    ClaimAssessment,
    GroundedClaim,
    GroundedResponse,
    ObservationRequest,
    QueryIntent,
    WorldChange,
)
from backend.app.domain.types import AbsenceStatus, EpistemicStatus, EventType, FreshnessState, HypothesisStatus, QueryKind, TaskStatus
from backend.app.providers.base import EmbeddingProvider, ReasoningProvider, RetrievalProvider, Vocabulary
from backend.app.providers.embedding import HashingEmbeddingProvider
from backend.app.providers.reasoning import RuleBasedReasoningProvider
from backend.app.providers.retrieval import InMemoryVectorIndex
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.conditions import instruction_for
from backend.app.services.evidence_policy import grade
from backend.app.services.hybrid_retrieval import HybridRetriever
from backend.app.services.hypotheses import HypothesisService
from backend.app.services.memory import MemoryService
from backend.app.services.tasks import TaskService
from backend.app.services.world_diff import WorldDiffService
from backend.app.services.world_state_engine import WorldStateEngine


def hhmm(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "unknown time"


class QueryAgent:
    def __init__(
        self,
        repository: Repository,
        engine: WorldStateEngine,
        memory: MemoryService,
        diff: WorldDiffService,
        tasks: TaskService,
        hypotheses: HypothesisService,
        reasoning: Optional[ReasoningProvider] = None,
        embedding: Optional[EmbeddingProvider] = None,
        retrieval: Optional[RetrievalProvider] = None,
        gate_evidence: bool = True,
        planner=None,
    ):
        self.repo = repository
        self.engine = engine
        self.memory = memory
        self.diff = diff
        self.tasks = tasks
        self.hypotheses = hypotheses
        self.reasoning = reasoning or RuleBasedReasoningProvider()
        self.embedding = embedding or HashingEmbeddingProvider()
        self.retrieval = retrieval or InMemoryVectorIndex(self.embedding)
        self.retriever = HybridRetriever(repository, self.retrieval)
        # gate_evidence=False is the "LLM-only / ungated answer" ablation (spec §28).
        self.gate_evidence = gate_evidence
        self.planner = planner  # ActivePerceptionPlanner: ranks requested observations

    # ----------------------------------------------------------------- entry
    def vocabulary(self) -> Vocabulary:
        entities, types, attributes = {}, {}, set()
        everything = self.repo.list_entities()
        aliases: Dict[str, List[str]] = {}
        for e in everything:
            if e.merged_into:
                aliases.setdefault(e.merged_into, []).append(e.id)
        for e in everything:
            if e.merged_into:
                continue
            forms = [e.id] + aliases.get(e.id, [])
            if e.name and e.name != e.type:
                forms.append(e.name)
            entities[e.id] = forms
            types[e.id] = e.type
            attributes.update(e.current_state)
            attributes.update(e.canonical_attributes)
        anchors = {a.id: [a.id] + ([a.name] if a.name and a.name != a.id else []) for a in self.repo.list_anchors()}
        tasks = {t.id: [t.id, t.goal] for t in self.repo.list_tasks()}
        return Vocabulary(entities, types, anchors, sorted(attributes), tasks)

    def answer(self, query: str, at: datetime) -> GroundedResponse:
        intent = self.reasoning.interpret(query, self.vocabulary(), at)
        # Questions about the world or a task step do not need a single object resolved.
        if intent.ambiguous and not intent.entity_ids and intent.kind not in (
            QueryKind.WHAT_CHANGED, QueryKind.CONTINUE, QueryKind.SAFETY
        ):
            return self._finish(self._clarify(intent, at))
        handler = {
            QueryKind.WHERE_IS: self._where_is,
            QueryKind.ATTRIBUTE: self._attribute,
            QueryKind.CONTENTS: self._contents,
            QueryKind.WHAT_CHANGED: self._what_changed,
            QueryKind.CONTINUE: self._continue,
            QueryKind.WHAT_HAPPENED: self._what_happened,
            QueryKind.WHY: self._why,
            QueryKind.SAFETY: self._safety,
        }.get(intent.kind, self._unknown)
        return self._finish(handler(intent, at))

    def _finish(self, response: GroundedResponse) -> GroundedResponse:
        response.providers = {
            "reasoning": self.reasoning.name,
            "embedding": self.embedding.name,
            "retrieval": self.retrieval.name,
            "evidence_gate": "on" if self.gate_evidence else "off",
        }
        if self.planner is not None and response.requested_observations:
            response.requested_observations = self.planner.rank_requests(response.requested_observations)
        if response.requested_observations and response.requested_observation is None:
            response.requested_observation = response.requested_observations[0]
        return response

    # -------------------------------------------------------------- helpers
    def _name(self, entity_id: str) -> str:
        e = self.repo.get_entity(entity_id)
        return e.name if e and e.name and e.name != e.type else entity_id

    def _respond(self, intent: QueryIntent, at: datetime, summary: str, answer: Optional[str] = None, **kw) -> GroundedResponse:
        return GroundedResponse(
            query=intent.raw, as_of=intent.as_of or at, intent=intent, answer=answer, summary=summary, abstained=answer is None, **kw
        )

    @staticmethod
    def _claim(a: ClaimAssessment, text: str) -> GroundedClaim:
        return GroundedClaim(
            claim=text,
            entity_id=a.entity_id,
            attribute=a.attribute,
            value=a.value if a.supportable else a.last_known_value,
            status=a.status,
            supportable=a.supportable,
            freshness=a.freshness,
            evidence_refs=a.evidence_refs,
        )

    def _request(self, a: ClaimAssessment) -> ObservationRequest:
        return ObservationRequest(
            entity_id=a.entity_id,
            attribute=a.attribute,
            reason=a.reason,
            instruction=instruction_for(self._name(a.entity_id), a.attribute, a.status, a.last_known_value),
            current_status=a.status,
            last_known_value=a.last_known_value,
        )

    def _clarify(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        parts = []
        for mention, ids in intent.ambiguous.items():
            options = []
            for i in ids:
                loc = self.engine.claims.assess_attribute(i, LOCATION, at).last_known_value
                options.append(f"{i}" + (f" (last at {loc})" if loc else ""))
            parts.append(f"Which {mention} do you mean: {', '.join(options)}?")
        return self._respond(intent, at, " ".join(parts))

    def _require_entities(self, intent: QueryIntent, at: datetime, what: str) -> Optional[GroundedResponse]:
        if intent.entity_ids:
            return None
        return self._respond(intent, at, f"I couldn't tell which object you mean, so I can't answer {what}.")

    # ---------------------------------------------- point-in-time state claims
    def _point(self, intent: QueryIntent, at: datetime, attribute: str) -> GroundedResponse:
        as_of = intent.as_of or at
        historical = intent.as_of is not None and intent.as_of < at
        answers, notes, claims, conflicts, requests = [], [], [], [], []
        for eid in intent.entity_ids:
            name = self._name(eid)
            a = self.engine.claims.assess_attribute(eid, attribute, as_of)
            verb = "was" if historical else "is"
            noun = "at" if attribute == LOCATION else f"{attribute.replace('_', ' ')} ="
            subject = name if attribute == LOCATION else f"{name}'s"
            if a.supportable or (not self.gate_evidence and a.last_known_value is not None):
                value = a.value if a.supportable else a.last_known_value
                when = a.freshness.last_supported_at if a.freshness else None
                line = (
                    f"{name} {verb} at {value}" if attribute == LOCATION else f"{subject} {attribute.replace('_', ' ')} {verb} {value!r}"
                ) + f" ({a.status.value}, last supported {hhmm(when)})"
                if a.supportable and a.freshness and a.freshness.state == FreshnessState.AGING:
                    line += " — aging, worth re-checking"
                answers.append(line + ".")
                claims.append(self._claim(a, line))
                if a.recommended_action == "observe" and not historical:
                    requests.append(self._request(a))
                continue
            if a.status == EpistemicStatus.CONTRADICTED:
                sides = "; ".join(f"{s.value!r} ({', '.join(s.sources)})" for s in a.conflicts)
                notes.append(f"Sources disagree about {subject} {attribute.replace('_', ' ')}: {sides}. I won't pick one.")
                conflicts += a.conflicts
            elif not a.has_current_claim and a.last_known_value is not None:
                notes.append(f"{name}: {a.reason}; its current {attribute.replace('_', ' ')} is unknown.")
            elif a.status == EpistemicStatus.STALE:
                when = a.freshness.last_supported_at if a.freshness else None
                notes.append(
                    f"{name} {'was last seen at' if attribute == LOCATION else f'last had {attribute} ='} {a.last_known_value} "
                    f"({hhmm(when)}), but that is stale ({a.reason.replace('stale: ', '')}); I can't confirm it now."
                )
            elif a.status == EpistemicStatus.INFERRED:
                notes.append(f"{subject} {attribute} is only inferred ({a.last_known_value!r}), not observed.")
            else:
                notes.append(f"I don't have sufficient evidence about {subject} {attribute.replace('_', ' ')}.")
            claims.append(self._claim(a, notes[-1]))
            if not historical:
                requests.append(self._request(a))
        summary = " ".join(answers + notes)
        return self._respond(
            intent, at, summary, " ".join(answers) if answers else None,
            claims=claims, conflicts=conflicts, requested_observations=requests,
        )

    def _where_is(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        return self._require_entities(intent, at, "where it is") or self._point(intent, at, LOCATION)

    def _attribute(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        return self._require_entities(intent, at, "that") or self._point(intent, at, intent.attribute or LOCATION)

    def _contents(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        anchor = intent.anchor_id
        if anchor is None:
            return self._respond(intent, at, "I couldn't tell which place you mean.")
        as_of = intent.as_of or at
        items = self.memory.contents(anchor, as_of)
        confirmed = [c for c in items if c.assessment.supportable]
        unconfirmed = [c for c in items if not c.assessment.supportable]
        claims = [self._claim(c.assessment, f"{c.entity_id} at {c.location} (via {c.via})") for c in items]
        answer = (
            f"On/in {anchor}: " + ", ".join(f"{self._name(c.entity_id)} ({c.location}, {c.assessment.status.value})" for c in confirmed) + "."
            if confirmed
            else None
        )
        notes = []
        if unconfirmed:
            notes.append("Not confirmed now: " + ", ".join(f"{self._name(c.entity_id)} ({c.assessment.status.value})" for c in unconfirmed) + ".")
        if not items:
            notes.append(f"Nothing is recorded at {anchor}; that does not mean it is empty.")
        return self._respond(
            intent, at, " ".join(([answer] if answer else []) + notes), answer,
            claims=claims, requested_observations=[self._request(c.assessment) for c in unconfirmed],
        )

    # ------------------------------------------------------------ what changed
    def _baseline(self, intent: QueryIntent, at: datetime) -> Optional[datetime]:
        if intent.since is not None:
            return intent.since
        sessions = [s for s in self.repo.list_sessions() if s.started_at < at]
        if not sessions:
            return None
        latest = sessions[-1]
        if latest.ended_at is not None and latest.ended_at <= at:
            return latest.ended_at
        if len(sessions) >= 2:  # we are inside the latest session: compare with the one before
            return MemoryService.session_end(sessions[-2])
        return None

    def phrase_change(self, c: WorldChange) -> str:
        n = self._name(c.entity_id)
        t = c.change_type
        if t == EventType.OBJECT_MOVED:
            return f"{n} moved from {c.before} to {c.after}." if c.before else f"{n} was found again at {c.after}."
        if t in (EventType.OBJECT_STATE_CHANGED, EventType.PROCEDURE_REVISION_DETECTED):
            return f"{n} {c.attribute.replace('_', ' ')} changed from {c.before!r} to {c.after!r}."
        if t == EventType.OBJECT_ADDED:
            where = c.after.get(LOCATION) if isinstance(c.after, dict) else None
            return f"{n} appeared" + (f" at {where}" if where else "") + (f" — {c.note}" if c.note else "") + "."
        if t == EventType.OBJECT_REMOVED_OR_UNOBSERVED:
            if c.absence == AbsenceStatus.CONFIRMED_ABSENT:
                return f"{n} is confirmed absent from {c.before} (validated search); whereabouts unknown."
            if c.absence == AbsenceStatus.NOT_FOUND_PARTIAL_COVERAGE:
                return f"{n} was not found at {c.before}, but the search did not cover enough to call it removed."
            return f"{n} was not re-observed (last known at {c.before}); unknown, not assumed removed."
        if t == EventType.RELATION_CHANGED:
            if c.before and c.after:
                return f"{n} {c.attribute.replace('_', ' ')} changed from {c.before} to {c.after}."
            if c.after:
                return f"{n} is now {c.attribute.replace('_', ' ')} {c.after}."
            return f"{n} is no longer {c.attribute.replace('_', ' ')} {c.before}."
        if t == EventType.EVIDENCE_CONFLICT:
            return f"Sources now disagree about {n}'s {c.attribute}: {', '.join(repr(v) for v in c.after)}."
        if t == EventType.IDENTITY_MERGED:
            return f"{c.entity_id} was confirmed to be {self._name(c.after)} (identity corrected by a person)."
        if t == EventType.TASK_PROGRESS_CHANGED:
            target = f"step {c.attribute}" if c.attribute else "task"
            return f"Task {c.entity_id} {target}: {c.before} → {c.after}."
        return f"{t.value} on {n}."

    def _what_changed(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        baseline = self._baseline(intent, at)
        if baseline is None:
            return self._respond(intent, at, "There is no earlier session or time to compare against.")
        diff = self.diff.diff(baseline, intent.until or at)
        changes = [c for c in diff.changes if not intent.entity_ids or c.entity_id in intent.entity_ids]
        lines = [self.phrase_change(c) for c in changes]
        claims = [
            GroundedClaim(
                claim=line,
                entity_id=c.entity_id,
                attribute=c.attribute,
                value=c.after,
                status=c.status or EpistemicStatus.OBSERVED,
                supportable=bool(c.evidence_refs) or c.change_type == EventType.TASK_PROGRESS_CHANGED,
                evidence_refs=c.evidence_refs,
            )
            for c, line in zip(changes, lines)
        ]
        uncertain = [u for u in diff.uncertain if not intent.entity_ids or u.entity_id in intent.entity_ids]
        requests = [
            ObservationRequest(
                entity_id=u.entity_id,
                attribute=u.attribute,
                reason=u.reason,
                instruction=instruction_for(self._name(u.entity_id), u.attribute, u.status, u.last_known_value),
                current_status=u.status,
                last_known_value=u.last_known_value,
            )
            for u in uncertain
        ]
        head = f"Since {hhmm(baseline)}: " + (" ".join(lines) if lines else "no evidence-supported changes.")
        tail = (
            " Not confirmed (needs a fresh look): "
            + ", ".join(f"{self._name(u.entity_id)}.{u.attribute} ({u.status.value})" for u in uncertain)
            + "."
            if uncertain
            else ""
        )
        return self._respond(intent, at, head + tail, head, claims=claims, changes=changes, requested_observations=requests)

    # ------------------------------------------------------------- continue
    def _continue(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        task_id = intent.task_id
        if task_id is None:
            open_tasks = [t for t in self.repo.list_tasks() if t.status not in (TaskStatus.COMPLETED, TaskStatus.ABANDONED)]
            if not open_tasks:
                return self._respond(intent, at, "There is no open task to continue.")
            task_id = max(open_tasks, key=lambda t: t.updated_at).id
            intent.task_id = task_id
        plan = self.tasks.resume(task_id, at)
        claims = []
        for step in plan.steps:
            for chk in step.preconditions + step.postconditions:
                c = chk.condition
                claims.append(
                    GroundedClaim(
                        claim=f"{c.entity_id}.{c.attribute} {c.operator} {c.expected!r}: {chk.state.value} ({chk.reason})",
                        entity_id=c.entity_id,
                        attribute=c.attribute,
                        value=chk.observed_value,
                        status=chk.status,
                        supportable=chk.state.value == "SATISFIED",
                        evidence_refs=chk.evidence_refs,
                    )
                )
        answer = None
        summary = plan.message
        requests = list(plan.requested_observations)
        if plan.next_step is not None:
            verb = "Re-verify" if plan.next_step.status.value == "NEEDS_REVERIFICATION" else "Next"
            answer = f"{verb}: step {plan.next_step.step_order} — {plan.next_step.description}."
            if plan.recommended_checks:
                # Supported but unverified facts the step rests on: worth one look first.
                summary += " Before you start, confirm: " + " ".join(r.instruction for r in plan.recommended_checks)
                requests += plan.recommended_checks
        return self._respond(
            intent, at, summary, answer,
            claims=claims, changes=plan.world_changes, resume_plan=plan, requested_observations=requests,
        )

    # ---------------------------------------------------------------- safety
    def _match_step(self, intent: QueryIntent, at: datetime):
        """The unfinished step the question is about (by wording), else the next step."""
        from backend.app.providers.embedding import tokens

        wanted = set(tokens(intent.raw)) - {"safe", "ok", "okay", "start", "begin", "now", "go", "ahead", "can", "i", "it"}
        best, best_score = None, 0
        for task in self.repo.list_tasks():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.ABANDONED) or (intent.task_id and task.id != intent.task_id):
                continue
            for step in task.steps:
                if step.status.value in ("COMPLETED", "SKIPPED"):
                    continue
                score = len(wanted & set(tokens(step.description)))
                if score > best_score:
                    best, best_score = (task, step), score
        if best is not None:
            return best
        if re.search(r"\b(continue|next)\b", intent.raw.lower()):
            open_tasks = [t for t in self.repo.list_tasks() if t.status not in (TaskStatus.COMPLETED, TaskStatus.ABANDONED)]
            for task in sorted(open_tasks, key=lambda t: t.updated_at, reverse=True):
                plan = self.tasks.preview(task.id, at)
                if plan.next_step is not None:
                    return task, next(s for s in task.steps if s.id == plan.next_step.step_id)
        return None

    def _safety(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        """Readiness of a step at its risk bar. ORBIT vouches only for the facts it
        tracks and never authorises a physical action itself."""
        match = self._match_step(intent, at)
        if match is None:
            return self._respond(intent, at, "I couldn't tell which task step you mean.")
        task, step = match
        intent.task_id = task.id
        a = self.tasks.assess_step(task, step, at)
        checks = self.tasks.decision_critical(task, step, at)
        label = f"step {step.step_order} ({step.description}, {step.risk.value} risk)"
        claims = [
            GroundedClaim(
                claim=f"{c.condition.entity_id}.{c.condition.attribute} {c.condition.operator} {c.condition.expected!r}: "
                      f"{c.state.value} ({c.status.value})",
                entity_id=c.condition.entity_id,
                attribute=c.condition.attribute,
                value=c.observed_value,
                status=c.status,
                supportable=c.state.value == "SATISFIED",
                evidence_refs=c.evidence_refs,
            )
            for c in checks
        ]
        caveat = " ORBIT can only vouch for the conditions it tracks, and it does not authorise physical actions."
        if a.ready:
            facts = "; ".join(f"{c.condition.entity_id}.{c.condition.attribute} = {c.observed_value!r} ({c.status.value})" for c in checks)
            answer = f"All prerequisites ORBIT tracks for {label} are met" + (f": {facts}." if facts else ".")
            return self._respond(intent, at, answer + caveat, answer, claims=claims)
        requests = [self.tasks.conditions.request_for(c, [step.id]) for c in checks if c.state.value == "UNSUPPORTED"]
        summary = f"Not yet for {label}: " + "; ".join(a.blockers) + "." + caveat
        return self._respond(intent, at, summary, claims=claims, requested_observations=requests)

    # -------------------------------------------------------- what happened
    def _what_happened(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        since, until = intent.since, intent.until or at
        hits = self.retriever.recall(intent.raw, intent.entity_ids or None, since, until)
        events: Dict[str, object] = {}
        for eid in intent.entity_ids:  # structured recall
            for e in self.memory.timeline(eid, since, until):
                events[e.id] = e
        for hit in hits:  # semantic recall, re-validated against structured state
            if hit.kind == "event":
                e = next((x for x in self.repo.list_events() if x.id == hit.ref_id), None)
                if e is not None and (since is None or e.timestamp >= since) and e.timestamp <= until:
                    if not intent.entity_ids or e.entity_id in intent.entity_ids:
                        events[e.id] = e
        ordered = sorted(events.values(), key=lambda e: e.timestamp)
        claims = []
        for e in ordered:
            ev = self.repo.get_evidence(e.evidence_refs[0]) if e.evidence_refs else None
            status = grade(ev.source_type, ev.quality, ev.authority) if ev else EpistemicStatus.UNKNOWN
            claims.append(
                GroundedClaim(
                    claim=f"{hhmm(e.timestamp)}: {e.description}",
                    entity_id=e.entity_id,
                    status=status,
                    supportable=ev is not None and status in (EpistemicStatus.OBSERVED, EpistemicStatus.VERIFIED),
                    evidence_refs=e.evidence_refs,
                )
            )
        if not claims:
            return self._respond(intent, at, "I have no recorded events matching that.", retrieval=hits)
        text = " ".join(c.claim for c in claims)
        return self._respond(intent, at, text, text, claims=claims, retrieval=hits)

    # ------------------------------------------------------------------ why
    def _why(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        hyps = []
        for eid in intent.entity_ids:
            hyps += self.hypotheses.for_entity(eid)
        if not intent.entity_ids:
            ids = {h.ref_id for h in self.retriever.recall(intent.raw, k=5) if h.kind == "hypothesis"}
            hyps = [h for h in self.repo.list_hypotheses() if h.id in ids]
        supported = [h for h in hyps if h.status == HypothesisStatus.SUPPORTED]
        refuted = [h for h in hyps if h.status == HypothesisStatus.REFUTED]
        open_ = [h for h in hyps if h.status == HypothesisStatus.HYPOTHESIS]
        claims = [
            GroundedClaim(
                claim=f"{h.status.value}: {h.statement}",
                status=h.epistemic_status,
                supportable=h.status == HypothesisStatus.SUPPORTED,
                evidence_refs=h.evidence_refs,
            )
            for h in hyps
        ]
        notes = []
        if open_:
            notes.append("Open hypotheses (INFERRED, not established): " + "; ".join(h.statement for h in open_) + ".")
        if refuted:
            notes.append("Refuted by causal test: " + "; ".join(h.statement for h in refuted) + ".")
        if not hyps:
            notes.append("I have no causal evidence about that, and I won't infer a cause from events that merely co-occurred.")
        if not supported:
            notes.append("Establishing a cause needs a causal test (e.g. a controlled rollback or diagnostic).")
        answer = ("Supported by causal-test evidence: " + "; ".join(h.statement for h in supported) + ".") if supported else None
        if answer is None and not self.gate_evidence and open_:
            answer = f"Likely cause: {open_[0].statement}."  # ablation: hypothesis stated as fact
        return self._respond(intent, at, " ".join(([answer] if answer else []) + notes), answer, claims=claims)

    def _unknown(self, intent: QueryIntent, at: datetime) -> GroundedResponse:
        hits = self.retriever.recall(intent.raw, k=5)
        return self._respond(
            intent, at,
            "I couldn't map that to a question about the world state. Related memories are listed as recall aids only.",
            retrieval=hits,
        )
