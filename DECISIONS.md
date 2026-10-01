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
