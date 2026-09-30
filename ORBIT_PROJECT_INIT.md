# ORBIT — Project Initiation & Engineering Specification

**Project:** ORBIT — Persistent World Model Agent  
**Research status:** Proposed research program / prototype  
**Primary goal:** Build a persistent, evidence-aware, temporally evolving model of a physical workspace and its ongoing tasks.  
**Initial hardware assumption:** No Meta/AR glasses required. Begin with controlled observations and laptop/phone camera input later.  
**Document purpose:** This file is the working source of truth for the engineering agent. It translates the ORBIT research dossier into an implementation-ready project plan while preserving the research intent.

---

# 0. START HERE — INSTRUCTIONS FOR THE ENGINEERING AGENT

You are the implementation agent for ORBIT.

Before writing production code:

1. Read this entire file.
2. Read `docs/research/ORBIT_RESEARCH_DOSSIER.pdf` if it exists.
3. Read `AGENTS.md`, `PROJECT_STATUS.md`, `ARCHITECTURE.md`, `DECISIONS.md`, and `ROADMAP.md` if they exist.
4. Inspect the repository before changing anything.
5. Do not replace the research architecture with a generic chatbot/RAG architecture.
6. Do not begin with AR glasses, VR, autonomous physical control, fine-tuning, or a large-scale dataset.
7. Build the smallest testable core first.
8. Keep perception, retrieval, reasoning, and simulation replaceable.
9. Treat structured world state and evidence as the source of truth. The language model is not the source of truth.
10. Do not silently turn uncertainty into certainty.
11. Every major feature must have tests and a clear failure mode.
12. Do not make unrelated refactors.
13. Record important architectural decisions in `DECISIONS.md`.
14. Keep `PROJECT_STATUS.md` updated after meaningful milestones.
15. Work incrementally: inspect → plan → implement → test → document.

## First task

Do NOT immediately build the full application.

First produce a repository audit and implementation plan covering:

- current files and technology state;
- missing project structure;
- the minimum data model for ORBIT v0.1;
- the dependency order for implementation;
- proposed database schema;
- API boundaries;
- tests for the first milestone;
- risks and assumptions.

Then implement **Phase 0 / World State Engine foundations** only after the plan is clear.

The first working milestone is:

> Create entities → record observations → update state → store evidence/history → retrieve current state → compute a structured world diff.

---

# 1. WHAT ORBIT IS

## One-sentence definition

ORBIT is a persistent world-model agent that maintains an evidence-aware, temporally evolving representation of a physical workspace and its ongoing tasks, detects what changed, knows what it does not know, helps humans resume work, and uses AR/VR for grounded inspection and controlled simulation.

## Research question

Can an AI agent maintain a reliable longitudinal model of a person's physical environment and ongoing work across repeated observations and interruptions while explicitly tracking evidence, freshness, uncertainty, and task state, and can that model measurably improve continuity and decision quality?

## Central abstraction

ORBIT is about **world memory**, not merely user/chat memory.

The system must represent:

- physical entities;
- persistent identity;
- spatial relationships;
- current state;
- state history;
- events;
- evidence;
- freshness;
- uncertainty / epistemic status;
- tasks;
- task dependencies;
- progress;
- search coverage;
- outcomes;
- contradictions.

---

# 2. RESEARCH PRINCIPLES — NON-NEGOTIABLE

## 2.1 Longitudinal state

ORBIT must reason across multiple sessions, not isolated frames.

## 2.2 Persistent spatial identity

The same physical object should retain a stable identity across:

- viewpoint changes;
- partial observations;
- relocation relative to anchors;
- repeated sessions.

## 2.3 Epistemic status

Every important claim/state must distinguish:

- `OBSERVED`
- `VERIFIED`
- `INFERRED`
- `STALE`
- `CONTRADICTED`
- `UNKNOWN`

### Meaning

| Status | Meaning | Behavior |
|---|---|---|
| OBSERVED | Directly perceived in a defined context | Use with timestamp/freshness constraints |
| VERIFIED | Supported by strong or authoritative evidence | Higher-confidence guidance within scope |
| INFERRED | Reasoned from evidence, not directly observed | Clearly label as inference |
| STALE | Previously supported but now too old or invalidated | Do not use for current-state claims without refresh |
| CONTRADICTED | Relevant sources disagree | Surface conflict; do not silently choose |
| UNKNOWN | Evidence is insufficient | Ask, observe, or abstain |

## 2.4 State is not truth

The physical world `W_t` and ORBIT's belief state `B_t` are different.

Conceptually:

```text
W_t = latent physical world
O_t = observations
B_t = ORBIT belief/world-state representation

B_t = Update(B_(t-1), O_t, context, evidence, time)
```

Never interpret model confidence as physical-world truth.

## 2.5 Evidence is first-class

Important claims must be traceable to evidence.

A response should be grounded in:

- evidence reference;
- evidence timestamp;
- epistemic status;
- freshness;
- unresolved contradictions;
- requested next observation when needed.

## 2.6 Freshness is contextual

Freshness is attribute-dependent.

Examples:

```text
wall_color       → low volatility → long validity
machine_power    → high volatility → short validity
procedure_revision → may invalidate dependent task claims
```

Conceptual model:

```text
freshness(entity, attribute)
    = f(age, volatility, interventions, contradiction, validation_cost)
```

Do not use one global TTL for everything.

## 2.7 Unknown is not absent

If an object is outside the visible/search coverage:

```text
NOT OBSERVED
```

must not automatically become:

```text
REMOVED
```

A stronger absence claim requires sufficient coverage and a defined search policy.

## 2.8 Contradictions are retained

Example:

```text
Digital registry → configuration R6
Visual label     → configuration R7
```

Expected behavior:

```text
state = CONTRADICTED

retain both evidence items
do not silently pick one because an LLM sounds confident
request verification
```

## 2.9 Causality is conservative

Co-occurring events do not prove causality.

Example:

```text
firmware changed
motor failed later
```

ORBIT may store:

```text
causal_hypothesis:
"firmware may explain motor failure"
```

but must not represent it as a fact without evidence.

## 2.10 Action safety

For consequential physical actions:

```text
observe
→ verify prerequisites
→ authorize
→ act
→ verify outcome
```

The first prototype is not an autonomous high-consequence physical controller.

---

# 3. WHAT ORBIT IS NOT

ORBIT is not:

- a generic voice assistant with an AR overlay;
- a vector database rebranded as memory;
- a static digital twin;
- a system that treats absence of observation as proof of removal;
- a first-release autonomous controller for high-consequence physical actions;
- a system whose novelty depends on claiming every individual component is new.

Novelty remains a research hypothesis until appropriate prior-art review is complete.

---

# 4. FLAGSHIP USER EXPERIENCE

## Session A

A user works at a physical workspace.

Example:

```text
Bench B3
├── Microscope M17
├── Cable C4
├── Tool T1
└── Notebook N1
```

ORBIT observes:

```text
M17 → Bench B3
C4  → connected to M17
M17 configuration → R6
Task T12 → step 6 / 9
Isolation → VERIFIED
```

The user leaves.

## Between sessions

The world changes:

```text
M17 moved
C4 replaced
procedure revision changed
```

## Session B

User:

> What changed?

ORBIT returns only changes supported by evidence.

User:

> Continue.

ORBIT:

1. loads last verified task state;
2. loads relevant changes since that state;
3. invalidates affected steps;
4. checks freshness;
5. requests targeted observations where evidence is insufficient;
6. only then presents the next supported step.

This is the core longitudinal continuity problem.

---

# 5. REFERENCE SYSTEM ARCHITECTURE

## 5.1 Clean architecture

```text
┌───────────────────────┐
│   USER / WEARABLE I/O │
│ camera • voice • IMU  │
│ gaze • hands • pose   │
└───────────┬───────────┘
            │ observations
            ▼
┌───────────────────────┐
│   PERCEPTION GATEWAY  │
│ objects • text        │
│ events • pose         │
│ spatial relationships │
└───────────┬───────────┘
            │ observations
            ▼
┌───────────────────────────────────────┐
│              WORLD STATE              │
│ persistent entities + relations       │
│ current state + state history          │
│ spatial + temporal context             │
└───────────────┬───────────────────────┘
                │
        ┌───────┼────────┐
        ▼       ▼        ▼
┌──────────┐ ┌──────────┐ ┌───────────┐
│ MEMORY   │ │ EVIDENCE │ │ TASK STATE│
│ temporal │ │ freshness│ │ goals     │
│ spatial  │ │ provenance││ steps     │
│ episodic │ │ conflict │ │ deps      │
│ procedural││           ││ outcomes  │
└────┬─────┘ └────┬─────┘ └────┬──────┘
     └─────────────┼─────────────┘
                   ▼
        ┌─────────────────────┐
        │ AGENT ORCHESTRATOR  │
        │ retrieve / reason   │
        │ observe / verify    │
        │ act / synthesize    │
        └─────────┬───────────┘
                  │
             ┌────┴────┐
             ▼         ▼
       ┌──────────┐ ┌──────────┐
       │    AR    │ │    VR    │
       │ in-situ  │ │ replay / │
       │ guidance │ │ what-if  │
       └──────────┘ └──────────┘
```

## 5.2 Core control loop

```text
USER QUERY
   ↓
Interpret query
   ↓
Identify required claims
   ↓
Retrieve world/task/evidence memory
   ↓
Check freshness
   ├── stale → refresh / abstain
   ├── conflict → surface contradiction
   ├── insufficient → active perception
   └── sufficient → continue
   ↓
Generate grounded response
   ↓
Include evidence + freshness + epistemic status
   ↓
Physical action requested?
   ├── NO → finish
   └── YES
        ↓
      verify prerequisites
        ↓
      human authorization
        ↓
      act
        ↓
      verify outcome
        ↓
      write outcome memory
```

---

# 6. COMPONENT RESPONSIBILITIES

## 6.1 Device Adapter

Purpose:
Normalize input from physical devices.

## 6.2 Perception Gateway

Purpose:
Convert raw observations into structured observations.

## 6.3 Entity Registry

Purpose:
Maintain durable identity for important physical entities.

## 6.4 World State Engine

This is the initial research core.

Responsibilities:
- take observations;
- identify matching entities;
- compare previous state with new observations;
- update current state;
- preserve state history;
- create events;
- maintain relations;
- apply epistemic status;
- trigger freshness/invalidation effects.

The world state engine must be deterministic where possible.

---

# 7. MEMORY ARCHITECTURE

Memory is a system, not just a vector store.
ORBIT combines structured state with semantic retrieval.

---

# 8. HYBRID RETRIEVAL

Semantic retrieval = recall relevant memories
Structured world state = determine current structured reality belief
Evidence + freshness = decide whether a claim is supportable

---

# 9. EVIDENCE MODEL

Minimum evidence fields:
- evidence_id
- source_type
- source_reference
- timestamp
- quality
- provenance
- observation_context
- authority_level

---

# 10. FRESHNESS + INVALIDATION

For each state attribute, store enough information to calculate validity:
- last_supported_at
- freshness_policy
- volatility_class
- invalidated_at
- invalidation_reason
- last_validated_at

---

# 11. TASK CONTINUITY

Tasks are first-class state.

---

# 12. WORLD DIFF

Supported change types:
- OBJECT_MOVED
- OBJECT_STATE_CHANGED
- OBJECT_ADDED
- OBJECT_REMOVED_OR_UNOBSERVED
- RELATION_CHANGED
- TASK_PROGRESS_CHANGED
- PROCEDURE_REVISION_DETECTED
- EVIDENCE_CONFLICT

---

# 13. ACTIVE PERCEPTION

When evidence is insufficient, ORBIT seeks useful observations instead of guessing.

---

# 14. AGENT ORCHESTRATOR

Coordinates retrieval, reasoning, evidence checks, verification, and grounded response.

---

# 15. RESPONSE CONTRACT

Semantic contract with claims, statuses, freshness, evidence refs, conflicts, requested observations.

---

# 16. ACTION SAFETY

Observe → Verify → Authorize → Act → Verify → Record.

---

# 17. PRIVACY + SECURITY

Store useful structured state and compact evidence references; retain raw video only when necessary.

---

# 18. INITIAL MVP — NO META HARDWARE

Controlled scene of 5–10 objects, desk/workbench, Session A vs Session B.

---

# 19. PROPOSED MVP TECHNOLOGY STACK

Backend: Python, FastAPI, Pydantic, SQLAlchemy.
Database: Relational/structured storage.
Frontend: React / Next.js dashboard.
Replaceable providers.

---

# 20. DEVELOPMENT PHASES

- Phase 0 — Framing + repository & World State Engine foundations
- Phase 1 — Spatial persistence
- Phase 2 — Memory core
- Phase 3 — Evidence engine
- Phase 4 — World diff
- Phase 5 — Agentic perception
- Phase 6 — Task resumption
- Phase 7 — VR counterfactuals
- Phase 8 — Wearable/general client
- Phase 9 — Pilot + paper
