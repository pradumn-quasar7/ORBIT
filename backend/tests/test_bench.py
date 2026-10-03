"""Phase 9 — ORBIT-BENCH. These tests pin the research claims, not just the code:
full ORBIT meets ground truth, and removing each component degrades the metric that
component exists for (spec §28)."""
import pytest

from backend.app.evaluation.bench import LOWER_IS_BETTER, METRICS, VARIANTS, run_bench, to_markdown
from experiments.scenarios.catalog import SCENARIOS


@pytest.fixture(scope="module")
def report():
    return run_bench(SCENARIOS, VARIANTS, {"git_commit": "test"})


def test_catalog_covers_spec_scenarios():
    ids = {s.id for s in SCENARIOS}
    assert len(SCENARIOS) == 14
    assert {"object_relocation", "configuration_change", "partial_observation", "contradiction", "stale_state",
            "negative_search", "task_interruption", "causal_temptation", "same_looking_objects", "object_replaced",
            "multi_user_handoff", "adversarial_memory", "high_risk_stale_premise", "identity_correction"} == ids


def test_full_orbit_meets_ground_truth(report):
    r = report.results["orbit"]
    perfect = {m: (0.0 if m in LOWER_IS_BETTER else 1.0) for m in METRICS}
    perfect["entity_persistence_accuracy"] = None  # checked below: abstaining on look-alikes costs accuracy
    for metric, target in perfect.items():
        if target is not None:
            assert r[metric] == pytest.approx(target), metric
    assert r["entity_persistence_accuracy"] >= 0.95 and r["false_merge_rate"] == 0.0
    per = {x.scenario_id: x for x in report.per_scenario if x.variant == "orbit"}
    assert per["identity_correction"].metrics["entity_persistence_accuracy"].value == 1.0


@pytest.mark.parametrize(
    "variant,metric",
    [
        ("no_freshness", "stale_claim_rate"),
        ("no_freshness", "unsafe_continuation_rate"),
        ("last_writer_wins", "conflict_detection_rate"),
        ("last_writer_wins", "stale_claim_rate"),
        ("unobserved_removed", "diff_precision"),
        ("unobserved_removed", "search_coverage_precision"),
        ("no_evidence_gate", "correct_abstention_rate"),
        ("no_evidence_gate", "evidence_backed_claim_rate"),
        ("no_evidence_gate", "unsupported_causal_claim_rate"),
        ("event_log_diff", "diff_recall"),
        ("event_log_diff", "diff_precision"),
        ("naive_resume", "unsafe_continuation_rate"),
        ("naive_resume", "blocked_step_detection"),
        ("no_risk_grading", "unsafe_continuation_rate"),
        ("no_risk_grading", "task_resumption_success"),
    ],
)
def test_each_ablation_degrades_its_metric(report, variant, metric):
    full, ablated = report.results["orbit"][metric], report.results[variant][metric]
    if metric in LOWER_IS_BETTER:
        assert ablated > full, (variant, metric, full, ablated)
    else:
        assert ablated < full, (variant, metric, full, ablated)


def test_bench_is_deterministic(report):
    again = run_bench(SCENARIOS, VARIANTS, {"git_commit": "test"})
    assert again.results == report.results


def test_report_is_complete(report):
    assert len(report.per_scenario) == len(SCENARIOS) * len(VARIANTS)
    md = to_markdown(report)
    for metric in METRICS:
        assert metric in md
    assert "git_commit" in md and "observation_ms_p95" in md
    assert all(v["config"] for v in report.variants)
