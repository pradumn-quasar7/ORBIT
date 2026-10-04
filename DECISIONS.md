# Architecture Decisions (ADR Log)

## ADR-001 — Structured World State as Source of Truth
- **Status**: Accepted
- **Context**: LLMs hallucinate, and vector stores only retrieve semantically close text without temporal validity, consistency guarantees, or exact spatial relations.
- **Decision**: Store world entities, attributes, relations, states, and evidence as structured, typed models. Language models and vector stores are secondary tools for semantic recall and text synthesis.
- **Consequences**: State transitions are deterministic, verifiable, and testable.

## ADR-002 — Replaceable Perception and Reasoning Providers
- **Status**: Accepted
- **Context**: Foundation models and computer vision detectors evolve quickly.
- **Decision**: Keep `PerceptionProvider`, `ReasoningProvider`, and `StorageProvider` interfaces cleanly decoupled from core business logic.
- **Consequences**: System runs with mock/simulated providers in tests, local models (YOLO/OWLv2), or frontier cloud models without changing core engine code.

## ADR-003 — Append-Only State History with Epistemic Status
- **Status**: Accepted
- **Context**: Tracking "what changed" requires longitudinal auditability. Overwriting state destroys the ability to reason across sessions.
- **Decision**: All state updates create a new `StateVersion` referencing supporting `Evidence` and update the previous version's `valid_to` timestamp.
- **Consequences**: Enables time-travel queries, audit logs, and zero-information-loss rollbacks.

## ADR-004 — Contradiction Retention Policy
- **Status**: Accepted
- **Context**: When digital registry and visual evidence conflict, conventional systems either crash or pick one arbitrarily.
- **Decision**: Mark entity state as `CONTRADICTED`, retain all conflicting evidence pointers, and surface conflicts in queries rather than guessing.
- **Consequences**: Prevents unsafe operations when uncertainty exists.

## ADR-005 — Repository boundary with value semantics; SQL as durable store
- **Status**: Accepted (Phase 0 remediation)
- **Context**: The original in-memory repository returned live object references, so state changed without `save_*` and tests passed through aliasing. That would break silently on any real database. The spec's dependency order (§48 step 3) requires a schema and migrations before the engine grows.
- **Decision**: Define an abstract `Repository` with value semantics (returned objects are copies; `transaction()` is atomic). Ship `InMemoryRepository` (deep copies, snapshot rollback) and `SqlRepository` (SQLAlchemy Core, explicit tables, Alembic migrations in `database/migrations`). SQLite is the dev/test default; the schema is PostgreSQL-compatible (tz-aware `UTCDateTimeType`, JSON columns). Every engine test runs against both implementations.
- **Consequences**: World state survives restarts. Any schema change requires a migration; `test_migrations_match_schema` fails if tables and migrations drift.

## ADR-006 — All timestamps are timezone-aware UTC
- **Status**: Accepted
- **Context**: The original engine contained per-comparison naive/aware alignment hacks, which silently mis-order evidence from sources in different zones.
- **Decision**: All domain timestamps use `UTCDateTime` (Pydantic validator). Aware values are converted to UTC; naive values are interpreted as UTC. "Now" comes from an injectable `Clock`.
- **Consequences**: Evidence ordering is total and deterministic; freshness tests use `FixedClock`.

## ADR-007 — Phase order follows spec §48 dependency order
- **Status**: Accepted
- **Context**: Spec §35 (phase list) and §48 ("use this exact dependency order") disagree: §35 puts memory before evidence and active perception before task resumption.
- **Decision**: Follow §48. Phases: 0 foundations (steps 1–3) → 1 spatial persistence (4–6) → 2 evidence engine (7–8) → 3 memory core (9, plus basic procedural/task persistence) → 4 world diff (10) → 5 task continuity (11–12) → 6 grounded query agent (13–14) → 7 active perception + action safety (15, §16) → 8 perception adapter + web UI (16–17) → 9 ORBIT-BENCH (20). AR/VR (18–19) stay deferred, as §44/§49 require and §36 does not need them.
- **Consequences**: ROADMAP.md is re-ordered to match.

## ADR-008 — Initial epistemic status comes from source authority, entity status is the weakest link
- **Status**: Accepted (superseded in part by the Phase 2 evidence policy)
- **Context**: Phase 0 code marked every camera sighting `VERIFIED` (default authority 1.0 compared with `>=`), while attribute logic used `> 1.0`, which is unreachable.
- **Decision**: `VERIFIED` requires an authoritative source type *and* authority ≥ 0.9; perception yields `OBSERVED`. Entity status = weakest attribute status (`CONTRADICTED` > `STALE` > `UNKNOWN` > … ; `VERIFIED` only if all attributes are).
- **Consequences**: Entity-level status can no longer hide a stale or contradicted attribute.

## ADR-009 — Deterministic, conservative re-identification
- **Status**: Accepted (Phase 1)
- **Context**: Persistent identity (§2.2) must survive viewpoint changes and relocation, but wrongly merging two physical objects corrupts every later claim about both (§37 "same-looking objects", "object replaced").
- **Decision**: `EntityRegistry.resolve` applies, in order: explicit id (unless type/identifier contradicts) → strong identifier (`serial_number`, `asset_tag`, …) → new explicit id → stable-attribute signature, disambiguated by anchor-hierarchy proximity and geometry. An undecidable tie creates an `AMBIGUOUS` entity listing its candidates; contradicting identifiers create a `POSSIBLE_REPLACEMENT` entity. Nothing is merged on a guess. An entity matches at most one detection per observation. Every decision is persisted on `Observation.resolutions` for audit and for the persistence-accuracy metric.
- **Consequences**: Detectors without stable ids must omit `candidate_entity_id`; a supplied unknown id always means "new object". Merging an ambiguous entity into its true identity is future work (needs a verification flow).

## ADR-010 — Relations: explicit absence only, exclusive vs accumulating types
- **Status**: Accepted (Phase 1)
- **Decision**: Relations carry validity intervals. `on`/`inside`/`held_by` are exclusive (a new target closes the old interval); others accumulate; `connected_to`/`adjacent_to` are symmetric. A relation ends only on an explicit `present=False` observation — not seeing a connection is not evidence of disconnection (§2.7). Relation targets in an observation are mapped through that observation's identity resolutions.
- **Consequences**: `RELATION_CHANGED` events carry `{type: before}` → `{type: after}` and feed the world diff.

## ADR-011 — Deterministic evidence policy (supersede / corroborate / contradict / unconfirmed)
- **Status**: Accepted (Phase 2)
- **Context**: The Phase 0 engine overwrote state on any differing value, so a registry/label disagreement silently became a "state change" (violating §2.8), and weak or misleading evidence could replace strong evidence (§37).
- **Decision**: Every claim (from an observation, record, person or inference) passes through `EvidencePolicy.decide`, a pure function. Evidence has a `SourceType` mapped to a *channel* (DIRECT, RECORD, TESTIMONY, INFERENCE) and a strength = quality × authority × detection confidence. Rules, in order: no current claim → NEW; older than current support → UNCONFIRMED; manual verification → RESOLVE/CORROBORATE/SUPERSEDE; strength < 0.35 → UNCONFIRMED (or weak corroboration); inference never overrides an observation; same value → CORROBORATE; all current claims stale/invalidated/unknown → SUPERSEDE; during an open conflict → CONFLICT_UPDATE (a channel replaces only its own side; the conflict closes when one value remains); same source or same channel → SUPERSEDE, except different sources disagreeing within 30 s → CONTRADICT; cross-channel within the attribute's contradiction window → CONTRADICT; outside it, weaker → UNCONFIRMED, else SUPERSEDE.
- **Consequences**: Contradictions are explicit `Conflict` records with both sides retained; nothing is decided by model confidence. Unconfirmed claims are kept as zero-length versions with a reason (evidence is never discarded). `detect_contradictions=False` gives the last-writer-wins ablation baseline.

## ADR-012 — Freshness and STALE/CONTRADICTED are derived at read time
- **Status**: Accepted (Phase 2)
- **Context**: Phase 0 wrote STALE into stored versions (rewriting history) and never refreshed support on re-observation.
- **Decision**: `StateVersion.status` stores only the evidential grade at assertion. `ClaimEvaluator` derives STALE (TTL from the attribute's volatility policy, or explicit invalidation not followed by new support) and CONTRADICTED (conflict open at `as_of`) for any instant. Support refs carry timestamps so freshness is correct for past `as_of`. Entities keep a cached `current_state`/`attribute_statuses` view, refreshed at the latest known time.
- **Consequences**: "As of" questions are answerable; history is append-oriented; the cache is a convenience, never the gate. Only fresh OBSERVED/VERIFIED claims are `supportable`.

## ADR-013 — Invalidation propagation via explicit dependencies and policy triggers
- **Status**: Accepted (Phase 2)
- **Decision**: Claims are invalidated by interventions, changes or contradictions of claims they depend on. Dependencies are explicit `ClaimDependency` records (cross-entity) or `FreshnessPolicy.invalidation_triggers` (same entity, e.g. `calibration` ← `location`/`configuration`/`firmware`). Propagation is transitive with a visited-set cycle guard and emits `STATE_INVALIDATED` events. An intervention also closes open conflicts on the affected attributes.
- **Consequences**: Task steps (Phase 5) can depend on claims through the same mechanism.

## ADR-014 — Memory is a read model over structured state, not a separate store
- **Status**: Accepted (Phase 3)
- **Decision**: Temporal, spatial and episodic memory (`MemoryService`) are queries over versions, relations, events and sessions, each passed through the evidence gate *as of* the requested instant. A `WorldSnapshot` is the concrete B_t used by the diff (Phase 4) and the query agent (Phase 6). Spatial recall includes stale positions but labels them, so a caller cannot mistake memory for current fact.
- **Consequences**: No second copy of state to drift; the vector index (Phase 6) will index these records, not replace them.

## ADR-015 — Task progress and completion evidence are separate dimensions
- **Status**: Accepted (Phase 3)
- **Context**: `TaskStep.status` held an `EpistemicStatus`, conflating "is it done?" with "how do we know?".
- **Decision**: `StepStatus` (PENDING / IN_PROGRESS / COMPLETED / BLOCKED / NEEDS_REVERIFICATION / SKIPPED) tracks progress; `completion_status` holds the evidential grade of the completion (a person saying "done" is OBSERVED; VERIFIED needs verification or an authoritative record). Steps carry structured `preconditions` and `postconditions` (`StateCondition`). Every change emits a `TASK_PROGRESS_CHANGED` event with `task_id`, so task state is replayable. Migration 0004 converts old rows.

## ADR-016 — Causal hypotheses change only on qualifying causal-test evidence
- **Status**: Accepted (Phase 3)
- **Decision**: Hypotheses are created INFERRED/HYPOTHESIS; a cause after its effect is rejected. Evidence of `kind="causal_test"` from `TOOL_OUTPUT` or `MANUAL_VERIFICATION` with authority ≥ 0.9 may mark it SUPPORTED/REFUTED; all other evidence (co-occurrence, observations, statements) is retained as context only.
- **Consequences**: Enables the "unsupported causal claim rate" metric (Experiment E).

## ADR-017 — World diff is a net comparison of belief snapshots
- **Status**: Accepted (Phase 4); supersedes the Phase 0 event-log diff
- **Context**: Replaying events reports A→B→A as two moves, mislabels creation-in-window, cannot express confirmed absence, and misses task progress and unobserved objects.
- **Decision**: `WorldDiffService.diff(a, b)` compares `WorldSnapshot(a)` with `WorldSnapshot(b)`. Reported: OBJECT_ADDED (with identity/replacement notes), OBJECT_MOVED, OBJECT_STATE_CHANGED, PROCEDURE_REVISION_DETECTED, RELATION_CHANGED (exclusive retarget or add/remove), EVIDENCE_CONFLICT (newly contradicted), OBJECT_REMOVED_OR_UNOBSERVED (graded by `AbsenceStatus`), TASK_PROGRESS_CHANGED. Not reported as changes: first observation of an attribute (knowledge gain) and knowledge decay — stale/unconfirmed claims go to `WorldDiff.uncertain`. Replacement is only ever an INFERRED note. The event-log diff is kept as an ablation baseline (`mode="event_log"`).
- **Consequences**: On Experiment B-0 the snapshot diff scores P = R = 1.0; the event-log baseline scores P = 0.71, R = 0.62.

## ADR-018 — Absence requires validated search coverage of the last known location
- **Status**: Accepted (Phase 4)
- **Decision**: `SearchService.record_search` stores negative memory (`SearchCoverage`: region, coverage fraction, visibility, confidence, targets found / confirmed absent / inconclusive). A target is CONFIRMED_ABSENT only if it was searched for, its last known location lies inside the region, and `SearchPolicy` validates coverage (≥ 90 %, confidence ≥ 0.7, lighting not poor, occlusion ≤ 0.2). Confirmed absence closes the location claim (whereabouts UNKNOWN, reason retained) and emits OBJECT_REMOVED_OR_UNOBSERVED; nothing else changes belief. Ablations: `SearchPolicy(enabled=False)` and `WorldDiffService(treat_unobserved_as_removed=True)`.

## ADR-019 — Resume protocol and evidence-gated step readiness
- **Status**: Accepted (Phase 5)
- **Decision**: `TaskService.resume` implements spec §11 literally: (1) checkpoint = `last_verified_at` (else last interruption, else creation); (2) world diff since the checkpoint filtered to entities the task's conditions reference; (3) completed steps whose postconditions are now VIOLATED or CONTRADICTED become `NEEDS_REVERIFICATION`; (4) readiness = every *transitive* prerequisite step complete with still-SATISFIED postconditions, every precondition SATISFIED by fresh evidence of at least `min_status`, and the procedure revision unchanged; (5)–(6) every UNSUPPORTED condition yields one deduplicated `ObservationRequest` with an attribute-specific instruction; (7) the next step is a re-verification if one is ready, else the lowest-ordered ready step, else none. Stale-but-not-contradicted outcomes keep the step COMPLETED but block dependents. A changed procedure revision blocks all pending steps until `acknowledge_revision` (a human decision). `start_step` uses the same readiness check; `complete_step` refuses completions contradicted by world evidence and upgrades completion to VERIFIED when postconditions are verified.
- **Consequences**: A resume can never present a step with unsupported prerequisites (unsafe-continuation rate = 0 by construction, asserted in tests). Multi-user handoff is the same protocol with a different actor.

## ADR-020 — Evidence-bearing input models forbid unknown fields
- **Status**: Accepted (Phase 5)
- **Context**: A test passed `quality=` to an `ObservedEntity`; Pydantic ignored it silently, so the "weak" evidence was recorded at full strength.
- **Decision**: `Observation`, `ObservedEntity`, `ObservedRelation` and `StateCondition` use `extra="forbid"`.

## ADR-021 — Deterministic default providers behind replaceable boundaries
- **Status**: Accepted (Phase 6); refines ADR-002
- **Decision**: `providers/base.py` defines `EmbeddingProvider`, `RetrievalProvider` and `ReasoningProvider`. Defaults: `HashingEmbeddingProvider` (signed feature hashing over unigrams+bigrams with a stable digest — Python's `hash` is salted per process), `InMemoryVectorIndex` (cosine), `RuleBasedReasoningProvider` (intent + entity/anchor/attribute/time resolution against a vocabulary built from structured state). Every response records the provider names for reproducibility (spec §22.9).
- **Consequences**: The whole agent runs offline and deterministically; an LLM reasoning provider or pgvector retrieval can be swapped in without touching world state, evidence, diff or tasks. No LLM is in the loop today.

## ADR-022 — Grounded response contract: answer only what the evidence gate supports
- **Status**: Accepted (Phase 6)
- **Decision**: `QueryAgent.answer` returns `GroundedResponse` (spec §15): `answer` is non-null only when the underlying claims are supportable; otherwise `abstained=true`, a `summary` explains the last known value and why it cannot be asserted (stale, contradicted, confirmed absent, inferred, insufficient), conflicts are listed with all sides, and a targeted `requested_observation` is attached. Ambiguous mentions get a clarifying question, never a guess. "What changed?" defaults to the previous session; "Continue." runs the resume protocol; "Why?" never asserts causation without causal-test evidence. Semantic recall (`HybridRetriever`) only nominates records, which are re-read from structured state; similarity scores are exposed as recall aids. `gate_evidence=False` is the ungated (LLM-only-style) ablation.

## ADR-023 — Heuristic active perception with marginal information gain
- **Status**: Accepted (Phase 7)
- **Decision**: `ActivePerceptionPlanner` gathers uncertain claims from the evidence gate (weights: CONTRADICTED 1.0, UNKNOWN 0.9, STALE 0.7, INFERRED 0.6, below-task-requirement 0.5, AGING 0.3; ×2 when the claim blocks a task step) and generates candidates: LOOK_AT_ANCHOR (one view covers every claim in that region), INSPECT_ENTITY (close-up), SEARCH_REGION (whereabouts unknown) and VERIFY_WITH_PERSON (contradictions). `p_resolve(action, claim)` encodes what each look can settle (a wide view confirms location, not serial labels; only a person settles record-vs-label conflicts). Cost = time/60 + effort + motion + privacy + interruption, with privacy raised for anchors marked `privacy: high`. Expected reduction is **marginal**: each planned look reduces the residual uncertainty of the claims it covers by (1 − p), so overlapping views never double-count. `InformationGainPolicy` is greedy on marginal gain/cost; `FixedPolicy` and seeded `RandomPolicy` are the Experiment F baselines. The query agent ranks its requested observations with the same heuristic.
- **Consequences**: On the Phase 7 scene, three information-gain looks remove 2.89 of 3.10 units of uncertainty vs 1.93 for the fixed policy and 1.67–2.89 for random seeds. A learned policy can replace it behind `PerceptionPolicy`.

## ADR-024 — Action safety: human authorization and no actuator
- **Status**: Accepted (Phase 7)
- **Decision**: Consequential actions follow `ActionSafetyService`: propose (requires `recommend`) → prerequisites verified through the evidence gate (UNSUPPORTED ⇒ observation requests) → human authorization (requires `authorize`; prerequisites re-verified at that moment) → a human reports performing it (requires `actuate`) → outcome verified only with evidence *after* the action → `OutcomeRecord` (outcome memory: action, conditions, result, evidence). Principals are HUMAN or AGENT; agents can never hold `authorize`/`actuate`, and principals may be scoped to entities. Every transition emits an `ACTION_STATUS_CHANGED` audit event. There is no actuator interface; `execute_autonomously` (and `POST /actions/{id}/execute`) always refuses.
- **Consequences**: Satisfies §36 Safety: no autonomous actuation, an explicit authorization boundary, auditable claims.

## ADR-025 — Vendor-neutral perception adapters; hashes instead of pixels
- **Status**: Accepted (Phase 8)
- **Decision**: Devices emit `RawFrame`s; a `PerceptionProvider` returns an `Observation`. `DetectionPerceptionProvider` adapts any detector (label, normalised box, confidence, OCR text) — boxes map to anchors through a per-view calibrated `region_map`; OCR text yields strong identifiers (serial, asset tag) and attributes; detector track ids are deliberately *not* used as identities (ADR-009). `SimulatedPerceptionProvider` renders a ground-truth `SimulatedScene` with field of view, occlusion, seeded misses and optional anonymous detections, for ORBIT-BENCH. By default only the frame's content hash is stored (`retention_policy="hash_only"`); raw media is kept only with `retain_raw=True`. `PerceptionGateway.redact` removes the raw reference but keeps structured facts. Integrity v2 hashes a digest of the raw reference so redaction does not break verification (v1 records still verify).

## ADR-026 — Zero-build static inspection dashboard
- **Status**: Accepted (Phase 8); deviates from the §19 React/Next.js proposal
- **Context**: §31 asks for a simple dashboard that exposes uncertainty; §19 calls the stack a proposal, and §44 warns against building more than the experiment needs.
- **Decision**: `frontend/` holds a plain HTML/CSS/JS page served by FastAPI at `/ui/` (redirect from `/dashboard`), fed by one read model, `GET /inspect/summary`, plus `POST /queries`. All text is inserted via `textContent`. Every row shows epistemic status and freshness; confirmed-absent objects show no location, only the reason.
- **Consequences**: No Node toolchain to install or maintain. A React client can replace it against the same endpoints if a richer UI is ever needed.

## ADR-027 — ORBIT-BENCH: ground-truth scenarios, single-component ablations, count-based metrics
- **Status**: Accepted (Phase 9)
- **Decision**: Scenarios (`experiments/scenarios/catalog.py`) are deterministic scripts whose ground truth describes the true world — never ORBIT's output. `ScenarioRunner` (`backend/app/evaluation/bench.py`) executes each on a fresh in-memory store per variant; metrics accumulate as numerator/denominator counts and are micro-averaged across scenarios. Variants come from `OrbitConfig` switches so each removes exactly one component: freshness, contradiction detection, coverage policy (+ unobserved ⇒ removed), evidence gate, snapshot diff (→ event log), task graph (→ first unfinished step). Definitions: *stale-claim rate* = asserted current-state answers contradicting ground truth; *evidence-backed claim rate* = asserted claims passing the evidence gate with cited evidence; identity reports both persistence accuracy and false-merge rate (declining to merge look-alikes is not counted as a merge). Every report records git commit, schema revision, catalog version, provider ids and Python version (spec §22.10). `test_bench.py` asserts that full ORBIT meets ground truth and that every ablation degrades its target metric.
- **Consequences**: Research claims are regression-tested. Internal self-assessment alone is insufficient (with freshness disabled the system believes its stale claims are fine); ground truth is required.

## ADR-028 — World projection: B_T as a standalone, id-preserving copy
- **Status**: Accepted (Phase 10)
- **Decision**: `WorldProjector.project(T, target)` copies the source repository as it was known at T: records after T are dropped; version closures, invalidations, conflict resolutions, step completions, revision acknowledgements, interruption resumptions and action transitions after T are rolled back; version status is re-graded from the supports known at T (`grading.status_from_supports`, which replays the belief updater's grading rules); canonical identifiers and geometry are rebuilt from observations ≤ T; the materialised view is rebuilt at T. Record ids are preserved so a projection can be compared one-to-one with the source. A fidelity test requires `world_snapshot(T)`, task state, open conflicts and query answers to be identical in source and projection at every event instant of the flagship scenario.
- **Consequences**: Snapshot-copy was chosen over re-running inputs (event sourcing) because re-running would regenerate ids and diverge for generated entities.

## ADR-012 (amendment) — As-of reads use only evidence known at the time
- **Status**: Accepted (Phase 10)
- **Context**: The projection fidelity probe showed that historical assessments cited evidence that arrived later and reported statuses upgraded by later corroboration (e.g. VERIFIED at 09:00 for a claim only verified at 09:01).
- **Decision**: `ClaimEvaluator` derives the status at `as_of` from supports with `at ≤ as_of` and cites only that evidence; snapshot relations cite only evidence known at `as_of`.

## ADR-029 — Counterfactual premises are SIMULATION evidence, accepted only in sandboxes
- **Status**: Accepted (Phase 10)
- **Decision**: A sandbox is a projection into its own in-memory store with its own services and clock (`SandboxRegistry`, bounded, process-local). What-if premises (`Variation`: SET_ATTRIBUTE, MOVE, REMOVE, INVALIDATE, SET_RELATION, ADVANCE_TIME) are applied through the normal engine as `SourceType.SIMULATION` evidence with `provenance.counterfactual = true`; SIMULATION grades as verification-strength so the premise defines the sandbox world. The real engine raises `SimulationEvidenceRejected` (HTTP 403) for SIMULATION evidence, so hypotheticals cannot contaminate memory. `compare` evaluates the baseline (static replay of T) and the premise world at the same instant, except that ADVANCE_TIME is part of the premise.

## ADR-030 — Decision sensitivity analysis and Experiment H
- **Status**: Accepted (Phase 10)
- **Decision**: `sensitivity(task)` collects every claim the next decision rests on (preconditions of unfinished steps, postconditions of their completed ancestors, the procedure revision) and probes each in its own sandbox twice — *violated* and *unverified* — re-running the resume protocol. Claims whose failure changes the next step are decision-critical; those not yet VERIFIED become recommended checks. Experiment H (`evaluation/counterfactual_eval.py`) compares a static-replay policy with a counterfactual contingency policy over five hand-specified futures of the interrupted T12 task: decision quality 0.20 vs 1.00, unsafe pre-commitments 0.80 vs 0.00, and ORBIT's real resume in each future agrees with the ground truth.
- **Consequences**: Gives active perception a decision-centric priority signal ("check what would change the plan"), and the VR layer (spec §33) a backend to render. The futures are hand-specified; generalising requires a scenario generator.

## ADR-031 — Decision-aware active perception (value of information)
- **Status**: Accepted (Phase 11); extends ADR-023
- **Context**: Uncertainty-driven perception never looks at a *fresh* claim, yet a fresh but merely OBSERVED claim can be the only thing standing between the user and an unsafe step (the valve "closed" ten minutes ago may have been reopened). Value of information says observations are worth what they could change about a decision.
- **Decision**: Uncertain claims carry two weights: `weight` (uncertainty × task relevance, unchanged) and `decision_weight`. A claim is decision-critical when the next step of an open task rests on it (structural check over a non-mutating resume preview: the step's preconditions, its completed ancestors' postconditions, the procedure revision) or when a blocked step waits on it. Decision weight = uncertainty × 3 for critical claims (0.4 × 3 for fresh-but-unverified ones), uncertainty × 0.25 for claims no pending decision rests on. `DecisionAwarePolicy` is greedy on marginal decision value / cost; `InformationGainPolicy` is unchanged. Resume plans list `decision_critical` facts and `recommended_checks` (critical, supported, not VERIFIED); "Continue." keeps its answer and adds "Before you start, confirm: …". The dashboard ranks by decision value.
- **Consequences**: Experiment F2 — with the valve secretly reopened amid eight stale irrelevant objects: unsafe continuation 0.00 (decision-aware) vs 1.00 (information gain, fixed, random) at k = 1–3 looks; safe work never blocked; information gain refreshes more clutter after one look (8 vs 1), decision-awareness catches up by the second. The structural criticality set equals the Phase 10 sandbox sensitivity result on T12.

## ADR-032 — Non-mutating preview by rolled-back transaction
- **Status**: Accepted (Phase 11)
- **Decision**: `TaskService.preview` runs the real `resume` inside a repository transaction and always rolls it back, so preview and resume can never diverge. It refuses to run inside an already-open transaction (writes could not be discarded without savepoints). Exposed as `POST /tasks/{id}/preview`.

## ADR-033 — Risk-graded verification of prerequisites
- **Status**: Accepted (Phase 12)
- **Context**: A fact observed hours ago can still be "fresh" by its volatility TTL, which is adequate for routine steps but not for consequential ones (spec §2.10: verify prerequisites before acting). Phase 11 only *recommended* a check.
- **Decision**: Task steps and actions carry `RiskLevel` (LOW, MEDIUM default, HIGH). `RiskPolicy` is applied inside `ConditionEvaluator.check(condition, as_of, risk)`, so every readiness path (resume, start, preview, decision-critical facts, planner, action prerequisites) uses one rule. HIGH requires the prerequisite to hold on evidence that is *recent*: observed within 10 min or verified within 60 min. Verification lasts longer but also ages (a first draft exempted VERIFIED entirely; the tests showed that a two-camera corroboration hours old would then license a high-risk step indefinitely). A miss becomes UNSUPPORTED with `risk_shortfall=True`, producing a targeted observation request; the decision-aware planner ranks it first. A step's *own* outcome checks and the procedure revision are evaluated at the plain bar (fact detection, document state). Ablation: `OrbitConfig(risk_grading_enabled=False)`.
- **Consequences**: Bench scenario `high_risk_stale_premise`: full ORBIT blocks the cut and later re-verifies the reopened valve; without risk grading ORBIT offers the cut and answers "Is it safe…?" with "all prerequisites met" (unsafe continuation 0.00 → 0.20 across the catalog).

## ADR-034 — Explicit, recorded waivers; actions inherit step prerequisites
- **Status**: Accepted (Phase 12)
- **Decision**: An authorising human may approve an action despite *risk shortfalls* only by naming each one (`waive=["entity.attribute"]`) and giving a reason; the waiver is stored on `Authorization.waived` and in the audit event. Stale, unknown, contradicted and violated prerequisites can never be waived. Prerequisites are re-verified at authorisation: a request that waited past the window drops back unless waived. An action proposed for a task step without explicit prerequisites inherits the step's preconditions and its completed ancestors' postconditions, and the step's risk. The query agent answers "Is it safe to …?" with readiness at the step's risk bar, phrased as "all prerequisites ORBIT tracks … are met" plus a caveat that ORBIT does not authorise physical actions.

## ADR-035 — Identity merges re-derive belief by evidence replay, recorded bitemporally
- **Status**: Accepted (Phase 13); resolves the ADR-009 limitation "ambiguous entities cannot be merged"
- **Decision**: Only a human principal with the new `curate` scope may merge record S into entity T (with a reason), undo a merge, or confirm two records distinct; ORBIT only *suggests* (never applies) merges. A merge is refused if S and T were resolved in the same observation, if strong identifiers conflict, if types differ, or if a person confirmed them distinct. On merge, every claim (support), invalidation and confirmed absence of both records is replayed in time order through the normal `BeliefUpdater` in a silent mode (no historical events re-emitted, no propagation into other entities). Disagreements between the two histories therefore become conflicts, not silent overwrites. The original versions are *retired* (`retired_at`) and rebuilt versions/conflicts are *recorded* (`recorded_at`) at the merge instant; reads honour both, so every "as known at t" answer for t before the merge is unchanged, and the world projection rolls merges and undos back. S becomes an alias (`merged_into`): it is hidden from views from the merge on, its id resolves to T in re-identification, search and questions, and relations move to T. Undo replays the two original partitions; evidence that arrived after the merge stays with T.
- **Consequences**: Replay fidelity is tested — rebuilding any benchmark entity from its own evidence reproduces its belief exactly. The old version and merge records remain for audit.

## ADR-036 — An unresolved identity casts doubt on every candidate
- **Status**: Accepted (Phase 13)
- **Context**: Writing ground truth for the identity scenario showed that after an unattributable sighting of one of two identical bottles elsewhere, ORBIT still asserted both bottles' old locations — although one of them had demonstrably moved.
- **Decision**: Creating an `AMBIGUOUS` record invalidates the location claim of each candidate (reason tagged `ambiguous:<record>`). When a person resolves the ambiguity (merge or distinct), that doubt is lifted from the candidates that were not involved by rebuilding them without the tagged invalidation; undoing a merge re-applies it.

## ADR-037 — A validated search refutes only claims inside the searched region
- **Status**: Accepted (Phase 13); refines ADR-018
- **Decision**: `apply_absence` closes only location claims within the searched region (anchor containment). If another claim survives — e.g. one side of a location conflict — the conflict resolves in its favour instead of the object becoming "whereabouts unknown".

## ADR-038 — Generated worlds with bootstrap intervals; relocation needs evidence of leaving
- **Status**: Accepted (Phase 14); refines ADR-009 and the anchor proximity of Phase 1
- **Context**: Hand-written scenarios were authored with knowledge of ORBIT, so they regression-test the design but are weak evidence. Running ORBIT on randomly generated worlds immediately exposed two false-merge paths that no hand-written scenario had hit.
- **Decision (benchmark)**: `evaluation/generator.py` keeps a hidden true world per seed (families: scene, task, conflict) and derives every expectation from it; questions ORBIT's information cannot settle are `expect="either"` and judged only against the truth. Searches carry what the search frame saw; observations carry the camera's field of view; expected changes can name objects by ground-truth identity (`truth:<id>`). `evaluation/stats.py` reports ratio-of-sums metrics with 95 % percentile-bootstrap intervals over worlds and *paired* ablation differences (same worlds, joint resampling). A new metric, `progress_rate`, pairs with unsafe continuation so caution has a measured cost. Task-family effects need ≥ 40 worlds per family (one resume decision per world).
- **Decision (re-identification)**: (1) A single compatible candidate that is *not near* the sighting matches only if a look at its last known place within 10 min did not see it there (field of view or other detections there); otherwise the sighting becomes an `AMBIGUOUS` record with that one candidate, which casts doubt on it (ADR-036) and is offered as a merge suggestion. (2) Sibling anchors are "near" (0.6) only under a surface; two surfaces in a room are separate places (0.3).
- **Consequences**: Over 300 random scene worlds the false-merge rate fell to 0.5 % (25 / 4 996 detections). Every residual case is information-theoretically indistinguishable without identifiers (identical objects swapped, a look-alike replacing an object that vanished, a look-alike placed on the same spot) and occurs only with anonymous detections — the practical remedy is identifiers (serials, markers) on high-value objects. On 120 generated worlds every ablation is significantly worse on the metric its component protects; risk grading lowers unsafe continuation 0.28 → 0.10 with a non-significant progress cost (+0.07 [0.00, 0.16] without it).

## ADR-039 — Live camera: on-device detection, calibration as anchor frames, markers as identity
- **Status**: Accepted (Phase 15); builds on ADR-025 (vendor-neutral perception) and ADR-018/037 (validated absence)
- **Context**: First real sensor. A webcam produces noisy, flickering detections; frames show people; a fixed camera knows image coordinates, not places.
- **Decision**: Detection runs in the browser (COCO-SSD lite via TensorFlow.js); ORBIT receives only `Detection`s (type, confidence, normalised box, colour, optional marker) — never pixels. `person` is dropped in the browser *and* on the server. A client-side tracker turns flicker into stable objects (IoU match per label, ≥ 3 hits, colour by ≥ 60 % majority vote, otherwise omitted) and a snapshot is sent only when the stable scene's signature (type, colour, region, marker) changes, or every 60 s as a heartbeat that corroborates. Calibration is part of the world model: each drawn region is an anchor (`anchor_type="region"`, parent = the camera's view) whose `frame` holds `{"camera": id, "bbox": [...]}`; the view anchor holds `{"camera_view": id}`. Removing a region strips its calibration, not the place or memories about it. Every snapshot is an observation whose field of view is the whole view, so re-identification can use "the camera looked at the old place" (ADR-038). A QR tag `orbit:<id>` or `orbit:<type>:<id>` read on an object is used as its explicit entity id — the practical remedy for the irreducible look-alike confusions found in Phase 14; a typed tag with no detection around it becomes an object of its own (for classes COCO cannot see). Stopping the camera or losing sight of an object asserts nothing; only an explicit *scan* of a region is a search (coverage 0.95), so absence is confirmed only by a deliberate look and only inside that region.
- **Consequences**: Works offline after the first model download; no video leaves the device. Vocabulary is limited to COCO everyday classes — industrial objects need tags or a stronger detector behind the same `Detection` contract (the Quest 3S client will use the same endpoints). Live accuracy is not yet measured.

## ADR-040 — Conversational assistant: delegation inside ORBIT, deterministic consent
- **Status**: Accepted (Phase 16); applies spec §14 (orchestrator), §16 (action safety), §34 (replaceable providers)
- **Context**: The user wants an assistant — with a 3D avatar — that "does things on my behalf". ORBIT's golden rules forbid autonomous consequential actuation and letting a model decide truth.
- **Decision**: (1) *What it does alone*: everything whose evidence is the user's own word or ORBIT's own reasoning — answer questions (grounded query agent), record statements (USER_STATEMENT testimony with provenance: conversation, utterance, actor), report task steps (through the task service's readiness and postcondition checks), pause tasks, suggest checks. (2) *Statements vs interventions*: "I moved / opened / turned off X" reports something the user did — a known intervention (spec §10) that invalidates the old belief before the new value is recorded; "X is Y" is a plain statement, so disagreement with fresh evidence becomes an explicit conflict rather than an overwrite. (3) *Physical actions*: the assistant (principal `orbit-assistant`, agent scopes observe/reason/recommend) only proposes; prerequisites are checked; if — and only if — the speaker is a human holding `authorize`, it asks once, binding a confirmation to that action id for 2 minutes. The "yes" is recorded as that human's authorisation, citing the conversation. The human acts and says "done"; the outcome is verified only against evidence observed after the action. Waivers are not possible by voice. (4) *Language*: a rule-based command parser is the default; an LLM parser (Claude via tool use) is opt-in (`ORBIT_ASSISTANT_LLM=anthropic`) because it sends the workspace vocabulary off-device. Its output is validated against the vocabulary (no invented ids) and falls back to rules; consent ("yes"/"no") and "done" are always parsed deterministically and never sent to the model. (5) *Avatar*: a procedural three.js character driven only by the server's `gesture` and speech — it expresses ORBIT's epistemic state (THINK when abstaining, ALERT on conflicts/blocks, ASK when awaiting consent) instead of decorating it.
- **Consequences**: "What did you do for me?" is answered from recorded delegated acts. Conversation context is process-local and fails safe. Without API authentication the speaker id is trusted — acceptable on localhost only (see Known Issues).

## ADR-041 — Realtime: commit-time notifications, SSE with replay, proactive notices
- **Status**: Accepted (Phase 17)
- **Context**: The user wants everything in realtime. Views polled every 15 s and the assistant only spoke when spoken to, so a camera seeing the result of an authorised action went unnoticed until the user said "check again".
- **Decision**: (1) The repository reports events and observations *after commit*, once per transaction, in write order; rollbacks report nothing — so no view ever shows, and the assistant never reacts to, a change ORBIT did not keep. (2) An in-process `EventBus` numbers messages, keeps a 500-message replay buffer and bounded queues per subscriber (a slow tab loses its oldest messages and gets an `overflow` signal to resync; writers are never slowed). (3) Delivery uses Server-Sent Events (`GET /stream`): one-way, plain HTTP, built-in browser reconnect with `Last-Event-ID` resume — the browser already talks back over ordinary POSTs, so WebSockets would add complexity without benefit. (4) The assistant listens in the committing thread and reacts within the same evidence rules: an authorised action's expected outcome is checked against the newly committed evidence (verified/failed is announced; still-unverified is not), changes and conflicts for the user's current focus and task steps losing support are announced, the user's own turn is not re-announced, and repeats within 20 s are suppressed. Notices are scoped to their conversation. (5) Realtime is enabled for the served app only; benchmarks and counterfactual sandboxes build without it.
- **Consequences**: Inspector latency is commit + ~250 ms debounce; outcome verification is immediate. The bus is per process (single-worker deployment; a shared broker is needed to scale out). UI files are now served with `Cache-Control: no-cache` after a stale cached script hid the feature in testing.
