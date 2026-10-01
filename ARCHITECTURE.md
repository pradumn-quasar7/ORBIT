# ORBIT Architecture

## 1. System Overview

ORBIT separates physical reality ($W_t$), observations ($O_t$), and belief state ($B_t$):
$$B_t = \text{Update}(B_{t-1}, O_t, \text{context}, \text{evidence}, \text{time})$$

```
┌───────────────────────┐
│   DEVICE ADAPTER      │ (laptop camera / phone / wearable / simulation)
└───────────┬───────────┘
            │ raw frames, sensor data
            ▼
┌───────────────────────┐
│  PERCEPTION GATEWAY   │ (pluggable PerceptionProvider)
└───────────┬───────────┘
            │ structured observations (candidate entities, labels, relations)
            ▼
┌───────────────────────────────────────┐
│        WORLD STATE ENGINE (CORE)      │
│  - Entity Registry & Re-ID            │
│  - State Transition & Versioning      │
│  - Evidence & Provenance Linkage      │
│  - Epistemic Status Policy            │
│  - Freshness & Invalidation Engine    │
│  - Longitudinal World Diff            │
└───────────────┬───────────────────────┘
                │
     ┌──────────┼──────────┐
     ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌───────────┐
│  MEMORY  │ │ EVIDENCE │ │ TASK STATE│
└────┬─────┘ └────┬─────┘ └────┬──────┘
     └────────────┬────────────┘
                  ▼
       ┌─────────────────────┐
       │  AGENT ORCHESTRATOR │ (grounded QA, active perception, resume protocol)
       └──────────┬──────────┘
                  ▼
          ┌───────────────┐
          │  CLIENT / UI  │
          └───────────────┘
```

## 2. Code Layout

```
backend/app/
  core/            time (UTCDateTime), clock, OrbitConfig + composition root (OrbitServices)
  domain/          Pydantic models, enums, weakest-link status aggregation
  repositories/    Repository ABC; InMemoryRepository; sql/ (tables + SqlRepository)
  providers/       replaceable boundaries: embedding, retrieval, reasoning, perception
  services/
    spatial.py            anchor hierarchy
    entity_registry.py    re-identification
    relations.py          relation intervals
    world_state_engine.py observation/claim/intervention orchestration
    evidence_policy.py    source typing, grading, integrity, decision table
    freshness.py          attribute-level freshness policies
    claims.py             evidence gate (ClaimEvaluator)
    belief.py             decision execution, conflicts, invalidation propagation, absence
    memory.py             temporal / spatial / episodic recall
    tasks.py              task graph, readiness, resume protocol
    conditions.py         pre/postcondition checks, observation instructions
    hypotheses.py         causal hypothesis memory
    search.py             negative search memory + coverage policy
    world_diff.py         Diff(B_a, B_b) + event-log baseline
    hybrid_retrieval.py   semantic recall over structured records
    query_agent.py        grounded query agent (spec §15 contract)
    active_perception.py  information-gain observation planning
    actions.py            action safety boundary + outcome memory
    perception_gateway.py frame ingestion, retention, redaction
    dashboard.py          inspection read model
    grading.py            status of a version from its supports (shared)
    projection.py         world as known at T (id-preserving copy)
    sandbox.py            fork a world into an isolated sandbox
    counterfactual.py     what-if premises, decision comparison, sensitivity analysis
    replay.py             frame-by-frame history
  evaluation/      metrics.py (P/R etc.), bench.py (ORBIT-BENCH runner), counterfactual_eval.py (Experiment H)
  api/             FastAPI routers (world, spatial, evidence, memory, queries, actions, inspect, counterfactual)
  main.py          create_app(repository, clock) factory; lazy module-level `app`
database/          Alembic migrations 0001–0007, migrate.py
experiments/       scenarios/catalog.py, runners/run_bench.py, results/
frontend/          static inspection dashboard served at /ui/
scripts/           seed_demo.py (flagship scenario)
```

Dependency direction: `api → services → repositories → domain`, with `core` usable
everywhere and `providers` behind interfaces. Services never import FastAPI; the
domain never imports services.

### Belief update path

```
Observation / claim / intervention
  → evidence record (typed source, strength, sha256 integrity)
  → EntityRegistry.resolve (identity)
  → per-attribute Claim → EvidencePolicy.decide (pure)
  → BeliefUpdater (versions, conflicts, invalidation propagation, events)
  → materialised entity view
Read side: ClaimEvaluator (freshness + conflicts + invalidation at any as_of)
  → MemoryService / WorldDiffService / TaskService / QueryAgent / ActivePerceptionPlanner
```

## 3. Core Domain Abstractions

1. **Entity**: A persistent physical or logical item with stable identity across sessions.
2. **Observation**: A single perceptual capture: detected entities, attributes, spatial context, timestamp, source.
3. **Evidence**: A provenance record with source type, quality, authority, retention, integrity.
4. **StateVersion**: Append-oriented attribute history with validity interval (`valid_from`, `valid_to`), epistemic status and supporting evidence.
5. **Event**: A typed state transition (`OBJECT_MOVED`, `OBJECT_STATE_CHANGED`, `OBJECT_ADDED`, `EVIDENCE_CONFLICT`, …).
6. **Relation**: A relationship between two entities (`on`, `connected_to`, `inside`) with validity interval.
7. **Task & TaskStep**: Work procedures with dependencies, preconditions and blocked reasons.
8. **WorldDiff**: Structured delta between two belief states.
9. **SearchCoverage**: Regions inspected, used to distinguish confirmed absence from "not observed".

## 4. Persistence

Structured relational storage is the source of truth (ADR-001, ADR-005). One table per
domain object (`entities`, `observations`, `evidence`, `state_versions`, `events`,
`relations`, `tasks`, `task_steps`, `world_diffs`, `search_coverage`). Scalars are
columns, nested value objects are JSON, timestamps are UTC. Each table has a `seq`
insertion counter for deterministic ordering on equal timestamps. Writes made by one
engine operation happen inside `repo.transaction()`.

## 5. Epistemic Statuses

- `OBSERVED`: Directly perceived in observation context.
- `VERIFIED`: Supported by authoritative evidence (authoritative source type and authority ≥ 0.9).
- `INFERRED`: Derived from rules, relationships, or reasoning.
- `STALE`: Previously valid, now expired or invalidated.
- `CONTRADICTED`: Conflicting evidence; conflict explicitly preserved.
- `UNKNOWN`: Insufficient evidence; prompts active perception or abstention.

Entity status is the weakest of its attribute statuses (ADR-008).
