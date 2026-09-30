# ORBIT — Persistent World Model Agent

ORBIT is a persistent, evidence-aware, temporally evolving world model agent for physical workspaces and ongoing tasks. It detects what changed, knows what it does not know, helps humans resume work after interruptions, and tracks evidence and freshness as first-class citizens.

## Key Principles

- **Longitudinal State**: Maintains world state across sessions, not isolated frames.
- **Persistent Spatial Identity**: Objects retain identity across moves and viewpoint changes.
- **Epistemic Status**: Distinguishes `OBSERVED`, `VERIFIED`, `INFERRED`, `STALE`, `CONTRADICTED`, and `UNKNOWN`.
- **Evidence Provenance**: All state claims are traceable to specific observations or records.
- **Unknown ≠ Absent**: Failure to observe does not mean removed unless validated by search coverage.
- **Structured Source of Truth**: Structured entities, states, events, and relations take precedence over vector similarity.

## Getting Started

### Prerequisites
- Python 3.9+

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Running Tests
```bash
pytest backend/tests/
```
