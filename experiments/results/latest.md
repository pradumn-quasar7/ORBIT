# ORBIT-BENCH results

- **run_at**: `2026-10-01T12:01:41+00:00`
- **git_commit**: `62ea2b6+dirty`
- **schema_revision**: `0007`
- **scenario_catalog**: `v0.1`
- **scenarios**: `12`
- **reasoning_provider**: `rule-based-v1`
- **embedding_provider**: `hashing-bow-v1`
- **retrieval_provider**: `in-memory-cosine-v1`
- **python**: `3.9.6`

| Metric | orbit | no_freshness | last_writer_wins | unobserved_removed | no_evidence_gate | event_log_diff | naive_resume |
|---|---|---|---|---|---|---|---|
| entity_persistence_accuracy | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 |
| false_merge_rate ↓ | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| diff_precision | 1.00 | 1.00 | 1.00 | 0.62 | 1.00 | 0.60 | 1.00 |
| diff_recall | 1.00 | 1.00 | 1.00 | 0.62 | 1.00 | 0.38 | 1.00 |
| stale_claim_rate ↓ | 0.00 | 0.20 | 0.25 | 0.00 | 0.18 | 0.00 | 0.00 |
| evidence_backed_claim_rate | 1.00 | 1.00 | 1.00 | 1.00 | 0.64 | 1.00 | 1.00 |
| correct_abstention_rate | 1.00 | 0.50 | 0.83 | 1.00 | 0.17 | 1.00 | 1.00 |
| useful_answer_rate | 1.00 | 1.00 | 0.86 | 0.86 | 1.00 | 1.00 | 1.00 |
| conflict_detection_rate | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| task_resumption_success | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 | 1.00 | 0.33 |
| blocked_step_detection | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| unsafe_continuation_rate ↓ | 0.00 | 0.33 | 0.00 | 0.00 | 0.00 | 0.00 | 0.67 |
| unsupported_causal_claim_rate ↓ | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 |
| search_coverage_precision | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 1.00 | 1.00 |

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

## Latency (ms)

| Variant | observation_ms_p50 | observation_ms_p95 | query_ms_p50 | query_ms_p95 |
|---|---|---|---|---|
| orbit | 0.59 | 1.25 | 0.32 | 0.47 |
| no_freshness | 0.55 | 1.09 | 0.24 | 0.30 |
| last_writer_wins | 0.57 | 1.07 | 0.25 | 0.30 |
| unobserved_removed | 0.56 | 1.04 | 0.26 | 0.30 |
| no_evidence_gate | 0.56 | 1.06 | 0.25 | 0.29 |
| event_log_diff | 0.56 | 1.06 | 0.25 | 0.30 |
| naive_resume | 0.59 | 1.33 | 0.29 | 0.43 |
