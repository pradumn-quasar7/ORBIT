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
