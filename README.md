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
.venv/bin/pytest
```

### Running the API
```bash
.venv/bin/uvicorn backend.app.main:app --reload
```
State is stored in `./orbit.db` (SQLite) by default; set `ORBIT_DATABASE_URL`
(e.g. `postgresql+psycopg://…`) to use PostgreSQL. Migrations run automatically on
start; to run them by hand: `.venv/bin/alembic upgrade head`.

### Demo: the flagship scenario
```bash
.venv/bin/python scripts/seed_demo.py --reset
ORBIT_DATABASE_URL=sqlite:///./orbit_demo.db .venv/bin/uvicorn backend.app.main:app --port 8765
```
Then open http://localhost:8765/ui/ and ask "What changed?" or "Continue.".

### Benchmark (ORBIT-BENCH)
```bash
.venv/bin/python experiments/runners/run_bench.py
```
Runs 12 ground-truth scenarios against ORBIT and six single-component ablations and
writes `experiments/results/latest.md`.

### Documentation
`ORBIT_PROJECT_INIT.md` (spec) · `ARCHITECTURE.md` · `DECISIONS.md` · `ROADMAP.md` ·
`PROJECT_STATUS.md` · `docs/experiments/BENCHMARK_PROTOCOL.md`
