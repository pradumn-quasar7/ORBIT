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

Inputs may include:

- camera frames;
- audio;
- IMU;
- pose;
- gaze;
- hand interaction;
- spatial anchors.

Important design rule:

> ORBIT must not depend on one specific hardware vendor.

Initial implementation may be:

```text
Laptop webcam / uploaded frames / simulated observations
```

A future wearable adapter can replace this input layer.

---

## 6.2 Perception Gateway

Purpose:

Convert raw observations into structured observations.

Responsibilities:

- object detection;
- object attributes;
- text / label extraction;
- event detection;
- spatial relationships;
- pose / anchor context.

The perception layer produces **observations**, not final truth.

Conceptual output:

```json
{
  "observation_id": "obs_001",
  "timestamp": "2026-09-30T10:00:00Z",
  "source": "camera",
  "entities": [
    {
      "candidate_entity_id": "bottle_01",
      "type": "bottle",
      "attributes": {
        "color": "blue"
      }
    }
  ],
  "spatial_context": {
    "anchor": "desk_01"
  }
}
```

---

## 6.3 Entity Registry

Purpose:

Maintain durable identity for important physical entities.

Entity should support:

- `entity_id`;
- type;
- canonical attributes;
- anchors;
- geometry;
- current state;
- status;
- observed time;
- freshness policy;
- evidence references;
- history references;
- permissions;
- relations.

Example:

```json
{
  "entity_id": "asset:m17",
  "type": "equipment",
  "anchors": ["lab204", "bench3"],
  "geometry": {
    "position": {},
    "orientation": {},
    "bounds": {}
  },
  "current_state": {
    "configuration": "R6",
    "power": "unknown"
  },
  "status": "VERIFIED",
  "observed_at": "2026-09-26T09:10:00+05:30",
  "freshness_policy": "revalidate_after_configuration_change",
  "evidence_refs": ["camera_obs_81", "asset_record_22"],
  "history_refs": ["entity_history:m17"],
  "relations": [
    {"type": "on", "target": "bench3"},
    {"type": "connected", "target": "cable_c4"}
  ]
}
```

---

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

### First milestone

```text
Create Entity
→ Record Observation
→ Re-identify entity
→ Update State
→ Store Evidence
→ Store History
→ Read Current State
```

---

# 7. MEMORY ARCHITECTURE

Memory is a system, not just a vector store.

ORBIT combines structured state with semantic retrieval.

## 7.1 Memory types

### Episodic memory

Example:

```text
Session 7: pump inspected
```

Properties:

- time-indexed;
- evidence-linked;
- session-aware.

### Spatial memory

Example:

```text
M17 is on Bench 3
```

Properties:

- anchor-relative;
- uncertainty bounds;
- validity interval.

### Temporal memory

Example:

```text
Firmware changed at 14:31
```

Properties:

- immutable event;
- source;
- timestamp.

### Entity state memory

Example:

```text
M17 configuration = R6
```

Properties:

- versioned;
- revalidation rules;
- current vs historical state.

### Procedural memory

Example:

```text
Task T12 step 6 complete
```

Properties:

- dependencies;
- prerequisites;
- completion state.

### Causal hypothesis memory

Example:

```text
Firmware may explain motor failure
```

Properties:

- always treated as hypothesis unless supported.

### Negative/search memory

Example:

```text
Shelf A searched at 14:32;
item not found in validated coverage
```

Properties:

- coverage;
- time;
- visibility conditions;
- search policy.

### Outcome memory

Example:

```text
Action A under conditions C produced outcome O
```

Used for later planning and analysis.

---

# 8. HYBRID RETRIEVAL

Semantic retrieval is useful for finding conceptually relevant memories.

Structured retrieval is required for exact current-world reasoning.

## Example

User:

> Where is M17 right now?

Do not rely on vector similarity alone.

Retrieve:

```text
Entity: M17
Current state
Current relations
Latest valid location
Freshness
Evidence
Contradictions
```

For a broader query:

> What happened to the pump yesterday?

Semantic retrieval may locate:

- pump-related events;
- inspection sessions;
- task steps;
- relevant observations;
- procedural history.

Then the agent combines semantic recall with structured state/evidence checks.

### Principle

```text
Semantic retrieval = recall relevant memories
Structured world state = determine current structured reality belief
Evidence + freshness = decide whether a claim is supportable
```

Never treat an embedding similarity score as truth.

---

# 9. EVIDENCE MODEL

Every important state transition should retain provenance.

Minimum evidence fields:

```text
evidence_id
source_type
source_reference
timestamp
quality
provenance
observation_context
authority_level
```

Possible source types:

```text
visual observation
audio observation
user statement
external authoritative record
manual/procedure
system event
tool output
```

## Evidence precedence

Do not hard-code:

> "whichever model is more confident wins."

Instead:

- retain all relevant evidence;
- evaluate authority and freshness;
- detect contradictions;
- ask for verification when needed.

---

# 10. FRESHNESS + INVALIDATION

Freshness is not simply a database expiry timer.

For each state attribute, store enough information to calculate validity.

Suggested fields:

```text
last_supported_at
freshness_policy
volatility_class
invalidated_at
invalidation_reason
last_validated_at
```

Example:

```text
M17.location
→ medium volatility

M17.power
→ high volatility

procedure_revision
→ may invalidate dependent task claims
```

## Invalidation triggers

Potential triggers include:

- explicit new observation;
- known intervention;
- procedure revision;
- conflicting evidence;
- state transition;
- excessive age;
- change in dependency;
- external record update.

For the MVP, implement a deterministic policy engine rather than a learned freshness model.

---

# 11. TASK CONTINUITY

Tasks are first-class state.

Do not store task progress only inside conversation history.

## Task graph

A task should support:

```text
Goal
Steps
Dependencies
Preconditions
Observations
Completion state
Blocked state
Interruptions
Outcomes
```

A linear checklist is acceptable for MVP.

The internal model should still allow future branching.

Example:

```text
TASK T12
Goal: Replace coolant pump

Step 5 → isolate system
         VERIFIED

Step 6 → remove old pump
         VERIFIED

Step 7 → install new pump
         BLOCKED
         dependency: verify model number
         evidence: STALE

Step 8 → leak test
         PENDING
```

## Resume algorithm

```text
1. Load last verified task state.
2. Load relevant world changes since that state.
3. Invalidate affected steps.
4. Check dependencies and prerequisites.
5. Identify unresolved evidence.
6. Request targeted observations.
7. Only then present the next supported step.
```

---

# 12. WORLD DIFF

ORBIT must compute structured world differences.

Concept:

```text
D = Diff(B_a, B_b)
```

Supported change types:

```text
OBJECT_MOVED
OBJECT_STATE_CHANGED
OBJECT_ADDED
OBJECT_REMOVED_OR_UNOBSERVED
RELATION_CHANGED
TASK_PROGRESS_CHANGED
PROCEDURE_REVISION_DETECTED
EVIDENCE_CONFLICT
```

## Important distinction

Do not turn:

```text
object not visible
```

into:

```text
object removed
```

unless search/visibility coverage supports the stronger conclusion.

---

# 13. ACTIVE PERCEPTION

When evidence is insufficient, ORBIT should seek a useful observation instead of guessing.

Examples:

```text
"Look at the label on the pump."

"Turn the camera toward the rear connector."

"Move closer so I can verify the serial marking."
```

For MVP, active perception can be heuristic.

Conceptual score:

```text
score(action)
=
expected_uncertainty_reduction(action)
/
observation_cost(action)
```

Cost may consider:

- time;
- user effort;
- motion;
- privacy exposure;
- interruption risk.

Keep this as a replaceable policy module so a learned policy can be evaluated later.

---

# 14. AGENT ORCHESTRATOR

The orchestrator is the coordinator, not the database.

Responsibilities:

- interpret user query;
- identify required claims;
- retrieve relevant state and memory;
- perform freshness/evidence checks;
- trigger active perception;
- build grounded response;
- request verification;
- route physical actions through authorization;
- record outcomes.

The LLM may help with:

- language understanding;
- structured reasoning;
- response synthesis;
- candidate action selection.

The LLM must not become the source of truth.

---

# 15. RESPONSE CONTRACT

A grounded response should conceptually contain:

```json
{
  "answer": "...",
  "claims": [
    {
      "claim": "...",
      "status": "VERIFIED",
      "freshness": {
        "state": "FRESH",
        "last_supported_at": "..."
      },
      "evidence_refs": ["..."]
    }
  ],
  "conflicts": [],
  "requested_observation": null
}
```

For uncertainty:

```json
{
  "answer": null,
  "claims": [],
  "conflicts": [],
  "requested_observation": {
    "reason": "configuration evidence is stale",
    "instruction": "Show the model label on the replacement pump."
  }
}
```

The exact API shape may evolve, but the semantic contract should remain.

---

# 16. ACTION SAFETY

All consequential physical actions must follow:

```text
Observe
   ↓
Verify prerequisites
   ↓
Authorize
   ↓
Act
   ↓
Verify outcome
   ↓
Record outcome
```

Permissions should be scoped independently from reasoning permissions.

Example:

```text
Reasoning permission:
may recommend "connect cable C4"

Action permission:
may not physically actuate a machine
```

Do not build autonomous high-consequence control into the MVP.

---

# 17. PRIVACY + SECURITY

## Privacy

Default principle:

> Store useful structured state and compact evidence references; retain raw video only when necessary.

Required concepts:

- retention policy;
- deletion;
- access control;
- evidence minimization;
- possible no-identity mode;
- selective redaction;
- multi-user workspace separation.

## Security threat model

Consider:

- malicious memory writes;
- poisoned external records;
- stolen credentials;
- unauthorized workspace access;
- adversarial visual inputs.

Memory records need integrity/provenance.

---

# 18. INITIAL MVP — NO META HARDWARE

The first implementation does not require AR glasses.

## Controlled scene

Start with a desk/workbench containing approximately 5–10 objects.

Example:

```text
Desk
├── Laptop
├── Phone
├── Bottle
├── Notebook
└── Cable
```

Later expand toward the research dossier's controlled-scene target of approximately 10–20 persistent objects.

## Session A

```text
Observe scene
Create entities
Record relationships
Record task state
Store evidence
```

## Change the scene manually

```text
Move cable
Move bottle
Replace notebook
Change task prerequisite
```

## Session B

Ask:

```text
What changed?
```

Then:

```text
Continue.
```

ORBIT must:

- preserve entity identities;
- calculate world diff;
- distinguish unknown from removed;
- identify stale state;
- preserve evidence;
- identify affected task steps;
- request a targeted observation where required;
- produce a grounded answer.

This is the first true ORBIT experiment.

---

# 19. PROPOSED MVP TECHNOLOGY STACK

These are **engineering choices for the prototype**, not claims from the research dossier.

## Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy or equivalent typed DB layer
```

## Database

```text
PostgreSQL
```

Use relational/structured storage as the primary source of truth.

Use vector search as a secondary retrieval mechanism.

If convenient:

```text
pgvector
```

can live alongside PostgreSQL rather than creating a separate vector database.

## Frontend

For the first MVP:

```text
React / Next.js
```

A simple dashboard is enough.

## Perception

Keep behind an adapter:

```text
PerceptionProvider
```

so different models can later be swapped.

## LLM/reasoning

Keep behind:

```text
ReasoningProvider
```

The world-state and evidence system must remain independent of a specific provider.

## AR

Later.

## VR

Later.

---

# 20. PROPOSED REPOSITORY STRUCTURE

```text
orbit/
│
├── README.md
├── AGENTS.md
├── PROJECT_STATUS.md
├── ARCHITECTURE.md
├── DECISIONS.md
├── ROADMAP.md
├── CONTRIBUTING.md
├── .gitignore
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── domain/
│   │   ├── services/
│   │   ├── providers/
│   │   ├── repositories/
│   │   └── main.py
│   │
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── package.json
│
├── database/
│   ├── migrations/
│   └── seed/
│
├── experiments/
│   ├── scenarios/
│   ├── datasets/
│   ├── runners/
│   └── evaluation/
│
├── docs/
│   ├── research/
│   │   └── ORBIT_RESEARCH_DOSSIER.pdf
│   ├── architecture/
│   │   └── ORBIT_ARCHITECTURE.excalidraw
│   ├── api/
│   └── experiments/
│
└── scripts/
```

Do not create every directory if it has no current purpose. Keep the repository clean.

---

# 21. DOMAIN DATA MODEL

The MVP should begin with these domain objects.

## Entity

```text
id
type
name / label
canonical_attributes
geometry
anchor
current_state
status
observed_at
freshness_policy
permissions
created_at
updated_at
```

## Observation

```text
id
timestamp
source
session_id
raw_reference
observed_entities
spatial_context
quality
provenance
```

## Evidence

```text
id
source_type
source_reference
timestamp
quality
authority
provenance
retention_policy
integrity_reference
```

## State Version

```text
id
entity_id
attribute
value
status
valid_from
valid_to
supported_by
invalidation_reason
```

## Event

```text
id
timestamp
event_type
entity_id / relation_id
before_state
after_state
evidence_refs
```

## Relation

```text
id
source_entity
relation_type
target_entity
valid_from
valid_to
status
evidence_refs
```

## Task

```text
id
goal
status
created_at
updated_at
```

## Task Step

```text
id
task_id
step_order
description
status
dependencies
preconditions
evidence_refs
blocked_reason
completed_at
```

## World Diff

```text
id
baseline_state
current_state
change_type
entity_id
before
after
evidence_refs
created_at
```

## Search Coverage

```text
id
region / anchor
timestamp
visibility_conditions
searched_for
result
confidence/quality
evidence_refs
```

---

# 22. DATABASE DESIGN PRINCIPLES

1. Structured state is the source of truth.
2. History should be append-oriented where practical.
3. Current state can be materialized for fast access.
4. Every important state transition should be traceable to evidence.
5. Contradictory evidence must not be silently overwritten.
6. Temporal validity belongs in the model.
7. Vector embeddings are secondary retrieval indexes.
8. Deletion/retention semantics must be represented.
9. Provider/model identifiers must be stored for reproducibility where relevant.
10. Benchmark runs need scenario seed/configuration/model/memory version metadata.

---

# 23. API BOUNDARIES

The internal API should evolve around domain capabilities.

Suggested first endpoints:

```text
POST   /entities
GET    /entities/{entity_id}

POST   /observations
GET    /observations/{observation_id}

GET    /entities/{entity_id}/history

POST   /world/diff

POST   /tasks
GET    /tasks/{task_id}

POST   /tasks/{task_id}/resume

POST   /queries

POST   /active-perception/plan
```

Do not prematurely expose every internal service as a public API.

---

# 24. FIRST IMPLEMENTATION — WORLD STATE ENGINE

## Goal

Given an entity and a new observation, produce the correct structured state transition.

### Example

Initial state:

```text
bottle_01
location = desk_left
status   = VERIFIED
```

New observation:

```text
bottle_01
location = desk_right
```

Expected:

```text
current location = desk_right

history:
    desk_left → desk_right

event:
    OBJECT_MOVED

evidence:
    observation reference

timestamp:
    new observation time
```

## Required tests

Test at minimum:

1. New entity creation.
2. Same entity observed again.
3. State attribute changes.
4. Location changes.
5. History is preserved.
6. Evidence is attached.
7. Unknown observation does not imply absence.
8. Contradiction can be represented.
9. Freshness can mark an old state stale.
10. World diff reports expected event types.

---

# 25. RESEARCH EXPERIMENT PLAN

The engineering system must eventually support these experiments.

## Experiment A — Persistent identity

Observe same objects from changing viewpoints and controlled relocation.

Measure:

```text
Entity persistence accuracy
Re-identification accuracy
Relocation accuracy
```

## Experiment B — World diff

Create known before/after changes:

- movement;
- replacement;
- state change;
- label change.

Measure:

```text
World-diff precision
World-diff recall
```

## Experiment C — Stale memory resistance

Make old information intentionally wrong.

Measure:

```text
Stale-claim rate
Refresh/abstention behavior
```

## Experiment D — Task resumption

Interrupt task at different stages and alter environment.

Measure:

```text
Task-resumption success
Blocked-step detection
Unsafe continuation rate
```

## Experiment E — Evidence and causality

Introduce coincident events without causal proof.

Measure:

```text
Unsupported causal claim rate
Evidence-backed claim rate
```

## Experiment F — Active perception

Ask queries where current evidence is insufficient.

Compare:

```text
random observation
fixed policy
information-gain heuristic
```

## Experiment G — AR utility

Compare:

```text
spatial overlay
vs
non-spatial interface
```

Measure:

```text
time-to-information
errors
interaction count
change localization
```

## Experiment H — VR counterfactuals

Clone stored world state and alter a variable.

Measure:

```text
counterfactual decision quality
vs
static replay
```

---

# 26. ORBIT-BENCH SCENARIOS

The benchmark should eventually include:

| Scenario | Primary metric |
|---|---|
| Session interruption | Task-resumption success |
| Object relocation | Re-ID + relocation accuracy |
| Configuration change | Stale invalidation rate |
| Partial observation | Unknown/abstention quality |
| Contradiction | Conflict detection rate |
| Stale state | Stale-claim rate |
| Negative search | Search coverage precision |
| Task interruption | Blocked-step detection |
| Causal temptation | Unsupported causal claim rate |
| Multi-user handoff | Handoff recovery score |
| Counterfactual | Decision quality under replay |
| Adversarial memory | Evidence-priority robustness |

---

# 27. CORE METRICS

## Entity persistence accuracy

```text
correct persistent identities
/
evaluated identity instances
```

## World-diff precision

```text
correct change events
/
all reported change events
```

## World-diff recall

```text
correct change events
/
all ground-truth changes
```

## Stale-claim rate

Current-state claims that rely on invalid/stale memory.

Lower is better for this metric, but report the definition and context rather than using it as a standalone product score.

## Evidence-backed claim rate

```text
claims with sufficient supporting evidence
/
evaluated claims
```

## Abstention quality

Measure:

- correct abstentions on unsupported queries;
- useful answers on supported queries.

## Task-resumption success

Tasks resumed from the correct verified state without unsafe assumptions.

## Search efficiency

Time/actions required to locate an object or establish that it is not within validated coverage.

## Interaction overhead

User actions per successful query/task step.

## Latency

Report:

```text
p50
p95
```

for observation-to-response and refresh-to-response.

---

# 28. BASELINE / ABLATION MATRIX

Every important research component needs a baseline.

| Capability | Baseline | ORBIT variant |
|---|---|---|
| Memory | Vector-only retrieval | Structured world memory + hybrid retrieval |
| Freshness | No freshness tracking | Attribute-level freshness/invalidation |
| Evidence | LLM-only response | Evidence-gated response |
| Task continuity | Conversation history | Task graph + last verified state |
| Change detection | Frame-pair comparison | Longitudinal entity-aware diff |
| Active perception | Random next observation | Information-gain heuristic |
| Search memory | Repeat search | Persistent coverage-aware search |
| AR utility | 2D dashboard | Spatial overlay |
| VR reasoning | Static replay | Counterfactual world variant |
| Conflict handling | Highest-confidence source | Explicit contradiction state |

A strong research result should show predictable degradation when a component is removed.

---

# 29. PRIVACY-ALIGNED DATA STRATEGY

## Public data

Potentially use first-person datasets such as Ego4D for general technique evaluation.

## Controlled ORBIT data

Build a reproducible local benchmark with:

- known scene;
- known object identities;
- known relationships;
- intentional changes;
- known task graph;
- exact ground-truth transitions.

The research value comes from correctness and reproducibility, not merely dataset size.

---

# 30. CONTROLLED DATA SCHEMA

Each benchmark sample should make ground truth explicit.

```text
Observation
→ what was visible and where

Entity
→ object identity + canonical attributes

Relation
→ spatial/functional relation + validity interval

Event
→ action/change + timestamp

Task state
→ step + dependencies + completion

Evidence
→ source + timestamp + quality + provenance

World change
→ true before/after delta

Query
→ expected answer + evidence + abstention conditions
```

---

# 31. UI — INITIAL WEB DASHBOARD

Do not build a visually complicated product.

The first dashboard only needs to show:

```text
┌─────────────────────────────────────────────┐
│ ORBIT                                       │
├─────────────────────────────────────────────┤
│ Current World                               │
│                                             │
│ M17     Bench 3     VERIFIED                │
│ C4      Connected   OBSERVED                │
│ Bottle  Unknown     UNKNOWN                 │
│                                             │
├─────────────────────────────────────────────┤
│ Recent Changes                              │
│                                             │
│ C4 moved                                    │
│ M17 configuration changed                   │
│                                             │
├─────────────────────────────────────────────┤
│ Task T12                                    │
│ ✓ Step 1                                    │
│ ✓ Step 2                                    │
│ ⚠ Step 3 BLOCKED                            │
└─────────────────────────────────────────────┘
```

The UI exists to inspect the research system, not to hide its uncertainty.

---

# 32. AR — LATER

AR is research-relevant when spatial presentation provides measurable utility.

Potential uses:

- highlight changed object;
- show remembered location;
- show previous verified position;
- point toward an object requiring refresh;
- show evidence timestamp in context.

AR must be compared against a non-AR interface.

---

# 33. VR — LATER

VR is the controlled replay/counterfactual environment.

Potential uses:

- replay stored world states;
- clone a workspace;
- alter one variable;
- compare decisions;
- study task interruption;
- study planning strategies.

VR is not a decorative visualization layer.

---

# 34. REPLACEABLE MODEL STRATEGY

Keep interfaces stable and implementations swappable.

Suggested provider boundaries:

```text
PerceptionProvider
ReasoningProvider
EmbeddingProvider
RetrievalProvider
SimulationProvider
SpeechProvider
```

The research contribution must survive changes in foundation model.

The same world-state/evidence layer should be testable with:

```text
small/local model
stronger model
different perception model
different retrieval method
```

This is necessary for fair ablation.

---

# 35. DEVELOPMENT PHASES

## Phase 0 — Framing + repository

Deliverables:

- repository;
- project documentation;
- architecture;
- domain model;
- benchmark protocol draft.

Exit criterion:

> Engineering team can explain exactly what the first experiment is.

## Phase 1 — Spatial persistence

Build:

- entities;
- anchors;
- persistent IDs;
- basic spatial relationships.

Exit:

> Same object persists across two observations/sessions.

## Phase 2 — Memory core

Build:

- temporal memory;
- spatial memory;
- episodic memory;
- procedural/task memory.

Exit:

> History and task state are queryable.

## Phase 3 — Evidence engine

Build:

- evidence records;
- provenance;
- statuses;
- freshness;
- invalidation;
- contradiction.

Exit:

> Unsupported current-state claims are blocked/downgraded.

## Phase 4 — World diff

Build:

- longitudinal comparison;
- typed change events.

Exit:

> Known scene changes are measured with precision/recall.

## Phase 5 — Agentic perception

Build:

- targeted observation requests;
- heuristic information gain.

Exit:

> Agent requests useful observations when uncertain.

## Phase 6 — Task resumption

Build:

- dependency checks;
- invalidation of affected steps;
- resume protocol.

Exit:

> Interrupted tasks resume from verified state.

## Phase 7 — VR counterfactuals

Build:

- state replay;
- controlled state variation.

Exit:

> Stored world states support controlled what-if experiments.

## Phase 8 — Wearable/general client

Build:

- device abstraction;
- camera/wearable path;
- multiple clients.

Exit:

> Same world model works across client types.

## Phase 9 — Pilot + paper

Deliver:

- ablation;
- user study;
- benchmark report;
- manuscript.

---

# 36. MVP ACCEPTANCE CRITERIA

ORBIT v0.1 is complete only when all of the following work:

### Entity

- [ ] Create persistent entity.
- [ ] Re-observe entity.
- [ ] Preserve identity.
- [ ] Preserve state history.

### Evidence

- [ ] Every state transition can reference evidence.
- [ ] Evidence has timestamp/provenance.
- [ ] Contradiction can be represented.

### Freshness

- [ ] State can become stale.
- [ ] Current-state retrieval respects freshness.
- [ ] Invalidation can propagate to dependent claims.

### Unknown

- [ ] Partial visibility does not create false removal.
- [ ] Unsupported queries can abstain.

### World diff

- [ ] Detect object movement.
- [ ] Detect object state change.
- [ ] Detect additions.
- [ ] Represent removed/unobserved distinction.
- [ ] Detect relation changes.
- [ ] Detect evidence conflict.

### Task

- [ ] Persist task.
- [ ] Persist task step.
- [ ] Persist dependencies.
- [ ] Mark blocked steps.
- [ ] Resume from verified state.

### Agent

- [ ] Query current state.
- [ ] Query historical state.
- [ ] Answer "what changed?"
- [ ] Request fresh observation when needed.
- [ ] Return evidence/freshness/status.

### Safety

- [ ] No consequential autonomous physical actuation.
- [ ] Authorization boundary exists.
- [ ] Important claims are auditable.

---

# 37. FAILURE MODES TO TEST DELIBERATELY

Do not only test happy paths.

Create explicit tests for:

```text
same-looking objects
occluded object
object moved
object replaced
object absent from camera
stale location
stale procedure
conflicting visual + digital evidence
weak evidence
missing evidence
unsupported causal inference
interrupted task
changed prerequisite
multi-user handoff
misleading low-authority evidence
```

A research-quality system must make its failures observable.

---

# 38. ENGINEERING RULES

## Rule 1

**Structured world state > vector similarity**

## Rule 2

**Evidence > language-model confidence**

## Rule 3

**Unknown ≠ absent**

## Rule 4

**Stale ≠ current**

## Rule 5

**Inference ≠ observed fact**

## Rule 6

**Contradiction must be surfaced**

## Rule 7

**Task state is first-class**

## Rule 8

**Raw video is not the default memory representation**

## Rule 9

**Models and hardware are replaceable**

## Rule 10

**Every research claim needs an experiment**

## Rule 11

**Do not overbuild**

## Rule 12

**Tests are part of the architecture**

---

# 39. GIT / VERSIONING RULES

The repository is the durable project state.

Use:

```text
feature branch
→ implementation
→ tests
→ commit
→ merge
```

Suggested commit style:

```text
feat(world-state): add entity state transitions
feat(evidence): add evidence provenance
feat(memory): add temporal history
test(world-diff): cover object relocation
docs: record freshness policy decision
```

Never rely on an AI agent's chat history as the authoritative project memory.

---

# 40. AI AGENT HANDOFF RULE

The project must remain portable between development agents.

Possible agents:

```text
Antigravity
Claude Code
Codex
other coding agents
```

A new agent should be able to continue by reading:

```text
AGENTS.md
PROJECT_STATUS.md
ARCHITECTURE.md
DECISIONS.md
ROADMAP.md
this file
```

The source code, tests, migrations, documentation, and git history are the real project state.

Do not assume the previous agent remembers anything.

---

# 41. PROJECT STATUS TEMPLATE

Maintain this file continuously.

```md
# ORBIT Project Status

## Current Phase
Phase 0 — World State Engine

## Completed
- [ ]

## In Progress
- [ ]

## Next
- [ ]

## Tests
- [ ]

## Known Issues
- None

## Recent Architecture Decisions
- [ ]

## Research Experiments Enabled
- [ ]

## Current Git Commit
- [ ]

## Last Updated
- [ ]
```

---

# 42. DECISION RECORD TEMPLATE

Use `DECISIONS.md`.

```md
# Architecture Decisions

## ADR-001 — Structured world state is source of truth

Decision:
Use structured entity/state/event/evidence storage as the primary source of truth.

Reason:
Vector similarity is insufficient for current physical state, temporal validity,
identity, contradictions and exact state transitions.

Status:
Accepted

---

## ADR-002 — Model providers are replaceable

Decision:
Perception, reasoning, embedding and simulation implementations must be replaceable.

Reason:
Supports research ablations and avoids coupling the contribution to one proprietary model.

Status:
Accepted
```

---

# 43. FIRST TECHNICAL MILESTONE

Build only this:

```text
                     ORBIT v0.1

             ┌────────────────────┐
             │     ENTITY         │
             └─────────┬──────────┘
                       │
                       ▼
             ┌────────────────────┐
             │   OBSERVATION      │
             └─────────┬──────────┘
                       │
                       ▼
             ┌────────────────────┐
             │  STATE UPDATE      │
             └─────────┬──────────┘
                       │
               ┌───────┴─────────┐
               ▼                 ▼
        ┌─────────────┐   ┌─────────────┐
        │   HISTORY   │   │   EVIDENCE  │
        └──────┬──────┘   └──────┬──────┘
               └────────┬────────┘
                        ▼
                ┌──────────────┐
                │ WORLD DIFF   │
                └──────────────┘
```

### Example test

Initial:

```json
{
  "entity_id": "bottle_01",
  "location": "desk_left",
  "status": "VERIFIED"
}
```

Second observation:

```json
{
  "entity_id": "bottle_01",
  "location": "desk_right",
  "status": "OBSERVED"
}
```

Expected result:

```text
Current state:
    bottle_01.location = desk_right

History:
    desk_left → desk_right

Event:
    OBJECT_MOVED

Evidence:
    observation_002

Timestamp:
    observation_002.timestamp
```

If this works reliably, ORBIT has its first real piece of intelligence.

---

# 44. WHAT NOT TO BUILD YET

Do not begin with:

```text
- Meta glasses integration
- VR engine
- autonomous robot control
- multi-agent swarm
- custom foundation model
- LLM fine-tuning
- huge vector database
- complex 3D scene renderer
- enterprise authentication
- microservice explosion
- Kubernetes
- large-scale streaming infrastructure
```

Build only what the next experiment requires.

---

# 45. RESEARCH / PRODUCT SEPARATION

ORBIT has two layers of thinking.

## Research core

```text
World state
Identity
Memory
Evidence
Freshness
Uncertainty
Diff
Task continuity
Active perception
Evaluation
```

## Product layer

```text
Dashboard
Voice
Wearable
AR
Integrations
Workflow UI
World-state API
```

The research core comes first.

The long-term commercial direction is a software intelligence layer for physical work and continuity, potentially exposing a world-state API where physical entities have stable identity, current/historical state, constraints, evidence and permissions.

---

# 46. LONG-TERM PLATFORM VISION

Conceptually:

```text
                ┌─────────────────────┐
                │     HUMAN / AI      │
                └──────────┬──────────┘
                           │
                           ▼
                 ┌──────────────────┐
                 │   ORBIT WORLD    │
                 │     STATE API    │
                 └────────┬─────────┘
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
       Wearable         Robot          Application
       Agent            Agent          Agent
          │               │                │
          └───────────────┼────────────────┘
                          ▼
                   Physical world
```

This is future scope. Do not build it during v0.1.

---

# 47. RESEARCH SUCCESS TEST

ORBIT is scientifically convincing only if controlled experiments show that it can:

1. maintain object identity across sessions;
2. detect known physical changes;
3. resist stale information;
4. recognize when evidence is insufficient;
5. request useful new observations;
6. resume interrupted tasks from verified state;
7. ground important claims in evidence;
8. distinguish unknown from absence;
9. represent contradictions explicitly;
10. demonstrate measurable utility from spatial interaction and later VR counterfactuals.

A polished demo alone is not sufficient.

---

# 48. FINAL IMPLEMENTATION ORDER

Use this exact dependency order:

```text
1. Repository + docs
        ↓
2. Domain models
        ↓
3. Database schema + migrations
        ↓
4. Entity registry
        ↓
5. Observation ingestion
        ↓
6. World state update engine
        ↓
7. Evidence + provenance
        ↓
8. Freshness + invalidation
        ↓
9. Temporal/spatial history
        ↓
10. World diff
        ↓
11. Task graph + state
        ↓
12. Resume protocol
        ↓
13. Hybrid retrieval
        ↓
14. Grounded query agent
        ↓
15. Active perception
        ↓
16. Camera perception adapter
        ↓
17. Web inspection UI
        ↓
18. AR client
        ↓
19. VR replay/counterfactuals
        ↓
20. ORBIT-BENCH + ablations
```

Do not reorder these merely to make the demo look impressive.

---

# 49. ANTIGRAVITY FIRST SESSION PROMPT

Copy this into the coding agent after placing this file in the repository:

```text
You are the lead implementation engineer for ORBIT.

This repository contains the ORBIT research and engineering specification.

Read:
1. ORBIT_PROJECT_INIT.md
2. AGENTS.md
3. PROJECT_STATUS.md
4. ARCHITECTURE.md
5. DECISIONS.md
6. ROADMAP.md
7. docs/research/ORBIT_RESEARCH_DOSSIER.pdf (when available)

Your job is to build ORBIT incrementally.

IMPORTANT:
- Do not turn ORBIT into a generic chatbot.
- Do not build AR/VR or Meta hardware integration yet.
- Do not begin with an LLM-heavy architecture.
- Structured world state and evidence are the source of truth.
- Semantic retrieval is secondary.
- Preserve epistemic states: OBSERVED, VERIFIED, INFERRED, STALE, CONTRADICTED, UNKNOWN.
- Unknown is not absence.
- Contradictions must be retained.
- Freshness must be respected.
- Models/providers must be replaceable.
- Every major behavior must have tests.

FIRST SESSION:
1. Audit the repository.
2. Identify what exists and what is missing.
3. Propose the concrete implementation plan for Phase 0.
4. Design the minimum database schema for Entity, Observation, StateVersion, Evidence, Event, Relation, Task, TaskStep, and WorldDiff.
5. Propose the first API boundaries.
6. Create/update project documentation.
7. Implement only the minimum World State Engine foundations.
8. Add automated tests.
9. Run the tests.
10. Update PROJECT_STATUS.md.
11. Record meaningful decisions in DECISIONS.md.

The first acceptance test is:

Observation A:
bottle_01 is at desk_left.

Observation B:
bottle_01 is at desk_right.

Expected:
- persistent identity remains bottle_01;
- current state becomes desk_right;
- previous state remains in history;
- an OBJECT_MOVED event is created;
- evidence links to both observations;
- a structured world diff can report the movement.

Do not proceed to large features until this milestone is working and tested.

At the end of the first session, report:
- files created/changed;
- architecture decisions;
- tests added;
- tests passed/failed;
- current project status;
- exact next step.
```

---

# 50. CTO WORKING PRINCIPLE

The project should always follow this sequence:

```text
Research question
      ↓
System hypothesis
      ↓
Smallest implementation
      ↓
Controlled experiment
      ↓
Metric
      ↓
Failure analysis
      ↓
Ablation
      ↓
Iteration
```

Not:

```text
Idea
 ↓
Huge app
 ↓
Fancy demo
 ↓
No idea whether the research works
```

The goal is to make ORBIT a **measurable research system that can later become a product**, not merely an impressive AI demo.

---

# SOURCE ALIGNMENT

This specification is based primarily on the ORBIT Complete Research Dossier, especially its sections on:

- research thesis and continuity gap;
- world/task state abstraction;
- epistemic status;
- memory architecture;
- freshness/invalidation;
- agent loop;
- active perception;
- task continuity;
- longitudinal world diff;
- AR/VR role;
- reliability/safety;
- privacy/security;
- data strategy;
- ORBIT-BENCH;
- baselines/ablations;
- reference architecture;
- roadmap;
- commercialization/governance;
- final research definition.

Where this document adds implementation details such as a proposed Python/FastAPI/PostgreSQL/React stack, those are engineering decisions for the prototype rather than claims that the research dossier itself mandates those technologies.
