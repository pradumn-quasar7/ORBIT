# ORBIT Development Roadmap

- [x] **Phase 0 — Framing, Repository & World State Engine Foundations**
  - Project documentation, ADRs, coding guidelines.
  - Domain models: Entity, Observation, Evidence, StateVersion, Event, Relation, Task, TaskStep, WorldDiff, SearchCoverage.
  - Epistemic status tracking (`OBSERVED`, `VERIFIED`, `INFERRED`, `STALE`, `CONTRADICTED`, `UNKNOWN`).
  - Core World State Engine: observation ingestion, entity persistence, state transition, evidence provenance, movement detection (`OBJECT_MOVED`), structured world diff.
  - Comprehensive automated tests for acceptance criteria.

- [ ] **Phase 1 — Spatial Persistence & Entity Re-Identification**
  - Spatial anchors, coordinate frames, bounding boxes.
  - Entity re-identification heuristics (labels, visual embeddings, spatial proximity).
  - Multi-session object identity preservation.

- [ ] **Phase 2 — Memory Core (Temporal, Spatial, Episodic, Procedural)**
  - Time-series queries ("Where was M17 at 10:00 AM?").
  - Spatial queries ("What is on Bench B3?").
  - Episodic session indexing and timeline generation.

- [ ] **Phase 3 — Evidence Engine & Contextual Freshness**
  - Attribute-specific volatility classes (low, medium, high).
  - Time-to-live and condition-based invalidation rules.
  - Contradiction resolution workflows.

- [ ] **Phase 4 — Longitudinal World Diff & Change Detection**
  - Deep diff between arbitrary timestamps / sessions.
  - Typed change events: `OBJECT_MOVED`, `OBJECT_STATE_CHANGED`, `OBJECT_ADDED`, `OBJECT_REMOVED_OR_UNOBSERVED`, `RELATION_CHANGED`.
  - Negative search / coverage tracking.

- [ ] **Phase 5 — Agentic Active Perception**
  - Information gain heuristic.
  - Targeted observation prompts ("Point camera at pump label").
  - Cost vs uncertainty optimization.

- [ ] **Phase 6 — Task Resumption & Safety Boundary**
  - Task state machine and dependency graph.
  - Invalidation of steps when prerequisites change.
  - Human authorization gates for actions.

- [ ] **Phase 7 — Grounded Query Agent & Hybrid Retrieval**
  - Structured state lookup + vector search hybrid.
  - Grounded response contract with claims, freshness, and evidence citations.

- [ ] **Phase 8 — Web Inspection Dashboard**
  - Visual display of current world state, change log, task progress, and evidence.

- [ ] **Phase 9 — Benchmarking (ORBIT-BENCH) & Ablations**
  - Controlled dataset and evaluation runners.
  - Ablation matrix.
