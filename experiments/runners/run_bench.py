"""Run ORBIT-BENCH across all variants and write JSON + Markdown reports.

Usage:
    .venv/bin/python experiments/runners/run_bench.py [--out experiments/results] [--variants orbit,no_freshness]
"""
import argparse
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.bench import VARIANTS, run_bench, to_markdown  # noqa: E402
from backend.app.providers.embedding import HashingEmbeddingProvider  # noqa: E402
from backend.app.providers.reasoning import RuleBasedReasoningProvider  # noqa: E402
from backend.app.providers.retrieval import InMemoryVectorIndex  # noqa: E402
from experiments.scenarios.catalog import SCENARIOS  # noqa: E402


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # pragma: no cover - not a git checkout
        return "unknown"


def _migration_head() -> str:
    versions = sorted((ROOT / "database" / "migrations" / "versions").glob("*.py"))
    return versions[-1].name.split("_", 1)[0] if versions else "none"


def metadata() -> dict:
    """Spec §22.10: scenario seed/configuration/model/memory version for every run."""
    dirty = bool(_git("status", "--porcelain"))
    return {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "git_commit": _git("rev-parse", "--short", "HEAD") + ("+dirty" if dirty else ""),
        "schema_revision": _migration_head(),
        "scenario_catalog": "v0.1",
        "scenarios": len(SCENARIOS),
        "reasoning_provider": RuleBasedReasoningProvider.name,
        "embedding_provider": HashingEmbeddingProvider.name,
        "retrieval_provider": InMemoryVectorIndex.name,
        "python": platform.python_version(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "experiments" / "results"))
    parser.add_argument("--variants", default=None, help="comma-separated subset of variant names")
    args = parser.parse_args()
    variants = VARIANTS
    if args.variants:
        wanted = set(args.variants.split(","))
        variants = [v for v in VARIANTS if v.name in wanted]
    report = run_bench(SCENARIOS, variants, metadata())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(report.model_dump_json(indent=2))
    (out / "latest.md").write_text(to_markdown(report))
    print(to_markdown(report))
    print(f"Wrote {out / 'latest.json'} and {out / 'latest.md'}")


if __name__ == "__main__":
    main()
