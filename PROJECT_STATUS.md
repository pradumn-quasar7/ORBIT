# ORBIT Project Status

## Current Phase
Phase 7 — Active perception + action safety (COMPLETE). Next: Phase 8 — Perception adapter + web inspection UI.

## Phase Log

### Phase 0 — Foundations (§48 steps 1–3)
**Planned**
- Audit the initial Phase 0 commit against the full spec; fix defects that violate research principles.
- Restore the full `ORBIT_PROJECT_INIT.md` (the repo held a condensed 571-line rewrite of the 2,702-line spec).
- Database schema + migrations (§48 step 3), repository boundary, deterministic time.
- Benchmark protocol draft (§35 Phase 0 deliverable).

**Implemented**
- `core/time.py` (`UTCDateTime`), `core/clock.py` (`SystemClock`, `FixedClock`), `core/container.py` (composition root).
- `repositories/base.py` (`Repository` ABC, value semantics, `transaction()`); `InMemoryRepository` rewritten (deep copies, rollback); `repositories/sql/` (`tables.py`, `SqlRepository`); Alembic in `database/migrations` (revision `0001`), auto-upgraded on app start.
- Engine fixes: correct `OBSERVED`/`VERIFIED` classification; entity status = weakest attribute status; atomic ingestion; duplicate-observation rejection; `register_entity` routes `POST /entities` through evidence.
- API: `create_app(repository, clock)` factory, routers in `api/world.py`, lazy module-level `app`.
- Tests: every engine test runs against both repositories; the tautological unknown-≠-absent test fixed; migration/schema parity test.
- Docs: `docs/experiments/BENCHMARK_PROTOCOL.md`; ADR-005…008; roadmap reordered to §48.

### Phase 1 — Spatial persistence (§48 steps 4–6)
**Planned**
- Anchor hierarchy; entity registry with deterministic re-identification; relation maintenance with validity intervals; sessions.
- Exit: same object persists across two observations/sessions.

**Implemented**
- Domain: `Anchor`, `Session`, `ObservedRelation`, `EntityResolution`; `ObservedEntity.identifiers`; `Entity.identity_status` / `identity_candidates`; `IdentityStatus`, `ResolutionMethod`; events `IDENTITY_AMBIGUOUS`, `IDENTITY_CONFLICT`.
- `services/spatial.py` `AnchorRegistry` (lineage, descendants, proximity, cycle guard).
- `services/entity_registry.py` `EntityRegistry` (explicit id → identifier → signature → spatial; ambiguity and replacement handling).
- `services/relations.py` `RelationService` (exclusive/symmetric types, explicit-absence closing, as-of queries).
- Engine: resolution per detection with one-match-per-observation, resolutions persisted on the observation, relations applied after resolution, sessions auto-created/touched.
- API: `POST/GET /anchors`, `GET /anchors/{id}`, `POST/GET /sessions`, `POST /sessions/{id}/end`, `GET /sessions/{id}/observations`, `GET /entities/{id}/relations`.
- Migration `0002`. Tests: `test_spatial_persistence.py` (16 cases × 2 backends) incl. same-looking objects, replaced object, relocation, relation lifecycle.

### Phase 2 — Evidence engine (§48 steps 7–8)
**Planned**
- Evidence source types, provenance, integrity; deterministic evidence policy; attribute-level freshness engine; interventions; invalidation propagation; contradiction lifecycle.
- Exit: unsupported current-state claims are blocked or downgraded.

**Implemented**
- Domain: `SourceType`, `EvidenceChannel`, `ClaimDisposition`, `ClaimDecision`, `FreshnessState`; `Evidence.source/content`; `SupportRef`; `StateVersion` support history, disposition, invalidation fields; `Conflict`, `ClaimDependency`; read models `FreshnessAssessment`, `ClaimAssessment`, `EntityAssessment`; events `CONFLICT_RESOLVED`, `UNCONFIRMED_CHANGE`, `STATE_INVALIDATED`.
- `services/freshness.py` (policy registry, read-time assessment, ablation switch), `services/evidence_policy.py` (source inference, grading, integrity hashing, decision table), `services/claims.py` (evidence gate), `services/belief.py` (decision execution, conflicts, invalidation + propagation, materialisation).
- Engine: observations, `assert_claim`, `record_intervention`, `add_dependency`, `verify_evidence` all share one policy path; late observations cannot rewind the cache.
- API: `GET /entities/{id}/state`, `GET /entities/{id}/claims/{attr}`, `POST /claims`, `POST /entities/{id}/interventions`, `POST /dependencies`, `GET /conflicts`, `GET /evidence/{id}`.
- Migration `0003` (schema + data backfill of source types and support). ADR-011…013.
- Tests: `test_evidence_engine.py` (31 cases × 2 backends) incl. exit-criterion table, weak/misleading/out-of-order evidence, conflict lifecycle, transitive cycle-safe propagation, tamper detection, ablations, migration backfill.

### Phase 3 — Memory core (§48 step 9)
**Planned**
- Temporal (as-of snapshots, timelines), spatial (locate, anchor contents), episodic (session summaries), procedural (task/step persistence and replay), causal-hypothesis memory.
- Exit: history and task state are queryable.

**Implemented**
- Domain: `StateCondition`, `Interruption`, reworked `TaskStep`/`Task`, `CausalHypothesis`; `TaskStatus`, `StepStatus`, `HypothesisStatus`; `Event.task_id`; read models `WorldSnapshot`, `EntitySnapshot`, `LocationAnswer`, `AnchorContent`, `SessionSummary`, `TaskStateView`.
- `services/memory.py` (`world_snapshot`, `timeline`, `locate`, `contents`, `session_summary`, `previous_session`), `services/tasks.py` (graph validation, evidence-backed start/complete/interrupt, as-of replay), `services/hypotheses.py`.
- API: `GET /world/snapshot`, `GET /entities/{id}/timeline`, `GET /entities/{id}/location`, `GET /anchors/{id}/contents`, `GET /sessions/{id}/summary`, `POST/GET /tasks`, `GET /tasks/{id}`, `/state`, `/history`, `POST /tasks/{id}/steps/{step}/start|complete`, `POST /tasks/{id}/interrupt`, `POST/GET /hypotheses`, `POST /hypotheses/{id}/evidence`.
- Migration `0004` (+ step-status data conversion). ADR-014…016. Tests: `test_memory_core.py`.

### Phase 4 — World diff (§48 step 10)
**Planned**
- Snapshot diff `Diff(B_a, B_b)` with typed changes; removed vs unobserved distinction via negative search memory; precision/recall evaluation.
- Exit: known scene changes are measured with precision/recall.

**Implemented**
- Domain: `AbsenceStatus`, `SearchResult`, extended `SearchCoverage`, `WorldChange` (status, absence, notes, related ids), `DiffUncertainty`, `WorldDiff.mode/uncertain`, `ClaimAssessment.has_current_claim`.
- `services/search.py` (`SearchPolicy`, `SearchService`), `BeliefUpdater.apply_absence`, `services/world_diff.py` (`diff`, `diff_since_session`, `event_log_diff` baseline), `evaluation/metrics.py` (`ExpectedChange`, one-to-one `diff_precision_recall`).
- API: `POST /world/diff` (timestamps or `baseline_session_id`, `mode`), `POST /search`, `GET /search-coverage`.
- Migration `0005`. ADR-017, ADR-018. Tests: `test_world_diff.py` incl. Experiment B-0.
- **Result (Experiment B-0):** snapshot diff P = 1.00 / R = 1.00 (8 changes); event-log baseline P = 0.71 / R = 0.62.

### Phase 5 — Task continuity (§48 steps 11–12)
**Planned**
- Dependency checks, invalidation of affected steps, resume protocol (§11), procedure-revision handling, precondition gating.
- Exit: interrupted tasks resume from verified state.

**Implemented**
- Domain: `ConditionState`, `ConditionCheck`, `ObservationRequest`, `StepAssessment`, `ResumePlan`; `StepSpec.step_order`; strict input models.
- `services/conditions.py` (`ConditionEvaluator`, attribute-aware `instruction_for`), `TaskService.assess_step`, `ancestors`, `revision_check`, `resume`, `acknowledge_revision`; gated `start_step`; evidence-checked `complete_step`.
- API: `POST /tasks/{id}/resume`, `POST /tasks/{id}/procedure/acknowledge`. ADR-019, ADR-020.
- Tests: `test_task_continuity.py` — spec §11 T12 scenario: no-change resume, reopened valve invalidates step 5, stale outcome blocks + requests observation, unverified model number ("move closer so I can read the model number"), contradicted precondition, procedure revision, multi-user handoff, branching DAG; every plan checked for unsafe continuation.

### Phase 6 — Grounded query agent + hybrid retrieval (§48 steps 13–14)
**Planned**
- Replaceable embedding/retrieval/reasoning providers; hybrid retrieval; grounded query agent implementing the §5.2 loop and §15 response contract.

**Implemented**
- `providers/` (`base.py`, `embedding.py`, `retrieval.py`, `reasoning.py`), `services/hybrid_retrieval.py`, `services/query_agent.py`.
- Domain: `QueryKind`, `QueryIntent`, `GroundedClaim`, `RetrievalHit`, `GroundedResponse`.
- Supported questions: where is / where was at T, attribute value, contents of an anchor, what changed (since last session / since T), continue, what happened (yesterday), why.
- API: `POST /queries`, `GET /memory/search`. ADR-021, ADR-022.
- Tests: `test_query_agent.py` — contract checked on every response; stale → abstain + "Point the camera near bench_3…"; contradiction surfaced, never resolved; ambiguity → clarifying question; ablation without evidence gate answers stale memory.

### Phase 7 — Active perception + action safety (§48 step 15, §16)
**Planned**
- Targeted observation planning with a replaceable heuristic information-gain policy and baselines; observe → verify → authorize → act → verify → record for consequential actions.

**Implemented**
- Domain: `ObservationActionType`, `ObservationCost`, `UncertainClaim`, `PlannedObservation`, `PerceptionPlan`; `PrincipalKind`, `Scope`, `Principal`, `ActionStatus`, `Authorization`, `ActionRequest`, `OutcomeResult`, `OutcomeRecord`; event `ACTION_STATUS_CHANGED`.
- `services/active_perception.py` (planner, marginal-gain greedy `InformationGainPolicy`, `FixedPolicy`, `RandomPolicy`, request ranking), `services/actions.py` (`ActionSafetyService`, built-in `orbit-agent` principal without authorize/actuate).
- API: `POST /active-perception/plan`, `POST/GET /principals`, `POST/GET /actions`, `GET /actions/{id}`, `POST /actions/{id}/recheck|authorize|performed|verify-outcome|execute(403)`, `GET /outcomes`. Migration `0006`. ADR-023, ADR-024.
- **Result (Experiment F, Phase 7 scene):** 3 looks remove 2.89/3.10 uncertainty (information gain) vs 1.93 (fixed) vs 1.67–2.89 (random seeds 0–9).

## Known Issues / Limitations (to be addressed in named phases)
- Ambiguous entities cannot yet be merged into their true identity after verification (future work).

## Tests
- `.venv/bin/pytest` → 312 passed.

## Recent Architecture Decisions
- ADR-005 Repository boundary + SQL store · ADR-006 UTC time · ADR-007 §48 phase order · ADR-008 status classification
- ADR-009 Conservative re-identification · ADR-010 Relation semantics
- ADR-011 Evidence policy · ADR-012 Read-time freshness · ADR-013 Invalidation propagation
- ADR-014 Memory as read model · ADR-015 Step progress vs completion evidence · ADR-016 Causal hypotheses
- ADR-017 Snapshot world diff · ADR-018 Coverage-validated absence
- ADR-019 Resume protocol · ADR-020 Strict evidence inputs
- ADR-021 Deterministic replaceable providers · ADR-022 Grounded response contract
- ADR-023 Marginal information-gain perception · ADR-024 Action safety boundary

## Research Experiments Enabled
- Experiment A (persistent identity): re-ID decisions are auditable per observation.
- Experiment C (stale-memory resistance): freshness gate + ablation switch.
- Evidence-gate ablation (`gate_evidence=False`) and vector recall vs structured state.
- Conflict handling ablation: `detect_contradictions=False` (last writer wins).
- Experiment D (task resumption): resume plans expose blocked/invalidated steps and requests.
- Experiment F (active perception): information-gain vs fixed vs random policies.
- Experiment E (evidence and causality): causal hypotheses gated on causal-test evidence.
- Experiment B-0 (world diff) runnable as a test with precision/recall; negative-search ablations available.

## Last Updated
- 2026-10-01
