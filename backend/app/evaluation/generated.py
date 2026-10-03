"""Run ORBIT and its ablations on generated worlds; report CIs and paired deltas."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.app.evaluation.bench import LOWER_IS_BETTER, METRICS, VARIANTS, ScenarioResult, Variant, run_bench
from backend.app.evaluation.generator import FAMILIES, generate_suite
from backend.app.evaluation.stats import bootstrap_ci, paired_delta


class GeneratedReport(BaseModel):
    metadata: Dict[str, Any]
    per_family: int
    seed: int
    variants: List[str]
    results: Dict[str, Dict[str, Dict[str, Optional[float]]]]  # variant -> metric -> CI
    deltas: Dict[str, Dict[str, Dict[str, Any]]]  # variant -> metric -> paired delta vs orbit
    family_results: Dict[str, Dict[str, Optional[float]]]  # family -> metric -> orbit estimate
    examples: List[str]  # sample of ORBIT's own errors on generated worlds


def _pairs(results: List[ScenarioResult], metric: str):
    return [(r.metrics[metric].num, r.metrics[metric].den) if metric in r.metrics else (0.0, 0.0) for r in results]


def run_generated(per_family: int = 20, seed: int = 0, variants: Optional[List[Variant]] = None,
                  bootstrap: int = 1000, metadata: Optional[Dict[str, Any]] = None) -> GeneratedReport:
    variants = variants or VARIANTS
    suite = generate_suite(per_family, seed)
    report = run_bench(suite, variants)
    by_variant: Dict[str, List[ScenarioResult]] = {v.name: [] for v in variants}
    for r in report.per_scenario:
        by_variant[r.variant].append(r)
    for rs in by_variant.values():
        rs.sort(key=lambda r: r.scenario_id)  # aligned for pairing

    metrics = [m for m in METRICS if any(m in r.metrics for r in by_variant["orbit"])]
    results = {v: {m: bootstrap_ci(_pairs(rs, m), bootstrap, seed) for m in metrics} for v, rs in by_variant.items()}
    deltas: Dict[str, Dict[str, Dict[str, Any]]] = {}
    base = by_variant["orbit"]
    for v, rs in by_variant.items():
        if v == "orbit":
            continue
        deltas[v] = {}
        for m in metrics:
            d = paired_delta(_pairs(base, m), _pairs(rs, m), bootstrap, seed)
            verdict = "n.s."
            if d["delta"] is not None and (d["low"] > 0 or d["high"] < 0):
                worse = d["delta"] > 0 if m in LOWER_IS_BETTER else d["delta"] < 0
                verdict = "worse" if worse else "better"
            deltas[v][m] = {**d, "verdict": verdict}

    family_results = {}
    for fam in FAMILIES:
        rs = [r for r in base if r.scenario_id.startswith(f"gen-{fam}-")]
        family_results[fam] = {m: bootstrap_ci(_pairs(rs, m), 1, seed)["estimate"] for m in metrics}
    examples = [f"{r.scenario_id}: {n}" for r in base for n in r.notes if not n.startswith("identity abstained")][:12]
    return GeneratedReport(metadata=metadata or {}, per_family=per_family, seed=seed, variants=[v.name for v in variants],
                           results=results, deltas=deltas, family_results=family_results, examples=examples)


def _fmt(ci: Dict[str, Optional[float]]) -> str:
    if ci.get("estimate") is None:
        return "—"
    return f"{ci['estimate']:.2f} [{ci['low']:.2f}, {ci['high']:.2f}]"


def to_markdown(report: GeneratedReport) -> str:
    names = report.variants
    lines = ["# ORBIT-BENCH — generated worlds", ""]
    lines += [f"- **{k}**: `{v}`" for k, v in report.metadata.items()]
    lines += [f"- **worlds**: {report.per_family} per family × {len(report.family_results)} families "
              f"({', '.join(report.family_results)}), generator seed {report.seed}", "",
              "Ground truth comes from a simulated true world; ORBIT only sees observations of it. "
              "Values are estimates with 95 % percentile-bootstrap intervals over worlds.", "",
              "## Results", "", "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for m in report.results["orbit"]:
        arrow = " ↓" if m in LOWER_IS_BETTER else ""
        lines.append(f"| {m}{arrow} | " + " | ".join(_fmt(report.results[n][m]) for n in names) + " |")
    lines += ["", "## Paired difference vs ORBIT (same worlds)", "",
              "Δ = ablation − ORBIT with a 95 % paired bootstrap interval; **worse**/**better** when the interval excludes 0.", "",
              "| Metric | " + " | ".join(n for n in names if n != "orbit") + " |", "|---|" + "---|" * (len(names) - 1)]
    for m in report.results["orbit"]:
        cells = []
        for n in names:
            if n == "orbit":
                continue
            d = report.deltas[n][m]
            if d["delta"] is None:
                cells.append("—")
            else:
                tag = f" **{d['verdict']}**" if d["verdict"] != "n.s." else ""
                cells.append(f"{d['delta']:+.2f} [{d['low']:+.2f}, {d['high']:+.2f}]{tag}")
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "## ORBIT by family", "", "| Metric | " + " | ".join(report.family_results) + " |",
              "|---|" + "---|" * len(report.family_results)]
    for m in report.results["orbit"]:
        vals = [report.family_results[f].get(m) for f in report.family_results]
        lines.append(f"| {m} | " + " | ".join("—" if v is None else f"{v:.2f}" for v in vals) + " |")
    if report.examples:
        lines += ["", "## Sample of ORBIT's own errors on generated worlds", ""] + [f"- {e}" for e in report.examples]
    return "\n".join(lines) + "\n"
