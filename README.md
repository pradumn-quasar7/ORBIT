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

### Assistant (Orbi)
Open http://localhost:8765/ui/assistant.html (Chrome or Edge for voice). Type or press
**Talk**: "Where is the microscope?", "I moved the notebook to bench 4", "I finished step 6",
"What should I check?", "Open the valve" (Orbi checks prerequisites and asks for your yes;
you do the action and say "done"; ORBIT verifies the result), "What did you do for me?".
The demo's `operator` may authorise actions. Optional: let Claude parse free-form
language — this sends the workspace vocabulary to the Anthropic API:
```bash
ORBIT_ASSISTANT_LLM=anthropic ANTHROPIC_API_KEY=... ORBIT_DATABASE_URL=sqlite:///./orbit_demo.db .venv/bin/uvicorn backend.app.main:app --port 8765
```

### Realtime
Every page is live: the Inspector redraws and shows a ticker the moment ORBIT commits a
change, and Orbi speaks up unasked (e.g. "Verified: the result of 'open the valve' is now
observed" when the camera sees it). Scripts can follow the same stream:
```bash
curl -N "localhost:8765/stream?topics=world,observation"
```

### Live camera
With the server running, open http://localhost:8765/ui/camera.html in Chrome or Edge,
click **Start camera** and allow camera access (on macOS the browser also needs
System Settings → Privacy & Security → Camera). Draw boxes on the picture for the places
you care about and name them; ORBIT then remembers what stable objects are where.
Detection runs in the browser — video and people are never sent. Print a QR code with
the text `orbit:<id>` (or `orbit:<type>:<id>`) and stick it on an object to give it a
permanent identity. **Scan region** is a deliberate look: anything ORBIT expects there
but cannot see is recorded as confirmed absent.

### Benchmark (ORBIT-BENCH)
```bash
.venv/bin/python experiments/runners/run_bench.py
```
Runs 12 ground-truth scenarios against ORBIT and six single-component ablations and
writes `experiments/results/latest.md`.

```bash
.venv/bin/python experiments/runners/run_generated_bench.py --per-family 40
```
Runs ORBIT and its ablations on randomly generated worlds (ground truth from a
simulator) and reports 95 % confidence intervals and paired differences in
`experiments/results/generated.md`.

### Documentation
`ORBIT_PROJECT_INIT.md` (spec) · `ARCHITECTURE.md` · `DECISIONS.md` · `ROADMAP.md` ·
`PROJECT_STATUS.md` · `docs/experiments/BENCHMARK_PROTOCOL.md`
