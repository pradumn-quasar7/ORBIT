# ORBIT Project Status

## Current Phase
**Phase 15 — Live webcam perception (COMPLETE).** ORBIT v0.1 (Phases 0–9) is complete;
Phases 10–13 added counterfactual sandboxes, decision-aware perception, risk-graded
verification and identity curation; Phase 14 evaluates ORBIT on randomly generated worlds
with bootstrap intervals; Phase 15 connects a real camera: detection runs in the browser
and only stable detections reach ORBIT.

## Phase Log

### Phase 0 — Foundations (§48 steps 1–3)
**Planned**
- Audit the initial Phase 0 commit against the full spec; fix defects that violate research principles.
- Restore the full `ORBIT_PROJECT_INIT.md` (the repo held a condensed 571-line rewrite of the 2,702-line spec).
- Database schema + migrations (§48 step 3), repository boundary, deterministic time.
- Benchmark protocol draft (§35 Phase 0 deliverable).

**Implemented**
- `core/time.py` (`UTCDateTime`), `core/clock.py` (`SystemClock`, `FixedClock`), `core/container.py` (composition root).
- `repositories/base.py` (`Repository` ABC, value semantics, `transaction()`); `InMemoryRepository` rewritten (deep copies, rollback); `repositories/sql/` (`tables.py`, `SqlRepository`); Alembic in `database/migrations` (revision `0001`), auto-upgraded on app start.
- Engine fixes: correct `OBSERVED`/`VERIFIED` classification; entity status = weakest attribute status; atomic ingestion; duplicate-observation rejection; `register_entity` routes `POST /entities` through evidence.
- API: `create_app(repository, clock)` factory, routers in `api/world.py`, lazy module-level `app`.
- Tests: every engine test runs against both repositories; the tautological unknown-≠-absent test fixed; migration/schema parity test.
- Docs: `docs/experiments/BENCHMARK_PROTOCOL.md`; ADR-005…008; roadmap reordered to §48.

### Phase 1 — Spatial persistence (§48 steps 4–6)
**Planned**
- Anchor hierarchy; entity registry with deterministic re-identification; relation maintenance with validity intervals; sessions.
- Exit: same object persists across two observations/sessions.

**Implemented**
- Domain: `Anchor`, `Session`, `ObservedRelation`, `EntityResolution`; `ObservedEntity.identifiers`; `Entity.identity_status` / `identity_candidates`; `IdentityStatus`, `ResolutionMethod`; events `IDENTITY_AMBIGUOUS`, `IDENTITY_CONFLICT`.
- `services/spatial.py` `AnchorRegistry` (lineage, descendants, proximity, cycle guard).
- `services/entity_registry.py` `EntityRegistry` (explicit id → identifier → signature → spatial; ambiguity and replacement handling).
- `services/relations.py` `RelationService` (exclusive/symmetric types, explicit-absence closing, as-of queries).
- Engine: resolution per detection with one-match-per-observation, resolutions persisted on the observation, relations applied after resolution, sessions auto-created/touched.
- API: `POST/GET /anchors`, `GET /anchors/{id}`, `POST/GET /sessions`, `POST /sessions/{id}/end`, `GET /sessions/{id}/observations`, `GET /entities/{id}/relations`.
- Migration `0002`. Tests: `test_spatial_persistence.py` (16 cases × 2 backends) incl. same-looking objects, replaced object, relocation, relation lifecycle.

### Phase 2 — Evidence engine (§48 steps 7–8)
**Planned**
- Evidence source types, provenance, integrity; deterministic evidence policy; attribute-level freshness engine; interventions; invalidation propagation; contradiction lifecycle.
- Exit: unsupported current-state claims are blocked or downgraded.

**Implemented**
- Domain: `SourceType`, `EvidenceChannel`, `ClaimDisposition`, `ClaimDecision`, `FreshnessState`; `Evidence.source/content`; `SupportRef`; `StateVersion` support history, disposition, invalidation fields; `Conflict`, `ClaimDependency`; read models `FreshnessAssessment`, `ClaimAssessment`, `EntityAssessment`; events `CONFLICT_RESOLVED`, `UNCONFIRMED_CHANGE`, `STATE_INVALIDATED`.
- `services/freshness.py` (policy registry, read-time assessment, ablation switch), `services/evidence_policy.py` (source inference, grading, integrity hashing, decision table), `services/claims.py` (evidence gate), `services/belief.py` (decision execution, conflicts, invalidation + propagation, materialisation).
- Engine: observations, `assert_claim`, `record_intervention`, `add_dependency`, `verify_evidence` all share one policy path; late observations cannot rewind the cache.
- API: `GET /entities/{id}/state`, `GET /entities/{id}/claims/{attr}`, `POST /claims`, `POST /entities/{id}/interventions`, `POST /dependencies`, `GET /conflicts`, `GET /evidence/{id}`.
- Migration `0003` (schema + data backfill of source types and support). ADR-011…013.
- Tests: `test_evidence_engine.py` (31 cases × 2 backends) incl. exit-criterion table, weak/misleading/out-of-order evidence, conflict lifecycle, transitive cycle-safe propagation, tamper detection, ablations, migration backfill.

### Phase 3 — Memory core (§48 step 9)
**Planned**
- Temporal (as-of snapshots, timelines), spatial (locate, anchor contents), episodic (session summaries), procedural (task/step persistence and replay), causal-hypothesis memory.
- Exit: history and task state are queryable.

**Implemented**
- Domain: `StateCondition`, `Interruption`, reworked `TaskStep`/`Task`, `CausalHypothesis`; `TaskStatus`, `StepStatus`, `HypothesisStatus`; `Event.task_id`; read models `WorldSnapshot`, `EntitySnapshot`, `LocationAnswer`, `AnchorContent`, `SessionSummary`, `TaskStateView`.
- `services/memory.py` (`world_snapshot`, `timeline`, `locate`, `contents`, `session_summary`, `previous_session`), `services/tasks.py` (graph validation, evidence-backed start/complete/interrupt, as-of replay), `services/hypotheses.py`.
- API: `GET /world/snapshot`, `GET /entities/{id}/timeline`, `GET /entities/{id}/location`, `GET /anchors/{id}/contents`, `GET /sessions/{id}/summary`, `POST/GET /tasks`, `GET /tasks/{id}`, `/state`, `/history`, `POST /tasks/{id}/steps/{step}/start|complete`, `POST /tasks/{id}/interrupt`, `POST/GET /hypotheses`, `POST /hypotheses/{id}/evidence`.
- Migration `0004` (+ step-status data conversion). ADR-014…016. Tests: `test_memory_core.py`.

### Phase 4 — World diff (§48 step 10)
**Planned**
- Snapshot diff `Diff(B_a, B_b)` with typed changes; removed vs unobserved distinction via negative search memory; precision/recall evaluation.
- Exit: known scene changes are measured with precision/recall.

**Implemented**
- Domain: `AbsenceStatus`, `SearchResult`, extended `SearchCoverage`, `WorldChange` (status, absence, notes, related ids), `DiffUncertainty`, `WorldDiff.mode/uncertain`, `ClaimAssessment.has_current_claim`.
- `services/search.py` (`SearchPolicy`, `SearchService`), `BeliefUpdater.apply_absence`, `services/world_diff.py` (`diff`, `diff_since_session`, `event_log_diff` baseline), `evaluation/metrics.py` (`ExpectedChange`, one-to-one `diff_precision_recall`).
- API: `POST /world/diff` (timestamps or `baseline_session_id`, `mode`), `POST /search`, `GET /search-coverage`.
- Migration `0005`. ADR-017, ADR-018. Tests: `test_world_diff.py` incl. Experiment B-0.
- **Result (Experiment B-0):** snapshot diff P = 1.00 / R = 1.00 (8 changes); event-log baseline P = 0.71 / R = 0.62.

### Phase 5 — Task continuity (§48 steps 11–12)
**Planned**
- Dependency checks, invalidation of affected steps, resume protocol (§11), procedure-revision handling, precondition gating.
- Exit: interrupted tasks resume from verified state.

**Implemented**
- Domain: `ConditionState`, `ConditionCheck`, `ObservationRequest`, `StepAssessment`, `ResumePlan`; `StepSpec.step_order`; strict input models.
- `services/conditions.py` (`ConditionEvaluator`, attribute-aware `instruction_for`), `TaskService.assess_step`, `ancestors`, `revision_check`, `resume`, `acknowledge_revision`; gated `start_step`; evidence-checked `complete_step`.
- API: `POST /tasks/{id}/resume`, `POST /tasks/{id}/procedure/acknowledge`. ADR-019, ADR-020.
- Tests: `test_task_continuity.py` — spec §11 T12 scenario: no-change resume, reopened valve invalidates step 5, stale outcome blocks + requests observation, unverified model number ("move closer so I can read the model number"), contradicted precondition, procedure revision, multi-user handoff, branching DAG; every plan checked for unsafe continuation.

### Phase 6 — Grounded query agent + hybrid retrieval (§48 steps 13–14)
**Planned**
- Replaceable embedding/retrieval/reasoning providers; hybrid retrieval; grounded query agent implementing the §5.2 loop and §15 response contract.

**Implemented**
- `providers/` (`base.py`, `embedding.py`, `retrieval.py`, `reasoning.py`), `services/hybrid_retrieval.py`, `services/query_agent.py`.
- Domain: `QueryKind`, `QueryIntent`, `GroundedClaim`, `RetrievalHit`, `GroundedResponse`.
- Supported questions: where is / where was at T, attribute value, contents of an anchor, what changed (since last session / since T), continue, what happened (yesterday), why.
- API: `POST /queries`, `GET /memory/search`. ADR-021, ADR-022.
- Tests: `test_query_agent.py` — contract checked on every response; stale → abstain + "Point the camera near bench_3…"; contradiction surfaced, never resolved; ambiguity → clarifying question; ablation without evidence gate answers stale memory.

### Phase 7 — Active perception + action safety (§48 step 15, §16)
**Planned**
- Targeted observation planning with a replaceable heuristic information-gain policy and baselines; observe → verify → authorize → act → verify → record for consequential actions.

**Implemented**
- Domain: `ObservationActionType`, `ObservationCost`, `UncertainClaim`, `PlannedObservation`, `PerceptionPlan`; `PrincipalKind`, `Scope`, `Principal`, `ActionStatus`, `Authorization`, `ActionRequest`, `OutcomeResult`, `OutcomeRecord`; event `ACTION_STATUS_CHANGED`.
- `services/active_perception.py` (planner, marginal-gain greedy `InformationGainPolicy`, `FixedPolicy`, `RandomPolicy`, request ranking), `services/actions.py` (`ActionSafetyService`, built-in `orbit-agent` principal without authorize/actuate).
- API: `POST /active-perception/plan`, `POST/GET /principals`, `POST/GET /actions`, `GET /actions/{id}`, `POST /actions/{id}/recheck|authorize|performed|verify-outcome|execute(403)`, `GET /outcomes`. Migration `0006`. ADR-023, ADR-024.
- **Result (Experiment F, Phase 7 scene):** 3 looks remove 2.89/3.10 uncertainty (information gain) vs 1.93 (fixed) vs 1.67–2.89 (random seeds 0–9).

### Phase 8 — Perception adapter + web inspection UI (§48 steps 16–17)
**Planned**
- Vendor-neutral device/perception boundary, evidence minimisation and redaction, simple inspection dashboard, demo scenario.

**Implemented**
- `providers/perception.py` (`RawFrame`, `make_frame`, `DetectionPerceptionProvider`, `parse_identifiers`, `SimulatedScene`, `SimulatedPerceptionProvider`), `services/perception_gateway.py` (ingest, hash-only retention, redaction), integrity v2; `Observation.redaction`.
- `services/dashboard.py` + `frontend/` (static dashboard at `/ui/`): current world, recent changes, ranked observation requests, tasks, open conflicts, query box.
- `scripts/seed_demo.py`: spec §4 flagship scenario via simulated perception (anonymous camera in Session B).
- API: `POST /perception/frames`, `POST /observations/{id}/redact`, `GET /inspect/summary`, `/ui/`, `/dashboard`. Migration `0007`. ADR-025, ADR-026.
- Verified in a browser: desktop and 375 px mobile (no page-level horizontal scroll); two UI bugs found and fixed (nested-node rendering in conflicts, confirmed-absent objects shown with their old location).

### Phase 9 — ORBIT-BENCH + ablations (§48 step 20)
**Planned**
- Scenario format with explicit ground truth (§30), scenarios for the §26 table and §37 failure modes, runner across ablations (§28), §27 metrics, run metadata (§22.10), report.

**Implemented**
- `OrbitConfig` switches in the composition root (one per ablated component).
- `evaluation/bench.py` (scenario DSL, `ScenarioRunner`, 7 variants, 14 metrics, Markdown/JSON report), `experiments/scenarios/catalog.py` (12 scenarios), `experiments/runners/run_bench.py`, `experiments/results/latest.{md,json}`.
- `test_bench.py`: full ORBIT meets ground truth; each ablation degrades its target metric; deterministic.
- ADR-027.

**Result (12 scenarios × 7 variants)** — see `experiments/results/latest.md`:

| Metric | ORBIT | Ablation that removes the component | Ablated value |
|---|---|---|---|
| diff precision / recall | 1.00 / 1.00 | event-log diff | 0.60 / 0.38 |
| stale-claim rate ↓ | 0.00 | no freshness · last writer wins · no evidence gate | 0.20 · 0.25 · 0.18 |
| conflict detection | 1.00 | last writer wins | 0.00 |
| correct abstention | 1.00 | no evidence gate | 0.17 |
| unsafe continuation ↓ | 0.00 | naive resume · no freshness | 0.67 · 0.33 |
| unsupported causal claims ↓ | 0.00 | no evidence gate | 1.00 |
| search coverage precision | 1.00 | unobserved ⇒ removed | 0.67 |
| entity persistence / false merges | 0.97 / 0.00 | — (abstains on an undecidable look-alike) | — |

### Phase 10 — Replay and counterfactual sandbox (§48 step 19, §33, Experiment H)
**Planned**
- Rebuild the world as known at any past instant; replay history frame by frame; isolated sandboxes for what-if premises; compare decisions against static replay; sensitivity analysis; Experiment H.
- Exit (spec §35 Phase 7): stored world states support controlled what-if experiments.

**Implemented**
- `services/grading.py` (`status_from_supports`), `services/projection.py` (`WorldProjector`), `services/sandbox.py` (`fork`), `services/counterfactual.py` (`apply_variation`, `compare`, `sensitivity`, `SandboxRegistry`), `services/replay.py`, `evaluation/counterfactual_eval.py` (Experiment H, included in the bench report).
- `SourceType.SIMULATION`; real engine rejects it (`SimulationEvidenceRejected`, HTTP 403).
- Domain: `VariationKind`, `Variation`, `SandboxInfo`, `DecisionComparison`, `CounterfactualReport`, `SensitivityItem`, `SensitivityReport`, `ReplayFrame`.
- API: `GET /replay`, `POST/GET /sandboxes`, `GET/DELETE /sandboxes/{id}`, `POST /sandboxes/{id}/variations`, `GET /sandboxes/{id}/snapshot`, `POST /sandboxes/{id}/queries`, `POST /sandboxes/{id}/tasks/{task}/resume`, `POST /counterfactuals/compare`, `POST /tasks/{id}/sensitivity`.
- **Bug fixed in the existing read path (found by the fidelity probe):** as-of questions cited later evidence and later status upgrades; now only evidence known at the time counts (ADR-012 amendment).
- Tests: `test_counterfactuals.py` — projection identical to the source at every event instant of the flagship scenario; roll-back of later knowledge; premise kinds; real-world isolation; compare; sensitivity; replay; Experiment H. ADR-028…030.
- **Result (Experiment H):** decision quality static replay 0.20 vs counterfactual 1.00; unsafe pre-commitments 0.80 vs 0.00.

### Phase 11 — Decision-aware active perception (value of information)
**Planned**
- Non-mutating resume preview; decision-critical facts per next step; a policy ranking looks by value for the next decision; pre-action checks in "Continue."; Experiment F2.
- Exit: before a consequential step ORBIT asks to check the facts it rests on — even fresh ones — and this measurably averts unsafe continuation.

**Implemented**
- `TaskService.preview` (rolled-back transaction), `TaskService.decision_critical`; `ResumePlan.decision_critical` / `recommended_checks`; `Repository.in_transaction`.
- `UncertainClaim.decision_critical` / `decision_weight` / `critical_for_steps`; `DecisionAwarePolicy`; `PerceptionPlan.weighting`; blocked-on claims count as critical.
- Agent: "Continue." adds "Before you start, confirm: …"; dashboard ranks by decision value with a "next step depends on it" badge and a decision-critical count.
- API: `POST /tasks/{id}/preview`; `policy: "decision_aware"` on `POST /active-perception/plan`.
- `evaluation/decision_perception_eval.py` (Experiment F2), included in the bench report. ADR-031, ADR-032. Tests: `test_decision_aware_perception.py`.
- **Result (Experiment F2):** unsafe continuation 0.00 decision-aware vs 1.00 information gain / fixed / random (k = 1–3); safe work kept 1.00 for all; stale clutter refreshed after one look 1 vs 8 (decision-aware catches up at k = 2).

### Phase 12 — Risk-graded verification before consequential steps and actions
**Planned**
- Risk levels on steps and actions; HIGH-risk prerequisites need recent evidence; explicit recorded waivers (never for stale/contradicted/violated facts); actions inherit task-step prerequisites; "Is it safe to …?" questions; bench scenario + ablation.
- Exit: a HIGH-risk step or action never proceeds on old unverified evidence unless a person explicitly waives that specific shortfall with a reason.

**Implemented**
- `RiskLevel`; `services/risk.py` (`RiskPolicy`: observed ≤ 10 min or verified ≤ 60 min for HIGH); risk-aware `ConditionEvaluator.check`; `ConditionCheck.risk` / `risk_shortfall` / `last_supported_at`; `TaskStep.risk`, `StepSpec.risk`, `ActionRequest.risk`, `Authorization.waived`; `QueryKind.SAFETY`.
- `ActionSafetyService`: inherited prerequisites, risk-aware verification, waiver rules, audit notes. Agent `_safety`. `OrbitConfig.risk_grading_enabled`.
- API: `risk` on steps/actions, `waive` on authorisation, prerequisites inherited when omitted. Migration `0008`.
- Bench: scenario `high_risk_stale_premise`, variant `no_risk_grading`. ADR-033, ADR-034. Tests: `test_risk_graded_verification.py`.
- Design correction during the phase: VERIFIED evidence initially exempted HIGH-risk steps forever; it now ages out after 60 min.

### Phase 13 — Identity curation (merge, undo, confirm distinct)
**Planned**
- Human-confirmed merge of a duplicate/ambiguous record into its true entity, re-deriving belief from both evidence histories; undo; "distinct" judgments; merge suggestions; aliases; bitemporal history; bench scenario.
- Exit: a person can resolve an identity ORBIT could not decide, the merged belief follows the normal evidence policy, earlier "as known at" answers are unchanged, and the merge can be undone.

**Implemented**
- `services/identity.py` (`IdentityService`: `merge`, `undo`, `confirm_distinct`, `suggestions`, `rebuild`, `resolve_alias`); `IdentityMerge`, `MergeSuggestion`; `Scope.CURATE`; events `IDENTITY_MERGED/UNMERGED/DISTINCT`.
- Bitemporal fields: `StateVersion.recorded_at/retired_at`, `Conflict.recorded_at`; `Entity.merged_into/merged_at/distinct_from`; silent replay mode in `BeliefUpdater`; transaction-time-aware reads, snapshots, projection.
- Aliases in re-identification, search, agent vocabulary; merged records hidden from views; world diff reports `IDENTITY_MERGED`; `GET /entities?include_merged`.
- Epistemic fixes found while writing ground truth: ambiguity now casts doubt on candidates (ADR-036); a search refutes only claims inside its region (ADR-037).
- API: `GET /identity/suggestions`, `POST /identity/merge`, `GET /identity/merges`, `POST /identity/merges/{id}/undo`, `POST /identity/distinct`. Migration `0009`.
- Bench: `ConfirmIdentity` step, alias-aware identity metric, scenario `identity_correction` (14 scenarios). ADR-035…037. Tests: `test_identity_curation.py`.

### Phase 14 — Generated worlds, confidence intervals, paired comparisons
**Planned**
- Seeded world generator with simulator ground truth (scene, task, conflict families); bootstrap 95 % intervals; paired ORBIT-vs-ablation differences; a progress metric; report any failure modes the random worlds reveal.

**Implemented**
- `evaluation/generator.py`, `evaluation/stats.py`, `evaluation/generated.py`, `experiments/runners/run_generated_bench.py`, `experiments/results/generated.{md,json}`.
- Bench extensions: `expect="either"`, truth-based resume checks + `progress_rate`, `truth:` ids, search frames with detections, field of view on observations, typed anchors.
- **Failure modes found by random worlds and fixed:** (1) a single compatible candidate elsewhere was merged without evidence that it had left its old place; (2) two surfaces in a room counted as "near". False merges over 300 worlds: 0.5 %, all irreducible without identifiers (ADR-038). Also a bug in my own first generator draft (searches reported seeing nothing) was caught by an implausible 0.10 search precision.
- ADR-038. Tests: `test_generated_bench.py`.
- **Result (40 worlds per family, 95 % CI):** every ablation significantly worse on its target metric; ORBIT: diff P/R 0.88 [0.78, 0.96] / 0.96 [0.93, 0.99]; stale claims 0.13 [0.07, 0.20] (objects moved while unobserved); unsafe continuation 0.10 [0.02, 0.20] vs 0.28 without risk grading and 0.68 with naive resume; progress 0.65 [0.50, 0.80].

### Phase 15 — Live webcam perception
**Planned**
- First real sensor: a laptop/USB webcam in Chrome/Edge. Detect objects on the device, never send pixels or people; calibrate places by drawing regions on the picture; optional QR tags for permanent identity; filter detector flicker; send only when the scene changes (plus a heartbeat); a deliberate "scan" that can confirm absence. Groundwork for the Quest 3S client.

**Implemented**
- Backend: `services/camera.py` (`CameraService`: `configure`, `config`, `snapshot`, `scan`), `api/camera.py` (`GET/PUT /cameras/{id}/config`, `POST /cameras/{id}/snapshot`, `POST /cameras/{id}/scan`). Calibration is stored in anchor frames (`{"camera", "bbox"}` per region, `{"camera_view"}` on the view anchor). `Detection.marker_id` → explicit entity id; `person` is excluded server-side as well.
- Browser client: `frontend/camera.html|css|js` (camera loop, region editor, scan, status/log, "ORBIT identified") and `frontend/camera_core.js` (pure logic: IoU tracker with 3-hit confirmation, majority colour vote, sticky QR markers, scene signature, send-on-change + 60 s heartbeat). COCO-SSD lite (TensorFlow.js) runs locally; QR via native `BarcodeDetector` or jsQR. Link from the Inspector.
- ADR-039. Tests: `test_camera.py` (calibration, 409/422, region placement, privacy and confidence filters, QR identity across a move, heartbeat corroboration, region-scoped absence via scan) and `frontend/tests/camera_core.test.js` (run by pytest when Node is present).
- Verified in the browser: libraries and model load (~25 s first download; now preloaded on page open), a simulated tracker → API round trip produces correct entities (QR id kept, person dropped). The browser pane blocks real cameras, so the live feed must be checked by the user.

## MVP Acceptance (spec §36)

| Criterion | Evidence (test) |
|---|---|
| Create persistent entity | `test_api.py::test_post_entity_is_backed_by_evidence` |
| Re-observe entity / preserve identity | `test_spatial_persistence.py::test_same_object_persists_across_two_sessions` |
| Preserve state history | `test_memory_core.py::test_timeline_and_attribute_history` |
| Every transition references evidence; timestamp/provenance | `test_foundations.py::test_evidence_links_back_to_each_observation`, `test_evidence_engine.py::test_evidence_has_provenance_and_integrity` |
| Contradiction can be represented | `test_evidence_engine.py::test_cross_channel_disagreement_is_contradiction_not_overwrite` |
| State can become stale | `test_evidence_engine.py::test_freshness_is_attribute_specific` |
| Current-state retrieval respects freshness | `test_evidence_engine.py::test_unsupported_current_state_claims_are_blocked_or_downgraded` |
| Invalidation propagates to dependent claims | `test_evidence_engine.py::test_dependency_propagation_is_transitive_and_cycle_safe` |
| Partial visibility does not create false removal | `test_world_diff.py::test_not_reobserved_is_not_removed` |
| Unsupported queries can abstain | `test_query_agent.py::test_where_is_stale_abstains_and_requests_observation` |
| Detect movement / state change / additions | `test_world_diff.py::test_moves_state_changes_additions_and_revisions` |
| Removed vs unobserved distinction | `test_world_diff.py::test_validated_search_confirms_absence_then_refound`, `::test_inadequate_search_is_inconclusive` |
| Detect relation changes / evidence conflict | `test_world_diff.py::test_relation_changes`, `::test_evidence_conflict_in_diff` |
| Persist task, steps, dependencies | `test_memory_core.py::test_task_progress_is_evidence_backed_and_replayable` |
| Mark blocked steps | `test_task_continuity.py::test_stale_outcome_blocks_and_requests_observation` |
| Resume from verified state | `test_task_continuity.py::test_resume_from_verified_state_without_changes` |
| Query current / historical state | `test_query_agent.py::test_where_is_fresh`, `::test_historical_question` |
| Answer "what changed?" | `test_query_agent.py::test_what_changed_since_last_session` |
| Request fresh observation when needed | `test_task_continuity.py::test_unverified_precondition_requests_targeted_observation` |
| Return evidence / freshness / status | `test_query_agent.py::test_where_is_fresh` |
| No consequential autonomous actuation | `test_perception_and_safety.py::test_agent_can_recommend_but_never_authorize_or_actuate` |
| Authorization boundary exists | `test_perception_and_safety.py::test_full_action_lifecycle_with_outcome_memory` |
| Important claims are auditable | `test_evidence_engine.py::test_tampered_evidence_fails_integrity`, action audit events |

## Known Issues / Limitations
- No multi-workspace / multi-tenant separation or authentication on the API (spec §17 access control is principal-scoped for actions only).
- Reasoning provider is rule-based; free-form language coverage is limited to the supported question types. An LLM provider can be added behind `ReasoningProvider`.
- Live camera: COCO-SSD knows ~80 everyday classes (no cables, tools, pumps) — such objects need QR tags `orbit:<type>:<id>`. One fixed camera per view; no pose tracking. The model is fetched from a CDN on first use (internet needed). Detection quality on real scenes is not yet measured.
- In-memory vector index is rebuilt per process; a pgvector `RetrievalProvider` is needed for large memories.
- PostgreSQL is supported by the schema but CI runs on SQLite only (no Postgres available in this environment).
- AR client and VR rendering (§48 steps 18–19 front-ends) are deferred; the replay/counterfactual backend exists.
- Sandboxes are process-local and disposable (not persisted); each probe copies the world, which is fine for small workspaces but not optimised.

## Next (candidates)
1. Meta Quest 3S client: WebXR AR session served over HTTPS on the LAN, then (optionally) a native app with the Passthrough Camera API.
2. Measured live accuracy: label a short recorded desk session and report identity/diff metrics on real frames.
3. Generated futures for Experiment H and generated perception worlds for Experiment F2.
3. API authentication and workspace separation.
4. Per-attribute pre-action windows (e.g. pressure vs lockout tag) instead of one HIGH window.

## Tests
- `.venv/bin/pytest` → 483 passed (every engine test runs on both in-memory and SQL backends).
- `.venv/bin/python experiments/runners/run_generated_bench.py` → generated-world report with confidence intervals.
- `.venv/bin/python experiments/runners/run_bench.py` → ORBIT-BENCH report.

## Architecture Decisions
- ADR-001…004 initial principles
- ADR-005 Repository boundary + SQL store · ADR-006 UTC time · ADR-007 §48 phase order · ADR-008 status classification
- ADR-009 Conservative re-identification · ADR-010 Relation semantics
- ADR-011 Evidence policy · ADR-012 Read-time freshness · ADR-013 Invalidation propagation
- ADR-014 Memory as read model · ADR-015 Step progress vs completion evidence · ADR-016 Causal hypotheses
- ADR-017 Snapshot world diff · ADR-018 Coverage-validated absence
- ADR-019 Resume protocol · ADR-020 Strict evidence inputs
- ADR-021 Deterministic replaceable providers · ADR-022 Grounded response contract
- ADR-023 Marginal information-gain perception · ADR-024 Action safety boundary
- ADR-025 Vendor-neutral perception, hash-only retention · ADR-026 Static dashboard
- ADR-027 ORBIT-BENCH design and metric definitions
- ADR-028 World projection · ADR-012 amendment (no future evidence in as-of reads) · ADR-029 Sandbox isolation / SIMULATION evidence · ADR-030 Sensitivity analysis + Experiment H
- ADR-031 Decision-aware perception (value of information) · ADR-032 Rolled-back preview
- ADR-033 Risk-graded verification · ADR-034 Explicit waivers, inherited prerequisites
- ADR-035 Merge by evidence replay (bitemporal) · ADR-036 Ambiguity casts doubt · ADR-037 Region-scoped absence
- ADR-038 Generated worlds with bootstrap CIs; relocation needs evidence of leaving
- ADR-039 Live camera: on-device detection, calibration as anchor frames, markers as identity

## Research Experiments Enabled
- A (persistent identity), B (world diff, incl. B-0), C (stale-memory resistance), D (task resumption), E (evidence and causality), F (active perception) — all runnable via ORBIT-BENCH or dedicated tests, each with its ablation baseline.
- H (counterfactual decisions vs static replay) — runnable, reported in the bench.
- F2 (decision-aware vs uncertainty-driven perception) — runnable, reported in the bench.
- G (AR utility) — deferred (needs an AR client).

## Last Updated
- 2026-10-04 (Phase 15)
