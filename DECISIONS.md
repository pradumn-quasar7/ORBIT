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
