# AGENTS.md — Agent Handoff & Operating Guide

## Welcome, Agent
You are an autonomous engineering agent working on **ORBIT** (Persistent World Model Agent).
ORBIT maintains an evidence-aware, temporally evolving representation of physical workspaces and ongoing tasks.

### Golden Rules
1. **Structured World State > Vector Similarity**: The primary source of truth is structured entities, states, events, relations, and evidence. Vector stores are secondary recall aids.
2. **Evidence > LLM Confidence**: Never use model confidence as physical world truth. Every state claim must be traceable to timestamped evidence with provenance.
3. **Unknown ≠ Absent**: If an object is not observed, it does not mean it was removed unless search coverage and search policy validate its absence.
4. **Epistemic Status is Mandatory**: Distinguish `OBSERVED`, `VERIFIED`, `INFERRED`, `STALE`, `CONTRADICTED`, `UNKNOWN`.
5. **Preserve Contradictions**: Never silently overwrite conflicting evidence or let an LLM pick a winner. Surface contradictions explicitly.
6. **Freshness is Contextual**: Freshness depends on attribute volatility (e.g., location vs serial number vs power state).
7. **Action Safety First**: Consequential physical actions require verify prerequisites → authorization → act → verify outcome.

### Context Files to Read Before Coding
1. `ORBIT_PROJECT_INIT.md`: Complete engineering specification and principles.
2. `PROJECT_STATUS.md`: Current phase, completed work, and next steps.
3. `ARCHITECTURE.md`: Architecture diagrams and domain boundaries.
4. `DECISIONS.md`: Architectural Decision Records (ADRs).
5. `ROADMAP.md`: Multi-phase roadmap.

### Development Workflow
1. Read current status and active phase in `PROJECT_STATUS.md`.
2. Inspect tests: run `.venv/bin/pytest`.
3. Plan change, implement domain logic and services.
4. Add comprehensive unit and integration tests.
5. Update `PROJECT_STATUS.md` and `DECISIONS.md`.
6. Make a clean git commit with conventional commit format (e.g. `feat(world-state): ...`).
