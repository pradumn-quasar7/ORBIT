"""Conversational commands (Phase 16): utterance → one structured, checkable request.

A command provider only *interprets* language against the vocabulary of structured
state; it never executes anything and never decides what is true. The assistant
service validates every command against the world model and the action-safety
boundary before doing anything (spec §14).

Two implementations:
- ``RuleBasedCommandProvider`` — deterministic, offline, the default;
- ``AnthropicCommandProvider`` — optional LLM parsing via the Claude Messages API
  (tool use), enabled explicitly; its output is validated against the vocabulary and
  falls back to the rule-based parse when invalid.

Consent words ("yes", "no") are always matched deterministically: authorisation must
never depend on a model's reading of the user (ADR-040).
"""
import json
import re
import urllib.request
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.app.providers.base import Vocabulary
from backend.app.providers.reasoning import _find, _overlaps, normalise


class CommandKind(str, Enum):
    ASK = "ASK"  # a question → grounded query agent
    TELL = "TELL"  # the user states a fact → recorded as USER_STATEMENT evidence
    STEP_DONE = "STEP_DONE"  # the user reports finishing a task step
    STEP_START = "STEP_START"  # the user starts a task step (readiness is checked)
    INTERRUPT = "INTERRUPT"  # pause the current task
    ACT = "ACT"  # a physical action → proposed, prerequisites checked, needs a human "yes"
    PERFORMED = "PERFORMED"  # the user reports having done the authorised action
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    CHECKS = "CHECKS"  # "what should I check?" → active-perception plan
    RECAP = "RECAP"  # "what did you do for me?"
    HELP = "HELP"
    GREET = "GREET"


class Command(BaseModel):
    kind: CommandKind
    raw: str
    entity_id: Optional[str] = None
    attribute: Optional[str] = None
    value: Any = None
    anchor_id: Optional[str] = None
    task_id: Optional[str] = None
    step_ref: Optional[str] = None  # a step id, or its order number as text
    verb: Optional[str] = None  # ACT: the action verb ("open", "turn off", …)
    ambiguous: Dict[str, List[str]] = Field(default_factory=dict)
    unknown_place: Optional[str] = None  # TELL: a place word ORBIT has no anchor for
    # TELL: the user reports something they *did* ("I moved…", "I opened…"): a known
    # intervention, which invalidates the old belief before the new value is recorded.
    intervention: bool = False
    used_context: bool = False  # "it"/"that" resolved from the conversation's focus
    parser: str = "rule-based-commands-v1"


class CommandContext(BaseModel):
    """What the conversation can lend the parser: recent focus and open business."""

    focus: List[str] = Field(default_factory=list)  # most recent entity first
    pending_confirmation: bool = False
    awaiting_performance: bool = False
    steps: Dict[str, List[Tuple[str, int, str]]] = Field(default_factory=dict)  # task -> (step id, order, description)


class CommandProvider(ABC):
    name = "command-provider"

    @abstractmethod
    def parse(self, text: str, vocabulary: Vocabulary, context: CommandContext) -> Command:
        ...


# --------------------------------------------------------------------- lexicons
YES = re.compile(r"^(yes|yeah|yep|yup|sure|ok|okay|confirm(ed)?|approved?|i approve|authori[sz]e( it)?|do it|go ahead|please do)\b")
NO = re.compile(r"^(no|nope|nah|cancel|don t|do not|deny|denied|stop|never ?mind|abort)\b")
QUESTION_START = re.compile(r"^(where|what|whats|which|who|whos|when|why|how|is|are|was|were|does|do|did|has|have|can i|could i|should i|tell me|show me|any)\b")
POLITE = re.compile(r"^(?:(?:hey |ok |okay )?orbit,? )?(?:please |kindly |can you |could you |would you |will you |go ahead and |i want you to |i d like you to |i need you to )*")
GREETING = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening)|namaste)\b(?! orbit,? (open|close|turn|switch))")
HELP = re.compile(r"\b(help|what can you do|how do i use you|what do you do)\b")
RECAP = re.compile(r"\bwhat (did|have) you (do|done)\b|\bwhat have you done\b|\byour actions\b")
CHECKS = re.compile(r"\bwhat (should|do) i (check|look at|verify|inspect)\b|\bwhat needs (checking|verifying|a look)\b|\bwhat should i look at\b")
INTERRUPT = re.compile(r"\b(pause|interrupt|hold|suspend) (the )?(task|work|job)\b|\b(i m|im|i am) (taking a break|stopping|going for (lunch|a break))\b|\btake a break\b")
PERFORMED = re.compile(r"^(i )?(did it|done it|have done it|ve done it|ve done that|did that|it s done|its done|done|finished it|all done)\b"
                       r"|\bcheck (it |that )?again\b|\bcheck the (outcome|result)\b|\bdid it work\b")
STEP_DONE = re.compile(r"\b(?:i )?(?:finished|completed|done with|did|have done|ve done|ve finished|ve completed)\b|\bmark\b.*\b(done|complete|completed)\b|\b(is|are) (done|complete|completed|finished)\b")
STEP_START = re.compile(r"^(?:i m |im |i am )?(?:start|starting|begin|beginning)\b")
STEP_NUMBER = re.compile(r"\bstep (\d{1,3})\b")
DID_VERBS: List[Tuple[re.Pattern, str, Any]] = [  # "I opened the valve" → (attribute, value)
    (re.compile(r"^(i|we) (just |have |ve )?(opened)\b"), "state", "open"),
    (re.compile(r"^(i|we) (just |have |ve )?(closed|shut)\b"), "state", "closed"),
    (re.compile(r"^(i|we) (just |have |ve )?(locked)\b"), "state", "locked"),
    (re.compile(r"^(i|we) (just |have |ve )?(unlocked)\b"), "state", "unlocked"),
    (re.compile(r"^(i|we) (just |have |ve )?(turned|switched|powered) (\S+ ){0,2}on\b"), "power", "on"),
    (re.compile(r"^(i|we) (just |have |ve )?(turned|switched|powered) (\S+ ){0,2}off\b"), "power", "off"),
]
PLACE_VERBS = re.compile(r"\b(put|placed|moved|left|set|dropped|kept|keep|stored|returned|brought|carried|took)\b")
LOCATION_STATEMENT = re.compile(r"\b(is|are) (now |still )?(on|in|at|inside|on top of)\b")
PREPOSITION = re.compile(r"\b(?:on|in|at|to|onto|into|inside|on top of)\s+(?:the |my )?([a-z0-9 ]{1,40})$")

# Imperative action verbs → (canonical verb, expected outcome attribute, value).
ACTION_VERBS: List[Tuple[re.Pattern, str, Optional[str], Any]] = [
    (re.compile(r"^(turn|switch|power) on\b|^(turn|switch|power) \S+( \S+)? on$"), "turn on", "power", "on"),
    (re.compile(r"^(turn|switch|power) off\b|^(turn|switch|power) \S+( \S+)? off$"), "turn off", "power", "off"),
    (re.compile(r"^open\b"), "open", "state", "open"),
    (re.compile(r"^(close|shut)\b"), "close", "state", "closed"),
    (re.compile(r"^unlock\b"), "unlock", "state", "unlocked"),
    (re.compile(r"^lock\b"), "lock", "state", "locked"),
    (re.compile(r"^install\b"), "install", "installed", True),
    (re.compile(r"^(move|bring|take|carry|put)\b"), "move", "location", None),
    (re.compile(r"^(remove|uninstall|detach|disconnect)\b"), "remove", None, None),
    (re.compile(r"^(replace|cut|tighten|loosen|start|stop|restart|calibrate|clean|connect|attach|fill|drain|flush|reset)\b"), None, None, None),
]

# Attribute values that identify their attribute when the user omits it.
VALUE_ATTRIBUTE = {
    "open": "state", "opened": "state", "closed": "state", "shut": "state", "locked": "state", "unlocked": "state",
    "on": "power", "off": "power",
    "installed": "installed", "uninstalled": "installed",
}
VALUE_CANONICAL = {"opened": "open", "shut": "closed", "uninstalled": False, "installed": True}
PRONOUNS = re.compile(r"\b(it|that|this one|that one|them)\b")


def _mentions(text: str, vocabulary: Vocabulary) -> Tuple[List[Tuple[str, Tuple[int, int]]], List[Tuple[str, Tuple[int, int]]], Dict[str, List[str]]]:
    """Entity and anchor mentions with their spans; type words resolve if unambiguous."""
    taken: List[Tuple[int, int]] = []
    specific = []
    for eid, forms in vocabulary.entities.items():
        for form in forms:
            for span in _find(form, text):
                specific.append((span[1] - span[0], eid, span))
    entities: List[Tuple[str, Tuple[int, int]]] = []
    for _, eid, span in sorted(specific, key=lambda x: -x[0]):
        if not _overlaps(span, taken):
            taken.append(span)
            if eid not in [e for e, _ in entities]:
                entities.append((eid, span))
    anchors: List[Tuple[str, Tuple[int, int]]] = []
    anchor_hits = []
    for aid, forms in vocabulary.anchors.items():
        for form in forms:
            for span in _find(form, text):
                anchor_hits.append((span[1] - span[0], aid, span))
    for _, aid, span in sorted(anchor_hits, key=lambda x: -x[0]):
        if not _overlaps(span, taken):
            taken.append(span)
            anchors.append((aid, span))
    ambiguous: Dict[str, List[str]] = {}
    by_type: Dict[str, List[str]] = {}
    for eid, etype in vocabulary.entity_types.items():
        by_type.setdefault(etype, []).append(eid)
    for etype, ids in sorted(by_type.items()):
        for span in _find(etype, text):
            if _overlaps(span, taken) or any(e in ids for e, _ in entities):
                continue
            taken.append(span)
            if len(ids) == 1:
                entities.append((ids[0], span))
            else:
                ambiguous[etype] = sorted(ids)
    entities.sort(key=lambda x: x[1][0])
    anchors.sort(key=lambda x: x[1][0])
    return entities, anchors, ambiguous


def match_step(text: str, context: CommandContext, task_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(task id, step ref) for "step 7" or wording that matches one step best."""
    from backend.app.providers.embedding import tokens

    tasks = {task_id: context.steps[task_id]} if task_id in context.steps else context.steps
    number = STEP_NUMBER.search(text)
    if number:
        order = int(number.group(1))
        hits = [(t, sid) for t, steps in tasks.items() for sid, o, _ in steps if o == order]
        if len(hits) == 1:
            return hits[0]
        return (None, str(order)) if not hits else (None, None)
    wanted = set(tokens(text)) - {"step", "finished", "completed", "done", "did", "mark", "start", "starting", "begin", "i", "m", "ve", "have", "just", "now", "the", "with"}
    best, best_score = (None, None), 0
    for t, steps in tasks.items():
        for sid, _, description in steps:
            words = set(tokens(description))
            score = len(wanted & words)
            if score and score == len(words) or score >= 2:
                if score > best_score:
                    best, best_score = (t, sid), score
    return best


class RuleBasedCommandProvider(CommandProvider):
    name = "rule-based-commands-v1"

    def parse(self, text: str, vocabulary: Vocabulary, context: CommandContext) -> Command:
        raw = text
        t = normalise(text)
        question = raw.strip().endswith("?") or bool(QUESTION_START.match(t))

        if context.pending_confirmation:
            if YES.match(t) and not question:
                return Command(kind=CommandKind.CONFIRM, raw=raw)
            if NO.match(t):
                return Command(kind=CommandKind.CANCEL, raw=raw)
        if context.awaiting_performance and PERFORMED.match(t) and not STEP_NUMBER.search(t):
            return Command(kind=CommandKind.PERFORMED, raw=raw)
        if HELP.search(t):
            return Command(kind=CommandKind.HELP, raw=raw)
        if RECAP.search(t):
            return Command(kind=CommandKind.RECAP, raw=raw)
        if CHECKS.search(t):
            return Command(kind=CommandKind.CHECKS, raw=raw)
        if GREETING.match(t) and len(t.split()) <= 4:
            return Command(kind=CommandKind.GREET, raw=raw)

        entities, anchors, ambiguous = _mentions(t, vocabulary)
        cmd = Command(kind=CommandKind.ASK, raw=raw, ambiguous=ambiguous)
        if entities:
            cmd.entity_id = entities[0][0]
        elif not ambiguous and context.focus and PRONOUNS.search(t):
            cmd.entity_id, cmd.used_context = context.focus[0], True
        task_id = next((tid for tid, forms in vocabulary.tasks.items() if any(_find(f, t) for f in forms)), None)
        cmd.task_id = task_id

        if question:
            return cmd
        if INTERRUPT.search(t):
            cmd.kind = CommandKind.INTERRUPT
            return cmd

        imperative = POLITE.sub("", t, count=1)
        # "start step 7" / "I'm starting the leak test"
        if STEP_START.match(imperative):
            task, step = match_step(t, context, task_id)
            if step:
                cmd.kind, cmd.task_id, cmd.step_ref = CommandKind.STEP_START, task, step
                return cmd
        # "I finished step 7" / "mark install new pump as done"
        if STEP_DONE.search(t) and not PLACE_VERBS.search(t):
            task, step = match_step(t, context, task_id)
            if step:
                cmd.kind, cmd.task_id, cmd.step_ref = CommandKind.STEP_DONE, task, step
                return cmd

        # Statements of where something is.
        first_person = bool(re.match(r"^(i|we)\b", t)) or bool(re.match(r"^(i|we) (just |have |ve )?", t))
        if (first_person and PLACE_VERBS.search(t)) or LOCATION_STATEMENT.search(t):
            if cmd.entity_id or ambiguous:
                cmd.kind, cmd.attribute = CommandKind.TELL, "location"
                cmd.intervention = first_person and bool(PLACE_VERBS.search(t))
                after = [a for a, span in anchors if not entities or span[0] > entities[0][1][0]]
                if after:
                    cmd.anchor_id = cmd.value = after[-1]
                else:
                    m = PREPOSITION.search(t)
                    cmd.unknown_place = m.group(1).strip() if m else "?"
                return cmd

        # "I opened the valve": the user already acted — record it, nothing to authorise.
        for rx, attribute, value in DID_VERBS:
            if rx.search(t) and (cmd.entity_id or ambiguous):
                cmd.kind, cmd.attribute, cmd.value, cmd.intervention = CommandKind.TELL, attribute, value, True
                return cmd

        # Imperatives: a physical action ORBIT must not take by itself.
        for rx, verb, attribute, value in ACTION_VERBS:
            m = rx.search(imperative)
            if m:
                task, step = match_step(imperative, context, task_id)
                if not (cmd.entity_id or ambiguous or step):
                    break
                cmd.kind = CommandKind.ACT
                cmd.verb = verb or m.group(0).split()[0]
                cmd.attribute, cmd.value = attribute, value
                if verb == "move":
                    after = [a for a, span in anchors if not entities or span[0] > entities[0][1][0]]
                    cmd.anchor_id = cmd.value = after[-1] if after else None
                    if cmd.anchor_id is None:
                        cmd.attribute = None
                cmd.task_id, cmd.step_ref = task or task_id, step
                return cmd

        # "<entity> is <value>" / "<entity> <attribute> is <value>"
        m = re.search(r"\b(?:is|are) (?:now |still )?(?:set to )?([a-z0-9 ]{1,30})$", t)
        if m and (cmd.entity_id or ambiguous):
            value_text = m.group(1).strip()
            attribute = next((a for a in sorted(vocabulary.attributes, key=len, reverse=True)
                              if a != "location" and _find(a, t[: m.start()])), None)
            attribute = attribute or VALUE_ATTRIBUTE.get(value_text)
            if attribute:
                cmd.kind, cmd.attribute = CommandKind.TELL, attribute
                cmd.value = VALUE_CANONICAL.get(value_text, value_text)
                if value_text in ("true", "yes"):
                    cmd.value = True
                elif value_text in ("false", "no"):
                    cmd.value = False
                return cmd
        return cmd


# ------------------------------------------------------------------ LLM parser
COMMAND_TOOL = {
    "name": "orbit_command",
    "description": "Record the single structured request the user's message makes to ORBIT.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": [k.value for k in CommandKind if k not in (CommandKind.CONFIRM, CommandKind.CANCEL)]},
            "entity_id": {"type": "string", "description": "id of a known entity, if the message is about one"},
            "attribute": {"type": "string"},
            "value": {"description": "TELL: the stated value; ACT: the value the action should produce"},
            "anchor_id": {"type": "string", "description": "id of a known place"},
            "task_id": {"type": "string"},
            "step_ref": {"type": "string", "description": "step id"},
            "verb": {"type": "string", "description": "ACT: the action verb, e.g. open, close, turn off, install"},
            "intervention": {"type": "boolean", "description": "TELL: true when the user reports something they did (moved, opened, …)"},
        },
        "required": ["kind"],
    },
}

SYSTEM_PROMPT = """You translate one message from a technician into exactly one ORBIT command by calling orbit_command.
ORBIT is a world-state memory of a physical workspace. You do not answer, you only classify.
Kinds: ASK (any question about the world or tasks), TELL (the user states a fact: where something is, or an attribute value),
STEP_DONE / STEP_START (the user finished / is starting a task step), INTERRUPT (pause the task),
ACT (the user asks for a physical action to be done, e.g. "open the valve"), PERFORMED (the user says they did the action
just discussed), CHECKS ("what should I check?"), RECAP ("what did you do?"), HELP, GREET.
Use only ids listed below; if something is not listed, omit the field. Never invent ids.
Known entities (id: names; type): {entities}
Known places: {anchors}
Known attributes: {attributes}
Tasks and steps: {steps}
Recently discussed (for "it"/"that"): {focus}"""


Transport = Callable[[Dict[str, Any]], Dict[str, Any]]


def http_transport(api_key: str, url: str = "https://api.anthropic.com/v1/messages", timeout: float = 20.0) -> Transport:
    def send(body: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https endpoint)
            return json.loads(response.read())
    return send


class AnthropicCommandProvider(CommandProvider):
    """Claude parses the message; ORBIT validates the result against its vocabulary."""

    def __init__(self, transport: Transport, model: str = "claude-sonnet-5-5", fallback: Optional[CommandProvider] = None):
        self.transport = transport
        self.model = model
        self.fallback = fallback or RuleBasedCommandProvider()
        self.name = f"anthropic:{model}"
        self.last_error: Optional[str] = None

    def parse(self, text: str, vocabulary: Vocabulary, context: CommandContext) -> Command:
        # Consent and the action being reported are never left to a model.
        base = self.fallback.parse(text, vocabulary, context)
        if base.kind in (CommandKind.CONFIRM, CommandKind.CANCEL, CommandKind.PERFORMED):
            return base
        try:
            body = {
                "model": self.model,
                "max_tokens": 300,
                "system": self._system(vocabulary, context),
                "tools": [COMMAND_TOOL],
                "tool_choice": {"type": "tool", "name": "orbit_command"},
                "messages": [{"role": "user", "content": text}],
            }
            reply = self.transport(body)
            call = next(b for b in reply.get("content", []) if b.get("type") == "tool_use")
            cmd = self._validate(call.get("input", {}), text, vocabulary, context)
        except Exception as exc:  # network, schema, or validation failure → deterministic parse
            self.last_error = f"{type(exc).__name__}: {exc}"
            return base
        self.last_error = None
        return cmd

    @staticmethod
    def _system(vocabulary: Vocabulary, context: CommandContext) -> str:
        entities = "; ".join(f"{eid}: {', '.join(forms)}; {vocabulary.entity_types.get(eid)}" for eid, forms in vocabulary.entities.items())
        anchors = ", ".join(vocabulary.anchors)
        steps = "; ".join(f"{t}: " + ", ".join(f"{sid} (step {o}: {d})" for sid, o, d in s) for t, s in context.steps.items())
        return SYSTEM_PROMPT.format(entities=entities or "none", anchors=anchors or "none",
                                    attributes=", ".join(vocabulary.attributes) or "none",
                                    steps=steps or "none", focus=", ".join(context.focus) or "none")

    def _validate(self, data: Dict[str, Any], text: str, vocabulary: Vocabulary, context: CommandContext) -> Command:
        kind = CommandKind(data["kind"])
        if kind in (CommandKind.CONFIRM, CommandKind.CANCEL):
            raise ValueError("consent is parsed deterministically")
        entity = data.get("entity_id")
        if entity is not None and entity not in vocabulary.entities:
            raise ValueError(f"unknown entity {entity}")
        anchor = data.get("anchor_id")
        if anchor is not None and anchor not in vocabulary.anchors:
            raise ValueError(f"unknown place {anchor}")
        task, step = data.get("task_id"), data.get("step_ref")
        if step is not None:
            owners = [t for t, steps in context.steps.items() if any(sid == step for sid, _, _ in steps)]
            if not owners or (task and task not in owners):
                raise ValueError(f"unknown step {step}")
            task = task or owners[0]
        elif task is not None and task not in vocabulary.tasks:
            raise ValueError(f"unknown task {task}")
        cmd = Command(
            kind=kind, raw=text, entity_id=entity, attribute=data.get("attribute"), value=data.get("value"),
            intervention=bool(data.get("intervention")),
            anchor_id=anchor, task_id=task, step_ref=step, verb=data.get("verb"), parser=self.name,
        )
        if kind == CommandKind.TELL and cmd.attribute == "location":
            cmd.value = anchor
            if anchor is None:
                raise ValueError("location statement without a known place")
        if kind == CommandKind.TELL and (entity is None or cmd.attribute is None):
            raise ValueError("statement without entity and attribute")
        if kind in (CommandKind.STEP_DONE, CommandKind.STEP_START) and step is None:
            raise ValueError("step command without a step")
        if kind == CommandKind.ACT and entity is None and step is None:
            raise ValueError("action without a target")
        return cmd
