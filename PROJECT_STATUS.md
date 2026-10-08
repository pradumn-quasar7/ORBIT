# ORBIT Project Status

## Current Phase
**Phase 19 — Realtime voice with Gemini Live + device actions (COMPLETE; awaiting the user's API key for a live test).**
ORBIT v0.1 (Phases 0–9) is complete; Phases 10–18 added counterfactual sandboxes,
decision-aware perception, risk-graded verification, identity curation, a generated
benchmark, live webcam perception, Orbi the assistant, realtime updates and the Quest
mixed-reality client (tested on a real Quest 3S in 18.1). Phase 19 makes the conversation
truly realtime with Gemini Live and lets Orbi open apps, search and send messages on
the user's Mac and Quest.

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

### Phase 16 — Conversational assistant + 3D avatar
**Planned**
- A conversational agent that does things on the user's behalf — by voice or text — through ORBIT's own services, with a 3D avatar as its face. It may record what the user says and does, report task progress, pause tasks, answer questions and suggest checks; it may never actuate, and may only *prepare* physical actions for the user's explicit, recorded "yes" (spec §14, §16, Golden Rule 7).

**Implemented**
- `providers/commands.py`: `Command` (ASK, TELL, STEP_DONE, STEP_START, INTERRUPT, ACT, PERFORMED, CONFIRM, CANCEL, CHECKS, RECAP, HELP, GREET); `RuleBasedCommandProvider` (default, offline); `AnthropicCommandProvider` (opt-in Claude tool-use parsing, validated against the vocabulary, falls back to rules; consent words never reach the model).
- `services/assistant.py`: `AssistantService` orchestrates the query agent, claims, interventions, task service, active perception and the action-safety boundary. "I moved/opened X" is a *known intervention* (invalidates, then records testimony); "X is Y" is a plain statement (a disagreement with fresh camera evidence becomes a conflict). Actions: propose as `orbit-assistant` (agent: observe/reason/recommend only) → prerequisites → if the speaker may authorise, a 2-minute confirmation bound to that action → "yes" recorded as the human's authorisation → the human performs and says "done" → outcome verified against post-action evidence. A recap lists every delegated act with references.
- `api/assistant.py`: `GET /assistant`, `POST /assistant/conversations`, `GET /assistant/conversations/{id}`, `POST /assistant/conversations/{id}/messages`.
- Frontend: `assistant.html|css|js` (chat with evidence badges, delegated-act list, observation requests, approve/decline card with countdown, voice in via Web Speech API, voice out via speech synthesis) and `avatar.js` (procedural three.js robot "Orbi": idle breathing, blinking, gaze following the pointer, lip movement while speaking, gestures WAVE/NOD/EXPLAIN/THINK/SHRUG/ASK/ALERT, an orbit ring coloured by state). Demo seed registers a human `operator` who may authorise.
- ADR-040. Tests: `test_assistant.py` (38, both backends): grounded answers + pronoun follow-up, spoken names instead of ids, testimony provenance, intervention vs conflicting statement, unknown place / ambiguous object record nothing, step start/done through readiness checks, pause, the full prepare → yes → done → verify flow, decline, confirmation expiry, unauthorised speaker, the assistant itself can never authorise, blocked action explains prerequisites, LLM parser validation/fallback/consent isolation, API.
- Verified in the browser: avatar renders; prepare → reminder on "done" → approve → done → outcome unverified with a look request → recap. Found and fixed during that check: input was blocked while the avatar spoke; stale approval cards stayed clickable; "done" before approval fell through to the question answerer.

### Phase 17 — Realtime
**Planned**
- No view should wait for a refresh or a question: push every committed world change to the dashboard, camera and assistant pages as it happens; let the assistant react to world changes on its own (verify an authorised action the moment the camera sees the result; announce changes to what the user is discussing, conflicts, and task steps losing support) — without ever announcing anything that was rolled back.

**Implemented**
- Repository commit notifications (`Repository.on_commit`): events and observations are reported once per transaction, after commit, in write order; rollbacks discard them; listener errors never fail a write (both backends).
- `core/realtime.py` `EventBus`: numbered messages (topics `world`, `observation`, `assistant`), a 500-message replay buffer, bounded per-subscriber queues (oldest dropped + `overflow` signal), thread-safe hand-off from worker threads to the event loop, conversation-scoped notices.
- `api/realtime.py`: `GET /stream` (Server-Sent Events, `Last-Event-ID` resume, heartbeats, topic / conversation filters), `GET /stream/recent`, `GET /stream/status`. Realtime is on for the served app only (`OrbitServices.build(realtime=True)`); benchmarks and sandboxes stay silent.
- Assistant notices (`AssistantNotice`): outcome verified / failed as soon as post-action evidence commits; focus changes and conflicts for what was just discussed; task steps blocked or needing re-verification. The user's own turn is answered in the reply, not re-announced; the same news is not repeated within 20 s.
- Frontend: `realtime.js` (shared auto-reconnecting EventSource + "● live" pill); the Inspector redraws within ~250 ms of a commit and shows a live ticker (60 s safety poll for ageing freshness); Orbi shows and speaks notices, queued behind current speech.
- UI files are served with `Cache-Control: no-cache` (ETag revalidation) — found in testing: a browser kept running a stale pre-realtime `app.js`.
- ADR-041. Tests: `test_realtime.py` (commit/rollback semantics on both backends, failing listeners, bus filters/replay/overflow, SSE framing and resume, camera snapshot → stream, outcome verified/failed by the camera, focus notices with cooldown, own changes not announced, no bus → no notices, UI cache header).
- Verified in the browser with two tabs: a simulated camera observation moved the microscope on the Inspector and in its ticker without a refresh; Orbi announced "Verified: the result of 'open the valve' is now observed" the moment the valve was seen open.

### Phase 17.1 — Voice input that works (fix)
**Planned**
- The user reported Orbi could not hear them. Find out why and make voice input work, or say clearly why not and how to fix it.

**Implemented**
- Cause: the Claude app's built-in browser blocks the microphone (`not-allowed`), and the page only showed a small grey hint. Chrome's Web Speech API also depends on an online Google service that other browsers (Electron apps, Brave, the Quest browser) lack.
- `frontend/voice.js`: two engines behind one interface. The browser speech service is the default; **on-device Whisper** (`onnx-community/whisper-base.en` via transformers.js, ~2 s per command, downloaded once and cached) is used when chosen or when the browser service fails (`network`, `service-not-allowed`, `language-not-supported`). It stops recording on its own after a pause. A live input-level meter shows the microphone is working. Every failure has a plain explanation and a fix (site permission, macOS privacy setting, missing device, mic in use, the Claude app's built-in browser with a copy-link button). The permission state is checked up front.
- Parser fixes found with Whisper output: present-tense reports ("I move the notebook to …") and spoken numbers ("bench four", "bench for" → bench 4, "step six" → step 6).
- Verified in the browser: an emulated microphone playing a synthesised clip went through recording → end-of-speech → Whisper → "Where is the microscope?" → Orbi's grounded answer. Tiny vs base on the same clips: tiny heard "bench for", so base was chosen. A real microphone could not be tested here (blocked in the built-in browser); the user needs Chrome or Edge.

### Phase 17.2 — Live conversation fixes (from the user's first real voice session)
**Planned**
- The user reported Orbi "still not listening, not replying". Find the cause and fix what their real transcript showed.

**Implemented**
- **Server would not restart**: since Phase 17 every open page holds a streaming connection, so a stopped server waited forever for them. Three half-stopped servers were alive and the port was held by one that no longer answered. Fix: `--timeout-graceful-shutdown 2`, and `scripts/run_demo.sh` (stops any old server, force-stops if needed, starts, waits until healthy, prints the URLs; log in `.run/server.log`). Verified: restart with an open stream completes in ~2 s.
- Voice worked once the page ran in Chrome. Fixes from the real transcript:
  - "hay Aur Bhi" ("Hey Orbi" transcribed for an Indian accent) and other renderings of the name are greetings, and are stripped as a form of address ("hey orbi, where is…").
  - New `HEARD` command: "I am audible to you", "can you hear me", "testing" get "Yes, I can hear you"; fillers ("ok so like") get "I'm listening".
  - "what change from the last scenario", "what's new", "any updates" now mean *what changed*.
  - Unknown input gets a friendly fallback instead of "couldn't map that to a question about the world state".
  - "What changed" is spoken as a short, ranked summary (moves, conflicts, confirmed absences first; "not seen again" last). The full list stays on screen.
- **Safety fix**: while an approval was pending, any utterance *starting* with "ok"/"yes" counted as consent, so the user's own filler "ok so like" would have approved an action. Consent must now be the whole utterance ("yes", "yes please", "ok go ahead").
- Tests: 38 new cases from the user's real phrases, consent phrasings and spoken summaries.

### Phase 18 — Meta Quest 3S mixed reality (+ live-webcam fixes)
**Planned**
- (A) Fix what the first live webcam test exposed. (B) Let a Quest headset on the same Wi-Fi reach ORBIT safely (WebXR needs HTTPS; the API has no accounts). (C) A headset client: ORBIT places pinned to the real room, live labels saying what the evidence supports, Orbi in the room, voice, consent.
- Checked against Meta's current documentation first: Quest Browser supports `immersive-ar` passthrough, anchors including persistent ones (`requestPersistentHandle`, `restorePersistentAnchor`) and plane detection; web pages get **no access to passthrough camera pixels**, so perception stays with the webcam and the headset is ORBIT's window into the room.

**Implemented**
- **(A) Webcam**: neighbouring neutral colours (black ≈ gray ≈ white, never black vs white) no longer prove two objects differ (`values_compatible`, used by re-identification and identity suggestions); tracker hysteresis (an object stays in view through ~2 s of flicker, so scans count it too), sticky colours (change only on an overwhelming new majority), snapshots at most every 2 s, hallucination-prone COCO labels dropped (tie, umbrella, teddy bear, sports ball, toothbrush), default camera place `my_desk` (no longer merges with the demo's `desk`).
- **(B) LAN access**: `python -m backend.app.serve --lan` (or `scripts/run_demo.sh --lan`) serves the *same app* on `http://localhost:8765` and `https://<lan-ip>:8766` from one process (shared realtime bus and conversations), with a self-signed certificate for the LAN address. `core/pairing.py`: network devices must pair once by typing an 8-character code at `/pair` (HttpOnly/Secure/SameSite=Strict cookie derived from the code; constant-time compare; 8 failures → 1 min lockout; off-site redirects refused; the code is shown only to the laptop). The code persists in `.run/` until `--new-code`.
- **(C) Headset client**: `api/xr.py` (`GET /xr/places` with supportable beliefs per place, `PUT/DELETE /xr/places/{id}` storing the persistent-anchor handle in the anchor frame, `GET /xr/pairing`); `frontend/xr.html|css|js` (WebXR `immersive-ar`, `local-floor`; optional anchors, plane detection, hit test, hands); `frontend/xr_core.js` (label wording, severity ordering, ray–plane placement, menu; unit-tested). In the headset: a lazy-following 3D menu (no DOM overlay on Quest), pin a place by pointing (hit test → detected table/wall → 1 m), labels that follow anchors and face you, point at a label to hear it, grip to talk (on-device Whisper when the browser has no speech service), Yes/No buttons when Orbi awaits consent, live updates and Orbi's notices. On a computer the same scene is a 3D preview with pairing instructions. `avatar.js` split into `buildAvatar()` (any scene) and `createAvatar()` (page).
- ADR-043. Tests: `test_xr.py` (places and contested beliefs, pin/unpin, pairing flow, cookie properties, lockout, off-site redirect, code alphabet and persistence, certificate SAN), `xr_core.test.js` (labels, ordering, geometry, menu), `test_camera.py` (black/gray mouse stays one object), `camera_core.test.js` (hysteresis, sticky colour, rate limit, labels).
- Verified: preview renders in the browser; a paired network client loaded the page, scripts, places, assistant and stream over HTTPS; unpaired requests were refused. **Not verified: an actual Quest session** (no headset here) — the WebXR calls follow the anchors / hit-test / plane-detection specifications and Meta's documentation.

### Phase 18.1 — First real Quest 3S session (fixes)
**Planned**
- The user connected a Quest 3S over USB (Developer Mode, `adb reverse tcp:8765 tcp:8765`, page at `http://localhost:8765/ui/xr.html`: a secure context with no certificate or pairing). AR worked first time (Bench 3, Bench 4 and the Cart were pinned), but Orbi could not hear or speak. Diagnose on the device and fix.

**Implemented**
- Diagnosed live through the Quest Browser's remote-debugging socket (`adb forward tcp:9222 localabstract:chrome_devtools_remote`, Chrome DevTools Protocol).
- **Hearing**: the Quest Browser has no Web Speech recognition (on-device Whisper was used, correctly), but its noise-suppressed microphone is ~20× quieter than a laptop's: room RMS ≈ 0.0007 against a fixed speech threshold of ≈ 0.013, so every utterance was discarded as silence. Speech detection now learns the room's noise floor in the first 0.4 s (threshold = 3.5 × median, min 0.0012). Recordings are never discarded for being quiet; they are peak-normalised before Whisper. The level meter is relative to the floor. Diagnostics go to the console.
- **Speaking**: the Quest Browser has no `speechSynthesis`. New `GET /speech?text=` renders replies with the server's system voice (macOS `say`, argv only, embedded commands stripped, ≤ 400 chars, bounded cache). The headset plays it with the mouth driven by the audio level.
- **Understanding** (from the real transcripts): "Hey R.B." / "Arby" are Orbi's name; leading fillers ("okay tell me…") are dropped; "where is bench 3" (a place) answers what is there; an unknown object gets "I know about: …"; "get an action approved" explains how. Place contents are spoken naturally ("At Bench 4: … Last seen there, but may have moved: … Confirmed gone: …").
- Tests: transcripts from the session, place-contents speech, speech endpoint (WAV, cache, length cap, unavailable).

### Phase 19 — Realtime voice (Gemini Live) and device actions
**Planned**
- The user asked for a complete, realtime conversational agent on their Google Gemini key that also does what they say: "open Instagram", "open WhatsApp", "search this in the browser". Decisions (asked): both devices (Quest and Mac); an `AIza…` API key; WhatsApp messages may be sent, but only after the user's own yes.
- Checked against Google's current docs: Live API over WebSocket, model `gemini-3.8-live` (native audio, function calling, barge-in, input/output transcription), 16 kHz PCM in, 24 kHz PCM out, ephemeral tokens recommended for clients.

**Implemented**
- `services/live.py` `LiveService`: mints a **single-use ephemeral token** per session (`POST /v1beta/auth_tokens`, 30 min, 2 min to start); the API key never reaches a browser. Writes the session setup: Orbi's instructions (never state facts about the room except from ORBIT; relay uncertainty; never give consent), the live workspace vocabulary, voice, transcription. Six tools: `orbit` (anything about the world, tasks and physical actions → the existing assistant, evidence gate and action-safety boundary), `open_app`, `web_search`, `open_website`, `prepare_message`, `send_message`.
- **Consent stays deterministic**: every tool call carries the user's own transcribed words for that turn (`heard`). A "yes" that authorises an action (assistant) or sends a message (live service) must be the user's whole utterance; a model-relayed "yes" is refused ("I need to hear the yes from you"). The user's words are stored in evidence provenance. ORBIT notices are injected into the conversation as notices, never as user words.
- `services/devices.py` `DeviceController`: Quest via adb (apps by package; web pages in the Quest browser; Instagram/YouTube etc. as websites since no Quest apps), Mac via `open`. Argument lists only, URLs must be web addresses, package names validated. WhatsApp: chat opened with the text filled in; on the Mac's WhatsApp app it is sent (app activated, then Return, needs Accessibility permission), on the Quest the user taps send. Contacts from `.run/contacts.json` or a spoken number. Messages expire after 2 min and send once.
- `api/live.py`: `GET /live/status`, `POST /live/session`, `POST /live/tool`. `core/secrets.py`: `.env` (git-ignored, owner-only) loaded at start.
- Frontend: `live_core.js` (resampling, soft gain for the quiet Quest mic, PCM16 ↔ base64; unit-tested), `live.js` (`LiveSession`: AudioWorklet capture → 100 ms chunks, gapless 24 kHz playback with barge-in flush, transcripts, tool round-trips). The assistant page gets "Gemini Live (realtime)" (default when a key exists), with live transcript bubbles and ✓/✗ tool lines. In the Quest, grip / Talk opens a live conversation; Orbi's mouth follows its real voice; a consent request shows the Yes/No buttons.
- ADR-044. Tests: `test_live.py` (device commands per device, unsafe URLs, Quest not connected, contacts; token never exposes the key, setup contents; orbit tool grounded with `heard`; consent matrix; device tools; message prepare → refuse → send once → expire; Quest messages opened not sent; API; `.env` loading), `live_core.test.js`.
- **Not yet verified against Google's servers**: no key was available at build time. The protocol follows the current API reference; the client falls back between `v1beta` and `v1alpha` for the token endpoint.

### Phase 19.1 — Hosted speech-to-speech on Groq's free plan
**Planned**
- The Gemini project had no credit, and the user did not want to pay or run models locally. Use Groq's free hosted models instead: speech to text, a tool-calling chat model, text to speech, as a continuous, interruptible voice conversation.

**Implemented**
- Verified with the user's key first: Whisper `whisper-large-v3-turbo` (0.3 s, word-perfect); tool calling with `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` (≈0.3 s; `llama-3.3-70b-versatile` is not available to this account); Orpheus TTS needs a one-time terms acceptance in the Groq console.
- `services/voice_agent.py` `VoiceAgent`: one turn = Groq Whisper → chat model with **the same six tools, instructions and consent rules as the Gemini engine** (`LiveService.call`, consent judged on the transcript) → reply. Model fallback on 404, plain errors for limits/keys, per-conversation history (16 messages), Whisper silence hallucinations ("Thank you.", "you") ignored. `api/voice.py`: `GET /voice/status`, `POST /voice/turn` (raw audio body).
- `/speech` now prefers Groq's Orpheus voice and falls back to the Mac voice (retrying Groq after 2 min); Hindi text uses the Mac Hindi voice.
- `frontend/conversation.js` `HostedConversation`: hands-free turn-taking in the browser. Adaptive speech detection, 0.5 s pre-roll, end after 0.9 s of quiet, normalised 16 kHz WAV, barge-in that stops Orbi when the user talks over it (threshold raised while Orbi speaks), the same callbacks as the Gemini engine. Both pages choose Groq, else Gemini, else the previous method. `live_core.js` gained `concat`, `normalise` (gain capped at 40×) and `wav`.
- Real round trips through ORBIT with Groq (synthesised speech): "where is the microscope" → `orbit("where is m17")` → an answer that keeps ORBIT's staleness; "can you open the valve" → the action prepared, consent pending. 1–2.5 s per turn. Fixed from that test: Orbi asked the user to point the *headset* camera; it now asks for the webcam or the user's own check.
- Tests: `test_voice_agent.py` (turn pipeline, history, device tools, the model cannot give consent, silence, model fallback and limits, API, Groq voice with fallback), `live_core.test.js` (WAV, normalisation).

### Phase 19.2 — Email drafts in Gmail
**Planned**
- The user asked whether Orbi can control Gmail and type mail for them.

**Implemented**
- New tool `compose_email(to, subject, body, cc, device)` for both voice engines: the model writes the subject and a proper body from what the user said, and Gmail opens on the Mac or in the Quest browser with the draft filled in. **Nothing is sent**: the user reviews it and presses Send. In a browser a keypress could land in another window, so sending is never automated.
- Contacts: `.run/contacts.json` accepts `{"Name": "+91…"}`, `{"Name": "a@b.com"}` or `{"Name": {"phone": "…", "email": "…"}}`; several recipients and cc. The model is told contact *names* only, never numbers or addresses.
- From a real Groq run: Orbi replied in Hindi script to an English request (now: reply in the language the user spoke, Hinglish in Latin letters); it asked for an address that was in the contacts (now it knows contact names); markdown such as `**Send**` is stripped before speaking; one of three quick runs hit the free per-minute limit (now a busy model hands over to `qwen/qwen3.8-27b`, then `openai/gpt-oss-20b`).
- Not done: reading the inbox needs Gmail API access (Google OAuth), which was not requested yet.

### Phase 19.3 — Videos by voice, and voice that really acts
**Planned**
- "Open YouTube and play this video", then change quality, full screen and escape, all by voice, on the Mac and in the Quest. (The headset's history showed the user had already asked Orbi for a song, and it could only search.)

**Implemented**
- `core/cdp.py`: a standard-library Chrome DevTools Protocol client (local endpoints only). `services/media.py`: `search_youtube` (first videos from YouTube's results data, no API key; 0.75 s) and `MediaController` (`play`, `control`: pause, resume, fullscreen, exit_fullscreen, quality 144p–4K/best/auto with fallback to the best available, mute, volume, forward/back, speed, next, status) using YouTube's own player API in the tab. Quest: the Quest browser over the USB DevTools forward. Mac: ORBIT's own Chrome window (separate profile, port 9223), so the user's Chrome is untouched. Full screen uses a CDP user gesture. Tools `play_video` and `video_control` for both voice engines.
- Verified on real YouTube (invisible, muted Chrome): play in 2.4 s; 480p → 1080p → 4K; full screen on/off; pause, +30 s, volume 60%.
- **Found with real Groq runs and fixed:** (1) after a few actions the model only *said* "Paused." / "Skipped" without calling a tool, having learned the pattern from text-only history. Now history keeps the turns' tool calls and results, and a reply claiming an action with no tool call is sent back with `tool_choice: required`. (2) The free plan's 8,000 tokens/min per model allowed ~2 commands a minute. Now device, video and email results are spoken directly (one model call instead of two), tool descriptions are shorter, gpt-oss reasons at `low`, and memory keeps 4 compact turns. (3) Repeated sentences and run-together text are cleaned before speaking. Result: eight commands in a row, every one executed, 0.3–0.8 s each, with automatic hand-over between models.

### Phase 19.4 — Songs and jokes
- Jokes: the model tells short, clean, original jokes itself (no tool). The "claimed action" guard now applies only when the user asked for something to be done, so a joke containing "opened" is not mistaken for an action.
- Singing: new tool `sing(lyrics, style)`; Orbi writes original lyrics (or uses public-domain songs) and the Mac's singing voices perform them (`/speech/sing`: Good News, Cellos, Bells, Organ, Bad News). For real copyrighted songs Orbi offers to play the original on YouTube instead. Played after Orbi's spoken intro in the Groq engine, and from the tool result in the Gemini engine; the headset shows the first lines in Orbi's bubble.
- Real Groq check: a joke, an original song about the lab, a refusal-plus-YouTube offer for "Tum Ho Toh", and Happy Birthday for a named person.

### Phase 19.5 — Screen control by voice
- New tool `screen_control` (both voice engines): scroll up/down/left/right ("a little", "a lot", screens), page up/down, top, bottom, back, forward, reload, zoom in/out/reset. `services/screen.py`: on the Quest, the page in front in the Quest browser via DevTools over USB (ORBIT's AR page is never touched); on the Mac, the front app via navigation keys only (System Events; needs Accessibility once).
- Honest results: scrolling reports whether anything moved ("It's already at the bottom.") and, found on the real headset, a full-screen video now gets "a video is full screen… say 'escape' first" instead of a false "Scrolled down." Instant scrolling (smooth scrolling did not advance) and inner scroll panels are handled.
- Verified on a real Quest page (0 → 387 → 885 → 719 → 3109 → 0 px) and with real Groq: seven casual phrases ("go down a little more", "move it to the right side", "take me back to the top", "zoom in, the text is small", "scroll up on the mac"…) mapped to the right action and device, 0.5–0.9 s each.

### Phase 19.6 — Orbi on the Mac desktop (no browser)
- `desktop/Orbi/main.swift` → **Orbi.app** (`scripts/build_orbi_app.sh` builds, ad-hoc signs and installs it in `~/Applications`): a frameless, transparent, always-on-top panel (all Spaces, beside full-screen apps) showing `/ui/orbi.html`, the avatar with its speech bubble and hands-free voice (Groq, else Gemini Live), ORBIT notices, and a drag handle (the position is remembered). A ◎ menu-bar item: Talk, Show/Hide, dashboard, Open at Login (SMAppService), restart the server, Quit. No Dock icon. The app starts the ORBIT server (`run_demo.sh --lan`) if it isn't running. The microphone is granted only to ORBIT's own page (`localhost:8765`).
- Verified: compiles with Swift 6.4 on macOS 26; launched, loaded the page from the server, and a screenshot shows Orbi floating over other windows with a transparent background. The first click asks macOS for microphone permission.

### Phase 19.7 — "Stop" means stop
- Saying "stop", "stop listening/talking", "be quiet", "that's all", "go to sleep", "goodbye Orbi", "bas", "chup", "ruk jao" ends the conversation: Orbi says one short line and turns the microphone off. Decided by a fixed phrase rule (server `voice_agent.is_stop` and browser `live_core.isStop`), instantly and without a model call. "Stop the video" still pauses the video; the Gemini engine judges only the whole utterance (a partial "stop…" may be "stop the video"). Orbi also stops listening by itself after 90 s with nobody talking to it.
- Real check with Groq audio: "Stop listening." stopped in 0.36 s with no model call; "Stop the video." went to video control; Whisper's "Orby, be quiet." first slipped through, so the rule now covers the name's spellings.

### Phase 19.8 — Quest home: apps and games by voice
- Tools `quest_apps` (installed apps and games, readable names, system bits hidden), `quest_close` (the named app, or whatever is open), `quest_home`; `open_app` on the Quest prefers an installed app over its website (Instagram VR) and finds apps by spoken name in package ids. Apps that live on the Quest open in the headset even when the user talks to the desktop Orbi on the Mac. That's how the user keeps talking while a game is running, since the headset's own Orbi page closes when a game takes over.
- Orbi does not play games or press buttons for the user, and cannot see the game. It helps by answering questions about controls and strategy.
- Also: the USB link to the Quest is restored automatically by the server whenever the headset is attached (`serve.keep_quest_linked`); the headset page reports a lost connection and resumes. Place labels in AR are hidden by default, with Show / Hide buttons and voice commands.
- Real check (read-only on the headset, then real Groq with a pretend headset): installed apps listed; "open toybox", "open instagram in the headset", "go home", and a game tip handled correctly from the Mac. "Close the game" first closed the game only mentioned in a question, not the one open; now it closes what is open unless the user names an app.

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
- No multi-workspace / multi-tenant separation or authentication on the API (spec §17 access control is principal-scoped for actions only). In particular the assistant trusts the "You are" id: anyone who can reach the server can speak as `operator`. Do not expose it beyond your own machine before authentication exists; `--lan` mode requires device pairing, but anyone who knows the code can act as any principal.
- Assistant conversations are process-local (a restart drops context and pending approvals — fail-safe); delegated acts themselves are in the durable audit trail. The realtime bus is in-process too: with several server workers each would only see its own commits (run one worker, or add a shared broker such as Postgres LISTEN/NOTIFY). Voice input uses the browser's speech service (online in Chrome) or on-device Whisper (private; ~80 MB first download). Voice never works inside the Claude app's built-in browser (microphone blocked).
- Reasoning provider is rule-based; free-form language coverage is limited to the supported question types. An LLM provider can be added behind `ReasoningProvider`.
- Live camera: COCO-SSD knows ~80 everyday classes (no cables, tools, pumps) — such objects need QR tags `orbit:<type>:<id>`. One fixed camera per view; no pose tracking. The model is fetched from a CDN on first use (internet needed). Detection quality on real scenes is not yet measured.
- In-memory vector index is rebuilt per process; a pgvector `RetrievalProvider` is needed for large memories.
- PostgreSQL is supported by the schema but CI runs on SQLite only (no Postgres available in this environment).
- AR client and VR rendering (§48 steps 18–19 front-ends) are deferred; the replay/counterfactual backend exists.
- Sandboxes are process-local and disposable (not persisted); each probe copies the world, which is fine for small workspaces but not optimised.

## Next (candidates)
1. First real Quest session: fix what it reveals (anchor drift, label legibility, Whisper latency on the headset).
2. Native Quest app with Meta's Passthrough Camera API so the headset itself can perceive (web pages get no camera pixels).
3. API authentication: real user identities instead of a trusted "You are" field.
4. Measured live accuracy: label a short recorded desk session and report identity/diff metrics on real frames.
5. Generated futures for Experiment H and generated perception worlds for Experiment F2.
3. API authentication and workspace separation.
4. Per-attribute pre-action windows (e.g. pressure vs lockout tag) instead of one HIGH window.

## Tests
- `.venv/bin/pytest` → 725 passed (every engine test runs on both in-memory and SQL backends).
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
- ADR-040 Conversational assistant: delegation inside ORBIT, deterministic consent, interventions vs statements
- ADR-041 Realtime: commit-time notifications, SSE with replay, proactive assistant notices
- ADR-042 Voice input: browser speech service with on-device Whisper fallback
- ADR-043 Quest client: memory pinned to the room, no camera pixels, paired LAN access over HTTPS
- ADR-044 Gemini Live: realtime voice, tools executed by ORBIT, consent from the user's own words
- ADR-045 Groq free-plan speech-to-speech as the default hosted voice engine

## Research Experiments Enabled
- A (persistent identity), B (world diff, incl. B-0), C (stale-memory resistance), D (task resumption), E (evidence and causality), F (active perception) — all runnable via ORBIT-BENCH or dedicated tests, each with its ablation baseline.
- H (counterfactual decisions vs static replay) — runnable, reported in the bench.
- F2 (decision-aware vs uncertainty-driven perception) — runnable, reported in the bench.
- G (AR utility) — deferred (needs an AR client).

## Last Updated
- 2026-10-08 (Phase 19)
