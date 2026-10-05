# ORBIT Development Roadmap

Phase order follows the spec §48 dependency order (ADR-007). Each phase ends with
tests, updated `PROJECT_STATUS.md` / `DECISIONS.md`, and a merge to `main`.

- [x] **Phase 0 — Foundations** (§48 steps 1–3)
  - Docs, ADRs, benchmark protocol draft, domain models, epistemic statuses.
  - Repository boundary; SQL schema + Alembic migrations; UTC time; injectable clock.
  - Remediation of the initial engine (status classification, atomic ingestion, evidence-backed registration).

- [x] **Phase 1 — Spatial persistence** (steps 4–6)
  - Anchor hierarchy, entity registry with deterministic re-identification (explicit id, strong identifiers, signature + spatial proximity, ambiguity handling).
  - Relation maintenance (`on`, `inside`, `connected_to`, …) with validity intervals; sessions.
  - Exit: same object persists across two observations/sessions.

- [x] **Phase 2 — Evidence engine** (steps 7–8)
  - Evidence source types, provenance, integrity hashes; evidence policy (supersede / corroborate / contradict / unconfirmed).
  - Attribute-level freshness policy engine; interventions; invalidation propagation; contradiction lifecycle.
  - Exit: unsupported current-state claims are blocked or downgraded.

- [x] **Phase 3 — Memory core** (step 9)
  - Temporal (state as-of), spatial (what is at an anchor), episodic (sessions), procedural (task persistence), causal-hypothesis memory.
  - Exit: history and task state are queryable.

- [x] **Phase 4 — World diff** (step 10)
  - Snapshot diff `Diff(B_a, B_b)`, typed changes, relation changes, conflicts.
  - Negative search memory + coverage-validated absence; precision/recall evaluation.
  - Exit: known scene changes are measured with precision/recall.

- [x] **Phase 5 — Task continuity** (steps 11–12)
  - Task graph (dependencies, preconditions, postconditions), interruptions, resume protocol (§11).
  - Exit: interrupted tasks resume from verified state.

- [x] **Phase 6 — Grounded query agent** (steps 13–14)
  - Replaceable Embedding/Retrieval/Reasoning providers; hybrid retrieval; response contract (§15) with claims, freshness, evidence, conflicts, abstention.

- [x] **Phase 7 — Active perception + action safety** (step 15, §16)
  - Information-gain heuristic vs fixed/random policies; targeted observation requests.
  - Observe → verify prerequisites → authorize → act → verify outcome → outcome memory.

- [x] **Phase 8 — Perception adapter + web inspection UI** (steps 16–17)
  - `PerceptionProvider` / device adapter; evidence minimisation; inspection dashboard (§31).

- [x] **Phase 9 — ORBIT-BENCH + ablations** (step 20)
  - Scenario runner, metrics, ablation matrix, run metadata.

- [x] **Phase 10 — Replay and counterfactual sandbox** (step 19 backend)
  - World projection at any instant, replay frames, isolated what-if sandboxes, decision comparison, sensitivity analysis, Experiment H.

- [x] **Phase 11 — Decision-aware active perception**
  - Value-of-information policy, resume preview, pre-action checks, Experiment F2.

- [x] **Phase 12 — Risk-graded verification**
  - Risk levels, recency bar for HIGH-risk prerequisites, explicit waivers, inherited action prerequisites, safety questions.

- [x] **Phase 13 — Identity curation**
  - Human-confirmed merge/undo/distinct with evidence replay, aliases, bitemporal history, suggestions.

- [x] **Phase 14 — Generated benchmark**
  - Seeded random worlds with simulator ground truth, bootstrap intervals, paired ablation deltas, progress metric.

- [x] **Phase 15 — Live webcam perception**
  - In-browser detection (COCO-SSD), regions drawn on the picture as ORBIT places, QR identity tags, stability filter, scan-to-confirm-absence; people never sent.

- [x] **Phase 16 — Conversational assistant + 3D avatar**
  - "Orbi": voice/text assistant acting on the user's behalf inside ORBIT (statements, interventions, task steps, checks); physical actions prepared for an explicit recorded yes; procedural three.js avatar.

- [x] **Phase 17 — Realtime**
  - Commit-time change notifications, Server-Sent Events with replay, live Inspector, proactive assistant notices.

- [x] **Phase 18 — Meta Quest 3S mixed reality**
  - Live-webcam fixes; paired HTTPS access on the LAN; ORBIT places pinned to the real room with persistent anchors, live evidence labels, Orbi in the room, voice and consent.

- [ ] **Phase 19 (next) — First headset session & native perception**
  - Fix what a real Quest session reveals; a native app with the Passthrough Camera API so the headset can perceive. — WebXR AR session over HTTPS on the LAN using the same camera endpoints; native Passthrough Camera API app optional.

- [ ] **Later** — AR client (step 18), VR rendering of replay/counterfactuals (step 19 front-end), wearable clients, pilot study.
