# ORBIT Project Status

## Current Phase
Phase 0 — Foundations (COMPLETE). Next: Phase 1 — Spatial persistence.

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

## Known Issues / Limitations (to be addressed in named phases)
- Conflicting observations still overwrite state unless `record_contradiction` is called (Phase 2).
- Freshness mutates stored versions and re-observation does not refresh support (Phase 2).
- World diff is an event-log filter, not a snapshot diff (Phase 4).
- Re-identification is by explicit id only; relations are not maintained (Phase 1).

## Tests
- `.venv/bin/pytest` → 41 passed.

## Recent Architecture Decisions
- ADR-005 Repository boundary + SQL store · ADR-006 UTC time · ADR-007 §48 phase order · ADR-008 status classification

## Research Experiments Enabled
- Experiment B-0 protocol defined (not yet runnable end-to-end).

## Last Updated
- 2026-10-01
