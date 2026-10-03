"""Phase 14 — generated worlds, bootstrap intervals, paired ablation comparisons."""
import pytest

from backend.app.domain.types import AbsenceStatus, EventType
from backend.app.evaluation.bench import VARIANTS, ScenarioRunner
from backend.app.evaluation.generated import run_generated, to_markdown
from backend.app.evaluation.generator import ABSENT, FAMILIES, conflict, generate_suite, scene, task
from backend.app.evaluation.stats import bootstrap_ci, paired_delta, ratio


# ---------------------------------------------------------------- generator
def test_generator_is_deterministic_and_seed_sensitive():
    a = [s.model_dump() for s in generate_suite(4, seed=7)]
    assert a == [s.model_dump() for s in generate_suite(4, seed=7)]
    assert a != [s.model_dump() for s in generate_suite(4, seed=8)]
    assert {s["id"].split("-")[1] for s in a} == set(FAMILIES)


@pytest.mark.parametrize("seed", range(15))
def test_scene_ground_truth_is_consistent(seed):
    sc = scene(seed)
    first, second = sc.steps[0], sc.steps[2]
    originals = {d["truth"] for d in first.detections}
    seen = {d["truth"] for d in second.detections}
    expected = {e.entity_id[6:]: e for e in sc.diffs[0].expected}
    for obj in originals - seen:  # every unseen original is reported, never silently dropped
        assert expected[obj].change_type == EventType.OBJECT_REMOVED_OR_UNOBSERVED
    for obj, e in expected.items():
        if e.absence == AbsenceStatus.CONFIRMED_ABSENT:
            assert obj in sc.truly_absent
    gap = second.at
    for q in sc.queries:
        obj = q.text[len("Where is "):-1]
        assert q.truth is not None
        if obj in seen:
            assert q.expect == "answer"  # re-observed: ORBIT has what it needs
        elif q.expect == "either":
            assert gap <= 1440  # only unexpired, unobserved memory is left to the truth check
        else:
            assert q.expect == "abstain"


@pytest.mark.parametrize("make", [scene, task, conflict])
def test_generated_worlds_run(make):
    for seed in range(10):
        ScenarioRunner(make(seed), VARIANTS[0]).run()


def test_task_ground_truth_marks_steps_after_a_reopened_valve_unsafe():
    for seed in range(30):
        sc = task(seed)
        rc = sc.resumes[0]
        if "reopen" in sc.title:
            assert rc.unsafe_steps and rc.unsafe_steps[-1] == "work"
        else:
            assert rc.unsafe_steps == []


# -------------------------------------------------------------------- stats
def test_ratio_and_bootstrap():
    pairs = [(1, 1), (0, 1), (1, 1), (1, 1)]
    assert ratio(pairs) == 0.75 and ratio([(0, 0)]) is None
    ci = bootstrap_ci(pairs, n=500, seed=1)
    assert ci["low"] <= ci["estimate"] <= ci["high"] and ci["scenarios"] == 4
    assert bootstrap_ci([(0, 0)])["estimate"] is None


def test_paired_delta():
    a = [(1, 1)] * 20
    same = paired_delta(a, a, n=200)
    assert (same["delta"], same["low"], same["high"]) == (0.0, 0.0, 0.0)
    worse = paired_delta(a, [(0, 1)] * 10 + [(1, 1)] * 10, n=200)
    assert worse["delta"] == -0.5 and worse["high"] < 0


# ------------------------------------------------------------------- report
@pytest.fixture(scope="module")
def report():
    return run_generated(per_family=8, seed=3, bootstrap=200, metadata={"git_commit": "test"})


def test_generated_report_shows_each_ablation_degrades(report):
    d = report.deltas
    assert d["last_writer_wins"]["conflict_detection_rate"]["verdict"] == "worse"
    assert d["no_evidence_gate"]["correct_abstention_rate"]["verdict"] == "worse"
    # One resume decision per task world: at this sample size only the direction is
    # stable (the committed 40-worlds-per-family report shows significance).
    assert d["naive_resume"]["unsafe_continuation_rate"]["delta"] >= 0
    assert d["no_risk_grading"]["unsafe_continuation_rate"]["delta"] >= 0
    assert d["event_log_diff"]["diff_recall"]["verdict"] == "worse"
    assert d["unobserved_removed"]["diff_precision"]["verdict"] == "worse"
    # Signature-only identity cannot tell swaps or look-alike replacements apart; the
    # residual false-merge rate over 300 random worlds is 0.5 % (ADR-038). Bound it.
    assert report.results["orbit"]["false_merge_rate"]["estimate"] <= 0.02
    assert report.results["orbit"]["conflict_detection_rate"]["estimate"] == 1.0


def test_generated_report_markdown(report):
    md = to_markdown(report)
    for section in ("## Results", "## Paired difference vs ORBIT", "## ORBIT by family"):
        assert section in md
    assert "95 %" in md and "[" in md


def test_false_merges_need_an_anonymous_camera_and_stay_rare():
    """Large-sample guard on the Phase 14 re-identification fix (ADR-038)."""
    from backend.app.evaluation.bench import Counter2

    merges, total = Counter2(), Counter2()
    for seed in range(60):
        sc = scene(500 + seed)
        r = ScenarioRunner(sc, VARIANTS[0]).run()
        fm = r.metrics["false_merge_rate"]
        merges.add(fm.num, fm.den)
        if fm.num:
            assert "anonymous camera" in sc.title  # with stable ids, never
    assert merges.value <= 0.02
