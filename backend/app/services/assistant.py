"""ORBIT assistant (Phase 16): a conversational agent that acts on the user's behalf
inside ORBIT's boundaries.

What it may do by itself, because the user's words are the evidence:
- answer questions (through the grounded query agent and its evidence gate);
- record what the user states (USER_STATEMENT evidence — testimony, graded as such,
  never allowed to silently overwrite stronger evidence);
- report a finished or started task step, pause a task;
- say what is worth checking (active perception).

What it may only *prepare*: a physical action. It proposes the action, has its
prerequisites checked, and asks the user for an explicit "yes" (matched
deterministically, within a short window, for that exact action). A "yes" is recorded
as the user's authorisation. Nothing is actuated: the user does the action and says so;
ORBIT then checks the expected outcome against evidence observed afterwards (spec §16).

Every delegated act is recorded with provenance (conversation, utterance, actor), so
"what did you do for me?" is answerable from the audit trail. Conversation context
(focus, pending confirmation) is process-local and fails safe: after a restart a
pending "yes" simply expires (ADR-040).

Realtime (Phase 17): the assistant also listens to committed world changes and speaks
up unasked — an authorised action's outcome is verified the moment the camera sees
it; a change to something the user was just talking about, a new conflict, a task
step losing its support are announced as notices (ADR-041).
"""
import re
import threading
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.app.core.time import UTCDateTime
from backend.app.domain.models import ActionRequest, GroundedResponse, ObservationRequest, StateCondition, generate_id
from backend.app.core.realtime import EventBus, Message
from backend.app.domain.types import ActionStatus, ClaimDecision, ConditionState, PrincipalKind, QueryKind, Scope, SourceType, TaskStatus

from backend.app.providers.base import Vocabulary
from backend.app.providers.commands import (
    PRONOUNS,
    Command,
    spoken_numbers,
    CommandContext,
    CommandKind,
    CommandProvider,
    RuleBasedCommandProvider,
)
from backend.app.repositories.base import Repository
from backend.app.services.actions import ActionError, ActionSafetyService
from backend.app.services.active_perception import ActivePerceptionPlanner, DecisionAwarePolicy
from backend.app.services.query_agent import QueryAgent, hhmm
from backend.app.services.tasks import PostconditionViolatedError, TaskError, TaskService
from backend.app.services.world_state_engine import WorldStateEngine

ASSISTANT = "orbit-assistant"
CONFIRM_WINDOW = timedelta(minutes=2)


class Gesture(str, Enum):
    """How the avatar should move while it speaks the reply."""

    WAVE = "WAVE"
    NOD = "NOD"
    EXPLAIN = "EXPLAIN"
    THINK = "THINK"  # abstained: ORBIT needs a look before it can say
    SHRUG = "SHRUG"  # did not understand / nothing to report
    ASK = "ASK"  # waiting for the user's decision
    ALERT = "ALERT"  # blocked, conflicting, or refused


class DelegatedAction(BaseModel):
    at: UTCDateTime
    kind: str  # recorded_statement | completed_step | started_step | interrupted_task | proposed_action | authorized_action | declined_action | reported_performed | outcome_checked
    summary: str
    refs: List[str] = Field(default_factory=list)  # event / action / version ids


class PendingConfirmation(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("confirm"))
    action_id: str
    summary: str
    created_at: UTCDateTime
    expires_at: UTCDateTime


class AssistantTurn(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("turn"))
    conversation_id: str
    at: UTCDateTime
    user_text: str
    command: Command
    reply: str
    speech: str  # the reply as the avatar says it (names instead of ids, short)
    gesture: Gesture
    done: List[DelegatedAction] = Field(default_factory=list)
    pending: Optional[PendingConfirmation] = None
    response: Optional[GroundedResponse] = None
    action: Optional[ActionRequest] = None
    observation_requests: List[ObservationRequest] = Field(default_factory=list)
    focus: List[str] = Field(default_factory=list)


class AssistantNotice(BaseModel):
    """Something the assistant says unasked, because the world changed."""

    id: str = Field(default_factory=lambda: generate_id("notice"))
    conversation_id: str
    at: UTCDateTime
    kind: str  # outcome_verified | outcome_failed | focus_changed | conflict | task_changed
    reply: str
    speech: str
    gesture: Gesture
    refs: List[str] = Field(default_factory=list)
    action: Optional[ActionRequest] = None


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("conv"))
    user_id: str
    started_at: UTCDateTime
    turns: List[AssistantTurn] = Field(default_factory=list)
    focus: List[str] = Field(default_factory=list)
    pending: Optional[PendingConfirmation] = None
    awaiting_performance: Optional[str] = None  # action the user was authorised to do
    delegated: List[DelegatedAction] = Field(default_factory=list)
    notices: List[AssistantNotice] = Field(default_factory=list)


class ConversationNotFound(KeyError):
    pass


def _sentences(text: str, limit: int = 260) -> str:
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if out and len(out) + len(s) > limit:
            break
        out = f"{out} {s}".strip()
    return out or text[:limit]


class AssistantService:
    def __init__(
        self,
        repository: Repository,
        engine: WorldStateEngine,
        agent: QueryAgent,
        tasks: TaskService,
        actions: ActionSafetyService,
        perception: ActivePerceptionPlanner,
        parser: Optional[CommandProvider] = None,
        confirm_window: timedelta = CONFIRM_WINDOW,
        bus: Optional[EventBus] = None,
        notice_cooldown: timedelta = timedelta(seconds=20),
    ):
        self.repo = repository
        self.engine = engine
        self.agent = agent
        self.tasks = tasks
        self.actions = actions
        self.perception = perception
        self.parser = parser or RuleBasedCommandProvider()
        self.confirm_window = confirm_window
        self.conversations: Dict[str, Conversation] = {}
        self.bus = bus
        self.notice_cooldown = notice_cooldown
        self._lock = threading.RLock()  # turns (request threads) and notices (committing threads)
        self._local = threading.local()  # .speaking: conversation of the turn in this thread; .reacting
        self._last_notice: Dict[Tuple[str, str, str], datetime] = {}
        if bus is not None:
            bus.listen(self.on_message)

    # --------------------------------------------------------------- lifecycle
    def ensure_principal(self, at: datetime) -> None:
        """The assistant is an agent: it may observe, reason and recommend — never
        authorise or actuate (those stay with the human it talks to)."""
        if self.repo.get_principal(ASSISTANT) is None:
            self.actions.register_principal(ASSISTANT, PrincipalKind.AGENT, [Scope.OBSERVE, Scope.REASON, Scope.RECOMMEND], at)

    def start(self, user_id: str, at: datetime, conversation_id: Optional[str] = None) -> Conversation:
        self.ensure_principal(at)
        conv = Conversation(user_id=user_id, started_at=at)
        if conversation_id:
            if conversation_id in self.conversations:
                raise ValueError(f"conversation {conversation_id} already exists")
            conv.id = conversation_id
        self.conversations[conv.id] = conv
        return conv

    def get(self, conversation_id: str) -> Conversation:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            raise ConversationNotFound(conversation_id)
        return conv

    # -------------------------------------------------------------------- turn
    def say(self, conversation_id: str, text: str, at: datetime) -> AssistantTurn:
        with self._lock:
            self._local.speaking = conversation_id  # its own writes are answered in the reply, not as notices
            try:
                return self._say(conversation_id, text, at)
            finally:
                self._local.speaking = None

    def _say(self, conversation_id: str, text: str, at: datetime) -> AssistantTurn:
        conv = self.get(conversation_id)
        text = spoken_numbers(text)
        if conv.pending and at > conv.pending.expires_at:
            conv.pending = None  # an old question cannot be answered with a new "yes"
        vocabulary = self.agent.vocabulary()
        context = CommandContext(
            focus=conv.focus, pending_confirmation=conv.pending is not None,
            awaiting_performance=conv.awaiting_performance is not None, steps=self._steps(),
        )
        command = self.parser.parse(text, vocabulary, context)
        handler = {
            CommandKind.ASK: self._ask,
            CommandKind.TELL: self._tell,
            CommandKind.STEP_DONE: self._step_done,
            CommandKind.STEP_START: self._step_start,
            CommandKind.INTERRUPT: self._interrupt,
            CommandKind.ACT: self._act,
            CommandKind.CONFIRM: self._confirm,
            CommandKind.CANCEL: self._cancel,
            CommandKind.PERFORMED: self._performed,
            CommandKind.CHECKS: self._checks,
            CommandKind.RECAP: self._recap,
            CommandKind.HELP: self._help,
            CommandKind.HEARD: self._heard,
            CommandKind.GREET: self._greet,
        }[command.kind]
        turn = AssistantTurn(conversation_id=conv.id, at=at, user_text=text, command=command, reply="", speech="", gesture=Gesture.EXPLAIN)
        if command.ambiguous and not command.entity_id and command.kind in (CommandKind.TELL, CommandKind.ACT):
            self._clarify(turn, command)
        else:
            handler(conv, command, turn, at)
        if command.entity_id and command.entity_id not in turn.focus:
            turn.focus.insert(0, command.entity_id)
        conv.focus = (turn.focus + [e for e in conv.focus if e not in turn.focus])[:5]
        turn.focus = list(conv.focus)
        turn.pending = conv.pending
        turn.speech = self._speakable(turn.speech or _sentences(turn.reply), vocabulary)
        conv.delegated += turn.done
        conv.turns.append(turn)
        del conv.turns[:-200]
        return turn

    # ---------------------------------------------------------------- helpers
    def _steps(self) -> Dict[str, List[Tuple[str, int, str]]]:
        return {
            t.id: [(s.id, s.step_order, s.description) for s in t.steps]
            for t in self.repo.list_tasks() if t.status not in (TaskStatus.COMPLETED, TaskStatus.ABANDONED)
        }

    def _name(self, entity_id: Optional[str]) -> str:
        e = self.repo.get_entity(entity_id) if entity_id else None
        if e is None:
            return entity_id or "it"
        return e.name if e.name and e.name != e.type else f"the {e.type}" if self._unique_type(e.type) else e.id

    def _unique_type(self, etype: str) -> bool:
        return sum(1 for e in self.repo.list_entities() if e.type == etype and not e.merged_into) == 1

    def _speakable(self, text: str, vocabulary: Vocabulary) -> str:
        """Ids are for screens; the avatar says names."""
        def name(m: re.Match) -> str:
            eid = m.group(0)
            if eid not in vocabulary.entities:
                return eid
            e = self.repo.get_entity(eid)
            return (e.name if e.name and e.name != e.type else f"a {e.type}") if e else eid
        text = re.sub(r"\b[a-z]+_[a-z]+_[0-9a-f]{8,}\b", name, text)
        return text.replace("_", " ")

    @staticmethod
    def _value(attribute: Optional[str], value: Any) -> str:
        """Speakable predicate: "is at bench_3", "is open", "is powered on", "has model_number CP-200"."""
        if attribute == "location":
            return f"is at {value}"
        if isinstance(value, bool):
            return f"is {'' if value else 'not '}{attribute}"
        if attribute == "state":
            return f"is {value}"
        if attribute == "power":
            return f"is powered {value}"
        return f"has {attribute} {value}"

    def _spoken_changes(self, response: GroundedResponse) -> str:
        """Speak the world changes first (objects moved, appeared, contested), task
        bookkeeping last; the full list stays on screen."""
        rank = {"EVIDENCE_CONFLICT": 0, "OBJECT_MOVED": 1, "OBJECT_STATE_CHANGED": 1, "PROCEDURE_REVISION_DETECTED": 1,
                "OBJECT_ADDED": 2, "RELATION_CHANGED": 3, "IDENTITY_MERGED": 3}

        def importance(c) -> int:
            kind = c.change_type.value
            if kind == "OBJECT_REMOVED_OR_UNOBSERVED":  # a confirmed absence matters; "not seen again" least
                return 2 if c.absence and c.absence.value == "CONFIRMED_ABSENT" else 9
            return rank.get(kind, 5)

        world = sorted((c for c in response.changes if c.change_type.value != "TASK_PROGRESS_CHANGED"), key=importance)
        tasks = [c for c in response.changes if c.change_type.value == "TASK_PROGRESS_CHANGED"]
        n = len(response.changes)
        head = f"{n} change{'s' if n != 1 else ''}."
        said = " ".join(self.agent.phrase_change(c) for c in world[:2])
        more = len(world) - 2
        tail = f" And {more} more on screen." if more > 0 else ""
        blocked = sum(1 for c in tasks if str(c.after).upper() == "BLOCKED")
        task = f" {blocked} task step{'s are' if blocked != 1 else ' is'} now blocked." if blocked else ""
        return f"{head} {said}{tail}{task}".replace("  ", " ").strip()

    def _clarify(self, turn: AssistantTurn, command: Command) -> None:
        mention, ids = next(iter(command.ambiguous.items()))
        turn.reply = f"Which {mention} do you mean: {', '.join(ids)}?"
        turn.gesture = Gesture.ASK

    def _record(self, turn: AssistantTurn, at: datetime, kind: str, summary: str, refs: List[str]) -> None:
        turn.done.append(DelegatedAction(at=at, kind=kind, summary=summary, refs=refs))

    def _provenance(self, conv: Conversation, text: str) -> Dict[str, Any]:
        return {"actor": conv.user_id, "via": ASSISTANT, "conversation": conv.id, "utterance": text}

    def _step(self, command: Command) -> Optional[Tuple[str, str]]:
        """Resolve the command's step reference to (task id, step id)."""
        if command.step_ref is None:
            return None
        for task in self.repo.list_tasks():
            if command.task_id and task.id != command.task_id:
                continue
            for s in task.steps:
                if s.id == command.step_ref or (command.step_ref.isdigit() and s.step_order == int(command.step_ref)):
                    return task.id, s.id
        return None

    def _current_task(self, command: Command):
        if command.task_id:
            return self.repo.get_task(command.task_id)
        open_tasks = [t for t in self.repo.list_tasks() if t.status not in (TaskStatus.COMPLETED, TaskStatus.ABANDONED)]
        return max(open_tasks, key=lambda t: t.updated_at) if open_tasks else None

    # ---------------------------------------------------------------- handlers
    def _ask(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        query = command.raw
        if command.used_context and command.entity_id:
            query = PRONOUNS.sub(command.entity_id, query, count=1)
        response = self.agent.answer(query, at)
        turn.response = response
        turn.focus = list(response.intent.entity_ids)
        turn.observation_requests = list(response.requested_observations)
        if response.intent.kind == QueryKind.UNKNOWN and conv.pending is not None:
            turn.reply = f"I'm still waiting for your decision: {conv.pending.summary}. Say yes or no."
            turn.gesture = Gesture.ASK
            turn.response = None
            return
        if response.intent.kind == QueryKind.UNKNOWN:
            turn.response = None  # "related memories" are recall aids, not an answer worth reading out
            turn.reply = ("I didn't catch that as a question or a request. You can ask where something is, tell me where "
                          "you put something, tell me you finished a step, or ask me to get an action approved.")
            turn.gesture = Gesture.SHRUG
            return
        turn.reply = response.summary
        if response.answer and response.answer not in response.summary:
            turn.reply = f"{response.answer} {response.summary}"
        if response.intent.kind == QueryKind.WHAT_CHANGED and response.changes:
            turn.speech = self._spoken_changes(response)
        turn.gesture = Gesture.EXPLAIN if not response.abstained else (Gesture.THINK if response.requested_observation else Gesture.SHRUG)
        if response.conflicts:
            turn.gesture = Gesture.ALERT

    def _tell(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        if command.unknown_place:
            turn.reply = (f"I don't know a place called '{command.unknown_place}' yet, so I haven't recorded that. "
                          "Add it as a place in ORBIT (or draw it as a camera region) and tell me again.")
            turn.gesture = Gesture.SHRUG
            return
        if not command.entity_id or not command.attribute:
            turn.reply = "I couldn't tell which object you mean, so I haven't recorded anything."
            turn.gesture = Gesture.SHRUG
            return
        name = self._name(command.entity_id)
        before = self.engine.claims.assess_attribute(command.entity_id, command.attribute, at)
        refs: List[str] = []
        if command.intervention:  # spec §10: a known intervention invalidates the old belief first
            refs += [e.id for e in self.engine.record_intervention(
                command.entity_id, at, f"{conv.user_id} reported: {command.raw}", [command.attribute],
                source=f"user:{conv.user_id}", provenance=self._provenance(conv, command.raw))]
        result = self.engine.assert_claim(
            command.entity_id, command.attribute, command.value, source=f"user:{conv.user_id}", timestamp=at,
            source_type=SourceType.USER_STATEMENT, source_reference=f"assistant:{conv.id}",
            provenance=self._provenance(conv, command.raw),
        )
        after = self.engine.claims.assess_attribute(command.entity_id, command.attribute, at)
        fact = f"{name} {self._value(command.attribute, command.value)}"
        decision = result.decision
        if decision == ClaimDecision.NEW and command.intervention:
            was = f" (before: {before.last_known_value})" if before.last_known_value not in (None, command.value) else ""
            turn.reply, turn.gesture = f"Got it: {fact}{was}. Recorded as your change ({after.status.value} until the camera sees it).", Gesture.NOD
        elif decision == ClaimDecision.NEW:
            turn.reply, turn.gesture = f"Noted: {fact}, on your word ({after.status.value}).", Gesture.NOD
        elif decision == ClaimDecision.CORROBORATE:
            turn.reply, turn.gesture = f"That matches what I had: {fact}. I've added your word as supporting evidence ({after.status.value}).", Gesture.NOD
        elif decision == ClaimDecision.SUPERSEDE:
            was = f" (before: {before.last_known_value})" if before.last_known_value not in (None, command.value) else ""
            how = "your change" if command.intervention else "your statement"
            turn.reply, turn.gesture = f"Updated: {fact}{was}. Recorded as {how} ({after.status.value} until the camera sees it).", Gesture.NOD
        elif decision == ClaimDecision.RESOLVE:
            turn.reply, turn.gesture = f"Thanks, that settles the open conflict: {fact}.", Gesture.NOD
        elif decision in (ClaimDecision.CONTRADICT, ClaimDecision.CONFLICT_UPDATE):
            turn.reply = (f"That disagrees with the evidence I have ({result.reason}). I've recorded your statement and "
                          f"flagged a conflict instead of overwriting either side. A fresh look would settle it.")
            turn.gesture = Gesture.ALERT
            turn.observation_requests = [self.tasks.conditions.request_for(self.tasks.conditions.check(
                StateCondition(entity_id=command.entity_id, attribute=command.attribute, expected=command.value, operator="exists"), at))]
        else:  # UNCONFIRMED
            turn.reply = f"I've recorded what you said, but it isn't enough to change what I believe: {result.reason}."
            turn.gesture = Gesture.THINK
        refs += [e.id for e in result.events] + ([result.version_id] if result.version_id else [])
        what = "your change" if command.intervention else "your statement"
        self._record(turn, at, "recorded_statement", f"Recorded {what}: {fact} ({decision.value})", refs)

    def _step_done(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        resolved = self._step(command)
        if resolved is None:
            turn.reply, turn.gesture = "I couldn't find that step in an open task.", Gesture.SHRUG
            return
        task_id, step_id = resolved
        try:
            task = self.tasks.complete_step(
                task_id, step_id, at, source=f"user:{conv.user_id}", source_type=SourceType.USER_STATEMENT,
                actor=conv.user_id, note=f"reported to the ORBIT assistant: {command.raw}",
            )
        except PostconditionViolatedError as exc:
            turn.reply, turn.gesture = f"I didn't record it as done, because the evidence says otherwise: {exc}.", Gesture.ALERT
            return
        except TaskError as exc:
            turn.reply, turn.gesture = f"I can't mark that done yet: {exc}.", Gesture.ALERT
            return
        step = next(s for s in task.steps if s.id == step_id)
        reply = f"Recorded step {step.step_order} ({step.description}) as done on your word ({step.completion_status.value})."
        if step.completion_status.value != "VERIFIED" and step.postconditions:
            reply += " It becomes verified once its result is observed."
        if task.status == TaskStatus.COMPLETED:
            reply += f" That completes the task '{task.goal}'."
        else:
            plan = self.tasks.preview(task_id, at)
            if plan.next_step is not None:
                reply += f" Next: step {plan.next_step.step_order}, {plan.next_step.description}."
            elif plan.requested_observations:
                turn.observation_requests = list(plan.requested_observations)
                reply += f" Before the next step: {plan.requested_observations[0].instruction}"
        turn.reply, turn.gesture = reply, Gesture.NOD
        self._record(turn, at, "completed_step", f"Marked {task_id} step {step.step_order} done", step.evidence_refs[-1:])

    def _step_start(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        resolved = self._step(command)
        if resolved is None:
            turn.reply, turn.gesture = "I couldn't find that step in an open task.", Gesture.SHRUG
            return
        task_id, step_id = resolved
        try:
            task = self.tasks.start_step(task_id, step_id, at, actor=conv.user_id)
        except TaskError as exc:
            plan = self.tasks.preview(task_id, at)
            turn.observation_requests = [r for r in plan.requested_observations if not r.for_steps or step_id in r.for_steps]
            ask = f" {turn.observation_requests[0].instruction}" if turn.observation_requests else ""
            turn.reply, turn.gesture = f"Not yet: {exc}.{ask}", Gesture.ALERT
            return
        step = next(s for s in task.steps if s.id == step_id)
        turn.reply, turn.gesture = f"Okay, step {step.step_order} ({step.description}) is in progress. Its prerequisites are met.", Gesture.NOD
        self._record(turn, at, "started_step", f"Started {task_id} step {step.step_order}", [])

    def _interrupt(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        task = self._current_task(command)
        if task is None:
            turn.reply, turn.gesture = "There is no open task to pause.", Gesture.SHRUG
            return
        self.tasks.interrupt_task(task.id, at, reason=f"said to the assistant: {command.raw}", actor=conv.user_id)
        turn.reply = (f"Paused '{task.goal}' at {hhmm(at)}. When you're back, say 'continue' and I'll check what "
                      "changed before suggesting the next step.")
        turn.gesture = Gesture.NOD
        self._record(turn, at, "interrupted_task", f"Paused task {task.id}", [])

    def _act(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        resolved = self._step(command)
        task_id, step_id = resolved if resolved else (None, None)
        targets = [command.entity_id] if command.entity_id else []
        description = f"{command.verb or 'act on'} {self._name(command.entity_id)}" if command.entity_id else None
        if description is None and step_id:
            step = next(s for s in self.repo.get_task(task_id).steps if s.id == step_id)
            description = step.description.lower()
        expected = []
        if command.entity_id and command.attribute and command.value is not None:
            goal = StateCondition(entity_id=command.entity_id, attribute=command.attribute, expected=command.value,
                                  description=f"{command.entity_id}.{command.attribute} == {command.value!r}")
            already = self.tasks.conditions.check(goal, at)
            if already.state == ConditionState.SATISFIED:
                turn.reply = (f"No need: {self._name(command.entity_id)} already {self._value(command.attribute, command.value)} "
                              f"({already.status.value}, {already.reason}).")
                turn.gesture = Gesture.EXPLAIN
                return
            expected = [goal]
        try:
            request = self.actions.propose(
                description, ASSISTANT, at, target_entity_ids=targets, expected_outcome=expected,
                prerequisites=None if step_id else [], task_id=task_id, step_id=step_id,
            )
        except ActionError as exc:
            turn.reply, turn.gesture = f"I can't prepare that: {exc}.", Gesture.ALERT
            return
        turn.action = request
        self._record(turn, at, "proposed_action", f"Prepared '{description}' for authorization", [request.id])
        if request.status == ActionStatus.PREREQUISITES_FAILED:
            failing = [c for c in request.prerequisite_checks if c.state != ConditionState.SATISFIED]
            why = "; ".join(f"{c.condition.entity_id}.{c.condition.attribute}: {c.reason}" for c in failing)
            turn.observation_requests = list(request.requested_observations)
            ask = f" {turn.observation_requests[0].instruction}" if turn.observation_requests else ""
            waiver = " (A risk shortfall can only be waived, with a reason, in the Inspector.)" if any(c.risk_shortfall for c in failing) else ""
            turn.reply = f"I've prepared '{description}', but it can't be authorized yet: {why}.{ask}{waiver}"
            turn.gesture = Gesture.ALERT
            return
        principal = self.repo.get_principal(conv.user_id)
        if principal is None or Scope.AUTHORIZE not in principal.scopes or principal.kind != PrincipalKind.HUMAN:
            turn.reply = (f"I've prepared '{description}' ({request.risk.value} risk), but {conv.user_id} isn't registered to "
                          "authorize actions, so someone who is will need to approve it.")
            turn.gesture = Gesture.ALERT
            return
        met = [f"{c.condition.entity_id}.{c.condition.attribute} = {c.observed_value!r} ({c.status.value})" for c in request.prerequisite_checks]
        facts = f"Prerequisites I track are met: {'; '.join(met)}." if met else "I track no prerequisites for it."
        summary = f"Authorize '{description}' ({request.risk.value} risk, action {request.id})"
        conv.pending = PendingConfirmation(action_id=request.id, summary=summary, created_at=at, expires_at=at + self.confirm_window)
        turn.reply = (f"I've prepared '{description}' ({request.risk.value} risk). {facts} I can't do it physically; you would. "
                      f"Shall I record your authorization? Say yes or no.")
        turn.gesture = Gesture.ASK

    def _confirm(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        pending = conv.pending
        conv.pending = None
        if pending is None:
            turn.reply, turn.gesture = "There's nothing waiting for your approval.", Gesture.SHRUG
            return
        try:
            request = self.actions.authorize(pending.action_id, conv.user_id, True, at,
                                             reason=f"confirmed in conversation {conv.id}: '{command.raw}'")
        except ActionError as exc:
            turn.reply, turn.gesture = f"I couldn't record the authorization: {exc}.", Gesture.ALERT
            return
        turn.action = request
        if request.status != ActionStatus.AUTHORIZED:
            turn.reply = "The evidence changed while we were talking: its prerequisites are no longer met, so it is not authorized."
            turn.observation_requests = list(request.requested_observations)
            turn.gesture = Gesture.ALERT
            return
        conv.awaiting_performance = request.id
        turn.reply = (f"Authorized under your name. Please {request.action} yourself, then tell me 'done' and I'll check "
                      "the result against what the camera sees.")
        turn.gesture = Gesture.NOD
        self._record(turn, at, "authorized_action", f"Recorded your authorization of '{request.action}'", [request.id])

    def _cancel(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        pending = conv.pending
        conv.pending = None
        if pending is None:
            turn.reply, turn.gesture = "Okay.", Gesture.NOD
            return
        request = self.actions.authorize(pending.action_id, conv.user_id, False, at, reason=f"declined in conversation {conv.id}")
        turn.action = request
        turn.reply, turn.gesture = f"Okay, I won't. Recorded '{request.action}' as declined.", Gesture.NOD
        self._record(turn, at, "declined_action", f"Recorded that you declined '{request.action}'", [request.id])

    def _performed(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        action_id = conv.awaiting_performance
        request = self.repo.get_action(action_id) if action_id else None
        if request is None:
            turn.reply, turn.gesture = "There's no authorized action I'm waiting on.", Gesture.SHRUG
            return
        try:
            if request.status == ActionStatus.AUTHORIZED:
                self.actions.report_performed(action_id, conv.user_id, at, notes=f"reported to the ORBIT assistant: {command.raw}")
                self._record(turn, at, "reported_performed", f"Recorded that you performed '{request.action}'", [action_id])
            request = self.actions.verify_outcome(action_id, at)
        except ActionError as exc:
            turn.reply, turn.gesture = f"I couldn't record that: {exc}.", Gesture.ALERT
            return
        turn.action = request
        self._record(turn, at, "outcome_checked", f"Checked the outcome of '{request.action}': {request.status.value}", [action_id])
        if request.status == ActionStatus.OUTCOME_VERIFIED:
            conv.awaiting_performance = None
            turn.reply, turn.gesture = f"Confirmed: the result of '{request.action}' is observed. Recorded as verified.", Gesture.NOD
        elif request.status == ActionStatus.OUTCOME_FAILED:
            conv.awaiting_performance = None
            why = "; ".join(c.reason for c in request.outcome_checks if c.state == ConditionState.VIOLATED)
            turn.reply, turn.gesture = f"The evidence says '{request.action}' did not have the expected result: {why}.", Gesture.ALERT
        elif not request.expected_outcome:
            conv.awaiting_performance = None
            turn.reply, turn.gesture = f"Thanks, recorded that you did '{request.action}'. There was no outcome for me to check.", Gesture.NOD
        else:
            turn.observation_requests = list(request.requested_observations)
            ask = turn.observation_requests[0].instruction if turn.observation_requests else "Show it to the camera."
            live = " I'll confirm it the moment the camera sees the result." if self.bus is not None else " Then say 'check again'."
            turn.reply = f"Thanks, recorded. I can't confirm the result yet: {ask}{live}"
            turn.gesture = Gesture.THINK

    def _checks(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        plan = self.perception.plan(at, DecisionAwarePolicy(), k=3)
        if not plan.actions:
            turn.reply, turn.gesture = "Nothing needs checking right now: everything I rely on is supported.", Gesture.NOD
            return
        tips = " ".join(f"{i + 1}. {a.instruction}" for i, a in enumerate(plan.actions))
        turn.reply, turn.gesture = f"Worth checking, most useful first: {tips}", Gesture.EXPLAIN
        turn.speech = f"The most useful thing to check: {plan.actions[0].instruction}"

    def _recap(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        if not conv.delegated:
            turn.reply, turn.gesture = "I haven't done anything on your behalf in this conversation yet.", Gesture.SHRUG
            return
        items = "; ".join(f"{d.at:%H:%M} {d.summary}" for d in conv.delegated[-6:])
        turn.reply, turn.gesture = f"On your behalf I have: {items}.", Gesture.EXPLAIN
        turn.speech = f"I've done {len(conv.delegated)} thing{'s' if len(conv.delegated) != 1 else ''} for you. They're listed on screen."

    def _help(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        turn.reply = ("I keep track of your workspace and act for you inside ORBIT. Ask 'where is the microscope?' or 'what changed?'; "
                      "tell me 'I put the notebook on bench 4' or 'I finished step 7'; say 'pause the task' or 'continue'; "
                      "ask 'what should I check?'. For physical actions like 'open the valve' I check the prerequisites and ask "
                      "for your yes. You do the action, I verify the result.")
        turn.speech = "Ask me where things are or what changed, tell me what you did, or ask me to prepare an action for your approval."
        turn.gesture = Gesture.EXPLAIN

    def _heard(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        from backend.app.providers.commands import FILLER, normalise
        if FILLER.match(normalise(command.raw)):
            turn.reply, turn.gesture = "I'm listening. Take your time.", Gesture.NOD
        else:
            turn.reply = "Yes, I can hear you clearly. Ask me about your workspace, or tell me what you did."
            turn.gesture = Gesture.NOD

    def _greet(self, conv: Conversation, command: Command, turn: AssistantTurn, at: datetime) -> None:
        turn.reply = f"Hi {conv.user_id}! I'm keeping track of your workspace. What do you need?"
        turn.gesture = Gesture.WAVE

    # ---------------------------------------------------------------- realtime
    _FOCUS_EVENTS = {"OBJECT_MOVED", "OBJECT_STATE_CHANGED", "OBJECT_REMOVED_OR_UNOBSERVED", "IDENTITY_AMBIGUOUS"}
    _ALERT_EVENTS = {"EVIDENCE_CONFLICT", "IDENTITY_CONFLICT"}

    def on_message(self, msg: Message) -> None:
        """Bus listener, called in the committing thread after commit."""
        if getattr(self._local, "reacting", False) or msg["topic"] not in ("world", "observation"):
            return
        data = msg["data"]
        entities = ([data["entity_id"]] if data.get("entity_id") else []) if msg["topic"] == "world" else list(data.get("entities") or [])
        at = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))  # Python 3.9 rejects "Z"
        with self._lock:
            self._local.reacting = True
            try:
                for conv in list(self.conversations.values()):
                    if conv.id == getattr(self._local, "speaking", None):
                        continue
                    self._react(conv, msg, data, entities, at)
            finally:
                self._local.reacting = False

    def _react(self, conv: Conversation, msg: Message, data: Dict[str, Any], entities: List[str], at: datetime) -> None:
        # 1. An authorised action the user performed: verify as soon as evidence arrives.
        if conv.awaiting_performance and entities:
            request = self.repo.get_action(conv.awaiting_performance)
            if request and request.status in (ActionStatus.PERFORMED, ActionStatus.OUTCOME_UNVERIFIED) \
                    and set(entities) & set(request.target_entity_ids):
                try:
                    request = self.actions.verify_outcome(request.id, at)
                except ActionError:
                    request = None
                if request and request.status == ActionStatus.OUTCOME_VERIFIED:
                    conv.awaiting_performance = None
                    seen = "; ".join(f"{c.condition.entity_id}.{c.condition.attribute} = {c.observed_value!r}" for c in request.outcome_checks)
                    self._notice(conv, at, "outcome_verified", f"Verified: the result of '{request.action}' is now observed ({seen}).",
                                 Gesture.NOD, [request.id], request, key=request.id)
                    return
                if request and request.status == ActionStatus.OUTCOME_FAILED:
                    conv.awaiting_performance = None
                    why = "; ".join(c.reason for c in request.outcome_checks if c.state == ConditionState.VIOLATED)
                    self._notice(conv, at, "outcome_failed", f"Careful: '{request.action}' did not have the expected result. {why}.",
                                 Gesture.ALERT, [request.id], request, key=request.id)
                    return
        if msg["topic"] != "world":
            return
        kind = data["event_type"]
        entity = data.get("entity_id")
        # 2. Something the user was just talking about changed, or became contested.
        if entity and entity in conv.focus[:3] and (kind in self._FOCUS_EVENTS or kind in self._ALERT_EVENTS):
            alert = kind in self._ALERT_EVENTS or kind == "OBJECT_REMOVED_OR_UNOBSERVED"
            what = data.get("description") or kind.lower().replace("_", " ")
            self._notice(conv, at, "conflict" if kind in self._ALERT_EVENTS else "focus_changed",
                         f"Update on {self._name(entity)}: {what}.", Gesture.ALERT if alert else Gesture.EXPLAIN,
                         [data["id"]], key=f"{entity}:{kind}")
            return
        # 3. A task step lost its support while the user is working.
        if kind == "TASK_PROGRESS_CHANGED" and data.get("task_id"):
            after = (data.get("after_state") or {}).get("status")
            if after in ("BLOCKED", "NEEDS_REVERIFICATION", "INVALIDATED"):
                self._notice(conv, at, "task_changed", f"Heads up: {data.get('description') or 'a task step changed'}.",
                             Gesture.ALERT, [data["id"]], key=f"{data['task_id']}:{after}")

    def _notice(self, conv: Conversation, at: datetime, kind: str, reply: str, gesture: Gesture, refs: List[str],
                action: Optional[ActionRequest] = None, key: str = "") -> None:
        k = (conv.id, kind, key)
        last = self._last_notice.get(k)
        if last is not None and at - last < self.notice_cooldown:
            return  # the same news again within seconds is noise
        self._last_notice[k] = at
        notice = AssistantNotice(conversation_id=conv.id, at=at, kind=kind, reply=reply,
                                 speech=self._speakable(_sentences(reply), self.agent.vocabulary()),
                                 gesture=gesture, refs=refs, action=action)
        if kind.startswith("outcome"):
            notice_act = DelegatedAction(at=at, kind="outcome_checked", summary=f"Checked the outcome of '{action.action}': {action.status.value}", refs=refs)
            conv.delegated.append(notice_act)
        conv.notices.append(notice)
        del conv.notices[:-100]
        if self.bus is not None:
            self.bus.publish("assistant", "NOTICE", notice.model_dump(mode="json"), conversation=conv.id, at=at)
