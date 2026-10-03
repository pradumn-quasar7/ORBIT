"""ORBIT-BENCH on generated worlds, with bootstrap confidence intervals.

Usage:
    .venv/bin/python experiments/runners/run_generated_bench.py [--per-family 40] [--seed 0] [--bootstrap 1000]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.generated import run_generated, to_markdown  # noqa: E402
from experiments.runners.run_bench import metadata  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-family", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--out", default=str(ROOT / "experiments" / "results"))
    args = parser.parse_args()
    meta = {k: v for k, v in metadata().items() if k not in ("scenario_catalog", "scenarios")}
    meta["generator"] = "v1 (scene, task, conflict)"
    report = run_generated(args.per_family, args.seed, bootstrap=args.bootstrap, metadata=meta)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "generated.json").write_text(report.model_dump_json(indent=2))
    (out / "generated.md").write_text(to_markdown(report))
    print(to_markdown(report))


if __name__ == "__main__":
    main()
