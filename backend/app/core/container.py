"""Composition root: wires repository, clock and services together.

Services are constructed once per app (or per test) and receive their collaborators
explicitly, so any provider can be swapped for ablations (ADR-002).
"""
import os
from dataclasses import dataclass
from typing import Optional

from backend.app.core.clock import Clock, SystemClock
from backend.app.core.realtime import EventBus, publish_committed
from backend.app.providers.commands import AnthropicCommandProvider, CommandProvider, RuleBasedCommandProvider, http_transport
from backend.app.repositories.base import Repository
from backend.app.services.actions import ActionSafetyService
from backend.app.services.assistant import AssistantService
from backend.app.services.devices import DeviceController
from backend.app.services.live import LiveService
from backend.app.services.voice_agent import VoiceAgent
from backend.app.services.active_perception import ActivePerceptionPlanner
from backend.app.services.hypotheses import HypothesisService
from backend.app.services.identity import IdentityService
from backend.app.services.memory import MemoryService
from backend.app.services.camera import CameraService
from backend.app.services.counterfactual import SandboxRegistry
from backend.app.services.query_agent import QueryAgent
from backend.app.services.replay import ReplayService
from backend.app.services.relations import RelationService
from backend.app.services.risk import RiskPolicy
from backend.app.services.evidence_policy import EvidencePolicy
from backend.app.services.freshness import FreshnessPolicyRegistry
from backend.app.services.search import SearchPolicy, SearchService
from backend.app.services.spatial import AnchorRegistry
from backend.app.services.tasks import TaskService
from backend.app.services.world_diff import WorldDiffService
from backend.app.services.world_state_engine import WorldStateEngine

DEFAULT_DATABASE_URL = "sqlite:///./orbit.db"


@dataclass(frozen=True)
class OrbitConfig:
    """Component switches. Defaults are ORBIT; each False/True flip is one ablation
    baseline from spec §28, so a benchmark can remove exactly one mechanism."""

    freshness_enabled: bool = True  # False: no freshness tracking
    detect_contradictions: bool = True  # False: last writer wins
    search_policy_enabled: bool = True  # False: any missing search target is "absent"
    treat_unobserved_as_removed: bool = False  # True: not seen ⇒ removed
    gate_evidence: bool = True  # False: answer from memory regardless of evidence
    risk_grading_enabled: bool = True  # False: HIGH-risk steps accept any supportable evidence


@dataclass
class OrbitServices:
    repo: Repository
    clock: Clock
    anchors: AnchorRegistry
    relations: RelationService
    engine: WorldStateEngine
    memory: MemoryService
    tasks: TaskService
    hypotheses: HypothesisService
    search: SearchService
    diff: WorldDiffService
    agent: QueryAgent
    perception: ActivePerceptionPlanner
    actions: ActionSafetyService
    replay: ReplayService
    sandboxes: SandboxRegistry
    identity: IdentityService
    camera: CameraService
    assistant: AssistantService
    bus: EventBus
    live: LiveService
    voice: VoiceAgent

    config: OrbitConfig = OrbitConfig()

    @classmethod
    def build(
        cls,
        repo: Repository,
        clock: Optional[Clock] = None,
        config: Optional[OrbitConfig] = None,
        sandbox: bool = False,
        realtime: bool = False,
    ) -> "OrbitServices":
        """``realtime=True`` (the served app) publishes every committed event and
        observation on ``bus`` and lets the assistant react to them (Phase 17)."""
        clock = clock or SystemClock()
        config = config or OrbitConfig()
        anchors = AnchorRegistry(repo)
        relations = RelationService(repo)
        freshness = FreshnessPolicyRegistry(enabled=config.freshness_enabled)
        engine = WorldStateEngine(
            repository=repo,
            anchors=anchors,
            relations=relations,
            freshness=freshness,
            policy=EvidencePolicy(freshness, detect_contradictions=config.detect_contradictions),
            sandbox=sandbox,
        )
        memory = MemoryService(repo, engine.claims, relations, anchors)
        tasks = TaskService(repo, engine, RiskPolicy(enabled=config.risk_grading_enabled))
        hypotheses = HypothesisService(repo, engine)
        diff = WorldDiffService(repo, memory, tasks, treat_unobserved_as_removed=config.treat_unobserved_as_removed)
        perception = ActivePerceptionPlanner(repo, engine.claims, anchors, tasks)
        search = SearchService(repo, engine, SearchPolicy(enabled=config.search_policy_enabled))
        actions = ActionSafetyService(repo, tasks.conditions, tasks)
        actions.ensure_agent_principal(clock.now())
        agent = QueryAgent(repo, engine, memory, diff, tasks, hypotheses, planner=perception, gate_evidence=config.gate_evidence)
        bus = EventBus()
        assistant = AssistantService(repo, engine, agent, tasks, actions, perception, command_provider(), bus=bus)
        assistant.ensure_principal(clock.now())
        if realtime and not sandbox:
            repo.on_commit(publish_committed(bus))
        live = LiveService(assistant, DeviceController())
        return cls(
            repo=repo,
            clock=clock,
            anchors=anchors,
            relations=relations,
            engine=engine,
            memory=memory,
            tasks=tasks,
            hypotheses=hypotheses,
            search=search,
            diff=diff,
            agent=agent,
            perception=perception,
            actions=actions,
            replay=ReplayService(repo, diff),
            sandboxes=SandboxRegistry(),
            identity=IdentityService(repo, engine),
            camera=CameraService(repo, engine, search),
            assistant=assistant,
            bus=bus,
            live=live,
            voice=VoiceAgent(live),
            config=config,
        )


def command_provider() -> CommandProvider:
    """Rule-based by default. ORBIT_ASSISTANT_LLM=anthropic (with ANTHROPIC_API_KEY) lets
    Claude parse messages; the workspace vocabulary is then sent to the API, so it is
    opt-in only. ORBIT_ASSISTANT_MODEL overrides the model."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if os.environ.get("ORBIT_ASSISTANT_LLM", "").lower() == "anthropic" and key:
        return AnthropicCommandProvider(http_transport(key), os.environ.get("ORBIT_ASSISTANT_MODEL", "claude-sonnet-5-5"))
    return RuleBasedCommandProvider()


def default_repository() -> Repository:
    """SQL repository from ORBIT_DATABASE_URL, migrated to head."""
    from backend.app.repositories.sql import SqlRepository
    from database.migrate import upgrade_to_head

    url = os.environ.get("ORBIT_DATABASE_URL", DEFAULT_DATABASE_URL)
    upgrade_to_head(url)
    return SqlRepository(url)
