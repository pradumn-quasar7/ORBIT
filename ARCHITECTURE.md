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
  core/           time (UTCDateTime), clock, composition root (OrbitServices)
  domain/         Pydantic models, enums, deterministic status rules
  repositories/   Repository ABC; InMemoryRepository; sql/ (tables + SqlRepository)
  services/       world_state_engine (and, per phase, registry/evidence/memory/diff/tasks/…)
  api/            FastAPI routers; dependencies resolve OrbitServices from app.state
  main.py         create_app(repository, clock) factory; lazy module-level `app`
database/
  migrations/     Alembic env + versions (schema source of change)
  migrate.py      upgrade_to_head(url) used by the app on start
docs/experiments/ BENCHMARK_PROTOCOL.md
```

Dependency direction: `api → services → repositories → domain`, with `core` usable
everywhere. Services never import FastAPI; domain never imports services.

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
