# ORBIT-BENCH results

- **run_at**: `2026-10-03T06:12:59+00:00`
- **git_commit**: `c79195a`
- **schema_revision**: `0009`
- **scenario_catalog**: `v0.1`
- **scenarios**: `14`
- **reasoning_provider**: `rule-based-v1`
- **embedding_provider**: `hashing-bow-v1`
- **retrieval_provider**: `in-memory-cosine-v1`
- **python**: `3.9.6`

| Metric | orbit | no_freshness | last_writer_wins | unobserved_removed | no_evidence_gate | event_log_diff | naive_resume | no_risk_grading |
|---|---|---|---|---|---|---|---|---|
| entity_persistence_accuracy | 0.96 | 0.96 | 0.96 | 0.96 | 0.96 | 0.96 | 0.96 | 0.96 |
| false_merge_rate ↓ | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| diff_precision | 1.00 | 1.00 | 1.00 | 0.62 | 1.00 | 0.60 | 1.00 | 1.00 |
| diff_recall | 1.00 | 1.00 | 1.00 | 0.62 | 1.00 | 0.38 | 1.00 | 1.00 |
| stale_claim_rate ↓ | 0.00 | 0.21 | 0.20 | 0.00 | 0.20 | 0.00 | 0.00 | 0.00 |
| evidence_backed_claim_rate | 1.00 | 1.00 | 1.00 | 1.00 | 0.60 | 1.00 | 1.00 | 1.00 |
| correct_abstention_rate | 1.00 | 0.44 | 0.89 | 1.00 | 0.22 | 1.00 | 1.00 | 0.89 |
| useful_answer_rate | 1.00 | 1.00 | 0.89 | 0.89 | 1.00 | 1.00 | 1.00 | 1.00 |
| conflict_detection_rate | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| task_resumption_success | 1.00 | 0.80 | 1.00 | 1.00 | 1.00 | 1.00 | 0.20 | 0.80 |
| blocked_step_detection | 1.00 | 0.80 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.80 |
| unsafe_continuation_rate ↓ | 0.00 | 0.20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.80 | 0.20 |
| unsupported_causal_claim_rate ↓ | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 0.00 |
| search_coverage_precision | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 | 1.00 |
| progress_rate | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.33 | 1.00 |

↓ = lower is better. Each ablation removes one component (spec §28).

## Metric definitions

- **entity_persistence_accuracy** — detections resolved to the entity of their true physical object / evaluated detections
- **false_merge_rate** — detections merged into another object's entity / evaluated detections (lower is better)
- **diff_precision** — correct reported changes / reported changes
- **diff_recall** — correct reported changes / ground-truth changes
- **stale_claim_rate** — asserted current-state answers contradicting ground truth / asserted current-state answers (lower is better)
- **evidence_backed_claim_rate** — asserted claims with sufficient evidence (fresh OBSERVED/VERIFIED, cited) / asserted claims
- **correct_abstention_rate** — abstentions on queries that should abstain / queries that should abstain
- **useful_answer_rate** — correct answers on answerable queries / answerable queries
- **conflict_detection_rate** — injected contradictions surfaced as open conflicts / injected contradictions
- **task_resumption_success** — resume decisions matching ground truth / resume checks
- **blocked_step_detection** — steps correctly blocked / steps that should be blocked
- **unsafe_continuation_rate** — resumes presenting an unsafe step / resume checks (lower is better)
- **unsupported_causal_claim_rate** — causal questions answered with an unsupported cause / causal questions (lower is better)
- **search_coverage_precision** — confirmed-absent claims that are truly absent / confirmed-absent claims
- **progress_rate** — resumes presenting a step when the true world allowed progress / such resumes (caution has a cost)

## Scenarios

- `object_relocation` — Objects relocated between sessions; session B camera is anonymous (primary: entity_persistence_accuracy)
- `configuration_change` — Known intervention invalidates configuration (primary: stale_claim_rate)
- `partial_observation` — Object outside the field of view (primary: diff_precision)
- `contradiction` — Registry and visual label disagree (primary: conflict_detection_rate)
- `stale_state` — High-volatility state goes stale (primary: stale_claim_rate)
- `negative_search` — Validated vs partial search coverage (primary: search_coverage_precision)
- `task_interruption` — Interrupted task with a changed prerequisite (primary: unsafe_continuation_rate)
- `causal_temptation` — Coincident events without causal proof (primary: unsupported_causal_claim_rate)
- `same_looking_objects` — Two identical bottles (primary: false_merge_rate)
- `object_replaced` — Notebook replaced by a different notebook (primary: diff_recall)
- `multi_user_handoff` — Task handed from Ana to Bob (primary: task_resumption_success)
- `adversarial_memory` — Misleading low-authority evidence (primary: stale_claim_rate)
- `high_risk_stale_premise` — High-risk step resting on an old (but unexpired) observation (primary: unsafe_continuation_rate)
- `identity_correction` — A person resolves an ambiguous identity (primary: entity_persistence_accuracy)

## Latency (ms)

| Variant | observation_ms_p50 | observation_ms_p95 | query_ms_p50 | query_ms_p95 |
|---|---|---|---|---|
| orbit | 0.61 | 1.35 | 0.32 | 0.63 |
| no_freshness | 0.58 | 1.32 | 0.26 | 0.38 |
| last_writer_wins | 0.62 | 1.35 | 0.27 | 0.44 |
| unobserved_removed | 0.58 | 1.24 | 0.30 | 0.39 |
| no_evidence_gate | 0.60 | 1.32 | 0.27 | 0.43 |
| event_log_diff | 0.58 | 1.21 | 0.28 | 0.40 |
| naive_resume | 0.58 | 1.30 | 0.27 | 0.38 |
| no_risk_grading | 0.57 | 1.29 | 0.28 | 0.39 |

## Experiment H — counterfactual decisions vs static replay

| Future during interruption | Safe next step | ORBIT on return | Static replay | Counterfactual |
|---|---|---|---|---|
| nothing changes | s7 | s7 | s7 | s7 |
| valve reopened | s5 | s5 | s7 | s5 |
| spare pumps swapped | wait/verify | wait/verify | s7 | wait/verify |
| label reads CP-150 | wait/verify | wait/verify | s7 | wait/verify |
| procedure revised | wait/verify | wait/verify | s7 | wait/verify |

- Decision quality: static replay **0.20**, counterfactual **1.00**
- Unsafe pre-commitments ↓: static replay **0.80**, counterfactual **0.00**
- Decision-critical claims found by sensitivity analysis: pump_old.installed, sop.procedure_revision, valve.state, pump_new.model_number
- Caveat: futures are hand-specified; this measures contingency planning in a controlled world.

## Experiment F2 — decision-aware vs uncertainty-driven perception

Valve secretly reopened while 8 unrelated stale objects sit on a shelf; k looks, then resume.

| Policy | unsafe@1 ↓ | unsafe@2 ↓ | unsafe@3 ↓ | stale refreshed@1 | stale refreshed@2 | stale refreshed@3 | safe work kept |
|---|---|---|---|---|---|---|---|
| decision_aware | 0.00 | 0.00 | 0.00 | 1.0 | 9.0 | 9.0 | 1.00 |
| information_gain | 1.00 | 1.00 | 1.00 | 8.0 | 9.0 | 9.0 | 1.00 |
| fixed | 1.00 | 1.00 | 1.00 | 1.0 | 2.0 | 3.0 | 1.00 |
| random | 1.00 | 1.00 | 1.00 | 1.0 | 2.0 | 3.0 | 1.00 |

- Random is averaged over 5 seeds. Fixed and random rank uncertain claims only, like information gain.
