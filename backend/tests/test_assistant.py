"""Phase 16: conversational assistant acting on the user's behalf within ORBIT's boundaries."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, StateCondition
from backend.app.domain.types import ActionStatus, PrincipalKind, Scope, SourceType, StepStatus, TaskStatus
from backend.app.providers.commands import AnthropicCommandProvider, CommandContext, CommandKind, RuleBasedCommandProvider
from backend.app.services.actions import PermissionDenied
from backend.app.services.assistant import ASSISTANT, AssistantService, Gesture
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0.0):
    return T0 + timedelta(minutes=minutes)


def ent(cid, loc, type, **kw):
    return ObservedEntity(candidate_entity_id=cid, type=type, location=loc, **kw)


class Lab:
    def __init__(self, repo, realtime=False):
        self.svc = OrbitServices.build(repo, realtime=realtime)
        for a, p in (("lab204", None), ("bench_3", "lab204"), ("bench_4", "lab204"), ("shelf_a", "lab204")):
            self.svc.anchors.register(a, T0, parent_id=p)
        self._n = 0
        self.see(0, ent("valve", "bench_3", "valve", attributes={"state": "closed"}),
                 ent("m17", "bench_4", "microscope", name="Microscope M17", attributes={"power": "off"}),
                 ent("n1", "bench_4", "notebook", name="Notebook N1"),
                 ent("b1", "shelf_a", "bottle"), ent("b2", "shelf_a", "bottle"),
                 ent("pump_new", "bench_3", "pump"),
                 ent("entity_mouse_0f27190249", "bench_3", "mouse"))
        self.svc.tasks.create_task("Replace coolant pump", [
            StepSpec(id="s6", step_order=6, description="Remove old pump"),
            StepSpec(id="s7", step_order=7, description="Install new pump", dependencies=["s6"], preconditions=[
                StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200")]),
        ], at(0), task_id="T12")
        self.svc.actions.register_principal("ana", PrincipalKind.HUMAN, [Scope.OBSERVE, Scope.AUTHORIZE, Scope.ACTUATE], T0)
        self.svc.actions.register_principal("guest", PrincipalKind.HUMAN, [Scope.OBSERVE], T0)
        self.conv = self.svc.assistant.start("ana", at(1))

    def see(self, minutes, *entities):
        self._n += 1
        self.svc.engine.record_observation(Observation(id=f"o{self._n}", timestamp=at(minutes), source="camera", observed_entities=list(entities)))

    def say(self, text, minutes, conv=None):
        return self.svc.assistant.say((conv or self.conv).id, text, at(minutes))


@pytest.fixture
def lab(repo):
    return Lab(repo)


# ------------------------------------------------------------------- questions
def test_questions_are_grounded_and_it_follows_the_conversation(lab):
    turn = lab.say("Where is the microscope?", 2)
    assert turn.command.kind == CommandKind.ASK and "bench_4" in turn.reply
    assert turn.gesture == Gesture.EXPLAIN and turn.focus[0] == "m17" and turn.response.claims
    again = lab.say("where is it now?", 3)
    assert again.command.used_context and "bench_4" in again.reply


def test_speech_says_names_not_generated_ids(lab):
    turn = lab.say("where is entity_mouse_0f27190249?", 2)
    assert "entity_mouse_0f27190249" in turn.reply
    assert "0f27190249" not in turn.speech and "mouse" in turn.speech


def test_unknown_requests_shrug_instead_of_guessing(lab):
    turn = lab.say("sing me a song", 2)
    assert turn.gesture == Gesture.SHRUG and not turn.done


# ------------------------------------------------------------------ statements
def test_statement_is_recorded_as_testimony_with_provenance(lab, repo):
    turn = lab.say("I put the notebook on bench 3", 5)
    assert turn.command.kind == CommandKind.TELL and turn.command.anchor_id == "bench_3"
    assert [d.kind for d in turn.done] == ["recorded_statement"]
    loc = lab.svc.engine.claims.assess_attribute("n1", "location", at(5))
    assert loc.last_known_value == "bench_3"
    evidence = repo.get_evidence(loc.evidence_refs[-1])
    assert evidence.source_type == SourceType.USER_STATEMENT
    assert evidence.provenance["utterance"] == "I put the notebook on bench 3"
    assert evidence.provenance["conversation"] == lab.conv.id and evidence.provenance["actor"] == "ana"


def test_a_bare_statement_against_fresh_evidence_is_a_conflict(lab):
    lab.see(10, ent("valve", "bench_3", "valve", attributes={"state": "closed"}))
    turn = lab.say("the valve is open", 10.5)
    assert turn.command.kind == CommandKind.TELL and (turn.command.attribute, turn.command.value, turn.command.intervention) == ("state", "open", False)
    assert turn.gesture == Gesture.ALERT and turn.observation_requests
    assert lab.svc.engine.claims.assess_attribute("valve", "state", at(11)).status.value == "CONTRADICTED"


@pytest.mark.parametrize("text, entity, attribute, value", [
    ("I opened the valve", "valve", "state", "open"),
    ("I just turned the microscope on", "m17", "power", "on"),
    ("I moved the notebook to bench 3", "n1", "location", "bench_3"),
])
def test_reporting_what_you_did_is_an_intervention(lab, repo, text, entity, attribute, value):
    lab.see(10, ent("valve", "bench_3", "valve", attributes={"state": "closed"}))
    turn = lab.say(text, 10.5)
    assert turn.command.intervention and turn.gesture == Gesture.NOD and "your change" in turn.reply
    claim = lab.svc.engine.claims.assess_attribute(entity, attribute, at(11))
    assert (claim.last_known_value, claim.status.value) == (value, "OBSERVED")
    assert any(e.event_type.value == "STATE_INVALIDATED" for e in repo.list_events() if e.entity_id == entity)


def test_unknown_place_and_ambiguous_object_record_nothing(lab, repo):
    events = len(repo.list_events())
    drawer = lab.say("I put the notebook in the drawer", 5)
    assert drawer.command.unknown_place == "drawer" and not drawer.done
    bottle = lab.say("I put the bottle on bench 4", 6)
    assert bottle.gesture == Gesture.ASK and "b1" in bottle.reply and "b2" in bottle.reply
    assert len(repo.list_events()) == events


# ---------------------------------------------------------------------- tasks
def test_step_reports_go_through_the_task_service(lab, repo):
    blocked = lab.say("start step 7", 2)
    assert blocked.command.kind == CommandKind.STEP_START and blocked.gesture == Gesture.ALERT
    done = lab.say("I finished step 6", 3)
    assert done.command.kind == CommandKind.STEP_DONE and done.gesture == Gesture.NOD
    task = repo.get_task("T12")
    s6 = next(s for s in task.steps if s.id == "s6")
    assert s6.status == StepStatus.COMPLETED and s6.completed_by == "ana"
    assert repo.get_evidence(s6.evidence_refs[-1]).source_type == SourceType.USER_STATEMENT
    assert "model" in done.reply.lower() and done.observation_requests  # what blocks step 7
    still = lab.say("I'm starting install new pump", 4)
    assert still.gesture == Gesture.ALERT and "model" in still.reply.lower()


def test_pause_interrupts_the_task(lab, repo):
    turn = lab.say("pause the task", 2)
    assert turn.command.kind == CommandKind.INTERRUPT
    assert repo.get_task("T12").status == TaskStatus.INTERRUPTED


# -------------------------------------------------------------------- actions
def test_action_needs_explicit_yes_then_user_acts_then_outcome_is_verified(lab, repo):
    ask = lab.say("please open the valve", 2)
    assert ask.command.kind == CommandKind.ACT and ask.gesture == Gesture.ASK and ask.pending is not None
    early = lab.say("done", 2.2)  # not consent, and nothing was authorised yet
    assert early.gesture == Gesture.ASK and "still waiting" in early.reply and early.pending is not None
    action = repo.get_action(ask.pending.action_id)
    assert action.status == ActionStatus.AWAITING_AUTHORIZATION and action.requested_by == ASSISTANT
    assert action.expected_outcome[0].expected == "open"

    yes = lab.say("yes", 2.5)
    assert yes.command.kind == CommandKind.CONFIRM and yes.pending is None
    action = repo.get_action(action.id)
    assert action.status == ActionStatus.AUTHORIZED and action.authorization.principal_id == "ana"
    assert lab.conv.id in action.authorization.reason

    done = lab.say("done", 4)
    action = repo.get_action(action.id)
    assert action.performed_by == "ana" and action.status == ActionStatus.OUTCOME_UNVERIFIED
    assert done.gesture == Gesture.THINK and done.observation_requests

    lab.see(5, ent("valve", "bench_3", "valve", attributes={"state": "open"}))
    verified = lab.say("check again", 6)
    assert repo.get_action(action.id).status == ActionStatus.OUTCOME_VERIFIED and verified.gesture == Gesture.NOD

    recap = lab.say("what did you do for me?", 7)
    assert recap.command.kind == CommandKind.RECAP
    kinds = [d.kind for d in lab.svc.assistant.get(lab.conv.id).delegated]
    assert kinds == ["proposed_action", "authorized_action", "reported_performed", "outcome_checked", "outcome_checked"]


def test_no_declines_and_an_old_yes_expires(lab, repo):
    first = lab.say("close the valve", 2)
    assert first.action is None and "already" in first.reply  # nothing to do: it is closed
    ask = lab.say("turn on the microscope", 3)
    no = lab.say("no", 3.5)
    assert repo.get_action(ask.pending.action_id).status == ActionStatus.DENIED and no.command.kind == CommandKind.CANCEL

    ask = lab.say("open the valve", 4)
    late = lab.say("yes", 7)  # the 2-minute window has passed
    assert late.command.kind != CommandKind.CONFIRM
    assert repo.get_action(ask.pending.action_id).status == ActionStatus.AWAITING_AUTHORIZATION


def test_only_people_who_may_authorize_are_asked_and_the_assistant_never_can(lab, repo):
    guest = lab.svc.assistant.start("guest", at(1))
    turn = lab.say("open the valve", 2, conv=guest)
    assert turn.pending is None and turn.gesture == Gesture.ALERT and "isn't registered" in turn.reply
    assert lab.say("yes", 2.2, conv=guest).command.kind != CommandKind.CONFIRM
    with pytest.raises(PermissionDenied):
        lab.svc.actions.authorize(turn.action.id, ASSISTANT, True, at(3))


def test_blocked_action_explains_prerequisites(lab):
    turn = lab.say("install the new pump", 2)
    assert turn.command.kind == CommandKind.ACT and turn.command.step_ref == "s7"
    assert turn.action.status == ActionStatus.PREREQUISITES_FAILED and turn.pending is None
    assert turn.gesture == Gesture.ALERT and turn.observation_requests


def test_checks_help_and_greeting(lab):
    assert lab.say("hello", 2).gesture == Gesture.WAVE
    assert "physical" in lab.say("what can you do?", 2).reply
    checks = lab.say("what should I check?", 2)
    assert checks.command.kind == CommandKind.CHECKS and checks.reply


# ---------------------------------------------------------------- LLM parser
class FakeClaude:
    def __init__(self, tool_input=None, error=None):
        self.tool_input, self.error, self.calls = tool_input, error, []

    def __call__(self, body):
        self.calls.append(body)
        if self.error:
            raise self.error
        return {"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "name": "orbit_command", "input": self.tool_input}]}


def test_llm_parser_is_validated_and_never_decides_consent(lab):
    vocab = lab.svc.agent.vocabulary()
    ctx = CommandContext(steps={"T12": [("s6", 6, "Remove old pump"), ("s7", 7, "Install new pump")]})
    good = AnthropicCommandProvider(FakeClaude({"kind": "TELL", "entity_id": "n1", "attribute": "location", "anchor_id": "bench_3"}))
    cmd = good.parse("the notes thing is over on the third bench", vocab, ctx)
    assert (cmd.kind, cmd.entity_id, cmd.value, cmd.parser) == (CommandKind.TELL, "n1", "bench_3", "anthropic:claude-sonnet-5-5")
    assert "bench_3" in good.transport.calls[0]["system"]

    invented = AnthropicCommandProvider(FakeClaude({"kind": "ACT", "entity_id": "reactor_9", "verb": "open"}))
    assert invented.parse("open the valve", vocab, ctx).parser == RuleBasedCommandProvider.name
    offline = AnthropicCommandProvider(FakeClaude(error=OSError("no network")))
    assert offline.parse("where is m17?", vocab, ctx).kind == CommandKind.ASK and "OSError" in offline.last_error

    claude = FakeClaude({"kind": "ACT", "entity_id": "valve", "verb": "open"})
    pending = AnthropicCommandProvider(claude)
    yes = pending.parse("yes", vocab, CommandContext(pending_confirmation=True))
    assert yes.kind == CommandKind.CONFIRM and claude.calls == []  # consent never reaches the model


def test_assistant_with_llm_parser_still_needs_the_yes(lab, repo):
    claude = FakeClaude({"kind": "ACT", "entity_id": "valve", "verb": "open", "attribute": "state", "value": "open"})
    svc = lab.svc
    assistant = AssistantService(repo, svc.engine, svc.agent, svc.tasks, svc.actions, svc.perception, AnthropicCommandProvider(claude))
    conv = assistant.start("ana", at(1))
    turn = assistant.say(conv.id, "could you crack that valve open for me", at(2))
    assert turn.command.parser.startswith("anthropic:") and turn.pending is not None
    assert repo.get_action(turn.pending.action_id).status == ActionStatus.AWAITING_AUTHORIZATION


# ------------------------------------------------------------------------ API
def test_assistant_api(client):
    info = client.get("/assistant").json()
    assert info["parser"] == "rule-based-commands-v1" and info["llm"] is False
    assert "perform physical actions" in info["cannot"]
    conv = client.post("/assistant/conversations", json={"user_id": "ana"})
    assert conv.status_code == 201
    cid = conv.json()["id"]
    turn = client.post(f"/assistant/conversations/{cid}/messages", json={"text": "hello"})
    assert turn.status_code == 200 and turn.json()["gesture"] == "WAVE"
    assert len(client.get(f"/assistant/conversations/{cid}").json()["turns"]) == 1
    assert client.post("/assistant/conversations/nope/messages", json={"text": "hi"}).status_code == 404
    assert client.post(f"/assistant/conversations/{cid}/messages", json={"text": ""}).status_code == 422
    assert client.post("/assistant/conversations", json={"user_id": "ana", "id": cid}).status_code == 409


# ------------------------------------------------------------- spoken input
@pytest.mark.parametrize("heard, entity, value", [
    ("I move the notebook to bench four.", "n1", "bench_4"),  # Whisper: present tense, number as a word
    ("I put the notebook on bench for", "n1", "bench_4"),  # homophone
    ("i placed the notebook on bench 3", "n1", "bench_3"),
])
def test_speech_transcripts_are_understood(lab, heard, entity, value):
    turn = lab.say(heard, 5)
    assert turn.command.kind == CommandKind.TELL and turn.command.intervention
    assert lab.svc.engine.claims.assess_attribute(entity, "location", at(5)).last_known_value == value


def test_spoken_numbers_only_touch_numbered_places():
    from backend.app.providers.commands import spoken_numbers
    assert spoken_numbers("I finished step six") == "I finished step 6"
    assert spoken_numbers("that one is for me") == "that one is for me"
    assert spoken_numbers("bring it to bench two") == "bring it to bench 2"


# ------------------------------------------------------- live conversation
@pytest.mark.parametrize("heard, kind", [
    ("hay Aur Bhi", CommandKind.GREET),  # "Hey Orbi" as transcribed for an Indian accent
    ("hey orbi", CommandKind.GREET),
    ("Orbit", CommandKind.GREET),
    ("I am audible to you", CommandKind.HEARD),
    ("can you hear me?", CommandKind.HEARD),
    ("testing one two three", CommandKind.HEARD),
    ("ok so like", CommandKind.HEARD),
    ("hey orbi where is the microscope", CommandKind.ASK),
])
def test_what_people_actually_say(lab, heard, kind):
    turn = lab.say(heard, 2)
    assert turn.command.kind == kind
    assert "couldn't map" not in turn.reply


def test_address_is_stripped_and_question_answered(lab):
    turn = lab.say("hey orbi, where is the microscope?", 2)
    assert "bench_4" in turn.reply


def test_unknown_gets_a_friendly_fallback(lab):
    turn = lab.say("so what about the weather", 2)
    assert turn.gesture == Gesture.SHRUG and "didn't catch" in turn.reply and turn.response is None


def test_what_changed_phrasings(lab):
    for text in ("so what change from the last scenario", "what's new?", "any updates"):
        assert lab.say(text, 2).response.intent.kind.value == "WHAT_CHANGED", text


@pytest.mark.parametrize("utterance, confirms", [
    ("yes", True), ("yes please", True), ("ok go ahead", True), ("yes, authorize it", True),
    ("ok so like", False), ("yes but where is the microscope", False), ("okay what changed", False),
])
def test_consent_must_be_the_whole_utterance(lab, repo, utterance, confirms):
    ask = lab.say("open the valve", 2)
    turn = lab.say(utterance, 2.2)
    assert (turn.command.kind == CommandKind.CONFIRM) is confirms
    expected = ActionStatus.AUTHORIZED if confirms else ActionStatus.AWAITING_AUTHORIZATION
    assert repo.get_action(ask.pending.action_id).status == expected


def test_what_changed_is_spoken_naturally(lab):
    lab.see(5, ent("m17", "bench_3", "microscope", name="Microscope M17"))
    turn = lab.say("what changed since 9:02?", 6)
    assert turn.response.changes and "→" not in turn.speech
    assert "change" in turn.speech and "Microscope M17" in turn.speech


# ----------------------------------------------- Quest headset transcripts
@pytest.mark.parametrize("heard, kind", [
    ("Hi, Hey R.B.", CommandKind.GREET),
    ("hey arby", CommandKind.GREET),
    ("Get an action approved.", CommandKind.HELP),
    ("okay, where is the microscope?", CommandKind.ASK),
])
def test_headset_transcripts(lab, heard, kind):
    assert lab.say(heard, 2).command.kind == kind


def test_where_is_a_place_answers_what_is_there(lab):
    turn = lab.say("okay tell me where is bench 4", 2)
    assert turn.response.intent.kind.value == "CONTENTS" and "Microscope M17" in turn.reply


def test_unknown_object_lists_what_orbi_knows(lab):
    turn = lab.say("where are the pin decks", 2)
    assert "I know about:" in turn.reply and "Microscope M17" in turn.reply


def test_place_contents_are_spoken_naturally(lab):
    turn = lab.say("what's on bench 4?", 2)
    assert "(" not in turn.speech and "OBSERVED" not in turn.speech
    assert turn.speech.startswith("At bench 4") and "Microscope M17" in turn.speech
