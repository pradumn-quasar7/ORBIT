# ORBIT Project Status

## Current Phase
Phase 1 — Spatial persistence (COMPLETE). Next: Phase 2 — Evidence engine.

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

## Known Issues / Limitations (to be addressed in named phases)
- Conflicting observations still overwrite state unless `record_contradiction` is called (Phase 2).
- Freshness mutates stored versions and re-observation does not refresh support (Phase 2).
- World diff is an event-log filter, not a snapshot diff (Phase 4).
- Ambiguous entities cannot yet be merged into their true identity after verification (future work).

## Tests
- `.venv/bin/pytest` → 73 passed.

## Recent Architecture Decisions
- ADR-005 Repository boundary + SQL store · ADR-006 UTC time · ADR-007 §48 phase order · ADR-008 status classification
- ADR-009 Conservative re-identification · ADR-010 Relation semantics

## Research Experiments Enabled
- Experiment A (persistent identity): re-ID decisions are auditable per observation.
- Experiment B-0 protocol defined (not yet runnable end-to-end).

## Last Updated
- 2026-10-01
