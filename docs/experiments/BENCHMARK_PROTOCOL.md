# ORBIT-BENCH Protocol (v0.1)

Status: **implemented** (Phase 9). Scenarios: `experiments/scenarios/catalog.py`;
runner: `backend/app/evaluation/bench.py`; CLI: `experiments/runners/run_bench.py`;
latest results: `experiments/results/latest.md`. Experiment B-0 below is also an
automated test (`test_world_diff.py::test_experiment_b0_controlled_desk_scene`).

```bash
.venv/bin/python experiments/runners/run_bench.py
```

## 1. The first experiment

**Experiment B-0 — "What changed?" on a controlled desk scene** (spec §18, §25-B).

| Stage | Action | Ground truth recorded |
|---|---|---|
| Session A | Observe desk with 5 objects: laptop, phone, bottle, notebook, cable. Record relations (cable `connected_to` laptop) and a 4-step task. | Entity ids, locations, relations, task step states, evidence ids |
| Intervention | Experimenter moves the bottle, moves the cable, replaces the notebook, changes one task prerequisite. Logged by hand with timestamps. | Exact before/after delta per change |
| Session B | Partial re-observation (one region deliberately out of view), then the queries "What changed?" and "Continue." | Expected answer, expected abstentions, expected observation requests |

The system passes B-0 when:

1. every persistent object keeps its identity (`entity persistence accuracy = 1.0`);
2. every ground-truth change is reported with the correct change type (recall);
3. nothing is reported that did not change (precision) — in particular the out-of-view
   object is reported as `OBJECT_REMOVED_OR_UNOBSERVED` with `absence = NOT_REOBSERVED`,
   never as confirmed removed;
4. the changed prerequisite blocks the dependent task step, and "Continue." returns a
   targeted observation request instead of the next step.

## 2. Scenario schema

Each scenario is a deterministic, seeded script. Ground truth is explicit (spec §30).

```text
scenario_id, seed, description
steps[]          ordered: observation | intervention | search | task_op | clock_advance
ground_truth
  entities[]     id, type, canonical attributes
  changes[]      change_type, entity_id, attribute, before, after
  queries[]      text, expected_answer | expected_abstain, expected_observation_request
```

## 3. Metrics (spec §27)

| Metric | Definition |
|---|---|
| Entity persistence accuracy | correct persistent identities / evaluated identity instances |
| World-diff precision | correct change events / all reported change events |
| World-diff recall | correct change events / all ground-truth changes |
| Stale-claim rate | current-state claims relying on stale/invalid memory / current-state claims made |
| Evidence-backed claim rate | claims with sufficient supporting evidence / evaluated claims |
| Abstention quality | correct abstentions on unsupported queries; useful answers on supported ones (both reported) |
| Conflict detection rate | contradictions surfaced / contradictions injected |
| Task-resumption success | tasks resumed from the correct verified state without unsafe assumptions |
| Blocked-step detection | correctly blocked steps / steps whose prerequisites were invalidated |
| Unsafe continuation rate | resumes that presented a step whose prerequisites were unsupported |
| Unsupported causal claim rate | causal claims asserted as fact without causal evidence / causal queries |
| Search coverage precision | confirmed-absent claims that are actually absent / confirmed-absent claims |
| Latency | p50 / p95 observation-to-response |

A change is "correct" when `(change_type, entity_id, attribute)` matches ground truth and
`after` equals the true value.

## 4. Ablation matrix (spec §28)

Each ablation removes exactly one component; a convincing result shows the matching
metric degrades predictably.

| Component removed | Expected degradation |
|---|---|
| Freshness policy | stale-claim rate ↑ |
| Evidence gate | evidence-backed claim rate ↓, abstention quality ↓ |
| Contradiction detection (last-writer-wins) | conflict detection rate → 0 |
| Search-coverage policy (unobserved ⇒ removed) | world-diff precision ↓, search coverage precision ↓ |
| Snapshot diff (event-log diff) | world-diff precision ↓ on A→B→A moves |
| Task graph (conversation-only progress) | blocked-step detection ↓, unsafe continuation ↑ |
| Information-gain perception (random policy) | observations needed to resolve uncertainty ↑ |

## 5. Run metadata (spec §22.10)

Every run records: scenario id + seed, ORBIT git commit, schema migration revision,
engine configuration (enabled components), provider identifiers (perception,
reasoning, embedding), and wall-clock timings.

## 6. v0.1 catalog

| Scenario | Spec §26 row / §37 failure mode | Primary metric |
|---|---|---|
| object_relocation | Object relocation, viewpoint change | entity persistence accuracy |
| configuration_change | Configuration change | stale-claim rate |
| partial_observation | Partial observation / object absent from camera | diff precision |
| contradiction | Contradiction / conflicting visual + digital evidence | conflict detection rate |
| stale_state | Stale state | stale-claim rate |
| negative_search | Negative search | search coverage precision |
| task_interruption | Task interruption / changed prerequisite | unsafe continuation rate |
| causal_temptation | Causal temptation / unsupported causal inference | unsupported causal claim rate |
| same_looking_objects | Same-looking objects | false merge rate |
| object_replaced | Object replaced | diff recall |
| multi_user_handoff | Multi-user handoff | task resumption success |
| adversarial_memory | Adversarial memory / misleading low-authority evidence | stale-claim rate |
| high_risk_stale_premise | Consequential step on old-but-unexpired evidence | unsafe continuation rate |
| identity_correction | Ambiguous identity resolved by a person | entity persistence accuracy |

## 7. Experiment H — counterfactual decisions vs static replay

World: task T12 interrupted after steps 5–6. Five futures can occur during the
interruption (nothing; valve reopened; spare pumps swapped; label reads CP-150;
procedure revised), each with a known safe next step. Policies commit before the
future is known: *static replay* plans once from the stored state; *counterfactual*
builds a contingency table from sensitivity probes. Metrics: decision quality
(prepared decision = safe decision) and unsafe pre-commitment rate. Implemented in
`backend/app/evaluation/counterfactual_eval.py`; reported in `latest.md`.

## 8. Experiment F2 — decision-aware vs uncertainty-driven perception

Task T12 resumed a day after interruption; its critical facts were re-observed
recently (fresh), eight unrelated shelf objects are two days stale. Hidden truth:
future A — the valve was reopened; future B — nothing changed. Each policy gets k
looks in a closed loop (each look reveals the true state of what it covers), then the
resume preview decides. Metrics: unsafe continuation (A), safe work kept (B), stale
claims refreshed, looks spent on decision-critical facts. Implemented in
`backend/app/evaluation/decision_perception_eval.py`.

## 9. Generated worlds and statistics

`backend/app/evaluation/generator.py` keeps a hidden true world per seed and derives
every expectation from it (families: *scene*, *task*, *conflict*). Questions whose
answer ORBIT's information cannot settle are judged only against the truth. Metrics
are ratios of sums over worlds with 95 % percentile-bootstrap intervals; ablations are
compared *paired* on the same worlds. Use ≥ 40 worlds per family: task worlds yield one
resume decision each. Run `experiments/runners/run_generated_bench.py`.

Known gaps: randomised futures for Experiment H, AR utility (G), latency on
real perception, and larger randomised scene generators for statistical power.
