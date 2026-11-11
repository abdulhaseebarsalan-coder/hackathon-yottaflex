"""Evaluate a published dataset and write <dir>/reports/evaluation_report.{json,md}.

Usage:
    python scripts/evaluate.py --scale starter
    python scripts/evaluate.py --scale full
    python scripts/evaluate.py --scale full --dir output/full --seed 20261022

Reads <dir>/csv and <dir>/ground_truth (if present), regenerates the clean baseline in memory with the
same seed/scale, runs validation + detection + KPIs and writes the reports. Exit code 0 = verdict PASS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.datasets import load_ground_truth  # noqa: E402
from orchestrator.metrics.evaluation import config_for, evaluate_and_write, summary_lines  # noqa: E402


def run(out_dir: Path, scale: str, seed: int | None, employees: int | None) -> dict:
    _, gt_meta = load_ground_truth(out_dir / "ground_truth")
    seed = seed if seed is not None else gt_meta.get("seed")
    with (out_dir / "csv" / "employee.csv").open(encoding="utf-8") as fh:
        n_emp = employees or sum(1 for _ in fh) - 1
    return evaluate_and_write(out_dir, config_for(scale, seed, n_emp))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", default="starter", choices=["starter", "full"])
    ap.add_argument("--dir", type=Path, default=None, help="dataset directory (default: output/<scale>)")
    ap.add_argument("--seed", type=int, default=None, help="seed used to generate the dataset (default: from ground truth / config)")
    ap.add_argument("--employees", type=int, default=None, help="employee count used (default: rows in employee.csv)")
    args = ap.parse_args()
    out_dir = args.dir or ROOT / "output" / args.scale
    report = run(out_dir, args.scale, args.seed, args.employees)
    print("\n".join(summary_lines(report, out_dir)))
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
