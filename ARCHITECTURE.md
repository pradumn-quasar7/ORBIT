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
│  PERCEPTION GATEWAY   │ (pluggable PerceptionProvider: YOLO, OWLv2, VLM)
└───────────┬───────────┘
            │ structured observations (candidate entities, bounding boxes, labels)
            ▼
┌───────────────────────────────────────┐
│        WORLD STATE ENGINE (CORE)      │
│  - Entity Registry & Re-ID            │
│  - State Transition & Versioning      │
│  - Evidence & Provenance Linkage      │
│  - Epistemic Status Classifier        │
│  - Freshness & Invalidation Engine    │
│  - Longitudinal World Diff            │
└───────────────┬───────────────────────┘
                │
     ┌──────────┼──────────┐
     ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌───────────┐
│  MEMORY  │ │ EVIDENCE │ │ TASK STATE│
│ temporal │ │ freshness│ │ goals     │
│ spatial  │ │ conflict │ │ steps     │
│ episodic │ │ audits   │ │ blocked   │
└────┬─────┘ └────┬─────┘ └────┬──────┘
     └────────────┬────────────┘
                  ▼
       ┌─────────────────────┐
       │  AGENT ORCHESTRATOR │ (grounded QA, active perception, resume protocol)
       └──────────┬──────────┘
                  ▼
          ┌───────────────┐
          │  CLIENT / UI  │ (web dashboard, AR overlay, VR counterfactual)
          └───────────────┘
```

## 2. Core Domain Abstractions

1. **Entity**: A persistent physical or logical item in the workspace with persistent identity across sessions.
2. **Observation**: A single perceptual capture containing detected entities, attributes, spatial context, timestamp, and source.
3. **Evidence**: An immutable verification record with source type, provenance, authority, and quality.
4. **StateVersion**: An append-only log of entity attribute states, including temporal validity (`valid_from`, `valid_to`), epistemic status, and supporting evidence.
5. **Event**: A typed state transition (e.g., `OBJECT_MOVED`, `OBJECT_STATE_CHANGED`, `OBJECT_ADDED`, `EVIDENCE_CONFLICT`).
6. **Relation**: A directional or symmetrical relationship between two entities (e.g. `on`, `connected_to`, `contains`) with validity intervals.
7. **Task & TaskStep**: Work procedures with explicit step dependencies, prerequisite checks, and blocked reasons.
8. **WorldDiff**: Longitudinal delta between two world states or baseline vs current state.
9. **SearchCoverage**: Spatial regions inspected to validate absence vs unobserved state.

## 3. Epistemic Statuses

- `OBSERVED`: Directly perceived in observation context.
- `VERIFIED`: Supported by high-authority or multi-modal verified evidence.
- `INFERRED`: Derived from rules, relationships, or reasoning.
- `STALE`: Previously valid, now expired or invalidated by dependency change.
- `CONTRADICTED`: Conflicting evidence sources disagree; conflict explicitly preserved.
- `UNKNOWN`: Insufficient evidence; prompts active perception or abstention.
